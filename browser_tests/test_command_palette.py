import json
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import Page, Route, expect

BASE_URL = "http://127.0.0.1:8000"


def test_command_palette_keeps_advanced_tools_search_only(page: Page) -> None:
    page_errors: list[str] = []
    search_queries: list[str] = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))

    def handler(route: Route) -> None:
        parsed = urlparse(route.request.url)
        if parsed.path == "/v1/health":
            body: object = {
                "status": "ok",
                "mode": "personal",
                "market_provider": "yahoo",
                "capabilities": {},
            }
        elif parsed.path == "/v1/watchlists":
            body = []
        elif parsed.path == "/v1/instruments/search":
            query = parse_qs(parsed.query).get("q", [""])[0]
            search_queries.append(query)
            body = [
                {
                    "symbol": "RKLB",
                    "name": "Rocket Lab USA, Inc.",
                    "exchange": "NMS",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "cik": "0001819994",
                }
            ]
        else:
            route.fallback()
            return
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    page.route("**/v1/**", handler)
    response = page.goto(f"{BASE_URL}/?lang=en", wait_until="networkidle")
    assert response is not None and response.ok

    page.keyboard.press("Control+k")
    palette = page.locator("#command-palette")
    results = page.locator("#command-results")
    expect(palette).to_be_visible()
    expect(page.locator("#command-input")).to_be_focused()

    # Empty state mirrors the seven primary product destinations instead of
    # dumping every specialist tool into a second navigation wall.
    items = page.locator("#command-results .command-item")
    expect(items).to_have_count(7)
    expect(results).to_contain_text("Home")
    expect(results).to_contain_text("Discover")
    expect(results).to_contain_text("Markets")
    expect(results).to_contain_text("Economy")
    expect(results).to_contain_text("Portfolio")
    expect(results).to_contain_text("AI Research")
    expect(results).to_contain_text("Settings")
    expect(results).not_to_contain_text("Compare charts")
    expect(results).not_to_contain_text("Rates")
    expect(results).not_to_contain_text("13F")

    navigation_first = items.first
    assert navigation_first.get_attribute("aria-selected") == "true"
    page.keyboard.press("ArrowDown")
    assert navigation_first.get_attribute("aria-selected") == "false"
    page.keyboard.press("ArrowUp")
    assert navigation_first.get_attribute("aria-selected") == "true"

    with page.expect_request(lambda request: "q=13f" in request.url):
        page.locator("#command-input").fill("13f")
    expect(results).to_contain_text("US institutional holdings")
    expect(results).not_to_contain_text("Institutional 13F")
    assert search_queries[-1] == "13f"

    with page.expect_request(lambda request: "q=rklb" in request.url):
        page.locator("#command-input").fill("rklb")
    expect(results).to_contain_text("Rocket Lab USA, Inc.")
    first = page.locator("#command-results .command-item").first
    expect(first).to_contain_text("RKLB")
    assert first.get_attribute("aria-selected") == "true"
    assert search_queries[-1] == "rklb"

    page.keyboard.press("Escape")
    expect(palette).not_to_be_visible()

    page.keyboard.press("Control+p")
    expect(palette).to_be_visible()
    expect(page.locator("#command-input")).to_be_focused()
    page.keyboard.press("Escape")
    assert page_errors == []


def test_command_palette_shortcut_does_not_duplicate_after_turbo_visit(page: Page) -> None:
    page_errors: list[str] = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))

    def handler(route: Route) -> None:
        parsed = urlparse(route.request.url)
        if parsed.path == "/v1/health":
            body: object = {
                "status": "ok",
                "mode": "personal",
                "market_provider": "yahoo",
                "capabilities": {},
            }
        elif parsed.path == "/v1/watchlists":
            body = []
        elif parsed.path == "/v1/ai/providers":
            body = []
        elif parsed.path == "/v1/settings/status":
            body = {
                "sec": True,
                "yahoo_personal": True,
                "edinet": False,
                "estat": False,
                "fred": False,
                "bea": False,
            }
        else:
            route.fallback()
            return
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    page.route("**/v1/**", handler)
    response = page.goto(f"{BASE_URL}/?lang=en", wait_until="networkidle")
    assert response is not None and response.ok

    # A Turbo visit must preserve the JS realm. This catches a regression where
    # the fix merely falls back to a full document reload.
    page.evaluate("window.__yowayowaTurboSentinel = 'alive'")
    page.get_by_role("link", name="Settings", exact=True).click()
    page.wait_for_url("**/settings**")
    expect(page.get_by_role("heading", name="Settings", exact=True)).to_be_visible()
    assert page.evaluate("window.__yowayowaTurboSentinel") == "alive"

    palette = page.locator("#command-palette")
    page.keyboard.press("Control+k")
    expect(palette).to_be_visible()
    expect(page.locator("#command-input")).to_be_focused()
    page.keyboard.press("Control+k")
    expect(palette).not_to_be_visible()
    page.keyboard.press("Control+k")
    expect(palette).to_be_visible()
    page.keyboard.press("Escape")
    expect(palette).not_to_be_visible()
    assert page_errors == []
