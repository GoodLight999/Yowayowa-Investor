import json

from playwright.sync_api import Page, expect

BASE_URL = "http://127.0.0.1:8000"


def market_payload() -> dict[str, object]:
    return {
        "items": [
            {
                "symbol": "^GSPC",
                "label": "S&P 500",
                "category": "US equities",
                "unit": "points",
                "value": 6500.25,
                "change_1d": 0.012,
                "change_1m": 0.035,
                "change_3m": 0.081,
                "change_1y": 0.142,
                "sparkline": [
                    ["2026-08-08T00:00:00Z", 6400.0],
                    ["2026-08-11T00:00:00Z", 6423.2],
                    ["2026-08-12T00:00:00Z", 6500.25],
                ],
                "as_of": "2026-08-12T00:00:00Z",
            },
            {
                "symbol": "BTC-USD",
                "label": "Bitcoin",
                "category": "Crypto",
                "unit": "USD",
                "value": 118000.0,
                "change_1d": -0.005,
                "change_1m": 0.02,
                "change_3m": 0.11,
                "change_1y": 0.55,
                "sparkline": [
                    ["2026-08-10T00:00:00Z", 119000.0],
                    ["2026-08-11T00:00:00Z", 118500.0],
                    ["2026-08-12T00:00:00Z", 118000.0],
                ],
                "as_of": "2026-08-12T00:00:00Z",
            },
        ],
        "unavailable_symbols": [],
        "provenance": {
            "provider": "yahoo/yfinance",
            "source": "Yahoo Finance",
            "source_url": "https://finance.yahoo.com/markets/",
            "license_class": "personal_only",
            "retrieved_at": "2026-08-12T00:01:00Z",
            "as_of": "2026-08-12T00:00:00Z",
            "notes": ["Batched daily history via yfinance."],
        },
    }


def portfolio_list_payload() -> list[dict[str, object]]:
    return [
        {
            "id": 1,
            "name": "Core",
            "base_currency": "USD",
            "positions": [
                {"symbol": "RKLB", "quantity": "100", "average_cost": "8", "currency": "USD"},
                {
                    "symbol": "7203.T",
                    "quantity": "10",
                    "average_cost": "2800",
                    "currency": "JPY",
                },
            ],
            "created_at": "2026-08-01T00:00:00Z",
            "updated_at": "2026-08-12T00:00:00Z",
        }
    ]


def portfolio_analytics_payload() -> dict[str, object]:
    return {
        "portfolio_id": 1,
        "name": "Core",
        "base_currency": "USD",
        "net_market_value": 2390.0,
        "gross_market_value": 2390.0,
        "known_cost_basis": 1864.0,
        "known_cost_market_value": 2390.0,
        "unrealized_pnl": 526.0,
        "unrealized_pnl_pct": 0.2821888412,
        "day_pnl": 21.88,
        "day_change_pct": 0.010077376,
        "largest_position_weight": 0.8368200837,
        "concentration_hhi": 0.7266515,
        "positions": [
            {
                "symbol": "RKLB",
                "quantity": "100",
                "currency": "USD",
                "average_cost": "8",
                "price": 20.0,
                "previous_close": 19.8,
                "fx_to_base": 1.0,
                "previous_fx_to_base": 1.0,
                "market_value_base": 2000.0,
                "cost_basis_base": 800.0,
                "unrealized_pnl_base": 1200.0,
                "unrealized_pnl_pct": 1.5,
                "day_pnl_base": 20.0,
                "day_change_pct": 0.0101010101,
                "weight": 0.8368200837,
                "as_of": "2026-08-12T00:00:00Z",
            },
            {
                "symbol": "7203.T",
                "quantity": "10",
                "currency": "JPY",
                "average_cost": "2800",
                "price": 6000.0,
                "previous_close": 5900.0,
                "fx_to_base": 0.0065,
                "previous_fx_to_base": 0.0064,
                "market_value_base": 390.0,
                "cost_basis_base": 182.0,
                "unrealized_pnl_base": 208.0,
                "unrealized_pnl_pct": 1.1428571429,
                "day_pnl_base": 1.88,
                "day_change_pct": 0.0049788136,
                "weight": 0.1631799163,
                "as_of": "2026-08-12T00:00:00Z",
            },
        ],
        "currency_exposure": [
            {"currency": "USD", "market_value_base": 2000.0, "weight": 0.8368200837},
            {"currency": "JPY", "market_value_base": 390.0, "weight": 0.1631799163},
        ],
        "unavailable_symbols": [],
        "provenance": {
            "provider": "fixture",
            "source": "Fixture market data",
            "source_url": None,
            "license_class": "personal_only",
            "retrieved_at": "2026-08-12T00:01:00Z",
            "as_of": "2026-08-12T00:00:00Z",
            "notes": [
                "Daily P/L includes both security-price and FX-rate movement "
                "when prior FX is available.",
                "Average-cost unrealized P/L is translated at current FX.",
            ],
        },
        "evaluated_at": "2026-08-12T00:01:00Z",
    }


