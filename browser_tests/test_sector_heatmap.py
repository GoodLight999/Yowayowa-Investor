import json
from urllib.parse import urlparse

from playwright.sync_api import Page, Route, expect

BASE_URL = "http://127.0.0.1:8000"


def _provenance(source: str) -> dict[str, object]:
    return {
        "provider": "fixture",
        "source": source,
        "source_url": None,
        "license_class": "personal_only",
        "retrieved_at": "2026-08-16T07:10:00Z",
        "as_of": "2026-08-15T00:00:00Z",
        "notes": [],
    }


def _sector(symbol: str, label: str, one_day: float, one_month: float) -> dict[str, object]:
    return {
        "symbol": symbol,
        "label": label,
        "category": "US sector",
        "unit": "USD",
        "value": 100.0,
        "change_1d": one_day,
        "change_1m": one_month,
        "change_3m": one_month * 1.5,
        "change_1y": one_month * 2.0,
        "sparkline": [],
        "as_of": "2026-08-15T00:00:00Z",
    }


def test_sector_heatmap_switches_horizon_and_reorders_strength(page: Page) -> None:
    page_errors: list[str] = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))

    def handler(route: Route) -> None:
        path = urlparse(route.request.url).path
        if path == "/v1/health":
            body: object = {
                "status": "ok",
                "mode": "personal",
                "market_provider": "yahoo",
                "capabilities": {},
            }
        elif path == "/v1/markets/overview":
            body = {
                "items": [],
                "unavailable_symbols": [],
                "provenance": _provenance("Fixture markets"),
            }
        elif path == "/v1/markets/sectors":
            body = {
                "items": [
                    _sector("XLK", "Technology", 0.01, 0.05),
                    _sector("XLE", "Energy", 0.04, -0.02),
                    _sector("XLU", "Utilities", -0.01, 0.01),
                ],
                "unavailable_symbols": [],
                "provenance": _provenance("Fixture sectors"),
            }
        else:
            route.fallback()
            return
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    page.route("**/v1/**", handler)
    response = page.goto(f"{BASE_URL}/markets?lang=en", wait_until="networkidle")
    assert response is not None and response.ok

    tiles = page.locator("#sector-heatmap .sector-tile")
    expect(tiles).to_have_count(3)
    expect(tiles.first).to_contain_text("Technology")
    expect(tiles.first).to_contain_text("+5.00%")
    assert tiles.first.get_attribute("data-sign") == "positive"
    expect(page.locator("#sector-source")).to_contain_text("Fixture sectors")

    page.get_by_role("button", name="1D").click()
    expect(tiles.first).to_contain_text("Energy")
    expect(tiles.first).to_contain_text("+4.00%")
    assert tiles.first.get_attribute("data-sign") == "positive"
    assert page_errors == []
