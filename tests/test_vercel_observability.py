from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from yowayowa import vercel_observability
from yowayowa.vercel_observability import install_vercel_observability, runtime_source_info


def test_runtime_debug_exposes_safe_vercel_metadata(monkeypatch) -> None:
    monkeypatch.setenv("VERCEL_DEPLOYMENT_ID", "dpl_test")
    monkeypatch.setenv("VERCEL_ENV", "preview")
    monkeypatch.setenv("VERCEL_REGION", "iad1")
    monkeypatch.setenv("VERCEL_PROJECT_ID", "prj_test")
    monkeypatch.setenv("VERCEL_PROJECT_PRODUCTION_URL", "example.vercel.app")
    monkeypatch.setenv("YOWAYOWA_BUILD_SHA", "abc1234")
    monkeypatch.setenv("DATABASE_URL", "postgresql://secret:secret@example.invalid/db")
    runtime_source_info.cache_clear()

    app = FastAPI()
    install_vercel_observability(app)

    with TestClient(app) as client:
        response = client.get("/internal/debug/runtime")

    assert response.status_code == 200
    assert response.headers["x-yowayowa-request-id"]
    body = response.json()
    assert body["source"]["source_revision"] == "abc1234"
    assert body["vercel"] == {
        "deployment_id": "dpl_test",
        "environment": "preview",
        "region": "iad1",
        "project_id": "prj_test",
        "production_url": "example.vercel.app",
    }
    assert "secret" not in response.text
    runtime_source_info.cache_clear()


def test_asset_requests_get_edge_cache_header_without_request_log(monkeypatch) -> None:
    emitted: list[dict[str, object]] = []

    def capture(payload: dict[str, object], *, error: bool = False) -> None:
        assert error is False
        emitted.append(payload)

    monkeypatch.setattr(vercel_observability, "_emit", capture)
    app = FastAPI()

    @app.get("/assets/static/abc/test.css")
    def asset() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/health")
    def health() -> dict[str, bool]:
        return {"ok": True}

    install_vercel_observability(app)

    with TestClient(app) as client:
        asset_response = client.get("/assets/static/abc/test.css")
        health_response = client.get("/health")

    assert asset_response.status_code == 200
    assert asset_response.headers["vercel-cdn-cache-control"] == (
        "public, max-age=31536000, immutable"
    )
    assert asset_response.headers["x-yowayowa-request-id"]
    assert health_response.status_code == 200
    assert [payload["path"] for payload in emitted] == ["/health"]


def test_install_is_idempotent() -> None:
    app = FastAPI()
    install_vercel_observability(app)
    route_count = len(app.routes)

    install_vercel_observability(app)

    assert len(app.routes) == route_count
