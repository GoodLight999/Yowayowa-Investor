import json

from playwright.sync_api import Page, expect

BASE_URL = "http://127.0.0.1:8000"


def test_lowercase_exact_ticker_beats_more_popular_alternate_listing(page: Page) -> None:
    rows = [
        {
            "symbol": "ABC",
            "name": "Example Holdings Inc.",
            "exchange": "NMS",
            "currency": "USD",
            "instrument_type": "equity",
        },
        {
            "symbol": "ABC.TO",
            "name": "Example Holdings Inc.",
            "exchange": "TOR",
            "currency": "CAD",
            "instrument_type": "equity",
        },
    ]
    page.route(
        "**/v1/instruments/search**",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(rows),
        ),
    )

    response = page.goto(f"{BASE_URL}/?lang=en", wait_until="networkidle")
    assert response is not None and response.ok
    page.locator("#search-input").fill("abc.to")
    page.locator("#global-search").get_by_role("button").click()

    primary = page.locator("#search-results .company-search-primary").first
    expect(primary).to_have_attribute("href", "/instrument/ABC.TO")
    expect(primary).to_contain_text("ABC.TO")
