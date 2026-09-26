import json
from urllib.parse import urlparse

from playwright.sync_api import Page, expect

BASE_URL = "http://127.0.0.1:8000"


def _json(route, payload: object, status: int = 200) -> None:
    route.fulfill(
        status=status,
        content_type="application/json",
        body=json.dumps(payload),
    )


def _provenance() -> dict[str, object]:
    return {
        "provider": "fixture",
        "source": "Fixture",
        "source_url": None,
        "license_class": "personal_only",
        "retrieved_at": "2026-08-19T00:00:00Z",
        "as_of": "2026-08-19T00:00:00Z",
        "notes": [],
    }


def test_home_groups_cross_listings_and_prefers_local_market_in_japanese(
    page: Page,
) -> None:
    rows = [
        {
            "symbol": "SONY",
            "name": "Sony Group Corporation",
            "exchange": "NYQ",
            "instrument_type": "equity",
            "currency": "USD",
            "cik": "0000313838",
        },
        {
            "symbol": "6758.T",
            "name": "Sony Group Corporation",
            "exchange": "JPX",
            "instrument_type": "equity",
            "currency": "JPY",
            "cik": None,
        },
        {
            "symbol": "SON1.F",
            "name": "Sony Group Corp",
            "exchange": "FRA",
            "instrument_type": "equity",
            "currency": "EUR",
            "cik": None,
        },
    ]
    page.route("**/v1/instruments/search**", lambda route: _json(route, rows))

    page.goto(f"{BASE_URL}/?lang=ja", wait_until="networkidle")
    page.locator("#search-input").fill("Sony")
    page.locator("#global-search").get_by_role("button", name="検索").click()

    expect(page.locator("#search-results .company-search-group")).to_have_count(1)
    expect(page.locator("#search-results .company-search-primary strong")).to_have_text("6758.T")
    expect(page.locator("#search-results .listing-preferred")).to_have_text("優先候補")
    expect(page.locator("#search-results .listing-alternatives summary")).to_contain_text(
        "他の上場先 2"
    )
    expect(page.locator("#search-results")).to_contain_text("JPX")
    expect(page.locator("#search-results")).to_contain_text("JPY")


def test_settings_hide_internal_notes_and_support_provider_and_model_filtering(
    page: Page,
) -> None:
    providers = [
        {
            "id": "openrouter",
            "label": "OpenRouter",
            "category": "cloud",
            "adapter": "openai_compatible",
            "base_url": "https://openrouter.ai/api/v1",
            "auth_modes": ["api_key", "oauth_pkce"],
            "note": "Router",
            "docs_url": None,
        },
        {
            "id": "openai",
            "label": "OpenAI",
            "category": "cloud",
            "adapter": "openai_compatible",
            "base_url": "https://api.openai.com/v1",
            "auth_modes": ["api_key"],
            "note": "OpenAI",
            "docs_url": None,
        },
        {
            "id": "anthropic",
            "label": "Anthropic",
            "category": "cloud",
            "adapter": "anthropic",
            "base_url": "https://api.anthropic.com",
            "auth_modes": ["api_key"],
            "note": "Anthropic",
            "docs_url": None,
        },
    ]
    status = {
        "sec": True,
        "yahoo_personal": True,
        "edinet": False,
        "estat": False,
        "fred": False,
        "bea": False,
    }
    models = {
        "models": [
            "openai/gpt-5.6",
            "anthropic/claude-opus-4.1",
            "google/gemini-3-pro",
        ]
    }
    page.route("**/v1/ai/providers", lambda route: _json(route, providers))
    page.route("**/v1/settings/status", lambda route: _json(route, status))
    page.route("**/v1/ai/models", lambda route: _json(route, models))

    page.goto(f"{BASE_URL}/settings?lang=ja", wait_until="networkidle")

    expect(page.get_by_text("Kilo Code", exact=False)).to_have_count(0)
    expect(page.get_by_text("OpenCodex", exact=False)).to_have_count(0)
    assert "Kilo Code" not in page.content()
    assert "OpenCodex" not in page.content()
    expect(page.get_by_text("openai_compatible", exact=False)).to_have_count(0)
    expect(page.locator(".provider-toggle")).to_have_count(3)
    expect(page.locator("#settings-provider option")).to_have_count(2)
    expect(page.locator(".provider-row")).to_have_count(1)
    page.locator(".provider-manager > summary").click()

    page.locator('.provider-toggle input[value="openai"]').check()
    expect(page.locator("#settings-provider option")).to_have_count(3)
    expect(page.locator(".provider-row")).to_have_count(2)

    page.locator("#settings-provider").select_option("openrouter")
    page.locator("#settings-api-key").fill("test-key")
    page.locator("#fetch-provider-models").click()
    expect(page.locator(".model-filter-bar")).to_be_visible()
    expect(page.locator("#model-results .model-result")).to_have_count(3)
    page.locator(".model-filter-bar input").fill("claude")
    expect(page.locator("#model-results .model-result:visible")).to_have_count(1)
    expect(page.locator(".model-filter-count")).to_have_text("1/3")

    edinet_panel = page.locator(".datasource-key-panel")
    expect(edinet_panel.get_by_role("heading")).to_contain_text("EDINET")
    expect(edinet_panel.get_by_role("link")).to_contain_text("APIキーを発行")
    expect(page.locator("#edinet-browser-key")).to_be_visible()

    for input_node in page.locator('.provider-toggle input[type="checkbox"]').all():
        box = input_node.bounding_box()
        assert box is not None
        assert abs(box["width"] - 16) <= 1
        assert abs(box["height"] - 16) <= 1


