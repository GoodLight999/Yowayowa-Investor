"""Regression contract for ``services.jp_value_quality_backtest``.

Two properties decide whether this simulation may be trusted, so both are
pinned here with exact arithmetic rather than ranges:

A. **Decision cut-off (look-ahead).** Everything that feeds a rebalance decision
   at session ``t`` — the scoring close, the eligible annual statements, the
   market cap built from them — must come from strictly before ``t``. Bars
   dated ``t`` or later may change the *marks* and the *later* decisions, but
   never ``t``'s selection.
B. **Cost arithmetic.** ``cost_rate = (commission_bps + slippage_bps) / 10_000``
   is charged once per rebalance on one-way traded notional (``sum |target -
   current|``): a build from cash costs one leg, a full switch costs two, an
   unchanged target costs nothing, and no fee is charged on any other session.
   No capital-gains or transaction tax is modelled anywhere in this engine.

The inspection checklist this file implements (watch point -> test) and the two
characterized shortcomings (F1 first-session rebase in ``total_return``, F2 a
symbol with no bar is given weight but never marked) are written up with
reproduction steps in ``docs/P5_JP_VALUE_QUALITY_BACKTEST_LEAK_20261023.md``.

Everything is synthetic and deterministic: no snapshot on disk is read.
"""

from datetime import date, timedelta

import pytest

from yowayowa.services.jp_value_quality import (
    DISCLOSURE_LAG_DAYS,
    AnnualFundamental,
    SymbolFundamentals,
    UniverseScore,
    disclosed_at,
)
from yowayowa.services.jp_value_quality_backtest import (
    PortfolioParams,
    PriceSeries,
    SelectionFn,
    _last_close_strictly_before,
    build_inputs,
    make_series,
    rebalance_sessions,
    run_backtest,
    select_ranked,
    total_return,
)

FY_END = date(2024, 3, 31)


def _weekdays(start: date, count: int) -> list[date]:
    days: list[date] = []
    day = start
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


#: Benchmark sessions used by the flat-price fixtures (2025-01-06 .. 2025-02-07).
CALENDAR = _weekdays(date(2025, 1, 6), 25)
#: Sessions before ``CALENDAR`` so that a first-session decision has a prior close.
PRE_CALENDAR = [date(2024, 12, 30), date(2025, 1, 2), date(2025, 1, 3)]
JAN_REBALANCE = CALENDAR[0]
FEB_REBALANCE = date(2025, 2, 3)
COMMISSION_BPS = 10.0
SLIPPAGE_BPS = 5.0
COST_RATE = (COMMISSION_BPS + SLIPPAGE_BPS) / 10_000


def _annual(**overrides: object) -> AnnualFundamental:
    base: dict[str, object] = {
        "fy_end": FY_END,
        "revenue": 1000.0,
        "operating_income": 150.0,
        "net_income": 100.0,
        "interest_expense": 10.0,
        "equity": 600.0,
        "assets": 1000.0,
        "current_assets": 700.0,
        "current_liabilities": 300.0,
        "liabilities": 400.0,
        "shares_outstanding": 100.0,
    }
    base.update(overrides)
    return AnnualFundamental(**base)  # type: ignore[arg-type]


def _fundamentals(symbol: str, annual: list[AnnualFundamental]) -> SymbolFundamentals:
    return SymbolFundamentals(symbol=symbol, annual=tuple(annual))


def _series(closes: dict[date, float], opens: dict[date, float] | None = None) -> PriceSeries:
    return make_series(closes, dict(closes) if opens is None else opens)


def _flat_universe() -> tuple[dict[str, SymbolFundamentals], dict[str, PriceSeries], PriceSeries]:
    """Two always-eligible names quoted flat at 100, plus a flat benchmark."""

    fundamentals = {
        "1000.T": _fundamentals("1000.T", [_annual()]),
        "2000.T": _fundamentals(
            "2000.T",
            [_annual(equity=900.0, assets=1200.0, current_assets=800.0, operating_income=200.0)],
        ),
    }
    days = [*PRE_CALENDAR, *CALENDAR]
    prices = {symbol: _series(dict.fromkeys(days, 100.0)) for symbol in fundamentals}
    benchmark = _series(dict.fromkeys(CALENDAR, 100.0))
    return fundamentals, prices, benchmark


