"""Tests for C2 daily MTM boundary, idempotency, and bar-date alignment."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from scripts.paper_c2.alpaca_paper_client import AlpacaAccountSummary
from scripts.paper_c2.c2_runner import (
    run_daily_cycle,
    save_state,
)

C2_SYMBOLS = ("SPY", "IWM", "JNJ", "PG")
SYNTHETIC_BASE_END = date(2026, 10, 30)


def _synthetic_base_history(
    end_date: date = SYNTHETIC_BASE_END, n_bars: int = 260
) -> dict[str, list[dict[str, Any]]]:
    """Deterministic OHLCV series whose last bar is always `end_date`.

    Bootstrap history must never come from the live store
    (data/stock-ohlcv/*/ohlcv.jsonl): that store advances every trading day, so the
    baseline "last bar is 2026-10-30" premise of this test would silently decay.
    This fixture is frozen, keeping the stale case stale and the fresh case exactly
    one bar ahead no matter what the production store contains.
    """
    sessions: list[date] = []
    cursor = end_date
    while len(sessions) < n_bars:
        if cursor.weekday() < 5:  # weekdays only
            sessions.append(cursor)
        cursor -= timedelta(days=1)
    sessions.reverse()

    starts = {"SPY": 500.0, "IWM": 200.0, "JNJ": 150.0, "PG": 160.0}
    drifts = {"SPY": 0.8, "IWM": 0.5, "JNJ": 0.25, "PG": 0.3}

    def _close(sym: str, i: int) -> float:
        return round(starts[sym] + drifts[sym] * i + 0.4 * ((i % 5) - 2), 2)

    history: dict[str, list[dict[str, Any]]] = {}
    for sym in C2_SYMBOLS:
        bars: list[dict[str, Any]] = []
        for i, session in enumerate(sessions):
            close = _close(sym, i)
            prev = _close(sym, i - 1) if i else close
            bars.append(
                {
                    "t": f"{session.isoformat()}T04:00:00Z",
                    "o": prev,
                    "h": round(max(prev, close) * 1.002, 2),
                    "l": round(min(prev, close) * 0.998, 2),
                    "c": close,
                    "v": 1_000_000.0,
                }
            )
        history[sym] = bars
    return history


def test_daily_cycle_stale_bar_skips_and_fresh_bar_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Set up isolated data directory.
    # NOTE: run_daily_cycle writes ATTRIBUTION_* and ORDERS_* through module-level
    # constants derived from DATA_DIR at import time, so they must be redirected
    # explicitly — patching DATA_DIR alone still writes the live data/paper_c2 files.
    test_state_file = tmp_path / "state.json"
    test_mtm_file = tmp_path / "mtm_history.jsonl"
    monkeypatch.setattr("scripts.paper_c2.c2_runner.STATE_FILE", test_state_file)
    monkeypatch.setattr("scripts.paper_c2.c2_runner.MTM_FILE", test_mtm_file)
    monkeypatch.setattr("scripts.paper_c2.c2_runner.DATA_DIR", tmp_path)
    monkeypatch.setattr(
        "scripts.paper_c2.c2_runner.ATTRIBUTION_JSON", tmp_path / "attribution_summary.json"
    )
    monkeypatch.setattr(
        "scripts.paper_c2.c2_runner.ATTRIBUTION_MD", tmp_path / "attribution_summary.md"
    )
    monkeypatch.setattr("scripts.paper_c2.c2_runner.ORDERS_FILE", tmp_path / "orders.jsonl")
    test_ledger = tmp_path / "decisions.jsonl"

    # Clean initial state at boundary
    initial_state = {
        "alpaca_paper_equity": 100000.0,
        "completed_trading_days": [],
        "consecutive_api_failures": 0,
        "current_equity_10": 100000.0,
        "current_equity_15": 100000.0,
        "current_weights_10": {
            "IWM": 0.214424,
            "JNJ": 0.214424,
            "PG": 0.214424,
            "SPY": 0.214424,
            "USD": 0.142304,
        },
        "current_weights_15": {
            "IWM": 0.25,
            "JNJ": 0.25,
            "PG": 0.25,
            "SPY": 0.25,
            "USD": 0.0,
        },
        "initial_paper_equity": 100000.0,
        "kill_switch_event": None,
        "kill_switch_triggered": False,
        "last_decision_date": "2026-11-03",
        "last_mtm_bar_date": "2026-10-30",
        "last_mtm_date": None,
        "missed_rebalance_days": 0,
        "missed_trading_days": 0,
        "peak_equity_10": 100000.0,
        "peak_equity_15": 100000.0,
    }
    save_state(initial_state)

    # Mock client with paper equity at +0.35% return ($100,350.00)
    mock_client = MagicMock()
    mock_acct = AlpacaAccountSummary(
        account_id="test_acc",
        masked_id="test...1ad4",
        status="ACTIVE",
        currency="USD",
        equity=100350.00,
        cash=25000.00,
        buying_power=200000.00,
        raw={},
    )
    mock_client.get_account.return_value = mock_acct

    # Frozen synthetic bootstrap history — never the live data/stock-ohlcv store.
    # Patching load_local_history keeps run_daily_cycle honest if it ever falls back
    # to the default history source instead of the explicit override below.
    base_history = _synthetic_base_history()
    monkeypatch.setattr(
        "scripts.paper_c2.c2_runner.load_local_history",
        lambda: {sym: [dict(bar) for bar in bars] for sym, bars in base_history.items()},
    )

    # Case 1: Latest bar is 2026-11-02 (stale/pre-phase bar)
    stale_history = {}
    for s in ["SPY", "IWM", "JNJ", "PG"]:
        bars = list(base_history[s])
        prev_close = bars[-1]["c"]
        bars.append({
            "t": "2026-11-02T04:00:00Z",
            "o": prev_close,
            "h": prev_close * 1.002,
            "l": prev_close * 0.998,
            "c": prev_close * 1.001,
            "v": 50000.0,
        })
        stale_history[s] = bars

    # Run for 2026-11-03 session with 2026-11-02 latest bar -> MUST SKIP safely
    res_stale = run_daily_cycle(
        as_of_date=date(2026, 11, 3),
        override_history=stale_history,
        client=mock_client,
        ledger_path=test_ledger,
    )
    assert res_stale["status"] == "skipped_stale_or_missing_bar"
    assert res_stale["last_bar_date"] == "2026-11-02"
    assert not test_mtm_file.exists() or len(test_mtm_file.read_text(encoding="utf-8").strip()) == 0
    # Missed trading days incremented
    assert res_stale["missed_trading_days"] == 1

    # Case 2: Latest bar is now 2026-11-03 (fresh bar matching target session)
    fresh_history = {}
    for s in ["SPY", "IWM", "JNJ", "PG"]:
        bars = list(stale_history[s])
        prev_close = bars[-1]["c"]
        if s == "SPY":
            new_close = round(prev_close * 1.01, 2)  # +1.0%
        elif s == "IWM":
            new_close = round(prev_close * 1.01, 2)  # +1.0%
        else:
            new_close = round(prev_close * 1.005, 2)  # +0.5%
        bars.append({
            "t": "2026-11-03T04:00:00Z",
            "o": prev_close,
            "h": max(prev_close, new_close),
            "l": min(prev_close, new_close),
            "c": new_close,
            "v": 100000.0,
        })
        fresh_history[s] = bars

    # Run for 2026-11-03 session with matching 2026-11-03 bar -> MUST SUCCEED
    res_fresh = run_daily_cycle(
        as_of_date=date(2026, 11, 3),
        override_history=fresh_history,
        client=mock_client,
        ledger_path=test_ledger,
    )
    assert res_fresh["status"] == "success"

    # Verify: exactly 1 MTM row created
    mtm_lines = test_mtm_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(mtm_lines) == 1, "Expected exactly 1 MTM row after successful 11-03 run"

    rec = json.loads(mtm_lines[0])
    assert rec["date"] == "2026-11-03"
    assert rec["last_bar_date"] == "2026-11-03"

    # 10% daily return strictly reflects the 11-03 session
    assert rec["daily_return_10"] > 0.0060 and rec["daily_return_10"] < 0.0070
    assert rec["equity_10"] > 100600.0

    # 15% daily return strictly reflects the difference from initial_paper_equity (100,000 -> 100,350 = +0.35%)
    assert pytest.approx(rec["daily_return_15"], abs=1e-5) == 0.0035
    assert rec["equity_15"] == 100350.0

    # Case 3: Re-running 2026-11-03 is strictly idempotent (no row increment)
    res_rerun = run_daily_cycle(
        as_of_date=date(2026, 11, 3),
        override_history=fresh_history,
        client=mock_client,
        ledger_path=test_ledger,
    )
    assert res_rerun["status"] == "already_executed"
    mtm_lines_after = test_mtm_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(mtm_lines_after) == 1, "Idempotency violated: row count must remain 1"
