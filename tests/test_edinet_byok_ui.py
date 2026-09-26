from starlette.testclient import TestClient

from yowayowa.config import get_settings


def test_personal_edinet_page_explains_browser_byok_without_server_key(monkeypatch) -> None:
    monkeypatch.setenv("YOWAYOWA_MODE", "personal")
    monkeypatch.delenv("YOWAYOWA_EDINET_API_KEY", raising=False)
    monkeypatch.delenv("YOWAYOWA_API_TOKEN", raising=False)
    get_settings.cache_clear()

    from yowayowa.api.app import app

    with TestClient(app) as client:
        response = client.get("/edinet?lang=ja")

    assert response.status_code == 200
    assert "/settings#data-sources" in response.text
    assert "このブラウザタブに無料キーを保存" in response.text
    assert "履歴インデックスはキーなしでも検索できます" in response.text


def test_public_edinet_page_keeps_server_only_key_guidance(monkeypatch) -> None:
    monkeypatch.setenv("YOWAYOWA_MODE", "public")
    monkeypatch.setenv("YOWAYOWA_API_TOKEN", "secret")
    monkeypatch.delenv("YOWAYOWA_EDINET_API_KEY", raising=False)
    get_settings.cache_clear()

    from yowayowa.api.app import app

    with TestClient(app) as client:
        response = client.get("/edinet?lang=en")

    assert response.status_code == 200
    assert "The server has no EDINET API key" in response.text
    assert "/settings#data-sources" not in response.text
