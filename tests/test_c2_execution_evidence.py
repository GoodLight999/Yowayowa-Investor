"""Tests for the C2 execution-evidence fields.

Covers the four minimal fixes from the lane15 evidence verdict:
1. order status history (append-only) for orders the ledger left unsettled,
2. target weights vs. actual holdings split in ``state.json`` (backward compatible),
3. ``positions_count`` / ``gross_exposure_15`` on each MTM row (existing keys kept),
4. ``zero_exposure_streak`` surveillance in the gap watchdog.
Plus the review follow-up: a failed ``GET /positions`` is *not* a flat book, so it never
overwrites the last observation or records a fresh zero.

All fixtures are local (``tmp_path`` plus the frozen synthetic history); no test reads
``data/paper_c2`` or the live OHLCV store.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from scripts.paper_c2 import c2_gap_watch, c2_runner
from scripts.paper_c2.alpaca_paper_client import AlpacaAccountSummary, AlpacaPaperClient
from tests.c2_synthetic_history import detached_copy, synthetic_base_history

C2_SYMBOLS = ("SPY", "IWM", "JNJ", "PG")
SESSION_DATE = date(2026, 11, 3)

LEGACY_MTM_KEYS = {
    "date",
    "last_bar_date",
    "timestamp_utc",
    "equity_10",
    "equity_15",
    "alpaca_paper_equity",
    "daily_return_10",
    "daily_return_15",
    "drawdown_10",
    "drawdown_15",
}


def _fresh_history() -> dict[str, list[dict[str, Any]]]:
    """Frozen base history plus a 2026-11-03 bar so the session date matches."""
    history = detached_copy(synthetic_base_history())
    for sym in C2_SYMBOLS:
        bars = history[sym]
        prev_close = bars[-1]["c"]
        bars.append(
            {
                "t": "2026-11-03T04:00:00Z",
                "o": prev_close,
                "h": round(prev_close * 1.01, 2),
                "l": round(prev_close * 0.99, 2),
                "c": round(prev_close * 1.005, 2),
                "v": 100_000.0,
            }
        )
    return history


def _base_state(**overrides: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "alpaca_paper_equity": 100_000.0,
        "completed_trading_days": [],
        "consecutive_api_failures": 0,
        "current_equity_10": 100_000.0,
        "current_equity_15": 100_000.0,
        "current_weights_10": {
            "IWM": 0.214424,
            "JNJ": 0.214424,
            "PG": 0.214424,
            "SPY": 0.214424,
            "USD": 0.142304,
        },
        # Pre-split state: no target_weights_15 / actual_positions_15 / exposure_15.
        "current_weights_15": {"IWM": 0.25, "JNJ": 0.25, "PG": 0.25, "SPY": 0.25, "USD": 0.0},
        "initial_paper_equity": 100_000.0,
        "kill_switch_event": None,
        "kill_switch_triggered": False,
        "last_decision_date": "2026-11-03",
        "last_mtm_bar_date": "2026-10-30",
        "last_mtm_date": None,
        "missed_rebalance_days": 0,
        "missed_trading_days": 0,
        "peak_equity_10": 100_000.0,
        "peak_equity_15": 100_000.0,
    }
    state.update(overrides)
    return state


def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Path]:
    paths = {
        "state": tmp_path / "state.json",
        "mtm": tmp_path / "mtm_history.jsonl",
        "orders": tmp_path / "orders.jsonl",
        "order_status": tmp_path / "order_status_history.jsonl",
        "attribution_json": tmp_path / "attribution_summary.json",
        "attribution_md": tmp_path / "attribution_summary.md",
        "ledger": tmp_path / "decisions.jsonl",
    }
    monkeypatch.setattr(c2_runner, "DATA_DIR", tmp_path)
    monkeypatch.setattr(c2_runner, "STATE_FILE", paths["state"])
    monkeypatch.setattr(c2_runner, "MTM_FILE", paths["mtm"])
    monkeypatch.setattr(c2_runner, "ORDERS_FILE", paths["orders"])
    monkeypatch.setattr(c2_runner, "ORDER_STATUS_FILE", paths["order_status"])
    monkeypatch.setattr(c2_runner, "ATTRIBUTION_JSON", paths["attribution_json"])
    monkeypatch.setattr(c2_runner, "ATTRIBUTION_MD", paths["attribution_md"])
    monkeypatch.setattr(c2_runner, "DEFAULT_LEDGER_PATH", paths["ledger"])
    monkeypatch.setattr(c2_runner, "load_local_history", _fresh_history)
    return paths


def _mock_client(
    equity: float = 100_500.0,
    positions: list[dict[str, Any]] | None = None,
) -> MagicMock:
    client = MagicMock()
    client.get_account.return_value = AlpacaAccountSummary(
        account_id="test_acc",
        masked_id="test...1ad4",
        status="ACTIVE",
        currency="USD",
        equity=equity,
        cash=0.0,
        buying_power=0.0,
        raw={},
    )
    client.get_positions.return_value = positions if positions is not None else []
    client.get_orders.return_value = []
    return client


def _write_orders(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _order_row(
    symbol: str,
    client_order_id: str,
    status: str,
) -> dict[str, Any]:
    return {
        "as_of_date": "2026-11-03",
        "timestamp_utc": "2026-11-02T10:16:03+00:00",
        "symbol": symbol,
        "side": "buy",
        "qty": 10,
        "order_type": "limit",
        "time_in_force": "day",
        "reference_price": 100.0,
        "limit_price": 100.5,
        "slippage_tolerance_bps": 50.0,
        "client_order_id": client_order_id,
        "alpaca_response": {
            "client_order_id": client_order_id,
            "symbol": symbol,
            "status": status,
            "filled_qty": "0",
            "filled_avg_price": None,
        },
    }


def test_daily_cycle_records_holdings_split_and_keeps_existing_mtm_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _isolate(monkeypatch, tmp_path)
    legacy_weights = dict(_base_state()["current_weights_15"])
    c2_runner.save_state(_base_state())

    positions = [
        {"symbol": "SPY", "qty": "10", "market_value": "25000.0"},
        {"symbol": "IWM", "qty": "20", "market_value": "15000.0"},
    ]
    result = c2_runner.run_daily_cycle(
        as_of_date=SESSION_DATE,
        ledger_path=paths["ledger"],
        override_history=_fresh_history(),
        client=_mock_client(positions=positions),
    )
    assert result["status"] == "success"

    record = result["mtm_record"]
    # Backward compatibility: every pre-existing key is still present, unchanged names.
    assert record.keys() >= LEGACY_MTM_KEYS
    assert record["date"] == "2026-11-03"
    assert record["equity_15"] == 100_500.0
    # New exposure evidence.
    assert record["positions_count"] == 2
    assert record["gross_exposure_15"] == pytest.approx(round(40_000.0 / 100_500.0, 6))

    saved = json.loads(paths["state"].read_text(encoding="utf-8"))
    # current_weights_15 is retained for readers and still mirrors the target.
    assert saved["current_weights_15"] == legacy_weights
    assert saved["target_weights_15"] == legacy_weights
    assert saved["actual_positions_15"] == {
        "SPY": {"qty": 10.0, "market_value": 25_000.0},
        "IWM": {"qty": 20.0, "market_value": 15_000.0},
    }
    assert saved["exposure_15"] == pytest.approx(round(40_000.0 / 100_500.0, 6))


def test_daily_cycle_with_flat_book_records_zero_exposure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _isolate(monkeypatch, tmp_path)
    c2_runner.save_state(_base_state())

    result = c2_runner.run_daily_cycle(
        as_of_date=SESSION_DATE,
        ledger_path=paths["ledger"],
        override_history=_fresh_history(),
        client=_mock_client(positions=[]),
    )
    assert result["mtm_record"]["positions_count"] == 0
    assert result["mtm_record"]["gross_exposure_15"] == 0.0
    saved = json.loads(paths["state"].read_text(encoding="utf-8"))
    assert saved["actual_positions_15"] == {}
    assert saved["exposure_15"] == 0.0


def test_weekly_cycle_updates_target_only_not_holdings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _isolate(monkeypatch, tmp_path)
    observed = {"SPY": {"qty": 3.0, "market_value": 1_000.0}}
    c2_runner.save_state(
        _base_state(
            last_decision_date=None,
            target_weights_15={"SPY": 0.1, "USD": 0.9},
            actual_positions_15=observed,
            exposure_15=0.01,
        )
    )

    class _MockWeeklyClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.base_url = "https://paper-api.alpaca.markets/v2"

        def get_daily_bars(self, *args: Any, **kwargs: Any) -> dict[str, list[dict[str, Any]]]:
            return {}

    monkeypatch.setattr(c2_runner, "AlpacaPaperClient", _MockWeeklyClient)

    result = c2_runner.run_weekly_cycle(
        as_of_date=SESSION_DATE,
        dry_run=True,
        force=False,
        force_rule_only=True,
        ledger_path=paths["ledger"],
    )
    assert result["status"] == "success"

    saved = json.loads(paths["state"].read_text(encoding="utf-8"))
    assert saved["target_weights_15"] == saved["current_weights_15"]
    assert sum(saved["target_weights_15"].values()) == pytest.approx(1.0)
    # The weekly cycle never observes holdings, so it must not claim them as achieved.
    assert saved["actual_positions_15"] == observed
    assert saved["exposure_15"] == 0.01


def test_daily_cycle_appends_broker_status_for_unsettled_orders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _isolate(monkeypatch, tmp_path)
    c2_runner.save_state(_base_state())
    _write_orders(
        paths["orders"],
        [
            _order_row("SPY", "c2_SPY_buy_2026-11-03", "pending_new"),
            _order_row("IWM", "c2_IWM_buy_2026-11-03", "new"),
            _order_row("JNJ", "c2_JNJ_buy_2026-11-03", "filled"),
        ],
    )
    orders_before = hashlib.sha256(paths["orders"].read_bytes()).hexdigest()

    client = _mock_client()
    client.get_orders.return_value = [
        {
            "client_order_id": "c2_SPY_buy_2026-11-03",
            "symbol": "SPY",
            "status": "canceled",
            "filled_qty": "0",
            "filled_avg_price": None,
            "canceled_at": "2026-11-02T11:02:08.921006Z",
            "expired_at": None,
        },
        {
            "client_order_id": "c2_IWM_buy_2026-11-03",
            "symbol": "IWM",
            "status": "filled",
            "filled_qty": "89",
            "filled_avg_price": "279.99",
            "canceled_at": None,
            "expired_at": None,
        },
    ]

    result = c2_runner.run_daily_cycle(
        as_of_date=SESSION_DATE,
        ledger_path=paths["ledger"],
        override_history=_fresh_history(),
        client=client,
    )
    assert result["status"] == "success"
    client.get_orders.assert_called_once_with(status="all")

    history = [
        json.loads(line)
        for line in paths["order_status"].read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(history) == 2
    required = {
        "checked_at",
        "client_order_id",
        "symbol",
        "status",
        "filled_qty",
        "filled_avg_price",
        "canceled_at",
        "expired_at",
    }
    for record in history:
        assert required <= set(record)
        assert record["found"] is True
        datetime.fromisoformat(record["checked_at"])
    by_symbol = {record["symbol"]: record for record in history}
    assert by_symbol["SPY"]["status"] == "canceled"
    assert by_symbol["SPY"]["filled_qty"] == "0"
    assert by_symbol["IWM"]["status"] == "filled"

    # The order ledger itself is append-only: not one byte changed by reconciliation.
    assert hashlib.sha256(paths["orders"].read_bytes()).hexdigest() == orders_before

    # Second daily invocation: these terminal statuses are already on record, so the
    # cycle must not re-query the broker or append duplicate lines.
    rerun = c2_runner.run_daily_cycle(
        as_of_date=SESSION_DATE,
        ledger_path=paths["ledger"],
        override_history=_fresh_history(),
        client=client,
    )
    assert rerun["status"] == "already_executed"
    assert client.get_orders.call_count == 1
    assert len(paths["order_status"].read_text(encoding="utf-8").splitlines()) == 2


def test_daily_cycle_does_not_call_get_orders_without_unsettled_orders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _isolate(monkeypatch, tmp_path)
    c2_runner.save_state(_base_state())
    _write_orders(
        paths["orders"],
        [_order_row("SPY", "c2_SPY_buy_2026-11-03", "filled")],
    )

    client = _mock_client()
    result = c2_runner.run_daily_cycle(
        as_of_date=SESSION_DATE,
        ledger_path=paths["ledger"],
        override_history=_fresh_history(),
        client=client,
    )
    assert result["status"] == "success"
    assert client.get_orders.call_count == 0
    assert not paths["order_status"].exists()


def test_order_status_api_failure_is_recorded_and_mtm_still_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _isolate(monkeypatch, tmp_path)
    c2_runner.save_state(_base_state())
    _write_orders(paths["orders"], [_order_row("SPY", "c2_SPY_buy_2026-11-03", "accepted")])

    client = _mock_client()
    client.get_orders.side_effect = RuntimeError("broker unavailable")

    result = c2_runner.run_daily_cycle(
        as_of_date=SESSION_DATE,
        ledger_path=paths["ledger"],
        override_history=_fresh_history(),
        client=client,
    )
    # The daily MTM is not stopped by a broker failure.
    assert result["status"] == "success"
    assert result["order_status"]["error"] is not None

    history = [
        json.loads(line)
        for line in paths["order_status"].read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(history) == 1
    assert set(history[0]) == {"checked_at", "error"}
    assert "RuntimeError" in history[0]["error"]


def _write_gap_fixture(
    data_dir: Path,
    state: dict[str, Any],
    mtm_rows: list[dict[str, Any]],
) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (data_dir / "mtm_history.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in mtm_rows), encoding="utf-8"
    )


def _legacy_mtm(date_str: str, daily_return_15: float) -> dict[str, Any]:
    return {
        "date": date_str,
        "last_bar_date": date_str,
        "timestamp_utc": f"{date_str}T00:01:00+00:00",
        "equity_10": 100_000.0,
        "equity_15": 100_000.0,
        "alpaca_paper_equity": 100_000.0,
        "daily_return_10": 0.0,
        "daily_return_15": daily_return_15,
        "drawdown_10": 0.0,
        "drawdown_15": 0.0,
    }


def _gap_state(target_key: str = "target_weights_15") -> dict[str, Any]:
    state = _base_state()
    state[target_key] = {"SPY": 0.25, "IWM": 0.25, "JNJ": 0.25, "PG": 0.25, "USD": 0.0}
    state["last_mtm_date"] = "2026-11-05"
    state["last_mtm_bar_date"] = "2026-11-05"
    state["completed_trading_days"] = ["2026-11-03", "2026-11-04", "2026-11-05"]
    if target_key == "current_weights_15":
        state.pop("target_weights_15", None)
    return state


def _collect(data_dir: Path, tmp_path: Path) -> tuple[list[str], dict[str, Any]]:
    return c2_gap_watch.collect_problems(
        data_dir=data_dir,
        ohlcv_dir=tmp_path / "no-ohlcv",
        db=tmp_path / "missing-executions.db",
        job_id="test",
        phase_start=date(2026, 11, 3),
        now=datetime.fromisoformat("2026-11-06T09:30:00+09:00"),
    )


@pytest.mark.parametrize("target_key", ["target_weights_15", "current_weights_15"])
def test_gap_watch_zero_exposure_streak_on_legacy_rows(tmp_path: Path, target_key: str) -> None:
    data_dir = tmp_path / "paper_c2"
    _write_gap_fixture(
        data_dir,
        _gap_state(target_key),
        [
            _legacy_mtm("2026-11-03", 0.0),
            _legacy_mtm("2026-11-04", 0.0),
            _legacy_mtm("2026-11-05", 0.0),
        ],
    )
    problems, facts = _collect(data_dir, tmp_path)
    assert facts["zero_exposure_streak"] == 3
    assert facts["target_weight_abs_sum"] == pytest.approx(1.0)
    assert any("zero exposure for 3 consecutive sessions" in p for p in problems)


def test_gap_watch_streak_breaks_on_a_day_with_exposure(tmp_path: Path) -> None:
    data_dir = tmp_path / "paper_c2"
    rows = [
        _legacy_mtm("2026-11-03", 0.0),
        _legacy_mtm("2026-11-04", 0.0),
        {**_legacy_mtm("2026-11-05", 0.012), "gross_exposure_15": 0.85},
    ]
    _write_gap_fixture(data_dir, _gap_state(), rows)
    problems, facts = _collect(data_dir, tmp_path)
    assert facts["zero_exposure_streak"] == 0
    assert not any("zero exposure" in p for p in problems)


def test_gap_watch_uses_recorded_gross_exposure_field(tmp_path: Path) -> None:
    data_dir = tmp_path / "paper_c2"
    rows = [
        _legacy_mtm("2026-11-03", 0.0),
        {**_legacy_mtm("2026-11-06", 0.0), "gross_exposure_15": 0.0},
        {**_legacy_mtm("2026-11-09", 0.0), "gross_exposure_15": 0.0},
    ]
    state = _gap_state()
    state["last_mtm_date"] = "2026-11-09"
    state["last_mtm_bar_date"] = "2026-11-09"
    state["completed_trading_days"] = ["2026-11-03", "2026-11-06", "2026-11-09"]
    _write_gap_fixture(data_dir, state, rows)
    problems, facts = _collect(data_dir, tmp_path)
    assert facts["zero_exposure_streak"] == 3
    assert any("zero exposure" in p for p in problems)


def test_gap_watch_no_warning_when_target_is_cash_only(tmp_path: Path) -> None:
    data_dir = tmp_path / "paper_c2"
    state = _gap_state()
    state["target_weights_15"] = {"SPY": 0.0, "IWM": 0.0, "JNJ": 0.0, "PG": 0.0, "USD": 1.0}
    _write_gap_fixture(
        data_dir,
        state,
        [
            _legacy_mtm("2026-11-03", 0.0),
            _legacy_mtm("2026-11-04", 0.0),
            _legacy_mtm("2026-11-05", 0.0),
        ],
    )
    problems, facts = _collect(data_dir, tmp_path)
    assert facts["target_weight_abs_sum"] == 0.0
    assert facts["zero_exposure_streak"] == 0
    assert not any("zero exposure" in p for p in problems)


# ---------------------------------------------------------------------------
# Review follow-up: a failed GET /positions must not be recorded as a flat book.
# ---------------------------------------------------------------------------


def _real_paper_client() -> AlpacaPaperClient:
    """Real client with explicit dummy credentials: no secret file, no network."""
    return AlpacaPaperClient(
        key_id="test-key-id",
        secret_key="test-secret",
        base_url="https://paper-api.alpaca.markets/v2",
        data_url="https://data.alpaca.markets/v2",
    )


def _client_with_positions_outage(equity: float = 100_500.0) -> AlpacaPaperClient:
    """Client whose GET /positions fails exactly the way ``_request`` reports failures.

    This reproduces the reviewer's probe against the production code path: ``_request``
    maps HTTP errors and connection failures to an error *dict* (never an exception),
    so the strictness has to live in ``get_positions`` itself.
    """
    client = _real_paper_client()

    def fake_request(
        url: str,
        method: str = "GET",
        data: dict[str, Any] | None = None,
        timeout: float = 30.0,
    ) -> Any:
        if url.endswith("/account"):
            return {
                "id": "testacct0001",
                "status": "ACTIVE",
                "currency": "USD",
                "equity": str(equity),
                "cash": "0",
                "buying_power": "0",
            }
        if url.endswith("/positions"):
            return {"error": "Timed out", "status": "connection_error"}
        if "/orders" in url:
            return []
        raise AssertionError(f"unexpected Alpaca URL in test: {url}")

    client._request = fake_request  # type: ignore[method-assign]
    return client


def test_get_positions_raises_on_failed_call_but_keeps_empty_list_for_flat_book(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _real_paper_client()

    monkeypatch.setattr(
        client, "_request", lambda *a, **k: {"http_code": 503, "message": "unavailable"}
    )
    # The message wording belongs to the client (HEAD says "read", the parent card's
    # patch said "fetch"); what the contract requires is that both failure shapes raise.
    with pytest.raises(RuntimeError, match=r"Failed to (read|fetch) Alpaca positions"):
        client.get_positions()

    monkeypatch.setattr(
        client,
        "_request",
        lambda *a, **k: {"error": "Timed out", "status": "connection_error"},
    )
    with pytest.raises(RuntimeError, match=r"Failed to (read|fetch) Alpaca positions"):
        client.get_positions()

    # A flat account answers with a JSON list; that is a valid empty book, not a failure.
    monkeypatch.setattr(client, "_request", lambda *a, **k: [])
    assert client.get_positions() == []


def test_daily_cycle_keeps_last_observed_holdings_when_positions_api_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _isolate(monkeypatch, tmp_path)
    observed = {"SPY": {"qty": 10.0, "market_value": 25_000.0}}
    c2_runner.save_state(_base_state(actual_positions_15=observed, exposure_15=0.25))

    result = c2_runner.run_daily_cycle(
        as_of_date=SESSION_DATE,
        ledger_path=paths["ledger"],
        override_history=_fresh_history(),
        client=_client_with_positions_outage(),
    )
    assert result["status"] == "success"

    record = result["mtm_record"]
    assert record["positions_source"] == "carried_forward"
    assert record["positions_count"] == 1
    assert record["gross_exposure_15"] == pytest.approx(round(25_000.0 / 100_500.0, 6))
    # Either wording is the client's business; the point is that the outage is recorded.
    assert "Alpaca positions" in record["positions_error"]

    saved = json.loads(paths["state"].read_text(encoding="utf-8"))
    # The outage must not overwrite the last observation with a fabricated flat book.
    assert saved["actual_positions_15"] == observed
    assert saved["exposure_15"] == 0.25


def test_daily_cycle_marks_exposure_unknown_without_prior_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _isolate(monkeypatch, tmp_path)
    c2_runner.save_state(_base_state())

    result = c2_runner.run_daily_cycle(
        as_of_date=SESSION_DATE,
        ledger_path=paths["ledger"],
        override_history=_fresh_history(),
        client=_client_with_positions_outage(),
    )
    assert result["status"] == "success"

    record = result["mtm_record"]
    # No observation ever succeeded: the row says "unknown" and asserts no numbers.
    assert record["positions_source"] == "unknown"
    assert record["positions_count"] is None
    assert record["gross_exposure_15"] is None

    saved = json.loads(paths["state"].read_text(encoding="utf-8"))
    assert "actual_positions_15" not in saved
    assert "exposure_15" not in saved


def test_gap_watch_streak_breaks_on_non_fresh_positions_rows(tmp_path: Path) -> None:
    data_dir = tmp_path / "paper_c2"
    rows = [
        {
            **_legacy_mtm("2026-11-03", 0.0),
            "positions_count": 0,
            "gross_exposure_15": 0.0,
            "positions_source": "fresh",
        },
        {
            **_legacy_mtm("2026-11-04", 0.0),
            "positions_count": 0,
            "gross_exposure_15": 0.0,
            "positions_source": "fresh",
        },
        {
            **_legacy_mtm("2026-11-05", 0.0),
            "positions_count": None,
            "gross_exposure_15": None,
            "positions_source": "unknown",
        },
    ]
    _write_gap_fixture(data_dir, _gap_state(), rows)
    problems, facts = _collect(data_dir, tmp_path)
    # An unobserved session can neither extend nor fabricate a zero-exposure streak.
    assert facts["zero_exposure_streak"] == 0
    assert not any("zero exposure" in p for p in problems)


def test_gap_watch_carried_forward_zero_does_not_extend_streak(tmp_path: Path) -> None:
    data_dir = tmp_path / "paper_c2"
    rows = [
        {
            **_legacy_mtm("2026-11-03", 0.0),
            "positions_count": 0,
            "gross_exposure_15": 0.0,
            "positions_source": "fresh",
        },
        {
            **_legacy_mtm("2026-11-04", 0.0),
            "positions_count": 0,
            "gross_exposure_15": 0.0,
            "positions_source": "carried_forward",
        },
    ]
    _write_gap_fixture(data_dir, _gap_state(), rows)
    problems, facts = _collect(data_dir, tmp_path)
    assert facts["zero_exposure_streak"] == 0
    assert not any("zero exposure" in p for p in problems)


def test_weekly_cycle_survives_positions_api_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _isolate(monkeypatch, tmp_path)
    c2_runner.save_state(_base_state(last_decision_date=None))

    submitted: list[dict[str, Any]] = []

    class _PositionsOutageClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.base_url = "https://paper-api.alpaca.markets/v2"

        def get_daily_bars(self, *args: Any, **kwargs: Any) -> dict[str, list[dict[str, Any]]]:
            return {}

        def get_account(self) -> AlpacaAccountSummary:
            return AlpacaAccountSummary(
                account_id="test_acc",
                masked_id="test...1ad4",
                status="ACTIVE",
                currency="USD",
                equity=100_000.0,
                cash=100_000.0,
                buying_power=200_000.0,
                raw={},
            )

        def get_positions(self) -> list[dict[str, Any]]:
            raise RuntimeError("Failed to fetch Alpaca positions: {'http_code': 503}")

        def submit_paper_order(self, **kwargs: Any) -> dict[str, Any]:
            submitted.append(kwargs)
            return {"status": "accepted"}

    monkeypatch.setattr(c2_runner, "AlpacaPaperClient", _PositionsOutageClient)

    result = c2_runner.run_weekly_cycle(
        as_of_date=SESSION_DATE,
        dry_run=False,
        force=False,
        force_rule_only=True,
        ledger_path=paths["ledger"],
    )
    # The weekly cycle still completes and records its decision; it just does not
    # rebalance against an unknown book.
    assert result["status"] == "success"
    assert result["orders_placed"] == []
    assert submitted == []
    saved = json.loads(paths["state"].read_text(encoding="utf-8"))
    # HEAD (model B) already fails closed with its own gate: the batch is not sent and
    # every blocked order is recorded with reason=positions_unreadable, instead of the
    # parent card's "let it raise into missed_rebalance_days". Same invariant, stronger
    # evidence (the reason is on the record), so the assertion follows HEAD.
    assert any(
        blocked.get("reason") == "positions_unreadable" for blocked in result["orders_blocked"]
    )
    assert saved["target_weights_15"] == saved["current_weights_15"]
