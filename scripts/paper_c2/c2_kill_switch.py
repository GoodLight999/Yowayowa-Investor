# ruff: noqa: E501
"""Kill-switch monitors for C2 paper operations (§7.2).

Implements the 3 explicit emergency conditions:
1. MAXDD_LIMIT_KILL (-15.00% reached): Equity <= peak * 0.85 -> Immediate FAIL, liquidate all.
2. SINGLE_DAY_DROP_KILL (-5.00% drop): Daily return < -0.0500 -> Trading halt, emergency inspect.
3. API_CONSECUTIVE_FAILURES (3 consecutive business days): Consecutive failures >= 3 -> INCONCLUSIVE abort.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

MAX_DD_THRESHOLD = -0.1500  # -15.00%
SINGLE_DAY_DROP_THRESHOLD = -0.0500  # -5.00%
CONSECUTIVE_API_FAILURE_THRESHOLD = 3


@dataclass(frozen=True)
class KillSwitchEvaluationResult:
    triggered: bool
    trigger_type: (
        str | None
    )  # "MAXDD_LIMIT_KILL", "SINGLE_DAY_DROP_KILL", "API_CONSECUTIVE_FAILURES", or None
    action: str  # "PROCEED", "LIQUIDATE_AND_FAIL", "HALT_AND_INSPECT", "INCONCLUSIVE_ABORT"
    verdict: str  # "OK", "FAIL", "HALT", "INCONCLUSIVE"
    details: str
    metrics: dict[str, Any]


def check_drawdown_kill(current_equity: float, peak_equity: float) -> tuple[bool, float, str]:
    """Check if portfolio drawdown reached or breached -15.00%."""
    if peak_equity <= 0:
        return False, 0.0, "Invalid peak equity <= 0"
    dd = current_equity / peak_equity - 1.0
    if dd <= MAX_DD_THRESHOLD:
        return (
            True,
            dd,
            f"MAXDD_LIMIT_KILL triggered: Drawdown reached {dd * 100:.2f}% (<= -15.00%). "
            f"Current: ${current_equity:.2f}, Peak: ${peak_equity:.2f}",
        )
    return False, dd, f"Drawdown {dd * 100:.2f}% within allowed limit (>-15.00%)"


def check_single_day_drop_kill(daily_return: float) -> tuple[bool, float, str]:
    """Check if single-day return dropped beyond -5.00%."""
    if daily_return < SINGLE_DAY_DROP_THRESHOLD:
        return (
            True,
            daily_return,
            f"SINGLE_DAY_DROP_KILL triggered: Daily return {daily_return * 100:.2f}% exceeded -5.00% threshold.",
        )
    return (
        False,
        daily_return,
        f"Daily return {daily_return * 100:.2f}% within allowed range (>= -5.00%)",
    )


def check_api_consecutive_failure_kill(consecutive_failures: int) -> tuple[bool, int, str]:
    """Check if API communication failure persisted for 3 consecutive business days."""
    if consecutive_failures >= CONSECUTIVE_API_FAILURE_THRESHOLD:
        return (
            True,
            consecutive_failures,
            f"API_CONSECUTIVE_FAILURES triggered: {consecutive_failures} consecutive business days of API failure (>= 3).",
        )
    return (
        False,
        consecutive_failures,
        f"API consecutive failures: {consecutive_failures} (< 3)",
    )


def evaluate_kill_switches(
    current_equity: float,
    peak_equity: float,
    daily_return: float | None = None,
    consecutive_api_failures: int = 0,
) -> KillSwitchEvaluationResult:
    """Evaluate all 3 emergency stop conditions."""
    # 1. API Consecutive Failures
    api_trig, fail_cnt, api_msg = check_api_consecutive_failure_kill(consecutive_api_failures)
    if api_trig:
        return KillSwitchEvaluationResult(
            triggered=True,
            trigger_type="API_CONSECUTIVE_FAILURES",
            action="INCONCLUSIVE_ABORT",
            verdict="INCONCLUSIVE",
            details=api_msg,
            metrics={"consecutive_api_failures": fail_cnt},
        )

    # 2. Drawdown Kill
    dd_trig, dd_val, dd_msg = check_drawdown_kill(current_equity, peak_equity)
    if dd_trig:
        return KillSwitchEvaluationResult(
            triggered=True,
            trigger_type="MAXDD_LIMIT_KILL",
            action="LIQUIDATE_AND_FAIL",
            verdict="FAIL",
            details=dd_msg,
            metrics={
                "drawdown": dd_val,
                "current_equity": current_equity,
                "peak_equity": peak_equity,
            },
        )

    # 3. Single Day Drop Kill
    if daily_return is not None:
        day_trig, ret_val, day_msg = check_single_day_drop_kill(daily_return)
        if day_trig:
            return KillSwitchEvaluationResult(
                triggered=True,
                trigger_type="SINGLE_DAY_DROP_KILL",
                action="HALT_AND_INSPECT",
                verdict="HALT",
                details=day_msg,
                metrics={"daily_return": ret_val},
            )

    return KillSwitchEvaluationResult(
        triggered=False,
        trigger_type=None,
        action="PROCEED",
        verdict="OK",
        details="All kill switches nominal.",
        metrics={
            "drawdown": dd_val,
            "daily_return": daily_return,
            "consecutive_api_failures": consecutive_api_failures,
        },
    )
