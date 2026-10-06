from __future__ import annotations

import json
from typing import Any

from typer.testing import CliRunner

from yowayowa.cli_entry import app

runner = CliRunner()


def test_private_help_exits_zero() -> None:
    result = runner.invoke(app, ["private", "--help"])
    assert result.exit_code == 0, result.output
    assert result.exception is None


def test_private_subcommand_help_exits_zero() -> None:
    for command in ["list", "register", "fetch", "auth-check", "snapshots", "diff"]:
        result = runner.invoke(app, ["private", command, "--help"])
        assert result.exit_code == 0, result.output
        assert result.exception is None


def _outcome() -> dict[str, Any]:
    return {
        "connector_id": "stub-conn",
        "resource": "account",
        "fetch_state": "ok",
        "auth_state": "authenticated",
        "payload": {"cash": 500},
        "source_url": "https://broker.example/api/account",
        "retrieved_at": "2026-09-23T00:00:00Z",
        "as_of": None,
        "parser_version": "json-v1",
        "schema_version": "json-v1",
        "network": [],
        "downloads": [],
        "snapshot": {
            "snapshot_id": "abc123",
            "connector_id": "stub-conn",
            "resource": "account",
            "captured_at": "2026-09-23T00:00:00Z",
            "parser_version": "json-v1",
            "schema_version": "json-v1",
            "fetch_state": "ok",
            "payload_sha256": "abc123",
            "as_of": None,
        },
        "diff": {"changed": False, "changes": []},
        "cache": None,
        "notes": [],
    }


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


def test_private_fetch_renders_outcome(monkeypatch: Any) -> None:
    fake = _FakeClient(_outcome())
    monkeypatch.setattr("yowayowa.private_cli._client", lambda base_url, token: fake)

    result = runner.invoke(
        app,
        [
            "private",
            "fetch",
            "stub-conn",
            "account",
            "--param",
            "market=jp",
            "--force-refresh",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "stub-conn/account" in result.output
    assert "ok" in result.output
    assert "authenticated" in result.output
    assert "https://broker.example/api/account" in result.output
    assert "abc123" in result.output
    assert "500" in result.output  # payload rendered
    sent = fake.requests[0]
    assert sent["path"] == "/v1/private/connectors/stub-conn/fetch"
    assert sent["json"] == {
        "resource": "account",
        "params": {"market": "jp"},
        "force_refresh": True,
    }


def test_private_fetch_rejects_malformed_param(monkeypatch: Any) -> None:
    fake = _FakeClient(_outcome())
    monkeypatch.setattr("yowayowa.private_cli._client", lambda base_url, token: fake)
    result = runner.invoke(app, ["private", "fetch", "stub-conn", "account", "--param", "noeq"])
    assert result.exit_code != 0
    assert fake.requests == []


def test_private_snapshots_renders_table(monkeypatch: Any) -> None:
    outcome = _outcome()
    payload = {"snapshots": [outcome["snapshot"]]}
    fake = _FakeClient(payload)
    monkeypatch.setattr("yowayowa.private_cli._client", lambda base_url, token: fake)

    result = runner.invoke(
        app, ["private", "snapshots", "stub-conn", "--resource", "account", "--limit", "5"]
    )

    assert result.exit_code == 0, result.output
    assert "abc123" in result.output
    assert "json-v1" in result.output
    assert fake.requests[0]["params"] == {"resource": "account", "limit": 5}


def test_private_diff_renders_changes(monkeypatch: Any) -> None:
    outcome = _outcome()
    outcome["diff"] = {
        "connector_id": "stub-conn",
        "resource": "account",
        "previous_snapshot_id": "old",
        "current_snapshot_id": "new",
        "previous_captured_at": "2026-09-22T00:00:00Z",
        "current_captured_at": "2026-09-23T00:00:00Z",
        "changed": True,
        "changes": [
            {"path": "$.cash", "previous": "500", "current": "600"},
        ],
    }
    fake = _FakeClient({"diff": outcome["diff"]})
    monkeypatch.setattr("yowayowa.private_cli._client", lambda base_url, token: fake)

    result = runner.invoke(app, ["private", "diff", "stub-conn", "--resource", "account"])

    assert result.exit_code == 0, result.output
    assert "changed: yes" in result.output
    assert "$.cash" in result.output


def test_private_diff_no_result(monkeypatch: Any) -> None:
    fake = _FakeClient({"diff": None})
    monkeypatch.setattr("yowayowa.private_cli._client", lambda base_url, token: fake)
    result = runner.invoke(app, ["private", "diff", "stub-conn", "--resource", "account"])
    assert result.exit_code == 0, result.output
    assert "No diff" in result.output


def test_private_list_renders_table(monkeypatch: Any) -> None:
    runtime = {
        "definition": {
            "id": "stub-conn",
            "provider": "broker",
            "base_url": "https://broker.example/api/",
            "method": "private_http",
            "parser": "json",
            "freshness": {"ttl_seconds": 900, "max_stale_seconds": 86400},
            "auth_recheck_resource": None,
            "notes": [],
        },
        "last_success_at": None,
        "last_fetch_state": "ok",
        "last_auth_state": "authenticated",
    }
    fake = _FakeClient({"connectors": [runtime]})
    monkeypatch.setattr("yowayowa.private_cli._client", lambda base_url, token: fake)

    result = runner.invoke(app, ["private", "list"])
    assert result.exit_code == 0, result.output
    assert "stub-conn" in result.output
    assert "broker" in result.output


def test_private_register_posts_definition(monkeypatch: Any) -> None:
    outcome = _outcome()
    runtime = json.loads(json.dumps(outcome))  # unused but keeps shape stable
    fake = _FakeClient({"definition": {"id": "new-conn"}, "last_fetch_state": None})
    monkeypatch.setattr("yowayowa.private_cli._client", lambda base_url, token: fake)

    result = runner.invoke(
        app,
        [
            "private",
            "register",
            "new-conn",
            "--provider",
            "broker",
            "--base-url",
            "https://broker.example/",
            "--method",
            "private_http",
            "--parser",
            "json",
        ],
    )
    assert result.exit_code == 0, result.output
    sent = fake.requests[0]
    assert sent["path"] == "/v1/private/connectors"
    assert sent["json"]["id"] == "new-conn"
    del runtime


def test_private_auth_check_renders(monkeypatch: Any) -> None:
    outcome = _outcome()
    outcome["fetch_state"] = "ok"
    outcome["notes"] = ["authenticated"]
    fake = _FakeClient(outcome)
    monkeypatch.setattr("yowayowa.private_cli._client", lambda base_url, token: fake)

    result = runner.invoke(app, ["private", "auth-check", "stub-conn", "--resource", "session"])
    assert result.exit_code == 0, result.output
    assert "authenticated" in result.output
    sent = fake.requests[0]
    assert sent["path"] == "/v1/private/connectors/stub-conn/auth-check"
    assert sent["json"] == {"resource": "session"}
