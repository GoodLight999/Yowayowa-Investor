from __future__ import annotations

from fastapi.testclient import TestClient

from yowayowa.vercel_app import app
from yowayowa.vercel_observability import runtime_source_info


def test_vercel_entrypoint_installs_runtime_debug(monkeypatch) -> None:
    monkeypatch.setenv("YOWAYOWA_BUILD_SHA", "entrypoint-sha")
    runtime_source_info.cache_clear()

    with TestClient(app) as client:
        response = client.get("/internal/debug/runtime")

    assert response.status_code == 200
    assert response.headers["x-yowayowa-request-id"]
    assert response.json()["source"]["source_revision"] == "entrypoint-sha"
    runtime_source_info.cache_clear()
