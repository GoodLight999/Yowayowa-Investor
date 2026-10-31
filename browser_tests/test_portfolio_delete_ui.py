"""P5 R3 (UI): delete a portfolio from the browser, safely.

The API contract (404 / 409 / force) is implemented and audited in
``tests/test_portfolio_delete.py``. This module proves the browser surface
actually reaches it and does not turn a ``409`` into a silent failure.
"""

import json

from playwright.sync_api import Dialog, Page, Route, expect

BASE_URL = "http://127.0.0.1:8000"

_PORTFOLIO = {
    "id": 1,
    "name": "Core",
    "base_currency": "USD",
    "positions": [{"symbol": "RKLB", "quantity": "10", "average_cost": "8", "currency": "USD"}],
    "created_at": "2026-08-12T00:00:00Z",
    "updated_at": "2026-08-12T00:00:00Z",
}

_ANALYTICS = {
    "portfolio_id": 1,
    "name": "Core",
    "base_currency": "USD",
    "net_market_value": 200.0,
    "gross_market_value": 200.0,
    "known_cost_basis": 80.0,
    "known_cost_market_value": 200.0,
    "unrealized_pnl": 120.0,
    "unrealized_pnl_pct": 1.5,
    "day_pnl": 10.0,
    "day_change_pct": 10 / 190,
    "largest_position_weight": 1.0,
    "concentration_hhi": 1.0,
    "positions": [
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
    ],
    "currency_exposure": [{"currency": "USD", "market_value_base": 200.0, "weight": 1.0}],
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

_CONFLICT_DETAIL = (
    "Portfolio 1 still owns dependent rows (positions=1, snapshots=1, applied_executions=1); "
    "pass force=true to cascade the delete"
)


def _record_dialogs(page: Page, accept: list[bool]) -> list[str]:
    """Collect confirm() texts; ``accept`` decides per dialog, in order."""
    messages: list[str] = []

    def handle(dialog: Dialog) -> None:
        messages.append(dialog.message)
        index = len(messages) - 1
        if index < len(accept) and not accept[index]:
            dialog.dismiss()
        else:
            dialog.accept()

    page.on("dialog", handle)
    return messages


def test_portfolio_delete_from_ui_forces_after_409_and_clears_the_list(page: Page) -> None:
    state = {"exists": True, "delete_calls": []}
    page_errors: list[str] = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    dialogs = _record_dialogs(page, accept=[True, True])

    def handle(route: Route) -> None:
        request = route.request
        path = request.url.split("/v1", 1)[-1]
        if path == "/portfolios" and request.method == "GET":
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps([_PORTFOLIO] if state["exists"] else []),
            )
            return
        if path == "/portfolios/1/analytics" and request.method == "GET":
            route.fulfill(status=200, content_type="application/json", body=json.dumps(_ANALYTICS))
            return
        if path.startswith("/portfolios/1/snapshots"):
            route.fulfill(status=200, content_type="application/json", body="[]")
            return
        if path.startswith("/portfolios/1") and request.method == "DELETE":
            state["delete_calls"].append(path)
            if "force=true" in path:
                state["exists"] = False
                body = {
                    "portfolio_id": 1,
                    "name": "Core",
                    "force": True,
                    "deleted_positions": 1,
                    "deleted_snapshots": 1,
                    "deleted_applied_executions": 1,
                }
                route.fulfill(status=200, content_type="application/json", body=json.dumps(body))
                return
            route.fulfill(
                status=409,
                content_type="application/json",
                body=json.dumps({"detail": _CONFLICT_DETAIL}),
            )
            return
        route.abort()

    page.route("**/v1/portfolios**", handle)
    page.goto(f"{BASE_URL}/portfolio", wait_until="networkidle")
    expect(page.locator("#portfolio-delete-note")).to_contain_text("Core")
    expect(page.locator("#portfolio-delete")).to_be_enabled()

    page.locator("#portfolio-delete").click()

    expect(page.locator("#portfolio-select")).to_have_value("")
    expect(page.locator("#portfolio-select option")).to_have_text("No portfolios")
    expect(page.locator("#portfolio-select")).to_be_disabled()
    expect(page.locator("#portfolio-delete")).to_be_disabled()
    expect(page.locator("#portfolio-message")).to_contain_text('"Core" deleted with 1 holdings')

    # One plain DELETE (409) then exactly one forced DELETE (200): no retry storm.
    assert state["delete_calls"] == ["/portfolios/1", "/portfolios/1?force=true"]
    # Both confirmations named the portfolio; the second one is the force step.
    assert len(dialogs) == 2, dialogs
    assert "Core" in dialogs[0]
    assert "holdings and valuation history" in dialogs[0]
    assert "1 holdings, 1 history entries and 1 applied executions" in dialogs[1]
    assert page_errors == []


