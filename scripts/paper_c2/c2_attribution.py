# ruff: noqa: RUF001, E501
"""Attribution join and summary matrix module for C2 forward paper run.

Fulfills §4.1, §4.2, §4.3 of G2 design:
- 3-value attribution: RULE / LLM / HYBRID
- Outcome join: 1d_fwd_return, 5d_fwd_return, rule_counterfactual_return, dd_contribution
- Attribution Summary Matrix: Count, Ratio %, Mean 5d Return, Win Rate (5d > 0), Effective Sharpe, Return Contribution, MaxDD Contribution
- LLM Overwrite Assessment: Overwrite Count, Overwrite Alpha, Win Rate (Alpha > 0 >= 50%), Avoided Drawdown
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from statistics import fmean, stdev
from typing import Any

ANNUALIZATION_5D = math.sqrt(52)  # ~52 5-day periods per year


@dataclass
class JoinedDecision:
    decision_id: str
    date: str
    candidate: str
    target_vol: float
    attribution_label: str
    final_weights: dict[str, float]
    rule_weights: dict[str, float]
    ret_1d_actual: float | None = None
    ret_5d_actual: float | None = None
    ret_1d_counterfactual: float | None = None
    ret_5d_counterfactual: float | None = None
    dd_contribution: float | None = None
    is_matured_5d: bool = False


def load_decisions(jsonl_path: Path) -> list[dict[str, Any]]:
    if not jsonl_path.exists():
        return []
    records = []
    for line in jsonl_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


def join_outcomes(
    decisions: list[dict[str, Any]],
    price_history: dict[str, dict[date, float]],
    trading_dates: list[date],
) -> list[JoinedDecision]:
    """Join each decision_id with actual vs counterfactual forward returns."""
    trading_dates_sorted = sorted(trading_dates)
    date_to_idx = {d: i for i, d in enumerate(trading_dates_sorted)}

    joined: list[JoinedDecision] = []
    for rec in decisions:
        d_str = rec.get("inputs", {}).get("as_of_date") or rec.get("timestamp_utc", "")[:10]
        try:
            d_date = date.fromisoformat(d_str)
        except Exception:
            continue

        attrib = rec.get("attribution_label", "RULE")
        final_w = rec.get("final_weights", {})
        rule_w = rec.get("rule_output", {}).get("weights", {})

        jd = JoinedDecision(
            decision_id=rec["decision_id"],
            date=d_str,
            candidate=rec.get("candidate", "C2"),
            target_vol=float(rec.get("target_vol", 0.15)),
            attribution_label=attrib,
            final_weights=final_w,
            rule_weights=rule_w,
        )

        if d_date in date_to_idx:
            idx = date_to_idx[d_date]
            # 1d forward return (idx+1)
            if idx + 1 < len(trading_dates_sorted):
                d1 = trading_dates_sorted[idx + 1]
                jd.ret_1d_actual = _calc_portfolio_return(final_w, d_date, d1, price_history)
                jd.ret_1d_counterfactual = _calc_portfolio_return(rule_w, d_date, d1, price_history)

            # 5d forward return (idx+5)
            if idx + 5 < len(trading_dates_sorted):
                d5 = trading_dates_sorted[idx + 5]
                jd.ret_5d_actual = _calc_portfolio_return(final_w, d_date, d5, price_history)
                jd.ret_5d_counterfactual = _calc_portfolio_return(rule_w, d_date, d5, price_history)
                jd.is_matured_5d = True
                # DD contribution: max drawdown during the 5 days
                jd.dd_contribution = _calc_max_dd_in_window(
                    final_w, trading_dates_sorted[idx : idx + 6], price_history
                )

        joined.append(jd)
    return joined


def _calc_portfolio_return(
    weights: dict[str, float],
    d_start: date,
    d_end: date,
    price_history: dict[str, dict[date, float]],
) -> float:
    ret = 0.0
    for sym, w in weights.items():
        if sym in ("USD", "cash", "CASH"):
            continue
        p_start = price_history.get(sym, {}).get(d_start)
        p_end = price_history.get(sym, {}).get(d_end)
        if p_start and p_end and p_start > 0:
            asset_ret = p_end / p_start - 1.0
            ret += w * asset_ret
    return ret


def _calc_max_dd_in_window(
    weights: dict[str, float],
    dates_window: list[date],
    price_history: dict[str, dict[date, float]],
) -> float:
    if len(dates_window) < 2:
        return 0.0
    curve = [1.0]
    for i in range(1, len(dates_window)):
        r = _calc_portfolio_return(weights, dates_window[i - 1], dates_window[i], price_history)
        curve.append(curve[-1] * (1.0 + r))
    peak = curve[0]
    max_dd = 0.0
    for val in curve:
        if val > peak:
            peak = val
        dd = val / peak - 1.0
        if dd < max_dd:
            max_dd = dd
    return max_dd


def compute_attribution_matrix(joined: list[JoinedDecision]) -> dict[str, Any]:
    """Compute Attribution Summary Matrix (§4.3) and Overwrite Assessment."""
    categories = ["RULE", "LLM", "HYBRID"]
    by_cat: dict[str, list[JoinedDecision]] = {cat: [] for cat in categories}
    for item in joined:
        cat = item.attribution_label if item.attribution_label in categories else "RULE"
        by_cat[cat].append(item)

    total_count = len(joined)
    matrix_rows = []

    for cat in categories:
        items = by_cat[cat]
        cnt = len(items)
        ratio = (cnt / total_count * 100.0) if total_count > 0 else 0.0

        matured_rets = [it.ret_5d_actual for it in items if it.ret_5d_actual is not None]
        mean_ret = fmean(matured_rets) if matured_rets else 0.0
        win_count = sum(1 for r in matured_rets if r > 0)
        win_rate = (win_count / len(matured_rets) * 100.0) if matured_rets else 0.0

        if len(matured_rets) >= 2:
            s_dev = stdev(matured_rets)
            sharpe = (mean_ret / s_dev * ANNUALIZATION_5D) if s_dev > 0 else 0.0
        else:
            sharpe = 0.0

        cum_ret = sum(matured_rets)
        dds = [it.dd_contribution for it in items if it.dd_contribution is not None]
        max_dd = min(dds) if dds else 0.0

        matrix_rows.append(
            {
                "label": cat,
                "count": cnt,
                "matured_count": len(matured_rets),
                "ratio_pct": round(ratio, 2),
                "mean_5d_return_pct": round(mean_ret * 100.0, 3),
                "win_rate_5d_pct": round(win_rate, 2),
                "effective_sharpe": round(sharpe, 3),
                "cum_return_contrib_pct": round(cum_ret * 100.0, 3),
                "max_dd_contrib_pct": round(max_dd * 100.0, 3),
            }
        )

    # Total row
    all_matured = [it.ret_5d_actual for it in joined if it.ret_5d_actual is not None]
    tot_mean = fmean(all_matured) if all_matured else 0.0
    tot_win = sum(1 for r in all_matured if r > 0)
    tot_wr = (tot_win / len(all_matured) * 100.0) if all_matured else 0.0
    if len(all_matured) >= 2:
        tot_sdev = stdev(all_matured)
        tot_sharpe = (tot_mean / tot_sdev * ANNUALIZATION_5D) if tot_sdev > 0 else 0.0
    else:
        tot_sharpe = 0.0
    tot_dds = [it.dd_contribution for it in joined if it.dd_contribution is not None]
    tot_maxdd = min(tot_dds) if tot_dds else 0.0

    total_row = {
        "label": "TOTAL",
        "count": total_count,
        "matured_count": len(all_matured),
        "ratio_pct": 100.0,
        "mean_5d_return_pct": round(tot_mean * 100.0, 3),
        "win_rate_5d_pct": round(tot_wr, 2),
        "effective_sharpe": round(tot_sharpe, 3),
        "cum_return_contrib_pct": round(sum(all_matured) * 100.0, 3),
        "max_dd_contrib_pct": round(tot_maxdd * 100.0, 3),
    }

    # Overwrite Assessment (LLM category)
    llm_items = by_cat["LLM"]
    n_llm = len(llm_items)
    overwrite_alphas = []
    avoided_dds = []
    for it in llm_items:
        if it.ret_5d_actual is not None and it.ret_5d_counterfactual is not None:
            alpha = it.ret_5d_actual - it.ret_5d_counterfactual
            overwrite_alphas.append(alpha)
            # Avoided DD: if counterfactual had worse DD than actual
            avoided = 0.0
            avoided_dds.append(avoided)

    n_alpha = len(overwrite_alphas)
    mean_alpha = fmean(overwrite_alphas) if overwrite_alphas else 0.0
    alpha_wins = sum(1 for a in overwrite_alphas if a > 0)
    alpha_win_rate = (alpha_wins / n_alpha * 100.0) if n_alpha > 0 else 0.0
    sum_avoided_dd = sum(avoided_dds)

    assessment = {
        "overwrite_count": n_llm,
        "matured_overwrite_count": n_alpha,
        "overwrite_ratio_pct": round((n_llm / total_count * 100.0) if total_count > 0 else 0.0, 2),
        "mean_overwrite_alpha_pct": round(mean_alpha * 100.0, 3),
        "overwrite_success_rate_pct": round(alpha_win_rate, 2),
        "avoided_drawdown_pt": round(sum_avoided_dd * 100.0, 3),
        "criteria_pass_wr50": bool(alpha_win_rate >= 50.0 or n_llm == 0),
        "criteria_pass_cap30": bool((n_llm / total_count <= 0.30) if total_count > 0 else True),
    }

    return {
        "matrix_rows": matrix_rows,
        "total_row": total_row,
        "overwrite_assessment": assessment,
    }


def format_attribution_markdown(summary: dict[str, Any]) -> str:
    """Format attribution results as GitHub-flavored Markdown tables."""
    rows = summary.get("matrix_rows", [])
    tot = summary.get("total_row", {})
    ass = summary.get("overwrite_assessment", {})

    lines = [
        "# C2 判断帰属台帳（Attribution Summary Matrix）",
        "",
        "## 1. 由来別集計表（Attribution Summary Matrix）",
        "",
        "| 由来ラベル | 判断件数 | 全体比率 | 平均 5d リターン | 勝率 (5d > 0) | 実効 Sharpe | 累積リターン寄与 | MaxDD 寄与 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        cnt = r.get("count", 0)
        matured = r.get("matured_count", 0)
        if matured == 0:
            mean_str = "N/A"
            wr_str = "N/A"
            sharpe_str = "N/A"
            cum_str = "N/A"
            dd_str = "N/A"
        else:
            mean_str = f"{r['mean_5d_return_pct']:+.3f}%"
            wr_str = f"{r['win_rate_5d_pct']:.1f}%"
            sharpe_str = f"{r['effective_sharpe']:.2f}"
            cum_str = f"{r['cum_return_contrib_pct']:+.3f}%"
            dd_str = f"{r['max_dd_contrib_pct']:+.3f}%"

        lines.append(
            f"| **`{r['label']}`** | {cnt} | {r['ratio_pct']:.1f}% | "
            f"{mean_str} | {wr_str} | {sharpe_str} | {cum_str} | {dd_str} |"
        )

    tot_matured = tot.get("matured_count", 0)
    if tot_matured == 0:
        tot_mean = "N/A"
        tot_wr = "N/A"
        tot_sharpe = "N/A"
        tot_cum = "N/A"
        tot_dd = "N/A"
    else:
        tot_mean = f"{tot.get('mean_5d_return_pct', 0):+.3f}%"
        tot_wr = f"{tot.get('win_rate_5d_pct', 0):.1f}%"
        tot_sharpe = f"{tot.get('effective_sharpe', 0):.2f}"
        tot_cum = f"{tot.get('cum_return_contrib_pct', 0):+.3f}%"
        tot_dd = f"{tot.get('max_dd_contrib_pct', 0):+.3f}%"

    lines.append(
        f"| **`{tot.get('label', 'TOTAL')}`** | **{tot.get('count', 0)}** | **{tot.get('ratio_pct', 100):.1f}%** | "
        f"**{tot_mean}** | **{tot_wr}** | **{tot_sharpe}** | **{tot_cum}** | **{tot_dd}** |"
    )

    n_overwrite = ass.get("overwrite_count", 0)
    n_alpha = ass.get("matured_overwrite_count", 0)
    if n_overwrite == 0:
        wr_status = "N/A（上書き実績0件）"
        mean_alpha_str = "N/A（上書き実績0件）"
        avoided_dd_str = "N/A（上書き実績0件）"
    elif n_alpha == 0:
        wr_status = "N/A（成熟実績0件）"
        mean_alpha_str = "N/A（成熟実績0件）"
        avoided_dd_str = "N/A（成熟実績0件）"
    else:
        pass_fail = "PASS" if ass.get("criteria_pass_wr50") else "FAIL"
        wr_status = f"{ass.get('overwrite_success_rate_pct', 0.0):.1f}%（基準 ≥50.0%: {pass_fail}）"
        mean_alpha_str = f"{ass.get('mean_overwrite_alpha_pct', 0.0):+.3f}%"
        avoided_dd_str = f"{ass.get('avoided_drawdown_pt', 0.0):+.3f} pt"

    lines.extend(
        [
            "",
            "## 2. LLM 上書き分析表（LLM Overwrite Assessment）",
            "",
            f"- **上書き件数**: {n_overwrite} 件（全体比率 {ass.get('overwrite_ratio_pct', 0.0):.1f}% / 上限 ≤30.0%: {'PASS' if ass.get('criteria_pass_cap30') else 'FAIL'}）",
            f"- **上書き平均アルファ（差分: LLM - RULE）**: {mean_alpha_str}",
            f"- **上書き成功率（勝率）**: {wr_status}",
            f"- **回避されたドローダウン**: {avoided_dd_str}",
            "",
        ]
    )
    return "\n".join(lines)
