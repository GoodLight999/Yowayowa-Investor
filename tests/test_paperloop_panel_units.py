"""Unit tests for the derived daily panel's aggregation and missing-data rules.

Scope: ``yowayowa.paperloop_panel`` only, and only through its public-ish
helpers: ``_build_panel_from_journal`` (the panel row builder) and the Q1-Q7
evaluators, which take the panel rows directly. Every expected number below is
hand-derived and its derivation is written next to the assertion, so a future
change that "moves the number" fails here and has to argue with the derivation.

The three rules this file pins (design P5 / §4):

1. missing is not zero — an unobserved price, turnover, margin or benchmark
   stays ``None`` with a reason, and an unanswerable question returns
   ``verdict="insufficient"`` with ``value=None``;
2. excluded rows (backfill, manual trigger, unjudgeable) are counted, never
   silently dropped and never part of a denominator;
3. periods are inclusive on both ends, and a calendar gap yields "unobserved"
   rather than a carried-forward return.

Units are fixed here as they are in the module: equity/weights/turnover are
fractions (1.0 == 100%), ``*_bps`` are basis points as given, and the two
``*_pp`` outputs multiply a fraction by 100. No FX conversion exists anywhere in
the panel, so a journal that mixes currencies in ``close``/``prices`` would be
divided as-is; that gap is documented (not asserted as correct) in
``test_no_currency_field_so_mixed_currency_is_not_detectable``.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from yowayowa import paperloop as pl
from yowayowa import paperloop_panel as pp

# ``_build_panel_from_journal`` takes a root it never reads (the panel is built
# from the journal alone); any path is fine here.
_UNUSED_ROOT = Path(".")


# --- hand-built journal rows --------------------------------------------------


def _decision(**over: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "event": "decision",
        "schema": pl.SCHEMA_V13,
        "book": pl.S01_BOOK,
        "asset": "BTC",
        "decision_date": "2026-01-05",
        "decision_id": "d1",
        "close": 100.0,
        "regime_state": "RISK_ON",
        "raw_target_weight": 0.5,
        "target_weight": 0.5,
        "held_weight_at_decision": 0.5,
        "backfill": False,
        "trigger": "cron",
        "halt_flag": False,
        "freshness_status": {"ok": True},
        "regime_tag": {"vol_bucket": "low", "trend_bucket": "up", "dd_bucket": "shallow"},
        "signal_value": 0.1,
        "bars_since_switch": 3,
        "switch_flag": False,
    }
    row.update(over)
    return row


def _mtm(**over: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "event": "mtm",
        "schema": pl.SCHEMA_V13,
        "book": pl.S01_BOOK,
        "mtm_date": "2026-01-06",
        "prices": {"BTC": 125.0},
        "equity": 1.0,
        "equity_gross": 1.0,
    }
    row.update(over)
    return row


def _fill(**over: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "event": "fill",
        "schema": pl.SCHEMA_V13,
        "book": pl.S01_BOOK,
        "asset": "BTC",
        "decision_id": "d1",
        "traded_notional": 500.0,
        "turnover": 0.5,
        "fill_lag_sec": 900,
        "weight_after": 0.5,
    }
    row.update(over)
    return row


def _panel(journal: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
    return pp._build_panel_from_journal(journal, _UNUSED_ROOT, **kwargs)  # type: ignore[arg-type]


def _row_for(panel: dict[str, Any], book: str, asset: str, day: str) -> dict[str, Any]:
    rows = panel["rows"]
    assert isinstance(rows, list)
    matches = [r for r in rows if (r["book"], r["asset"], r["date"]) == (book, asset, day)]
    assert len(matches) == 1, matches
    return matches[0]


# --- a panel row that a question evaluator sees -------------------------------


def _eval_row(**over: Any) -> dict[str, Any]:
    """A panel-shaped row with every column present and explicitly null.

    Hand-copied from ``_build_panel_from_journal``'s row dict so the evaluators
    are exercised without re-building a journal for each question.
    """

    row: dict[str, Any] = {
        "book": pl.S01_BOOK,
        "asset": "BTC",
        "date": "2026-01-05",
        "schema": pl.SCHEMA_V13,
        "regime_state": "RISK_ON",
        "raw_target_weight": 0.5,
        "target_weight": 0.5,
        "weight_eod": 0.5,
        "close": 100.0,
        "ret_next": 0.0,
        "ret_next_why": "",
        "filled": False,
        "fill_lag_sec": None,
        "shortfall_bps": None,
        "cost_assumed_bps": None,
        "turnover": None,
        "halted": False,
        "halt_margin_min": None,
        "vol_bucket": "low",
        "trend_bucket": "up",
        "dd_bucket": "shallow",
        "backfill": False,
        "trigger": "cron",
        "judgeable": True,
        "equity": None,
        "equity_gross": None,
        "bench_equity": None,
        "signal_value": 0.1,
        "bars_since_switch": 3,
        "switch_flag": False,
    }
    row.update(over)
    return row


# --- 1. ret_next: the only market-dependent column ----------------------------


def test_ret_next_is_next_calendar_day_mtm_over_close() -> None:
    # 125.0 / 100.0 - 1 = 0.25 exactly (both values are binary-exact)
    panel = _panel([_decision(), _mtm()])
    row = _row_for(panel, pl.S01_BOOK, "BTC", "2026-01-05")
    assert row["ret_next"] == 0.25
    assert row["ret_next_why"] == ""
    assert row["judgeable"] is True


def test_no_next_day_mtm_is_unobserved_not_zero() -> None:
    panel = _panel([_decision()])
    row = _row_for(panel, pl.S01_BOOK, "BTC", "2026-01-05")
    assert row["ret_next"] is None
    assert row["ret_next_why"] == "unobserved"
    assert row["judgeable"] is False


def test_next_mtm_without_the_asset_price_is_unobserved() -> None:
    panel = _panel([_decision(), _mtm(prices={"ETH": 60.0})])
    row = _row_for(panel, pl.S01_BOOK, "BTC", "2026-01-05")
    assert row["ret_next"] is None
    assert row["ret_next_why"] == "unobserved"


@pytest.mark.parametrize("close", [None, 0.0])
def test_unusable_close_never_divides(close: object) -> None:
    # close == 0 would be ZeroDivisionError and close is None would be TypeError;
    # both must fall back to the explicit null instead of a fabricated 0.
    panel = _panel([_decision(close=close), _mtm()])
    row = _row_for(panel, pl.S01_BOOK, "BTC", "2026-01-05")
    assert row["ret_next"] is None
    assert row["ret_next_why"] == "unobserved"


def test_calendar_gap_does_not_carry_a_return_forward() -> None:
    # 2026-01-09 is a Friday; the next row is the Monday MTM. ret_next looks at
    # decision_date + 1 calendar day only, so the gap is "unobserved" and not a
    # three-day return.
    panel = _panel(
        [
            _decision(decision_date="2026-01-09"),
            _mtm(mtm_date="2026-01-12", prices={"BTC": 125.0}),
        ]
    )
    row = _row_for(panel, pl.S01_BOOK, "BTC", "2026-01-09")
    assert row["ret_next"] is None
    assert row["ret_next_why"] == "unobserved"


def test_unparsable_decision_date_is_not_applicable() -> None:
    panel = _panel([_decision(decision_date="2026-13-40")])
    row = _row_for(panel, pl.S01_BOOK, "BTC", "2026-13-40")
    assert row["ret_next"] is None
    # _next_day() returns None for an unparsable date, which the builder labels
    # "not_applicable" (a malformed date, not an unobserved market).
    assert row["ret_next_why"] == "not_applicable"


# --- 2. missing vs zero in the panel columns ----------------------------------


def test_no_fill_keeps_turnover_null_and_filled_false() -> None:
    panel = _panel([_decision()])
    row = _row_for(panel, pl.S01_BOOK, "BTC", "2026-01-05")
    assert row["filled"] is False
    assert row["turnover"] is None
    assert row["fill_lag_sec"] is None
    assert row["shortfall_bps"] is None
    assert row["cost_assumed_bps"] is None


def test_filled_requires_a_nonzero_traded_notional() -> None:
    zero = _panel([_decision(), _fill(traded_notional=0.0)])
    assert _row_for(zero, pl.S01_BOOK, "BTC", "2026-01-05")["filled"] is False
    # the fill row still exists, so turnover is observed (not null) even though
    # the decision is not counted as filled
    assert _row_for(zero, pl.S01_BOOK, "BTC", "2026-01-05")["turnover"] == 0.5
    real = _panel([_decision(), _fill(traded_notional=500.0)])
    assert _row_for(real, pl.S01_BOOK, "BTC", "2026-01-05")["filled"] is True


def test_weight_eod_keeps_a_zero_weight_and_only_falls_back_when_missing() -> None:
    # weight_after == 0.0 is a real "flat" state and must not fall back
    flat = _panel([_decision(), _fill(weight_after=0.0)])
    assert _row_for(flat, pl.S01_BOOK, "BTC", "2026-01-05")["weight_eod"] == 0.0
    # weight_after == None is unobserved -> the decision's held weight is the
    # only defensible value
    missing = _panel([_decision(held_weight_at_decision=0.4), _fill(weight_after=None)])
    assert _row_for(missing, pl.S01_BOOK, "BTC", "2026-01-05")["weight_eod"] == 0.4
    # no fill at all -> held weight, not zero
    unfilled = _panel([_decision(held_weight_at_decision=0.4)])
    assert _row_for(unfilled, pl.S01_BOOK, "BTC", "2026-01-05")["weight_eod"] == 0.4


def test_missing_halt_check_keeps_margin_null() -> None:
    panel = _panel([_decision()])
    row = _row_for(panel, pl.S01_BOOK, "BTC", "2026-01-05")
    assert row["halt_margin_min"] is None
    assert row["halted"] is False


def test_halt_check_margin_is_read_for_the_same_book_and_date() -> None:
    journal = [
        _decision(halt_flag=True),
        {
            "event": "halt_check",
            "book": pl.S01_BOOK,
            "check_date": "2026-01-05",
            "margin_min": 0.25,
        },
        {"event": "halt_check", "book": pl.S01_BOOK, "check_date": "2026-01-06", "margin_min": 0.9},
    ]
    row = _row_for(_panel(journal), pl.S01_BOOK, "BTC", "2026-01-05")
    assert row["halt_margin_min"] == 0.25
    assert row["halted"] is True


def test_backfill_and_frozen_schema_rows_are_unjudgeable() -> None:
    backfill = _panel([_decision(backfill=True), _mtm()])
    assert _row_for(backfill, pl.S01_BOOK, "BTC", "2026-01-05")["judgeable"] is False
    frozen = _panel([_decision(schema=pl.SCHEMA), _mtm()])
    assert _row_for(frozen, pl.S01_BOOK, "BTC", "2026-01-05")["judgeable"] is False
    halted = _panel([_decision(regime_state="HALT"), _mtm()])
    assert _row_for(halted, pl.S01_BOOK, "BTC", "2026-01-05")["judgeable"] is False


def test_equity_as_of_the_decision_date_includes_that_day_and_excludes_later() -> None:
    journal = [
        _decision(decision_date="2026-01-05"),
        _mtm(mtm_date="2026-01-04", prices={}, equity=0.90, equity_gross=0.90),
        _mtm(mtm_date="2026-01-05", prices={}, equity=1.00, equity_gross=1.02),
        _mtm(mtm_date="2026-01-06", prices={}, equity=1.10, equity_gross=1.12),
    ]
    row = _row_for(_panel(journal), pl.S01_BOOK, "BTC", "2026-01-05")
    assert row["equity"] == 1.00  # same-day MTM counts (<= day)
    assert row["equity_gross"] == 1.02
    assert row["equity"] != 1.10  # tomorrow's MTM is never used


# --- 3. period boundaries and the empty window --------------------------------


def _three_days() -> list[dict[str, Any]]:
    return [
        _decision(decision_date="2026-01-05", decision_id="d05"),
        _decision(decision_date="2026-01-06", decision_id="d06"),
        _decision(decision_date="2026-01-07", decision_id="d07"),
        _mtm(mtm_date="2026-01-06", prices={"BTC": 110.0}),
        _mtm(mtm_date="2026-01-07", prices={"BTC": 110.0}),
        _mtm(mtm_date="2026-01-08", prices={"BTC": 110.0}),
    ]


def test_start_and_end_are_both_inclusive() -> None:
    panel = _panel(_three_days(), start=date(2026, 1, 6), end=date(2026, 1, 6))
    rows = panel["rows"]
    assert isinstance(rows, list)
    assert [r["date"] for r in rows] == ["2026-01-06"]


def test_open_ended_bounds_only_trim_the_side_given() -> None:
    only_start = _panel(_three_days(), start=date(2026, 1, 6))
    rows = only_start["rows"]
    assert isinstance(rows, list)
    assert [r["date"] for r in rows] == ["2026-01-06", "2026-01-07"]
    only_end = _panel(_three_days(), end=date(2026, 1, 6))
    rows = only_end["rows"]
    assert isinstance(rows, list)
    assert [r["date"] for r in rows] == ["2026-01-05", "2026-01-06"]


def test_window_outside_the_journal_is_empty_and_hashed_not_faked() -> None:
    panel = _panel(_three_days(), start=date(2030, 1, 1))
    assert panel["rows"] == []
    # sha256 of the canonical JSON of an empty row list == sha256("[]")
    assert panel["hash"] == hashlib.sha256(b"[]").hexdigest()
    assert json.dumps([], sort_keys=True, default=str) == "[]"


def test_empty_window_reports_insufficient_instead_of_zero() -> None:
    rows: list[dict[str, Any]] = []
    for question in (pp.eval_q1, pp.eval_q2, pp.eval_q3, pp.eval_q7):
        out = question(rows)  # type: ignore[operator]
        assert out["verdict"] == "insufficient", (question.__name__, out)
        assert out["value"] is None, (question.__name__, out)
        assert out["insufficient_reason"], (question.__name__, out)
    # the informational questions still answer: 0 observations, 0 rejections
    q4 = pp.eval_q4(rows)
    assert q4["value"] is None and q4["flag_regime_concentration"] is False
    q5 = pp.eval_q5(rows)
    assert q5["halted_days"] == 0 and q5["margin_min"] is None
    q6 = pp.eval_q6(rows, journal=[])
    assert q6["value"] == 0 and q6["rejections"] == []


# --- 4. units -------------------------------------------------------------------


def test_cost_drag_pp_is_fraction_times_hundred() -> None:
    # (1.00 - 0.98) / 1.00 * 100 = 2.0 percentage points
    rows = [
        _eval_row(date="2026-01-05", equity=1.00, equity_gross=1.00),
        _eval_row(date="2026-01-06", equity=0.98, equity_gross=1.00),
    ]
    assert pp._cost_drag_pp(rows, pl.S01_BOOK) == pytest.approx(2.0)


def test_cost_drag_pp_uses_the_last_date_and_refuses_a_zero_gross() -> None:
    rows = [
        _eval_row(date="2026-01-05", equity=0.50, equity_gross=1.00),
        _eval_row(date="2026-01-06", equity=0.99, equity_gross=1.00),
    ]
    # last date only: (1.00 - 0.99) / 1.00 * 100 = 1.0
    assert pp._cost_drag_pp(rows, pl.S01_BOOK) == pytest.approx(1.0)
    zero_gross = [_eval_row(date="2026-01-06", equity=0.99, equity_gross=0.0)]
    assert pp._cost_drag_pp(zero_gross, pl.S01_BOOK) is None
    no_gross = [_eval_row(date="2026-01-06", equity=0.99, equity_gross=None)]
    assert pp._cost_drag_pp(no_gross, pl.S01_BOOK) is None
    assert pp._cost_drag_pp([], pl.S01_BOOK) is None


def test_shortfall_is_reported_in_basis_points_without_rescaling() -> None:
    rows = [
        _eval_row(filled=True, turnover=0.5, shortfall_bps=10.0, cost_assumed_bps=15.0),
        _eval_row(filled=True, turnover=0.5, shortfall_bps=30.0, cost_assumed_bps=15.0),
    ]
    q3 = pp.eval_q3(rows)
    # effective = assumed + shortfall = 25 and 45 bps; equal weights -> 35 bps
    assert q3["value"] == pytest.approx(35.0)
    assert q3["assumed_bps"] == pytest.approx(15.0)
    # 35 / 15 = 2.3333... > 1.5 -> warn
    assert q3["ratio_to_assumed"] == pytest.approx(35.0 / 15.0)
    assert q3["verdict"] == "warn"


def test_cumulative_benchmark_difference_is_fraction_times_hundred() -> None:
    # (0.88 - 0.90) * 100 = -2.0 pp, not -0.02
    rows = [
        _eval_row(date="2026-01-05", equity=1.00, bench_equity=1.00),
        _eval_row(date="2026-01-06", equity=1.10, bench_equity=1.05),
        _eval_row(date="2026-01-07", equity=0.88, bench_equity=0.90),
    ]
    q7 = pp.eval_q7(rows)
    assert q7["value"] == pytest.approx(-2.0)
    assert q7["equity"] == 0.88
    assert q7["bench_equity"] == 0.90


def test_no_currency_field_so_mixed_currency_is_not_detectable() -> None:
    # Documents a gap rather than a guarantee: the panel never records the
    # currency/unit of close/prices/equity, and ret_next divides them as-is, so
    # a JPY close against a USD next price would produce a plausible-looking
    # ratio. The journal is the only writer and is single-currency today; this
    # test exists so that adding a currency field is a deliberate change.
    panel = _panel([_decision(close=15000.0), _mtm(prices={"BTC": 15150.0})])
    row = _row_for(panel, pl.S01_BOOK, "BTC", "2026-01-05")
    assert row["ret_next"] == pytest.approx(0.01)  # 15150 / 15000 - 1
    assert not {"currency", "quote_currency", "close_currency"} & set(row)
    assert not {"currency", "quote_currency"} & set(panel)


# --- 5. Q1-Q7 aggregation --------------------------------------------------------


def test_excluded_counts_are_counted_not_dropped() -> None:
    rows = [
        _eval_row(backfill=True, judgeable=False),
        _eval_row(trigger="manual", judgeable=True),
        _eval_row(judgeable=False),
        _eval_row(),
    ]
    assert pp._excluded_counts(rows) == {"backfill": 1, "manual": 1, "unjudgeable": 2}


def test_q1_below_the_minimum_per_state_is_insufficient() -> None:
    rows = [_eval_row(ret_next=0.01, regime_state="RISK_ON") for _ in range(19)]
    rows += [_eval_row(ret_next=-0.01, regime_state="RISK_OFF") for _ in range(19)]
    q1 = pp.eval_q1(rows)
    assert q1["verdict"] == "insufficient"
    assert q1["value"] is None and q1["se"] is None
    assert q1["n"] == 19  # min(n_on, n_off)
    assert "ON=19 OFF=19" in q1["insufficient_reason"]
    assert q1.get("by_states") is None  # _verdict_block only copies a Mapping value


def test_q1_delta_is_mean_on_minus_mean_off() -> None:
    rows = [_eval_row(ret_next=0.01, regime_state="RISK_ON") for _ in range(20)]
    rows += [_eval_row(ret_next=-0.01, regime_state="RISK_OFF") for _ in range(20)]
    q1 = pp.eval_q1(rows)
    # mean(ON) = 0.01, mean(OFF) = -0.01 -> delta = 0.02
    assert q1["value"] == pytest.approx(0.02)
    assert q1["by_states"]["on"] == {"n": 20, "mean": pytest.approx(0.01)}
    assert q1["by_states"]["off"] == {"n": 20, "mean": pytest.approx(-0.01)}
    assert q1["n"] == 20
    # the documented decision rule: pass only when |delta| > 2 * se
    assert q1["se"] is not None and q1["se"] > 0
    assert q1["verdict"] == ("pass" if abs(q1["value"]) > 2 * q1["se"] else "warn")
    assert q1["t"] == pytest.approx(q1["value"] / q1["se"])


def test_q1_se_is_deterministic_and_zero_when_every_return_is_identical() -> None:
    rows = [_eval_row(ret_next=0.02, regime_state="RISK_ON") for _ in range(20)]
    rows += [_eval_row(ret_next=-0.02, regime_state="RISK_OFF") for _ in range(40)]
    # pooled mean = (20*0.02 + 40*-0.02)/60 = -0.0066...; block bootstrap of a
    # two-valued series still varies, but re-running must give the same number
    first = pp.eval_q1(rows)
    second = pp.eval_q1(rows)
    assert first["se"] == second["se"]

    flat = [_eval_row(ret_next=0.01, regime_state="RISK_ON") for _ in range(20)]
    flat += [_eval_row(ret_next=0.01, regime_state="RISK_OFF") for _ in range(20)]
    q1 = pp.eval_q1(flat)
    assert q1["value"] == pytest.approx(0.0)
    assert q1["se"] == 0.0
    assert q1["t"] is None  # no division by a zero se
    assert q1["verdict"] == "warn"  # se == 0 is falsy -> the pass branch is skipped


def test_q1_scope_ignores_other_books_and_unjudgeable_rows() -> None:
    rows = [_eval_row(ret_next=0.01, regime_state="RISK_ON") for _ in range(20)]
    rows += [_eval_row(ret_next=-0.01, regime_state="RISK_OFF") for _ in range(20)]
    rows += [_eval_row(ret_next=0.5, regime_state="RISK_ON", book=pl.BOOK_C_BOOK)]
    rows += [_eval_row(ret_next=0.5, regime_state="RISK_ON", judgeable=False)]
    rows += [_eval_row(ret_next=0.5, regime_state="RISK_ON", backfill=True, judgeable=False)]
    q1 = pp.eval_q1(rows)
    assert q1["by_states"]["on"]["n"] == 20  # the 0.5 outliers stayed out
    assert q1["n_excluded"] == {"backfill": 1, "manual": 0, "unjudgeable": 2}


def test_q2_below_the_fill_minimum_is_insufficient() -> None:
    rows = [
        _eval_row(filled=True, turnover=0.5, shortfall_bps=5.0, fill_lag_sec=60) for _ in range(19)
    ]
    q2 = pp.eval_q2(rows)
    assert q2["verdict"] == "insufficient"
    assert q2["value"] is None
    assert "observed 19" in q2["insufficient_reason"]


def test_q2_shortfall_mean_stdev_and_t_are_hand_computed() -> None:
    # 20 fills with shortfall 1..20 bps and lag 100..2000 s (all < 2 h)
    rows = [
        _eval_row(
            filled=True,
            turnover=0.5,
            shortfall_bps=float(i),
            fill_lag_sec=100 * i,
        )
        for i in range(1, 21)
    ]
    q2 = pp.eval_q2(rows)
    # mean(1..20) = 10.5; sample stdev = sqrt( sum((x-10.5)^2) / 19 )
    # sum of squared deviations = n(n^2-1)/12 = 20*399/12 = 665 -> sqrt(665/19) = sqrt(35)
    assert q2["value"] == pytest.approx(10.5)
    assert q2["se"] == pytest.approx(35.0**0.5)
    # t = mean / (se / sqrt(n)) = 10.5 / sqrt(35/20) = 10.5 / sqrt(1.75)
    assert q2["t"] == pytest.approx(10.5 / (1.75**0.5))
    # the lag "median" is the upper median (index n // 2 = 10) = 1100 s, not the
    # average of the two middle values (1050)
    assert q2["fill_lag_sec_median"] == 1100
    assert q2["fill_lag_sec_max"] == 2000
    assert q2["fills_over_2h"] == 0
    assert q2["verdict"] == "warn"  # t > 2.0


def test_q2_counts_lags_over_two_hours_and_a_turnover_threshold() -> None:
    rows = [
        _eval_row(filled=True, turnover=0.5, shortfall_bps=1.0, fill_lag_sec=7201),
        _eval_row(filled=True, turnover=0.5, shortfall_bps=1.0, fill_lag_sec=7200),
        _eval_row(filled=True, turnover=0.009, shortfall_bps=999.0, fill_lag_sec=1),
    ]
    # 18 more qualifying fills so the 20-fill minimum is met
    rows += [
        _eval_row(filled=True, turnover=0.5, shortfall_bps=1.0, fill_lag_sec=60) for _ in range(18)
    ]
    q2 = pp.eval_q2(rows)
    assert q2["n"] == 20  # turnover 0.009 is below the frozen 0.01 threshold
    assert q2["fills_over_2h"] == 1  # strictly greater than 2 h
    assert q2["value"] == pytest.approx(1.0)


def test_q2_missing_turnover_is_collapsed_to_zero_by_the_threshold() -> None:
    # ``(r.get("turnover") or 0.0) >= 0.01`` treats an unobserved turnover as 0,
    # so a filled row with no turnover recorded is excluded from Q2 rather than
    # counted as a zero-turnover fill. Pinned as-is (see the task report).
    rows = [_eval_row(filled=True, turnover=None, shortfall_bps=0.0, fill_lag_sec=60)]
    q2 = pp.eval_q2(rows)
    assert q2["n"] == 0
    assert q2["verdict"] == "insufficient"


def test_q2_ignores_nulls_in_the_mean_but_keeps_the_fill_count() -> None:
    rows = [
        _eval_row(filled=True, turnover=0.5, shortfall_bps=10.0, fill_lag_sec=60) for _ in range(20)
    ]
    rows.append(_eval_row(filled=True, turnover=0.5, shortfall_bps=None, fill_lag_sec=None))
    q2 = pp.eval_q2(rows)
    assert q2["n"] == 21  # the fill still counts
    assert q2["value"] == pytest.approx(10.0)  # the null shortfall does not
    assert q2["fill_lag_sec_max"] == 60


def test_q3_weighted_mean_by_turnover() -> None:
    rows = [
        _eval_row(shortfall_bps=10.0, cost_assumed_bps=15.0, turnover=0.25),
        _eval_row(shortfall_bps=30.0, cost_assumed_bps=15.0, turnover=0.75),
    ]
    q3 = pp.eval_q3(rows)
    # (25*0.25 + 45*0.75) / 1.00 = 6.25 + 33.75 = 40.0 bps
    assert q3["value"] == pytest.approx(40.0)
    assert q3["n"] == 2
    assert q3["ratio_to_assumed"] == pytest.approx(40.0 / 15.0)


def test_q3_without_shortfalls_is_insufficient() -> None:
    rows = [_eval_row(shortfall_bps=None, turnover=0.5)]
    rows += [_eval_row(shortfall_bps=None, turnover=0.5, judgeable=False)]
    q3 = pp.eval_q3(rows)
    assert q3["verdict"] == "insufficient"
    assert q3["value"] is None
    assert q3["n"] == 0
    assert q3["insufficient_reason"] == "no marked fills in the window"


def test_q3_missing_per_row_cost_assumed_is_treated_as_zero_bps() -> None:
    # Per-row: effective = (cost_assumed_bps or 0.0) + shortfall_bps, so an
    # unobserved assumed fee adds nothing. Headline: scope[0] or 15.0. The two
    # defaults disagree (0.0 vs 15.0); pinned as-is (see the task report).
    no_fee = pp.eval_q3([_eval_row(shortfall_bps=10.0, cost_assumed_bps=None, turnover=0.5)])
    assert no_fee["value"] == pytest.approx(10.0)  # 0 + 10, not 15 + 10
    assert no_fee["assumed_bps"] == pytest.approx(15.0)  # headline default
    assert no_fee["ratio_to_assumed"] == pytest.approx(10.0 / 15.0)


def test_q3_zero_total_turnover_falls_back_to_a_denominator_of_one() -> None:
    # total_w = sum(w) or 1.0 -> 0.0 weighted cost instead of "insufficient".
    # Pinned as-is: a fabricated 0.0 is reported as a pass (see the task report).
    rows = [_eval_row(shortfall_bps=25.0, cost_assumed_bps=15.0, turnover=0.0)]
    q3 = pp.eval_q3(rows)
    assert q3["value"] == 0.0
    assert q3["verdict"] == "pass"


def test_q4_bucket_means_and_concentration_flag() -> None:
    rows = [
        _eval_row(vol_bucket="low", trend_bucket="up", equity=1.10, bench_equity=1.00),
        _eval_row(vol_bucket="low", trend_bucket="up", equity=1.20, bench_equity=1.00),
        _eval_row(vol_bucket="high", trend_bucket="down", equity=1.05, bench_equity=1.00),
        _eval_row(vol_bucket="high", trend_bucket="down", equity=1.05, bench_equity=1.00),
    ]
    q4 = pp.eval_q4(rows)
    assert q4["by_bucket"]["lowxup"] == {"n": 2, "mean_excess": pytest.approx(0.15)}
    assert q4["by_bucket"]["highxdown"] == {"n": 2, "mean_excess": pytest.approx(0.05)}
    # contributions = |last - first|: lowxup 0.10, highxdown 0.00 -> share 1.0
    assert q4["value"] == pytest.approx(1.0)
    assert q4["flag_regime_concentration"] is True
    assert q4["n"] == 4
    assert q4["verdict"] == "informational"


def test_q4_without_equity_has_no_share_and_no_flag() -> None:
    rows = [_eval_row(equity=None, bench_equity=None)]
    q4 = pp.eval_q4(rows)
    assert q4["value"] is None
    assert q4["flag_regime_concentration"] is False
    assert q4["by_bucket"] == {}


def test_q4_a_zero_total_contribution_is_not_a_zero_share() -> None:
    # equal first/last excess -> contribution 0.0 for every bucket
    rows = [
        _eval_row(vol_bucket="low", trend_bucket="up", equity=1.10, bench_equity=1.10),
        _eval_row(vol_bucket="low", trend_bucket="up", equity=1.20, bench_equity=1.20),
    ]
    q4 = pp.eval_q4(rows)
    assert q4["value"] is None
    assert q4["flag_regime_concentration"] is False


def test_q5_margin_distribution_and_near_miss_boundary() -> None:
    rows = [
        _eval_row(halted=True, halt_margin_min=0.3),
        _eval_row(halted=False, halt_margin_min=0.5),
        _eval_row(halted=True, halt_margin_min=None),
        _eval_row(halted=False, halt_margin_min=-0.1),
    ]
    q5 = pp.eval_q5(rows)
    assert q5["halted_days"] == 2
    assert q5["value"] == 2
    # strictly below 0.5: 0.3 and -0.1 -> 2; the null margin is not a near miss
    # and 0.5 exactly is not either
    assert q5["near_miss_days_margin_lt_0_5"] == 2
    assert q5["margin_min"] == pytest.approx(-0.1)
    assert len(q5["per_halt"]) == 2
    assert q5["n"] == 4
    assert q5["verdict"] == "informational"


def test_q5_without_observed_margins_keeps_minimum_null() -> None:
    q5 = pp.eval_q5([_eval_row(halt_margin_min=None)])
    assert q5["margin_min"] is None
    assert q5["near_miss_days_margin_lt_0_5"] == 0
    assert q5["halted_days"] == 0


def test_q6_counts_rejections_from_the_journal_per_book() -> None:
    journal = [
        {
            "event": "fill_rejected",
            "book": pl.S01_BOOK,
            "reject_class": "guard",
            "reject_reason": "min hold",
            "decision_id": "d1",
            "rejected_delta_weight": -0.1,
            "ref_bar_open_ts": "2026-01-05T00:00:00Z",
        },
        {"event": "fill_rejected", "book": pl.BOOK_C_BOOK, "reject_class": "guard"},
        {"event": "fill", "book": pl.S01_BOOK, "decision_id": "d1"},
    ]
    q6 = pp.eval_q6([_eval_row()], journal=journal)
    assert q6["value"] == 1  # book C's rejection is not the book's
    assert q6["rejections_by_class"] == {"guard": 1}
    assert q6["rejections"][0]["reason"] == "min hold"
    assert q6["rejections"][0]["rejected_delta_weight"] == -0.1
    assert q6["verdict"] == "informational"


def test_q6_zero_rejections_is_reported_with_its_reason() -> None:
    q6 = pp.eval_q6([_eval_row()], journal=[])
    assert q6["value"] == 0
    assert q6["rejections"] == []
    assert q6["rejections_by_class"] == {}
    # 0 is the expected daily-flow state and is labelled as such, not as a pass
    assert q6["verdict"] == "informational"
    assert "0 is expected" in q6["note"]


def test_q7_requires_a_benchmark_equity() -> None:
    q7 = pp.eval_q7([_eval_row(equity=1.02, bench_equity=None)])
    assert q7["verdict"] == "insufficient"
    assert q7["value"] is None
    assert q7["insufficient_reason"] == "no benchmark equity yet"
    no_equity = pp.eval_q7([_eval_row(equity=None, bench_equity=1.0)])
    assert no_equity["insufficient_reason"] == "no equity rows in the window"


def test_q7_drawdown_ratio_uses_the_benchmark_book_rows() -> None:
    rows = [
        _eval_row(date="2026-01-05", equity=1.00, bench_equity=1.00),
        _eval_row(date="2026-01-06", equity=1.10, bench_equity=1.05),
        _eval_row(date="2026-01-07", equity=0.88, bench_equity=0.90),
        # the benchmark book's own equity series (drives bench_dd)
        _eval_row(book=pl.BENCH_CRYPTO_BOOK, date="2026-01-05", equity=1.00, bench_equity=None),
        _eval_row(book=pl.BENCH_CRYPTO_BOOK, date="2026-01-06", equity=1.05, bench_equity=None),
        _eval_row(book=pl.BENCH_CRYPTO_BOOK, date="2026-01-07", equity=0.945, bench_equity=None),
    ]
    q7 = pp.eval_q7(rows)
    # book DD: 0.88 / 1.10 - 1 = -0.2 ; bench DD: 0.945 / 1.05 - 1 = -0.1
    assert q7["max_drawdown_book"] == pytest.approx(-0.2)
    assert q7["max_drawdown_bench"] == pytest.approx(-0.1)
    assert q7["dd_ratio"] == pytest.approx(2.0)
    assert q7["n"] == 3  # the benchmark book's rows are not the book's rows


def test_max_drawdown_is_the_worst_close_below_the_running_peak() -> None:
    assert pp._max_drawdown([1.0, 1.1, 0.88]) == pytest.approx(-0.2)
    assert pp._max_drawdown([1.0, 1.2, 1.3]) == 0.0  # monotonically up
    # an empty series returns 0.0 (no drawdown observed, not "unknown");
    # pinned as-is — see the task report.
    assert pp._max_drawdown([]) == 0.0


# --- 6. documented current wiring -----------------------------------------------


def test_bench_equity_column_is_always_the_crypto_book() -> None:
    # Every panel row carries _equity_on(..., BENCH_CRYPTO_BOOK, day), including
    # rows of the equity book C, whose registered comparison book is EQUITY_EW
    # (paperloop.BOOK_BENCHMARK). Pinned as-is so that changing the wiring is an
    # explicit decision; flagged in the task report.
    journal = [
        _decision(book=pl.BOOK_C_BOOK, asset="SPY", decision_id="c1"),
        _mtm(book=pl.BOOK_C_BOOK, mtm_date="2026-01-05", prices={"SPY": 500.0}, equity=1.03),
        _mtm(book=pl.BENCH_CRYPTO_BOOK, mtm_date="2026-01-05", prices={}, equity=1.01),
        _mtm(book=pl.BENCH_EQUITY_BOOK, mtm_date="2026-01-05", prices={}, equity=0.99),
    ]
    row = _row_for(_panel(journal), pl.BOOK_C_BOOK, "SPY", "2026-01-05")
    assert row["equity"] == 1.03
    assert row["bench_equity"] == 1.01  # crypto 1/N, not EQUITY_EW's 0.99


def test_panel_schema_and_freshness_flag_are_constant() -> None:
    # "freshness_status_seen" is a hard-coded True even for an empty window;
    # pinned as-is (see the task report).
    panel = _panel([_decision()])
    assert panel["schema"] == "demo-loop-panel-v1.2"
    assert panel["freshness_status_seen"] is True
    empty = _panel(_three_days(), start=date(2030, 1, 1))
    assert empty["freshness_status_seen"] is True