def test_portfolio_delete_force_prompt_can_be_declined(page: Page) -> None:
    state = {"delete_calls": []}
    page_errors: list[str] = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    _record_dialogs(page, accept=[True, False])

    def handle(route: Route) -> None:
        request = route.request
        path = request.url.split("/v1", 1)[-1]
        if path == "/portfolios" and request.method == "GET":
            route.fulfill(
                status=200, content_type="application/json", body=json.dumps([_PORTFOLIO])
            )
            return
        if path == "/portfolios/1/analytics" and request.method == "GET":
            route.fulfill(status=200, content_type="application/json", body=json.dumps(_ANALYTICS))
            return
        if path.startswith("/portfolios/1/snapshots"):
            route.fulfill(status=200, content_type="application/json", body="[]")
            return
        if path.startswith("/portfolios/1") and request.method == "DELETE":
            state["delete_calls"].append(path)
            route.fulfill(
                status=409,
                content_type="application/json",
                body=json.dumps({"detail": _CONFLICT_DETAIL}),
            )
            return
        route.abort()

    page.route("**/v1/portfolios**", handle)
    page.goto(f"{BASE_URL}/portfolio", wait_until="networkidle")

    page.locator("#portfolio-delete").click()

    expect(page.locator("#portfolio-message")).to_contain_text('Deletion of "Core" was cancelled.')
    expect(page.locator("#portfolio-select")).to_have_value("1")
    expect(page.locator("#portfolio-delete")).to_be_enabled()
    # Declining the force prompt must not send a second request.
    assert state["delete_calls"] == ["/portfolios/1"]
    assert page_errors == []


def test_portfolio_delete_404_reports_already_gone_and_reloads(page: Page) -> None:
    state = {"gone": False, "delete_calls": []}
    page_errors: list[str] = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    _record_dialogs(page, accept=[True])

    def handle(route: Route) -> None:
        request = route.request
        path = request.url.split("/v1", 1)[-1]
        if path == "/portfolios" and request.method == "GET":
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps([] if state["gone"] else [_PORTFOLIO]),
            )
            return
        if path == "/portfolios/1/analytics" and request.method == "GET":
            route.fulfill(status=200, content_type="application/json", body=json.dumps(_ANALYTICS))
            return
        if path.startswith("/portfolios/1/snapshots"):
            route.fulfill(status=200, content_type="application/json", body="[]")
            return
        if path.startswith("/portfolios/1") and request.method == "DELETE":
            state["delete_calls"].append(path)
            state["gone"] = True
            route.fulfill(
                status=404,
                content_type="application/json",
                body=json.dumps({"detail": "Portfolio 1 not found"}),
            )
            return
        route.abort()

    page.route("**/v1/portfolios**", handle)
    page.goto(f"{BASE_URL}/portfolio", wait_until="networkidle")

    page.locator("#portfolio-delete").click()

    expect(page.locator("#portfolio-message")).to_contain_text(
        '"Core" no longer exists. The list was reloaded.'
    )
    expect(page.locator("#portfolio-select option")).to_have_text("No portfolios")
    expect(page.locator("#portfolio-delete")).to_be_disabled()
    assert state["delete_calls"] == ["/portfolios/1"]
    assert page_errors == []
