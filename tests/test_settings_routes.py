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
    assert status["private_connectors"] is True
    assert status["scraping"] is True
    assert status["broker_control"] is True
    assert status["broker_live_orders"] is False


def test_vercel_defaults_full_operator_capabilities_off(monkeypatch) -> None:
    from yowayowa.config import Settings

    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("YOWAYOWA_PRIVATE_CONNECTORS_ENABLED", raising=False)
    monkeypatch.delenv("YOWAYOWA_SCRAPING_ENABLED", raising=False)
    monkeypatch.delenv("YOWAYOWA_BROKER_CONTROL_ENABLED", raising=False)

    settings = Settings(_env_file=None)

    assert settings.private_connectors_enabled is False
    assert settings.scraping_enabled is False
    assert settings.broker_control_enabled is False
    assert settings.broker_live_orders_enabled is False
