# ruff: noqa: RUF001, E501
"""C2 Paper Phase Gate Evaluator (c2_gate.py).

Implements the deterministic, discretionary-free mechanical evaluation for C2 forward
paper operations at the 60-business-day milestone (§6, §7.1, §7.2, §8, §5.2).

Original Design Document Reference:
  Path:   docs/G2_PAPER_PHASE_DESIGN_20261102.md
  SHA256: be036bbf3b8a7b393ba19fd5288724e0c0c9798fd652aa3d97cf65f6b8d3744b
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import sys
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from statistics import fmean, stdev
from typing import Any

from yowayowa.trading_sessions import is_session_day

# ==============================================================================
# 1. FROZEN SPECIFICATION CONSTANTS (§7.1, §7.2, §8, §5.2, §6.1, §6.2)
# Any runtime modification invalidates gate governance.
# ==============================================================================
DESIGN_DOC_PATH = Path("docs/G2_PAPER_PHASE_DESIGN_20261102.md")
EXPECTED_DESIGN_DOC_SHA256 = (
    "be036bbf3b8a7b393ba19fd5288724e0c0c9798fd652aa3d97cf65f6b8d3744b"
)

# §6.1 Period Definitions
TARGET_BUSINESS_DAYS: int = 60
PHASE_START_DATE: date = date(2026, 11, 3)
PROJECTED_FINAL_SESSION_DATE: date = date(2027, 1, 29)
PROJECTED_JUDGEMENT_DATE: date = date(2027, 2, 1)

# Mandatory Input Contract (fail-closed, t_dbf3fb69)
# The gate must never report a missing/unreadable mandatory input as "0 business days
# (期間未達)". Absence or read failure yields an explicit INPUT_MISSING verdict, a
# non-zero process exit, and the list of missing files in the output.
INPUT_MISSING_VERDICT: str = "INPUT_MISSING"
INPUT_MISSING_EXIT_CODE: int = 3

# The "5 input set" fixed by t_313bbfbb (all machine-generated under data/paper_c2).
REQUIRED_DATA_INPUTS: tuple[tuple[str, str], ...] = (
    ("state.json", "運用状態（営業日数・欠測・キルスイッチ）"),
    ("mtm_history.jsonl", "日次MTM系列（Sharpe/MaxDD/CAGR/PF）"),
    ("decisions.jsonl", "判断ログ（上書き率・ハッシュ鎖）"),
    ("orders.jsonl", "発注ログ（約定・リバランス欠測）"),
    ("attribution_summary.json", "帰属集計（判定④⑤）"),
)
# Required keys per structural input (missing keys would otherwise silently default
# to values that can pass criteria, e.g. attribution defaults to 100% win rate).
REQUIRED_STATE_KEYS: tuple[str, ...] = ("completed_trading_days",)
REQUIRED_ATTRIBUTION_KEYS: tuple[str, ...] = ("overwrite_assessment", "matrix_rows")
REQUIRED_MTM_ROW_KEYS: tuple[str, ...] = (
    "daily_return_10",
    "daily_return_15",
    "equity_10",
    "equity_15",
)
TRADING_CALENDAR_LABEL: str = "営業日カレンダー (yowayowa/trading_sessions.py)"

# §7.1 Thresholds (The 7 Mandatory Criteria)
# ① Forward Sharpe >= 0.50
SHARPE_THRESHOLD: float = 0.50

# ② Forward MaxDD >= -15.00% (Drawdown <= 15.00%)
MAX_DD_THRESHOLD: float = -0.1500

# ③ Forward Profit Factor >= 1.05
PROFIT_FACTOR_THRESHOLD: float = 1.05

# ④ Attribution Non-Degradation:
#    Sharpe_LLM/HYB >= Sharpe_RULE - 0.10 AND Overwrite Win Rate >= 50.0%
SHARPE_DEGRADATION_MARGIN: float = 0.10
OVERWRITE_WIN_RATE_THRESHOLD: float = 50.0

# ⑤ LLM Overwrite Cap <= 30.0% of all decisions
OVERWRITE_RATIO_CAP_PCT: float = 30.0

# ⑥ Infrastructure Completion:
#    Missed trading days <= 3 (<= 5.0%) AND Missed rebalance days <= 1
MAX_MISSED_TRADING_DAYS: int = 3
MAX_MISSED_RATIO: float = 0.05
MAX_MISSED_REBALANCE_DAYS: int = 1

# ⑦ Kill Switch Invocations == 0
MAX_KILL_SWITCH_EVENTS: int = 0

# §8.3 Inconclusive Abort Thresholds
ABORT_CONSECUTIVE_MISSED_DAYS: int = 3  # >2 consecutive days
ABORT_MISSED_TRADING_DAYS: int = 4  # >= 4 cumulative days
ABORT_MISSED_REBALANCE_DAYS: int = 2  # >= 2 rebalance days

# §5.2 Forward Score Function Weights and Normalizers
# Score = 0.40*(Sharpe/0.80) + 0.30*(1 - |MaxDD|/0.15) + 0.30*(CAGR/0.08) - 0.10*(Turnover/3.00)
SCORE_WEIGHT_SHARPE: float = 0.40
SCORE_NORM_SHARPE: float = 0.80

SCORE_WEIGHT_MAXDD: float = 0.30
SCORE_NORM_MAXDD: float = 0.15

SCORE_WEIGHT_CAGR: float = 0.30
SCORE_NORM_CAGR: float = 0.08

SCORE_WEIGHT_TURNOVER: float = -0.10
SCORE_NORM_TURNOVER: float = 3.00

ANNUALIZATION_FACTOR_DAILY = math.sqrt(252)


# ==============================================================================
# 2. DATA STRUCTURES
# ==============================================================================
@dataclass(frozen=True)
class GateCriterionResult:
    item_number: int
    name: str
    threshold_str: str
    actual_value: float | int | str | None
    actual_str: str
    passed: bool
    details: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SeriesGateEvaluation:
    series_name: str  # "vol_target_10" or "vol_target_15"
    criteria: list[GateCriterionResult]
    all_passed: bool
    sharpe: float
    max_dd: float
    cagr: float
    turnover: float
    forward_score: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "series_name": self.series_name,
            "all_passed": self.all_passed,
            "metrics": {
                "sharpe": round(self.sharpe, 4),
                "max_dd": round(self.max_dd, 4),
                "cagr": round(self.cagr, 4),
                "turnover": round(self.turnover, 4),
                "forward_score": round(self.forward_score, 4),
            },
            "criteria": [c.to_dict() for c in self.criteria],
        }


@dataclass
class GateVerdict:
    verdict: str  # "PASS", "FAIL", "INCONCLUSIVE"
    reason: str
    design_doc_sha256: str
    design_doc_valid: bool
    judged_business_days: int
    target_business_days: int
    start_date: str
    projected_final_date: str
    projected_judgement_date: str
    actual_judgement_date: str | None
    missed_trading_days: int
    missed_rebalance_days: int
    consecutive_missed_days: int
    recommended_candidate: str | None
    evaluations: dict[str, SeriesGateEvaluation]
    input_files_sha256: dict[str, str]
    # Only populated for INPUT_MISSING (fail-closed); kept empty otherwise so the
    # normal-path JSON stays byte-identical to the pre-change output.
    missing_inputs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "verdict": self.verdict,
            "reason": self.reason,
            "design_doc": {
                "path": str(DESIGN_DOC_PATH),
                "sha256": self.design_doc_sha256,
                "verified": self.design_doc_valid,
            },
            "period": {
                "target_business_days": self.target_business_days,
                "judged_business_days": self.judged_business_days,
                "start_date": self.start_date,
                "projected_final_date": self.projected_final_date,
                "projected_judgement_date": self.projected_judgement_date,
                "actual_judgement_date": self.actual_judgement_date,
                "missed_trading_days": self.missed_trading_days,
                "missed_rebalance_days": self.missed_rebalance_days,
                "consecutive_missed_days": self.consecutive_missed_days,
            },
            "recommended_candidate": self.recommended_candidate,
            "series_evaluations": {
                k: v.to_dict() for k, v in self.evaluations.items()
            },
            "input_files_sha256": self.input_files_sha256,
        }
        if self.missing_inputs:
            out["missing_inputs"] = list(self.missing_inputs)
        return out


# ==============================================================================
# 3. METRIC CALCULATION HELPERS
# ==============================================================================
def file_sha256(path: Path) -> str:
    """Compute hex sha256 of file, or empty string if not found."""
    if not path.is_file():
        return ""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def verify_design_doc(doc_path: Path = DESIGN_DOC_PATH) -> tuple[bool, str]:
    """Verify that the design doc exists and its SHA256 matches the frozen value."""
    actual_sha = file_sha256(doc_path)
    if not actual_sha:
        return False, "File not found"
    return (actual_sha == EXPECTED_DESIGN_DOC_SHA256), actual_sha


def check_required_inputs(data_dir: Path) -> list[str]:
    """Detect missing, unreadable or structurally-invalid mandatory data inputs.

    Fail-closed contract (t_dbf3fb69): an absent or unreadable input is NOT
    "0 business days". Every problem found is returned as a human-readable string
    (empty list = all mandatory inputs usable).
    """
    problems: list[str] = []
    for name, description in REQUIRED_DATA_INPUTS:
        path = data_dir / name
        if not path.is_file():
            problems.append(f"{name}（不在: {description}）")
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            problems.append(f"{name}（読取失敗: {type(exc).__name__}）")
            continue

        if name.endswith(".jsonl"):
            rows = [line for line in text.splitlines() if line.strip()]
            if not rows:
                problems.append(f"{name}（データ行なし）")
                continue
            for lineno, line in enumerate(rows, 1):
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    problems.append(f"{name}（JSON解析失敗: {lineno}行目）")
                    break
                if not isinstance(row, dict):
                    problems.append(f"{name}（{lineno}行目がJSONオブジェクトでない）")
                    break
                if name == "mtm_history.jsonl":
                    absent = [k for k in REQUIRED_MTM_ROW_KEYS if k not in row]
                    if absent:
                        problems.append(
                            f"{name}（{lineno}行目に必須キー欠落: {', '.join(absent)}）"
                        )
                        break
            continue

        # .json inputs
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            problems.append(f"{name}（JSON解析失敗: {exc.lineno}行目）")
            continue
        if not isinstance(payload, dict):
            problems.append(f"{name}（JSONオブジェクトでない）")
            continue
        if name == "state.json":
            if not isinstance(payload.get("completed_trading_days"), list):
                problems.append(
                    "state.json（completed_trading_days 欠落またはlist以外 — "
                    "営業日数を0と誤読するため判定不能）"
                )
        elif name == "attribution_summary.json":
            for key in REQUIRED_ATTRIBUTION_KEYS:
                if key not in payload:
                    problems.append(f"attribution_summary.json（必須キー欠落: {key}）")
    return problems


def check_trading_calendar() -> list[str]:
    """Verify the trading-calendar input (git-tracked module) is present and usable."""
    module = sys.modules.get("yowayowa.trading_sessions")
    module_path = Path(getattr(module, "__file__", "") or "")
    if module is None or not module_path.is_file():
        return [f"{TRADING_CALENDAR_LABEL}（不在）"]
    try:
        if not isinstance(is_session_day(PHASE_START_DATE), bool):
            return [f"{TRADING_CALENDAR_LABEL}（判定不能: is_session_day がbool以外を返す）"]
        is_session_day(PROJECTED_FINAL_SESSION_DATE)
    except Exception as exc:  # any failure means the calendar is unusable
        return [f"{TRADING_CALENDAR_LABEL}（評価失敗: {type(exc).__name__}: {exc}）"]
    return []


def build_input_missing_verdict(
    missing_inputs: list[str],
    design_doc_sha256: str,
    design_doc_valid: bool,
    input_files_sha256: dict[str, str],
) -> GateVerdict:
    """Explicit fail-closed verdict: mandatory input absent/unreadable.

    Deliberately worded so it can never be read as "period not yet reached (0/60)".
    """
    listed = "; ".join(missing_inputs)
    return GateVerdict(
        verdict=INPUT_MISSING_VERDICT,
        reason=(
            "必須入力の欠損または読取失敗により判定不能（INPUT_MISSING）。"
            "これは「期間未達（0/60）」ではなく、入力が存在しないことと営業日経過を混同しない。"
            f" 欠損: {listed}"
        ),
        design_doc_sha256=design_doc_sha256,
        design_doc_valid=design_doc_valid,
        judged_business_days=0,
        target_business_days=TARGET_BUSINESS_DAYS,
        start_date=PHASE_START_DATE.isoformat(),
        projected_final_date=PROJECTED_FINAL_SESSION_DATE.isoformat(),
        projected_judgement_date=PROJECTED_JUDGEMENT_DATE.isoformat(),
        actual_judgement_date=None,
        missed_trading_days=0,
        missed_rebalance_days=0,
        consecutive_missed_days=0,
        recommended_candidate=None,
        evaluations={},
        input_files_sha256=input_files_sha256,
        missing_inputs=tuple(missing_inputs),
    )


def compute_sharpe(daily_returns: list[float]) -> float:
    """Annualized Sharpe ratio from daily returns."""
    if len(daily_returns) < 2:
        return 0.0
    s_dev = stdev(daily_returns)
    if s_dev <= 1e-12:
        return 0.0
    return (fmean(daily_returns) / s_dev) * ANNUALIZATION_FACTOR_DAILY


def compute_max_dd(equity_series: list[float]) -> float:
    """Max drawdown as a negative float (e.g. -0.12 for 12% drop)."""
    if not equity_series:
        return 0.0
    peak = equity_series[0]
    max_dd = 0.0
    for eq in equity_series:
        if eq > peak:
            peak = eq
        if peak > 0:
            dd = eq / peak - 1.0
            if dd < max_dd:
                max_dd = dd
    return max_dd


def compute_profit_factor(daily_returns: list[float]) -> float:
    """Profit factor = gross profit / gross loss."""
    g_profit = sum(r for r in daily_returns if r > 0)
    g_loss = sum(abs(r) for r in daily_returns if r < 0)
    if g_loss == 0.0:
        return 100.0 if g_profit > 0 else 1.0
    return g_profit / g_loss


def compute_cagr(start_equity: float, end_equity: float, trading_days: int) -> float:
    """Annualized compound growth rate assuming 252 trading days/year."""
    if start_equity <= 0 or end_equity <= 0 or trading_days <= 0:
        return 0.0
    tot_ret = end_equity / start_equity
    if tot_ret <= 0:
        return -1.0
    years = trading_days / 252.0
    return (tot_ret ** (1.0 / years)) - 1.0


def compute_forward_score(
    sharpe: float, max_dd: float, cagr: float, turnover: float
) -> float:
    """§5.2 Forward Score Function."""
    return (
        SCORE_WEIGHT_SHARPE * (sharpe / SCORE_NORM_SHARPE)
        + SCORE_WEIGHT_MAXDD * (1.0 - abs(max_dd) / SCORE_NORM_MAXDD)
        + SCORE_WEIGHT_CAGR * (cagr / SCORE_NORM_CAGR)
        + SCORE_WEIGHT_TURNOVER * (turnover / SCORE_NORM_TURNOVER)
    )


def compute_sliding_judgement_date(
    start_date: date, target_days: int, missed_days: int
) -> tuple[date, date]:
    """Calculate the final trading day and judgement date accounting for sliding on missed days (§6.2)."""
    cur = start_date
    session_count = 0
    # Walk until target_days valid trading sessions are achieved
    while session_count < target_days + missed_days:
        if is_session_day(cur):
            session_count += 1
            if session_count == target_days + missed_days:
                final_session = cur
                break
        cur += timedelta(days=1)
    else:
        final_session = cur

    # Judgement date is the next trading session after the final session
    nxt = final_session + timedelta(days=1)
    while not is_session_day(nxt):
        nxt += timedelta(days=1)
    return final_session, nxt


# ==============================================================================
# 4. EVALUATION LOGIC
# ==============================================================================
def evaluate_single_series(
    series_name: str,
    daily_returns: list[float],
    equity_series: list[float],
    attribution_stats: dict[str, Any],
    missed_trading_days: int,
    missed_rebalance_days: int,
    kill_switch_triggered_count: int,
    completed_days_count: int,
    turnover: float,
) -> SeriesGateEvaluation:
    """Evaluate §7.1 the 7 mandatory criteria for a single series (10% or 15%)."""
    # 1. Sharpe
    sharpe = compute_sharpe(daily_returns)
    pass_1 = sharpe >= SHARPE_THRESHOLD
    crit_1 = GateCriterionResult(
        item_number=1,
        name="フォワード実測 Sharpe",
        threshold_str=f"≥ {SHARPE_THRESHOLD:.2f}",
        actual_value=round(sharpe, 4),
        actual_str=f"{sharpe:.4f}",
        passed=pass_1,
        details="PASS" if pass_1 else f"FAIL (Sharpe {sharpe:.4f} < {SHARPE_THRESHOLD:.2f})",
    )

    # 2. MaxDD
    max_dd = compute_max_dd(equity_series)
    pass_2 = max_dd >= MAX_DD_THRESHOLD
    crit_2 = GateCriterionResult(
        item_number=2,
        name="フォワード実測 MaxDD",
        threshold_str=f"≥ {MAX_DD_THRESHOLD * 100:.2f}% (下落幅 ≤ 15.00%)",
        actual_value=round(max_dd, 4),
        actual_str=f"{max_dd * 100:.2f}%",
        passed=pass_2,
        details="PASS" if pass_2 else f"FAIL (MaxDD {max_dd * 100:.2f}% < {MAX_DD_THRESHOLD * 100:.2f}%)",
    )

    # 3. Profit Factor
    pf = compute_profit_factor(daily_returns)
    pass_3 = pf >= PROFIT_FACTOR_THRESHOLD
    crit_3 = GateCriterionResult(
        item_number=3,
        name="フォワード実測 Profit Factor",
        threshold_str=f"≥ {PROFIT_FACTOR_THRESHOLD:.2f}",
        actual_value=round(pf, 4),
        actual_str=f"{pf:.4f}",
        passed=pass_3,
        details="PASS" if pass_3 else f"FAIL (PF {pf:.4f} < {PROFIT_FACTOR_THRESHOLD:.2f})",
    )

    # 4. LLM Attribution Non-Degradation
    sharpe_rule = attribution_stats.get("sharpe_rule", 0.0)
    sharpe_llm_hyb = attribution_stats.get("sharpe_llm_hyb", sharpe_rule)
    sharpe_diff = sharpe_llm_hyb - sharpe_rule
    overwrite_win_rate = attribution_stats.get("overwrite_win_rate_pct", 100.0)
    overwrite_count = attribution_stats.get("overwrite_count", 0)

    # If no LLM overwrites occurred, non-degradation is trivially satisfied
    if overwrite_count == 0:
        pass_4 = True
        act_val_4 = "上書き0件 (非劣化)"
    else:
        pass_4 = (sharpe_diff >= -SHARPE_DEGRADATION_MARGIN) and (
            overwrite_win_rate >= OVERWRITE_WIN_RATE_THRESHOLD
        )
        act_val_4 = f"ΔSharpe={sharpe_diff:+.3f}, 勝率={overwrite_win_rate:.1f}%"

    crit_4 = GateCriterionResult(
        item_number=4,
        name="LLM由来の非劣化条件",
        threshold_str=f"Sharpe_LLM/HYB ≥ Sharpe_RULE - {SHARPE_DEGRADATION_MARGIN:.2f} かつ 勝率 ≥ {OVERWRITE_WIN_RATE_THRESHOLD:.1f}%",
        actual_value=act_val_4,
        actual_str=str(act_val_4),
        passed=pass_4,
        details="PASS" if pass_4 else f"FAIL ({act_val_4})",
    )

    # 5. Overwrite Cap
    overwrite_ratio = attribution_stats.get("overwrite_ratio_pct", 0.0)
    pass_5 = overwrite_ratio <= OVERWRITE_RATIO_CAP_PCT
    crit_5 = GateCriterionResult(
        item_number=5,
        name="LLM上書き率上限",
        threshold_str=f"≤ {OVERWRITE_RATIO_CAP_PCT:.1f}%",
        actual_value=round(overwrite_ratio, 2),
        actual_str=f"{overwrite_ratio:.1f}%",
        passed=pass_5,
        details="PASS" if pass_5 else f"FAIL (上書き率 {overwrite_ratio:.1f}% > {OVERWRITE_RATIO_CAP_PCT:.1f}%)",
    )

    # 6. Infrastructure Completion (Missed trading days & rebalance)
    missed_ratio = (
        (missed_trading_days / completed_days_count)
        if completed_days_count > 0
        else 0.0
    )
    pass_6 = (
        (missed_trading_days <= MAX_MISSED_TRADING_DAYS)
        and (missed_ratio <= MAX_MISSED_RATIO)
        and (missed_rebalance_days <= MAX_MISSED_REBALANCE_DAYS)
    )
    crit_6 = GateCriterionResult(
        item_number=6,
        name="運用インフラ完走（欠測率）",
        threshold_str=f"欠測 ≤ {MAX_MISSED_TRADING_DAYS}日 (≤ {MAX_MISSED_RATIO*100:.1f}%) かつ リバランス欠測 ≤ {MAX_MISSED_REBALANCE_DAYS}回",
        actual_value=missed_trading_days,
        actual_str=f"欠測 {missed_trading_days}日 ({missed_ratio*100:.1f}%), リバランス欠測 {missed_rebalance_days}回",
        passed=pass_6,
        details="PASS" if pass_6 else "FAIL",
    )

    # 7. Kill Switch Invocations
    pass_7 = kill_switch_triggered_count <= MAX_KILL_SWITCH_EVENTS
    crit_7 = GateCriterionResult(
        item_number=7,
        name="キルスイッチ非発動",
        threshold_str=f"== {MAX_KILL_SWITCH_EVENTS}回",
        actual_value=kill_switch_triggered_count,
        actual_str=f"{kill_switch_triggered_count}回",
        passed=pass_7,
        details="PASS" if pass_7 else f"FAIL (キルスイッチ発動 {kill_switch_triggered_count}回)",
    )

    criteria = [crit_1, crit_2, crit_3, crit_4, crit_5, crit_6, crit_7]
    all_passed = all(c.passed for c in criteria)

    # Forward score metrics
    cagr = (
        compute_cagr(equity_series[0], equity_series[-1], completed_days_count)
        if len(equity_series) >= 2
        else 0.0
    )
    score = compute_forward_score(sharpe, max_dd, cagr, turnover)

    return SeriesGateEvaluation(
        series_name=series_name,
        criteria=criteria,
        all_passed=all_passed,
        sharpe=sharpe,
        max_dd=max_dd,
        cagr=cagr,
        turnover=turnover,
        forward_score=score,
    )


def evaluate_c2_gate(
    data_dir: Path,
    design_doc_path: Path = DESIGN_DOC_PATH,
    override_completed_days: int | None = None,
    mock_returns_10: list[float] | None = None,
    mock_returns_15: list[float] | None = None,
    mock_equity_10: list[float] | None = None,
    mock_equity_15: list[float] | None = None,
    mock_turnover_10: float | None = None,
    mock_turnover_15: float | None = None,
    mock_attribution_stats: dict[str, Any] | None = None,
) -> GateVerdict:
    """Main gate evaluation pipeline fulfilling §7.1, §7.2, §8, §5.2."""
    input_files_sha256 = {}

    state_file = data_dir / "state.json"
    mtm_file = data_dir / "mtm_history.jsonl"
    decisions_file = data_dir / "decisions.jsonl"
    orders_file = data_dir / "orders.jsonl"
    attribution_file = data_dir / "attribution_summary.json"

    # 1. Mandatory Input Contract (fail-closed, t_dbf3fb69):
    # absence or read failure is INPUT_MISSING, never "0 business days (期間未達)".
    missing_inputs: list[str] = []

    design_doc_present = design_doc_path.is_file()
    if not design_doc_present:
        missing_inputs.append(f"設計書 {design_doc_path}（不在）")
        design_doc_valid, doc_sha = False, ""
    else:
        try:
            design_doc_valid, doc_sha = verify_design_doc(design_doc_path)
        except OSError as exc:
            missing_inputs.append(
                f"設計書 {design_doc_path}（読取失敗: {type(exc).__name__}）"
            )
            design_doc_valid, doc_sha = False, ""
    input_files_sha256["design_doc"] = doc_sha

    # Evidence hashes (read-only) for whichever inputs exist.
    input_files_sha256["state.json"] = file_sha256(state_file)
    input_files_sha256["mtm_history.jsonl"] = file_sha256(mtm_file)
    input_files_sha256["decisions.jsonl"] = file_sha256(decisions_file)
    input_files_sha256["orders.jsonl"] = file_sha256(orders_file)

    # 2. Verify Design Doc SHA256 (§3 Requirement) — governance stop takes precedence
    # when the doc exists but is tampered with (existing INCONCLUSIVE contract).
    if design_doc_present and not design_doc_valid:
        return GateVerdict(
            verdict="INCONCLUSIVE",
            reason=(
                f"設計書 SHA256 不一致または不在 (got '{doc_sha}', expected '{EXPECTED_DESIGN_DOC_SHA256}'). "
                f"ガバナンス規則により評価を即時停止（FAIL-CLOSED）。"
            ),
            design_doc_sha256=doc_sha,
            design_doc_valid=False,
            judged_business_days=0,
            target_business_days=TARGET_BUSINESS_DAYS,
            start_date=PHASE_START_DATE.isoformat(),
            projected_final_date=PROJECTED_FINAL_SESSION_DATE.isoformat(),
            projected_judgement_date=PROJECTED_JUDGEMENT_DATE.isoformat(),
            actual_judgement_date=None,
            missed_trading_days=0,
            missed_rebalance_days=0,
            consecutive_missed_days=0,
            recommended_candidate=None,
            evaluations={},
            input_files_sha256=input_files_sha256,
        )

    # 2b. Data inputs (5-point set) and the trading calendar.
    missing_inputs.extend(check_required_inputs(data_dir))
    missing_inputs.extend(check_trading_calendar())

    if missing_inputs:
        return build_input_missing_verdict(
            missing_inputs,
            design_doc_sha256=doc_sha,
            design_doc_valid=design_doc_valid,
            input_files_sha256=input_files_sha256,
        )

    # 3. Load Persisted Operational State & Logs (Read-Only)
    # Existence/readability/structure already proven by check_required_inputs above.
    state: dict[str, Any] = json.loads(state_file.read_text(encoding="utf-8"))

    completed_days_list = state.get("completed_trading_days", [])
    completed_days_count = (
        override_completed_days
        if override_completed_days is not None
        else len(completed_days_list)
    )

    missed_trading_days = int(state.get("missed_trading_days", 0))
    missed_rebalance_days = int(state.get("missed_rebalance_days", 0))
    consecutive_api_failures = int(state.get("consecutive_api_failures", 0))
    kill_switch_triggered = bool(state.get("kill_switch_triggered", False))
    kill_switch_count = 1 if kill_switch_triggered else 0

    # Projected / sliding judgement date calculation (§6.2)
    _, sliding_judgement_date = compute_sliding_judgement_date(
        PHASE_START_DATE, TARGET_BUSINESS_DAYS, missed_trading_days
    )

    # 3. Check for Fatal Operational Inconclusive Conditions (§8.3)
    if missed_trading_days >= ABORT_MISSED_TRADING_DAYS:
        return GateVerdict(
            verdict="INCONCLUSIVE",
            reason=f"インフラ障害: 累計欠測営業日超過 (現在 {missed_trading_days}日 ≥ 上限 {ABORT_MISSED_TRADING_DAYS}日)",
            design_doc_sha256=doc_sha,
            design_doc_valid=True,
            judged_business_days=completed_days_count,
            target_business_days=TARGET_BUSINESS_DAYS,
            start_date=PHASE_START_DATE.isoformat(),
            projected_final_date=PROJECTED_FINAL_SESSION_DATE.isoformat(),
            projected_judgement_date=sliding_judgement_date.isoformat(),
            actual_judgement_date=None,
            missed_trading_days=missed_trading_days,
            missed_rebalance_days=missed_rebalance_days,
            consecutive_missed_days=consecutive_api_failures,
            recommended_candidate=None,
            evaluations={},
            input_files_sha256=input_files_sha256,
        )

    if consecutive_api_failures >= ABORT_CONSECUTIVE_MISSED_DAYS:
        return GateVerdict(
            verdict="INCONCLUSIVE",
            reason=f"インフラ障害: 連続欠測営業日超過 (現在 {consecutive_api_failures}日 ≥ 上限 {ABORT_CONSECUTIVE_MISSED_DAYS}日)",
            design_doc_sha256=doc_sha,
            design_doc_valid=True,
            judged_business_days=completed_days_count,
            target_business_days=TARGET_BUSINESS_DAYS,
            start_date=PHASE_START_DATE.isoformat(),
            projected_final_date=PROJECTED_FINAL_SESSION_DATE.isoformat(),
            projected_judgement_date=sliding_judgement_date.isoformat(),
            actual_judgement_date=None,
            missed_trading_days=missed_trading_days,
            missed_rebalance_days=missed_rebalance_days,
            consecutive_missed_days=consecutive_api_failures,
            recommended_candidate=None,
            evaluations={},
            input_files_sha256=input_files_sha256,
        )

    if missed_rebalance_days >= ABORT_MISSED_REBALANCE_DAYS:
        return GateVerdict(
            verdict="INCONCLUSIVE",
            reason=f"インフラ障害: リバランス日欠測超過 (現在 {missed_rebalance_days}回 ≥ 上限 {ABORT_MISSED_REBALANCE_DAYS}回)",
            design_doc_sha256=doc_sha,
            design_doc_valid=True,
            judged_business_days=completed_days_count,
            target_business_days=TARGET_BUSINESS_DAYS,
            start_date=PHASE_START_DATE.isoformat(),
            projected_final_date=PROJECTED_FINAL_SESSION_DATE.isoformat(),
            projected_judgement_date=sliding_judgement_date.isoformat(),
            actual_judgement_date=None,
            missed_trading_days=missed_trading_days,
            missed_rebalance_days=missed_rebalance_days,
            consecutive_missed_days=consecutive_api_failures,
            recommended_candidate=None,
            evaluations={},
            input_files_sha256=input_files_sha256,
        )

    # 4. Check for Period Completion (60 business days required)
    # "先出しの合否は出さない" -> If completed_days < 60, immediately return INCONCLUSIVE (期間未達)
    if completed_days_count < TARGET_BUSINESS_DAYS:
        return GateVerdict(
            verdict="INCONCLUSIVE",
            reason=f"期間未達（判定営業日 {TARGET_BUSINESS_DAYS} 未達、現在 {completed_days_count} 営業日目）",
            design_doc_sha256=doc_sha,
            design_doc_valid=True,
            judged_business_days=completed_days_count,
            target_business_days=TARGET_BUSINESS_DAYS,
            start_date=PHASE_START_DATE.isoformat(),
            projected_final_date=PROJECTED_FINAL_SESSION_DATE.isoformat(),
            projected_judgement_date=sliding_judgement_date.isoformat(),
            actual_judgement_date=None,
            missed_trading_days=missed_trading_days,
            missed_rebalance_days=missed_rebalance_days,
            consecutive_missed_days=consecutive_api_failures,
            recommended_candidate=None,
            evaluations={},
            input_files_sha256=input_files_sha256,
        )

    # 5. Extract MTM and Attribution Data for 60-day Judgement
    returns_10: list[float] = []
    returns_15: list[float] = []
    equity_10: list[float] = []
    equity_15: list[float] = []

    if mock_returns_10 is not None and mock_returns_15 is not None:
        returns_10 = mock_returns_10
        returns_15 = mock_returns_15
        equity_10 = mock_equity_10 or [100000.0]
        equity_15 = mock_equity_15 or [100000.0]
    elif mtm_file.exists():
        for line in mtm_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                returns_10.append(float(row.get("daily_return_10", 0.0)))
                returns_15.append(float(row.get("daily_return_15", 0.0)))
                equity_10.append(float(row.get("equity_10", 100000.0)))
                equity_15.append(float(row.get("equity_15", 100000.0)))
            except Exception:
                continue

    # Default attribution statistics
    attrib_stats: dict[str, Any] = {
        "sharpe_rule": 0.80,
        "sharpe_llm_hyb": 0.80,
        "overwrite_win_rate_pct": 100.0,
        "overwrite_ratio_pct": 0.0,
        "overwrite_count": 0,
    }
    if mock_attribution_stats is not None:
        attrib_stats = mock_attribution_stats
    elif attribution_file.exists():
        with contextlib.suppress(Exception):
            a_data = json.loads(attribution_file.read_text(encoding="utf-8"))
            ass = a_data.get("overwrite_assessment", {})
            attrib_stats["overwrite_ratio_pct"] = float(ass.get("overwrite_ratio_pct", 0.0))
            attrib_stats["overwrite_win_rate_pct"] = float(
                ass.get("overwrite_success_rate_pct", 100.0)
            )
            attrib_stats["overwrite_count"] = int(ass.get("overwrite_count", 0))
            for row in a_data.get("matrix_rows", []):
                if row.get("label") == "RULE":
                    attrib_stats["sharpe_rule"] = float(row.get("effective_sharpe", 0.80))
                elif row.get("label") in ("LLM", "HYBRID"):
                    attrib_stats["sharpe_llm_hyb"] = float(
                        row.get("effective_sharpe", attrib_stats["sharpe_rule"])
                    )

    turnover_10 = (
        mock_turnover_10
        if mock_turnover_10 is not None
        else 3.40  # S02 target 10 baseline
    )
    turnover_15 = (
        mock_turnover_15
        if mock_turnover_15 is not None
        else 2.03  # S02 target 15 baseline
    )

    # 6. Evaluate Both Series (§7.1)
    eval_10 = evaluate_single_series(
        series_name="vol_target_10",
        daily_returns=returns_10,
        equity_series=equity_10,
        attribution_stats=attrib_stats,
        missed_trading_days=missed_trading_days,
        missed_rebalance_days=missed_rebalance_days,
        kill_switch_triggered_count=kill_switch_count,
        completed_days_count=completed_days_count,
        turnover=turnover_10,
    )

    eval_15 = evaluate_single_series(
        series_name="vol_target_15",
        daily_returns=returns_15,
        equity_series=equity_15,
        attribution_stats=attrib_stats,
        missed_trading_days=missed_trading_days,
        missed_rebalance_days=missed_rebalance_days,
        kill_switch_triggered_count=kill_switch_count,
        completed_days_count=completed_days_count,
        turnover=turnover_15,
    )

    evaluations = {
        "vol_target_10": eval_10,
        "vol_target_15": eval_15,
    }

    # 7. Synthesize Overall 3-Value Verdict (§8) and Recommended Candidate (§5.2)
    # Check Kill Switch immediate FAIL
    if kill_switch_triggered:
        final_verdict = "FAIL"
        final_reason = "不合格: 運用期間中にキルスイッチが発動"
        recommended = None
    elif eval_10.all_passed and eval_15.all_passed:
        final_verdict = "PASS"
        if eval_15.forward_score >= eval_10.forward_score:
            recommended = "vol_target_15"
            final_reason = (
                f"合格 (10% & 15% 両者合格): スコア関数により vol_target_15 を推挙 "
                f"(Score 15%: {eval_15.forward_score:.4f} ≥ 10%: {eval_10.forward_score:.4f})"
            )
        else:
            recommended = "vol_target_10"
            final_reason = (
                f"合格 (10% & 15% 両者合格): スコア関数により vol_target_10 を推挙 "
                f"(Score 10%: {eval_10.forward_score:.4f} > 15%: {eval_15.forward_score:.4f})"
            )
    elif eval_15.all_passed:
        final_verdict = "PASS"
        recommended = "vol_target_15"
        final_reason = "合格: vol_target_15 のみ合格基準を満たしたため推挙"
    elif eval_10.all_passed:
        final_verdict = "PASS"
        recommended = "vol_target_10"
        final_reason = "合格: vol_target_10 のみ合格基準を満たしたため推挙"
    else:
        final_verdict = "FAIL"
        recommended = None
        final_reason = "不合格: 10% および 15% の両系列ともに合格7項目を満たせず"

    # Actual judgement date = today if finished
    actual_judgement_date_str = sliding_judgement_date.isoformat()

    return GateVerdict(
        verdict=final_verdict,
        reason=final_reason,
        design_doc_sha256=doc_sha,
        design_doc_valid=True,
        judged_business_days=completed_days_count,
        target_business_days=TARGET_BUSINESS_DAYS,
        start_date=PHASE_START_DATE.isoformat(),
        projected_final_date=PROJECTED_FINAL_SESSION_DATE.isoformat(),
        projected_judgement_date=sliding_judgement_date.isoformat(),
        actual_judgement_date=actual_judgement_date_str,
        missed_trading_days=missed_trading_days,
        missed_rebalance_days=missed_rebalance_days,
        consecutive_missed_days=consecutive_api_failures,
        recommended_candidate=recommended,
        evaluations=evaluations,
        input_files_sha256=input_files_sha256,
    )


# ==============================================================================
# 5. MARKDOWN FORMATTER
# ==============================================================================
def format_gate_markdown(verdict: GateVerdict) -> str:
    """Format GateVerdict as a crisp GitHub Flavored Markdown report."""
    v_badge = {
        "PASS": "**【合格 (PASS)】**",
        "FAIL": "**【不合格 (FAIL)】**",
        "INCONCLUSIVE": "**【判定不能 (INCONCLUSIVE)】**",
        "INPUT_MISSING": "**【必須入力欠損 (INPUT_MISSING)】**",
    }.get(verdict.verdict, verdict.verdict)

    lines = [
        "# C2 ペーパー運用 G2ゲート判定書",
        "",
        f"- **総合判定**: {v_badge}",
        f"- **判定理由**: {verdict.reason}",
        f"- **推挙戦略**: `{verdict.recommended_candidate or 'なし'}`",
        (
            "- **判定営業日数**: 不明（必須入力欠損のため算定不能。0営業日目ではない）"
            if verdict.verdict == INPUT_MISSING_VERDICT
            else f"- **判定営業日数**: {verdict.judged_business_days} / {verdict.target_business_days} 営業日"
        ),
        f"- **起算日**: {verdict.start_date}",
        f"- **当初判定日**: {verdict.projected_judgement_date}",
        (
            "- **欠測日数**: 不明（必須入力欠損のため算定不能）"
            if verdict.verdict == INPUT_MISSING_VERDICT
            else f"- **欠測日数**: 累計 {verdict.missed_trading_days} 日 / リバランス欠測 {verdict.missed_rebalance_days} 回"
        ),
        f"- **設計書 SHA256**: `{verdict.design_doc_sha256}` ({'MATCH' if verdict.design_doc_valid else 'MISMATCH'})",
        "",
        "---",
        "",
        "## 1. §7.1 フォワード実績合格判定（7項目）",
        "",
    ]

    if not verdict.evaluations:
        if verdict.verdict == INPUT_MISSING_VERDICT:
            lines.extend(
                [
                    "> [!WARNING]",
                    "> 必須入力の欠損・読取失敗により判定不能（INPUT_MISSING）。",
                    "> **これは「期間未達」ではない**（入力を0営業日目として扱わない）。",
                    f"> 欠損: {'; '.join(verdict.missing_inputs)}",
                    "",
                ]
            )
        else:
            lines.extend(
                [
                    "> [!NOTE]",
                    f"> 現在は {verdict.judged_business_days} 営業日目（期間未達）または外的中断のため、先出しの7項目確定値評価は保留されています。",
                    "",
                ]
            )
    else:
        for s_name, eval_res in verdict.evaluations.items():
            s_title = "C2 15% 系列 (vol_target_15)" if "15" in s_name else "C2 10% 系列 (vol_target_10)"
            lines.extend(
                [
                    f"### {s_title} — {'合格 (PASS)' if eval_res.all_passed else '不合格 (FAIL)'}",
                    "",
                    "| 番号 | 判定項目 | 凍結合格閾値 | フォワード実測値 | 合否 | 備考 |",
                    "|:---:|---|:---:|:---:|:---:|---|",
                ]
            )
            for c in eval_res.criteria:
                status = "**PASS**" if c.passed else "**FAIL**"
                lines.append(
                    f"| **①** | {c.name} | {c.threshold_str} | {c.actual_str} | {status} | {c.details} |"
                    if c.item_number == 1
                    else f"| **{'②③④⑤⑥⑦'[c.item_number - 2]}** | {c.name} | {c.threshold_str} | {c.actual_str} | {status} | {c.details} |"
                )
            lines.extend(
                [
                    "",
                    f"- **Forward Score (§5.2)**: `{eval_res.forward_score:.4f}` "
                    f"(Sharpe: {eval_res.sharpe:.2f}, MaxDD: {eval_res.max_dd*100:.2f}%, CAGR: {eval_res.cagr*100:.2f}%, Turnover: {eval_res.turnover:.2f}x)",
                    "",
                ]
            )

    lines.extend(
        [
            "---",
            "",
            "## 2. 参照ファイル SHA256",
            "",
            "| ファイル | SHA256 |",
            "|---|---|",
        ]
    )
    for fname, fsha in sorted(verdict.input_files_sha256.items()):
        lines.append(f"| `{fname}` | `{fsha}` |")
    lines.append("")

    return "\n".join(lines)


# ==============================================================================
# 6. CLI ENTRYPOINT & IDEMPOTENT RUNNER
# ==============================================================================
def main() -> int:
    parser = argparse.ArgumentParser(
        description="C2 Forward Paper 60-Day Gate Evaluator"
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("/root/current-work/yowayowa-investor/data/paper_c2"),
        help="Path to paper_c2 data directory",
    )
    parser.add_argument(
        "--design-doc",
        type=Path,
        default=DESIGN_DOC_PATH,
        help="Path to G2 design document",
    )
    parser.add_argument(
        "--out-json",
        type=Path,
        default=None,
        help="Path to write output verdict JSON",
    )
    parser.add_argument(
        "--out-md",
        type=Path,
        default=None,
        help="Path to write output verdict Markdown",
    )
    parser.add_argument(
        "--cron-mode",
        action="store_true",
        help="Run in cron mode: silent on INCONCLUSIVE period non-matured, exit 0",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Dry run without writing outputs to disk",
    )
    args = parser.parse_args()

    # Perform evaluation
    verdict = evaluate_c2_gate(
        data_dir=args.data_dir,
        design_doc_path=args.design_doc,
    )

    out_json_path = (
        args.out_json or args.data_dir / "c2_gate_verdict.json"
    )
    out_md_path = (
        args.out_md or args.data_dir / "c2_gate_verdict.md"
    )

    verdict_dict = verdict.to_dict()
    json_bytes = (
        json.dumps(verdict_dict, indent=2, sort_keys=True, ensure_ascii=False)
        + "\n"
    ).encode("utf-8")
    md_str = format_gate_markdown(verdict)

    if not args.dry_run:
        out_json_path.parent.mkdir(parents=True, exist_ok=True)
        out_json_path.write_bytes(json_bytes)
        out_md_path.write_text(md_str, encoding="utf-8")

    # Fail-closed: mandatory input missing/unreadable -> explicit verdict, non-zero exit.
    # This takes precedence over cron-mode silence: a broken pipeline must be loud.
    if verdict.verdict == INPUT_MISSING_VERDICT:
        print(
            f"C2 GATE EVAL: verdict={verdict.verdict} (必須入力欠損 — 期間未達ではない)",
            file=sys.stderr,
        )
        print(f"C2 GATE REASON: {verdict.reason}", file=sys.stderr)
        for item in verdict.missing_inputs:
            print(f"C2 GATE MISSING INPUT: {item}", file=sys.stderr)
        if not args.dry_run:
            print(f"C2 GATE VERDICT JSON: {out_json_path}", file=sys.stderr)
        return INPUT_MISSING_EXIT_CODE

    # In cron mode, if it's period unmatured, be completely silent and exit 0
    if args.cron_mode:
        if verdict.verdict == "INCONCLUSIVE" and "期間未達" in verdict.reason:
            return 0
        # If reached judgement or inconclusive error, print exactly one line
        print(f"C2 GATE EVAL: verdict={verdict.verdict} reason={verdict.reason}")
        return 0

    # Normal CLI mode: print summary
    print(f"Verdict:   {verdict.verdict}")
    print(f"Reason:    {verdict.reason}")
    print(f"Days:      {verdict.judged_business_days} / {verdict.target_business_days}")
    print(f"Candidate: {verdict.recommended_candidate or 'None'}")
    if not args.dry_run:
        print(f"Wrote JSON: {out_json_path}")
        print(f"Wrote MD:   {out_md_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