def _drift_universe() -> tuple[dict[str, SymbolFundamentals], dict[str, PriceSeries], PriceSeries]:
    """Two names drifting at different rates (distinct market caps over time)."""

    fundamentals = {
        "1000.T": _fundamentals("1000.T", [_annual()]),
        "2000.T": _fundamentals("2000.T", [_annual(equity=900.0, operating_income=200.0)]),
    }
    prices: dict[str, PriceSeries] = {}
    for symbol, drift in (("1000.T", 1.002), ("2000.T", 1.001)):
        closes = dict.fromkeys(PRE_CALENDAR, 10.0)
        for offset, day in enumerate(CALENDAR):
            closes[day] = 10.0 * drift**offset
        prices[symbol] = _series(closes)
    benchmark = _series({day: 100.0 * 1.0005**offset for offset, day in enumerate(CALENDAR)})
    return fundamentals, prices, benchmark


def _record(
    inner: SelectionFn,
) -> tuple[SelectionFn, list[UniverseScore]]:
    """Wrap a selection rule and keep every ``UniverseScore`` it decided on."""

    seen: list[UniverseScore] = []

    def choose(scores: UniverseScore, params: PortfolioParams) -> dict[str, float]:
        seen.append(scores)
        return inner(scores, params)

    return choose, seen


def _month_switch(scores: UniverseScore, params: PortfolioParams) -> dict[str, float]:
    """Fully invested in one name: ``1000.T`` in January, ``2000.T`` afterwards."""

    return {("1000.T" if scores.decision_date.month == 1 else "2000.T"): 1.0}


def _run(
    fundamentals: dict[str, SymbolFundamentals],
    prices: dict[str, PriceSeries],
    benchmark: PriceSeries,
    selection: SelectionFn,
    *,
    top_n: int = 1,
    commission_bps: float = COMMISSION_BPS,
    slippage_bps: float = SLIPPAGE_BPS,
    start: date | None = None,
    end: date | None = None,
):
    return run_backtest(
        fundamentals=fundamentals,
        prices=prices,
        benchmark=benchmark,
        start=CALENDAR[0] if start is None else start,
        end=CALENDAR[-1] if end is None else end,
        params=PortfolioParams(
            label="unit",
            top_n=top_n,
            missing_policy="graceful",
            commission_bps=commission_bps,
            slippage_bps=slippage_bps,
        ),
        selection=selection,
    )


def _equity_at(outcome, day: date) -> float:
    matches = [value for when, value in outcome.equity_curve if when == day]
    assert len(matches) == 1, day
    return matches[0]


# ---------------------------------------------------------------------------
# A. decision cut-off (look-ahead)
# ---------------------------------------------------------------------------


def test_scoring_close_is_strictly_before_the_decision_date() -> None:
    series = _series({PRE_CALENDAR[1]: 50.0, PRE_CALENDAR[2]: 60.0, JAN_REBALANCE: 999.0})

    # A session's own close is never available to that session's decision.
    assert _last_close_strictly_before(series, JAN_REBALANCE) == 60.0
    assert _last_close_strictly_before(series, PRE_CALENDAR[2]) == 50.0
    # Nothing strictly before the first bar: no fallback to the same-day close.
    assert _last_close_strictly_before(series, PRE_CALENDAR[1]) is None


def test_build_inputs_ignores_bars_on_or_after_the_decision_date() -> None:
    fundamentals = {"1000.T": _fundamentals("1000.T", [_annual()])}
    base = {day: 100.0 for day in (*PRE_CALENDAR, *CALENDAR)}
    baseline, violations = build_inputs(fundamentals, {"1000.T": _series(base)}, JAN_REBALANCE)
    assert violations == 0

    same_day = {**base, JAN_REBALANCE: 1e5}
    later = {**base, **{day: 1e5 for day in CALENDAR[1:]}}
    for hostile in (same_day, later):
        inputs, violations = build_inputs(fundamentals, {"1000.T": _series(hostile)}, JAN_REBALANCE)
        assert violations == 0
        # Dataclass equality, not a fuzzy bound: not one input may move.
        assert inputs["1000.T"] == baseline["1000.T"]
        assert inputs["1000.T"].market_cap == pytest.approx(100.0 * 100.0)


