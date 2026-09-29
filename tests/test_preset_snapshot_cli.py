from __future__ import annotations

from typing import Any

from typer.testing import CliRunner

from yowayowa import preset_cli
from yowayowa.cli_entry import app


class _Response:
    def raise_for_status(self) -> _Response:
        return self

    def json(self) -> list[dict[str, str]]:
        return [
            {"id": "kiyohara_global_value_growth"},
            {"id": "unsupported_strategy"},
        ]


class _Client:
    def __enter__(self) -> _Client:
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def get(self, path: str) -> _Response:
        assert path == "/v1/strategy-presets"
        return _Response()


def test_snapshot_builtins_runs_only_implemented_presets(monkeypatch: Any) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(preset_cli, "_client", lambda *_args: _Client())
    monkeypatch.setattr(preset_cli, "run_builtin", lambda **kwargs: calls.append(kwargs))

    result = CliRunner().invoke(app, ["preset", "snapshot-builtins", "--region", "jp"])

    assert result.exit_code == 0, result.output
    assert calls == [
        {
            "strategy_id": "kiyohara_global_value_growth",
            "region": "jp",
            "size": 25,
            "edinet_key": None,
            "base_url": "http://127.0.0.1:8000",
            "token": None,
        }
    ]


def test_snapshot_builtins_fails_closed_when_no_implemented_preset(monkeypatch: Any) -> None:
    class EmptyClient(_Client):
        def get(self, path: str) -> _Response:
            assert path == "/v1/strategy-presets"
            response = _Response()
            response.json = lambda: [{"id": "unsupported_strategy"}]  # type: ignore[method-assign]
            return response

    monkeypatch.setattr(preset_cli, "_client", lambda *_args: EmptyClient())
    result = CliRunner().invoke(app, ["preset", "snapshot-builtins"])

    assert result.exit_code != 0
    assert "No implemented built-in strategy" in result.output


def test_snapshot_builtins_help_is_available() -> None:
    result = CliRunner().invoke(app, ["preset", "snapshot-builtins", "--help"])
    assert result.exit_code == 0, result.output
