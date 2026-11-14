"""POST /v1/sizing/quote-idea boundary (P5 R5).

API/CLI parity with the shared service: same fail-closed codes, fixed failure
details without URL/token/exception leaks, response model is the bare
PortfolioSizingIdea (no wrapper) so the JSON can be handed to the existing
sizing-proposals surface unchanged. Only fixture providers are used here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import Mock

import httpx
import pytest
from starlette.testclient import TestClient

from yowayowa.config import get_settings
from yowayowa.domain import LicenseClass, MarketQuote, MarketQuoteBatch, Provenance

_NOW = datetime(2026, 10, 22, 0, 0, tzinfo=UTC)
_RETRIEVED_AT = datetime(2026, 10, 23, 1, 2, 3, tzinfo=UTC)
_TOKEN = "fixture-token-123"


def _provenance() -> Provenance:
    return Provenance(
        provider="fixture",
        source="Fixture Market",
        license_class=LicenseClass.OFFICIAL_PUBLIC,
        retrieved_at=_RETRIEVED_AT,
        as_of=_NOW,
        notes=["Fixture batch."],
    )


def _quote(
    symbol: str = "AAPL",
    *,
    price: float = 348.22,
    currency: str | None = "USD",
    currency_source: str | None = "fixture:fast_info",
    currency_reason: str | None = None,
) -> MarketQuote:
    return MarketQuote(
        symbol=symbol,
        price=price,
        previous_close=340.0,
        currency=currency,
        currency_source=currency_source,
        currency_reason=currency_reason,
        as_of=_NOW,
    )


def _batch(quotes: dict[str, MarketQuote], *, unavailable: list[str] | None = None):
    return MarketQuoteBatch(
        quotes=quotes,
        unavailable_symbols=unavailable or [],
        provenance=_provenance(),
    )


class _StubProvider:
    def __init__(self, batch=None, error: Exception | None = None) -> None:
        self._batch = batch
        self._error = error
        self.calls: list[list[str]] = []

    def quotes(self, symbols):
        self.calls.append(list(symbols))
        if self._error is not None:
            raise self._error
        return self._batch


def _install(monkeypatch, provider: _StubProvider) -> None:
    monkeypatch.setenv("YOWAYOWA_MODE", "personal")
    monkeypatch.setenv("YOWAYOWA_API_TOKEN", _TOKEN)
    get_settings.cache_clear()

    from yowayowa.api import portfolio_sizing_routes

    monkeypatch.setattr(portfolio_sizing_routes, "yahoo_market_provider", lambda: provider)


def _client() -> TestClient:
    from yowayowa.api.app import app

    return TestClient(app, headers={"Authorization": f"Bearer {_TOKEN}"})


def _idea_body(symbol: str = "AAPL", stop: str = "300", lot: int = 1) -> dict[str, object]:
    return {"symbol": symbol, "stop_price": stop, "lot_size": lot}


@pytest.fixture()
def _clear_settings(monkeypatch):
    yield
    get_settings.cache_clear()


# --------------------------------------------------------------------------- #
# Happy path: quote-idea feeds sizing-proposals unchanged
# --------------------------------------------------------------------------- #


def test_quote_idea_response_is_a_bare_portfolio_sizing_idea(monkeypatch, _clear_settings):
    provider = _StubProvider(_batch({"AAPL": _quote()}))
    _install(monkeypatch, provider)

    with _client() as client:
        response = client.post("/v1/sizing/quote-idea", json=_idea_body())

    assert response.status_code == 200
    from yowayowa.sizing_models import PortfolioSizingIdea

    assert set(response.json()) == set(PortfolioSizingIdea.model_fields)
    assert PortfolioSizingIdea.model_validate(response.json()).entry_price == Decimal("348.22")
    assert provider.calls == [["AAPL"]]


def test_quote_idea_json_feeds_sizing_proposals_like_a_handwritten_idea(
    monkeypatch, _clear_settings
):
    provider = _StubProvider(_batch({"AAPL": _quote()}))
    _install(monkeypatch, provider)

    from yowayowa.api import portfolio_sizing_routes

    monkeypatch.setattr(
        portfolio_sizing_routes,
        "portfolio_analytics",
        lambda portfolio, _provider: _usd_valuation(portfolio.id),
    )

    with _client() as client:
        created = client.post("/v1/portfolios", json={"name": "Core", "base_currency": "USD"})
        assert created.status_code == 201
        portfolio_id = created.json()["id"]
        position = client.put(
            f"/v1/portfolios/{portfolio_id}/positions",
            json={"symbol": "AAPL", "quantity": "10", "currency": "USD"},
        )
        assert position.status_code == 200

        idea_response = client.post("/v1/sizing/quote-idea", json=_idea_body())
        assert idea_response.status_code == 200
        idea = idea_response.json()

        proposal = client.post(
            f"/v1/portfolios/{portfolio_id}/sizing-proposals",
            json={
                "ideas": [idea],
                "risk_budget_pct": "0.10",
                "max_position_pct": "0.50",
            },
        )
        assert proposal.status_code == 200
        generated = proposal.json()["ideas"][0]

        handwritten = client.post(
            f"/v1/portfolios/{portfolio_id}/sizing-proposals",
            json={
                "ideas": [
                    {
                        "symbol": "AAPL",
                        "entry_price": "348.22",
                        "stop_price": "300",
                        "currency": "USD",
                        "lot_size": 1,
                        "price_provenance": idea["price_provenance"],
                    }
                ],
                "risk_budget_pct": "0.10",
                "max_position_pct": "0.50",
            },
        )
        assert handwritten.status_code == 200
        manual = handwritten.json()["ideas"][0]

    assert proposal.json()["executable"] is False
    assert generated["quantity"] == manual["quantity"]
    assert generated["proposed_notional_base"] == manual["proposed_notional_base"]
    assert generated["entry_price"] == idea["entry_price"]
    assert generated["price_provenance"] == idea["price_provenance"]
    assert Decimal(generated["estimated_stop_loss_base"]) == Decimal(generated["quantity"]) * (
        Decimal("348.22") - Decimal("300")
    )


def _usd_valuation(portfolio_id: int):
    from yowayowa.domain import PortfolioAnalytics, PositionAnalytics

    return PortfolioAnalytics(
        portfolio_id=portfolio_id,
        name="Core",
        base_currency="USD",
        net_market_value=3482.2,
        gross_market_value=3482.2,
        known_cost_basis=3482.2,
        known_cost_market_value=3482.2,
        unrealized_pnl=0.0,
        day_pnl=0.0,
        largest_position_weight=1.0,
        concentration_hhi=1.0,
        positions=[
            PositionAnalytics(
                symbol="AAPL",
                quantity=Decimal("10"),
                currency="USD",
                price=348.22,
                fx_to_base=1.0,
                market_value_base=3482.2,
                weight=1.0,
                as_of=_NOW,
            )
        ],
        currency_exposure=[],
        unavailable_symbols=[],
        provenance=_provenance(),
        evaluated_at=_NOW,
    )


# --------------------------------------------------------------------------- #
# Fail-closed statuses and fixed details
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("body", "expected_status"),
    [
        ({"symbol": "AAPL", "stop_price": "300"}, 422),  # lot_size missing
        ({"symbol": "AAPL", "stop_price": "0", "lot_size": 1}, 422),
        ({"symbol": "AAPL", "stop_price": "-1", "lot_size": 1}, 422),
        ({"symbol": "AAPL", "stop_price": "NaN", "lot_size": 1}, 422),
        ({"symbol": "AAPL", "stop_price": "300", "lot_size": 0}, 422),
        ({"symbol": "AAPL", "stop_price": "300", "lot_size": 1.5}, 422),
        ({"symbol": "AAPL", "stop_price": "300", "lot_size": 1, "currency": "USD"}, 422),
        ({"symbol": "", "stop_price": "300", "lot_size": 1}, 422),
    ],
)
def test_invalid_inputs_are_rejected_before_the_provider(
    monkeypatch, _clear_settings, body, expected_status
):
    provider = _StubProvider(_batch({"AAPL": _quote()}))
    _install(monkeypatch, provider)

    with _client() as client:
        response = client.post("/v1/sizing/quote-idea", json=body)

    assert response.status_code == expected_status
    assert provider.calls == []  # input defects never reach the provider


def test_unknown_symbol_maps_to_404_with_fixed_detail(monkeypatch, _clear_settings):
    provider = _StubProvider(_batch({}, unavailable=["MISSING"]))
    _install(monkeypatch, provider)

    with _client() as client:
        response = client.post("/v1/sizing/quote-idea", json=_idea_body("MISSING"))

    assert response.status_code == 404
    detail = response.json()["detail"]
    assert detail["code"] == "quote_unavailable"
    assert "no usable quote" in detail["reason"]
    assert provider.calls == [["MISSING"]]


def test_currency_and_price_defects_map_to_422(monkeypatch, _clear_settings):
    missing_currency = _StubProvider(
        _batch({"AAPL": _quote(currency=None, currency_reason="provider_returned_no_currency")})
    )
    _install(monkeypatch, missing_currency)
    with _client() as client:
        response = client.post("/v1/sizing/quote-idea", json=_idea_body())
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "currency_missing"

    zero_price = _StubProvider(_batch({"AAPL": _quote(price=0.0)}))
    _install(monkeypatch, zero_price)
    with _client() as client:
        response = client.post("/v1/sizing/quote-idea", json=_idea_body())
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "price_invalid"


def test_provider_policy_failure_maps_to_403_without_exception_text(monkeypatch, _clear_settings):
    from yowayowa.providers.base import ProviderPolicyError

    provider = _StubProvider(
        error=ProviderPolicyError("Provider 'fixture' is not licensed for public redistribution")
    )
    _install(monkeypatch, provider)

    with _client() as client:
        response = client.post("/v1/sizing/quote-idea", json=_idea_body())

    assert response.status_code == 403
    detail = response.json()["detail"]
    assert detail["code"] == "provider_policy"
    assert "not licensed" not in detail["reason"]
    assert "fixture" not in detail["reason"]


def test_upstream_failure_maps_to_502_without_leaking_the_exception(monkeypatch, _clear_settings):
    provider = _StubProvider(error=RuntimeError("boom http://secret-url token=abc"))
    _install(monkeypatch, provider)

    with _client() as client:
        response = client.post("/v1/sizing/quote-idea", json=_idea_body())

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail["code"] == "upstream_provider_failed"
    assert "secret-url" not in detail["reason"]
    assert "token" not in detail["reason"]
    assert "boom" not in detail["reason"]


def test_authentication_is_enforced_on_the_quote_idea_route(monkeypatch, _clear_settings):
    provider = _StubProvider(_batch({"AAPL": _quote()}))
    _install(monkeypatch, provider)

    from yowayowa.api.app import app

    with TestClient(app) as anonymous:
        rejected = anonymous.post("/v1/sizing/quote-idea", json=_idea_body())
    with TestClient(app, headers={"Authorization": "Bearer wrong"}) as wrong:
        wrong_token = wrong.post("/v1/sizing/quote-idea", json=_idea_body())
    with _client() as authorized:
        accepted = authorized.post("/v1/sizing/quote-idea", json=_idea_body())

    assert rejected.status_code == 401
    assert wrong_token.status_code == 401
    assert accepted.status_code == 200
    assert provider.calls == [["AAPL"]]  # only the authenticated call fetched


# --------------------------------------------------------------------------- #
# Machine-discoverability and agent-tool contract pins
# --------------------------------------------------------------------------- #


def test_quote_idea_path_stays_agent_discoverable() -> None:
    from yowayowa.api.app import app

    schema = app.openapi()
    operation = schema["paths"]["/v1/sizing/quote-idea"]["post"]
    ref = operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    # FastAPI names response schemas "<Model>-Output"; the shape is still the
    # bare PortfolioSizingIdea — no wrapper object around it.
    assert ref == "#/components/schemas/PortfolioSizingIdea-Output"
    assert "/v1/sizing/quote-idea" in schema["paths"]


def test_agent_tool_catalog_keeps_quote_sizing_idea() -> None:
    from yowayowa.config import Settings
    from yowayowa.services.ai_agent import InvestmentResearchAgent

    agent = InvestmentResearchAgent(Settings(database_url="sqlite:///:memory:"), Mock())
    assert {"propose_portfolio_sizing", "build_quote_sizing_idea"} <= set(agent.tools)


# --------------------------------------------------------------------------- #
# CLI command (module level; registration is a separate cli_entry step)
# --------------------------------------------------------------------------- #


class _StubResponse:
    def __init__(self, status_code: int, payload: dict[str, object] | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "http://fixture.invalid/v1/sizing/quote-idea")
            response = httpx.Response(status_code=self.status_code, request=request)
            raise httpx.HTTPStatusError(
                f"fixture status {self.status_code}", request=request, response=response
            )

    def json(self) -> dict[str, object]:
        return self._payload


class _StubClient:
    def __init__(self, response: _StubResponse) -> None:
        self.response = response

    def post(self, path: str, json=None):
        self.last_request = {"path": path, "json": json}
        return self.response

    def __enter__(self) -> _StubClient:
        return self

    def __exit__(self, *_args: object) -> bool:
        return False


def test_cli_success_prints_exactly_one_json_line(monkeypatch) -> None:
    import typer
    from typer.testing import CliRunner

    from yowayowa import sizing_cli

    idea_payload = {
        "symbol": "AAPL",
        "entry_price": "348.22",
        "stop_price": "300",
        "currency": "USD",
        "lot_size": 1,
        "price_provenance": _provenance().model_dump(mode="json"),
    }
    stub = _StubClient(_StubResponse(200, idea_payload))
    captured: dict[str, object] = {}

    def fake_client(base_url: str, token: str | None) -> _StubClient:
        captured["base_url"] = base_url
        captured["token"] = token
        return stub

    monkeypatch.setattr(sizing_cli, "_api_client", fake_client)

    cli = typer.Typer()
    cli.command(name="sizing-idea")(sizing_cli.sizing_idea)
    result = CliRunner().invoke(
        cli,
        [
            "AAPL",
            "--stop-price",
            "300",
            "--lot-size",
            "1",
            "--base-url",
            "http://fixture.invalid",
            "--token",
            "secret-cli-token",
        ],
    )

    assert result.exit_code == 0
    assert captured == {
        "base_url": "http://fixture.invalid",
        "token": "secret-cli-token",
    }
    assert stub.last_request == {
        "path": "/v1/sizing/quote-idea",
        "json": {"symbol": "AAPL", "stop_price": "300", "lot_size": 1},
    }
    lines = [line for line in result.output.splitlines() if line.strip()]
    assert len(lines) == 1
    import json

    assert json.loads(lines[0]) == idea_payload  # decimal precision kept as strings


def test_cli_http_failure_exits_nonzero_without_faking_json(monkeypatch) -> None:
    import typer
    from typer.testing import CliRunner

    from yowayowa import sizing_cli

    stub = _StubClient(_StubResponse(404))
    monkeypatch.setattr(sizing_cli, "_api_client", lambda base_url, token: stub)

    cli = typer.Typer()
    cli.command(name="sizing-idea")(sizing_cli.sizing_idea)
    result = CliRunner().invoke(cli, ["AAPL", "--stop-price", "300", "--lot-size", "1"])

    assert result.exit_code != 0
    assert result.stdout.strip() == ""  # success-shaped JSON is never printed
    # The escaped httpx.HTTPStatusError is the exact failure class that the
    # cli_shim boundary converts into one fixed sentence with exit 1
    # (tests/test_cli_http_errors.py pins that boundary).
    assert isinstance(result.exception, httpx.HTTPStatusError)
