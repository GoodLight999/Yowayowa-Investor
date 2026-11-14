from typing import Any

from starlette.testclient import TestClient

from yowayowa.api.deps import get_private_acquisition_service
from yowayowa.config import get_settings

_DEFINITION: dict[str, Any] = {
    "id": "api-conn",
    "provider": "broker",
    "base_url": "https://broker.example/api/",
    "method": "private_http",
    "parser": "json",
}


class _StubResponse:
    status_code = 200
    url = "https://broker.example/api/account"
    content_type = "application/json"
    text = '{"cash": 500}'
    content = b'{"cash": 500}'
    elapsed_ms = 5.0


class _StubTransport:
    def __init__(self) -> None:
        self.calls = 0

    def fetch(
        self,
        method: str,
        resource: str,
        *,
        params: Any = None,
        headers: Any = None,
        data: Any = None,
    ) -> _StubResponse:
        self.calls += 1
        return _StubResponse()


def _personal_env(monkeypatch: Any, tmp_path: Any) -> _StubTransport:
    """Personal-mode settings plus a stub transport on the cached service."""
    monkeypatch.setenv("YOWAYOWA_MODE", "personal")
    monkeypatch.setenv("YOWAYOWA_PRIVATE_ACQUISITION_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("YOWAYOWA_API_TOKEN", raising=False)
    monkeypatch.delenv("YOWAYOWA_PRIVATE_CONNECTORS_ENABLED", raising=False)
    get_settings.cache_clear()
    get_private_acquisition_service.cache_clear()
    service = get_private_acquisition_service()
    stub = _StubTransport()
    service._transport_factory = lambda definition: stub  # type: ignore[attr-defined]
    return stub


def test_personal_mode_full_connector_lifecycle(monkeypatch: Any, tmp_path: Any) -> None:
    _personal_env(monkeypatch, tmp_path)
    from yowayowa.api.app import app

    with TestClient(app) as client:
        created = client.post("/v1/private/connectors", json=_DEFINITION)
        assert created.status_code == 201, created.text
        assert created.json()["definition"]["id"] == "api-conn"

        listed = client.get("/v1/private/connectors")
        assert listed.status_code == 200
        assert [runtime["definition"]["id"] for runtime in listed.json()["connectors"]] == [
            "api-conn"
        ]

        fetched = client.post("/v1/private/connectors/api-conn/fetch", json={"resource": "account"})
        assert fetched.status_code == 200, fetched.text
        body = fetched.json()
        assert body["fetch_state"] == "ok"
        assert body["auth_state"] == "authenticated"
        assert body["payload"] == {"cash": 500}
        assert body["snapshot"] is not None
        assert body["source_url"] == "https://broker.example/api/account"

        snapshots = client.get(
            "/v1/private/connectors/api-conn/snapshots",
            params={"resource": "account", "limit": 5},
        )
        assert snapshots.status_code == 200
        assert len(snapshots.json()["snapshots"]) == 1

        diff = client.get("/v1/private/connectors/api-conn/diff", params={"resource": "account"})
        assert diff.status_code == 200
        assert diff.json()["diff"] is None  # single snapshot, nothing to diff

        auth = client.post(
            "/v1/private/connectors/api-conn/auth-check", json={"resource": "session"}
        )
        assert auth.status_code == 200
        assert auth.json()["fetch_state"] == "ok"

        single = client.get("/v1/private/connectors/api-conn")
        assert single.status_code == 200
        assert single.json()["definition"]["id"] == "api-conn"


def test_unknown_connector_returns_404(monkeypatch: Any, tmp_path: Any) -> None:
    _personal_env(monkeypatch, tmp_path)
    from yowayowa.api.app import app

    with TestClient(app) as client:
        assert client.get("/v1/private/connectors/ghost").status_code == 404
        fetch = client.post("/v1/private/connectors/ghost/fetch", json={"resource": "x"})
        assert fetch.status_code == 404
        assert (
            client.post(
                "/v1/private/connectors/ghost/auth-check", json={"resource": "x"}
            ).status_code
            == 404
        )
        assert (
            client.get(
                "/v1/private/connectors/ghost/snapshots", params={"resource": "x"}
            ).status_code
            == 404
        )


def test_invalid_connector_id_returns_422(monkeypatch: Any, tmp_path: Any) -> None:
    _personal_env(monkeypatch, tmp_path)
    from yowayowa.api.app import app

    with TestClient(app) as client:
        bad = dict(_DEFINITION, id="Bad_ID!")
        response = client.post("/v1/private/connectors", json=bad)
        assert response.status_code == 422


def test_fetch_validation_rejects_blank_resource(monkeypatch: Any, tmp_path: Any) -> None:
    _personal_env(monkeypatch, tmp_path)
    from yowayowa.api.app import app

    with TestClient(app) as client:
        client.post("/v1/private/connectors", json=_DEFINITION)
        response = client.post("/v1/private/connectors/api-conn/fetch", json={"resource": ""})
        assert response.status_code == 422


def test_public_mode_fails_closed_403(monkeypatch: Any, tmp_path: Any) -> None:
    monkeypatch.setenv("YOWAYOWA_MODE", "public")
    monkeypatch.setenv("YOWAYOWA_API_TOKEN", "token")
    monkeypatch.setenv("YOWAYOWA_PRIVATE_ACQUISITION_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    get_private_acquisition_service.cache_clear()
    from yowayowa.api.app import app

    with TestClient(app) as client:
        headers = {"Authorization": "Bearer token"}
        checks: list[tuple[str, str, dict[str, Any] | None]] = [
            ("get", "/v1/private/connectors", None),
            ("post", "/v1/private/connectors", _DEFINITION),
            ("get", "/v1/private/connectors/api-conn", None),
            ("post", "/v1/private/connectors/api-conn/fetch", {"resource": "x"}),
            ("post", "/v1/private/connectors/api-conn/auth-check", {"resource": "x"}),
        ]
        for method, path, body in checks:
            if method == "get":
                response = client.get(path, headers=headers)
            else:
                response = client.post(path, json=body, headers=headers)
            assert response.status_code == 403, f"{method} {path} -> {response.status_code}"
        assert (
            client.get(
                "/v1/private/connectors/api-conn/snapshots",
                params={"resource": "a"},
                headers=headers,
            ).status_code
            == 403
        )
        assert (
            client.get(
                "/v1/private/connectors/api-conn/diff",
                params={"resource": "a"},
                headers=headers,
            ).status_code
            == 403
        )


def test_private_connectors_disabled_returns_403(monkeypatch: Any, tmp_path: Any) -> None:
    monkeypatch.setenv("YOWAYOWA_MODE", "personal")
    monkeypatch.setenv("YOWAYOWA_PRIVATE_CONNECTORS_ENABLED", "false")
    monkeypatch.setenv("YOWAYOWA_PRIVATE_ACQUISITION_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("YOWAYOWA_API_TOKEN", raising=False)
    get_settings.cache_clear()
    from yowayowa.api.app import app

    with TestClient(app) as client:
        response = client.get("/v1/private/connectors")
        assert response.status_code == 403
        assert response.json() == {"detail": "Private acquisition is disabled"}


def test_openapi_operation_ids_present(monkeypatch: Any, tmp_path: Any) -> None:
    _personal_env(monkeypatch, tmp_path)
    from yowayowa.api.app import app

    schema = app.openapi()
    private_ops = {
        operation.get("operationId")
        for path_item in schema["paths"].values()
        for operation in path_item.values()
        if isinstance(operation, dict)
        and str(operation.get("operationId", "")).startswith("private_")
    }
    assert {
        "private_list_connectors",
        "private_register_connector",
        "private_get_connector",
        "private_fetch_connector",
        "private_auth_check_connector",
        "private_list_snapshots",
        "private_get_diff",
    } <= private_ops
