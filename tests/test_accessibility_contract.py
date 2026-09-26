from fastapi.testclient import TestClient

from yowayowa.api.app import app


def test_primary_navigation_exposes_current_page_and_locale_state() -> None:
    with TestClient(app) as client:
        home = client.get("/?lang=ja")
        markets = client.get("/markets?lang=en")

    assert home.status_code == 200
    assert 'href="/" aria-current="page"' in home.text
    assert 'data-locale="ja" lang="ja" aria-pressed="true"' in home.text
    assert 'data-locale="en" lang="en" aria-pressed="false"' in home.text
    assert 'aria-label="AIパネルを閉じる"' in home.text

    assert markets.status_code == 200
    assert 'href="/markets" aria-current="page"' in markets.text
    assert 'data-locale="ja" lang="ja" aria-pressed="false"' in markets.text
    assert 'data-locale="en" lang="en" aria-pressed="true"' in markets.text
    assert 'aria-label="Close AI panel"' in markets.text
