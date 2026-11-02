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
