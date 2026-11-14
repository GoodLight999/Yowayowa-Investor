import json
from urllib.parse import urlparse

from playwright.sync_api import Page, Route, expect

BASE_URL = "http://127.0.0.1:8000"


def clear_metric_selection(page: Page) -> None:
    page.locator("#metric-picker input").evaluate_all(
        "els => els.forEach(el => { el.checked = false; })"
    )


def test_screener_preset_round_trip_restores_research_conditions(page: Page) -> None:
    page_errors: list[str] = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))

    response = page.goto(f"{BASE_URL}/screener?lang=en", wait_until="networkidle")
    assert response is not None and response.ok

    page.locator("#screen-symbols").fill("RKLB, ASTS")
    page.locator("#filter-metric").select_option("revenue_growth_yoy")
    page.locator("#filter-operator").select_option("gt")
    page.locator("#filter-value").fill("0.2")
    page.locator("#screen-preset-name").fill("Browser growth preset")
    page.locator("#screen-preset-save").click()

    preset_select = page.locator("#screen-preset-select")
    expect(preset_select.locator("option", has_text="Browser growth preset")).to_have_count(1)
    expect(page.locator("#screen-preset-status")).to_have_text("Browser growth preset")

    page.locator("#screen-symbols").fill("HOOD")
    page.locator("#filter-metric").select_option("current_ratio")
    page.locator("#filter-value").fill("9")
    page.locator("#screen-preset-load").click()

    expect(page.locator("#screen-symbols")).to_have_value("RKLB, ASTS")
    expect(page.locator("#filter-metric")).to_have_value("revenue_growth_yoy")
    expect(page.locator("#filter-operator")).to_have_value("gt")
    expect(page.locator("#filter-value")).to_have_value("0.2")

    page.locator("#screen-preset-delete").click()
    expect(preset_select.locator("option", has_text="Browser growth preset")).to_have_count(0)
    assert page_errors == []


def test_chart_preset_round_trip_restores_sources_and_composes(page: Page) -> None:
    page_errors: list[str] = []
    compose_payloads: list[dict[str, object]] = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))

    def handler(route: Route) -> None:
        parsed = urlparse(route.request.url)
        if parsed.path != "/v1/charts/compose":
            route.fallback()
            return
        compose_payloads.append(json.loads(route.request.post_data or "{}"))
        body = {
            "series": [],
            "errors": {},
            "composed_at": "2026-08-17T00:00:00Z",
            "notes": [],
        }
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    page.route("**/v1/charts/compose", handler)
    response = page.goto(f"{BASE_URL}/charts?lang=en", wait_until="networkidle")
    assert response is not None and response.ok

    identifiers = page.locator(".composer-source-identifier")
    identifiers.nth(0).fill("RKLB")
    identifiers.nth(1).fill("RKLB")
    page.locator("#chart-preset-name").fill("Browser RKLB research")
    page.locator("#chart-preset-save").click()

    preset_select = page.locator("#chart-preset-select")
    expect(preset_select.locator("option", has_text="Browser RKLB research")).to_have_count(1)
    identifiers.nth(0).fill("HOOD")
    page.locator("#chart-preset-load").click()

    expect(identifiers.nth(0)).to_have_value("RKLB")
    expect(identifiers.nth(1)).to_have_value("RKLB")
    expect(page.locator("#chart-preset-status")).to_have_text("Browser RKLB research")
    expect(page.locator("#composer-status")).not_to_have_text("—")
    assert compose_payloads[-1]["sources"][0]["symbol"] == "RKLB"

    page.locator("#chart-preset-delete").click()
    expect(preset_select.locator("option", has_text="Browser RKLB research")).to_have_count(0)
    assert page_errors == []


def test_compare_preset_round_trip_restores_metrics_and_reruns(page: Page) -> None:
    page_errors: list[str] = []
    compare_payloads: list[dict[str, object]] = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))

    def handler(route: Route) -> None:
        parsed = urlparse(route.request.url)
        if parsed.path != "/v1/compare":
            route.fallback()
            return
        payload = json.loads(route.request.post_data or "{}")
        compare_payloads.append(payload)
        metrics = [
            {
                "key": key,
                "label": key,
                "display": "percent",
                "higher_is_better": True,
                "default_selected": False,
            }
            for key in payload["metrics"]
        ]
        rows = [
            {
                "symbol": symbol,
                "company_name": f"{symbol} Corp",
                "metrics": {key: 0.1 for key in payload["metrics"]},
                "provenance": {
                    "provider": "test",
                    "source": "fixture",
                    "license_class": "official_public",
                    "retrieved_at": "2026-08-17T00:00:00Z",
                },
            }
            for symbol in payload["symbols"]
        ]
        body = {"metrics": metrics, "rows": rows, "evaluated_at": "2026-08-17T00:00:00Z"}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    page.route("**/v1/compare", handler)
    response = page.goto(f"{BASE_URL}/compare?lang=en", wait_until="networkidle")
    assert response is not None and response.ok

    page.locator("#compare-symbols").fill("RKLB, ASTS, HOOD")
    clear_metric_selection(page)
    page.locator('#metric-picker input[value="revenue_growth_yoy"]').check()
    page.locator('#metric-picker input[value="return_on_equity"]').check()
    page.locator("#compare-preset-name").fill("Browser comparison preset")
    page.locator("#compare-preset-save").click()

    preset_select = page.locator("#compare-preset-select")
    expect(preset_select.locator("option", has_text="Browser comparison preset")).to_have_count(1)
    expect(page.locator("#compare-preset-status")).to_have_text("Browser comparison preset")

    page.locator("#compare-symbols").fill("SOFI, HOOD")
    clear_metric_selection(page)
    page.locator('#metric-picker input[value="operating_margin"]').check()
    page.locator("#compare-preset-load").click()

    expect(page.locator("#compare-symbols")).to_have_value("RKLB, ASTS, HOOD")
    expect(page.locator('#metric-picker input[value="revenue_growth_yoy"]')).to_be_checked()
    expect(page.locator('#metric-picker input[value="return_on_equity"]')).to_be_checked()
    expect(page.locator('#metric-picker input[value="operating_margin"]')).not_to_be_checked()
    expect(page.locator("#compare-meta")).to_have_text("3 issuers · 2 metrics")
    assert compare_payloads[-1] == {
        "symbols": ["RKLB", "ASTS", "HOOD"],
        "metrics": ["revenue_growth_yoy", "return_on_equity"],
    }

    page.locator("#compare-preset-delete").click()
    expect(preset_select.locator("option", has_text="Browser comparison preset")).to_have_count(0)
    assert page_errors == []
