from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from typer.testing import CliRunner

from yowayowa import preset_cli
from yowayowa.cli_entry import app
from yowayowa.services.strategy_presets import KIYOHARA_GLOBAL_ID


@pytest.fixture
def frozen_time() -> datetime:
    return datetime.now(UTC)


class _Response:
    def __init__(self, payload: Any) -> None:
        self._payload = payload

    def raise_for_status(self) -> _Response:
        return self

    def json(self) -> Any:
        return self._payload


class _Client:
    def __init__(self, strategies: list[dict[str, str]]) -> None:
        self._strategies = strategies

    def __enter__(self) -> _Client:
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def get(self, path: str) -> _Response:
        assert path == "/v1/strategy-presets"
        return _Response(self._strategies)


_STRATEGIES = [
    {"id": KIYOHARA_GLOBAL_ID},
    {"id": "unsupported_strategy"},
]


def _evaluation_item(symbol: str) -> SimpleNamespace:
    return SimpleNamespace(
        symbol=symbol,
        research_priority=SimpleNamespace(score=80.0),
        market_cap=100.0,
        pe_ratio=10.0,
        yowayowa_conservative_net_cash_ratio=0.8,
        net_cash_ratio=0.9,
        net_cash_ratio_is_lower_bound=False,
        cash_neutral_pe=5.0,
        cash_neutral_pe_is_upper_bound=False,
        revenue_growth_yoy=0.1,
        free_cash_flow=10.0,
    )


def _outcome(
    *,
    evaluations: int = 0,
    snapshots: int = 0,
) -> SimpleNamespace:
    evaluation_items = [_evaluation_item(f"SYM{index}") for index in range(1, evaluations + 1)]
    return SimpleNamespace(
        evaluations=evaluation_items,
        snapshots=[
            SimpleNamespace(id=index, symbol=f"SYM{index}") for index in range(1, snapshots + 1)
        ],
        errors={},
        supplement_errors={},
    )


