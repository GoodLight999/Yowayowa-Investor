# ruff: noqa: RUF002, RUF003 — 日本語の句読点（全角）は本文テキストであり誤字ではない
"""S-07-A04/A08/A09 のオフライン受入試験（合成データ）。

* A04: ピーク比 DD の候補計測（凍結値の変更ではない）
* A08: data_kind 必須の JSON/Markdown renderer
* A09: 凍結された 5 条件・窓・比較演算子の不変性

PASS は試験アサーションの PASS であり、運用成績ではない。
"""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path

import pytest

from yowayowa.hl_exec.s07_offline.criteria import (
    MAX_PEAK_DRAWDOWN_PCT,
    MAX_SLIPPAGE_MEDIAN_BP,
    MIN_COMPLETED_ROUND_TRIPS,
    OFFICIAL_VERDICT_HELD,
    WINDOW_DAYS,
    WINDOW_END_UTC,
    WINDOW_START_UTC,
    CriteriaMetrics,
    Verdict,
    classify_window,
    endpoint_sensitive,
    evaluate_frozen_criteria,
    in_window,
)
from yowayowa.hl_exec.s07_offline.drawdown import EquitySample, parse_sample, peak_drawdown
from yowayowa.hl_exec.s07_offline.manifest import MS_PER_DAY, load_batch, partition_by_window
from yowayowa.hl_exec.s07_offline.report import (
    DataKindError,
    build_payload,
    render_json,
    render_markdown,
)

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "s07_offline_synthetic"
EQUITY = FIXTURES / "equity_synthetic_A04.json"
BARS = FIXTURES / "bars_synthetic_A01.json"


# --------------------------------------------------------------------------- A04


def _equity_samples() -> list[EquitySample]:
    data = json.loads(EQUITY.read_text(encoding="utf-8"))
    return [parse_sample(row) for row in data["samples"]]


def test_a04_peak_relative_drawdown_uses_running_peak_not_fixed_100() -> None:
    result = peak_drawdown(_equity_samples())
    assert result.verdict.value == "MEASURED"
    assert result.max_drawdown_pct == Decimal("10.0")
    assert result.sampled_days == 4
    assert result.excluded_outside_window == 1
    assert result.frequency == "daily_utc_end_candidate"
    assert result.intraday_guaranteed is False
    # 固定 100 分母なら 12% になる。ピーク 120 分母との区別を固定する。
    assert result.max_drawdown_pct != Decimal("12")
    peak_points = [point for point in result.points if point.equity == Decimal("108")]
    assert peak_points[0].peak == Decimal("120")
    assert peak_points[0].drawdown_pct == Decimal("10.0")


def test_a04_missing_or_nonpositive_peak_is_inconclusive() -> None:
    samples = [
        EquitySample(time_ms=int(WINDOW_START_UTC.timestamp() * 1000), equity=Decimal("0")),
    ]
    assert peak_drawdown(samples).verdict.value == "INCONCLUSIVE"

    samples = [
        EquitySample(
            time_ms=int(WINDOW_START_UTC.timestamp() * 1000) + i * MS_PER_DAY,
            equity=Decimal(value),
        )
        for i, value in enumerate([100, -1, 90])
    ]
    result = peak_drawdown(samples)
    assert result.verdict.value == "INCONCLUSIVE"
    assert result.max_drawdown_pct is None


def test_a04_external_flow_and_non_finite_are_inconclusive() -> None:
    base = int(WINDOW_START_UTC.timestamp() * 1000)
    flow = [
        EquitySample(time_ms=base, equity=Decimal("100")),
        EquitySample(time_ms=base + MS_PER_DAY, equity=Decimal("120"), external_flow=True),
    ]
    result = peak_drawdown(flow)
    assert result.verdict.value == "INCONCLUSIVE"
    assert any("external cash flow" in problem for problem in result.problems)

    with pytest.raises(ValueError, match="must be finite"):
        parse_sample({"time": base, "equity": "NaN"})


def test_a04_empty_window_is_inconclusive_not_zero() -> None:
    result = peak_drawdown([])
    assert result.verdict.value == "INCONCLUSIVE"
    assert result.max_drawdown_pct is None


# --------------------------------------------------------------------------- A08


def _run_payload() -> dict:
    return {
        "run_id": "a08-synthetic",
        "mode": "dry_run",
        "network": "testnet",
        "generated_at": "2026-11-09T00:00:00+00:00",
    }


def test_a08_json_report_requires_and_marks_data_kind() -> None:
    text = render_json(
        run=_run_payload(),
        data_kind="synthetic",
        acceptance=[{"id": "S07-A08", "status": "PASS", "reason": "synthetic contract"}],
        metrics={"max_drawdown_pct": "10.0"},
    )
    payload = json.loads(text)
    assert payload["data_kind"] == "synthetic"
    assert payload["official_result"] is False
    assert "合成データ" in payload["disclaimer"]
    assert payload["acceptance"][0]["status"] == "PASS"  # 試験アサーションのみ


def test_a08_markdown_report_marks_synthetic_and_mixed_as_non_official() -> None:
    synthetic = render_markdown(
        run=_run_payload(),
        data_kind="synthetic",
        acceptance={"S07-A08": "PASS"},
        metrics={},
    )
    assert synthetic.startswith("# S-07 offline verification report (SYNTHETIC)")
    assert "合成データ" in synthetic
    assert "official_result: false" in synthetic

    mixed = render_markdown(run=_run_payload(), data_kind="mixed")
    assert "MIXED" in mixed
    assert "運用成績として使用できない" in mixed


