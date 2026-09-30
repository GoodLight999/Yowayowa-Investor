from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from pydantic import ValidationError

from yowayowa.backtest_models import BacktestRunRequest, BacktestStrategyDefinition
from yowayowa.services.backtest import _metrics, get_strategy, list_strategies, run_backtest


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
