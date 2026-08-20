from __future__ import annotations

import re

from fastapi.testclient import TestClient

from yowayowa.api.app import app


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


def test_global_ux_hardening_and_turbo_use_shared_static_assets() -> None:
    with TestClient(app) as client:
        response = client.get("/")
        ux_css = client.get("/static/ux.css")
        pages_css = client.get("/static/pages.css")

    assert response.status_code == 200
    assert "@hotwired/turbo@8.0.23" in response.text
    assert "context-ai-drawer" in response.text
    assert 'href="/static/styles.css"' in response.text
    assert 'href="/static/expansion.css"' in response.text
    assert 'href="/static/ux.css"' in response.text
    assert 'href="/static/product.css"' in response.text
    assert 'href="/static/pages.css"' in response.text
    assert 'src="/static/app.js" data-turbo-eval="false"' in response.text
    assert 'src="/static/ux.js" data-turbo-eval="false"' in response.text
    assert "max-width: 100%; overflow-x: hidden" not in response.text
    assert ux_css.status_code == 200
    assert pages_css.status_code == 200
    assert "max-width: 100%; overflow-x: hidden" in ux_css.text
    assert ".composer-layout" in pages_css.text
    assert ".edinet-controls" in pages_css.text


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


def test_page_scripts_are_external_static_assets() -> None:
    pages = {
        "/instrument/AAPL": ("instrument.js", "instrument_ux.js"),
        "/settings": ("settings.js",),
        "/charts": ("charts.js", "charts_ux.js"),
        "/calendar": ("calendar.js", "calendar_ux.js"),
        "/ai": ("ai.js",),
        "/portfolio": ("portfolio.js",),
        "/markets": ("market.js",),
        "/discover": ("discover.js",),
        "/research/AAPL": ("research.js",),
        "/compare": ("compare.js",),
        "/screener": ("screener.js",),
        "/alerts": ("alerts.js",),
        "/news": ("news.js",),
        "/macro": ("macro.js",),
        "/rates": ("rates.js",),
        "/institutional": ("institutional.js",),
        "/edinet": ("edinet.js", "edinet_ux.js"),
    }
    with TestClient(app) as client:
        for path, assets in pages.items():
            response = client.get(path)
            assert response.status_code == 200, path
            for asset in assets:
                assert f'src="/static/{asset}"' in response.text, (path, asset)
