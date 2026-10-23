from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from yowayowa.backtest_models import BacktestRunRequest, BacktestSignal, BacktestStrategyDefinition
from yowayowa.services.backtest import (
    _metrics,
    _target_weights,
    get_strategy,
    list_strategies,
    run_backtest,
)


def _small_strategy() -> BacktestStrategyDefinition:
    return BacktestStrategyDefinition(
        id="unit_equal",
        name="unit equal",
        description="Test-only equal weighting.",
        signal="equal_weight",
        universe=["AAA", "BBB"],
        rebalance="daily",
        max_positions=2,
    )


def _rows(
    start: date,
    count: int,
    *,
    growth: float = 0.001,
    provider: str = "alpaca",
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    close = 100.0
    day = start
    while len(rows) < count:
        if day.weekday() < 5:
            opening = close
            close *= 1 + growth
            rows.append(
                {
                    "as_of": day.isoformat(),
                    "open": opening,
                    "high": close,
                    "low": opening,
                    "close": close,
                    "provider": provider,
                    "source_url": f"https://data.example/{provider}",
                    "license_class": "personal_only",
                    "retrieved_at": datetime(2025, 6, 1, tzinfo=UTC).isoformat(),
                    "currency": "USD",
                }
            )
        day += timedelta(days=1)
    return rows


def test_strategy_catalog_is_data_driven_and_ids_are_unique() -> None:
    strategies = list_strategies()
    assert len(strategies) >= 3
    assert len({item.id for item in strategies}) == len(strategies)
    assert get_strategy("equal_weight").signal == "equal_weight"
    assert get_strategy("value_fundamental").signal == "value_fundamental"
    assert get_strategy("kiyohara_value").signal == "kiyohara_value"


@pytest.mark.parametrize(
    ("signal", "cash_key"),
    [("value_fundamental", "net_cash_ratio"), ("kiyohara_value", "kiyohara_net_cash_ratio")],
)
def test_fundamental_selection_is_point_in_time_and_ranks_best_assets(
    signal: BacktestSignal, cash_key: str
) -> None:
    decision = date(2025, 2, 3)
    strategy = BacktestStrategyDefinition(
        id="fundamental_test",
        name="fundamental test",
        description="test",
        signal=signal,
        universe=["AAA", "BBB", "CCC"],
        max_positions=1,
    )
    rows = {
        "AAA": {
            date(2025, 1, 31): {"disclosure_date": "2025-02-03", "pbr": 0.1, "per": 1, cash_key: 10}
        },
        "BBB": {
            date(2025, 1, 31): {"disclosure_date": "2025-01-30", "pbr": 1, "per": 10, cash_key: 0.1}
        },
        "CCC": {
            date(2025, 1, 31): {"disclosure_date": "2025-01-30", "pbr": 2, "per": 20, cash_key: 0}
        },
    }
    assert _target_weights(strategy, rows, decision) == {"BBB": 1.0}


def test_fundamental_missing_point_in_time_data_returns_no_selection() -> None:
    strategy = get_strategy("value_fundamental").model_copy(
        update={"universe": ["AAA"], "max_positions": 1}
    )
    decision = date(2025, 2, 3)
    rows = {"AAA": {date(2025, 1, 31): {"pbr": 1, "per": 2, "net_cash_ratio": 1}}}
    assert _target_weights(strategy, rows, decision) == {}


def test_strategy_definition_rejects_duplicate_symbols() -> None:
    with pytest.raises(ValidationError):
        BacktestStrategyDefinition(
            id="bad",
            name="bad",
            description="bad",
            signal="equal_weight",
            universe=["AAA", "AAA"],
        )


def test_backtest_uses_prior_data_and_charges_explicit_costs() -> None:
    start = date(2025, 1, 1)
    strategy = _small_strategy()
    histories = {symbol: _rows(start, 35, growth=0.002) for symbol in strategy.universe}
    request = BacktestRunRequest(
        strategy_id=strategy.id,
        start=date(2025, 1, 20),
        end=date(2025, 2, 20),
        commission_bps=10,
        slippage_bps=5,
        bootstrap_samples=100,
    )
    result = run_backtest(request, strategy, histories)
    assert result.equity_curve[0].date == request.start
    assert all(request.start <= point.date <= request.end for point in result.equity_curve)
    assert result.equity_curve[0].transaction_cost > 0
    assert result.trades
    assert result.metrics.bootstrap_status in {"available", "insufficient"}
    assert result.oos_start is not None
    assert result.oos_metrics is not None
    assert any("data dated before execution" in item for item in result.assumptions)
    assert result.provenance[0].source_url == "https://data.example/alpaca"
    assert result.provenance[0].license_class == "personal_only"
    assert result.provenance[0].retrieved_at == datetime(2025, 6, 1, tzinfo=UTC)


@pytest.mark.parametrize("frequency", ["weekly", "monthly"])
def test_non_rebalance_weights_track_fixed_share_overnight_and_intraday_returns(
    frequency: str,
) -> None:
    start = date(2025, 1, 6)
    strategy = _small_strategy().model_copy(update={"rebalance": frequency})
    histories = {symbol: _rows(start, 45, growth=0.0) for symbol in strategy.universe}
    # Establish equal holdings, then create an overnight gap and intraday reversal
    # on a session which is not a scheduled rebalance.
    histories["AAA"][2]["open"] = 120.0
    histories["AAA"][2]["close"] = 110.0
    histories["BBB"][2]["open"] = 90.0
    histories["BBB"][2]["close"] = 100.0
    request = BacktestRunRequest(
        strategy_id=strategy.id,
        start=start,
        end=date(2025, 2, 28),
        commission_bps=0,
        slippage_bps=0,
        bootstrap_samples=0,
    )
    result = run_backtest(request, strategy, histories)
    point = next(item for item in result.equity_curve if item.date == date(2025, 1, 8))
    # Equal-weight initial holdings are 0.005 shares of each asset. Value at
    # this close is fixed quantity times close: 0.005 * (110 + 100) = 1.05.
    # The gap and intraday reversal make stale close weights yield a different
    # result when the overnight open weights are discarded.
    expected = 0.005 * (110.0 + 100.0)
    assert point.equity == pytest.approx(expected)


def test_insufficient_sample_is_null_and_zero_variance_is_undefined() -> None:
    start = date(2025, 1, 6)
    strategy = _small_strategy()
    histories = {symbol: _rows(start, 8) for symbol in strategy.universe}
    request = BacktestRunRequest(
        strategy_id=strategy.id, start=start, end=date(2025, 1, 30), bootstrap_samples=0
    )
    result = run_backtest(request, strategy, histories)
    assert result.status == "insufficient"
    assert result.metrics.cagr.value is None
    assert result.metrics.cagr.status == "insufficient"
    zero_variance = _metrics([0.0] * 30, 0, None, False)
    assert zero_variance.sharpe_ratio.status == "undefined"
    assert zero_variance.calmar_ratio.status == "undefined"


def test_duplicate_and_mismatched_sessions_fail_closed() -> None:
    start = date(2025, 1, 6)
    strategy = _small_strategy()
    first = _rows(start, 25)
    second = _rows(start, 25)
    request = BacktestRunRequest(strategy_id=strategy.id, start=start, end=date(2025, 2, 7))
    with pytest.raises(ValueError, match="duplicate OHLCV"):
        run_backtest(request, strategy, {"AAA": first + first[:1], "BBB": second})
    with pytest.raises(ValueError, match="identical OHLCV sessions"):
        run_backtest(request, strategy, {"AAA": first, "BBB": second[1:]})


def test_non_us_and_unknown_currency_fail_closed() -> None:
    start = date(2025, 1, 6)
    strategy = _small_strategy()
    histories = {symbol: _rows(start, 25) for symbol in strategy.universe}
    histories["AAA"][0]["currency"] = "JPY"
    request = BacktestRunRequest(strategy_id=strategy.id, start=start, end=date(2025, 2, 7))
    with pytest.raises(ValueError, match="Non-USD"):
        run_backtest(request, strategy, histories)


def test_bootstrap_is_seeded_and_source_filter_is_explicit() -> None:
    start = date(2025, 1, 6)
    strategy = _small_strategy()
    histories = {symbol: _rows(start, 70) for symbol in strategy.universe}
    request = BacktestRunRequest(
        strategy_id=strategy.id, start=start, end=date(2025, 4, 18), bootstrap_samples=100
    )
    first = run_backtest(request, strategy, histories)
    second = run_backtest(request, strategy, histories)
    assert first.metrics.bootstrap_ci_95 == second.metrics.bootstrap_ci_95
    histories["AAA"] = _rows(start, 70, provider="other")
    no_data = run_backtest(request, strategy, histories)
    assert no_data.status == "insufficient"


def test_incomplete_provenance_is_rejected() -> None:
    start = date(2025, 1, 6)
    strategy = _small_strategy()
    histories = {symbol: _rows(start, 25) for symbol in strategy.universe}
    del histories["AAA"][0]["retrieved_at"]
    request = BacktestRunRequest(strategy_id=strategy.id, start=start, end=date(2025, 2, 7))
    with pytest.raises(ValueError, match="Incomplete OHLCV provenance"):
        run_backtest(request, strategy, histories)


def test_stock_split_preserves_price_series_continuity_and_avoids_spurious_losses() -> None:
    """Characterized regression C2: stock splits corroborated by SEC split calendar

    scale pre-split prices so overnight return reflects the true economic performance
    (flat / continuous), preserving terminal equity and avoiding spurious momentum/drawdown
    corruption.
    """
    start = date(2025, 1, 6)
    strategy = _small_strategy().model_copy(
        update={"rebalance": "monthly", "signal": "equal_weight"}
    )
    histories = {symbol: _rows(start, 20, growth=0.0) for symbol in strategy.universe}

    # Introduce a 2:1 split on symbol 'AAA' on session index 5 (2025-01-13):
    # From index 5 onwards, raw price is 50.0 instead of 100.0.
    for i in range(5, len(histories["AAA"])):
        histories["AAA"][i]["open"] = 50.0
        histories["AAA"][i]["high"] = 50.0
        histories["AAA"][i]["low"] = 50.0
        histories["AAA"][i]["close"] = 50.0

    request = BacktestRunRequest(
        strategy_id=strategy.id,
        start=start,
        end=date(2025, 1, 31),
        commission_bps=0,
        slippage_bps=0,
        bootstrap_samples=0,
    )
    split_calendar = {"AAA": [{"effective_date": "2025-01-13", "ratio": 2.0}]}
    result = run_backtest(
        request, strategy, histories, _include_oos=False, split_calendar=split_calendar
    )

    split_day = date(2025, 1, 13)
    point = next(item for item in result.equity_curve if item.date == split_day)
    assert point.daily_return == pytest.approx(0.0, abs=1e-6)
    assert point.equity == pytest.approx(1.0, abs=1e-6)
    assert result.equity_curve[-1].equity == pytest.approx(1.0, abs=1e-6)
    assert result.metrics.max_drawdown.value == pytest.approx(0.0, abs=1e-6)

    # Check disclosure assumption
    assert any("Raw OHLCV is adjusted for stock splits" in item for item in result.assumptions)
    # Check warning provenance
    assert any(
        "Split adjustment applied for AAA on 2025-01-13: ratio=2, basis=sec_calendar" in item
        for item in result.warnings
    )


def test_stock_split_execution_price_stays_nominal_raw_open() -> None:
    """Check that execution_price records the nominal raw open before (100) and after (50) split."""
    start = date(2025, 1, 6)
    strategy = _small_strategy().model_copy(update={"rebalance": "daily", "signal": "equal_weight"})
    histories = {
        "AAA": _rows(start, 20, growth=0.01),
        "BBB": _rows(start, 20, growth=0.0),
    }
    # 2:1 split on AAA on day 5 (2025-01-13)
    for i in range(5, len(histories["AAA"])):
        histories["AAA"][i]["open"] = round(histories["AAA"][i]["open"] / 2.0, 2)
        histories["AAA"][i]["close"] = round(histories["AAA"][i]["close"] / 2.0, 2)

    request = BacktestRunRequest(
        strategy_id=strategy.id,
        start=start,
        end=date(2025, 1, 31),
        commission_bps=0,
        slippage_bps=0,
        bootstrap_samples=0,
    )
    split_calendar = {"AAA": [{"effective_date": "2025-01-13", "ratio": 2.0}]}
    result = run_backtest(
        request, strategy, histories, _include_oos=False, split_calendar=split_calendar
    )

    aaa_trades = [t for t in result.trades if t.symbol == "AAA"]
    assert len(aaa_trades) >= 2
    pre_split_trade = next(t for t in aaa_trades if t.date == date(2025, 1, 10))
    split_trade = next(t for t in aaa_trades if t.date == date(2025, 1, 13))
    # Before split raw open was ~104; after split raw open was ~52.55
    assert pre_split_trade.execution_price > 100.0
    assert split_trade.execution_price < 60.0


def test_stock_split_genuine_crash_is_not_adjusted_negative_test() -> None:
    """Negative test 1: Persistent -50% crash (no split corroboration) must remain a loss."""
    start = date(2025, 1, 6)
    strategy = _small_strategy().model_copy(
        update={"rebalance": "monthly", "signal": "equal_weight"}
    )
    histories = {symbol: _rows(start, 20, growth=0.0) for symbol in strategy.universe}

    # Genuine uncorroborated -50% crash on AAA at index 5 (2025-01-13)
    for i in range(5, len(histories["AAA"])):
        for k in ("open", "high", "low", "close"):
            histories["AAA"][i][k] = 50.0

    request = BacktestRunRequest(
        strategy_id=strategy.id,
        start=start,
        end=date(2025, 1, 31),
        commission_bps=0,
        slippage_bps=0,
        bootstrap_samples=0,
    )
    result = run_backtest(request, strategy, histories, _include_oos=False)

    # AAA was 50% of the portfolio and dropped 50% -> portfolio equity should drop 25% (0.75)
    assert result.equity_curve[-1].equity == pytest.approx(0.75, abs=1e-4)
    assert result.metrics.max_drawdown.value == pytest.approx(-0.25, abs=1e-4)
    assert any(
        "Uncorroborated price discontinuity on AAA at 2025-01-13" in w for w in result.warnings
    )
    assert not any("Split adjustment applied for AAA" in w for w in result.warnings)


def test_stock_split_continuous_factor_is_not_applied_twice_negative_test() -> None:
    """Negative test 2: split_factor on continuous raw prices must NOT be applied twice."""
    start = date(2025, 1, 6)
    strategy = _small_strategy().model_copy(
        update={"rebalance": "monthly", "signal": "equal_weight"}
    )
    histories = {symbol: _rows(start, 20, growth=0.0) for symbol in strategy.universe}

    # Row carries split_factor=2.0 metadata, but raw prices are completely continuous at 100.0
    histories["AAA"][5]["split_factor"] = 2.0

    request = BacktestRunRequest(
        strategy_id=strategy.id,
        start=start,
        end=date(2025, 1, 31),
        commission_bps=0,
        slippage_bps=0,
        bootstrap_samples=0,
    )
    result = run_backtest(request, strategy, histories, _include_oos=False)

    # Must stay 1.0 (no fake +50% gain)
    assert result.equity_curve[-1].equity == pytest.approx(1.0, abs=1e-6)
    assert result.metrics.max_drawdown.value == pytest.approx(0.0, abs=1e-6)
    assert any(
        "Skipped uncorroborated split_factor=2 for AAA on 2025-01-13" in w for w in result.warnings
    )
    assert not any("Split adjustment applied for AAA" in w for w in result.warnings)


def test_stock_split_explicit_factor_and_adj_close_support() -> None:
    """Verify that explicit split_factor (corroborated) and adj_close are respected."""
    start = date(2025, 1, 6)
    strategy = _small_strategy().model_copy(
        update={"rebalance": "monthly", "signal": "equal_weight"}
    )

    # 1. Test explicit split_factor corroborated by 100 -> 50 price step
    histories_factor = {symbol: _rows(start, 20, growth=0.0) for symbol in strategy.universe}
    histories_factor["AAA"][5]["split_factor"] = 2.0
    for i in range(5, len(histories_factor["AAA"])):
        histories_factor["AAA"][i]["open"] = 50.0
        histories_factor["AAA"][i]["close"] = 50.0

    request = BacktestRunRequest(
        strategy_id=strategy.id,
        start=start,
        end=date(2025, 1, 31),
        commission_bps=0,
        slippage_bps=0,
        bootstrap_samples=0,
    )
    result_factor = run_backtest(request, strategy, histories_factor, _include_oos=False)
    assert result_factor.equity_curve[-1].equity == pytest.approx(1.0, abs=1e-6)
    assert any(
        "Split adjustment applied for AAA on 2025-01-13: ratio=2, basis=explicit_metadata" in w
        for w in result_factor.warnings
    )

    # 2. Test explicit adj_close
    histories_adj = {symbol: _rows(start, 20, growth=0.0) for symbol in strategy.universe}
    for row in histories_adj["AAA"]:
        row["adj_close"] = 50.0
        row["adj_open"] = 50.0
    result_adj = run_backtest(request, strategy, histories_adj, _include_oos=False)
    assert result_adj.equity_curve[-1].equity == pytest.approx(1.0, abs=1e-6)


def test_stock_split_missing_or_corrupt_calendar_warns(tmp_path: Path) -> None:
    """Missing or corrupt split calendar must emit an explicit warning instead of silent ignore."""
    start = date(2025, 1, 6)
    strategy = _small_strategy().model_copy(
        update={"rebalance": "monthly", "signal": "equal_weight"}
    )
    histories = {symbol: _rows(start, 20, growth=0.0) for symbol in strategy.universe}
    request = BacktestRunRequest(
        strategy_id=strategy.id,
        start=start,
        end=date(2025, 1, 31),
        commission_bps=0,
        slippage_bps=0,
        bootstrap_samples=0,
    )

    # 1. Missing calendar file
    missing_path = tmp_path / "nonexistent-split-calendar.json"
    res_missing = run_backtest(
        request, strategy, histories, _include_oos=False, split_calendar_path=missing_path
    )
    assert any(
        "split calendar unavailable:" in w and "splits not adjusted" in w
        for w in res_missing.warnings
    )

    # 2. Corrupt calendar file
    corrupt_path = tmp_path / "corrupt-split-calendar.json"
    corrupt_path.write_text("{invalid_json: true", encoding="utf-8")
    res_corrupt = run_backtest(
        request, strategy, histories, _include_oos=False, split_calendar_path=corrupt_path
    )
    assert any(
        "split calendar unavailable:" in w and "splits not adjusted" in w
        for w in res_corrupt.warnings
    )

    # 3. Calendar with skipped invalid entry
    bad_entry_calendar = {"AAA": [{"effective_date": "not-a-date", "ratio": "invalid"}]}
    res_bad_entry = run_backtest(
        request, strategy, histories, _include_oos=False, split_calendar=bad_entry_calendar
    )
    assert any("split calendar: skipped invalid entry" in w for w in res_bad_entry.warnings)