def test_one_day_later_disclosure_boundary_changes_the_factor_inputs() -> None:
    previous = _annual(fy_end=FY_END - timedelta(days=366), operating_income=100.0, equity=500.0)
    latest = _annual(fy_end=FY_END, operating_income=200.0, equity=900.0)
    fundamentals = {"1000.T": _fundamentals("1000.T", [previous, latest])}

    boundary = disclosed_at(FY_END)
    assert boundary == FY_END + timedelta(days=DISCLOSURE_LAG_DAYS)

    # The close must sit strictly before the boundary: at ``boundary`` itself
    # the bar is a legitimate prior close, while a close after the boundary
    # would leave the symbol with no eligible mark and excluded entirely
    # (strictly-before is the no-lookahead contract, t_3b044506).
    prices = {"1000.T": _series({boundary - timedelta(days=1): 10.0})}

    before, _ = build_inputs(fundamentals, prices, boundary)
    after, _ = build_inputs(fundamentals, prices, boundary + timedelta(days=1))

    # On the boundary day the new statement is still invisible: the older one
    # is used, so market cap / B-M come from 500 of equity, not 900.
    assert before["1000.T"].fy_end == previous.fy_end
    assert before["1000.T"].bm == pytest.approx(500.0 / (10.0 * 100.0))
    # One day later it is public and every derived figure changes.
    assert after["1000.T"].fy_end == FY_END
    assert after["1000.T"].bm == pytest.approx(900.0 / (10.0 * 100.0))
    assert after["1000.T"].bm != pytest.approx(before["1000.T"].bm)
    assert before["1000.T"].op_growth is None
    assert after["1000.T"].op_growth == pytest.approx(1.0)


def test_every_rebalance_decides_on_strictly_disclosed_data() -> None:
    fundamentals, prices, benchmark = _drift_universe()
    selection, seen = _record(select_ranked)
    outcome = _run(fundamentals, prices, benchmark, selection)

    assert outcome.lookahead_violations == 0
    assert [scores.decision_date for scores in seen] == [JAN_REBALANCE, FEB_REBALANCE]
    for scores in seen:
        assert scores.scores, scores.decision_date
        for item in scores.scores.values():
            inputs = item.inputs
            assert inputs.disclosed_at < scores.decision_date
            assert inputs.disclosed_at == disclosed_at(inputs.fy_end)
            # Market cap must come from the last close *before* the session.
            prior = _last_close_strictly_before(prices[item.symbol], scores.decision_date)
            assert prior is not None
            assert inputs.market_cap == pytest.approx(prior * 100.0)


def test_same_day_close_moves_the_mark_but_not_the_decision() -> None:
    fundamentals, prices, benchmark = _drift_universe()
    selection, base_seen = _record(select_ranked)
    baseline = _run(fundamentals, prices, benchmark, selection)

    # Hostile same-day close: the session's own close quintuples nothing, it
    # just moves the mark that this session's P&L is measured against.
    shocked: dict[str, PriceSeries] = {}
    for symbol, series in prices.items():
        closes = dict(series.closes)
        closes[JAN_REBALANCE] *= 1.7
        shocked[symbol] = _series(closes, dict(series.opens))

    selection, shocked_seen = _record(select_ranked)
    hostile = _run(fundamentals, shocked, benchmark, selection)

    for scores_a, scores_b in zip(base_seen, shocked_seen, strict=True):
        assert scores_a.scores.keys() == scores_b.scores.keys()
        for symbol, item in scores_a.scores.items():
            assert item.inputs == scores_b.scores[symbol].inputs, scores_a.decision_date
    assert hostile.holdings == baseline.holdings
    # ... and the test is not vacuous: the same-day close did change the mark.
    assert _equity_at(hostile, JAN_REBALANCE) != pytest.approx(_equity_at(baseline, JAN_REBALANCE))


