from __future__ import annotations

from fastapi.testclient import TestClient

from yowayowa.config import get_settings
from yowayowa.vercel_app import app
from yowayowa.vercel_observability import runtime_source_info


def test_vercel_entrypoint_installs_runtime_debug(monkeypatch) -> None:
    monkeypatch.setenv("YOWAYOWA_BUILD_SHA", "entrypoint-sha")
    monkeypatch.setenv("YOWAYOWA_API_TOKEN", "fixture-entrypoint-token")
    get_settings.cache_clear()
    runtime_source_info.cache_clear()

    with TestClient(app) as client:
        held = client.get(
            "/internal/debug/runtime",
            headers={"Authorization": "Bearer fixture-entrypoint-token"},
        )
        anonymous = client.get("/internal/debug/runtime")

    assert held.status_code == 200
    assert held.headers["x-yowayowa-request-id"]
    assert held.json()["source"]["source_revision"] == "entrypoint-sha"
    assert anonymous.status_code == 200
    assert anonymous.headers["x-yowayowa-request-id"]
    assert "source" not in anonymous.json()
    assert "vercel" not in anonymous.json()
    get_settings.cache_clear()
    runtime_source_info.cache_clear()
