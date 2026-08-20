from __future__ import annotations

import re

from fastapi.testclient import TestClient

from yowayowa.api.app import app

_STATIC_ASSET_PATTERN = re.compile(
    r'(?:href|src)="(/assets/static/[0-9a-f]{16}/([^"/]+))"'
)
_PAGE_BUNDLE_PATTERN = re.compile(r'src="(/assets/page/[0-9a-f]{16}/([a-z_]+)\.js)"')


def _input_tag(html: str, element_id: str) -> str:
    match = re.search(rf'<input[^>]*id="{re.escape(element_id)}"[^>]*>', html)
    assert match is not None, element_id
    return match.group(0)


def _textarea_markup(html: str, element_id: str) -> str:
    match = re.search(
        rf'<textarea[^>]*id="{re.escape(element_id)}"[^>]*>.*?</textarea>',
        html,
        flags=re.DOTALL,
    )
    assert match is not None, element_id
    return match.group(0)


def test_beginner_ux_pages_explain_actions_and_use_neutral_examples() -> None:
    with TestClient(app) as client:
        home = client.get("/")
        charts = client.get("/charts")
        calendar = client.get("/calendar")
        edinet = client.get("/edinet")
        news = client.get("/news")
        settings = client.get("/settings")
        instrument = client.get("/instrument/AAPL")
        portfolio = client.get("/portfolio")
        compare = client.get("/compare")
        screener = client.get("/screener")
        alerts = client.get("/alerts")

    assert home.status_code == 200
    assert "ホーム" in home.text
    home_search = _input_tag(home.text, "search-input")
    assert "AAPL / Apple / 7203.T / トヨタ" in home_search
    assert "RKLB" not in home_search

    assert charts.status_code == 200
    assert "比較チャート" in charts.text
    assert "AAPLとMSFTの株価" in charts.text
    assert 'data-chart-example="correlation"' in charts.text

    assert calendar.status_code == 200
    assert "決算・経済イベント" in calendar.text
    assert "Economic release" in calendar.text
    assert 'data-calendar-range="30"' in calendar.text

    assert edinet.status_code == 200
    assert "金融庁" in edinet.text
    assert "会社別の提出履歴" in edinet.text
    assert 'id="edinet-company-search"' in edinet.text

    assert news.status_code == 200
    news_search = _input_tag(news.text, "news-query")
    assert "AAPL / Apple / 7203.T / トヨタ" in news_search
    assert "RKLB" not in news_search

    assert settings.status_code == 200
    assert "各リサーチ項目にAI窓を表示" in settings.text
    assert "OpenRouter OAuthで接続" in settings.text

    assert instrument.status_code == 200
    assert 'data-indicator-token="rsi14"' in instrument.text
    assert "財務から機械的に読めること" in instrument.text
    assert "アナリスト予想" in instrument.text
    assert "AI見通し（根拠付き）" in instrument.text

    portfolio_symbol = _input_tag(portfolio.text, "position-symbol")
    assert 'placeholder="AAPL"' in portfolio_symbol
    assert "RKLB" not in portfolio_symbol

    compare_symbols = _textarea_markup(compare.text, "compare-symbols")
    assert 'placeholder="AAPL, MSFT, 7203.T"' in compare_symbols
    assert "RKLB" not in compare_symbols

    screen_symbols = _textarea_markup(screener.text, "screen-symbols")
    assert ">AAPL, MSFT, 7203.T</textarea>" in screen_symbols
    assert "RKLB" not in screen_symbols

    alert_symbol = _input_tag(alerts.text, "alert-symbol")
    assert 'placeholder="AAPL"' in alert_symbol
    assert "RKLB" not in alert_symbol


