import json

from playwright.sync_api import Page, Route, expect

BASE_URL = "http://127.0.0.1:8000"


def test_screener_exposes_richer_sec_metrics_and_formats_results(page: Page) -> None:
    requests: list[dict[str, object]] = []

    def handler(route: Route) -> None:
        requests.append(json.loads(route.request.post_data or "{}"))
        body = {
            "rows": [
                {
                    "symbol": "RKLB",
                    "metrics": {
                        "return_on_equity": 0.184,
                        "current_ratio": 2.4,
                        "free_cash_flow_margin": 0.123,
                        "revenue_growth_yoy": 0.31,
                    },
                    "matched": True,
                    "failures": [],
                }
            ],
            "evaluated_at": "2026-08-17T00:00:00Z",
        }
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    page.route("**/v1/screen", handler)
    response = page.goto(f"{BASE_URL}/screener?lang=en", wait_until="networkidle")
    assert response is not None and response.ok

    metric = page.locator("#filter-metric")
    expect(metric.locator('option[value="return_on_equity"]')).to_have_text("Return on equity")
    expect(metric.locator('option[value="current_ratio"]')).to_have_text("Current ratio")
    expect(metric.locator('option[value="free_cash_flow_margin"]')).to_have_text(
        "Free cash flow margin"
    )

    metric.select_option("return_on_equity")
    page.locator("#filter-value").fill("0.15")
    page.get_by_role("button", name="Run").click()

    expect(page.locator("#screen-results").get_by_text("RKLB", exact=True)).to_be_visible()
    expect(page.locator("#screen-results").get_by_text("18.4%", exact=True)).to_be_visible()
    assert requests[-1]["filters"] == [
        {"metric": "return_on_equity", "operator": "gt", "value": 0.15}
    ]
