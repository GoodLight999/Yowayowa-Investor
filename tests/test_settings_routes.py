from starlette.testclient import TestClient

from yowayowa.config import get_settings


def test_settings_status_reports_keyless_bls_as_available(monkeypatch) -> None:
    monkeypatch.setenv("YOWAYOWA_MODE", "personal")
    monkeypatch.delenv("YOWAYOWA_BLS_API_KEY", raising=False)
    monkeypatch.delenv("YOWAYOWA_API_TOKEN", raising=False)
    get_settings.cache_clear()

    from yowayowa.api.app import app

    with TestClient(app) as client:
        response = client.get("/v1/settings/status")

    assert response.status_code == 200
    status = response.json()
    assert status["bls"] is True
    assert status["sec"] is True
    assert status["yahoo_personal"] is True