def test_shared_assets_are_fingerprinted_external_and_immutable() -> None:
    required = {
        "styles.css",
        "expansion.css",
        "ux.css",
        "product.css",
        "pages.css",
        "product.js",
        "app.js",
        "ux.js",
        "instrument_context.js",
    }
    with TestClient(app) as client:
        response = client.get("/")
        matches = _STATIC_ASSET_PATTERN.findall(response.text)
        urls = {filename: url for url, filename in matches}
        assert required <= urls.keys()
        assert "/static/" not in response.text

        asset_responses = {filename: client.get(urls[filename]) for filename in required}
        stale_asset = client.get("/assets/static/0000000000000000/app.js")

    for asset_response in asset_responses.values():
        assert asset_response.status_code == 200
        assert asset_response.headers["cache-control"] == "public, max-age=31536000, immutable"
        assert asset_response.headers["x-content-type-options"] == "nosniff"
    assert "max-width: 100%; overflow-x: hidden" in asset_responses["ux.css"].text
    assert ".composer-layout" in asset_responses["pages.css"].text
    assert ".edinet-controls" in asset_responses["pages.css"].text
    assert stale_asset.status_code == 404


def test_page_specific_css_is_not_reembedded_in_html() -> None:
    with TestClient(app) as client:
        for path in ("/compare", "/charts", "/markets", "/rates", "/institutional", "/edinet"):
            response = client.get(path)
            assert response.status_code == 200, path
            assert "<style>" not in response.text, path


def test_browser_i18n_is_fingerprinted_external_and_immutable() -> None:
    pattern = re.compile(r'src="(/assets/i18n/(ja|en)/[0-9a-f]{16}\.js)"')
    with TestClient(app) as client:
        ja_page = client.get("/?lang=ja")
        en_page = client.get("/?lang=en")

        ja_match = pattern.search(ja_page.text)
        en_match = pattern.search(en_page.text)
        assert ja_match is not None
        assert en_match is not None
        assert ja_match.group(1) != en_match.group(1)
        assert "window.YOWAYOWA_I18N=" not in ja_page.text
        assert "window.YOWAYOWA_I18N=" not in en_page.text

        ja_asset = client.get(ja_match.group(1))
        en_asset = client.get(en_match.group(1))
        stale_asset = client.get("/assets/i18n/ja/0000000000000000.js")

    for response in (ja_asset, en_asset):
        assert response.status_code == 200
        assert response.text.startswith("window.YOWAYOWA_I18N=")
        assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
        assert response.headers["x-content-type-options"] == "nosniff"
    assert stale_asset.status_code == 404


def test_page_scripts_are_centralized_fingerprinted_bundles() -> None:
    pages = {
        "/": "dashboard",
        "/instrument/AAPL": "instrument",
        "/settings": "settings",
        "/charts": "charts",
        "/calendar": "calendar",
        "/ai": "ai",
        "/portfolio": "portfolio",
        "/markets": "market",
        "/discover": "discover",
        "/research/AAPL": "research",
        "/compare": "compare",
        "/screener": "screener",
        "/alerts": "alerts",
        "/news": "news",
        "/macro": "macro",
        "/rates": "rates",
        "/institutional": "institutional",
        "/edinet": "edinet",
    }
    with TestClient(app) as client:
        for path, expected_key in pages.items():
            response = client.get(path)
            assert response.status_code == 200, path
            match = _PAGE_BUNDLE_PATTERN.search(response.text)
            assert match is not None, path
            assert match.group(2) == expected_key, path
            assert "/static/" not in response.text, path
            bundle = client.get(match.group(1))
            assert bundle.status_code == 200, path
            assert bundle.headers["cache-control"] == "public, max-age=31536000, immutable"
            assert bundle.headers["x-content-type-options"] == "nosniff"

        no_bundle = client.get("/licenses")
        assert _PAGE_BUNDLE_PATTERN.search(no_bundle.text) is None
        stale_bundle = client.get("/assets/page/0000000000000000/instrument.js")
        unknown_bundle = client.get("/assets/page/0000000000000000/unknown.js")

    assert stale_bundle.status_code == 404
    assert unknown_bundle.status_code == 404