def mock_market_api(page: Page) -> None:
    payload = json.dumps(market_payload())
    page.route(
        "**/v1/markets/overview",
        lambda route: route.fulfill(status=200, content_type="application/json", body=payload),
    )


def mock_portfolio_api(page: Page) -> None:
    portfolios = json.dumps(portfolio_list_payload())
    analytics = json.dumps(portfolio_analytics_payload())
    page.route(
        "**/v1/portfolios/1/analytics",
        lambda route: route.fulfill(status=200, content_type="application/json", body=analytics),
    )
    page.route(
        "**/v1/portfolios",
        lambda route: route.fulfill(status=200, content_type="application/json", body=portfolios),
    )


def test_primary_web_surfaces_render_without_javascript_errors(page: Page) -> None:
    page_errors: list[str] = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))

    for path, heading in (
        ("/", "Home"),
        ("/compare", "Compare"),
        ("/screener", "Screener"),
        ("/portfolio", "Portfolio"),
    ):
        if path == "/portfolio":
            mock_portfolio_api(page)
        response = page.goto(f"{BASE_URL}{path}", wait_until="networkidle")
        assert response is not None and response.ok
        expect(page.get_by_role("heading", name=heading, exact=True)).to_be_visible()

    assert page_errors == []


def test_overview_loads_shared_styles_and_search_is_wired(page: Page) -> None:
    search_payload = json.dumps(
        [
            {
                "symbol": "RKLB",
                "name": "Rocket Lab USA, Inc.",
                "exchange": None,
                "instrument_type": "equity",
                "currency": None,
                "cik": "0001819994",
            }
        ]
    )
    page.route(
        "**/v1/instruments/search**",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=search_payload,
        ),
    )
    page.goto(BASE_URL, wait_until="networkidle")
    stylesheet_expression = (
        "[...document.styleSheets]"
        ".map(s => s.href ? new URL(s.href).pathname : null)"
        ".filter(Boolean)"
    )
    loaded_styles = set(page.evaluate(stylesheet_expression))
    assert loaded_styles >= {
        "/static/styles.css",
        "/static/expansion.css",
        "/static/ux.css",
        "/static/product.css",
    }
    page.locator("#search-input").fill("RKLB")
    page.get_by_role("button", name="Search").click()
    expect(page.locator("#search-results").get_by_text("RKLB", exact=True)).to_be_visible()
    expect(page.get_by_text("Rocket Lab USA, Inc.", exact=True)).to_be_visible()


def test_comparison_metric_catalog_reaches_browser(page: Page) -> None:
    page.goto(f"{BASE_URL}/compare", wait_until="networkidle")
    picker = page.locator("#metric-picker")
    expect(picker.get_by_text("Revenue growth YoY")).to_be_visible()
    assert picker.locator("input:checked").count() >= 1


