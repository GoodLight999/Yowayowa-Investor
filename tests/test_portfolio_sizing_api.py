from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import Mock

from starlette.testclient import TestClient

from yowayowa.config import Settings, get_settings
from yowayowa.domain import (
    LicenseClass,
    Portfolio,
    PortfolioAnalytics,
    Position,
    PositionAnalytics,
    Provenance,
)

_NOW = datetime(2026, 9, 29, 0, 0, tzinfo=UTC)


def _provenance(source: str) -> Provenance:
    return Provenance(
        provider="fixture",
        source=source,
        license_class=LicenseClass.OFFICIAL_PUBLIC,
        retrieved_at=_NOW,
        as_of=_NOW,
    )


def _portfolio(portfolio_id: int = 1) -> Portfolio:
    return Portfolio(
        id=portfolio_id,
        name="Core",
        base_currency="USD",
        positions=[Position(symbol="CORE", quantity=Decimal("10"), currency="USD")],
        created_at=_NOW,
        updated_at=_NOW,
    )


def _valuation(portfolio_id: int) -> PortfolioAnalytics:
    return PortfolioAnalytics(
        portfolio_id=portfolio_id,
        name="Core",
        base_currency="USD",
        net_market_value=1000.0,
        gross_market_value=1000.0,
        known_cost_basis=1000.0,
        known_cost_market_value=1000.0,
        unrealized_pnl=0.0,
        day_pnl=0.0,
        largest_position_weight=1.0,
        concentration_hhi=1.0,
        positions=[
            PositionAnalytics(
                symbol="CORE",
                quantity=Decimal("10"),
                currency="USD",
                price=100.0,
                fx_to_base=1.0,
                market_value_base=1000.0,
                weight=1.0,
                as_of=_NOW,
            )
        ],
        currency_exposure=[],
        provenance=_provenance("portfolio quotes"),
        evaluated_at=_NOW,
    )


def _idea_payload(symbol: str = "NEW") -> dict[str, object]:
    return {
        "symbol": symbol,
        "entry_price": "100",
        "stop_price": "90",
        "currency": "USD",
        "lot_size": 1,
        "price_provenance": _provenance(f"{symbol} quote").model_dump(mode="json"),
    }


def test_portfolio_sizing_api_returns_non_executable_proposal(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("YOWAYOWA_MODE", "personal")
    monkeypatch.setenv("YOWAYOWA_DATABASE_URL", f"sqlite:///{tmp_path / 'sizing.db'}")
    monkeypatch.delenv("YOWAYOWA_API_TOKEN", raising=False)
    get_settings.cache_clear()

    from yowayowa.api import portfolio_sizing_routes
    from yowayowa.api.app import app

    monkeypatch.setattr(portfolio_sizing_routes, "yahoo_market_provider", lambda: object())
    monkeypatch.setattr(
        portfolio_sizing_routes,
        "portfolio_analytics",
        lambda portfolio, _provider: _valuation(portfolio.id),
    )
    try:
        with TestClient(app) as client:
            created = client.post("/v1/portfolios", json={"name": "Core", "base_currency": "USD"})
            assert created.status_code == 201
            portfolio_id = created.json()["id"]
            position = client.put(
                f"/v1/portfolios/{portfolio_id}/positions",
                json={"symbol": "CORE", "quantity": "10", "currency": "USD"},
            )
            assert position.status_code == 200

            response = client.post(
                f"/v1/portfolios/{portfolio_id}/sizing-proposals",
                json={
                    "ideas": [_idea_payload()],
                    "risk_budget_pct": "0.10",
                    "max_position_pct": "0.50",
                },
            )

            assert response.status_code == 200
            payload = response.json()
            assert payload["portfolio_id"] == portfolio_id
            assert payload["ideas"][0]["quantity"] == 5
            assert payload["ideas"][0]["limiting_constraints"] == ["position_limit"]
            assert payload["executable"] is False
            assert payload["provenance"][1]["source"] == "NEW quote"

            mismatch = client.post(
                f"/v1/portfolios/{portfolio_id}/sizing-proposals",
                json={
                    "ideas": [_idea_payload("FOREIGN") | {"currency": "EUR"}],
                    "risk_budget_pct": "0.10",
                    "max_position_pct": "0.50",
                },
            )
            assert mismatch.status_code == 422
            assert "cross-currency candidates fail closed" in mismatch.json()["detail"]
    finally:
        get_settings.cache_clear()


def test_ai_sizing_tool_calls_shared_deterministic_service(monkeypatch) -> None:
    from yowayowa.services import ai_agent
    from yowayowa.services.ai_agent import InvestmentResearchAgent

    portfolio = _portfolio(4)
    valuation = _valuation(portfolio.id)
    monkeypatch.setattr(ai_agent, "get_portfolio", lambda _session, _portfolio_id: portfolio)
    monkeypatch.setattr(ai_agent, "portfolio_analytics", lambda *_args: valuation)
    monkeypatch.setattr(ai_agent, "yahoo_market_provider", lambda: object())

    agent = InvestmentResearchAgent(Settings(database_url="sqlite:///:memory:"), Mock())
    result = agent._tool_propose_portfolio_sizing(
        {
            "portfolio_id": portfolio.id,
            "ideas": [_idea_payload()],
            "risk_budget_pct": "0.10",
            "max_position_pct": "0.50",
        }
    )

    assert "propose_portfolio_sizing" in agent.tools
    assert result["ideas"][0]["quantity"] == 5
    assert result["executable"] is False
    assert result["provenance"][1]["source"] == "NEW quote"
