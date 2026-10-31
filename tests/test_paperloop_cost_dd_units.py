"""Regression net for the cost / max-drawdown / comparison / weekly paths.

Card t_706a031c. ``src/`` is NOT changed here: this file pins what the current
implementation computes at its boundaries, and — just as important — the cases
where it returns ``None`` with a reason instead of a number. Every test name
states the case it pins, and every expected number is hand-derived next to the
assertion so a future change that "moves the number" has to argue with the
derivation.

Surfaces under test (arguments and boundaries, as they are today):

``_cost_accumulators(root, book, equity) -> (cum_cost, cum_turnover, equity_gross)``
  * only rows whose ``book`` matches; ``fill`` contributes ``cost_assumed`` and
    ``abs(traded_notional)``; a missing field reads as 0.0;
  * ``mtm`` closes one step: ``gross *= (equity + cost_since_prev_mtm) / prev``;
  * a step is skipped when the previous equity is ``0``/negative (no division);
  * the ``equity`` ARGUMENT is the step for the row being written now, so fills
    after the last mtm row land in that final step;
  * costs charged before the first mtm row are added back at face value.

``_max_drawdown_since(marks, base_day)``
  * window = marks with ``day >= base_day``; empty window -> ``None``;
  * running peak STARTS at the baseline day's equity (pre-baseline highs are
    outside the window);
  * no drawdown -> ``0.0`` (a number), not ``None`` (unknown);
  * peak ``<= 0`` cannot be divided by and contributes nothing.

``_comparison_line(root, book, bench, state_by_book)`` / ``_dd_vs_benchmark_ratio``
  * every numeric field is ``None`` while the comparison cannot be made, with
    ``comparison_reason``; ``dd_ratio_reason`` explains a missing ratio;
  * baseline = earliest mtm date BOTH books carry on/after the benchmark's
    first fill date;
  * ratio needs both drawdowns to be negative.

``_week_return(marks, window_start, week_end)``
  * ``(week_return, base_label, as_of, reason)``; window is inclusive on both
    ends; a mark after ``week_end`` is ignored; a hole in the week is not
    carried forward.

``_week_shortfalls(marks, window_start, week_end)``
  * week = the row's own ``recorded_at`` date; out-of-window rows are skipped
    silently, unusable ones are returned as ``{reason, ...}`` records.

``weekly_summary(data_dir, week_end)``
  * week-scoped ``shortfall_*`` columns + ``week_return`` vs ``cum_return``;
  * per-book comparison lines and the legacy top-level ``vs_benchmark_pp_cum``;
  * append-only: the same ``week_end`` can be written twice.

Units are the module's own: equity / drawdown / ``*_pp`` inputs are fractions,
``*_pp`` outputs are a fraction times 100, ``*_bps`` are basis points as given,
``dd_vs_benchmark_ratio`` is a plain ratio. The module carries NO currency and
NO FX conversion: a currency label on a row changes nothing (pinned below as a
known gap, prior art ``docs/PAPERLOOP_MONEY_REVIEW_20261023.md`` §C-11).
"""

from __future__ import annotations

import hashlib
import inspect
import json
import math
import shutil
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from yowayowa import paperloop as pl

DEFAULT_TS = "2026-10-27T00:20:00+00:00"


# --- journal fixtures ---------------------------------------------------------


def _data_dir(tmp_path: Path, name: str = "data") -> Path:
    data_dir = tmp_path / name
    pl.loop_root(data_dir).mkdir(parents=True, exist_ok=True)
    return data_dir


def _append(root: Path, **row: Any) -> None:
    """Append one hand-built journal row (seq / prev_hash / row_hash handled)."""

    payload: dict[str, Any] = {"recorded_at": DEFAULT_TS}
    payload.update(row)
    pl._append_row(root, payload)  # type: ignore[arg-type]


def _mtm(
    root: Path,
    book: str,
    day: str,
    equity: float,
    *,
    drawdown: float = 0.0,
    peak: float = 1.0,
    **extra: Any,
) -> None:
    row: dict[str, Any] = {
        "event": "mtm",
        "book": book,
        "mtm_date": day,
        "equity": equity,
        "cash": 0.0,
        "drawdown": drawdown,
        "peak_equity": peak,
    }
    row.update(extra)
    _append(root, **row)


def _fill(
    root: Path,
    book: str,
    *,
    cost: float | None = None,
    notional: float | None = 0.25,
    day: str = "2026-10-21",
    asset: str = "SPY",
    **extra: Any,
) -> None:
    row: dict[str, Any] = {
        "event": "fill",
        "book": book,
        "asset": asset,
        "side": "buy",
        "paper_fill_price": 100.0,
        "paper_fill_bar_open_ts": f"{day}T13:30:00+00:00",
        "units_after": 0.25,
        "cash_after": 0.0,
    }
    if cost is not None:
        row["cost_assumed"] = cost
    if notional is not None:
        row["traded_notional"] = notional
    row.update(extra)
    _append(root, **row)


def _mark(
    root: Path,
    day: str,
    *,
    book: str = pl.S01_BOOK,
    asset: str = "BTC",
    shortfall: float | str | None = 0.0,
    with_key: bool = True,
) -> None:
    row: dict[str, Any] = {
        "event": "mark",
        "book": book,
        "recorded_at": f"{day}T00:20:00+00:00",
        "decision_id": f"{book}:{asset}:{day}",
        "asset": asset,
        "bt_fill_price": 100.0,
        "recompute_match": True,
        "turnover": 0.0,
    }
    if with_key:
        row["shortfall_bps"] = shortfall
    _append(root, **row)


def _state(equity: float, peak: float | None = None) -> dict[str, Any]:
    return {
        "units": {},
        "cash": 0.0,
        "equity": equity,
        "peak": equity if peak is None else peak,
        "halted": False,
        "stopped": False,
    }


