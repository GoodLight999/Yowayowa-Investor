import json

from playwright.sync_api import Page, Route, expect

BASE_URL = "http://127.0.0.1:8000"
STRATEGY_ID = "kiyohara_global_value_growth"


def _provenance() -> dict[str, object]:
    return {
        "provider": "fixture",
        "source": "Fixture screener",
        "source_url": None,
        "license_class": "personal_only",
        "retrieved_at": "2026-08-25T00:00:00Z",
        "as_of": "2026-08-25T00:00:00Z",
        "notes": [],
    }


def test_builtin_kiyohara_strategy_applies_region_and_renders_bounds(page: Page) -> None:
    submitted: list[dict[str, object]] = []
    catalog = {
        "fields": {
            "identity": ["region"],
            "price": ["intradaymarketcap"],
            "valuation": ["peratio.lasttwelvemonths"],
        },
        "regions": ["jp", "us"],
        "predefined": ["most_actives"],
        "operators": ["eq", "is-in", "btwn", "gt", "lt", "gte", "lte"],
        "max_results": 250,
    }
    strategies = [
        {
            "id": STRATEGY_ID,
            "name_ja": "清原達郎モード（非公式）",
            "name_en": "Tatsuro Kiyohara style (unofficial)",
            "description_ja": "公開手法を使った候補発掘。",
            "description_en": "Candidate discovery based on published methodology.",
            "unofficial": True,
            "default_region": "jp",
            "region_required": True,
            "discovery": {
                "filters": [
                    {
                        "field": "peratio.lasttwelvemonths",
                        "operator": "btwn",
                        "value": [0.01, 20.0],
                    },
                    {"field": "intradaymarketcap", "operator": "gt", "value": 0},
                ],
                "predefined": None,
                "sort_field": "intradaymarketcap",
                "sort_ascending": True,
                "offset": 0,
                "size": 25,
            },
            "research_metrics": ["net_cash_ratio"],
            "qualitative_review_ja": [],
            "qualitative_review_en": [],
            "sources": [],
        }
    ]

    page.route(
        "**/v1/discover/catalog",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(catalog),
        ),
    )
    page.route(
        "**/v1/strategy-presets",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(strategies),
        ),
    )

    def screen_handler(route: Route) -> None:
        submitted.append(route.request.post_data_json)
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "quotes": [
                        {
                            "symbol": "7203.T",
                            "shortName": "Toyota",
                            "exchange": "JPX",
                            "intradayprice": 3000,
                            "intradaymarketcap": 100,
                            "peratio.lasttwelvemonths": 10,
                        }
                    ],
                    "total": 1,
                    "offset": 0,
                    "size": 25,
                    "query": {},
                    "provenance": _provenance(),
                }
            ),
        )

    page.route("**/v1/discover/screen", screen_handler)
    page.route(
        f"**/v1/strategy-presets/{STRATEGY_ID}/evaluate",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "strategy_id": STRATEGY_ID,
                    "evaluations": [
                        {
                            "symbol": "7203.T",
                            "company_name": "Toyota",
                            "market_cap": 100,
                            "pe_ratio": 10,
                            "current_assets": 120,
                            "liabilities": 40,
                            "investment_securities": None,
                            "net_cash": 80,
                            "net_cash_ratio": 0.8,
                            "net_cash_ratio_is_lower_bound": True,
                            "cash_neutral_pe": 2,
                            "cash_neutral_pe_is_upper_bound": True,
                            "revenue_growth_yoy": 0.1,
                            "net_income_growth_yoy": 0.1,
                            "free_cash_flow": 10,
                            "return_on_equity": 0.15,
                            "deep_value_net_cash": False,
                            "basis": "conservative_floor_ex_investment_securities",
                            "missing": ["investment_securities"],
                            "provenance": _provenance(),
                        }
                    ],
                    "errors": {},
                    "evaluated_at": "2026-08-25T00:00:00Z",
                }
            ),
        ),
    )

    response = page.goto(f"{BASE_URL}/discover?lang=en", wait_until="networkidle")
    assert response is not None and response.ok

    page.locator("#discover-preset").select_option(f"builtin:{STRATEGY_ID}")
    expect(page.locator("#discover-region")).to_have_value("jp")
    expect(page.locator("#discover-strategy-note")).to_be_visible()
    expect(page.locator("#discover-strategy-note")).to_contain_text("not an official")
    expect(page.locator(".research-filter-row")).to_have_count(2)
    expect(page.locator(".filter-field").nth(0)).to_have_value("peratio.lasttwelvemonths")
    expect(page.locator(".filter-operator").nth(0)).to_have_value("btwn")
    expect(page.locator(".filter-value input").nth(0)).to_have_value("0.01, 20")

    page.get_by_role("button", name="Run", exact=True).click()

    expect(page.locator("#discover-results").get_by_text("7203.T", exact=True)).to_be_visible()
    expect(page.locator("#discover-results").get_by_text("≥0.80×", exact=True)).to_be_visible()
    expect(page.locator("#discover-results").get_by_text("≤2.00×", exact=True)).to_be_visible()
    assert submitted[-1]["sort_field"] == "intradaymarketcap"
    assert submitted[-1]["sort_ascending"] is True
    assert submitted[-1]["filters"][0] == {
        "field": "region",
        "operator": "is-in",
        "value": ["jp"],
    }
