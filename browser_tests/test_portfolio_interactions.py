import json

from playwright.sync_api import Page, Route, expect

BASE_URL = "http://127.0.0.1:8000"


def _portfolio(positions: list[dict[str, object]]) -> dict[str, object]:
    return {
        "id": 1,
        "name": "Core",
        "base_currency": "USD",
        "positions": positions,
        "created_at": "2026-08-12T00:00:00Z",
        "updated_at": "2026-08-12T00:00:00Z",
    }


def _analytics(positions: list[dict[str, object]]) -> dict[str, object]:
    analytics_positions = []
    if positions:
        analytics_positions.append(
            {
                "symbol": "RKLB",
                "quantity": "10",
                "currency": "USD",
                "average_cost": "8",
                "price": 20.0,
                "previous_close": 19.0,
                "fx_to_base": 1.0,
                "previous_fx_to_base": 1.0,
                "market_value_base": 200.0,
                "cost_basis_base": 80.0,
                "unrealized_pnl_base": 120.0,
                "unrealized_pnl_pct": 1.5,
                "day_pnl_base": 10.0,
                "day_change_pct": 10 / 190,
                "weight": 1.0,
                "as_of": "2026-08-12T00:00:00Z",
            }
        )
    value = 200.0 if positions else 0.0
    return {
        "portfolio_id": 1,
        "name": "Core",
        "base_currency": "USD",
        "net_market_value": value,
        "gross_market_value": value,
        "known_cost_basis": 80.0 if positions else 0.0,
        "known_cost_market_value": value,
        "unrealized_pnl": 120.0 if positions else 0.0,
        "unrealized_pnl_pct": 1.5 if positions else None,
        "day_pnl": 10.0 if positions else 0.0,
        "day_change_pct": 10 / 190 if positions else None,
        "largest_position_weight": 1.0 if positions else 0.0,
        "concentration_hhi": 1.0 if positions else 0.0,
        "positions": analytics_positions,
        "currency_exposure": (
            [{"currency": "USD", "market_value_base": value, "weight": 1.0}] if positions else []
        ),
        "unavailable_symbols": [],
        "provenance": {
            "provider": "fixture",
            "source": "Fixture market data",
            "source_url": None,
            "license_class": "personal_only",
            "retrieved_at": "2026-08-12T00:01:00Z",
            "as_of": "2026-08-12T00:00:00Z",
            "notes": [],
        },
        "evaluated_at": "2026-08-12T00:01:00Z",
    }


def _risk_payload() -> dict[str, object]:
    provenance = {
        "provider": "fixture",
        "source": "Fixture history",
        "source_url": None,
        "license_class": "personal_only",
        "retrieved_at": "2026-08-12T00:01:00Z",
        "as_of": "2026-08-12T00:00:00Z",
        "notes": [],
    }
    return {
        "portfolio_id": 1,
        "name": "Core",
        "base_currency": "USD",
        "benchmark": "^GSPC",
        "period": "1y",
        "observations": 252,
        "annualized_return": 0.18,
        "annualized_volatility": 0.42,
        "sharpe_ratio": 0.428571,
        "max_drawdown": -0.23,
        "value_at_risk_95": 0.031,
        "expected_shortfall_95": 0.045,
        "beta": 1.4,
        "benchmark_correlation": 0.72,
        "gross_exposure": 200.0,
        "net_exposure": 200.0,
        "largest_position_weight": 1.0,
        "concentration_hhi": 1.0,
        "covered_gross_weight": 1.0,
        "positions": [
            {
                "symbol": "RKLB",
                "signed_weight": 1.0,
                "volatility_annualized": 0.42,
                "beta": 1.4,
                "correlation_to_portfolio": 1.0,
                "variance_contribution": 1.0,
                "observations": 252,
            }
        ],
        "correlations": [],
        "unavailable_symbols": [],
        "risk_free_rate": 0.0,
        "notes": ["Historical statistics describe the sampled period and are not forecasts."],
        "provenance": [provenance],
        "evaluated_at": "2026-08-12T00:01:00Z",
    }


def test_portfolio_create_and_position_save_are_browser_safe(page: Page) -> None:
    state: dict[str, object] = {"created": False, "positions": []}
    page_errors: list[str] = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))

    def handle(route: Route) -> None:
        request = route.request
        path = request.url.split("/v1", 1)[-1]
        positions = state["positions"]
        assert isinstance(positions, list)

        if path == "/portfolios" and request.method == "GET":
            body = [_portfolio(positions)] if state["created"] else []
        elif path == "/portfolios" and request.method == "POST":
            state["created"] = True
            body = _portfolio(positions)
        elif path == "/portfolios/1/positions" and request.method == "PUT":
            state["positions"] = [
                {"symbol": "RKLB", "quantity": "10", "average_cost": "8", "currency": "USD"}
            ]
            body = _portfolio(state["positions"])
        elif path == "/portfolios/1/analytics" and request.method == "GET":
            current = state["positions"]
            assert isinstance(current, list)
            body = _analytics(current)
        elif path.startswith("/portfolios/1/snapshots") and request.method == "GET":
            body = []
        else:
            route.abort()
            return
        route.fulfill(
            status=200 if request.method != "POST" else 201,
            content_type="application/json",
            body=json.dumps(body),
        )

    page.route("**/v1/portfolios**", handle)
    page.goto(f"{BASE_URL}/portfolio", wait_until="networkidle")

    page.locator("#portfolio-name").fill("Core")
    page.locator("#portfolio-base").fill("USD")
    page.get_by_role("button", name="Create portfolio").click()
    expect(page.locator("#portfolio-select")).to_have_value("1")
    expect(page.locator("#portfolio-message")).to_contain_text("Core created.")

    page.locator("#position-symbol").fill("RKLB")
    page.locator("#position-quantity").fill("10")
    page.locator("#position-cost").fill("8")
    page.locator("#position-currency").fill("USD")
    page.get_by_role("button", name="Save position").click()
    expect(page.locator("#portfolio-message")).to_contain_text("Position saved.")
    expect(page.locator(".portfolio-table")).to_contain_text("RKLB")

    assert page_errors == []


def test_portfolio_risk_analysis_renders_without_browser_errors(page: Page) -> None:
    positions = [{"symbol": "RKLB", "quantity": "10", "average_cost": "8", "currency": "USD"}]
    page_errors: list[str] = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))

    def handle(route: Route) -> None:
        request = route.request
        path = request.url.split("/v1", 1)[-1]
        if path == "/portfolios":
            body: object = [_portfolio(positions)]
        elif path == "/portfolios/1/analytics":
            body = _analytics(positions)
        elif path.startswith("/portfolios/1/snapshots"):
            body = []
        elif path.startswith("/portfolios/1/risk?"):
            body = _risk_payload()
        else:
            route.abort()
            return
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    page.route("**/v1/portfolios**", handle)
    page.goto(f"{BASE_URL}/portfolio", wait_until="networkidle")
    page.get_by_role("button", name="Analyze risk").click()

    expect(page.locator("#risk-summary")).to_contain_text("Annualized volatility")
    expect(page.locator("#risk-positions")).to_contain_text("RKLB")
    expect(page.locator("#risk-message")).to_contain_text("^GSPC · 1y")
    expect(page.locator("#risk-provenance")).to_contain_text("Fixture history")
    assert page_errors == []