def _weekly(data_dir: Path, week_end: date, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """weekly_summary without the Q1-Q7 panel (out of scope for this card)."""

    monkeypatch.setattr(pl, "_eval_blocks", lambda *a, **k: {"stub": True})
    return pl.weekly_summary(data_dir, week_end)


# --- argument surface ---------------------------------------------------------


def test_signatures_and_defaults_of_the_four_paths_are_pinned() -> None:
    """Renaming/moving a parameter must fail loudly, not silently re-bind.

    The module's call sites pass these positionally in places, so the names and
    order below are part of the contract this file is a net for.
    """

    def params(func: Any) -> list[str]:
        return list(inspect.signature(func).parameters)

    assert params(pl._cost_accumulators) == ["root", "book", "equity"]
    assert params(pl._max_drawdown_since) == ["marks", "base_day"]
    assert params(pl._comparison_line) == ["root", "book", "bench", "state_by_book", "as_of"]
    assert (
        inspect.signature(pl._comparison_line).parameters["as_of"].kind
        is inspect.Parameter.KEYWORD_ONLY
    )
    assert params(pl._dd_vs_benchmark_ratio) == ["book_dd", "bench_dd"]
    assert params(pl._week_return) == ["marks", "window_start", "week_end"]
    assert params(pl._week_shortfalls) == ["marks", "window_start", "week_end"]
    assert params(pl.weekly_summary) == ["data_dir", "week_end"]
    # weekly_summary's week_end is required (no "today" default inside it)
    assert (
        inspect.signature(pl.weekly_summary).parameters["week_end"].default
        is inspect.Parameter.empty
    )


# --- _cost_accumulators: one charge, one book, one add-back -------------------


def test_cost_is_charged_once_and_only_for_the_requested_book(tmp_path: Path) -> None:
    """Each fill's ``cost_assumed`` enters ``cum_cost`` once and is added back once.

    Book A charges 0.01 on 1.0 of equity and ends the step at 0.99: the add-back
    restores exactly that 0.01, so gross returns to 1.0 — not 1.01 (charged
    twice) and not 1.005 (half). Book C's 0.50 cost and 9.00 notional must not
    appear anywhere in A's accumulator.
    """

    root = pl.loop_root(_data_dir(tmp_path))
    _mtm(root, pl.S01_BOOK, "2026-10-20", 1.0)
    _fill(root, pl.S01_BOOK, cost=0.01, notional=0.30, day="2026-10-20")
    _fill(root, pl.BOOK_C_BOOK, cost=0.50, notional=9.00, day="2026-10-20")
    _mtm(root, pl.BOOK_C_BOOK, "2026-10-20", 1.0)
    _mtm(root, pl.S01_BOOK, "2026-10-21", 0.99)

    cum_cost, cum_turnover, gross = pl._cost_accumulators(root, pl.S01_BOOK, 0.99)
    assert cum_cost == pytest.approx(0.01)
    assert cum_turnover == pytest.approx(0.30)
    assert gross == pytest.approx(1.0, abs=1e-12)


def test_cost_charged_before_the_first_mtm_is_added_back_at_face_value(tmp_path: Path) -> None:
    """Pre-first-mtm costs are added back un-grown (documented approximation).

    Daily resolution cannot reconstruct intra-day growth, so a 0.02 cost spent
    before the first mark keeps its face value in the first step: with the first
    mark at 0.50 the gross chain reads 0.52, not 0.51 (the cost grown by the
    -50% market move) and not 0.50 (the cost silently dropped).
    """

    root = pl.loop_root(_data_dir(tmp_path, "face"))
    _fill(root, pl.S01_BOOK, cost=0.02, day="2026-10-20")
    _mtm(root, pl.S01_BOOK, "2026-10-21", 0.50)
    cum_cost, _turnover, gross = pl._cost_accumulators(root, pl.S01_BOOK, 0.50)
    assert cum_cost == pytest.approx(0.02)
    assert gross == pytest.approx(0.52, abs=1e-12)
    assert gross != pytest.approx(0.51, abs=1e-6)

    root_at_one = pl.loop_root(_data_dir(tmp_path, "at_one"))
    _mtm(root_at_one, pl.S01_BOOK, "2026-10-20", 1.0)
    _fill(root_at_one, pl.S01_BOOK, cost=0.02, day="2026-10-20")
    _mtm(root_at_one, pl.S01_BOOK, "2026-10-21", 0.98)
    _cum, _turn, gross_flat = pl._cost_accumulators(root_at_one, pl.S01_BOOK, 0.98)
    assert gross_flat == pytest.approx(1.0, abs=1e-12)


def test_a_zero_equity_mark_zeroes_the_gross_chain_permanently(tmp_path: Path) -> None:
    """prev_equity == 0 skips the step — and the product can never recover.

    The guard is there to avoid division by zero, but ``equity_gross`` is a
    product: once a mark reads 0.0 the chain is 0.0 forever, even after equity
    is priced again. Pinned AS-IS (see the report): a mispriced 0 valuation
    (the C-04 class) would silently kill ``cost_drag_pp`` for every later row.
    """

    root = pl.loop_root(_data_dir(tmp_path))
    _mtm(root, pl.S01_BOOK, "2026-10-20", 0.0)
    _mtm(root, pl.S01_BOOK, "2026-10-21", 0.50)
    _cum, _turn, gross = pl._cost_accumulators(root, pl.S01_BOOK, 0.50)
    assert gross == 0.0
    assert math.isfinite(gross) and not math.isnan(gross)
    # the row that is being written now does not restart the chain either
    _cum2, _turn2, gross_next = pl._cost_accumulators(root, pl.S01_BOOK, 0.60)
    assert gross_next == 0.0


def test_final_step_uses_the_equity_argument_and_that_steps_costs_only(tmp_path: Path) -> None:
    """Fills after the last mtm row belong to the final step, exactly once.

    Journal: mtm 1.00, fill 0.01, mtm 1.05, fill 0.02, then the mtm row being
    written has equity 1.03. The 0.01 is added back in the step that closed at
    1.05 and the 0.02 in the final step, giving gross 1.06 and the identity
    gross/equity == (1 + 0.01/1.05) * (1 + 0.02/1.03).
    """

    root = pl.loop_root(_data_dir(tmp_path))
    _mtm(root, pl.S01_BOOK, "2026-10-20", 1.0)
    _fill(root, pl.S01_BOOK, cost=0.01, day="2026-10-20")
    _mtm(root, pl.S01_BOOK, "2026-10-21", 1.05)
    _fill(root, pl.S01_BOOK, cost=0.02, day="2026-10-21")

    cum_cost, _turn, gross = pl._cost_accumulators(root, pl.S01_BOOK, 1.03)
    assert cum_cost == pytest.approx(0.03)
    assert gross == pytest.approx(1.06, rel=1e-12)
    assert gross / 1.03 == pytest.approx((1 + 0.01 / 1.05) * (1 + 0.02 / 1.03), rel=1e-12)
    # if either cost were added back twice the product would be ~1.09
    assert gross < 1.07


def test_missing_cost_fields_read_as_zero_and_turnover_is_absolute(tmp_path: Path) -> None:
    """A fill row without ``cost_assumed`` understates cost silently (AS-IS).

    ``float(row.get("cost_assumed") or 0.0)`` turns a missing column into a
    free trade, and a negative ``traded_notional`` (a sell) into positive
    turnover. Pinned as the current contract; the "missing is not zero"
    principle is not applied on this path (reported, not fixed).
    """

    root = pl.loop_root(_data_dir(tmp_path))
    _mtm(root, pl.S01_BOOK, "2026-10-20", 1.0)
    _fill(root, pl.S01_BOOK, cost=None, notional=None, day="2026-10-20")
    _fill(root, pl.S01_BOOK, cost=0.0, notional=-0.40, day="2026-10-20")
    _mtm(root, pl.S01_BOOK, "2026-10-21", 1.0)

    cum_cost, cum_turnover, gross = pl._cost_accumulators(root, pl.S01_BOOK, 1.0)
    assert cum_cost == 0.0
    assert cum_turnover == pytest.approx(0.40)
    assert gross == pytest.approx(1.0, abs=1e-12)


# --- _max_drawdown_since: the window, not the inception peak ------------------


def test_pre_baseline_peak_is_not_divided_into_the_window(tmp_path: Path) -> None:
    """A high made before the shared baseline must not leak into the window DD.

    Book topped at 1.20 before the baseline, then fell to 1.00. Measured from
    the baseline (1.05) the window drawdown is -4.76%; the inception-based
    number a reviewer would compute from the journal's own ``drawdown`` column
    is -16.67% — the whole reason this helper exists.
    """

    marks = {date(2026, 10, 20): 1.20, date(2026, 10, 21): 1.05, date(2026, 10, 22): 1.00}
    dd = pl._max_drawdown_since(marks, date(2026, 10, 21))
    assert dd == pytest.approx(1.00 / 1.05 - 1.0, rel=1e-12)
    assert dd == pytest.approx(-0.04761904761904756, rel=1e-12)
    assert abs(dd - (1.00 / 1.20 - 1.0)) > 0.1


def test_baseline_day_is_inside_the_window_even_when_it_is_the_peak(tmp_path: Path) -> None:
    marks = {date(2026, 10, 19): 0.90, date(2026, 10, 20): 1.00, date(2026, 10, 21): 0.95}
    dd = pl._max_drawdown_since(marks, date(2026, 10, 20))
    assert dd == pytest.approx(0.95 / 1.00 - 1.0, rel=1e-12)
    # the day before the baseline (0.90) is neither peak nor window start
    assert dd > -1.0


def test_no_mark_reaching_the_baseline_is_unknown_not_zero() -> None:
    marks = {date(2026, 10, 10): 1.10, date(2026, 10, 11): 1.05}
    assert pl._max_drawdown_since(marks, date(2026, 10, 20)) is None
    assert pl._max_drawdown_since({}, date(2026, 10, 20)) is None


def test_a_monotonic_rise_reports_zero_drawdown_not_none() -> None:
    """0.0 means "measured, never fell"; None means "cannot measure"."""

    marks = {date(2026, 10, i): 1.0 + 0.01 * i for i in range(10, 15)}
    dd = pl._max_drawdown_since(marks, date(2026, 10, 10))
    assert dd is not None
    assert dd == 0.0


def test_single_mark_at_the_baseline_is_a_zero_drawdown() -> None:
    marks = {date(2026, 10, 22): 1.07}
    assert pl._max_drawdown_since(marks, date(2026, 10, 22)) == 0.0


def test_non_positive_peaks_are_skipped_instead_of_dividing_by_zero() -> None:
    zero_base = {date(2026, 10, 20): 0.0, date(2026, 10, 21): -0.50}
    assert pl._max_drawdown_since(zero_base, date(2026, 10, 20)) == 0.0
    negative = {date(2026, 10, 20): -0.50, date(2026, 10, 21): -1.00}
    assert pl._max_drawdown_since(negative, date(2026, 10, 20)) == 0.0


# --- _dd_vs_benchmark_ratio: only two negative drawdowns make a ratio ---------


def test_ratio_is_none_when_either_side_is_unknown() -> None:
    assert pl._dd_vs_benchmark_ratio(None, -0.05) is None
    assert pl._dd_vs_benchmark_ratio(-0.05, None) is None
    assert pl._dd_vs_benchmark_ratio(None, None) is None


def test_ratio_is_none_when_the_benchmark_has_no_drawdown_to_divide_by() -> None:
    assert pl._dd_vs_benchmark_ratio(-0.10, 0.0) is None  # 0 would fabricate inf
    assert pl._dd_vs_benchmark_ratio(-0.10, 0.02) is None  # benchmark up: no DD


def test_a_book_that_never_fell_gets_a_real_zero_ratio() -> None:
    """0.0 is a measurement ("book DD = 0"), so it must not collapse to None."""

    ratio = pl._dd_vs_benchmark_ratio(0.0, -0.05)
    assert ratio is not None
    assert ratio == 0.0


def test_ratio_divides_two_negative_drawdowns() -> None:
    assert pl._dd_vs_benchmark_ratio(-0.03, -0.06) == pytest.approx(0.5, rel=1e-12)
    # the §4 gate threshold (book DD <= benchmark DD x 0.6) reads this scalar
    assert pl._dd_vs_benchmark_ratio(-0.06, -0.06) == pytest.approx(1.0, rel=1e-12)


# --- _comparison_line: one shared baseline, reasons instead of zeros ----------


def test_comparison_line_reports_benchmark_has_no_fill(tmp_path: Path) -> None:
    root = pl.loop_root(_data_dir(tmp_path))
    for day in ("2026-10-21", "2026-10-22", "2026-10-23"):
        _mtm(root, pl.BOOK_C_BOOK, day, 1.01)
        _mtm(root, pl.BENCH_EQUITY_BOOK, day, 1.0)  # in cash, never filled
    line = pl._comparison_line(
        root,
        pl.BOOK_C_BOOK,
        pl.BENCH_EQUITY_BOOK,
        {
            pl.BOOK_C_BOOK: _state(1.01),
            pl.BENCH_EQUITY_BOOK: _state(1.0),
        },
    )
    assert line["comparison_reason"] == "benchmark_has_no_fill"
    assert line["comparison_start"] is None
    for key in (
        "cum_pnl",
        "max_drawdown",
        "benchmark_cum_pnl",
        "benchmark_max_drawdown",
        "vs_benchmark_pp_cum",
        "dd_vs_benchmark_ratio",
    ):
        assert line[key] is None, key


def test_comparison_line_reports_no_shared_mark_after_the_benchmarks_fill(tmp_path: Path) -> None:
    """A benchmark that filled but never shares an mtm date cannot be compared."""

    root = pl.loop_root(_data_dir(tmp_path))
    _fill(root, pl.BENCH_EQUITY_BOOK, day="2026-10-21")
    _mtm(root, pl.BOOK_C_BOOK, "2026-10-22", 1.02)
    _mtm(root, pl.BENCH_EQUITY_BOOK, "2026-10-23", 1.00)  # no overlapping date
    line = pl._comparison_line(
        root,
        pl.BOOK_C_BOOK,
        pl.BENCH_EQUITY_BOOK,
        {pl.BOOK_C_BOOK: _state(1.02), pl.BENCH_EQUITY_BOOK: _state(1.0)},
    )
    assert line["comparison_reason"] == "no_shared_mark_since_benchmark_first_fill"
    assert line["vs_benchmark_pp_cum"] is None
    assert line["comparison_start"] is None


def test_comparison_line_refuses_a_zero_baseline_or_a_missing_state(tmp_path: Path) -> None:
    root_zero = pl.loop_root(_data_dir(tmp_path, "zero"))
    _fill(root_zero, pl.BENCH_EQUITY_BOOK, day="2026-10-21")
    _mtm(root_zero, pl.BOOK_C_BOOK, "2026-10-21", 0.0)
    _mtm(root_zero, pl.BENCH_EQUITY_BOOK, "2026-10-21", 1.0)
    line = pl._comparison_line(
        root_zero,
        pl.BOOK_C_BOOK,
        pl.BENCH_EQUITY_BOOK,
        {pl.BOOK_C_BOOK: _state(0.0), pl.BENCH_EQUITY_BOOK: _state(1.0)},
    )
    assert line["comparison_reason"] == "baseline_unusable"
    assert line["cum_pnl"] is None

    root_state = pl.loop_root(_data_dir(tmp_path, "state"))
    _fill(root_state, pl.BENCH_EQUITY_BOOK, day="2026-10-21")
    _mtm(root_state, pl.BOOK_C_BOOK, "2026-10-21", 1.0)
    _mtm(root_state, pl.BENCH_EQUITY_BOOK, "2026-10-21", 1.0)
    line = pl._comparison_line(
        root_state,
        pl.BOOK_C_BOOK,
        pl.BENCH_EQUITY_BOOK,
        {pl.BOOK_C_BOOK: _state(1.0)},  # benchmark state absent
    )
    assert line["comparison_reason"] == "baseline_unusable"
    assert line["benchmark_cum_pnl"] is None


def test_comparison_line_starts_at_the_first_shared_mark_after_the_benchmark_fill(
    tmp_path: Path,
) -> None:
    """Pre-fill shared marks are excluded even when both books carry them.

    Both books are marked on 10-20 (before the benchmark filled on 10-22), so a
    naive "earliest shared date" would start there and credit the book with the
    +10% it made while the benchmark was in cash. The baseline must be 10-22.
    """

    root = pl.loop_root(_data_dir(tmp_path))
    _mtm(root, pl.S01_BOOK, "2026-10-20", 1.10)
    _mtm(root, pl.BENCH_CRYPTO_BOOK, "2026-10-20", 1.00)
    _fill(root, pl.BENCH_CRYPTO_BOOK, day="2026-10-22", asset="BTC")
    _mtm(root, pl.S01_BOOK, "2026-10-22", 1.00)
    _mtm(root, pl.BENCH_CRYPTO_BOOK, "2026-10-22", 1.00)
    _mtm(root, pl.S01_BOOK, "2026-10-23", 1.05)
    _mtm(root, pl.BENCH_CRYPTO_BOOK, "2026-10-23", 1.02)

    line = pl._comparison_line(
        root,
        pl.S01_BOOK,
        pl.BENCH_CRYPTO_BOOK,
        {pl.S01_BOOK: _state(1.05), pl.BENCH_CRYPTO_BOOK: _state(1.02)},
    )
    assert line["comparison_start"] == "2026-10-22"
    assert line["comparison_reason"] is None
    assert line["cum_pnl"] == pytest.approx(1.05 / 1.00 - 1.0, rel=1e-12)
    assert line["benchmark_cum_pnl"] == pytest.approx(1.02 / 1.00 - 1.0, rel=1e-12)
    assert line["vs_benchmark_pp_cum"] == pytest.approx(3.0, rel=1e-12)
    # the since-inception book number the baseline rule exists to avoid
    assert line["cum_pnl"] != pytest.approx(1.05 / 1.10 - 1.0, rel=1e-6)


def test_comparison_line_measures_both_drawdowns_inside_the_shared_window(tmp_path: Path) -> None:
    root = pl.loop_root(_data_dir(tmp_path))
    _fill(root, pl.BENCH_CRYPTO_BOOK, day="2026-10-21", asset="BTC")
    _mtm(root, pl.S01_BOOK, "2026-10-21", 1.00)
    _mtm(root, pl.BENCH_CRYPTO_BOOK, "2026-10-21", 1.00)
    _mtm(root, pl.S01_BOOK, "2026-10-22", 0.97)
    _mtm(root, pl.BENCH_CRYPTO_BOOK, "2026-10-22", 0.94)
    _mtm(root, pl.S01_BOOK, "2026-10-23", 0.95)
    _mtm(root, pl.BENCH_CRYPTO_BOOK, "2026-10-23", 0.96)

    line = pl._comparison_line(
        root,
        pl.S01_BOOK,
        pl.BENCH_CRYPTO_BOOK,
        {pl.S01_BOOK: _state(0.95), pl.BENCH_CRYPTO_BOOK: _state(0.96)},
    )
    assert line["max_drawdown"] == pytest.approx(-0.05, rel=1e-12)
    assert line["benchmark_max_drawdown"] == pytest.approx(-0.06, rel=1e-12)
    assert line["cum_pnl"] == pytest.approx(-0.05, rel=1e-12)
    assert line["benchmark_cum_pnl"] == pytest.approx(-0.04, rel=1e-12)
    assert line["vs_benchmark_pp_cum"] == pytest.approx(-1.0, rel=1e-12)
    assert line["dd_vs_benchmark_ratio"] == pytest.approx(0.05 / 0.06, rel=1e-12)
    assert line["dd_ratio_reason"] is None


def test_comparison_line_with_one_shared_row_reports_zero_pp_and_no_ratio(tmp_path: Path) -> None:
    """The minimum sample: the benchmark's fill day is the only shared mark.

    ``cum_pnl`` is 0.0 here because the book's latest mark IS the baseline, so
    the line reads "no difference yet" with ``comparison_reason=None`` — the
    ratio still refuses (nothing fell on either side).
    """

    root = pl.loop_root(_data_dir(tmp_path))
    _fill(root, pl.BENCH_CRYPTO_BOOK, day="2026-10-22", asset="BTC")
    _mtm(root, pl.S01_BOOK, "2026-10-22", 1.10)
    _mtm(root, pl.BENCH_CRYPTO_BOOK, "2026-10-22", 1.00)

    line = pl._comparison_line(
        root,
        pl.S01_BOOK,
        pl.BENCH_CRYPTO_BOOK,
        {pl.S01_BOOK: _state(1.10), pl.BENCH_CRYPTO_BOOK: _state(1.00)},
    )
    assert line["comparison_start"] == "2026-10-22"
    assert line["cum_pnl"] == 0.0
    assert line["benchmark_cum_pnl"] == 0.0
    assert line["vs_benchmark_pp_cum"] == 0.0
    assert line["max_drawdown"] == 0.0
    assert line["dd_vs_benchmark_ratio"] is None
    assert line["dd_ratio_reason"] == "benchmark_drawdown_not_negative"


def test_book_and_benchmark_drawdown_missing_branches_are_unreachable_here(
    tmp_path: Path,
) -> None:
    """``*_drawdown_missing`` cannot fire: the baseline day carries both marks.

    Because ``base_day`` is chosen from the intersection of both mark sets, both
    ``_max_drawdown_since`` calls always see at least that day and return a
    number (0.0 at worst). The two reason branches exist but are dead through
    this entry point — pinned so a future refactor has to decide about them
    deliberately instead of inheriting them.
    """

    root = pl.loop_root(_data_dir(tmp_path))
    _fill(root, pl.BENCH_CRYPTO_BOOK, day="2026-10-22", asset="BTC")
    _mtm(root, pl.S01_BOOK, "2026-10-22", 1.0)
    _mtm(root, pl.BENCH_CRYPTO_BOOK, "2026-10-22", 1.0)
    _mtm(root, pl.S01_BOOK, "2026-10-23", 0.99)
    _mtm(root, pl.BENCH_CRYPTO_BOOK, "2026-10-23", 0.995)

    line = pl._comparison_line(
        root,
        pl.S01_BOOK,
        pl.BENCH_CRYPTO_BOOK,
        {pl.S01_BOOK: _state(0.99), pl.BENCH_CRYPTO_BOOK: _state(0.995)},
    )
    assert line["max_drawdown"] is not None
    assert line["benchmark_max_drawdown"] is not None
    assert line["dd_ratio_reason"] not in {
        "book_drawdown_missing",
        "benchmark_drawdown_missing",
    }
    assert line["dd_vs_benchmark_ratio"] is not None


# --- _week_return: the week's move, bounded by week_end -----------------------


def test_week_return_with_no_marks_at_all_is_unknown_with_a_reason() -> None:
    result = pl._week_return({}, date(2026, 10, 19), date(2026, 10, 25))
    assert result == (None, None, None, "no_mark_at_or_before_week_end")


def test_week_return_ignores_marks_after_week_end() -> None:
    """A mark dated after week_end must not price a row generated for it."""

    marks = {date(2026, 10, 28): 1.10}
    result = pl._week_return(marks, date(2026, 10, 19), date(2026, 10, 25))
    assert result == (None, None, None, "no_mark_at_or_before_week_end")


def test_week_return_with_only_stale_marks_has_no_mark_in_window() -> None:
    marks = {date(2026, 10, 5): 1.05}
    week_return, base, as_of, reason = pl._week_return(
        marks, date(2026, 10, 19), date(2026, 10, 25)
    )
    assert week_return is None
    assert base is None and as_of is None
    assert reason == "no_mark_in_window"


def test_week_return_refuses_a_zero_base() -> None:
    marks = {date(2026, 10, 12): 0.0, date(2026, 10, 20): 1.0}
    week_return, base, as_of, reason = pl._week_return(
        marks, date(2026, 10, 19), date(2026, 10, 25)
    )
    assert week_return is None
    assert base == "2026-10-12"
    assert as_of == "2026-10-20"
    assert reason == "base_unusable"


def test_week_return_window_is_inclusive_on_both_ends() -> None:
    marks = {date(2026, 10, 19): 1.00, date(2026, 10, 25): 1.10}
    week_return, base, as_of, reason = pl._week_return(
        marks, date(2026, 10, 19), date(2026, 10, 25)
    )
    assert reason is None
    assert base == "inception"  # nothing before window_start -> 1.0 anchor
    assert as_of == "2026-10-25"
    assert week_return == pytest.approx(0.10, rel=1e-12)


def test_week_return_across_a_hole_uses_the_last_mark_inside_the_window() -> None:
    """Days without a mark are a gap, not a 0% day: no carry-forward is invented."""

    marks = {date(2026, 10, 12): 1.00, date(2026, 10, 20): 1.05, date(2026, 10, 24): 1.10}
    week_return, base, as_of, reason = pl._week_return(
        marks, date(2026, 10, 19), date(2026, 10, 25)
    )
    assert reason is None
    assert base == "2026-10-12"
    assert as_of == "2026-10-24"
    assert week_return == pytest.approx(1.10 / 1.00 - 1.0, rel=1e-12)
    # the 10-21..10-23 hole must not be filled with the 10-20 value
    assert week_return != pytest.approx(1.05 / 1.00 - 1.0, rel=1e-12)


def test_mtm_marks_keep_the_last_row_for_a_duplicated_date(tmp_path: Path) -> None:
    """Same-day duplicate mtm rows: the last one wins (dict overwrite, no sum)."""

    root = pl.loop_root(_data_dir(tmp_path))
    _mtm(root, pl.S01_BOOK, "2026-10-22", 1.00)
    _mtm(root, pl.S01_BOOK, "2026-10-22", 1.20)  # re-run later the same day
    marks = pl._mtm_marks(root, pl.S01_BOOK)
    assert marks == {date(2026, 10, 22): 1.20}
    # and the weekly week_return reads the duplicate day as one mark
    week_return, _base, as_of, _reason = pl._week_return(
        marks, date(2026, 10, 20), date(2026, 10, 25)
    )
    assert as_of == "2026-10-22"
    assert week_return == pytest.approx(0.20, rel=1e-12)


# --- _week_shortfalls: week scope, and every dropped row is named -------------


def test_shortfall_window_is_inclusive_and_out_of_window_rows_are_silent() -> None:
    marks: list[dict[str, Any]] = [
        {"recorded_at": "2026-10-19T00:00:00+00:00", "shortfall_bps": 10.0, "decision_id": "start"},
        {"recorded_at": "2026-10-25T23:59:00+00:00", "shortfall_bps": 20.0, "decision_id": "end"},
        {
            "recorded_at": "2026-10-18T23:59:00+00:00",
            "shortfall_bps": 99.0,
            "decision_id": "before",
        },
        {"recorded_at": "2026-10-26T00:00:00+00:00", "shortfall_bps": 99.0, "decision_id": "after"},
    ]
    values, errors = pl._week_shortfalls(marks, date(2026, 10, 19), date(2026, 10, 25))
    assert values == [10.0, 20.0]
    assert errors == []  # out-of-window is not an error, it is another week


def test_each_unusable_shortfall_row_is_named_with_its_reason() -> None:
    marks: list[dict[str, Any]] = [
        {"recorded_at": "2026-10-20T00:00:00+00:00", "decision_id": "missing"},
        {"recorded_at": "2026-10-20T00:00:00+00:00", "shortfall_bps": None, "decision_id": "null"},
        {"recorded_at": "2026-10-20T00:00:00+00:00", "shortfall_bps": True, "decision_id": "bool"},
        {"recorded_at": "2026-10-20T00:00:00+00:00", "shortfall_bps": "abc", "decision_id": "text"},
        {
            "recorded_at": "2026-10-20T00:00:00+00:00",
            "shortfall_bps": float("nan"),
            "decision_id": "nan",
        },
        {
            "recorded_at": "2026-10-20T00:00:00+00:00",
            "shortfall_bps": float("inf"),
            "decision_id": "inf",
        },
        {"recorded_at": "not-a-date", "shortfall_bps": 5.0, "decision_id": "bad-ts"},
        {"shortfall_bps": 5.0, "decision_id": "no-ts"},
    ]
    values, errors = pl._week_shortfalls(marks, date(2026, 10, 19), date(2026, 10, 25))
    assert values == []
    assert [error["reason"] for error in errors] == [
        "missing_shortfall_bps",
        "missing_shortfall_bps",
        "non_numeric_shortfall_bps",
        "non_numeric_shortfall_bps",
        "non_finite_shortfall_bps",
        "non_finite_shortfall_bps",
        "unparseable_recorded_at",
        "unparseable_recorded_at",
    ]
    assert [error["decision_id"] for error in errors] == [
        "missing",
        "null",
        "bool",
        "text",
        "nan",
        "inf",
        "bad-ts",
        "no-ts",
    ]
    # the record carries no fabricated value
    assert all("shortfall_bps" not in error for error in errors)


def test_a_numeric_string_is_accepted_while_a_bool_is_not() -> None:
    """Type handling is asymmetric (AS-IS): "12.5" counts, True does not.

    ``float(raw)`` coerces a numeric string into the gate statistics while
    ``isinstance(raw, bool)`` rejects a boolean. Pinned as the current contract.
    """

    marks: list[dict[str, Any]] = [
        {"recorded_at": "2026-10-20T00:00:00+00:00", "shortfall_bps": "12.5", "decision_id": "s"},
        {"recorded_at": "2026-10-21T00:00:00+00:00", "shortfall_bps": False, "decision_id": "b"},
    ]
    values, errors = pl._week_shortfalls(marks, date(2026, 10, 19), date(2026, 10, 25))
    assert values == [12.5]
    assert [error["reason"] for error in errors] == ["non_numeric_shortfall_bps"]


def test_duplicate_marks_on_one_day_are_counted_twice() -> None:
    """No dedupe on this path: n is the number of ROWS, not distinct executions.

    ``mark_up`` dedupes by ``decision_id``, so a duplicate can only arrive from
    a hand-written/legacy row or a different decision on the same asset+day.
    The statistics then weight that execution twice (pinned as-is; the weekly
    row also has no week_end dedupe — see the append-only test below).
    """

    marks: list[dict[str, Any]] = [
        {"recorded_at": "2026-10-20T00:00:00+00:00", "shortfall_bps": 10.0, "decision_id": "d1"},
        {"recorded_at": "2026-10-20T00:00:00+00:00", "shortfall_bps": 10.0, "decision_id": "d1"},
    ]
    values, errors = pl._week_shortfalls(marks, date(2026, 10, 19), date(2026, 10, 25))
    assert values == [10.0, 10.0]
    assert errors == []


def test_shortfall_bps_are_kept_in_basis_points_and_empty_is_empty() -> None:
    empty_values, empty_errors = pl._week_shortfalls([], date(2026, 10, 19), date(2026, 10, 25))
    assert (empty_values, empty_errors) == ([], [])
    # n < 2 has no upper bound -> None, never 0
    assert pl.shortfall_bps_mean_upper95([]) is None
    assert pl.shortfall_bps_mean_upper95([15.0]) is None

    marks: list[dict[str, Any]] = [
        {"recorded_at": "2026-10-20T00:00:00+00:00", "shortfall_bps": 15.0, "decision_id": "d"},
    ]
    values, _errors = pl._week_shortfalls(marks, date(2026, 10, 19), date(2026, 10, 25))
    assert values == [15.0]  # 15 bps stays 15.0, not 0.0015


# --- weekly_summary: the row the operator actually reads ---------------------


def test_weekly_row_keeps_comparison_null_while_the_benchmark_has_not_filled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Case (a): an un-contracted benchmark yields None + a reason, never 0.

    v1.8 (C-07): the top-level ``vs_benchmark_pp_cum`` is now the SAME
    shared-baseline definition as ``books[A].vs_benchmark_pp_cum``, so it is
    None here too. The old baseline-less difference (two 1.0 inception equities
    -> 0.0) survives under ``vs_benchmark_pp_cum_legacy_nobaseline``; before
    the fix that number sat under the shared name (prior art
    ``docs/PAPERLOOP_MONEY_REVIEW_20261023.md`` §C-07).
    """

    data_dir = _data_dir(tmp_path)
    root = pl.loop_root(data_dir)
    for day in ("2026-10-21", "2026-10-22", "2026-10-23"):
        _mtm(root, pl.BOOK_C_BOOK, day, 1.01)
        _mtm(root, pl.BENCH_EQUITY_BOOK, day, 1.00)

    out = _weekly(data_dir, date(2026, 10, 27), monkeypatch)
    book_c = out["books"][pl.BOOK_C_BOOK]
    assert book_c["benchmark_book"] == pl.BENCH_EQUITY_BOOK
    assert book_c["comparison_reason"] == "benchmark_has_no_fill"
    assert book_c["comparison_start"] is None
    for key in (
        "cum_pnl",
        "max_drawdown",
        "benchmark_cum_pnl",
        "benchmark_max_drawdown",
        "vs_benchmark_pp_cum",
        "dd_vs_benchmark_ratio",
    ):
        assert book_c[key] is None, key
    book_a = out["books"][pl.S01_BOOK]
    assert book_a["comparison_reason"] == "benchmark_has_no_fill"
    assert book_a["vs_benchmark_pp_cum"] is None
    # one name, one definition: the top level IS books[A]
    assert out["vs_benchmark_pp_cum"] == book_a["vs_benchmark_pp_cum"]
    assert out["vs_benchmark_pp_cum"] is None
    # the old baseline-less definition keeps its own name — and stays honest:
    # book A has no mark at/before week_end in this fixture, so the as-of
    # difference is unknown (the pre-fix key claimed a measured 0.0 here, from
    # two untouched 1.0 inception states)
    assert out["vs_benchmark_pp_cum_legacy_nobaseline"] is None
    # the append-only row is one valid JSON line
    lines = pl.summary_path(root).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["week_end"] == "2026-10-27"


def test_weekly_row_scopes_shortfall_statistics_to_the_week(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Case (c)+(d): only this week's marks, and a dropped row is counted.

    In-window: 10 and 20 bps; one in-window row without the column (excluded);
    one out-of-window row (99 bps, a different week). mean_signed/abs = 15.0,
    and the n=2 one-sided 95% bound is 15 + 1.6449 * 7.0711 / sqrt(2) = 23.2245.
    """

    data_dir = _data_dir(tmp_path)
    root = pl.loop_root(data_dir)
    _mark(root, "2026-10-21", shortfall=10.0)
    _mark(root, "2026-10-23", shortfall=20.0)
    _mark(root, "2026-10-12", shortfall=99.0)  # previous week, must not be read
    _mark(root, "2026-10-24", with_key=False)  # in-window but unusable

    out = _weekly(data_dir, date(2026, 10, 27), monkeypatch)
    book = out["books"][pl.S01_BOOK]
    assert book["shortfall_scope"] == "week"
    assert book["shortfall_window_start"] == "2026-10-21"
    assert book["shortfall_n"] == 2
    assert book["shortfall_n_excluded"] == 1
    assert book["shortfall_bps_mean_signed"] == pytest.approx(15.0, rel=1e-12)
    assert book["shortfall_bps_mean_abs"] == pytest.approx(15.0, rel=1e-12)
    assert book["shortfall_bps_mean_upper95"] == pytest.approx(23.2245, rel=1e-9)
    assert [error["reason"] for error in book["shortfall_errors"]] == ["missing_shortfall_bps"]


def test_weekly_row_week_return_and_cum_return_are_different_columns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Case (d): week_end bounds ``week_return`` AND the equity columns.

    week_end 2026-10-25 -> window 10-19..10-25. The 10-30 mark is outside the
    window, so ``week_return`` reads the 10-24 mark (+5%). Before v1.8 (C-05)
    ``cum_return`` / ``drawdown_now`` rode that 10-30 mark — a row labelled
    10-25 carrying a 10-30 valuation (-10% / -22.08%). They are now closed at
    the last mark on/before week_end too: +15.5% cumulative and no drawdown,
    with the mark named by ``cum_return_as_of`` / ``drawdown_as_of``.
    """

    data_dir = _data_dir(tmp_path)
    root = pl.loop_root(data_dir)
    _mtm(root, pl.S01_BOOK, "2026-10-12", 1.10, peak=1.10)
    _mtm(root, pl.S01_BOOK, "2026-10-22", 1.144, peak=1.144)
    _mtm(root, pl.S01_BOOK, "2026-10-24", 1.155, peak=1.155)
    _mtm(root, pl.S01_BOOK, "2026-10-30", 0.90, drawdown=0.90 / 1.155 - 1.0, peak=1.155)

    out = _weekly(data_dir, date(2026, 10, 25), monkeypatch)
    book = out["books"][pl.S01_BOOK]
    assert book["week_return"] == pytest.approx(1.155 / 1.10 - 1.0, rel=1e-12)
    assert book["week_return_base"] == "2026-10-12"
    assert book["week_return_as_of"] == "2026-10-24"
    assert book["week_return_reason"] is None
    # v1.8 (C-05): the 10-30 mark is after week_end -> not in these columns
    assert book["cum_return"] == pytest.approx(1.155 - 1.0, rel=1e-12)
    assert book["cum_return_as_of"] == "2026-10-24"
    assert book["cum_return_reason"] is None
    assert book["drawdown_now"] == pytest.approx(0.0, abs=1e-12)
    assert book["drawdown_as_of"] == "2026-10-24"
    assert book["drawdown_reason"] is None
    # the values the pre-fix code wrote from that later mark
    assert book["cum_return"] != pytest.approx(-0.10, rel=1e-9)
    assert book["drawdown_now"] != pytest.approx(0.90 / 1.155 - 1.0, rel=1e-9)
    # only the in-window marks (10-22, 10-24) are counted; 10-30 is after week_end
    assert book["mtm_days"] == 2


def test_weekly_row_is_append_only_and_the_same_week_end_can_be_written_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Case (d): duplicate week_end rows are appended, not replaced (C-05)."""

    data_dir = _data_dir(tmp_path)
    root = pl.loop_root(data_dir)
    _mtm(root, pl.S01_BOOK, "2026-10-22", 1.02, peak=1.02)

    first = _weekly(data_dir, date(2026, 10, 25), monkeypatch)
    second = _weekly(data_dir, date(2026, 10, 25), monkeypatch)
    assert first["books"] == second["books"]  # deterministic inputs
    lines = pl.summary_path(root).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert [json.loads(line)["week_end"] for line in lines] == ["2026-10-25", "2026-10-25"]
    # a journal with ONE mtm row: the week is priced from inception, no drawdown
    book = first["books"][pl.S01_BOOK]
    assert book["mtm_days"] == 1
    assert book["week_return"] == pytest.approx(0.02, rel=1e-12)
    assert book["week_return_base"] == "inception"
    assert book["week_return_as_of"] == "2026-10-22"
    assert book["cum_return"] == pytest.approx(0.02, rel=1e-12)
    assert book["drawdown_now"] == 0.0


def test_weekly_row_on_an_empty_journal_says_unknown_instead_of_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Empty period: every unmeasurable column is None, never a fabricated 0.

    The row is still written (the weekly job must record that it ran), but no
    ``week_return`` / shortfall statistic is fabricated as 0. v1.8 (C-05):
    ``cum_return`` / ``drawdown_now`` are None with a reason as well — with no
    mark at/before week_end the book's equity is unknown. Before the fix they
    read 0.0 from the untouched 1.0 inception, which claimed a measured flat
    week that never existed.
    """

    data_dir = _data_dir(tmp_path)  # a loop root with no journal at all
    root = pl.loop_root(data_dir)
    assert not pl.journal_path(root).exists()

    out = _weekly(data_dir, date(2026, 10, 25), monkeypatch)
    book = out["books"][pl.S01_BOOK]
    assert book["week_return"] is None
    assert book["week_return_reason"] == "no_mark_at_or_before_week_end"
    assert book["week_return_base"] is None and book["week_return_as_of"] is None
    assert book["shortfall_n"] == 0
    assert book["shortfall_bps_mean_signed"] is None
    assert book["shortfall_bps_mean_abs"] is None
    assert book["shortfall_bps_mean_upper95"] is None
    assert book["recompute_R1_rate"] is None
    assert book["mtm_days"] == 0
    assert book["cum_return"] is None
    assert book["cum_return_as_of"] is None
    assert book["cum_return_reason"] == "no_mark_at_or_before_week_end"
    assert book["drawdown_now"] is None
    assert book["drawdown_as_of"] is None
    assert book["drawdown_reason"] == "no_mark_at_or_before_week_end"
    assert book["comparison_reason"] == "benchmark_has_no_fill"
    assert book["vs_benchmark_pp_cum"] is None
    # no rows for this book -> the accumulator is the pure inception state
    assert pl._cost_accumulators(root, pl.S01_BOOK, 1.0) == (0.0, 0.0, 1.0)
    assert len(pl.summary_path(root).read_text(encoding="utf-8").splitlines()) == 1


def test_drawdown_now_falls_back_to_the_previous_peak_when_the_column_is_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Legacy/hand-built mtm rows without ``peak_equity`` keep the prior peak.

    ``run_daily_mtm`` always writes the column, but a row that predates it falls
    back to the running peak instead of resetting it to that row's equity — so
    ``drawdown_now`` on the weekly row stays a since-inception number.
    """

    data_dir = _data_dir(tmp_path)
    root = pl.loop_root(data_dir)
    legacy: dict[str, Any] = {
        "event": "mtm",
        "book": pl.S01_BOOK,
        "mtm_date": "2026-10-21",
        "equity": 1.05,
        "cash": 0.0,
        "drawdown": 0.05,
    }
    _append(root, **legacy)  # no peak_equity key
    _mtm(root, pl.S01_BOOK, "2026-10-22", 0.98, drawdown=0.98 / 1.05 - 1.0, peak=1.05)

    out = _weekly(data_dir, date(2026, 10, 25), monkeypatch)
    book = out["books"][pl.S01_BOOK]
    assert book["drawdown_now"] == pytest.approx(0.98 / 1.05 - 1.0, rel=1e-12)


def test_weekly_pp_columns_are_fraction_differences_times_hundred(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Units: cum_pnl is a fraction, ``*_pp`` is that fraction x 100.

    Book +10%, benchmark +5% from a shared 1.00 baseline -> cum_pnl 0.10 /
    0.05 and vs_benchmark_pp_cum 5.0 (not 0.05).
    """

    data_dir = _data_dir(tmp_path)
    root = pl.loop_root(data_dir)
    _fill(root, pl.BENCH_CRYPTO_BOOK, day="2026-10-21", asset="BTC")
    _mtm(root, pl.S01_BOOK, "2026-10-21", 1.00)
    _mtm(root, pl.BENCH_CRYPTO_BOOK, "2026-10-21", 1.00)
    _mtm(root, pl.S01_BOOK, "2026-10-22", 1.10)
    _mtm(root, pl.BENCH_CRYPTO_BOOK, "2026-10-22", 1.05)

    out = _weekly(data_dir, date(2026, 10, 25), monkeypatch)
    book = out["books"][pl.S01_BOOK]
    assert book["comparison_start"] == "2026-10-21"
    assert book["cum_pnl"] == pytest.approx(0.10, rel=1e-12)
    assert book["benchmark_cum_pnl"] == pytest.approx(0.05, rel=1e-12)
    assert book["vs_benchmark_pp_cum"] == pytest.approx(5.0, rel=1e-12)
    assert book["max_drawdown"] == 0.0  # measured, never fell
    # the legacy top-level column is the same number only because both books
    # start at the 1.0 inception here (prior art: §C-07)
    assert out["vs_benchmark_pp_cum"] == pytest.approx(5.0, rel=1e-12)


def test_currency_labels_change_nothing_because_no_conversion_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Case (e): the module is currency-blind by construction.

    Two journals with identical equity chains — one unlabelled, one mixing a
    JPY-labelled book with a USD-labelled benchmark — produce byte-identical
    book blocks. A cross-currency comparison is therefore performed as if both
    sides were the same unit: undetectable, and not refused (prior art
    ``docs/PAPERLOOP_MONEY_REVIEW_20261023.md`` §C-11).
    """

    def build(name: str, currency: str | None) -> dict[str, Any]:
        data_dir = _data_dir(tmp_path, name)
        root = pl.loop_root(data_dir)
        extra = {} if currency is None else {"currency": currency}
        _fill(root, pl.BENCH_CRYPTO_BOOK, day="2026-10-21", asset="BTC")
        _mtm(root, pl.S01_BOOK, "2026-10-21", 1.00, **({"currency": "JPY"} if extra else {}))
        _mtm(
            root, pl.BENCH_CRYPTO_BOOK, "2026-10-21", 1.00, **({"currency": "USD"} if extra else {})
        )
        _mtm(root, pl.S01_BOOK, "2026-10-22", 1.10, **({"currency": "JPY"} if extra else {}))
        _mtm(
            root, pl.BENCH_CRYPTO_BOOK, "2026-10-22", 1.05, **({"currency": "USD"} if extra else {})
        )
        return _weekly(data_dir, date(2026, 10, 25), monkeypatch)

    plain = build("plain", None)
    labelled = build("labelled", "USD")
    assert plain["books"] == labelled["books"]
    assert plain["vs_benchmark_pp_cum"] == labelled["vs_benchmark_pp_cum"]
    # no currency/FX field is produced anywhere on the comparison line
    line_keys = plain["books"][pl.S01_BOOK].keys()
    assert not any("curr" in key or "fx" in key or "fx_" in key for key in line_keys)


# --- the real journal, copied (never touched in place) ------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_LOOP = REPO_ROOT / "data" / "demo-loop"
_real_journal = pytest.mark.skipif(
    not (REAL_LOOP / "journal.jsonl").exists(),
    reason="data/demo-loop is not present (data/ is git-ignored)",
)


@_real_journal
def test_real_journal_contract_holds_and_the_source_copy_is_untouched(tmp_path: Path) -> None:
    """Invariants on the live journal, without pinning any live number.

    The real store grows every day, so only the CONTRACT is asserted here: the
    copy loads, each book's cost / drawdown / comparison / week_return output is
    finite and self-consistent, and the source file is byte-identical before and
    after (read-only use).
    """

    source = REAL_LOOP / "journal.jsonl"
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    root = pl.loop_root(_data_dir(tmp_path))
    shutil.copy2(source, pl.journal_path(root))

    rows = pl._load_journal(root)
    assert rows, "the real journal must not be empty"
    assert all(int(row["seq"]) == index for index, row in enumerate(rows, start=1))

    for book in pl.BOOKS_ENABLED:
        marks = pl._mtm_marks(root, book)
        last_day = max(marks) if marks else None
        equity = marks[last_day] if last_day is not None else 1.0
        cum_cost, cum_turnover, gross = pl._cost_accumulators(root, book, equity)
        for value in (cum_cost, cum_turnover, gross):
            assert math.isfinite(value)
        assert cum_cost >= 0.0 and cum_turnover >= 0.0 and gross >= 0.0
        if marks:
            # the chain never drifts away from the equity it is measured against
            assert gross > 0.0

        first_fill = pl._first_fill_date(root, book)
        dd = pl._max_drawdown_since(marks, first_fill) if first_fill is not None else None
        if dd is not None:
            assert -1.0 <= dd <= 0.0

    for book, bench in pl.BOOK_BENCHMARK.items():
        marks = pl._mtm_marks(root, book)
        last_day = max(marks) if marks else None
        line = pl._comparison_line(
            root,
            book,
            bench,
            {
                book: _state(marks[last_day] if last_day is not None else 1.0),
                bench: _state(1.0),
            },
        )
        numeric = (
            line["cum_pnl"],
            line["max_drawdown"],
            line["benchmark_cum_pnl"],
            line["benchmark_max_drawdown"],
            line["vs_benchmark_pp_cum"],
        )
        if line["comparison_reason"] is not None:
            # a reason means NO numbers: never a half-filled line
            assert all(value is None for value in numeric)
            assert line["comparison_start"] is None
        else:
            assert line["comparison_start"] is not None
            assert all(value is not None for value in numeric)

        if marks:
            week_end = max(marks)
            week_return, base_label, as_of, reason = pl._week_return(
                marks, week_end.fromordinal(week_end.toordinal() - 6), week_end
            )
            if reason is None:
                assert week_return is not None and math.isfinite(week_return)
                assert base_label is not None and as_of == week_end.isoformat()
            else:
                assert week_return is None

    assert hashlib.sha256(source.read_bytes()).hexdigest() == before, (
        "the real data must only ever be read"
    )
