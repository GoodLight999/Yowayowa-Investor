# ruff: noqa: E501, RUF001
"""Unit tests for C2 Gate Evaluator (§7.1, §7.2, §8, §5.2).

Validates:
1. Synthetic 3 cases: PASS, FAIL, INCONCLUSIVE
2. Isolated failure for each of the 7 criteria (proves boolean AND aggregation)
3. Governance tamper detection (design doc SHA256 mismatch -> INCONCLUSIVE)
4. Score function comparison (§5.2)
5. Idempotent JSON output
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from scripts.paper_c2.c2_gate import (
    INPUT_MISSING_EXIT_CODE,
    INPUT_MISSING_VERDICT,
    MAX_DD_THRESHOLD,
    SHARPE_THRESHOLD,
    compute_forward_score,
    evaluate_c2_gate,
    evaluate_single_series,
)


# ==============================================================================
# FIXTURES & HELPERS
# ==============================================================================
@pytest.fixture
def base_returns_pass() -> list[float]:
    """Generates a steady positive-return series of 60 trading days (Sharpe ~ 1.5, MaxDD ~ -2%, PF > 1.5)."""
    # Alternating small gains with occasional minor pullbacks
    pattern = [0.003, 0.002, -0.001, 0.004, 0.001, -0.001, 0.002, 0.005, -0.002, 0.003]
    return (pattern * 6)[:60]


@pytest.fixture
def base_equity_pass(base_returns_pass: list[float]) -> list[float]:
    eq = [100000.0]
    for r in base_returns_pass:
        eq.append(eq[-1] * (1.0 + r))
    return eq


@pytest.fixture
def base_attrib_pass() -> dict[str, Any]:
    return {
        "sharpe_rule": 0.85,
        "sharpe_llm_hyb": 0.90,  # +0.05 vs rule (exceeds rule - 0.10)
        "overwrite_win_rate_pct": 60.0,  # >= 50.0%
        "overwrite_ratio_pct": 15.0,  # <= 30.0%
        "overwrite_count": 9,
    }


# Mandatory "5-input set" fixed by t_313bbfbb. The gate is fail-closed on these,
# so every evaluate_c2_gate() test must materialise the whole set.
MINIMAL_MTM_ROW: dict[str, Any] = {
    "date": "2026-11-03",
    "daily_return_10": 0.0,
    "daily_return_15": 0.0,
    "equity_10": 100000.0,
    "equity_15": 100000.0,
}
MINIMAL_ATTRIBUTION: dict[str, Any] = {"matrix_rows": [], "overwrite_assessment": {}}


def write_required_inputs(data_dir: Path, state: dict[str, Any] | None = None) -> Path:
    """Write the mandatory input set (state/mtm/decisions/orders/attribution)."""
    data_dir.mkdir(parents=True, exist_ok=True)
    if state is not None:
        (data_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (data_dir / "mtm_history.jsonl").write_text(
        json.dumps(MINIMAL_MTM_ROW) + "\n", encoding="utf-8"
    )
    for name in ("decisions.jsonl", "orders.jsonl"):
        (data_dir / name).write_text("{}\n", encoding="utf-8")
    (data_dir / "attribution_summary.json").write_text(
        json.dumps(MINIMAL_ATTRIBUTION), encoding="utf-8"
    )
    return data_dir


# ==============================================================================
# TEST 1: PASS CASE (§8.1)
# ==============================================================================
def test_gate_pass_both_series(
    tmp_path: Path,
    base_returns_pass: list[float],
    base_equity_pass: list[float],
    base_attrib_pass: dict[str, Any],
) -> None:
    """Both 10% and 15% pass all 7 criteria; forward score determines candidate (§5.2)."""
    # Create empty mock data directory
    data_dir = tmp_path / "paper_c2"
    data_dir.mkdir(parents=True)
    state = {
        "completed_trading_days": [f"2026-11-{i:02d}" for i in range(1, 61)],
        "missed_trading_days": 1,
        "missed_rebalance_days": 0,
        "consecutive_api_failures": 0,
        "kill_switch_triggered": False,
    }
    write_required_inputs(data_dir, state)

    # Evaluate with 60 days
    verdict = evaluate_c2_gate(
        data_dir=data_dir,
        override_completed_days=60,
        mock_returns_10=base_returns_pass,
        mock_returns_15=base_returns_pass,
        mock_equity_10=base_equity_pass,
        mock_equity_15=base_equity_pass,
        mock_turnover_10=3.40,
        mock_turnover_15=2.03,  # Lower turnover gives higher forward score
        mock_attribution_stats=base_attrib_pass,
    )

    assert verdict.verdict == "PASS"
    assert verdict.judged_business_days == 60
    assert verdict.recommended_candidate == "vol_target_15"
    assert "vol_target_10" in verdict.evaluations
    assert "vol_target_15" in verdict.evaluations
    assert verdict.evaluations["vol_target_10"].all_passed is True
    assert verdict.evaluations["vol_target_15"].all_passed is True


# ==============================================================================
# TEST 2: FAIL CASES - PROVING INDIVIDUAL AND ISOLATION (§7.1)
# ==============================================================================
def test_criteria_1_sharpe_fail(
    base_equity_pass: list[float], base_attrib_pass: dict[str, Any]
) -> None:
    """Criterion ①: Sharpe < 0.50 fails."""
    # A flat/choppy return series with zero drift -> Sharpe ~ 0.0 < 0.50
    choppy_returns = [0.002, -0.002] * 30
    res = evaluate_single_series(
        series_name="test_15",
        daily_returns=choppy_returns,
        equity_series=base_equity_pass,
        attribution_stats=base_attrib_pass,
        missed_trading_days=0,
        missed_rebalance_days=0,
        kill_switch_triggered_count=0,
        completed_days_count=60,
        turnover=2.0,
    )
    assert res.criteria[0].passed is False
    assert res.all_passed is False
    assert res.sharpe < SHARPE_THRESHOLD


def test_criteria_2_max_dd_fail(
    base_returns_pass: list[float], base_attrib_pass: dict[str, Any]
) -> None:
    """Criterion ②: MaxDD < -15.00% (e.g. -18%) fails."""
    # Start 100k, crash to 80k (-20%), then recover
    crashing_equity = [100000.0] * 10 + [80000.0] * 10 + [105000.0] * 41
    res = evaluate_single_series(
        series_name="test_15",
        daily_returns=base_returns_pass,
        equity_series=crashing_equity,
        attribution_stats=base_attrib_pass,
        missed_trading_days=0,
        missed_rebalance_days=0,
        kill_switch_triggered_count=0,
        completed_days_count=60,
        turnover=2.0,
    )
    assert res.criteria[1].passed is False
    assert res.all_passed is False
    assert res.max_dd < MAX_DD_THRESHOLD


def test_criteria_3_profit_factor_fail(
    base_equity_pass: list[float], base_attrib_pass: dict[str, Any]
) -> None:
    """Criterion ③: Profit Factor < 1.05 fails."""
    # Returns with gains = 1.0, losses = 1.0 -> PF = 1.00 < 1.05
    flat_returns = [0.001, -0.001] * 30
    res = evaluate_single_series(
        series_name="test_15",
        daily_returns=flat_returns,
        equity_series=base_equity_pass,
        attribution_stats=base_attrib_pass,
        missed_trading_days=0,
        missed_rebalance_days=0,
        kill_switch_triggered_count=0,
        completed_days_count=60,
        turnover=2.0,
    )
    assert res.criteria[2].passed is False
    assert res.all_passed is False


def test_criteria_4_attribution_degradation_fail(
    base_returns_pass: list[float], base_equity_pass: list[float], base_attrib_pass: dict[str, Any]
) -> None:
    """Criterion ④: LLM attribution degradation fails when Sharpe_LLM < Sharpe_RULE - 0.10 or WinRate < 50%."""
    # 4a: Sharpe degraded by 0.20
    attrib_degraded = dict(base_attrib_pass)
    attrib_degraded["sharpe_rule"] = 1.00
    attrib_degraded["sharpe_llm_hyb"] = 0.80  # diff = -0.20 < -0.10
    res_a = evaluate_single_series(
        series_name="test_15",
        daily_returns=base_returns_pass,
        equity_series=base_equity_pass,
        attribution_stats=attrib_degraded,
        missed_trading_days=0,
        missed_rebalance_days=0,
        kill_switch_triggered_count=0,
        completed_days_count=60,
        turnover=2.0,
    )
    assert res_a.criteria[3].passed is False
    assert res_a.all_passed is False

    # 4b: Win rate < 50%
    attrib_low_win = dict(base_attrib_pass)
    attrib_low_win["overwrite_win_rate_pct"] = 40.0  # < 50%
    res_b = evaluate_single_series(
        series_name="test_15",
        daily_returns=base_returns_pass,
        equity_series=base_equity_pass,
        attribution_stats=attrib_low_win,
        missed_trading_days=0,
        missed_rebalance_days=0,
        kill_switch_triggered_count=0,
        completed_days_count=60,
        turnover=2.0,
    )
    assert res_b.criteria[3].passed is False
    assert res_b.all_passed is False


def test_criteria_5_overwrite_cap_fail(
    base_returns_pass: list[float], base_equity_pass: list[float], base_attrib_pass: dict[str, Any]
) -> None:
    """Criterion ⑤: LLM Overwrite Cap > 30.0% fails."""
    attrib_high_cap = dict(base_attrib_pass)
    attrib_high_cap["overwrite_ratio_pct"] = 35.0  # > 30.0%
    res = evaluate_single_series(
        series_name="test_15",
        daily_returns=base_returns_pass,
        equity_series=base_equity_pass,
        attribution_stats=attrib_high_cap,
        missed_trading_days=0,
        missed_rebalance_days=0,
        kill_switch_triggered_count=0,
        completed_days_count=60,
        turnover=2.0,
    )
    assert res.criteria[4].passed is False
    assert res.all_passed is False


def test_criteria_6_missed_days_fail(
    base_returns_pass: list[float], base_equity_pass: list[float], base_attrib_pass: dict[str, Any]
) -> None:
    """Criterion ⑥: Missed trading days > 3 fails."""
    res = evaluate_single_series(
        series_name="test_15",
        daily_returns=base_returns_pass,
        equity_series=base_equity_pass,
        attribution_stats=base_attrib_pass,
        missed_trading_days=4,  # > 3 days
        missed_rebalance_days=0,
        kill_switch_triggered_count=0,
        completed_days_count=60,
        turnover=2.0,
    )
    assert res.criteria[5].passed is False
    assert res.all_passed is False


def test_criteria_7_kill_switch_fail(
    base_returns_pass: list[float], base_equity_pass: list[float], base_attrib_pass: dict[str, Any]
) -> None:
    """Criterion ⑦: Kill switch invocations > 0 fails."""
    res = evaluate_single_series(
        series_name="test_15",
        daily_returns=base_returns_pass,
        equity_series=base_equity_pass,
        attribution_stats=base_attrib_pass,
        missed_trading_days=0,
        missed_rebalance_days=0,
        kill_switch_triggered_count=1,  # > 0
        completed_days_count=60,
        turnover=2.0,
    )
    assert res.criteria[6].passed is False
    assert res.all_passed is False


def test_both_series_fail_gives_overall_fail(
    tmp_path: Path,
    base_attrib_pass: dict[str, Any],
) -> None:
    """When both 10% and 15% fail, overall verdict is FAIL and recommendation is None."""
    data_dir = tmp_path / "paper_c2"
    data_dir.mkdir(parents=True)
    state = {
        "completed_trading_days": [f"2026-11-{i:02d}" for i in range(1, 61)],
        "missed_trading_days": 0,
        "missed_rebalance_days": 0,
        "consecutive_api_failures": 0,
        "kill_switch_triggered": False,
    }
    write_required_inputs(data_dir, state)

    # Low Sharpe returns
    bad_returns = [0.001, -0.001] * 30
    bad_equity = [100000.0] * 61

    verdict = evaluate_c2_gate(
        data_dir=data_dir,
        override_completed_days=60,
        mock_returns_10=bad_returns,
        mock_returns_15=bad_returns,
        mock_equity_10=bad_equity,
        mock_equity_15=bad_equity,
        mock_attribution_stats=base_attrib_pass,
    )
    assert verdict.verdict == "FAIL"
    assert verdict.recommended_candidate is None


# ==============================================================================
# TEST 3: INCONCLUSIVE CASES (§8.3)
# ==============================================================================
def test_inconclusive_unmatured_period(tmp_path: Path) -> None:
    """Days < 60 must return INCONCLUSIVE (期間未達) without pre-mature pass/fail."""
    data_dir = tmp_path / "paper_c2"
    data_dir.mkdir(parents=True)
    state = {
        "completed_trading_days": ["2026-11-03", "2026-11-04"],
        "missed_trading_days": 0,
        "missed_rebalance_days": 0,
        "consecutive_api_failures": 0,
    }
    write_required_inputs(data_dir, state)

    verdict = evaluate_c2_gate(data_dir=data_dir, override_completed_days=2)
    assert verdict.verdict == "INCONCLUSIVE"
    assert "期間未達" in verdict.reason
    assert verdict.judged_business_days == 2
    assert verdict.target_business_days == 60


def test_inconclusive_consecutive_missed_days(tmp_path: Path) -> None:
    """Consecutive missed days >= 3 aborts as INCONCLUSIVE (§8.3 item 1)."""
    data_dir = tmp_path / "paper_c2"
    data_dir.mkdir(parents=True)
    state = {
        "completed_trading_days": ["2026-11-03"],
        "missed_trading_days": 3,
        "missed_rebalance_days": 0,
        "consecutive_api_failures": 3,
    }
    write_required_inputs(data_dir, state)

    verdict = evaluate_c2_gate(data_dir=data_dir)
    assert verdict.verdict == "INCONCLUSIVE"
    assert "連続欠測営業日超過" in verdict.reason


def test_inconclusive_cumulative_missed_days(tmp_path: Path) -> None:
    """Cumulative missed days >= 4 aborts as INCONCLUSIVE (§8.3 item 1)."""
    data_dir = tmp_path / "paper_c2"
    data_dir.mkdir(parents=True)
    state = {
        "completed_trading_days": ["2026-11-03"],
        "missed_trading_days": 4,
        "missed_rebalance_days": 0,
        "consecutive_api_failures": 1,
    }
    write_required_inputs(data_dir, state)

    verdict = evaluate_c2_gate(data_dir=data_dir)
    assert verdict.verdict == "INCONCLUSIVE"
    assert "累計欠測営業日超過" in verdict.reason


def test_inconclusive_missed_rebalance_days(tmp_path: Path) -> None:
    """Missed rebalance days >= 2 aborts as INCONCLUSIVE (§8.3 item 2)."""
    data_dir = tmp_path / "paper_c2"
    data_dir.mkdir(parents=True)
    state = {
        "completed_trading_days": ["2026-11-03"],
        "missed_trading_days": 2,
        "missed_rebalance_days": 2,
        "consecutive_api_failures": 1,
    }
    write_required_inputs(data_dir, state)

    verdict = evaluate_c2_gate(data_dir=data_dir)
    assert verdict.verdict == "INCONCLUSIVE"
    assert "リバランス日欠測超過" in verdict.reason


def test_inconclusive_design_doc_hash_mismatch(tmp_path: Path) -> None:
    """Tampered design doc must immediately trigger INCONCLUSIVE abort (Governance requirement)."""
    data_dir = tmp_path / "paper_c2"
    data_dir.mkdir(parents=True)
    fake_doc = tmp_path / "fake_design.md"
    fake_doc.write_text("tampered content", encoding="utf-8")

    verdict = evaluate_c2_gate(data_dir=data_dir, design_doc_path=fake_doc)
    assert verdict.verdict == "INCONCLUSIVE"
    assert "設計書 SHA256 不一致" in verdict.reason


# ==============================================================================
# TEST 3b: FAIL-CLOSED ON MISSING / UNREADABLE MANDATORY INPUTS (t_dbf3fb69)
# ==============================================================================
STATE_4_DAYS: dict[str, Any] = {
    "completed_trading_days": ["2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06"],
    "missed_trading_days": 0,
    "missed_rebalance_days": 0,
    "consecutive_api_failures": 0,
    "kill_switch_triggered": False,
}


def test_input_missing_when_all_five_inputs_absent(tmp_path: Path) -> None:
    """① All five data inputs absent -> INPUT_MISSING, never '0 business days'."""
    data_dir = tmp_path / "paper_c2"
    data_dir.mkdir(parents=True)

    verdict = evaluate_c2_gate(data_dir=data_dir)

    assert verdict.verdict == INPUT_MISSING_VERDICT == "INPUT_MISSING"
    assert verdict.verdict != "INCONCLUSIVE"  # not a period/abort inconclusive
    assert "必須入力の欠損" in verdict.reason
    assert "期間未達（判定営業日" not in verdict.reason  # the old fail-open wording
    assert len(verdict.missing_inputs) >= 5
    for name in ("state.json", "mtm_history.jsonl", "decisions.jsonl", "orders.jsonl",
                 "attribution_summary.json"):
        assert any(name in item for item in verdict.missing_inputs), name
    payload = verdict.to_dict()
    assert payload["verdict"] == "INPUT_MISSING"
    assert payload["missing_inputs"] == list(verdict.missing_inputs)


def test_input_missing_when_state_json_only_absent(tmp_path: Path) -> None:
    """② Only state.json absent -> INPUT_MISSING (the exact t_313bbfbb fail-open case)."""
    data_dir = write_required_inputs(tmp_path / "paper_c2", STATE_4_DAYS)
    (data_dir / "state.json").unlink()

    verdict = evaluate_c2_gate(data_dir=data_dir)

    assert verdict.verdict == INPUT_MISSING_VERDICT
    assert any("state.json" in item for item in verdict.missing_inputs)
    assert not any(
        other in item
        for item in verdict.missing_inputs
        for other in ("mtm_history.jsonl", "decisions.jsonl", "orders.jsonl",
                      "attribution_summary.json")
    )
    assert "0 営業日目" not in verdict.reason
    assert verdict.judged_business_days == 0


def test_unmatured_period_still_reports_4_of_60(tmp_path: Path) -> None:
    """③ Normal case (all five inputs) keeps the existing '4/60' behaviour."""
    data_dir = write_required_inputs(tmp_path / "paper_c2", STATE_4_DAYS)

    verdict = evaluate_c2_gate(data_dir=data_dir)

    assert verdict.verdict == "INCONCLUSIVE"
    assert verdict.reason == "期間未達（判定営業日 60 未達、現在 4 営業日目）"
    assert verdict.judged_business_days == 4
    assert verdict.target_business_days == 60
    assert verdict.missing_inputs == ()


def test_input_missing_when_design_doc_absent(tmp_path: Path) -> None:
    """Design doc absence is INPUT_MISSING (not a silent pass, not 期間未達)."""
    data_dir = write_required_inputs(tmp_path / "paper_c2", STATE_4_DAYS)

    verdict = evaluate_c2_gate(
        data_dir=data_dir, design_doc_path=tmp_path / "missing_design.md"
    )

    assert verdict.verdict == INPUT_MISSING_VERDICT
    assert any("設計書" in item for item in verdict.missing_inputs)


def test_input_missing_when_state_lacks_completed_days_key(tmp_path: Path) -> None:
    """A state.json without completed_trading_days must not be read as day 0."""
    data_dir = write_required_inputs(tmp_path / "paper_c2")
    (data_dir / "state.json").write_text(
        json.dumps({"missed_trading_days": 0}), encoding="utf-8"
    )

    verdict = evaluate_c2_gate(data_dir=data_dir)

    assert verdict.verdict == INPUT_MISSING_VERDICT
    assert any("completed_trading_days" in item for item in verdict.missing_inputs)


def test_cli_exits_nonzero_on_input_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLI (normal and cron mode) must fail closed with a non-zero exit code."""
    from scripts.paper_c2 import c2_gate as gate_module

    data_dir = tmp_path / "paper_c2"
    data_dir.mkdir(parents=True)
    base_argv = ["c2_gate", "--data-dir", str(data_dir), "--dry-run"]

    monkeypatch.setattr(sys, "argv", base_argv)
    assert gate_module.main() == INPUT_MISSING_EXIT_CODE
    assert INPUT_MISSING_EXIT_CODE != 0

    # cron-mode must not silence a broken pipeline
    monkeypatch.setattr(sys, "argv", [*base_argv, "--cron-mode"])
    assert gate_module.main() == INPUT_MISSING_EXIT_CODE


# ==============================================================================
# TEST 4: SCORE FUNCTION ACCURACY (§5.2)
# ==============================================================================
def test_forward_score_calculation() -> None:
    """Validates §5.2 score formula against manual calculation:
    Score = 0.40*(Sharpe/0.80) + 0.30*(1 - |MaxDD|/0.15) + 0.30*(CAGR/0.08) - 0.10*(Turnover/3.00)
    """
    sharpe = 0.80
    max_dd = -0.15
    cagr = 0.08
    turnover = 3.00

    calc = compute_forward_score(sharpe, max_dd, cagr, turnover)
    assert pytest.approx(calc, abs=1e-6) == 0.60