def test_calendar_uses_semantic_event_colors_and_high_impact_marker(
    page: Page,
) -> None:
    payload = {
        "start": "2026-08-19",
        "end": "2026-08-30",
        "events": [
            {
                "event_type": "economic",
                "subtype": None,
                "symbol": None,
                "title": "US CPI release",
                "starts_at": "2026-08-20T12:30:00Z",
                "ends_at": None,
            },
            {
                "event_type": "earnings",
                "subtype": "earnings",
                "symbol": "SONY",
                "title": "Sony earnings",
                "starts_at": "2026-08-21T07:00:00Z",
                "ends_at": None,
            },
            {
                "event_type": "split",
                "subtype": None,
                "symbol": "ABC",
                "title": "Stock split",
                "starts_at": "2026-08-22T00:00:00Z",
                "ends_at": None,
            },
        ],
        "provenance": _provenance(),
    }
    page.route("**/v1/calendar?**", lambda route: _json(route, payload))
    page.route("**/v1/watchlists", lambda route: _json(route, []))
    page.route("**/v1/portfolios", lambda route: _json(route, []))

    page.goto(f"{BASE_URL}/calendar?lang=ja", wait_until="networkidle")
    expect(page.locator("#calendar-results tbody tr")).to_have_count(3)
    expect(page.locator("#calendar-results tbody tr.event-economic.event-high")).to_have_count(1)
    expect(page.locator("#calendar-results .event-importance")).to_have_text("重要")
    expect(page.locator("#calendar-results tbody tr.event-earnings")).to_have_count(1)
    expect(page.locator("#calendar-results tbody tr.event-split")).to_have_count(1)

    backgrounds = page.locator("#calendar-results .event-badge").evaluate_all(
        "els => els.map(el => getComputedStyle(el).backgroundColor)"
    )
    assert len(set(backgrounds)) >= 3

    selector = '#calendar-form input[type="checkbox"]:visible'
    for input_node in page.locator(selector).all():
        box = input_node.bounding_box()
        assert box is not None
        assert abs(box["width"] - 16) <= 1
        assert abs(box["height"] - 16) <= 1


def test_japanese_instrument_is_company_hub_with_news_events_and_edinet_filings(
    page: Page,
) -> None:
    seen_edinet: list[str] = []

    def edinet_handler(route) -> None:
        seen_edinet.append(urlparse(route.request.url).query)
        _json(
            route,
            {
                "filings": [
                    {
                        "doc_id": "S100TEST",
                        "doc_description": "有価証券報告書",
                        "submit_date_time": "2026-06-20T10:30:00+09:00",
                    }
                ],
                "coverage_complete": True,
                "indexed_days": 10,
                "expected_days": 10,
            },
        )

    page.route(
        "**/v1/news/7203.T**",
        lambda route: _json(
            route,
            {
                "items": [
                    {
                        "title": "Toyota update",
                        "publisher": "Fixture",
                        "published_at": "2026-08-19T00:00:00Z",
                        "url": None,
                    }
                ],
                "provenance": _provenance(),
            },
        ),
    )
    page.route(
        "**/v1/calendar?**symbol=7203.T**",
        lambda route: _json(
            route,
            {
                "start": "2026-08-19",
                "end": "2026-11-17",
                "events": [
                    {
                        "event_type": "earnings",
                        "subtype": "earnings",
                        "symbol": "7203.T",
                        "title": "決算予定",
                        "starts_at": "2026-11-05T06:00:00Z",
                        "ends_at": None,
                    }
                ],
                "provenance": _provenance(),
            },
        ),
    )
    page.route("**/v1/filings/edinet/index/history**", edinet_handler)

    page.goto(
        f"{BASE_URL}/instrument/7203.T?lang=ja",
        wait_until="domcontentloaded",
    )
    expect(page.locator(".instrument-section-nav")).to_be_visible()
    expect(page.locator(".instrument-section-nav")).to_contain_text("ニュース")
    expect(page.locator(".instrument-section-nav")).to_contain_text("イベント")
    expect(page.locator(".instrument-section-nav")).to_contain_text("開示書類")
    expect(page.get_by_role("heading", name="この会社のニュース")).to_be_visible()
    expect(page.get_by_role("heading", name="この会社の今後のイベント")).to_be_visible()
    expect(page.get_by_role("heading", name="決算・開示書類")).to_be_visible()
    expect(page.locator("#company-news-list")).to_contain_text("Toyota update")
    expect(page.locator("#company-events-list")).to_contain_text("決算予定")
    expect(page.locator("#company-filings-list")).to_contain_text("有価証券報告書")
    assert seen_edinet and "security_code=7203" in seen_edinet[0]