def test_later_bars_cannot_change_the_past() -> None:
    fundamentals, prices, benchmark = _drift_universe()
    selection, base_seen = _record(select_ranked)
    baseline = _run(fundamentals, prices, benchmark, selection)

    shifted: dict[str, PriceSeries] = {}
    for symbol, series in prices.items():
        closes = dict(series.closes)
        for day in CALENDAR[1:]:
            closes[day] *= 1.7
        shifted[symbol] = _series(closes, dict(series.opens))

    selection, shifted_seen = _record(select_ranked)
    hostile = _run(fundamentals, shifted, benchmark, selection)

    # Nothing at or before the first rebalance may move.
    assert [pair for pair in hostile.equity_curve if pair[0] <= JAN_REBALANCE] == [
        pair for pair in baseline.equity_curve if pair[0] <= JAN_REBALANCE
    ]
    assert hostile.holdings[0] == baseline.holdings[0]
    for symbol, item in base_seen[0].scores.items():
        assert item.inputs == shifted_seen[0].scores[symbol].inputs
    # From the next session on the perturbed bars are legitimately visible.
    assert hostile.equity_curve[-1][1] != pytest.approx(baseline.equity_curve[-1][1])
    assert shifted_seen[1].scores["1000.T"].inputs.market_cap != pytest.approx(
        base_seen[1].scores["1000.T"].inputs.market_cap
    )


def test_symbol_without_a_prior_close_is_not_scored_that_day() -> None:
    fundamentals = {"1000.T": _fundamentals("1000.T", [_annual()])}
    # First bar is the rebalance session itself: no prior close exists yet.
    prices = {"1000.T": _series({day: 100.0 for day in CALENDAR})}
    benchmark = _series(dict.fromkeys(CALENDAR, 100.0))

    outcome = _run(fundamentals, prices, benchmark, select_ranked)

    assert outcome.holdings == [0, 1]
    assert outcome.equity_curve[0] == (JAN_REBALANCE, 1.0)
    assert outcome.turnover == pytest.approx(1.0)


def test_disclosure_boundary_shift_of_one_day_changes_the_portfolio() -> None:
    boundary = disclosed_at(FY_END)
    calendar = _weekdays(boundary, 10)
    fundamentals = {"1000.T": _fundamentals("1000.T", [_annual()])}
    closes = {boundary - timedelta(days=5): 10.0}
    for offset, day in enumerate(calendar):
        closes[day] = 10.0 * 1.001**offset
    prices = {"1000.T": _series(closes)}
    benchmark = _series(dict.fromkeys(calendar, 100.0))

    def run_from(start: date):
        return _run(fundamentals, prices, benchmark, select_ranked, start=start, end=calendar[-1])

    on_boundary = run_from(boundary)
    one_day_later = run_from(boundary + timedelta(days=1))

    # On the boundary nothing is public yet, so the first rebalance sits in cash.
    assert on_boundary.holdings[0] == 0
    assert on_boundary.equity_curve[0][1] == 1.0
    assert one_day_later.holdings[0] == 1
    # Total turnover cannot discriminate the shift: both books eventually deploy
    # the same 1.0 notional, only on different sessions (t_3b044506: the old
    # ``one_day_later.turnover > on_boundary.turnover`` compared 1.0 > 1.0).
    assert on_boundary.turnover == pytest.approx(one_day_later.turnover)
    # The shift moves the *entry session*: the first sessions already differ.
    assert one_day_later.equity_curve[0][1] != pytest.approx(on_boundary.equity_curve[0][1])
    assert total_return(one_day_later.equity_curve) != pytest.approx(
        total_return(on_boundary.equity_curve)
    )


# ---------------------------------------------------------------------------
# B. cost arithmetic
# ---------------------------------------------------------------------------


