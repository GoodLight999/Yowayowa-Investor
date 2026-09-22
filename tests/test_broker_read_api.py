from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from starlette.testclient import TestClient

from yowayowa.api.deps import get_broker_read_service, get_private_acquisition_service
from yowayowa.config import get_settings

_POSITIONS_BODY = json.dumps(
    {
        "positions": [
            {
                "symbol": "7203",
                "name": "トヨタ自動車",
                "quantity": "100株",
                "average_cost": "2,500円",
                "market_price": "2,650円",
                "market_value": "265,000円",
                "unrealized_pnl": "▲15,000円",
            }
        ]
    },
    ensure_ascii=False,
).encode("utf-8")


class _StubResponse:
    status_code = 200
    url = "https://trade.rakuten-sec.co.jp/web/positions/jp"
    content_type = "application/json"
    text = _POSITIONS_BODY.decode("utf-8")
    content = _POSITIONS_BODY
    elapsed_ms = 5.0


class _StubTransport:
    def __init__(self) -> None:
        self.calls = 0

    def fetch(
        self,
        method: str,
        resource: str,
        *,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        data: object | None = None,
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
    get_broker_read_service.cache_clear()
    service = get_broker_read_service()
    stub = _StubTransport()
    object.__setattr__(service._acquisition, "_transport_factory", lambda definition: stub)
    return stub


def test_broker_read_list_connectors_200(monkeypatch: Any, tmp_path: Any) -> None:
    _personal_env(monkeypatch, tmp_path)
    from yowayowa.api.app import app

    with TestClient(app) as client:
        response = client.get("/v1/broker-read/connectors")
        assert response.status_code == 200, response.text
        body = response.json()
        assert [runtime["definition"]["id"] for runtime in body["connectors"]] == ["rakuten-web"]
        definition = body["connectors"][0]["definition"]
        assert definition["method"] == "authenticated_web_session"
        assert definition["provider"] == "rakuten-securities"


def test_broker_read_fetch_positions_200_normalized(monkeypatch: Any, tmp_path: Any) -> None:
    _personal_env(monkeypatch, tmp_path)
    from yowayowa.api.app import app

    with TestClient(app) as client:
        response = client.post(
            "/v1/broker-read/connectors/rakuten-web/fetch",
            json={"resource": "positions", "market": "jp"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["fetch_state"] == "ok"
        assert body["auth_state"] == "authenticated"
        assert body["connector_id"] == "rakuten-web"
        assert body["resource"] == "positions"
        assert body["market"] == "jp"
        positions = body["positions"]
        assert len(positions) == 1
        assert positions[0]["symbol"] == "7203"
        assert positions[0]["quantity"] == "100"
        assert positions[0]["unrealized_pnl"] == "-15000"
        assert positions[0]["currency"] == "JPY"
        assert body["snapshot"] is not None
        assert body["detail"] is not None
        assert body["detail"]["verified"] is False
        assert body["detail"]["symbol_names"] == {"7203": "トヨタ自動車"}


def test_broker_read_fetch_unknown_resource_failed_outcome(monkeypatch: Any, tmp_path: Any) -> None:
    _personal_env(monkeypatch, tmp_path)
    from yowayowa.api.app import app

    with TestClient(app) as client:
        response = client.post(
            "/v1/broker-read/connectors/rakuten-web/fetch",
            json={"resource": "bogus", "market": "jp"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["fetch_state"] == "failed"
        assert "unknown resource/market" in body["notes"][0]
        assert body["positions"] == []


def test_broker_read_unknown_connector_404(monkeypatch: Any, tmp_path: Any) -> None:
    _personal_env(monkeypatch, tmp_path)
    from yowayowa.api.app import app

    with TestClient(app) as client:
        assert client.get("/v1/broker-read/connectors/ghost").status_code == 404
        assert (
            client.post(
                "/v1/broker-read/connectors/ghost/fetch",
                json={
                    "resource": "positions",
                    "market": "jp",
                },
            ).status_code
            == 404
        )
        assert client.post("/v1/broker-read/connectors/ghost/auth-check").status_code == 404
        assert (
            client.get(
                "/v1/broker-read/connectors/ghost/snapshots",
                params={"resource": "positions", "market": "jp"},
            ).status_code
            == 404
        )
        assert (
            client.get(
                "/v1/broker-read/connectors/ghost/diff",
                params={"resource": "positions", "market": "jp"},
            ).status_code
            == 404
        )


def test_broker_read_public_mode_fails_closed_403(monkeypatch: Any, tmp_path: Any) -> None:
    monkeypatch.setenv("YOWAYOWA_MODE", "public")
    monkeypatch.setenv("YOWAYOWA_API_TOKEN", "token")
    monkeypatch.setenv("YOWAYOWA_PRIVATE_ACQUISITION_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    get_broker_read_service.cache_clear()
    from yowayowa.api.app import app

    with TestClient(app) as client:
        headers = {"Authorization": "Bearer token"}
        assert client.get("/v1/broker-read/connectors", headers=headers).status_code == 403
        assert (
            client.post(
                "/v1/broker-read/connectors/rakuten-web/fetch",
                json={"resource": "positions", "market": "jp"},
                headers=headers,
            ).status_code
            == 403
        )


def test_broker_read_invalid_market_422(monkeypatch: Any, tmp_path: Any) -> None:
    _personal_env(monkeypatch, tmp_path)
    from yowayowa.api.app import app

    with TestClient(app) as client:
        response = client.post(
            "/v1/broker-read/connectors/rakuten-web/fetch",
            json={"resource": "positions", "market": "eu"},
        )
        assert response.status_code == 422


def test_broker_read_openapi_operation_ids_present(monkeypatch: Any, tmp_path: Any) -> None:
    _personal_env(monkeypatch, tmp_path)
    from yowayowa.api.app import app

    schema = app.openapi()
    broker_read_ops = {
        operation.get("operationId")
        for path_item in schema["paths"].values()
        for operation in path_item.values()
        if isinstance(operation, dict)
        and str(operation.get("operationId", "")).startswith("broker_read_")
    }
    assert {
        "broker_read_list_connectors",
        "broker_read_get_connector",
        "broker_read_auth_check",
        "broker_read_fetch_resource",
        "broker_read_list_snapshots",
        "broker_read_get_diff",
    } <= broker_read_ops
