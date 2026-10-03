import json
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import Page, Route, expect

BASE_URL = "http://127.0.0.1:8000"


def _provenance() -> dict[str, object]:
    return {
        "provider": "estat",
        "source": "Government of Japan e-Stat API",
        "source_url": "https://www.e-stat.go.jp/",
        "license_class": "official_public",
        "retrieved_at": "2026-08-18T09:00:00Z",
        "as_of": "2026-08-18",
        "notes": [],
    }


def _table() -> dict[str, object]:
    return {
        "stats_data_id": "0003427113",
        "stats_code": "00200573",
        "stat_name": "Consumer Price Index",
        "gov_org_code": "00200",
        "gov_org": "Statistics Bureau of Japan",
        "statistics_name": "2025-Base Consumer Price Index",
        "title": "Indexes by middle classification, Japan",
        "table_no": "1",
        "cycle": "Monthly",
        "survey_date": "202607",
        "open_date": "2026-08-18",
        "collect_area": "Japan",
        "main_category_code": "07",
        "main_category": "Prices",
        "sub_category_code": "01",
        "sub_category": "Consumer Price Index",
        "total_number": 2,
        "updated_at": "2026-08-18",
    }


def test_estat_search_dimensions_filter_and_exact_facts(page: Page) -> None:
    page_errors: list[str] = []
    requests: list[tuple[str, dict[str, list[str]]]] = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))

    dimensions = [
        {
            "id": "tab",
            "name": "Tabulated item",
            "items": [{"code": "1", "name": "Index", "level": 1, "unit": "2025=100"}],
        },
        {
            "id": "cat01",
            "name": "Item",
            "items": [
                {"code": "0001", "name": "All items", "level": 1},
                {"code": "0002", "name": "All items, less fresh food", "level": 1},
            ],
        },
        {
            "id": "area",
            "name": "Area",
            "items": [{"code": "00000", "name": "Japan", "level": 1}],
        },
        {
            "id": "time",
            "name": "Time",
            "items": [{"code": "2026070000", "name": "July 2026", "level": 1}],
        },
    ]

    def handler(route: Route) -> None:
        parsed = urlparse(route.request.url)
        path = parsed.path
        query = parse_qs(parsed.query)
        requests.append((path, query))
        if path == "/v1/macro/estat/tables":
            body: object = {
                "query": "Consumer Price Index",
                "tables": [_table()],
                "matched_count": 1,
                "next_key": None,
                "provenance": _provenance(),
            }
        elif path == "/v1/macro/estat/0003427113/meta":
            body = {
                "stats_data_id": "0003427113",
                "table": _table(),
                "dimensions": dimensions,
                "provenance": _provenance(),
            }
        elif path == "/v1/macro/estat/0003427113/data":
            body = {
                "stats_data_id": "0003427113",
                "total_number": 2,
                "from_number": 1,
                "to_number": 2,
                "next_key": None,
                "table": _table(),
                "dimensions": dimensions,
                "values": [
                    {
                        "value": "111.2000000000000001",
                        "numeric_value": "111.2000000000000001",
                        "unit": "2025=100",
                        "annotation": None,
                        "dimensions": {
                            "tab": "1",
                            "cat01": "0001",
                            "area": "00000",
                            "time": "2026070000",
                        },
                    },
                    {
                        "value": "109.8",
                        "numeric_value": "109.8",
                        "unit": "2025=100",
                        "annotation": "A1",
                        "dimensions": {
                            "tab": "1",
                            "cat01": "0002",
                            "area": "00000",
                            "time": "2026070000",
                        },
                    },
                ],
                "notes": {},
                "annotations": {"A1": "fixture annotation"},
                "provenance": _provenance(),
            }
        else:
            route.fallback()
            return
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    page.route("**/v1/macro/estat/**", handler)
    response = page.goto(f"{BASE_URL}/macro?lang=en", wait_until="networkidle")
    assert response is not None and response.ok

    page.locator("#estat-query").fill("Consumer Price Index")
    page.get_by_role("button", name="Search e-Stat").click()
    expect(page.locator("#estat-results")).to_contain_text("Indexes by middle classification")
    expect(page.locator("#estat-status")).to_contain_text("1 table")

    page.locator("#estat-results .estat-result").click()
    expect(page.locator("#estat-filters")).to_contain_text("cat01 · Item")
    expect(page.locator("#estat-filters")).to_contain_text("0002=All items, less fresh food")
    expect(page.locator("#estat-load-data")).to_be_visible()

    page.locator('[data-estat-dimension="tab"]').fill("1")
    page.locator('[data-estat-dimension="cat01"]').fill("0001,0002")
    page.locator('[data-estat-dimension="area"]').fill("00000")
    page.locator('[data-estat-dimension="time"]').fill("2026070000")
    page.get_by_role("button", name="Load facts").click()

    expect(page.locator("#estat-data")).to_contain_text("111.2000000000000001")
    expect(page.locator("#estat-data")).to_contain_text("All items, less fresh food")
    expect(page.locator("#estat-data")).to_contain_text("A1")
    expect(page.locator("#estat-data-status")).to_contain_text("2 shown / 2")
    expect(page.locator("#macro-provenance")).to_contain_text("Government of Japan e-Stat API")

    estat_requests = [item for item in requests if item[0].startswith("/v1/macro/estat/")]
    assert estat_requests[0][1]["lang"] == ["E"]
    assert estat_requests[1][1]["lang"] == ["E"]
    assert estat_requests[2][1]["lang"] == ["E"]
    assert estat_requests[2][1]["filter"] == [
        "tab=1",
        "cat01=0001,0002",
        "area=00000",
        "time=2026070000",
    ]
    assert page_errors == []
