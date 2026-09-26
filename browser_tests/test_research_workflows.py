import json

from playwright.sync_api import Page, expect

BASE_URL = "http://127.0.0.1:8000"


def provenance(source: str = "Fixture") -> dict[str, object]:
    return {
        "provider": "fixture",
        "source": source,
        "source_url": None,
        "license_class": "personal_only",
        "retrieved_at": "2026-08-13T00:00:00Z",
        "as_of": "2026-08-13T00:00:00Z",
        "notes": [],
    }


def test_news_search_renders_localized_results(page: Page) -> None:
    payload = json.dumps(
        {
            "query": "RKLB",
            "items": [
                {
                    "id": "n1",
                    "title": "Rocket Lab update",
                    "publisher": "Example Wire",
                    "published_at": "2026-08-13T00:00:00Z",
                    "url": "https://example.test/rklb",
                    "summary": "A concise research update.",
                    "symbol": "RKLB",
                }
            ],
            "provenance": provenance("Yahoo Finance"),
        }
    )
    page.route(
        "**/v1/news/RKLB**",
        lambda route: route.fulfill(status=200, content_type="application/json", body=payload),
    )

    page.goto(f"{BASE_URL}/news?lang=ja", wait_until="networkidle")
    expect(page.get_by_role("heading", name="ニュース", exact=True)).to_be_visible()
    page.locator("#news-query").fill("RKLB")
    page.get_by_role("button", name="検索", exact=True).click()
    expect(page.get_by_text("Rocket Lab update", exact=True)).to_be_visible()
    expect(page.get_by_text("Example Wire", exact=False)).to_be_visible()


def test_calendar_filters_and_renders_event(page: Page) -> None:
    submitted_urls: list[str] = []
    payload = json.dumps(
        {
            "start": "2026-08-13",
            "end": "2026-08-20",
            "events": [
                {
                    "event_type": "earnings",
                    "starts_at": "2026-08-14T12:00:00Z",
                    "title": "Rocket Lab",
                    "symbol": "RKLB",
                    "details": {},
                }
            ],
            "provenance": provenance("Yahoo Finance"),
        }
    )

    def calendar_handler(route) -> None:
        submitted_urls.append(route.request.url)
        route.fulfill(status=200, content_type="application/json", body=payload)

    page.route("**/v1/calendar**", calendar_handler)
    page.goto(f"{BASE_URL}/calendar?lang=en", wait_until="networkidle")
    expect(page.get_by_role("heading", name="Earnings & market events", exact=True)).to_be_visible()
    page.locator("#calendar-start").fill("2026-08-13")
    page.locator("#calendar-end").fill("2026-08-20")
    page.locator("#calendar-symbol").fill("RKLB")
    page.get_by_role("button", name="Run", exact=True).click()
    expect(page.locator("#calendar-results").get_by_text("RKLB", exact=True)).to_be_visible()
    expect(page.locator("#calendar-results").get_by_text("Rocket Lab", exact=True)).to_be_visible()
    assert any("symbol=RKLB" in url for url in submitted_urls)