@pytest.mark.parametrize("bad", [None, "", "production", "SYNTHETIC", 1])
def test_a08_missing_or_unknown_data_kind_is_rejected(bad) -> None:
    with pytest.raises(DataKindError):
        render_json(run=_run_payload(), data_kind=bad)  # type: ignore[arg-type]


def test_a08_acceptance_pass_never_becomes_an_operating_pass() -> None:
    payload = build_payload(
        run=_run_payload(),
        data_kind="synthetic",
        acceptance=[{"id": "S07-A01", "status": "PASS", "reason": "assertion"}],
    )
    assert payload["official_result"] is False
    assert "operating result" in payload["official_result_reason"]
    assert payload["acceptance"][0]["id"] == "S07-A01"


# --------------------------------------------------------------------------- A09


def _metrics(**overrides) -> CriteriaMetrics:
    base = dict(
        completed_round_trips=MIN_COMPLETED_ROUND_TRIPS,
        net_pnl=Decimal("0.01"),
        max_peak_drawdown_pct=MAX_PEAK_DRAWDOWN_PCT,
        slippage_median_bp=MAX_SLIPPAGE_MEDIAN_BP,
        all_fills_recorded=True,
        daily_end_flat=True,
        all_days_lock_recorded=True,
        unfilled_residual=0,
    )
    base.update(overrides)
    return CriteriaMetrics(**base)


def test_a09_frozen_constants_are_unchanged() -> None:
    assert MIN_COMPLETED_ROUND_TRIPS == 8
    assert Decimal("10") == MAX_PEAK_DRAWDOWN_PCT
    assert Decimal("5") == MAX_SLIPPAGE_MEDIAN_BP
    assert WINDOW_DAYS == 28
    assert dt.datetime(2026, 11, 4, tzinfo=dt.UTC) == WINDOW_START_UTC
    assert dt.datetime(2026, 12, 2, tzinfo=dt.UTC) == WINDOW_END_UTC
    assert OFFICIAL_VERDICT_HELD == "HELD_PENDING_ADJUDICATION"


def test_a09_boundary_equalities_follow_the_frozen_comparators() -> None:
    # ① 8 以上。8 未満は INCONCLUSIVE（FAIL ではない）。
    assert evaluate_frozen_criteria(_metrics()).candidate_verdict is Verdict.PASS
    assert (
        evaluate_frozen_criteria(_metrics(completed_round_trips=7)).candidate_verdict
        is Verdict.INCONCLUSIVE
    )
    assert (
        evaluate_frozen_criteria(_metrics(completed_round_trips=0)).candidate_verdict
        is Verdict.INCONCLUSIVE
    )
    # ② net > 0 は厳密不等号。0 は FAIL。
    assert (
        evaluate_frozen_criteria(_metrics(net_pnl=Decimal("0"))).candidate_verdict is Verdict.FAIL
    )
    # ③ 10% は等号合格、10.01% は FAIL。
    assert evaluate_frozen_criteria(_metrics()).candidate_verdict is Verdict.PASS
    assert (
        evaluate_frozen_criteria(_metrics(max_peak_drawdown_pct=Decimal("10.01"))).candidate_verdict
        is Verdict.FAIL
    )
    # ④ 5bp は等号合格、5.01bp は FAIL。
    assert evaluate_frozen_criteria(_metrics()).candidate_verdict is Verdict.PASS
    assert (
        evaluate_frozen_criteria(_metrics(slippage_median_bp=Decimal("5.01"))).candidate_verdict
        is Verdict.FAIL
    )
    # ⑤ 未約定残留 0 は等号合格、1 は FAIL。
    assert evaluate_frozen_criteria(_metrics()).candidate_verdict is Verdict.PASS
    assert evaluate_frozen_criteria(_metrics(unfilled_residual=1)).candidate_verdict is Verdict.FAIL
    # 欠損は 0 補完せず INCONCLUSIVE。
    assert (
        evaluate_frozen_criteria(_metrics(all_fills_recorded=False)).candidate_verdict
        is Verdict.INCONCLUSIVE
    )
    assert (
        evaluate_frozen_criteria(_metrics(max_peak_drawdown_pct=None)).candidate_verdict
        is Verdict.INCONCLUSIVE
    )


def test_a09_official_verdict_is_held_until_window_semantics_are_adjudicated() -> None:
    verdict = evaluate_frozen_criteria(_metrics())
    assert verdict.candidate_verdict is Verdict.PASS
    assert verdict.official_verdict == OFFICIAL_VERDICT_HELD
    assert "window" in verdict.official_reason


def test_a09_window_is_half_open_and_endpoint_difference_is_flagged() -> None:
    start = int(WINDOW_START_UTC.timestamp() * 1000)
    end = int(WINDOW_END_UTC.timestamp() * 1000)
    assert in_window(start) is True
    assert in_window(end) is False
    assert classify_window(end) == "at_or_after_end"
    assert endpoint_sensitive(end) is True
    assert endpoint_sensitive(start) is False


def test_a09_old_1103_row_is_excluded_from_the_frozen_window() -> None:
    batch = load_batch(json.loads(BARS.read_text(encoding="utf-8")))
    partition = partition_by_window(batch.accepted)
    old_row = next(bar for bar in batch.accepted if bar.t == 1793664000000)  # 2026-11-03
    assert old_row.day_start_utc.date() == dt.date(2026, 11, 3)
    assert old_row in partition.warmup
    assert old_row not in partition.active
    assert all(bar.t < int(WINDOW_START_UTC.timestamp() * 1000) for bar in partition.warmup)