def test_cost_rate_is_commission_plus_slippage_and_nothing_else() -> None:
    params = PortfolioParams(label="x", commission_bps=COMMISSION_BPS, slippage_bps=SLIPPAGE_BPS)
    assert params.cost_rate == pytest.approx(COST_RATE)
    assert PortfolioParams(label="x", commission_bps=0.0, slippage_bps=0.0).cost_rate == 0.0
    assert PortfolioParams(label="x", commission_bps=20.0, slippage_bps=10.0).cost_rate == (
        pytest.approx(0.003)
    )
    # Fee/tax surface: these seven parameters are the whole cost model. There is
    # no tax, levy or fixed-fee input, so none can be applied behind our back.
    assert set(PortfolioParams.__dataclass_fields__) == {
        "label",
        "top_n",
        "weights",
        "missing_policy",
        "op_simple_basis",
        "commission_bps",
        "slippage_bps",
    }


def test_first_build_charges_one_way_notional_exactly_once() -> None:
    fundamentals, prices, benchmark = _flat_universe()
    # select_ranked keeps the January book unchanged in February, so the run's
    # total turnover is the initial build alone. (_month_switch would add the
    # February switch's 2.0 and the total would be 3.0 — that case is covered
    # by test_full_switch_charges_both_legs; t_3b044506.)
    outcome = _run(fundamentals, prices, benchmark, select_ranked)

    # Buying 100% of the book from cash is turnover 1.0 -> exactly one cost leg.
    assert outcome.turnover == pytest.approx(1.0)
    assert _equity_at(outcome, JAN_REBALANCE) == pytest.approx(1 - COST_RATE, abs=1e-12)
    assert outcome.daily_returns[0] == pytest.approx(-COST_RATE, abs=1e-12)
    # Not charged twice, and not charged as if the whole book had been traded
    # twice (a full sell + full buy).
    assert _equity_at(outcome, JAN_REBALANCE) != pytest.approx((1 - COST_RATE) ** 2, rel=1e-6)
    assert _equity_at(outcome, JAN_REBALANCE) != pytest.approx(1 - 2 * COST_RATE, rel=1e-6)


def test_full_switch_charges_both_legs() -> None:
    fundamentals, prices, benchmark = _flat_universe()
    outcome = _run(fundamentals, prices, benchmark, _month_switch)

    assert outcome.holdings == [1, 1]
    assert outcome.rebalances == 2
    # 1.0 to build the book in January, 2.0 (all of A sold + all of B bought).
    assert outcome.turnover == pytest.approx(3.0)
    january = _equity_at(outcome, JAN_REBALANCE)
    february = _equity_at(outcome, FEB_REBALANCE)

    assert february / january == pytest.approx(1 - 2 * COST_RATE, abs=1e-12)
    # Charging only the sell side (or only the buy side) would leave 1 x cost.
    assert february / january != pytest.approx(1 - COST_RATE, rel=1e-6)
    assert outcome.daily_returns[CALENDAR.index(FEB_REBALANCE)] == pytest.approx(
        -2 * COST_RATE, abs=1e-12
    )


def test_unchanged_target_costs_nothing() -> None:
    fundamentals, prices, benchmark = _flat_universe()
    outcome = _run(fundamentals, prices, benchmark, select_ranked)

    assert outcome.holdings == [1, 1]
    assert outcome.turnover == pytest.approx(1.0)  # the initial build only
    assert _equity_at(outcome, FEB_REBALANCE) == pytest.approx(
        _equity_at(outcome, JAN_REBALANCE), abs=1e-12
    )


def test_non_rebalance_sessions_charge_nothing() -> None:
    fundamentals, prices, benchmark = _flat_universe()
    outcome = _run(fundamentals, prices, benchmark, _month_switch)

    for day, daily in zip(
        (day for day, _ in outcome.equity_curve), outcome.daily_returns, strict=True
    ):
        if day not in (JAN_REBALANCE, FEB_REBALANCE):
            assert daily == 0.0, day
    between = [value for day, value in outcome.equity_curve if JAN_REBALANCE < day < FEB_REBALANCE]
    assert between
    assert set(between) == {_equity_at(outcome, JAN_REBALANCE)}


