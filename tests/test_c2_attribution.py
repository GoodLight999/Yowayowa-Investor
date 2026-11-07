"""Unit tests for C2 attribution join and matrix calculation."""

from __future__ import annotations

import pytest

from scripts.paper_c2.c2_attribution import (
    JoinedDecision,
    compute_attribution_matrix,
    format_attribution_markdown,
)


def test_attribution_matrix_and_overwrite_assessment() -> None:
    # Construct 3 mock joined decisions: 1 RULE, 1 LLM, 1 HYBRID
    jd_rule = JoinedDecision(
        decision_id="C2:15:2026-11-03:001",
        date="2026-11-03",
        candidate="C2",
        target_vol=0.15,
        attribution_label="RULE",
        final_weights={"SPY": 0.25, "IWM": 0.25, "JNJ": 0.25, "PG": 0.25},
        rule_weights={"SPY": 0.25, "IWM": 0.25, "JNJ": 0.25, "PG": 0.25},
        ret_1d_actual=0.005,
        ret_5d_actual=0.015,
        ret_1d_counterfactual=0.005,
        ret_5d_counterfactual=0.015,
        dd_contribution=-0.008,
        is_matured_5d=True,
    )

    jd_llm = JoinedDecision(
        decision_id="C2:15:2026-11-10:001",
        date="2026-11-10",
        candidate="C2",
        target_vol=0.15,
        attribution_label="LLM",
        final_weights={"SPY": 0.10, "IWM": 0.10, "JNJ": 0.10, "PG": 0.10, "USD": 0.60},
        rule_weights={"SPY": 0.25, "IWM": 0.25, "JNJ": 0.25, "PG": 0.25, "USD": 0.00},
        ret_1d_actual=-0.002,
        ret_5d_actual=-0.005,  # actual loss 0.5%
        ret_1d_counterfactual=-0.008,
        ret_5d_counterfactual=-0.020,  # rule loss would have been 2.0%
        dd_contribution=-0.005,
        is_matured_5d=True,
    )

    jd_hyb = JoinedDecision(
        decision_id="C2:15:2026-11-17:001",
        date="2026-11-17",
        candidate="C2",
        target_vol=0.15,
        attribution_label="HYBRID",
        final_weights={"SPY": 0.22, "IWM": 0.22, "JNJ": 0.22, "PG": 0.22, "USD": 0.12},
        rule_weights={"SPY": 0.25, "IWM": 0.25, "JNJ": 0.25, "PG": 0.25, "USD": 0.00},
        ret_1d_actual=0.004,
        ret_5d_actual=0.012,
        ret_1d_counterfactual=0.005,
        ret_5d_counterfactual=0.014,
        dd_contribution=-0.003,
        is_matured_5d=True,
    )

    summary = compute_attribution_matrix([jd_rule, jd_llm, jd_hyb])

    # 1. Matrix verification
    rows = {r["label"]: r for r in summary["matrix_rows"]}
    assert rows["RULE"]["count"] == 1
    assert rows["LLM"]["count"] == 1
    assert rows["HYBRID"]["count"] == 1

    tot = summary["total_row"]
    assert tot["count"] == 3
    assert tot["ratio_pct"] == 100.0

    # 2. Overwrite assessment verification
    ass = summary["overwrite_assessment"]
    assert ass["overwrite_count"] == 1
    assert pytest.approx(ass["overwrite_ratio_pct"], abs=1e-2) == 33.33
    # Overwrite alpha: -0.005 - (-0.020) = +0.015 (+1.5% alpha by reducing exposure before drop)
    assert pytest.approx(ass["mean_overwrite_alpha_pct"], abs=1e-2) == 1.50
    assert ass["overwrite_success_rate_pct"] == 100.0
    assert ass["criteria_pass_wr50"] is True

    # 3. Markdown format test
    md = format_attribution_markdown(summary)
    assert "Attribution Summary Matrix" in md
    assert "`RULE`" in md
    assert "`LLM`" in md
    assert "`HYBRID`" in md


def test_attribution_matrix_zero_cases_render_na() -> None:
    # Test completely empty or unmatured decisions render N/A without contradiction
    summary = compute_attribution_matrix([])
    md = format_attribution_markdown(summary)
    assert "N/A（上書き実績0件）" in md
    assert "N/A" in md
    assert "0.0%（基準 ≥50.0%: PASS）" not in md
