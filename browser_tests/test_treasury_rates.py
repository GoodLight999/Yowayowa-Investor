import json
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import Page, Route, expect

BASE_URL = "http://127.0.0.1:8000"


def test_treasury_curve_renders_official_points_and_spreads(page: Page) -> None:
    page_errors: list[str] = []
    requested_years: list[str] = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))

    def handler(route: Route) -> None:
        parsed = urlparse(route.request.url)
        if parsed.path == "/v1/health":
            body: object = {
                "status": "ok",
                "mode": "personal",
                "market_provider": "yahoo",
                "capabilities": {},
            }
        elif parsed.path == "/v1/rates/treasury/curve":
            requested_years.append(parse_qs(parsed.query).get("year", [""])[0])
            body = {
                "latest": {
                    "date": "2026-08-15",
                    "points": [
                        {"maturity": "3M", "years": 0.25, "yield_percent": 4.01},
                        {"maturity": "2Y", "years": 2.0, "yield_percent": 3.71},
                        {"maturity": "10Y", "years": 10.0, "yield_percent": 4.21},
                        {"maturity": "30Y", "years": 30.0, "yield_percent": 4.81},
                    ],
                    "spread_10y_2y": 0.50,
                    "spread_10y_3m": 0.20,
                },
                "history": [
                    {
                        "date": "2026-08-14",
                        "points": [],
                        "spread_10y_2y": 0.48,
                        "spread_10y_3m": 0.18,
                    },
                    {
                        "date": "2026-08-15",
                        "points": [],
                        "spread_10y_2y": 0.50,
                        "spread_10y_3m": 0.20,
                    },
                ],
                "provenance": {
                    "provider": "us-treasury",
                    "source": (
                        "U.S. Department of the Treasury Daily Treasury Par Yield Curve Rates"
                    ),
                    "source_url": (
                        "https://home.treasury.gov/resource-center/data-chart-center/"
                        "interest-rates/pages/xml"
                    ),
                    "license_class": "official_public",
                    "retrieved_at": "2026-08-16T07:20:00Z",
                    "as_of": "2026-08-15",
                    "notes": [],
                },
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
    response = page.goto(f"{BASE_URL}/rates?lang=en", wait_until="networkidle")
    assert response is not None and response.ok

    expect(page.get_by_role("heading", name="Treasury Yield Curve")).to_be_visible()
    expect(page.locator("#yield-date")).to_have_text("2026-08-15")
    expect(page.locator("#yield-table")).to_contain_text("10Y")
    expect(page.locator("#yield-table")).to_contain_text("4.21%")
    expect(page.locator("#spread-10y-2y")).to_contain_text("+0.5 pp")
    expect(page.locator("#spread-10y-3m")).to_contain_text("+0.2 pp")
    expect(page.locator("#rate-provenance")).to_contain_text("U.S. Department of the Treasury")
    assert page.locator("#yield-curve-chart canvas").count() > 0
    assert page.locator("#yield-spread-chart canvas").count() > 0

    page.locator("#rate-year").fill("2025")
    page.get_by_role("button", name="Load curve").click()
    expect(page.locator("#yield-date")).to_have_text("2026-08-15")
    assert requested_years[-1] == "2025"
    assert page_errors == []