def test_cost_is_linear_in_bps_and_zero_cost_is_exactly_free() -> None:
    fundamentals, prices, benchmark = _flat_universe()

    for commission, slippage in ((0.0, 0.0), (10.0, 5.0), (20.0, 10.0), (40.0, 20.0)):
        outcome = _run(
            fundamentals,
            prices,
            benchmark,
            _month_switch,
            commission_bps=commission,
            slippage_bps=slippage,
        )
        expected = 1 - (commission + slippage) / 10_000
        assert _equity_at(outcome, JAN_REBALANCE) == pytest.approx(expected, abs=1e-12)
    # No fixed fee and no tax: a frictionless run keeps the whole notional.
    free = _run(
        fundamentals, prices, benchmark, _month_switch, commission_bps=0.0, slippage_bps=0.0
    )
    assert _equity_at(free, JAN_REBALANCE) == 1.0
    assert _equity_at(free, FEB_REBALANCE) == 1.0


def test_empty_selection_holds_cash_and_pays_no_cost() -> None:
    fundamentals, prices, benchmark = _flat_universe()

    def nothing(scores: UniverseScore, params: PortfolioParams) -> dict[str, float]:
        return {}

    outcome = _run(fundamentals, prices, benchmark, nothing)

    assert outcome.holdings == [0, 0]
    assert outcome.turnover == 0.0
    assert all(daily == 0.0 for daily in outcome.daily_returns)
    assert outcome.equity_curve[0] == (JAN_REBALANCE, 1.0)
    assert total_return(outcome.equity_curve) == 0.0


# ---------------------------------------------------------------------------
# C. execution and ledger reconciliation
# ---------------------------------------------------------------------------


def test_orders_execute_at_the_rebalance_session_open() -> None:
    fundamentals = {"1000.T": _fundamentals("1000.T", [_annual()])}
    days = [PRE_CALENDAR[2], *CALENDAR[:3]]
    closes = {days[0]: 100.0, days[1]: 110.0, days[2]: 121.0, days[3]: 121.0}
    opens = {days[0]: 100.0, days[1]: 105.0, days[2]: 120.0, days[3]: 121.0}
    prices = {"1000.T": _series(closes, opens)}
    benchmark = _series(dict.fromkeys(CALENDAR[:3], 100.0))

    outcome = _run(
        fundamentals,
        prices,
        benchmark,
        select_ranked,
        start=CALENDAR[0],
        end=CALENDAR[2],
    )

    entry = _equity_at(outcome, CALENDAR[0])
    # Bought at the session open (105), marked at the session close (110).
    assert entry == pytest.approx((1 - COST_RATE) * (110.0 / 105.0), abs=1e-12)
    # Not filled at the decision close (100) nor at the same-day close (110).
    assert entry != pytest.approx((1 - COST_RATE) * (110.0 / 100.0), rel=1e-6)
    assert entry != pytest.approx(1 - COST_RATE, rel=1e-6)


def test_overnight_gap_and_intraday_leg_reconcile_to_close_to_close() -> None:
    fundamentals = {"1000.T": _fundamentals("1000.T", [_annual()])}
    days = [PRE_CALENDAR[2], *CALENDAR[:3]]
    closes = {days[0]: 100.0, days[1]: 110.0, days[2]: 121.0, days[3]: 121.0}
    opens = {days[0]: 100.0, days[1]: 105.0, days[2]: 120.0, days[3]: 121.0}
    prices = {"1000.T": _series(closes, opens)}
    benchmark = _series(dict.fromkeys(CALENDAR[:3], 100.0))

    outcome = _run(
        fundamentals, prices, benchmark, select_ranked, start=CALENDAR[0], end=CALENDAR[2]
    )

    first = _equity_at(outcome, CALENDAR[0])
    second = _equity_at(outcome, CALENDAR[1])
    # Held through the gap: the day's return must be the close-to-close move,
    # with the 110 -> 120 gap attributed to the overnight leg and 120 -> 121 to
    # the intraday leg.
    assert second / first == pytest.approx(121.0 / 110.0, abs=1e-12)
    assert outcome.daily_returns[1] == pytest.approx(
        (120.0 / 110.0) * (121.0 / 120.0) - 1, abs=1e-12
    )
    assert outcome.daily_returns[1] != pytest.approx(121.0 / 120.0 - 1, rel=1e-6)