def test_alert_create_evaluate_and_delete_flow(page: Page) -> None:
    alerts: list[dict[str, object]] = []

    def alert_handler(route) -> None:
        request = route.request
        if request.method == "GET":
            route.fulfill(status=200, content_type="application/json", body=json.dumps(alerts))
            return
        if request.method == "POST":
            body = request.post_data_json
            created = {
                "id": 1,
                "symbol": body["symbol"],
                "operator": body["operator"],
                "target": body["target"],
                "enabled": True,
                "triggered_at": None,
                "last_price": None,
                "last_checked_at": None,
                "created_at": "2026-08-13T00:00:00Z",
            }
            alerts[:] = [created]
            route.fulfill(status=201, content_type="application/json", body=json.dumps(created))
            return
        route.fulfill(status=405)

    def evaluate_handler(route) -> None:
        if alerts:
            alerts[0].update(
                {
                    "enabled": False,
                    "triggered_at": "2026-08-13T00:01:00Z",
                    "last_price": "101",
                    "last_checked_at": "2026-08-13T00:01:00Z",
                }
            )
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "alerts": alerts,
                    "provenance": provenance("Yahoo Finance"),
                    "evaluated_at": "2026-08-13T00:01:00Z",
                }
            ),
        )

    page.route("**/v1/alerts/evaluate", evaluate_handler)
    page.route("**/v1/alerts", alert_handler)
    page.route(
        "**/v1/alerts/1",
        lambda route: (
            alerts.clear(),
            route.fulfill(status=204, body=""),
        ),
    )

    page.goto(f"{BASE_URL}/alerts?lang=en", wait_until="networkidle")
    manage = page.locator(".alerts-manage-panel")
    expect(manage).not_to_have_attribute("open", "")
    manage.locator("summary").click()
    expect(page.locator("#alert-symbol")).to_be_visible()
    page.locator("#alert-symbol").fill("RKLB")
    page.locator("#alert-target").fill("100")
    page.get_by_role("button", name="Add", exact=True).click()
    expect(page.locator("#alerts-list").get_by_text("RKLB", exact=True)).to_be_visible()
    page.get_by_role("button", name="Check now", exact=True).click()
    expect(page.locator("#alerts-list").get_by_text("Triggered", exact=True)).to_be_visible()
    page.locator("#alerts-list .alert-delete").click()
    expect(page.locator("#alerts-list").get_by_text("No price alerts.", exact=True)).to_be_visible()


def test_portfolio_csv_import_and_history_render(page: Page) -> None:
    portfolio = {
        "id": 1,
        "name": "Core",
        "base_currency": "USD",
        "positions": [],
        "created_at": "2026-08-01T00:00:00Z",
        "updated_at": "2026-08-13T00:00:00Z",
    }
    analytics = {
        "portfolio_id": 1,
        "name": "Core",
        "base_currency": "USD",
        "net_market_value": 100,
        "gross_market_value": 100,
        "known_cost_basis": 80,
        "known_cost_market_value": 100,
        "unrealized_pnl": 20,
        "unrealized_pnl_pct": 0.25,
        "day_pnl": 1,
        "day_change_pct": 0.01,
        "largest_position_weight": 1,
        "concentration_hhi": 1,
        "positions": [],
        "currency_exposure": [],
        "unavailable_symbols": [],
        "provenance": provenance(),
        "evaluated_at": "2026-08-13T00:00:00Z",
    }
    snapshots = [
        {
            "id": 1,
            "portfolio_id": 1,
            "net_market_value": 90,
            "gross_market_value": 90,
            "unrealized_pnl": 10,
            "day_pnl": 1,
            "captured_at": "2026-08-12T00:00:00Z",
        },
        {
            "id": 2,
            "portfolio_id": 1,
            "net_market_value": 100,
            "gross_market_value": 100,
            "unrealized_pnl": 20,
            "day_pnl": 1,
            "captured_at": "2026-08-13T00:00:00Z",
        },
    ]
    submitted: list[dict[str, object]] = []
    page.route(
        "**/v1/portfolios/1/analytics",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(analytics),
        ),
    )
    page.route(
        "**/v1/portfolios/1/snapshots**",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(snapshots),
        ),
    )
    page.route(
        "**/v1/portfolios",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps([portfolio]),
        ),
    )

    def bulk_handler(route) -> None:
        submitted.append(route.request.post_data_json)
        route.fulfill(status=200, content_type="application/json", body=json.dumps(portfolio))

    page.route("**/v1/portfolios/1/positions/bulk", bulk_handler)
    page.goto(f"{BASE_URL}/portfolio?lang=en", wait_until="networkidle")
    assert page.locator("#portfolio-history polyline").count() == 1
    page.locator("#portfolio-import-file").set_input_files(
        {
            "name": "positions.csv",
            "mimeType": "text/csv",
            "buffer": b"symbol,quantity,average_cost,currency\nRKLB,10,20,USD\nASTS,5,40,USD\n",
        }
    )
    page.get_by_role("button", name="Import CSV", exact=True).click()
    expect(page.locator("#portfolio-message")).to_have_text("Imported 2 positions.")
    assert len(submitted[0]["positions"]) == 2
    assert submitted[0]["positions"][0]["symbol"] == "RKLB"


