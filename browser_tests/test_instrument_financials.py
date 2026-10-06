import json
from urllib.parse import urlparse

from playwright.sync_api import Page, Route, expect

BASE_URL = "http://127.0.0.1:8000"


def _point(
    start: str | None,
    end: str,
    fiscal_year: int,
    fiscal_period: str,
    value: float,
    unit: str = "USD",
) -> dict[str, object]:
    return {
        "period_start": start,
        "period_end": end,
        "fiscal_year": fiscal_year,
        "fiscal_period": fiscal_period,
        "value": value,
        "unit": unit,
        "accession": None,
        "filed": end,
        "form": "10-K" if fiscal_period == "FY" else "10-Q",
    }


def test_instrument_financials_switch_fy_and_reported_quarters(page: Page) -> None:
    page_errors: list[str] = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))

    provenance = {
        "provider": "sec-edgar",
        "source": "SEC EDGAR Company Facts",
        "source_url": "https://data.sec.gov/example",
        "license_class": "official_public",
        "retrieved_at": "2026-08-17T00:00:00Z",
        "as_of": "2026-08-17T00:00:00Z",
        "notes": [],
    }
    fundamentals = {
        "symbol": "RKLB",
        "cik": "0001819994",
        "company_name": "Rocket Lab USA, Inc.",
        "metrics": {
            "revenue": {
                "key": "revenue",
                "label": "Revenue",
                "points": [
                    _point("2024-01-01", "2024-12-31", 2024, "FY", 400),
                    _point("2024-04-01", "2024-06-30", 2024, "Q2", 95),
                    _point("2025-01-01", "2025-12-31", 2025, "FY", 520),
                    _point("2025-04-01", "2025-06-30", 2025, "Q2", 140),
                ],
            },
            "operating_income": {
                "key": "operating_income",
                "label": "Operating income",
                "points": [
                    _point("2024-01-01", "2024-12-31", 2024, "FY", -80),
                    _point("2025-01-01", "2025-12-31", 2025, "FY", -45),
                ],
            },
            "net_income": {
                "key": "net_income",
                "label": "Net income",
                "points": [
                    _point("2024-01-01", "2024-12-31", 2024, "FY", -90),
                    _point("2025-01-01", "2025-12-31", 2025, "FY", -60),
                ],
            },
            "cash": {
                "key": "cash",
                "label": "Cash and equivalents",
                "points": [_point(None, "2025-12-31", 2025, "FY", 430)],
            },
            "assets": {
                "key": "assets",
                "label": "Total assets",
                "points": [_point(None, "2025-12-31", 2025, "FY", 2100)],
            },
            "current_assets": {
                "key": "current_assets",
                "label": "Current assets",
                "points": [_point(None, "2025-12-31", 2025, "FY", 700)],
            },
            "current_liabilities": {
                "key": "current_liabilities",
                "label": "Current liabilities",
                "points": [_point(None, "2025-12-31", 2025, "FY", 300)],
            },
            "eps_diluted": {
                "key": "eps_diluted",
                "label": "Diluted EPS",
                "points": [_point("2025-01-01", "2025-12-31", 2025, "FY", -0.4, "USD/shares")],
            },
        },
        "provenance": provenance,
    }

    def handler(route: Route) -> None:
        parsed = urlparse(route.request.url)
        if parsed.path == "/v1/health":
            body: object = {
                "status": "ok",
                "mode": "personal",
                "market_provider": "yahoo",
                "capabilities": {},
            }
        elif parsed.path == "/v1/fundamentals/RKLB":
            body = fundamentals
        elif parsed.path == "/v1/screen":
            body = {
                "rows": [
                    {
                        "symbol": "RKLB",
                        "metrics": {
                            "revenue_growth_yoy": 0.30,
                            "operating_margin": -0.0865,
                            "net_margin": -0.1154,
                            "return_on_equity": -0.06,
                            "current_ratio": 2.33,
                            "free_cash_flow_margin": 0.12,
                            "liabilities_to_equity": 0.4,
                        },
                        "matched": True,
                        "failures": [],
                    }
                ],
                "evaluated_at": "2026-08-17T00:00:00Z",
            }
        elif parsed.path == "/v1/valuation/RKLB":
            body = {
                "symbol": "RKLB",
                "company_name": "Rocket Lab USA, Inc.",
                "price": 42,
                "shares_diluted": 500,
                "market_cap": 21000,
                "annual_period_end": "2025-12-31",
                "metrics": {
                    "price_to_sales": 4.0,
                    "price_to_earnings": None,
                    "price_to_book": 5.0,
                    "price_to_free_cash_flow": 20.0,
                    "earnings_yield": None,
                    "free_cash_flow_yield": 0.05,
                },
                "provenance": [provenance],
                "evaluated_at": "2026-08-17T00:00:00Z",
            }
        elif parsed.path == "/v1/markets/RKLB/history":
            body = {
                "symbol": "RKLB",
                "interval": "1d",
                "bars": [
                    {
                        "timestamp": "2026-08-14T00:00:00Z",
                        "open": 40,
                        "high": 43,
                        "low": 39,
                        "close": 42,
                        "volume": 1000000,
                    }
                ],
                "indicators": [],
                "provenance": {
                    **provenance,
                    "provider": "yahoo/yfinance",
                    "source": "Yahoo Finance",
                    "license_class": "personal_only",
                },
            }
        elif parsed.path == "/v1/markets/quotes":
            body = {
                "quotes": {
                    "RKLB": {
                        "symbol": "RKLB",
                        "price": 42,
                        "previous_close": 40,
                        "currency": "USD",
                        "as_of": "2026-08-17T00:00:00Z",
                    }
                },
                "unavailable_symbols": [],
                "provenance": {
                    **provenance,
                    "provider": "yahoo/yfinance",
                    "source": "Yahoo Finance",
                    "license_class": "personal_only",
                },
            }
        elif parsed.path == "/v1/watchlists":
            body = []
        else:
            route.fallback()
            return
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    page.route("**/v1/**", handler)
    response = page.goto(f"{BASE_URL}/instrument/RKLB?lang=en", wait_until="networkidle")
    assert response is not None and response.ok

    health = page.locator("#financial-health-metrics")
    expect(health).to_contain_text("Revenue growth YoY")
    expect(health).to_contain_text("+30%")
    expect(health).to_contain_text("Current ratio")
    expect(health).to_contain_text("2.33")

    table = page.locator("#fundamentals-table")
    expect(table).to_contain_text("FY 2025")
    expect(table).not_to_contain_text("Q2 2025")
    expect(table).to_contain_text("520")

    page.locator('[data-financial-view="quarterly"]').click()
    expect(table).to_contain_text("Q2 2025")
    expect(table).not_to_contain_text("FY 2025")
    expect(table).to_contain_text("140")
    assert page_errors == []