def test_rebalance_calendar_is_one_session_per_month() -> None:
    fundamentals, prices, benchmark = _flat_universe()
    outcome = _run(fundamentals, prices, benchmark, _month_switch)

    expected = rebalance_sessions(CALENDAR)
    assert expected == [JAN_REBALANCE, FEB_REBALANCE]
    assert outcome.rebalances == len(expected)
    assert len(outcome.holdings) == outcome.rebalances

    # A window that starts mid-month rebalances on its first session (initial
    # deployment) and then once per month, never twice in the same month.
    mid_month = [day for day in CALENDAR if day >= date(2025, 1, 15)]
    assert rebalance_sessions(mid_month)[0] == date(2025, 1, 15)


def test_window_with_fewer_than_two_sessions_is_rejected() -> None:
    fundamentals, prices, benchmark = _flat_universe()

    with pytest.raises(ValueError, match="fewer than two sessions"):
        _run(
            fundamentals,
            prices,
            benchmark,
            _month_switch,
            start=JAN_REBALANCE,
            end=JAN_REBALANCE,
        )


# ---------------------------------------------------------------------------
# D. documented shortcomings, characterized so a fix is a visible change
# ---------------------------------------------------------------------------


def test_total_return_rebases_after_the_first_session() -> None:
    """CHARACTERIZED DEFECT F1 (reported, not fixed).

    ``total_return`` divides by ``equity_curve[0]``, which already is the
    post-rebalance equity of the first session, so the first session's cost and
    return drop out of the reported total. Reproduce: the flat universe above
    reports ``total_return == -0.003`` while compounding every published daily
    return gives ``0.9955045`` (``-0.4495%``).
    """

    fundamentals, prices, benchmark = _flat_universe()
    outcome = _run(fundamentals, prices, benchmark, _month_switch)

    compounded = 1.0
    for daily in outcome.daily_returns:
        compounded *= 1 + daily
    assert compounded == pytest.approx(_equity_at(outcome, FEB_REBALANCE), abs=1e-12)
    assert total_return(outcome.equity_curve) == pytest.approx(-2 * COST_RATE, abs=1e-12)
    # The gap is exactly the first session's factor (one cost leg, no returns).
    assert compounded / (1 + total_return(outcome.equity_curve)) == pytest.approx(
        1 - COST_RATE, abs=1e-12
    )
    assert total_return(outcome.equity_curve) != pytest.approx(compounded - 1, rel=1e-6)


def test_symbol_with_no_bar_is_weighted_but_never_marked() -> None:
    """CHARACTERIZED DEFECT F2 (reported, not fixed).

    A selected symbol whose series has no bar at or after the rebalance is given
    full weight, never marked, and never counted in ``stale_marks``: the capital
    is silently parked at 0% instead of being flagged as unpriceable.
    """

    fundamentals = {"1000.T": _fundamentals("1000.T", [_annual()])}
    # Bars exist only before the window: the name scores, then never trades.
    prices = {"1000.T": _series(dict.fromkeys(PRE_CALENDAR, 10.0))}
    benchmark = _series(dict.fromkeys(CALENDAR, 100.0))

    outcome = _run(fundamentals, prices, benchmark, select_ranked)

    assert outcome.holdings == [1, 1]
    assert outcome.turnover == pytest.approx(1.0)
    assert outcome.stale_marks == 0
    # The book is never re-marked: every session carries the entry cost only.
    # (Element-wise compare: pytest.approx values are unhashable, so a set
    # equality raises TypeError regardless of the engine, t_3b044506.)
    assert all(
        value == pytest.approx(1 - COST_RATE, abs=1e-12) for _, value in outcome.equity_curve
    )
    assert _equity_at(outcome, CALENDAR[-1]) == pytest.approx(1 - COST_RATE, abs=1e-12)
