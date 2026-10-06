"""P1D private-source surfaces: API routes, CLI parity, fail-closed gating.

Follows the existing P1A/P1B/P1C surface-test conventions: the shared
module-level app, the private-connectors env switch, and dependency overrides
in place of any real mailbox access.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from starlette.testclient import TestClient
from typer.testing import CliRunner

from yowayowa.acquisition.mailbox import MailboxMessage
from yowayowa.api.deps import get_private_source_service
from yowayowa.cli_entry import app as cli_app
from yowayowa.config import get_settings
from yowayowa.services.private_source_service import (
    PrivateMailboxSource,
    PrivateSourceService,
)

runner = CliRunner()

ACCOUNT = "operator@example.invalid"
QUERY = "from:alerts@example.invalid subject:earnings"

BODY = "\r\n".join(
    [
        "\u25a0\u6c7a\u7b97\u30ab\u30ec\u30f3\u30c0\u30fc\uff082026/09/23\uff09\u66f4\u65b0\u9298\u67c4",
        "\u30c6\u30b9\u30c8\u30c6\u30c3\u30af(SMPL):2026/10/21",
        "\u30c6\u30b9\u30c8\u30c7\u30fc\u30bf(ALFA):2026/10/21",
    ]
)

SOURCE: dict[str, Any] = {
    "source_id": "sample-alerts",
    "provider": "sample-broker-alerts",
    "account": ACCOUNT,
    "kind": "earnings_calendar",
    "query": QUERY,
}

FIXED_NOW = datetime(2026, 9, 23, 1, 2, 3, tzinfo=UTC)


class _FakeReader:
    def __init__(self) -> None:
        self.calls = 0

    def search(self, query: str, *, max_results: int) -> list[MailboxMessage]:
        self.calls += 1
        assert query == QUERY
        assert max_results > 0
        return [
            MailboxMessage(
                message_id="msg-1",
                thread_id="thr-1",
                sent_at=datetime(2026, 9, 23, 7, 0, tzinfo=UTC),
                sender="alerts@example.invalid",
                subject="\u6c7a\u7b97\u30ab\u30ec\u30f3\u30c0\u30fc",
                body=BODY,
                labels=["INBOX"],
            )
        ]


def _service(tmp_path: Path) -> PrivateSourceService:
    return PrivateSourceService(
        data_dir=tmp_path,
        reader_factory=lambda _: _FakeReader(),
        sources=[PrivateMailboxSource.model_validate(SOURCE)],
        now=lambda: FIXED_NOW,
    )


def _personal_env(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("YOWAYOWA_MODE", "personal")
    monkeypatch.setenv("YOWAYOWA_PRIVATE_CONNECTORS_ENABLED", "true")
    monkeypatch.setenv("YOWAYOWA_PRIVATE_ACQUISITION_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("YOWAYOWA_API_TOKEN", raising=False)
    get_settings.cache_clear()
    get_private_source_service.cache_clear()


# -------------------------------------------------------------------- CLI


def test_cli_private_sources_help_exits_zero() -> None:
    result = runner.invoke(cli_app, ["private-sources", "--help"])
    assert result.exit_code == 0, result.output
    assert result.exception is None


def test_cli_every_subcommand_help_exits_zero() -> None:
    for command in ["sources", "add-source", "fetch", "events"]:
        result = runner.invoke(cli_app, ["private-sources", command, "--help"])
        assert result.exit_code == 0, result.output
        assert result.exception is None


# -------------------------------------------------------------------- API


def test_api_lists_and_registers_sources(monkeypatch: Any, tmp_path: Path) -> None:
    _personal_env(monkeypatch, tmp_path)
    from yowayowa.api.app import app

    app.dependency_overrides[get_private_source_service] = lambda: _service(tmp_path)
    try:
        with TestClient(app) as client:
            response = client.get("/v1/private-sources/sources")
            assert response.status_code == 200, response.text
            assert [item["source_id"] for item in response.json()["sources"]] == ["sample-alerts"]

            created = client.post(
                "/v1/private-sources/sources",
                json={**SOURCE, "source_id": "second-alerts"},
            )
            assert created.status_code == 201, created.text
            assert created.json()["source_id"] == "second-alerts"
            assert created.json()["license_class"] == "personal_only"
            assert len(client.get("/v1/private-sources/sources").json()["sources"]) == 2
    finally:
        app.dependency_overrides.pop(get_private_source_service, None)


def test_api_get_source_and_unknown_is_404(monkeypatch: Any, tmp_path: Path) -> None:
    _personal_env(monkeypatch, tmp_path)
    from yowayowa.api.app import app

    app.dependency_overrides[get_private_source_service] = lambda: _service(tmp_path)
    try:
        with TestClient(app) as client:
            found = client.get("/v1/private-sources/sources/sample-alerts")
            assert found.status_code == 200, found.text
            assert found.json()["provider"] == "sample-broker-alerts"

            assert client.get("/v1/private-sources/sources/ghost").status_code == 404
            assert (
                client.post("/v1/private-sources/sources/ghost/fetch", json={}).status_code == 404
            )
    finally:
        app.dependency_overrides.pop(get_private_source_service, None)


def test_api_fetch_returns_events_and_records_the_timeline(
    monkeypatch: Any, tmp_path: Path
) -> None:
    _personal_env(monkeypatch, tmp_path)
    from yowayowa.api.app import app

    app.dependency_overrides[get_private_source_service] = lambda: _service(tmp_path)
    try:
        with TestClient(app) as client:
            response = client.post(
                "/v1/private-sources/sources/sample-alerts/fetch", json={"force_refresh": True}
            )
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["fetch_state"] == "ok"
            assert body["auth_state"] == "authenticated"
            assert body["new_events"] == 2
            assert [event["symbol"] for event in body["events"]] == ["SMPL", "ALFA"]
            assert body["source_url"].startswith("mailbox://")

            events = client.get("/v1/private-sources/events")
            assert events.status_code == 200, events.text
            payload = events.json()
            assert payload["count"] == 2
            assert {item["symbol"] for item in payload["events"]} == {"SMPL", "ALFA"}
            assert all(item["license_class"] == "personal_only" for item in payload["events"])
    finally:
        app.dependency_overrides.pop(get_private_source_service, None)


def test_api_fetch_body_is_optional(monkeypatch: Any, tmp_path: Path) -> None:
    _personal_env(monkeypatch, tmp_path)
    from yowayowa.api.app import app

    app.dependency_overrides[get_private_source_service] = lambda: _service(tmp_path)
    try:
        with TestClient(app) as client:
            response = client.post("/v1/private-sources/sources/sample-alerts/fetch")
            assert response.status_code == 200, response.text
            assert response.json()["fetch_state"] == "ok"
    finally:
        app.dependency_overrides.pop(get_private_source_service, None)


def test_api_events_filters_by_source_id(monkeypatch: Any, tmp_path: Path) -> None:
    _personal_env(monkeypatch, tmp_path)
    from yowayowa.api.app import app

    app.dependency_overrides[get_private_source_service] = lambda: _service(tmp_path)
    try:
        with TestClient(app) as client:
            client.post("/v1/private-sources/sources/sample-alerts/fetch", json={})
            matched = client.get(
                "/v1/private-sources/events", params={"source_id": "sample-alerts"}
            )
            assert matched.status_code == 200
            assert matched.json()["count"] == 2
            other = client.get("/v1/private-sources/events", params={"source_id": "other"})
            assert other.status_code == 200
            assert other.json() == {"events": [], "count": 0}
    finally:
        app.dependency_overrides.pop(get_private_source_service, None)


def test_api_register_rejects_a_broader_license_class(monkeypatch: Any, tmp_path: Path) -> None:
    _personal_env(monkeypatch, tmp_path)
    from yowayowa.api.app import app

    app.dependency_overrides[get_private_source_service] = lambda: _service(tmp_path)
    try:
        with TestClient(app) as client:
            response = client.post(
                "/v1/private-sources/sources",
                json={**SOURCE, "license_class": "official_public"},
            )
            assert response.status_code == 422, response.text
    finally:
        app.dependency_overrides.pop(get_private_source_service, None)


def test_api_register_rejects_an_invalid_source_id(monkeypatch: Any, tmp_path: Path) -> None:
    _personal_env(monkeypatch, tmp_path)
    from yowayowa.api.app import app

    app.dependency_overrides[get_private_source_service] = lambda: _service(tmp_path)
    try:
        with TestClient(app) as client:
            response = client.post(
                "/v1/private-sources/sources", json={**SOURCE, "source_id": "Bad_ID!"}
            )
            assert response.status_code == 422, response.text
    finally:
        app.dependency_overrides.pop(get_private_source_service, None)


def test_api_public_mode_fails_closed_403(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("YOWAYOWA_MODE", "public")
    monkeypatch.setenv("YOWAYOWA_API_TOKEN", "token")
    monkeypatch.setenv("YOWAYOWA_PRIVATE_CONNECTORS_ENABLED", "false")
    monkeypatch.setenv("YOWAYOWA_PRIVATE_ACQUISITION_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    get_private_source_service.cache_clear()
    from yowayowa.api.app import app

    headers = {"Authorization": "Bearer token"}
    with TestClient(app) as client:
        checks: list[tuple[str, str, dict[str, Any] | None]] = [
            ("get", "/v1/private-sources/sources", None),
            ("post", "/v1/private-sources/sources", SOURCE),
            ("get", "/v1/private-sources/sources/sample-alerts", None),
            ("post", "/v1/private-sources/sources/sample-alerts/fetch", {}),
            ("get", "/v1/private-sources/events", None),
        ]
        for method, path, body in checks:
            if method == "get":
                response = client.get(path, headers=headers)
            else:
                response = client.post(path, json=body, headers=headers)
            assert response.status_code == 403, f"{method} {path} -> {response.status_code}"


def test_api_private_connectors_disabled_returns_403(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("YOWAYOWA_MODE", "personal")
    monkeypatch.setenv("YOWAYOWA_PRIVATE_CONNECTORS_ENABLED", "false")
    monkeypatch.setenv("YOWAYOWA_PRIVATE_ACQUISITION_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("YOWAYOWA_API_TOKEN", raising=False)
    get_settings.cache_clear()
    get_private_source_service.cache_clear()
    from yowayowa.api.app import app

    with TestClient(app) as client:
        response = client.get("/v1/private-sources/sources")
        assert response.status_code == 403
        assert response.json() == {"detail": "Private acquisition is disabled"}


# ---------------------------------------------------------------- OpenAPI


def test_openapi_operation_ids_are_present_and_unique() -> None:
    from yowayowa.api.app import app

    schema = app.openapi()
    operation_ids: list[str] = [
        operation["operationId"]
        for path_item in schema["paths"].values()
        for operation in path_item.values()
        if isinstance(operation, dict) and "operationId" in operation
    ]
    assert len(operation_ids) == len(set(operation_ids))
    assert {
        "private_source_list_sources",
        "private_source_register_source",
        "private_source_get_source",
        "private_source_fetch",
        "private_source_events",
    } <= set(operation_ids)


def test_openapi_exposes_the_private_sources_prefix() -> None:
    from yowayowa.api.app import app

    schema = app.openapi()
    paths = set(schema["paths"])
    for expected in (
        "/v1/private-sources/sources",
        "/v1/private-sources/sources/{source_id}",
        "/v1/private-sources/sources/{source_id}/fetch",
        "/v1/private-sources/events",
    ):
        assert expected in paths


# ------------------------------------------------------------------ CLI IO


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> _FakeResponse:
        return self

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.requests: list[dict[str, Any]] = []

    def post(self, path: str, json: dict[str, Any] | None = None) -> _FakeResponse:
        self.requests.append({"path": path, "json": json})
        return _FakeResponse(self._payload)

    def get(self, path: str, params: dict[str, Any] | None = None) -> _FakeResponse:
        self.requests.append({"path": path, "params": params})
        return _FakeResponse(self._payload)

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *_: object) -> None:
        return None


def test_cli_fetch_renders_outcome_and_posts_force_refresh(monkeypatch: Any) -> None:
    payload = {
        "source_id": "sample-alerts",
        "provider": "sample-broker-alerts",
        "fetch_state": "ok",
        "auth_state": "authenticated",
        "source_url": "mailbox://operator@example.invalid?q=subject%3Aearnings",
        "retrieved_at": FIXED_NOW.isoformat(),
        "messages_scanned": 1,
        "messages_new": 1,
        "events": [
            {
                "kind": "earnings_announcement",
                "symbol": "SMPL",
                "market": "us",
                "announcement_date": "2026-10-21",
                "name": "sample",
                "market_provider": "sample-broker-alerts",
                "source_message_id": "msg-1",
                "source_subject": "earnings",
                "sent_at": FIXED_NOW.isoformat(),
            }
        ],
        "new_events": 1,
        "timeline_entries": 1,
        "network": [],
        "notes": [],
    }
    fake = _FakeClient(payload)
    monkeypatch.setattr("yowayowa.private_source_cli._client", lambda base_url, token: fake)

    result = runner.invoke(
        cli_app,
        ["private-sources", "fetch", "sample-alerts", "--force-refresh"],
    )

    assert result.exit_code == 0, result.output
    assert "sample-alerts" in result.output
    assert "SMPL" in result.output
    assert fake.requests[0]["path"] == "/v1/private-sources/sources/sample-alerts/fetch"
    assert fake.requests[0]["json"] == {"force_refresh": True}


def test_cli_events_passes_source_id_and_limit(monkeypatch: Any) -> None:
    payload = {
        "events": [
            {
                "symbol": "SMPL",
                "market": "us",
                "announcement_date": "2026-10-21",
                "name": "sample",
                "market_provider": "sample-broker-alerts",
                "license_class": "personal_only",
            }
        ],
        "count": 1,
    }
    fake = _FakeClient(payload)
    monkeypatch.setattr("yowayowa.private_source_cli._client", lambda base_url, token: fake)

    result = runner.invoke(
        cli_app,
        ["private-sources", "events", "--source-id", "sample-alerts", "--limit", "5"],
    )

    assert result.exit_code == 0, result.output
    assert "personal_only" in result.output
    assert "1 event(s)" in result.output
    assert fake.requests[0]["params"] == {"source_id": "sample-alerts", "limit": 5}


def test_cli_add_source_posts_the_source_body(monkeypatch: Any) -> None:
    fake = _FakeClient(SOURCE)
    monkeypatch.setattr("yowayowa.private_source_cli._client", lambda base_url, token: fake)

    result = runner.invoke(
        cli_app,
        [
            "private-sources",
            "add-source",
            "sample-alerts",
            "--provider",
            "sample-broker-alerts",
            "--account",
            ACCOUNT,
            "--query",
            QUERY,
        ],
    )

    assert result.exit_code == 0, result.output
    assert fake.requests[0]["path"] == "/v1/private-sources/sources"
    sent = fake.requests[0]["json"]
    assert sent == {
        "source_id": "sample-alerts",
        "provider": "sample-broker-alerts",
        "account": ACCOUNT,
        "kind": "earnings_calendar",
        "query": QUERY,
    }
    assert json.loads(result.output)["source_id"] == "sample-alerts"


def test_cli_sources_renders_a_table(monkeypatch: Any) -> None:
    fake = _FakeClient({"sources": [{**SOURCE, "license_class": "personal_only", "notes": []}]})
    monkeypatch.setattr("yowayowa.private_source_cli._client", lambda base_url, token: fake)

    # Non-tty output defaults to 80 columns, which truncates the table cells;
    # widen the virtual terminal so the rendered row is actually asserted.
    result = runner.invoke(cli_app, ["private-sources", "sources"], env={"COLUMNS": "220"})

    assert result.exit_code == 0, result.output
    assert fake.requests[0]["path"] == "/v1/private-sources/sources"
    assert "sample-alerts" in result.output
    assert "sample-broker-alerts" in result.output
    assert "personal_only" in result.output
    assert "earnings_calendar" in result.output
