from datetime import UTC, datetime
from decimal import Decimal

import pandas as pd
from starlette.testclient import TestClient

from yowayowa.config import get_settings
from yowayowa.domain import (
    LicenseClass,
    PortfolioAnalytics,
    PositionAnalytics,
    Provenance,
)
from yowayowa.providers.yahoo_risk import HistoricalReturnData


def _reset_settings() -> None:
    get_settings.cache_clear()


def _provenance(source: str) -> Provenance:
    now = datetime(2026, 8, 13, tzinfo=UTC)
    return Provenance(
        provider="fixture",
        source=source,
        license_class=LicenseClass.PERSONAL_ONLY,
        retrieved_at=now,
        as_of=now,
    )


def test_portfolio_risk_api_returns_historical_contract(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("YOWAYOWA_MODE", "personal")
    monkeypatch.setenv("YOWAYOWA_DATABASE_URL", f"sqlite:///{tmp_path / 'risk.db'}")
    monkeypatch.delenv("YOWAYOWA_API_TOKEN", raising=False)
    _reset_settings()

    from yowayowa.api import risk_routes
    from yowayowa.api.app import app

    now = datetime(2026, 8, 13, tzinfo=UTC)
    valuation = PortfolioAnalytics(
        portfolio_id=1,
        name="Core",
        base_currency="USD",
        net_market_value=200.0,
        gross_market_value=200.0,
        known_cost_basis=100.0,
        known_cost_market_value=200.0,
        unrealized_pnl=100.0,
        day_pnl=5.0,
        largest_position_weight=1.0,
        concentration_hhi=1.0,
        positions=[
            PositionAnalytics(
                symbol="RKLB",
                quantity=Decimal("10"),
                currency="USD",
                price=20.0,
                fx_to_base=1.0,
                market_value_base=200.0,
                weight=1.0,
                as_of=now,
            )
        ],
        currency_exposure=[],
        provenance=_provenance("quotes"),
        evaluated_at=now,
    )
    index = pd.date_range("2026-08-03", periods=5, tz="UTC")
    history = HistoricalReturnData(
        returns=pd.DataFrame({"RKLB": [0.02, -0.01, 0.03, -0.02, 0.01]}, index=index),
        benchmark_returns=pd.Series(
            [0.01, -0.005, 0.015, -0.01, 0.005],
            index=index,
            name="^GSPC",
        ),
        unavailable_symbols=[],
        provenance=_provenance("history"),
    )

    class FakeRiskProvider:
        def portfolio_returns(self, portfolio, benchmark: str, period: str):
            assert portfolio.name == "Core"
            assert benchmark == "^GSPC"
            assert period == "1y"
            return history

    monkeypatch.setattr(risk_routes, "yahoo_market_provider", lambda: object())
    monkeypatch.setattr(risk_routes, "yahoo_risk_provider", FakeRiskProvider)
    monkeypatch.setattr(risk_routes, "portfolio_analytics", lambda portfolio, provider: valuation)

    try:
        with TestClient(app) as client:
            created = client.post(
                "/v1/portfolios",
                json={"name": "Core", "base_currency": "USD"},
            )
            assert created.status_code == 201
            portfolio_id = created.json()["id"]
            position = client.put(
                f"/v1/portfolios/{portfolio_id}/positions",
                json={
                    "symbol": "RKLB",
                    "quantity": "10",
                    "average_cost": "10",
                    "currency": "USD",
                },
            )
            assert position.status_code == 200

            response = client.get(
                f"/v1/portfolios/{portfolio_id}/risk",
                params={"benchmark": "^GSPC", "period": "1y", "risk_free_rate": 0.01},
            )

            assert response.status_code == 200
            payload = response.json()
            assert payload["portfolio_id"] == portfolio_id
            assert payload["benchmark"] == "^GSPC"
            assert payload["period"] == "1y"
            assert payload["observations"] == 5
            assert payload["covered_gross_weight"] == 1.0
            assert payload["positions"][0]["symbol"] == "RKLB"
            assert payload["annualized_volatility"] > 0
            assert payload["max_drawdown"] <= 0
            assert payload["provenance"][1]["source"] == "history"
    finally:
        _reset_settings()
