import json

from playwright.sync_api import Page, expect

BASE_URL = "http://127.0.0.1:8000"


def test_language_switch_persists_between_pages(page: Page) -> None:
    response = page.goto(f"{BASE_URL}/?lang=ja", wait_until="networkidle")
    assert response is not None and response.ok
    expect(page.get_by_role("heading", name="ホーム", exact=True)).to_be_visible()
    assert page.locator("html").get_attribute("lang") == "ja"

    page.get_by_role("button", name="English", exact=True).click()
    page.wait_for_load_state("networkidle")
    expect(page.get_by_role("heading", name="Home", exact=True)).to_be_visible()
    assert page.locator("html").get_attribute("lang") == "en"

    page.goto(f"{BASE_URL}/compare", wait_until="networkidle")
    expect(page.get_by_role("heading", name="Compare", exact=True)).to_be_visible()
    assert page.locator("html").get_attribute("lang") == "en"


def test_watchlist_renders_batched_live_quote(page: Page) -> None:
    watchlists = json.dumps(
        [
            {
                "id": 1,
                "name": "Main",
                "symbols": ["RKLB", "ASTS"],
                "created_at": "2026-08-01T00:00:00Z",
                "updated_at": "2026-08-12T00:00:00Z",
            }
        ]
    )
    quotes = json.dumps(
        {
            "quotes": {
                "RKLB": {
                    "symbol": "RKLB",
                    "price": 20.0,
                    "previous_close": 19.0,
                    "as_of": "2026-08-12T00:00:00Z",
                },
                "ASTS": {
                    "symbol": "ASTS",
                    "price": 45.0,
                    "previous_close": 46.0,
                    "as_of": "2026-08-12T00:00:00Z",
                },
            },
            "unavailable_symbols": [],
            "provenance": {
                "provider": "fixture",
                "source": "Fixture",
                "source_url": None,
                "license_class": "personal_only",
                "retrieved_at": "2026-08-12T00:00:00Z",
                "as_of": "2026-08-12T00:00:00Z",
                "notes": [],
            },
        }
    )
    page.route(
        "**/v1/watchlists",
        lambda route: route.fulfill(status=200, content_type="application/json", body=watchlists),
    )
    page.route(
        "**/v1/markets/quotes**",
        lambda route: route.fulfill(status=200, content_type="application/json", body=quotes),
    )

    page.goto(f"{BASE_URL}/?lang=en", wait_until="networkidle")
    expect(page.locator("#watchlist").get_by_text("RKLB", exact=True)).to_be_visible()
    expect(page.locator("#watchlist").get_by_text("20", exact=True)).to_be_visible()
    expect(page.locator("#watchlist").get_by_text("+5.26%", exact=True)).to_be_visible()
    expect(page.locator("#watchlist").get_by_text("-2.17%", exact=True)).to_be_visible()
    assert page.locator("#watchlist .symbol-row").count() == 2


def test_screener_uses_watchlist_and_submits_multiple_filters(page: Page) -> None:
    watchlists = json.dumps(
        [
            {
                "id": 1,
                "name": "Main",
                "symbols": ["RKLB", "ASTS"],
                "created_at": "2026-08-01T00:00:00Z",
                "updated_at": "2026-08-12T00:00:00Z",
            }
        ]
    )
    submitted: list[dict[str, object]] = []

    page.route(
        "**/v1/watchlists",
        lambda route: route.fulfill(status=200, content_type="application/json", body=watchlists),
    )

    def screen_handler(route) -> None:
        submitted.append(route.request.post_data_json)
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "rows": [
                        {
                            "symbol": "RKLB",
                            "metrics": {
                                "revenue_growth_yoy": 0.31,
                                "operating_margin": 0.12,
                            },
                            "matched": True,
                            "failures": [],
                        }
                    ],
                    "evaluated_at": "2026-08-12T00:00:00Z",
                }
            ),
        )

    page.route("**/v1/screen", screen_handler)
    page.goto(f"{BASE_URL}/screener?lang=en", wait_until="networkidle")
    page.get_by_role("button", name="Use watchlist", exact=True).click()
    expect(page.locator("#screen-symbols")).to_have_value("RKLB, ASTS")

    page.get_by_role("button", name="+ Add", exact=True).click()
    assert page.locator(".screen-filter-row").count() == 2
    second = page.locator(".screen-filter-row").nth(1)
    second.locator(".screen-filter-metric").select_option("operating_margin")
    second.locator(".screen-filter-value").fill("0.1")
    page.get_by_role("button", name="Run", exact=True).click()

    expect(page.locator("#screen-results").get_by_text("RKLB", exact=True)).to_be_visible()
    assert submitted[0]["symbols"] == ["RKLB", "ASTS"]
    assert len(submitted[0]["filters"]) == 2
    assert submitted[0]["filters"][1]["metric"] == "operating_margin"
    assert submitted[0]["filters"][1]["value"] == 0.1
