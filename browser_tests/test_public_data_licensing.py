import json
from urllib.parse import urlparse

from playwright.sync_api import Page, Route, expect

BASE_URL = "http://127.0.0.1:8000"


def test_macro_prefers_bls_public_domain_series(page: Page) -> None:
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
        elif path == "/v1/macro/bls/catalog":
            body = {
                "series": [
                    {
                        "series_id": "CUSR0000SA0",
                        "title": "Consumer Price Index — All urban consumers, all items",
                        "unit": "Index 1982-84=100",
                        "seasonal_adjustment": "Seasonally adjusted",
                        "category": "Inflation",
                    }
                ],
                "provenance": {
                    "provider": "bls",
                    "source": "U.S. Bureau of Labor Statistics Public Data API",
                    "source_url": "https://www.bls.gov/developers/",
                    "license_class": "official_public",
                    "retrieved_at": "2026-08-18T00:00:00Z",
                    "as_of": "2026-08-18T00:00:00Z",
                    "notes": [],
                },
            }
        elif path == "/v1/macro/bls/CUSR0000SA0":
            body = {
                "series_id": "CUSR0000SA0",
                "title": "Consumer Price Index — All urban consumers, all items",
                "unit": "Index 1982-84=100",
                "seasonal_adjustment": "Seasonally adjusted",
                "observations": [
                    {
                        "year": 2026,
                        "period": "M01",
                        "period_name": "January",
                        "date": "2026-01-01",
                        "value": 320.1,
                        "footnotes": [],
                    },
                    {
                        "year": 2026,
                        "period": "M02",
                        "period_name": "February",
                        "date": "2026-02-01",
                        "value": 321.4,
                        "footnotes": [],
                    },
                ],
                "provenance": {
                    "provider": "bls",
                    "source": "U.S. Bureau of Labor Statistics Public Data API",
                    "source_url": "https://data.bls.gov/timeseries/CUSR0000SA0",
                    "license_class": "official_public",
                    "retrieved_at": "2026-08-18T00:00:00Z",
                    "as_of": "2026-08-18T00:00:00Z",
                    "notes": [],
                },
            }
        else:
            route.fallback()
            return
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    page.route("**/v1/**", handler)
    response = page.goto(f"{BASE_URL}/macro?lang=en", wait_until="networkidle")
    assert response is not None and response.ok

    expect(page.get_by_role("heading", name="Official public macro")).to_be_visible()
    expect(page.locator("#bls-catalog")).to_contain_text("CUSR0000SA0")
    page.locator('.bls-result[data-id="CUSR0000SA0"]').click()
    expect(page.locator("#macro-series")).to_contain_text("321.4")
    expect(page.locator("#macro-provenance")).to_contain_text("official_public")
    expect(page.locator("#macro-series svg")).to_be_visible()
    assert page_errors == []


def test_license_page_exposes_restricted_and_public_sources(page: Page) -> None:
    response = page.goto(f"{BASE_URL}/licenses?lang=en", wait_until="networkidle")
    assert response is not None and response.ok

    expect(page.get_by_role("heading", name="Data sources & licenses")).to_be_visible()
    expect(
        page.get_by_text("U.S. Securities and Exchange Commission EDGAR", exact=True)
    ).to_be_visible()
    expect(page.get_by_text("Financial Services Agency EDINET", exact=True)).to_be_visible()
    expect(page.get_by_text("U.S. Bureau of Labor Statistics", exact=True)).to_be_visible()
    expect(page.get_by_text("Federal Reserve Economic Data (FRED)", exact=True)).to_be_visible()
    expect(page.get_by_text("Yahoo Finance via yfinance", exact=True)).to_be_visible()
    expect(page.get_by_text("Public mode fails closed", exact=False)).to_be_visible()