def test_instrument_valuation_renders_without_external_chart_dependency(page: Page) -> None:
    chart_stub = """
    window.LightweightCharts={
      CrosshairMode:{MagnetOHLC:1},LineStyle:{Dashed:1},
      CandlestickSeries:{},HistogramSeries:{},LineSeries:{},
      createChart:()=>({
        addSeries:()=>({setData:()=>{},createPriceLine:()=>{}}),
        panes:()=>[{setHeight:()=>{}},{setHeight:()=>{}},{setHeight:()=>{}}],
        timeScale:()=>({fitContent:()=>{}}),remove:()=>{}
      })
    };
    """
    page.route(
        "https://unpkg.com/lightweight-charts@5.2.0/**",
        lambda route: route.fulfill(
            status=200,
            content_type="application/javascript",
            body=chart_stub,
        ),
    )
    page.route(
        "**/v1/fundamentals/RKLB",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "symbol": "RKLB",
                    "cik": "0001819994",
                    "company_name": "Rocket Lab USA, Inc.",
                    "metrics": {},
                    "provenance": provenance("SEC"),
                }
            ),
        ),
    )
    page.route(
        "**/v1/valuation/RKLB",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "symbol": "RKLB",
                    "company_name": "Rocket Lab USA, Inc.",
                    "price": 80,
                    "shares_diluted": 500000000,
                    "market_cap": 40000000000,
                    "annual_period_end": "2025-12-31",
                    "metrics": {
                        "price_to_sales": 20,
                        "price_to_earnings": None,
                        "price_to_book": 12,
                        "price_to_free_cash_flow": None,
                        "earnings_yield": None,
                        "free_cash_flow_yield": None,
                    },
                    "provenance": [provenance("SEC"), provenance("Yahoo Finance")],
                    "evaluated_at": "2026-08-13T00:00:00Z",
                }
            ),
        ),
    )
    page.route(
        "**/v1/markets/RKLB/history**",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "symbol": "RKLB",
                    "interval": "1d",
                    "bars": [
                        {
                            "timestamp": "2026-08-12T00:00:00Z",
                            "open": 79,
                            "high": 81,
                            "low": 78,
                            "close": 80,
                            "volume": 1000,
                        }
                    ],
                    "indicators": [],
                    "provenance": provenance("Yahoo Finance"),
                }
            ),
        ),
    )
    page.route(
        "**/v1/markets/quotes**",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "quotes": {
                        "RKLB": {
                            "symbol": "RKLB",
                            "price": 80,
                            "previous_close": 79,
                            "as_of": "2026-08-12T00:00:00Z",
                        }
                    },
                    "unavailable_symbols": [],
                    "provenance": provenance("Yahoo Finance"),
                }
            ),
        ),
    )
    page.route(
        "**/v1/watchlists",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                [
                    {
                        "id": 1,
                        "name": "Main",
                        "symbols": [],
                        "created_at": "2026-08-01T00:00:00Z",
                        "updated_at": "2026-08-13T00:00:00Z",
                    }
                ]
            ),
        ),
    )

    page.goto(f"{BASE_URL}/instrument/RKLB?lang=en", wait_until="networkidle")
    expect(page.get_by_role("heading", name="RKLB", exact=True)).to_be_visible()
    expect(
        page.locator("#valuation-metrics").get_by_text("Price / sales", exact=True)
    ).to_be_visible()
    expect(page.locator("#valuation-metrics").get_by_text("20\u00d7", exact=True)).to_be_visible()
    expect(page.locator("#valuation-basis")).to_have_text("Latest annual financials · 2025-12-31")
