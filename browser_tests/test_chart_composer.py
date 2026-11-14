import json
from urllib.parse import urlparse

from playwright.sync_api import Page, Route, expect

BASE_URL = "http://127.0.0.1:8000"


def _provenance(provider: str) -> dict[str, object]:
    return {
        "provider": provider,
        "source": f"{provider} fixture",
        "source_url": None,
        "license_class": "official_public",
        "retrieved_at": "2026-08-16T07:00:00Z",
        "as_of": "2026-08-15T00:00:00Z",
        "notes": [],
    }


def test_chart_composer_keeps_successful_series_when_one_source_fails(page: Page) -> None:
    page_errors: list[str] = []
    requests: list[dict[str, object]] = []
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
        elif path == "/v1/watchlists":
            body = []
        elif path == "/v1/charts/compose":
            requests.append(route.request.post_data_json)
            body = {
                "series": [
                    {
                        "id": "px",
                        "label": "AAPL price",
                        "kind": "source",
                        "source": "price",
                        "transform": None,
                        "unit": "USD",
                        "points": [
                            {"date": "2026-08-13", "value": 100.0},
                            {"date": "2026-08-14", "value": 102.0},
                            {"date": "2026-08-15", "value": 103.0},
                        ],
                        "provenance": [_provenance("market")],
                    },
                    {
                        "id": "rev",
                        "label": "AAPL revenue",
                        "kind": "source",
                        "source": "fundamental",
                        "transform": None,
                        "unit": "USD",
                        "points": [
                            {"date": "2026-06-30", "value": 90.0},
                            {"date": "2026-08-15", "value": 100.0},
                        ],
                        "provenance": [_provenance("sec")],
                    },
                    {
                        "id": "f1",
                        "label": "AAPL price / AAPL revenue",
                        "kind": "derived",
                        "source": None,
                        "transform": "ratio",
                        "unit": "ratio",
                        "points": [
                            {"date": "2026-08-13", "value": 1.0},
                            {"date": "2026-08-14", "value": 1.02},
                            {"date": "2026-08-15", "value": 1.03},
                        ],
                        "provenance": [_provenance("market"), _provenance("sec")],
                    },
                ],
                "errors": {"s1": "FRED requires YOWAYOWA_FRED_API_KEY"},
                "composed_at": "2026-08-16T07:01:00Z",
                "notes": ["no backfill/look-ahead"],
            }
        else:
            route.fallback()
            return
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    page.route("**/v1/**", handler)
    response = page.goto(f"{BASE_URL}/charts?lang=en", wait_until="networkidle")
    assert response is not None and response.ok

    expect(page.get_by_role("heading", name="Compare charts")).to_be_visible()
    expect(page.locator("#composer-sources .composer-row")).to_have_count(2)

    page.get_by_role("button", name="+ Add source").click()
    third = page.locator("#composer-sources .composer-row").nth(2)
    third.locator(".composer-source-type").select_option("fred")
    third.locator(".composer-source-identifier").fill("CPIAUCSL")

    page.get_by_role("button", name="+ Add formula").click()
    formula = page.locator("#composer-formulas .composer-row").first
    formula.locator(".composer-formula-kind").select_option("ratio")
    formula.locator(".composer-formula-left").select_option("px")
    formula.locator(".composer-formula-right").select_option("rev")

    page.get_by_role("button", name="Show chart").click()
    expect(page.locator("#composer-errors")).to_contain_text("s1")
    expect(page.locator("#composer-errors")).to_contain_text("YOWAYOWA_FRED_API_KEY")
    expect(page.locator("#composer-provenance")).to_contain_text("AAPL price")
    expect(page.locator("#composer-provenance")).to_contain_text("AAPL revenue")
    assert page.locator("#composer-chart canvas").count() > 0

    payload = requests[-1]
    assert [item["id"] for item in payload["sources"]] == ["px", "rev", "s1"]
    assert payload["sources"][2]["source"] == "fred"
    assert payload["sources"][2]["series_id"] == "CPIAUCSL"
    assert payload["transforms"][0]["id"] == "f1"
    assert payload["transforms"][0]["left"] == "px"
    assert payload["transforms"][0]["right"] == "rev"
    assert page_errors == []
