from datetime import date, timedelta

import pytest

from yowayowa.services.jp_value_quality import (
    DISCLOSURE_LAG_DAYS,
    AnnualFundamental,
    BaselineFilter,
    FactorInputs,
    ScoreWeights,
    SymbolFundamentals,
    compute_factor_inputs,
    disclosed_at,
    percentile_ranks,
    score_universe,
)
from yowayowa.services.jp_value_quality_backtest import (
    PortfolioParams,
    PriceSeries,
    adjust_prices,
    benchmark_outcome,
    make_series,
    rebalance_sessions,
    run_backtest,
    select_baseline,
    select_ranked,
    slice_outcome,
    total_return,
)

FY_END = date(2024, 3, 31)
ELIGIBLE = FY_END + timedelta(days=DISCLOSURE_LAG_DAYS + 1)


def _annual(**overrides: object) -> AnnualFundamental:
    base = {
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


def test_statement_is_invisible_until_the_disclosure_lag_has_passed() -> None:
    fundamentals = _fundamentals("1000.T", [_annual()])
    boundary = FY_END + timedelta(days=DISCLOSURE_LAG_DAYS)

    assert disclosed_at(FY_END) == boundary
    # On the boundary the statement is not yet usable (strictly before).
    assert fundamentals.point_in_time(boundary) is None
    assert fundamentals.point_in_time(boundary + timedelta(days=1)) is not None


def test_point_in_time_uses_previous_year_growth_only_when_already_disclosed() -> None:
    prior = _annual(fy_end=date(2023, 3, 31), operating_income=100.0)
    latest = _annual(fy_end=date(2024, 3, 31), operating_income=150.0)
    fundamentals = _fundamentals("1000.T", [prior, latest])

    # 2023 statement is public 120 days after its own FY end; before that only
    # the 2024 statement... which is even later, so nothing is eligible.
    early = date(2023, 4, 1)
    assert fundamentals.point_in_time(early) is None

    first_year_only = date(2023, 9, 1)
    pit = fundamentals.point_in_time(first_year_only)
    assert pit is not None
    latest_visible, previous_visible = pit
    assert latest_visible.fy_end == date(2023, 3, 31)
    assert previous_visible.operating_income is None  # sentinel, not a zero

    both = date(2024, 10, 1)
    pit = fundamentals.point_in_time(both)
    assert pit is not None
    assert pit[0].fy_end == date(2024, 3, 31)
    assert pit[1].fy_end == date(2023, 3, 31)


def test_factor_inputs_follow_the_spec_formulas() -> None:
    fundamentals = _fundamentals("1000.T", [_annual()])
    # price 10 x 100 shares = market cap 1000
    inputs = compute_factor_inputs(fundamentals, ELIGIBLE, 10.0)

    assert inputs is not None
    assert inputs.market_cap == pytest.approx(1000.0)
    assert inputs.bm == pytest.approx(0.6)
    assert inputs.pbr == pytest.approx(1000.0 / 600.0)
    # conservative floor: current assets - total liabilities, no securities term
    assert inputs.net_cash_ratio == pytest.approx((700.0 - 400.0) / 1000.0)
    assert inputs.op_strict == pytest.approx((150.0 - 10.0) / 600.0)
    assert inputs.op_simple == pytest.approx(150.0 / 600.0)
    assert inputs.equity_ratio == pytest.approx(600.0 / 1000.0)
    assert inputs.roe == pytest.approx(100.0 / 600.0)
    assert inputs.catalyst is None
    assert inputs.disclosed_at == disclosed_at(FY_END)


def test_missing_inputs_are_never_zero_filled() -> None:
    fundamentals = _fundamentals(
        "1000.T",
        [_annual(interest_expense=None, current_assets=None, shares_outstanding=None)],
    )
    inputs = compute_factor_inputs(fundamentals, ELIGIBLE, 10.0)

    assert inputs is not None
    assert inputs.market_cap is None
    assert inputs.bm is None
    assert inputs.net_cash_ratio is None
    assert inputs.op_strict is None
    assert inputs.equity_ratio == pytest.approx(0.6)
    assert {"market_cap", "bm", "net_cash_ratio", "op_strict"} <= set(inputs.missing)


def test_forecast_basis_marks_quality_missing_when_no_forecast_is_available() -> None:
    fundamentals = _fundamentals("1000.T", [_annual()])
    actual = compute_factor_inputs(fundamentals, ELIGIBLE, 10.0, op_simple_basis="actual")
    forecast = compute_factor_inputs(fundamentals, ELIGIBLE, 10.0, op_simple_basis="forecast")

    assert actual is not None and actual.op_simple is not None
    assert forecast is not None and forecast.op_simple is None
    assert "op_simple_forecast" in forecast.missing


def test_percentile_ranks_order_ties_and_singletons() -> None:
    ranks = percentile_ranks({"a": 1.0, "b": 2.0, "c": 2.0, "d": 4.0, "e": None})
    assert ranks["a"] == pytest.approx(0.0)
    assert ranks["b"] == pytest.approx(0.5)
    assert ranks["c"] == pytest.approx(0.5)
    assert ranks["d"] == pytest.approx(1.0)
    assert "e" not in ranks
    assert percentile_ranks({"only": 3.0}) == {"only": 0.5}
    assert percentile_ranks({}) == {}


def _inputs(symbol: str, **overrides: object) -> FactorInputs:
    base = {
        "symbol": symbol,
        "fy_end": FY_END,
        "disclosed_at": disclosed_at(FY_END),
        "market_cap": 1000.0,
        "bm": 0.6,
        "net_cash_ratio": 0.3,
        "op_strict": 0.2,
        "op_simple": 0.25,
        "op_growth": 0.1,
        "equity_ratio": 0.6,
        "roe": 0.16,
        "pbr": 1.66,
        "catalyst": None,
        "missing": (),
    }
    base.update(overrides)
    return FactorInputs(**base)  # type: ignore[arg-type]


def test_strict_policy_drops_a_dimension_with_one_missing_component() -> None:
    inputs = {
        "A": _inputs("A"),
        "B": _inputs("B"),
        "C": _inputs("C", op_growth=None, missing=("op_growth",)),
    }
    strict = score_universe(inputs, decision_date=ELIGIBLE)
    graceful = score_universe(inputs, missing_policy="graceful", decision_date=ELIGIBLE)

    assert strict.scores["C"].dimensions["quality"] is None
    assert strict.scores["A"].dimensions["quality"] is not None
    assert graceful.scores["C"].dimensions["quality"] is not None
    assert strict.coverage["missing_rate_by_dimension"]["quality"] == pytest.approx(1 / 3)


def test_catalyst_never_earns_points_and_weights_are_renormalised() -> None:
    inputs = {"A": _inputs("A"), "B": _inputs("B", bm=0.2, net_cash_ratio=0.1)}
    scores = score_universe(inputs, decision_date=ELIGIBLE)

    for item in scores.scores.values():
        assert item.dimensions["catalyst"] is None
        assert item.components["catalyst"] is None
    # value/quality/safety only, renormalised to sum 1: A wins on value.
    assert scores.ranked()[0].symbol == "A"
    assert scores.scores["A"].composite == pytest.approx(
        (0.35 * 1.0 + 0.30 * 0.5 + 0.20 * 0.5) / 0.85
    )


def test_equal_weights_differ_from_value_heavy_weights() -> None:
    inputs = {
        "cheap_weak": _inputs(
            "cheap_weak",
            bm=1.0,
            net_cash_ratio=1.0,
            op_strict=0.01,
            op_simple=0.01,
            equity_ratio=0.1,
        ),
        "rich_strong": _inputs(
            "rich_strong",
            bm=0.1,
            net_cash_ratio=0.01,
            op_strict=0.9,
            op_simple=0.9,
            equity_ratio=0.9,
        ),
    }
    equal = score_universe(inputs, weights=ScoreWeights.equal(), decision_date=ELIGIBLE)
    value_heavy = score_universe(
        inputs, weights=ScoreWeights(0.9, 0.05, 0.05, 0.0), decision_date=ELIGIBLE
    )

    # Equal weights: two strong dimensions (quality+safety) beat one (value).
    assert equal.ranked()[0].symbol == "rich_strong"
    # Value-dominated weights flip the ranking to the cheap, weakly profitable name.
    assert value_heavy.ranked()[0].symbol == "cheap_weak"


def test_baseline_filter_matches_spec_section_3a() -> None:
    rule = BaselineFilter()
    passing = _inputs("pass", pbr=0.9, roe=0.12, equity_ratio=0.5, op_simple=0.16)
    high_pbr = _inputs("pbr", pbr=1.01)
    low_roe = _inputs("roe", roe=0.09)
    thin_equity = _inputs("eq", equity_ratio=0.39)
    low_op = _inputs("op", op_simple=0.149)
    missing = _inputs("miss", op_simple=None)

    assert rule.passes(passing)
    for name, item in (
        ("pbr", high_pbr),
        ("roe", low_roe),
        ("equity", thin_equity),
        ("op", low_op),
        ("missing", missing),
    ):
        assert not rule.passes(item), name


def _prices(closes: list[tuple[date, float]]) -> PriceSeries:
    return PriceSeries(
        opens={day: value for day, value in closes},
        closes={day: value for day, value in closes},
    )


def _synthetic_universe(days: int = 70):
    start = date(2025, 1, 1)
    calendar = [start + timedelta(days=offset) for offset in range(days)]
    fundamentals = {}
    prices = {}
    for index, symbol in enumerate(("1000.T", "2000.T", "3000.T")):
        fy_end = date(2024, 3, 31)
        fundamentals[symbol] = _fundamentals(
            symbol,
            [
                _annual(
                    fy_end=fy_end,
                    operating_income=100.0 + index * 200.0,
                    equity=500.0 + index * 100.0,
                    assets=1000.0,
                    current_assets=600.0 + index * 100.0,
                    liabilities=400.0,
                    shares_outstanding=100.0,
                )
            ],
        )
        # higher index drifts up faster
        prices[symbol] = _prices(
            [(day, 10.0 * (1 + 0.001 * index) ** offset) for offset, day in enumerate(calendar)]
        )
    benchmark = _prices(
        [(day, 100.0 * (1 + 0.0005) ** offset) for offset, day in enumerate(calendar)]
    )
    return fundamentals, prices, benchmark


def test_backtest_uses_only_disclosed_data_and_charges_costs() -> None:
    fundamentals, prices, benchmark = _synthetic_universe()
    params = PortfolioParams(label="test", top_n=2, missing_policy="graceful")
    outcome = run_backtest(
        fundamentals=fundamentals,
        prices=prices,
        benchmark=benchmark,
        start=min(benchmark.dates),
        end=max(benchmark.dates),
        params=params,
        selection=select_ranked,
    )

    assert outcome.lookahead_violations == 0
    assert outcome.sessions == len(benchmark.dates)
    assert outcome.rebalances >= 2
    assert all(count <= 2 for count in outcome.holdings)
    assert total_return(outcome.equity_curve) != pytest.approx(0.0)

    free = run_backtest(
        fundamentals=fundamentals,
        prices=prices,
        benchmark=benchmark,
        start=min(benchmark.dates),
        end=max(benchmark.dates),
        params=PortfolioParams(
            label="free", top_n=2, missing_policy="graceful", commission_bps=0, slippage_bps=0
        ),
        selection=select_ranked,
    )
    assert total_return(outcome.equity_curve) < total_return(free.equity_curve)


def test_backtest_baseline_selection_and_empty_result_behaviour() -> None:
    fundamentals, prices, benchmark = _synthetic_universe()
    outcome = run_backtest(
        fundamentals=fundamentals,
        prices=prices,
        benchmark=benchmark,
        start=min(benchmark.dates),
        end=max(benchmark.dates),
        params=PortfolioParams(label="baseline", top_n=20),
        selection=select_baseline,
    )
    # Synthetic names are expensive (PBR >> 1) so the §3-A screen stays empty
    # and the portfolio sits in cash: no look-ahead, no phantom trades.
    assert outcome.lookahead_violations == 0
    assert total_return(outcome.equity_curve) == pytest.approx(0.0)


def test_adjust_prices_neutralises_a_split_and_repairs_a_bad_tick() -> None:
    days = [date(2025, 1, 6), date(2025, 1, 7), date(2025, 1, 8), date(2025, 1, 9)]
    # 3-for-1 split on day 3 (Yahoo leaves the bar unadjusted), then a bad tick.
    closes = {
        days[0]: 3000.0,
        days[1]: 3030.0,
        days[2]: 1010.0,
        days[3]: 1020.1,
    }
    opens = dict(closes)
    adjusted, adjusted_opens, splits, repaired = adjust_prices(closes, opens)

    assert splits == (days[2],)
    assert repaired == ()
    # 3030 -> 1010 is a 3:1 split: the neutralised return is ~0, not -67%.
    assert adjusted[days[2]] / adjusted[days[1]] - 1 == pytest.approx(0.0, abs=0.02)
    assert adjusted[days[3]] / adjusted[days[2]] - 1 == pytest.approx(0.01, abs=0.003)
    assert adjusted_opens[days[0]] == pytest.approx(1000.0)
    # Raw closes are untouched for market-cap arithmetic.
    assert closes[days[0]] == 3000.0

    glitch_closes = {
        days[0]: 382.7,
        days[1]: 37.64,
        days[2]: 37.14,
        days[3]: 389.2,
    }
    adjusted_glitch, _, glitch_splits, glitch_repaired = adjust_prices(
        glitch_closes, dict(glitch_closes)
    )
    # Two consecutive bad bars that revert: both are interpolated, no split.
    assert glitch_repaired == (days[1], days[2])
    assert glitch_splits == ()
    assert adjusted_glitch[days[2]] / adjusted_glitch[days[0]] == pytest.approx(
        389.2 / 382.7, rel=0.05
    )


def test_price_series_exposes_raw_and_return_views() -> None:
    days = [date(2025, 1, 6), date(2025, 1, 7)]
    series = make_series({days[0]: 100.0, days[1]: 50.0}, {days[0]: 100.0, days[1]: 50.0})
    assert series.closes[days[0]] == 100.0
    assert series.return_close(days[0]) == pytest.approx(50.0)


def test_rebalance_sessions_are_first_session_of_each_month() -> None:
    days = [date(2025, 1, 6), date(2025, 1, 31), date(2025, 2, 3), date(2025, 3, 4)]
    assert rebalance_sessions(days) == [date(2025, 1, 6), date(2025, 2, 3), date(2025, 3, 4)]


def test_slice_outcome_rebases_and_keeps_window() -> None:
    fundamentals, prices, benchmark = _synthetic_universe()
    outcome = run_backtest(
        fundamentals=fundamentals,
        prices=prices,
        benchmark=benchmark,
        start=min(benchmark.dates),
        end=max(benchmark.dates),
        params=PortfolioParams(label="slice", top_n=2, missing_policy="graceful"),
        selection=select_ranked,
    )
    middle = outcome.equity_curve[len(outcome.equity_curve) // 2][0]
    sliced = slice_outcome(outcome, middle, outcome.end)

    assert sliced.equity_curve[0][0] == middle
    assert sliced.equity_curve[0][1] == pytest.approx(1.0)
    assert sliced.end == outcome.end


def test_benchmark_outcome_matches_buy_and_hold_return() -> None:
    _, _, benchmark = _synthetic_universe()
    outcome = benchmark_outcome(benchmark, min(benchmark.dates), max(benchmark.dates))
    first = benchmark.closes[min(benchmark.dates)]
    last = benchmark.closes[max(benchmark.dates)]

    assert total_return(outcome.equity_curve) == pytest.approx(last / first - 1)
