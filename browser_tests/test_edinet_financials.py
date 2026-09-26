import json
from urllib.parse import urlparse

from playwright.sync_api import Page, Route, expect

BASE_URL = "http://127.0.0.1:8000"


def test_edinet_company_history_to_normalized_financials_and_fact_search(page: Page) -> None:
    page_errors: list[str] = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))

    def provenance(provider: str = "edinet-v2") -> dict[str, object]:
        return {
            "provider": provider,
            "source": (
                "EDINET API Version 2 / local filing index"
                if provider == "edinet-v2-index"
                else "EDINET API Version 2"
            ),
            "source_url": "https://disclosure2.edinet-fsa.go.jp/",
            "license_class": "official_public",
            "retrieved_at": "2026-08-18T00:00:00Z",
            "as_of": "2026-06-20",
            "notes": [],
        }

    filing = {
        "doc_id": "S100TEST",
        "edinet_code": "E00001",
        "security_code": "72030",
        "filer_name": "Test Motors Co., Ltd.",
        "fund_code": None,
        "ordinance_code": "010",
        "form_code": "030000",
        "doc_type_code": "120",
        "description": "Annual Securities Report",
        "period_start": "2025-04-01",
        "period_end": "2026-03-31",
        "submitted_at": "2026-06-20T10:30:00",
        "xbrl_available": True,
        "csv_available": True,
        "legal_status": "1",
    }

    def handler(route: Route) -> None:
        path = urlparse(route.request.url).path
        if path == "/v1/filings/edinet/index/history":
            body: object = {
                "start_date": "2025-08-18",
                "end_date": "2026-08-18",
                "security_code": "72030",
                "edinet_code": None,
                "documents": [filing],
                "matched_count": 1,
                "indexed_days": 366,
                "expected_days": 366,
                "coverage_complete": True,
                "index_start": "2025-08-18",
                "index_end": "2026-08-17",
                "provenance": provenance("edinet-v2-index"),
            }
        elif path == "/v1/filings/edinet/documents":
            body = {
                "filing_date": "2026-06-20",
                "documents": [filing],
                "matched_count": 1,
                "total_count": 55,
                "provenance": provenance(),
            }
        elif path == "/v1/filings/edinet/S100TEST/financials":
            body = {
                "doc_id": "S100TEST",
                "company_name": "Test Motors Co., Ltd.",
                "edinet_code": "E00001",
                "security_code": "72030",
                "accounting_standard": "Japan GAAP",
                "document_type": "Annual Securities Report",
                "period_start": "2025-04-01",
                "period_end": "2026-03-31",
                "metrics": {
                    "revenue": [
                        {
                            "source_file": "XBRL_TO_CSV/report.csv",
                            "element_id": "jppfs_cor:NetSales",
                            "label": "Net sales",
                            "context_id": "CurrentYearDuration",
                            "relative_year": "Current",
                            "consolidation": "Consolidated",
                            "period_type": "Duration",
                            "unit_id": "JPY",
                            "unit": "JPY",
                            "value": "1000000000000",
                            "numeric_value": "1000000000000",
                        }
                    ],
                    "operating_income": [
                        {
                            "source_file": "XBRL_TO_CSV/report.csv",
                            "element_id": "jppfs_cor:OperatingIncome",
                            "label": "Operating income",
                            "context_id": "CurrentYearDuration",
                            "relative_year": "Current",
                            "consolidation": "Consolidated",
                            "period_type": "Duration",
                            "unit_id": "JPY",
                            "unit": "JPY",
                            "value": "123000000000",
                            "numeric_value": "123000000000",
                        }
                    ],
                },
                "unavailable_metrics": ["gross_profit"],
                "fact_count": 842,
                "source_files": ["XBRL_TO_CSV/report.csv"],
                "parse_warnings": [],
                "provenance": provenance(),
            }
        elif path == "/v1/filings/edinet/S100TEST/facts":
            body = {
                "doc_id": "S100TEST",
                "query": "NetSales",
                "facts": [
                    {
                        "source_file": "XBRL_TO_CSV/report.csv",
                        "element_id": "jppfs_cor:NetSales",
                        "label": "Net sales",
                        "context_id": "CurrentYearDuration",
                        "relative_year": "Current",
                        "consolidation": "Consolidated",
                        "period_type": "Duration",
                        "unit_id": "JPY",
                        "unit": "JPY",
                        "value": "1000000000000",
                    }
                ],
                "matched_count": 1,
                "total_count": 842,
                "source_files": ["XBRL_TO_CSV/report.csv"],
                "parse_warnings": [],
                "provenance": provenance(),
            }
        else:
            route.fallback()
            return
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    page.route("**/v1/filings/edinet/**", handler)
    response = page.goto(f"{BASE_URL}/edinet?lang=en", wait_until="networkidle")
    assert response is not None and response.ok

    page.locator("#edinet-history-security-code").fill("7203")
    page.locator("#edinet-history-start").fill("2025-08-18")
    page.locator("#edinet-history-end").fill("2026-08-18")
    page.get_by_role("button", name="Search history").last.click()
    history = page.locator("#edinet-history-documents")
    expect(history).to_contain_text("Test Motors Co., Ltd.")
    expect(page.locator("#edinet-history-status")).to_contain_text("1 filings")
    expect(page.locator("#edinet-history-coverage")).to_contain_text(
        "Complete coverage for the requested dates"
    )
    expect(page.locator("#edinet-history-coverage")).to_contain_text("2025-08-18 → 2026-08-17")

    history.get_by_role("button", name="Financials").click()
    expect(page.locator("#edinet-financial-panel")).to_be_visible()
    expect(page.locator("#edinet-meta")).to_contain_text("Japan GAAP")
    expect(page.locator("#edinet-metrics")).to_contain_text("Revenue / net sales")
    expect(page.locator("#edinet-metrics")).to_contain_text("1,000,000,000,000")
    expect(page.locator("#edinet-provenance")).to_contain_text("EDINET API Version 2")

    page.locator("#edinet-facts-panel > summary").click()
    page.locator("#edinet-fact-query").fill("NetSales")
    page.get_by_role("button", name="Search facts").click()
    expect(page.locator("#edinet-facts")).to_contain_text("jppfs_cor:NetSales")
    expect(page.locator("#edinet-fact-status")).to_contain_text("1 matched / 842")

    page.locator("details.edinet-advanced").first.locator("summary").click()
    page.locator("#edinet-date").fill("2026-06-20")
    page.locator("#edinet-security-code").fill("7203")
    page.get_by_role("button", name="Find filings").click()
    expect(page.locator("#edinet-documents")).to_contain_text("Test Motors Co., Ltd.")
    expect(page.locator("#edinet-document-status")).to_contain_text("1 matched / 55")
    assert page_errors == []