def test_compare_submit_renders_results(page: Page) -> None:
    payload = json.dumps(
        {
            "metrics": [
                {
                    "key": "revenue_growth_yoy",
                    "label": "Revenue growth YoY",
                    "display": "percent",
                    "higher_is_better": True,
                    "default_selected": True,
                }
            ],
            "rows": [
                {
                    "symbol": "RKLB",
                    "company_name": "Rocket Lab USA, Inc.",
                    "metrics": {"revenue_growth_yoy": 0.31},
                    "provenance": {
                        "provider": "fixture",
                        "source": "Fixture",
                        "source_url": None,
                        "license_class": "official_public",
                        "retrieved_at": "2026-08-12T00:00:00Z",
                        "as_of": "2026-08-12T00:00:00Z",
                        "notes": [],
                    },
                },
                {
                    "symbol": "ASTS",
                    "company_name": "AST SpaceMobile, Inc.",
                    "metrics": {"revenue_growth_yoy": 0.18},
                    "provenance": {
                        "provider": "fixture",
                        "source": "Fixture",
                        "source_url": None,
                        "license_class": "official_public",
                        "retrieved_at": "2026-08-12T00:00:00Z",
                        "as_of": "2026-08-12T00:00:00Z",
                        "notes": [],
                    },
                },
            ],
            "evaluated_at": "2026-08-12T00:00:00Z",
        }
    )
    page.route(
        "**/v1/compare",
        lambda route: route.fulfill(status=200, content_type="application/json", body=payload),
    )
    page.goto(f"{BASE_URL}/compare", wait_until="networkidle")
    page.locator("#compare-symbols").fill("RKLB, ASTS")
    page.get_by_role("button", name="Compare").click()
    expect(page.locator("#compare-results .compare-symbol strong").first).to_have_text("RKLB")
    expect(page.locator("#compare-results").get_by_text("+31%", exact=True)).to_be_visible()


def test_screener_submit_renders_results(page: Page) -> None:
    payload = json.dumps(
        {
            "rows": [
                {
                    "symbol": "RKLB",
                    "metrics": {"revenue_growth_yoy": 0.31},
                    "matched": True,
                    "failures": [],
                }
            ],
            "evaluated_at": "2026-08-12T00:00:00Z",
        }
    )
    page.route(
        "**/v1/screen",
        lambda route: route.fulfill(status=200, content_type="application/json", body=payload),
    )
    page.goto(f"{BASE_URL}/screener", wait_until="networkidle")
    page.locator("#filter-value").fill("0.2")
    page.get_by_role("button", name="Run").click()
    expect(page.locator("#screen-results").get_by_text("RKLB", exact=True)).to_be_visible()
    expect(page.locator("#screen-results").get_by_text("match", exact=True)).to_be_visible()


def test_market_overview_renders_cross_asset_api_contract(page: Page) -> None:
    mock_market_api(page)
    page_errors: list[str] = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))

    response = page.goto(f"{BASE_URL}/markets", wait_until="networkidle")
    assert response is not None and response.ok
    expect(page.get_by_role("heading", name="Markets", exact=True)).to_be_visible()
    expect(page.get_by_role("heading", name="S&P 500")).to_be_visible()
    expect(page.get_by_role("heading", name="Bitcoin")).to_be_visible()
    expect(page.get_by_text("+1.2%")).to_be_visible()
    expect(page.get_by_text("-0.5%")).to_be_visible()
    assert page.locator(".market-sparkline").count() == 2
    assert page_errors == []


def test_portfolio_analytics_renders_positions_exposure_and_provenance(page: Page) -> None:
    mock_portfolio_api(page)
    page_errors: list[str] = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))

    response = page.goto(f"{BASE_URL}/portfolio", wait_until="networkidle")
    assert response is not None and response.ok
    expect(page.get_by_role("heading", name="Portfolio", exact=True)).to_be_visible()
    expect(page.get_by_text("RKLB", exact=True)).to_be_visible()
    expect(page.get_by_text("7203.T", exact=True)).to_be_visible()
    assert page.locator(".portfolio-table tbody tr").count() == 2
    assert page.locator(".exposure-row").count() == 2
    expect(page.get_by_text("Fixture market data")).to_be_visible()
    expect(page.locator("#portfolio-summary").get_by_text("+83.68%", exact=True)).to_be_visible()
    assert page_errors == []


def test_mobile_surfaces_do_not_overflow_viewport(page: Page) -> None:
    mock_market_api(page)
    mock_portfolio_api(page)
    page.set_viewport_size({"width": 390, "height": 844})
    for path in ("/", "/markets", "/portfolio", "/compare", "/screener"):
        page.goto(f"{BASE_URL}{path}", wait_until="networkidle")
        overflow = page.evaluate(
            "document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
        )
        assert overflow is False, f"horizontal overflow detected on {path}"
