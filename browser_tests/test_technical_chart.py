import json
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import Page, Route, expect

BASE_URL = "http://127.0.0.1:8000"


def _provenance() -> dict[str, object]:
    return {
        "provider": "fixture",
        "source": "Fixture market history",
        "source_url": None,
        "license_class": "personal_only",
        "retrieved_at": "2026-08-16T06:50:00Z",
        "as_of": "2026-08-15T00:00:00Z",
        "notes": [],
    }


def _history() -> dict[str, object]:
    bars = []
    for day, close in enumerate([100.0, 101.0, 102.5, 101.8, 103.2], start=11):
        bars.append(
            {
                "timestamp": f"2026-08-{day:02d}T00:00:00Z",
                "open": close - 0.5,
                "high": close + 1.0,
                "low": close - 1.0,
                "close": close,
                "volume": 1_000_000 + day * 1000,
            }
        )
    times = [bar["timestamp"] for bar in bars]

    def series(
        name: str,
        values: list[float],
        *,
        pane: str = "price",
        render: str = "line",
        reference_lines: list[float] | None = None,
    ) -> dict[str, object]:
        return {
            "name": name,
            "parameters": {},
            "points": list(zip(times, values, strict=True)),
            "pane": pane,
            "render": render,
            "reference_lines": reference_lines or [],
        }

    return {
        "symbol": "RKLB",
        "interval": "1d",
        "bars": bars,
        "indicators": [
            series("SMA 20", [99.0, 99.5, 100.0, 100.5, 101.0]),
            series("Bollinger Lower 20", [95.0, 95.3, 95.8, 96.0, 96.5]),
            series("Bollinger Mid 20", [99.0, 99.5, 100.0, 100.5, 101.0]),
            series("Bollinger Upper 20", [103.0, 103.7, 104.2, 105.0, 105.5]),
            series("RSI 14", [45, 52, 58, 54, 61], pane="rsi", reference_lines=[30, 70]),
            series(
                "MACD 12/26/9",
                [-0.2, -0.1, 0.1, 0.2, 0.3],
                pane="macd",
                reference_lines=[0],
            ),
            series("MACD Signal 12/26/9", [-0.15, -0.08, 0.02, 0.1, 0.2], pane="macd"),
            series(
                "MACD Histogram 12/26/9",
                [-0.05, -0.02, 0.08, 0.1, 0.1],
                pane="macd",
                render="histogram",
            ),
            series("ATR 14", [2.0, 2.1, 2.2, 2.0, 2.3], pane="atr"),
        ],
        "provenance": _provenance(),
    }


def test_instrument_renders_expanded_multi_pane_indicators(page: Page) -> None:
    page_errors: list[str] = []
    requested_indicators: list[str] = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))

    def handler(route: Route) -> None:
        parsed = urlparse(route.request.url)
        path = parsed.path
        if path == "/v1/markets/RKLB/history":
            requested_indicators.append(parse_qs(parsed.query).get("indicators", [""])[0])
            body: object = _history()
        elif path == "/v1/markets/quotes":
            body = {
                "quotes": {
                    "RKLB": {
                        "symbol": "RKLB",
                        "price": 103.2,
                        "previous_close": 101.8,
                        "as_of": "2026-08-15T00:00:00Z",
                    }
                },
                "unavailable_symbols": [],
                "provenance": _provenance(),
            }
        elif path == "/v1/fundamentals/RKLB":
            body = {
                "symbol": "RKLB",
                "cik": "0001819994",
                "company_name": "Rocket Lab USA, Inc.",
                "metrics": {},
                "provenance": _provenance(),
            }
        elif path == "/v1/valuation/RKLB":
            body = {
                "symbol": "RKLB",
                "company_name": "Rocket Lab USA, Inc.",
                "price": 103.2,
                "shares_diluted": None,
                "market_cap": None,
                "annual_period_end": None,
                "metrics": {
                    "price_to_sales": None,
                    "price_to_earnings": None,
                    "price_to_book": None,
                    "price_to_free_cash_flow": None,
                    "earnings_yield": None,
                    "free_cash_flow_yield": None,
                },
                "provenance": [_provenance()],
                "evaluated_at": "2026-08-16T06:50:00Z",
            }
        elif path == "/v1/watchlists":
            body = []
        else:
            route.fallback()
            return
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    page.route("**/v1/**", handler)
    response = page.goto(f"{BASE_URL}/instrument/RKLB?lang=en", wait_until="networkidle")
    assert response is not None and response.ok

    expect(page.locator("#indicator-input")).to_have_value("sma20,bb20,rsi14,macd")
    expect(page.locator("#chart-source")).to_contain_text("Fixture market history")
    assert page.locator("#chart canvas").count() > 0
    chart_height = page.locator("#chart").evaluate(
        "element => element.getBoundingClientRect().height"
    )
    assert chart_height >= 700

    page.locator(".indicator-menu summary").click()
    page.locator('[data-indicator-token="sma20"]').uncheck()
    page.locator('[data-indicator-token="rsi14"]').uncheck()
    page.locator('[data-indicator-token="atr14"]').check()
    expect(page.locator("#indicator-input")).to_have_value("bb20,macd,atr14")
    page.get_by_role("button", name="Apply").click()
    expect(page.locator("#chart-source")).to_contain_text("Fixture market history")
    assert requested_indicators[-1] == "bb20,macd,atr14"
    assert page_errors == []