def test_snapshot_builtins_runs_only_implemented_presets(monkeypatch: Any) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(preset_cli, "run_builtin", lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr(preset_cli, "_client", lambda *_args: _Client(_STRATEGIES))

    result = CliRunner().invoke(app, ["preset", "snapshot-builtins", "--region", "jp"])

    assert result.exit_code == 0, result.output
    assert calls == [
        {
            "strategy_id": KIYOHARA_GLOBAL_ID,
            "region": "jp",
            "size": 25,
            "edinet_key": None,
            "base_url": "http://127.0.0.1:8000",
            "token": None,
            "via_api": False,
        }
    ]


def test_snapshot_builtins_via_api_keeps_http_route(monkeypatch: Any) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(preset_cli, "run_builtin", lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr(preset_cli, "_client", lambda *_args: _Client(_STRATEGIES))

    result = CliRunner().invoke(
        app,
        [
            "preset",
            "snapshot-builtins",
            "--region",
            "jp",
            "--via-api",
            "--base-url",
            "http://127.0.0.1:9999",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls == [
        {
            "strategy_id": KIYOHARA_GLOBAL_ID,
            "region": "jp",
            "size": 25,
            "edinet_key": None,
            "base_url": "http://127.0.0.1:9999",
            "token": None,
            "via_api": True,
        }
    ]


def test_snapshot_builtins_fails_closed_when_no_implemented_preset(monkeypatch: Any) -> None:
    monkeypatch.setattr(preset_cli, "list_builtin_strategies", lambda: [])
    monkeypatch.setattr(
        preset_cli, "run_builtin", lambda **kwargs: (_ for _ in ()).throw(AssertionError("no call"))
    )

    result = CliRunner().invoke(app, ["preset", "snapshot-builtins"])

    assert result.exit_code != 0
    assert "No implemented built-in strategy" in result.output


def test_snapshot_builtins_via_api_fails_closed_when_no_implemented_preset(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(preset_cli, "_client", lambda *_args: _Client([{"id": "unsupported"}]))
    monkeypatch.setattr(
        preset_cli, "run_builtin", lambda **kwargs: (_ for _ in ()).throw(AssertionError("no call"))
    )

    result = CliRunner().invoke(app, ["preset", "snapshot-builtins", "--via-api"])

    assert result.exit_code != 0
    assert "No implemented built-in strategy" in result.output


def test_run_builtin_defaults_to_local_service_without_http(monkeypatch: Any) -> None:
    calls: list[dict[str, Any]] = []
    closed: list[bool] = []
    session = SimpleNamespace(name="session", close=lambda: closed.append(True))

    def fake_session_factory() -> SimpleNamespace:
        return session

    def fake_evaluate_and_record_builtin(*args: Any, **kwargs: Any) -> SimpleNamespace:
        calls.append({"args": args, "kwargs": kwargs})
        return _outcome(evaluations=3, snapshots=3)

    monkeypatch.setattr(preset_cli, "get_session", fake_session_factory)
    monkeypatch.setattr(preset_cli, "evaluate_and_record_builtin", fake_evaluate_and_record_builtin)
    monkeypatch.setattr(preset_cli, "list_strategy_snapshots", lambda *_args, **_kwargs: [])

    result = CliRunner().invoke(
        app,
        ["preset", "run-builtin", KIYOHARA_GLOBAL_ID, "--region", " JP "],
    )

    assert result.exit_code == 0, result.output
    assert closed == [True]
    assert calls == [
        {
            "args": (session, KIYOHARA_GLOBAL_ID, "jp", 25),
            "kwargs": {"edinet_key": None},
        }
    ]
    assert "Recorded 3 new snapshot row(s)" in result.output
    assert "duplicates skipped: 0" in result.output


def test_run_builtin_counts_same_day_duplicates(
    monkeypatch: Any,
    frozen_time: datetime,
) -> None:
    existing = [
        SimpleNamespace(symbol="GOOD", captured_at=frozen_time),
        SimpleNamespace(symbol="OLD", captured_at=datetime(2025, 9, 1, tzinfo=UTC)),
    ]
    outcome = SimpleNamespace(
        evaluations=[_evaluation_item(symbol="GOOD")],
        snapshots=[SimpleNamespace(id=1, symbol="GOOD")],
        errors={},
        supplement_errors={},
    )
    monkeypatch.setattr(preset_cli, "get_session", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(
        preset_cli, "evaluate_and_record_builtin", lambda *_args, **_kwargs: outcome
    )
    monkeypatch.setattr(preset_cli, "list_strategy_snapshots", lambda *_args, **_kwargs: existing)

    result = CliRunner().invoke(
        app,
        ["preset", "run-builtin", KIYOHARA_GLOBAL_ID, "--region", "jp"],
    )

    assert result.exit_code == 0, result.output
    assert "Recorded 0 new snapshot row(s)" in result.output
    assert "duplicates skipped: 1" in result.output


def test_run_builtin_reports_errors_and_duplicate_skips(monkeypatch: Any) -> None:
    outcome = SimpleNamespace(
        evaluations=[],
        snapshots=[],
        errors={"7203.T": "LookupError: fixture unavailable"},
        supplement_errors={},
    )
    monkeypatch.setattr(preset_cli, "get_session", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(
        preset_cli, "evaluate_and_record_builtin", lambda *_args, **_kwargs: outcome
    )
    monkeypatch.setattr(preset_cli, "list_strategy_snapshots", lambda *_args, **_kwargs: [])

    result = CliRunner().invoke(
        app,
        ["preset", "run-builtin", KIYOHARA_GLOBAL_ID, "--region", "jp"],
    )

    assert result.exit_code == 0, result.output
    assert "No candidates with market-cap data were returned." in result.output
    assert "Unavailable: 7203.T" in result.output
    assert "LookupError: fixture unavailable" in result.output
    assert "Recorded 0 new snapshot row(s)" in result.output


def test_run_builtin_fails_closed_outside_personal_mode(monkeypatch: Any) -> None:
    monkeypatch.setattr(preset_cli, "get_settings", lambda: SimpleNamespace(mode="public"))
    monkeypatch.setattr(
        preset_cli,
        "evaluate_and_record_builtin",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("no local run")),
    )

    result = CliRunner().invoke(
        app,
        ["preset", "run-builtin", KIYOHARA_GLOBAL_ID, "--region", "jp"],
    )

    assert result.exit_code != 0
    assert "personal" in result.output


def test_run_builtin_help_is_available() -> None:
    result = CliRunner().invoke(app, ["preset", "run-builtin", "--help"])
    assert result.exit_code == 0, result.output
    assert "--via-api" in result.output


def test_snapshot_builtins_help_is_available() -> None:
    result = CliRunner().invoke(app, ["preset", "snapshot-builtins", "--help"])
    assert result.exit_code == 0, result.output
    assert "--via-api" in result.output
