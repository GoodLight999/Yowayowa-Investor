from __future__ import annotations

import json
from typing import Any

import pytest
from typer.testing import CliRunner

from yowayowa.broker_read_cli import app

runner = CliRunner()

_FETCH_PAYLOAD: dict[str, Any] = {
    "connector_id": "rakuten-web",
    "resource": "positions",
    "market": "jp",
    "fetch_state": "ok",
    "auth_state": "authenticated",
    "account": None,
    "positions": [
        {
            "broker": "rakuten-securities",
            "symbol": "7203",
            "quantity": "100",
            "average_cost": "2500",
            "market_price": "2650",
            "market_value": "265000",
            "unrealized_pnl": "-15000",
            "currency": "JPY",
            "account_type": "cash",
        }
    ],
    "orders": [
        {
            "broker": "rakuten-securities",
            "broker_order_id": "20260923-0001",
            "symbol": "7203",
            "side": "buy",
            "quantity": 100,
            "filled_quantity": 0,
            "average_fill_price": None,
            "status": "pending",
        }
    ],
    "detail": {
        "parser_version": "rakuten-json-v1",
        "schema_version": "rakuten-web-v1",
        "verified": False,
        "margin_state": {"margin_deposit": "300000"},
    },
    "source_url": "https://www.rakuten-sec.co.jp/web/positions/jp",
    "retrieved_at": "2026-09-23T12:00:00+00:00",
    "as_of": None,
    "parser_version": "json-v1",
    "schema_version": "json-v1",
    "snapshot": None,
    "diff": None,
    "cache": None,
    "network": [],
    "notes": ["cache-hit"],
}


@pytest.fixture()
def _api_stub(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Stub the HTTP client used by the CLI commands."""
    calls: list[dict[str, Any]] = []

    class _Response:
        def __init__(self, payload: dict[str, Any]) -> None:
            self._payload = payload
            self.status_code = 200
            self.text = json.dumps(payload)

        def raise_for_status(self) -> _Response:
            return self

        def json(self) -> dict[str, Any]:
            return self._payload

    class _Client:
        def __init__(self, base_url: str, headers: dict[str, str], timeout: int) -> None:
            self.base_url = base_url

        def __enter__(self) -> _Client:
            return self

        def __exit__(self, *_: object) -> None:
            return

        def get(self, path: str, params: dict[str, Any] | None = None) -> _Response:
            calls.append({"method": "GET", "path": path, "params": params})
            if path.endswith("/connectors"):
                return _Response(
                    {
                        "connectors": [
                            {
                                "definition": {
                                    "id": "rakuten-web",
                                    "provider": "rakuten-securities",
                                    "method": "authenticated_web_session",
                                    "parser": "json",
                                },
                                "last_fetch_state": "ok",
                                "last_success_at": "2026-09-23T12:00:00+00:00",
                            }
                        ]
                    }
                )
            if path.endswith("/snapshots"):
                return _Response(
                    {
                        "snapshots": [
                            {
                                "snapshot_id": "abc123",
                                "captured_at": "2026-09-23T12:00:00+00:00",
                                "fetch_state": "ok",
                                "parser_version": "json-v1",
                            }
                        ]
                    }
                )
            if path.endswith("/diff"):
                return _Response({"diff": None})
            raise AssertionError(f"unexpected GET {path}")

        def post(self, path: str, json: dict[str, Any] | None = None) -> _Response:
            calls.append({"method": "POST", "path": path, "json": json})
            if path.endswith("/auth-check"):
                return _Response(
                    {
                        "connector_id": "rakuten-web",
                        "resource": "web/account/summary",
                        "fetch_state": "ok",
                        "auth_state": "authenticated",
                        "payload": None,
                        "network": [],
                        "notes": ["authenticated"],
                    }
                )
            if path.endswith("/fetch"):
                return _Response(_FETCH_PAYLOAD)
            raise AssertionError(f"unexpected POST {path}")

    import yowayowa.broker_read_cli as cli_module

    monkeypatch.setattr(cli_module, "_client", lambda base_url, token: _Client(base_url, {}, 30))
    return {"calls": calls}


def test_broker_read_help_exits_zero() -> None:
    for args in (
        ["--help"],
        ["list", "--help"],
        ["auth-check", "--help"],
        ["fetch", "--help"],
        ["snapshots", "--help"],
        ["diff", "--help"],
    ):
        result = runner.invoke(app, args)
        assert result.exit_code == 0, result.output
        assert result.exception is None


def test_broker_read_list_renders_connectors(_api_stub: dict[str, Any]) -> None:
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0, result.output
    assert "rakuten-web" in result.output
    assert _api_stub["calls"][0]["path"] == "/v1/broker-read/connectors"


def test_broker_read_auth_check_renders_state(_api_stub: dict[str, Any]) -> None:
    result = runner.invoke(app, ["auth-check", "rakuten-web"])
    assert result.exit_code == 0, result.output
    assert "authenticated" in result.output


def test_broker_read_fetch_renders_summary(_api_stub: dict[str, Any]) -> None:
    result = runner.invoke(app, ["fetch", "rakuten-web", "positions", "--market", "jp"])
    assert result.exit_code == 0, result.output
    assert "positions/jp" in result.output or "positions" in result.output
    assert "7203" in result.output
    assert "100" in result.output
    assert "-15000" in result.output
    assert "margin" in result.output
    assert "cache-hit" in result.output
    call = _api_stub["calls"][0]
    assert call["json"] == {"resource": "positions", "market": "jp", "force_refresh": False}


def test_broker_read_fetch_json_flag_prints_full_payload(_api_stub: dict[str, Any]) -> None:
    result = runner.invoke(app, ["fetch", "rakuten-web", "positions", "--market", "jp", "--json"])
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert parsed["connector_id"] == "rakuten-web"
    assert parsed["positions"][0]["symbol"] == "7203"


def test_broker_read_snapshots_renders_table(_api_stub: dict[str, Any]) -> None:
    result = runner.invoke(
        app, ["snapshots", "rakuten-web", "positions", "--market", "jp", "--limit", "5"]
    )
    assert result.exit_code == 0, result.output
    assert "abc123" in result.output
    call = _api_stub["calls"][0]
    assert call["params"] == {"resource": "positions", "market": "jp", "limit": 5}


def test_broker_read_diff_renders_no_diff_message(_api_stub: dict[str, Any]) -> None:
    result = runner.invoke(app, ["diff", "rakuten-web", "positions", "--market", "jp"])
    assert result.exit_code == 0, result.output
    assert "No diff available" in result.output


def test_broker_read_cli_registered_on_entry_app() -> None:
    from yowayowa.cli_entry import app as entry_app

    result = runner.invoke(entry_app, ["broker-read", "--help"])
    assert result.exit_code == 0, result.output
    assert "broker-read" in result.output
