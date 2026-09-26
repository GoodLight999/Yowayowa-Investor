import json
from urllib.parse import urlparse

from playwright.sync_api import Page, Route, expect

BASE_URL = "http://127.0.0.1:8000"


def _provenance(source: str = "Fixture ticker calendar") -> dict[str, object]:
    return {
        "provider": "fixture",
        "source": source,
        "source_url": None,
        "license_class": "personal_only",
        "retrieved_at": "2026-08-13T12:00:00Z",
        "as_of": "2026-08-13T12:00:00Z",
        "notes": [],
    }


def test_calendar_switches_to_saved_symbol_events(page: Page) -> None:
    page_errors: list[str] = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))

    def handle(route: Route) -> None:
        parsed = urlparse(route.request.url)
        path = parsed.path
        if path == "/v1/watchlists":
            body: object = [
                {
                    "id": 1,
                    "name": "Main",
                    "symbols": ["RKLB"],
                    "created_at": "2026-08-01T00:00:00Z",
                    "updated_at": "2026-08-13T00:00:00Z",
                }
            ]
        elif path == "/v1/portfolios":
            body = []
        elif path == "/v1/calendar":
            body = {
                "start": "2026-08-13",
                "end": "2026-08-27",
                "events": [],
                "provenance": _provenance(),
            }
        elif path == "/v1/calendar/tracked":
            body = {
                "start": "2026-08-13",
                "end": "2026-08-27",
                "scope": "all",
                "scope_id": None,
                "symbols": ["RKLB"],
                "events": [
                    {
                        "event_type": "dividend",
                        "subtype": "ex_dividend",
                        "starts_at": "2026-08-16T00:00:00Z",
                        "ends_at": None,
                        "title": "Ex-dividend date",
                        "symbol": "RKLB",
                        "details": {},
                    }
                ],
                "unavailable_symbols": [],
                "provenance": _provenance(),
            }
        else:
            route.fallback()
            return
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    page.route("**/v1/**", handle)
    response = page.goto(f"{BASE_URL}/calendar?lang=en", wait_until="networkidle")
    assert response is not None and response.ok

    expect(page.locator("#calendar-scope")).to_have_value("market")
    expect(page.locator("#calendar-symbol-field")).to_be_visible()
    expect(page.locator("#calendar-tracked-types")).to_be_hidden()

    page.locator("#calendar-scope").select_option("all")
    expect(page.locator("#calendar-symbol-field")).to_be_hidden()
    expect(page.locator("#calendar-market-types")).to_be_hidden()
    expect(page.locator("#calendar-tracked-types")).to_be_visible()
    page.get_by_role("button", name="Run").click()

    expect(page.locator("#calendar-results").get_by_text("RKLB", exact=True)).to_be_visible()
    expect(page.locator("#calendar-results").get_by_text("Dividend", exact=True)).to_be_visible()
    expect(
        page.locator("#calendar-results").get_by_text("Ex-dividend date", exact=True)
    ).to_be_visible()
    expect(page.locator("#calendar-provenance")).to_contain_text("Fixture ticker calendar")
    assert page_errors == []


def test_news_switches_to_saved_symbol_feed(page: Page) -> None:
    page_errors: list[str] = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))

    def handle(route: Route) -> None:
        path = urlparse(route.request.url).path
        if path == "/v1/watchlists":
            body: object = [
                {
                    "id": 1,
                    "name": "Main",
                    "symbols": ["RKLB"],
                    "created_at": "2026-08-01T00:00:00Z",
                    "updated_at": "2026-08-13T00:00:00Z",
                }
            ]
        elif path == "/v1/portfolios":
            body = []
        elif path == "/v1/news/saved":
            body = {
                "scope": "all",
                "scope_id": None,
                "symbols": ["ASTS", "RKLB", "SOFI"],
                "items": [
                    {
                        "id": "space-story",
                        "title": "Saved space story",
                        "publisher": "Fixture Wire",
                        "published_at": "2026-08-13T12:00:00Z",
                        "url": "https://example.test/space",
                        "summary": "One story related to two saved symbols.",
                        "symbols": ["ASTS", "RKLB"],
                    }
                ],
                "unavailable_symbols": ["SOFI"],
                "provenance": _provenance("Fixture saved news"),
            }
        else:
            route.fallback()
            return
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    page.route("**/v1/**", handle)
    response = page.goto(f"{BASE_URL}/news?lang=en", wait_until="networkidle")
    assert response is not None and response.ok

    expect(page.locator("#news-scope")).to_have_value("search")
    expect(page.locator("#news-query")).to_be_visible()
    page.locator("#news-scope").select_option("all")
    expect(page.locator("#news-query")).to_be_hidden()
    page.get_by_role("button", name="Run").click()

    expect(page.get_by_text("Saved space story", exact=True)).to_be_visible()
    expect(page.locator("#news-results").get_by_role("link", name="ASTS")).to_be_visible()
    expect(page.locator("#news-results").get_by_role("link", name="RKLB")).to_be_visible()
    expect(page.locator("#news-provenance")).to_contain_text("Fixture saved news")
    expect(page.locator("#news-provenance")).to_contain_text("SOFI")
    assert page_errors == []
