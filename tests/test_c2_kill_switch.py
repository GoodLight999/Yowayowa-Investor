"""Unit tests for C2 kill-switch monitors (§7.2 and Condition d).

Verifies the 3 emergency stop conditions:
1. MAXDD_LIMIT_KILL (-15.00% drawdown reached) -> Immediate FAIL, liquidate all.
2. SINGLE_DAY_DROP_KILL (-5.00% single day loss exceeded) -> Trading halt, emergency inspect.
3. API_CONSECUTIVE_FAILURES (3 consecutive business days of API outage) -> INCONCLUSIVE abort.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure scripts and src are on path
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

from scripts.paper_c2.c2_kill_switch import (  # noqa: E402
    check_api_consecutive_failure_kill,
    check_drawdown_kill,
    check_single_day_drop_kill,
    evaluate_kill_switches,
)


def test_kill_switch_maxdd_15pct_breach() -> None:
    """Condition 1: Portfolio equity drops to -15.00% or worse from peak."""
    peak = 100_000.0
    # Exactly -15.00%
    curr_15 = 85_000.0
    trig, dd, msg = check_drawdown_kill(curr_15, peak)
    assert trig is True
    assert pytest.approx(dd, abs=1e-6) == -0.1500
    assert "MAXDD_LIMIT_KILL" in msg

    # -15.01% breach
    curr_breach = 84_990.0
    trig2, dd2, msg2 = check_drawdown_kill(curr_breach, peak)
    assert trig2 is True
    assert dd2 < -0.1500
    assert "MAXDD_LIMIT_KILL" in msg2

    # Safe drawdown (-14.90%)
    curr_safe = 85_100.0
    trig_safe, dd_safe, _ = check_drawdown_kill(curr_safe, peak)
    assert trig_safe is False
    assert dd_safe > -0.1500

    # Full evaluation integration
    res = evaluate_kill_switches(current_equity=84_900.0, peak_equity=peak)
    assert res.triggered is True
    assert res.trigger_type == "MAXDD_LIMIT_KILL"
    assert res.action == "LIQUIDATE_AND_FAIL"
    assert res.verdict == "FAIL"


def test_kill_switch_single_day_drop_5pct_breach() -> None:
    """Condition 2: Daily loss strictly worse than -5.00% (5-sigma anomaly)."""
    # -5.01% breach
    ret_breach = -0.0501
    trig, val, msg = check_single_day_drop_kill(ret_breach)
    assert trig is True
    assert val == -0.0501
    assert "SINGLE_DAY_DROP_KILL" in msg

    # -5.50% severe drop
    ret_severe = -0.0550
    trig_sev, _, _ = check_single_day_drop_kill(ret_severe)
    assert trig_sev is True

    # Safe return (-4.90%)
    ret_safe = -0.0490
    trig_safe, _, _ = check_single_day_drop_kill(ret_safe)
    assert trig_safe is False

    # Positive return (+2.00%)
    trig_pos, _, _ = check_single_day_drop_kill(0.0200)
    assert trig_pos is False

    # Full evaluation integration
    res = evaluate_kill_switches(
        current_equity=100_000.0, peak_equity=100_000.0, daily_return=-0.0520
    )
    assert res.triggered is True
    assert res.trigger_type == "SINGLE_DAY_DROP_KILL"
    assert res.action == "HALT_AND_INSPECT"
    assert res.verdict == "HALT"


def test_kill_switch_consecutive_api_failures_3bd() -> None:
    """Condition 3: Alpaca API communication failure for 3 consecutive business days."""
    # 3 consecutive failures
    trig, count, msg = check_api_consecutive_failure_kill(3)
    assert trig is True
    assert count == 3
    assert "API_CONSECUTIVE_FAILURES" in msg

    # 4 consecutive failures
    trig4, count4, _ = check_api_consecutive_failure_kill(4)
    assert trig4 is True
    assert count4 == 4

    # 2 consecutive failures (not yet triggered)
    trig2, count2, _ = check_api_consecutive_failure_kill(2)
    assert trig2 is False
    assert count2 == 2

    # 0 failures (nominal)
    trig0, count0, _ = check_api_consecutive_failure_kill(0)
    assert trig0 is False
    assert count0 == 0

    # Full evaluation integration
    res = evaluate_kill_switches(
        current_equity=100_000.0,
        peak_equity=100_000.0,
        consecutive_api_failures=3,
    )
    assert res.triggered is True
    assert res.trigger_type == "API_CONSECUTIVE_FAILURES"
    assert res.action == "INCONCLUSIVE_ABORT"
    assert res.verdict == "INCONCLUSIVE"


def test_kill_switch_all_nominal() -> None:
    """Verify normal operation when all parameters are healthy."""
    res = evaluate_kill_switches(
        current_equity=98_000.0,
        peak_equity=100_000.0,
        daily_return=-0.0150,
        consecutive_api_failures=1,
    )
    assert res.triggered is False
    assert res.trigger_type is None
    assert res.action == "PROCEED"
    assert res.verdict == "OK"


def test_kill_switch_liquidation_and_halt_integration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify that when kill switch triggers, runner halts and calls close_all_positions."""
    from scripts.paper_c2 import c2_runner

    state_file = tmp_path / "state.json"
    mtm_file = tmp_path / "mtm_history.jsonl"
    monkeypatch.setattr(c2_runner, "STATE_FILE", state_file)
    monkeypatch.setattr(c2_runner, "MTM_FILE", mtm_file)
    # run_daily_cycle also writes attribution/orders via module-level constants that are
    # bound to the live data/paper_c2 dir at import time; redirect them or the test
    # overwrites production reports.
    monkeypatch.setattr(c2_runner, "ATTRIBUTION_JSON", tmp_path / "attribution_summary.json")
    monkeypatch.setattr(c2_runner, "ATTRIBUTION_MD", tmp_path / "attribution_summary.md")
    monkeypatch.setattr(c2_runner, "ORDERS_FILE", tmp_path / "orders.jsonl")

    initial_state = {
        "peak_equity_10": 100000.0,
        "peak_equity_15": 100000.0,
        "current_equity_10": 100000.0,
        "current_equity_15": 84000.0,  # -16.0% DD -> triggers MAXDD_LIMIT_KILL
        "alpaca_paper_equity": 84000.0,
        "current_weights_10": {"USD": 1.0, "SPY": 0.0, "IWM": 0.0, "JNJ": 0.0, "PG": 0.0},
        "current_weights_15": {"USD": 0.0, "SPY": 0.25, "IWM": 0.25, "JNJ": 0.25, "PG": 0.25},
        "consecutive_api_failures": 0,
        "last_decision_date": None,
        "last_mtm_date": None,
        "last_mtm_bar_date": None,
        "kill_switch_triggered": False,
        "kill_switch_event": None,
        "missed_trading_days": 0,
        "missed_rebalance_days": 0,
        "completed_trading_days": [],
    }
    c2_runner.save_state(initial_state)

    closed_positions = []
    cancelled_orders = []

    class MockAlpacaClient:
        def get_account(self):
            class Acct:
                equity = 84000.0
            return Acct()

        def cancel_all_orders(self):
            cancelled_orders.append(True)
            return []

        def close_all_positions(self, cancel_orders=True):
            closed_positions.append(True)
            return [{"status": "closed_all"}]

    monkeypatch.setattr(c2_runner, "AlpacaPaperClient", MockAlpacaClient)

    # Run daily cycle on 2026-11-03 with 2026-11-03 bar in history.
    # Bootstrap history comes from the frozen synthetic fixture, never from the live
    # data/stock-ohlcv store: the live store advances daily, so reading it made the
    # "SPY has >= 22 bars / latest bar is 2026-10-30" premise depend on retention.
    from datetime import date

    from tests.c2_synthetic_history import detached_copy, synthetic_base_history

    base_hist = synthetic_base_history()
    # Keep the default history source isolated too: if run_daily_cycle ever ignored the
    # explicit override it would otherwise fall back to the production store.
    monkeypatch.setattr(c2_runner, "load_local_history", lambda: detached_copy(base_hist))

    mock_hist = {}
    for s, bars in base_hist.items():
        b_list = list(bars)
        b_list.append({"t": "2026-11-03T04:00:00Z", "o": 100.0, "h": 100.0, "l": 100.0, "c": 100.0, "v": 1000.0})
        mock_hist[s] = b_list

    # The ledger lives in data/paper_c2 by default; point it at tmp_path so the run opens
    # no production file (attribution joins the ledger passed here).
    d_res = c2_runner.run_daily_cycle(
        as_of_date=date(2026, 11, 3),
        ledger_path=tmp_path / "decisions.jsonl",
        override_history=mock_hist,
    )
    assert d_res["kill_switch_eval"].triggered is True
    assert len(cancelled_orders) == 1
    assert len(closed_positions) == 1

    # Verify state updated with kill switch trigger
    st = c2_runner.load_state()
    assert st["kill_switch_triggered"] is True
    assert st["kill_switch_event"]["trigger_type"] == "MAXDD_LIMIT_KILL"

    # Subsequent run must be immediately halted
    halted_res = c2_runner.run_weekly_cycle(as_of_date=date(2026, 11, 9))
    assert halted_res["status"] == "halted_by_kill_switch"

