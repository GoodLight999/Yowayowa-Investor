from __future__ import annotations

import re

from fastapi.testclient import TestClient

from yowayowa.api.app import app


def _input_tag(html: str, element_id: str) -> str:
    match = re.search(rf'<input[^>]*id="{re.escape(element_id)}"[^>]*>', html)
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


def test_global_ux_hardening_and_turbo_are_present() -> None:
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "@hotwired/turbo@8.0.23" in response.text
    assert "context-ai-drawer" in response.text
    assert "max-width: 100%; overflow-x: hidden" in response.text
    assert 'data-turbo-eval="false"' in response.text
