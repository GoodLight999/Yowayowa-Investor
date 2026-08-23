from starlette.testclient import TestClient

from yowayowa.config import get_settings


def test_macro_page_keeps_bea_catalog_available_for_browser_byok(monkeypatch) -> None:
    monkeypatch.setenv("YOWAYOWA_MODE", "personal")
    monkeypatch.delenv("YOWAYOWA_BEA_API_KEY", raising=False)
    monkeypatch.delenv("YOWAYOWA_API_TOKEN", raising=False)
    get_settings.cache_clear()

    from yowayowa.api.app import app

    with TestClient(app) as client:
        response = client.get("/macro?lang=ja")

    assert response.status_code == 200
    assert 'id="bea-catalog"' in response.text
    assert "/settings#data-sources" in response.text
    assert "このブラウザタブに保存" in response.text
    assert "サーバー側の無料登録APIキーが必要" not in response.text
