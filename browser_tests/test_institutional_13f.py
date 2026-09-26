import json
from urllib.parse import urlparse

from playwright.sync_api import Page, Route, expect

BASE_URL = "http://127.0.0.1:8000"


def test_13f_page_labels_delayed_holdings_and_changes(page: Page) -> None:
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
        elif path == "/v1/institutional/13f/1067983":
            body = {
                "cik": "0001067983",
                "manager_name": "BERKSHIRE HATHAWAY INC",
                "filings": [
                    {
                        "accession_number": "0000950123-26-000001",
                        "form": "13F-HR",
                        "filing_date": "2026-05-15",
                        "report_date": "2026-03-31",
                        "primary_document": "primary_doc.xml",
                        "source_url": (
                            "https://www.sec.gov/Archives/edgar/data/1067983/fixture/infotable.xml"
                        ),
                        "holdings": [
                            {
                                "issuer": "APPLE INC",
                                "title_of_class": "COM",
                                "cusip": "037833100",
                                "reported_value_thousands": 100000,
                                "value_usd": 100000000,
                                "shares_or_principal": 1000000,
                                "amount_type": "SH",
                                "put_call": None,
                                "investment_discretion": "DFND",
                                "voting_sole": 0,
                                "voting_shared": 0,
                                "voting_none": 1000000,
                                "weight": 1.0,
                            }
                        ],
                        "total_value_usd": 100000000,
                        "provenance": {
                            "provider": "sec-edgar",
                            "source": "SEC EDGAR Form 13F information table",
                            "source_url": (
                                "https://www.sec.gov/Archives/edgar/data/1067983/"
                                "fixture/infotable.xml"
                            ),
                            "license_class": "official_public",
                            "retrieved_at": "2026-08-16T07:30:00Z",
                            "as_of": "2026-03-31",
                            "notes": [],
                        },
                    }
                ],
                "changes": [
                    {
                        "issuer": "APPLE INC",
                        "title_of_class": "COM",
                        "cusip": "037833100",
                        "put_call": None,
                        "status": "decreased",
                        "current_shares": 1000000,
                        "previous_shares": 1200000,
                        "share_change": -200000,
                        "share_change_fraction": -0.1666667,
                        "current_value_usd": 100000000,
                        "previous_value_usd": 120000000,
                    }
                ],
                "unavailable_filings": [],
                "retrieved_at": "2026-08-16T07:30:00Z",
                "notes": ["13F is a delayed quarterly disclosure."],
            }
        else:
            route.fallback()
            return
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(body),
        )

    page.route("**/v1/**", handler)
    response = page.goto(
        f"{BASE_URL}/institutional?lang=en&cik=1067983",
        wait_until="networkidle",
    )
    assert response is not None and response.ok
    expect(
        page.get_by_role("heading", name="US institutional holdings disclosures")
    ).to_be_visible()
    expect(page.get_by_text("Never interpret this table as real-time ownership.")).to_be_visible()
    expect(page.locator("#institutional-meta")).to_contain_text("BERKSHIRE HATHAWAY INC")
    expect(page.locator("#institutional-meta")).to_contain_text("45 days")
    expect(page.locator("#institutional-holdings")).to_contain_text("APPLE INC")
    expect(page.locator("#institutional-holdings")).to_contain_text("037833100")
    expect(page.locator("#institutional-changes")).to_contain_text("Decreased")
    expect(page.locator("#institutional-provenance")).to_contain_text(
        "SEC EDGAR Form 13F information table"
    )
    assert page_errors == []
