from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from yowayowa.backtest_models import BacktestRunRequest, BacktestSignal, BacktestStrategyDefinition
from yowayowa.services.backtest import _target_weights, run_backtest

START = date(2023, 1, 1)


def _strategy(lookback: int | None = 3) -> BacktestStrategyDefinition:
    return BacktestStrategyDefinition(
        id="crypto_trend_dual_ma",
        name="Crypto dual-MA trend filter",
        description="Synthetic trend regression fixture.",
        signal="crypto_trend_dual_ma",
        universe=["BTC", "ETH"],
        rebalance="daily",
        max_positions=2,
        weighting="equal",
        lookback=lookback,
    )


def _rows(prices: list[float]) -> list[dict[str, Any]]:
    return [
        {
            "as_of": (START + timedelta(days=index)).isoformat(),
            "open": prices[index - 1] if index else price,
            "close": price,
            "provider": "binance",
            "currency": "USDT",
            "source_url": "https://example.invalid/binance-fixture",
            "license_class": "personal_only",
            "retrieved_at": "2026-09-30T00:00:00Z",
        }
        for index, price in enumerate(prices)
    ]


def _mapping(prices: list[float]) -> dict[date, dict[str, Any]]:
    return {date.fromisoformat(row["as_of"]): row for row in _rows(prices)}


def _request(count: int) -> BacktestRunRequest:
    return BacktestRunRequest(
        strategy_id="crypto_trend_dual_ma",
        start=START,
        end=START + timedelta(days=count - 1),
        provider="binance",
        bootstrap_samples=0,
        commission_bps=10,
        slippage_bps=5,
    )


@pytest.mark.parametrize(
    "btc,eth,expected",
    [
        ([1.0, 2.0, 3.0], [4.0, 5.0, 6.0], {"BTC": 0.5, "ETH": 0.5}),
        ([1.0, 2.0, 3.0], [6.0, 5.0, 4.0], {"BTC": 1.0, "ETH": 0.0}),
        ([3.0, 2.0, 1.0], [4.0, 5.0, 6.0], {"BTC": 0.0, "ETH": 1.0}),
        ([3.0, 2.0, 1.0], [6.0, 5.0, 4.0], {"BTC": 0.0, "ETH": 0.0}),
        ([2.0, 2.0, 2.0], [5.0, 5.0, 5.0], {"BTC": 0.0, "ETH": 0.0}),
    ],
)
def test_independent_absolute_gates_and_active_equal_weights(
    btc: list[float], eth: list[float], expected: dict[str, float]
) -> None:
    rows = {"BTC": _mapping(btc), "ETH": _mapping(eth)}
    assert _target_weights(_strategy(), rows, START + timedelta(days=3)) == expected


def test_exact_lookback_boundary_and_missing_history_are_not_filled() -> None:
    rows = {"BTC": _mapping([1.0, 2.0, 3.0]), "ETH": _mapping([4.0, 5.0, 6.0])}
    assert _target_weights(_strategy(), rows, START + timedelta(days=2)) == {
        "BTC": 0.0,
        "ETH": 0.0,
    }
    assert _target_weights(_strategy(), rows, START + timedelta(days=3)) == {
        "BTC": 0.5,
        "ETH": 0.5,
    }
    del rows["ETH"][START + timedelta(days=1)]
    assert _target_weights(_strategy(), rows, START + timedelta(days=30)) == {
        "BTC": 1.0,
        "ETH": 0.0,
    }


def test_lookback_parameter_changes_decision() -> None:
    rows = {symbol: _mapping([10.0, 10.0, 1.0, 2.0, 3.0]) for symbol in ("BTC", "ETH")}
    decision = START + timedelta(days=5)
    assert _target_weights(_strategy(3), rows, decision) == {"BTC": 0.5, "ETH": 0.5}
    assert _target_weights(_strategy(5), rows, decision) == {"BTC": 0.0, "ETH": 0.0}


def test_no_ranking_cap_or_inverse_volatility_for_absolute_trend_gate() -> None:
    strategy = _strategy().model_copy(
        update={"max_positions": 1, "weighting": "inverse_volatility"}
    )
    rows = {symbol: _mapping([1.0, 2.0, 3.0]) for symbol in strategy.universe}
    assert _target_weights(strategy, rows, START + timedelta(days=3)) == {
        "BTC": 0.5,
        "ETH": 0.5,
    }
    result = run_backtest(
        _request(100),
        strategy,
        {symbol: _rows([float(i + 1) for i in range(100)]) for symbol in strategy.universe},
    )
    assert result.purged_sessions == 3


@pytest.mark.parametrize("last_close", [1.0, 4.5])
def test_both_assets_liquidate_instead_of_retaining_holdings(last_close: float) -> None:
    # At index 3 both hold; index 5 crosses below SMA or exactly equals
    # mean([4, 5, 4.5]). The lagged signal liquidates at index 6's open.
    prices = [1.0, 2.0, 3.0, 4.0, 5.0, last_close, 99.0, 100.0]
    histories = {symbol: _rows(prices) for symbol in ("BTC", "ETH")}
    result = run_backtest(_request(len(prices)), _strategy(), histories, _include_oos=False)
    exit_day = START + timedelta(days=6)
    assert {trade.symbol for trade in result.trades if trade.date == START + timedelta(days=3)} == {
        "BTC",
        "ETH",
    }
    exits = [trade for trade in result.trades if trade.date == exit_day]
    assert len(exits) == 2
    for trade in exits:
        assert trade.previous_weight == pytest.approx(0.5)
        assert trade.target_weight == 0.0
        assert trade.turnover == pytest.approx(0.5)
        assert trade.transaction_cost == pytest.approx(0.00075)
        assert trade.execution_price == last_close
    point = next(point for point in result.equity_curve if point.date == exit_day)
    assert point.cash == pytest.approx(point.equity)
    assert point.turnover == pytest.approx(1.0)
    assert point.transaction_cost == pytest.approx(0.0015)
    assert point.daily_return == pytest.approx(-0.0015)


def test_current_and_future_closes_cannot_change_current_decision() -> None:
    rows = {symbol: _mapping([1.0, 2.0, 3.0, 4.0, 5.0]) for symbol in ("BTC", "ETH")}
    decision = START + timedelta(days=3)
    baseline = _target_weights(_strategy(), rows, decision)
    for history in rows.values():
        for day, row in history.items():
            if day >= decision:
                row["close"] = 0.001
    assert _target_weights(_strategy(), rows, decision) == baseline


def test_future_bar_mutation_leaves_past_decisions_trades_and_equity_unchanged() -> None:
    prices = [100.0 + i + (4.0 if i % 5 == 0 else 0.0) for i in range(100)]
    histories = {symbol: _rows(prices) for symbol in ("BTC", "ETH")}
    request = _request(len(prices))
    baseline = run_backtest(request, _strategy(5), histories)
    changed = deepcopy(histories)
    cutoff = START + timedelta(days=60)
    for rows in changed.values():
        for row in rows:
            if date.fromisoformat(row["as_of"]) >= cutoff:
                row["open"] *= 10
                row["close"] *= 0.001
    altered = run_backtest(request, _strategy(5), changed)
    assert [p for p in baseline.equity_curve if p.date < cutoff] == [
        p for p in altered.equity_curve if p.date < cutoff
    ]
    assert [t for t in baseline.trades if t.date < cutoff] == [
        t for t in altered.trades if t.date < cutoff
    ]
    mapped = {
        symbol: {date.fromisoformat(row["as_of"]): row for row in rows}
        for symbol, rows in histories.items()
    }
    changed_mapped = {
        symbol: {date.fromisoformat(row["as_of"]): row for row in rows}
        for symbol, rows in changed.items()
    }
    for i in range(61):
        day = START + timedelta(days=i)
        assert _target_weights(_strategy(5), mapped, day) == _target_weights(
            _strategy(5), changed_mapped, day
        )


@pytest.mark.parametrize("lookback", [None, 3, 5, 100, 150, 200, 300])
def test_engine_complete_and_lookback_aligned_warmup_purge_oos(lookback: int | None) -> None:
    prices = [100.0]
    for i in range(1, 1100):
        prices.append(prices[-1] * (0.98 if i % 7 == 0 else 1.005))
    strategy = _strategy(lookback)
    histories = {symbol: _rows(prices) for symbol in strategy.universe}
    result = run_backtest(_request(len(prices)), strategy, histories)
    effective = lookback or 200
    assert result.status == "complete"
    assert result.purged_sessions == effective
    assert result.oos_start == START + timedelta(days=int(len(prices) * 0.7) + effective)
    assert result.oos_metrics is not None
    assert result.oos_metrics.cagr.sample_count == len(prices) - int(len(prices) * 0.7) - effective
    assert all(point.cash == point.equity for point in result.equity_curve[:effective])
    assert all(trade.date >= START + timedelta(days=effective) for trade in result.trades)
    assert result.trades
    assert any("full liquidation" in text for text in result.assumptions)


@pytest.mark.parametrize("lookback", [1, 1001])
def test_invalid_lookback_rejected(lookback: int) -> None:
    with pytest.raises(ValidationError):
        _strategy(lookback)


@pytest.mark.parametrize(
    "signal",
    [
        "momentum_12_1",
        "low_volatility",
        "mean_reversion_20",
        "equal_weight",
        "value_fundamental",
        "kiyohara_value",
    ],
)
def test_optional_lookback_does_not_change_existing_signal_results(signal: BacktestSignal) -> None:
    strategy = _strategy(None).model_copy(update={"signal": signal})
    prices = [100.0 + i + (4.0 if i % 5 == 0 else 0.0) for i in range(300)]
    histories = {symbol: _rows(prices) for symbol in strategy.universe}
    for rows in histories.values():
        for row in rows:
            row.update(
                disclosure_date="2022-12-31",
                pbr=1,
                per=10,
                net_cash_ratio=0.2,
                kiyohara_net_cash_ratio=0.3,
            )
    baseline = run_backtest(_request(len(prices)), strategy, histories)
    configured = run_backtest(
        _request(len(prices)), strategy.model_copy(update={"lookback": 3}), histories
    )
    assert baseline.metrics == configured.metrics
    assert baseline.in_sample_metrics == configured.in_sample_metrics
    assert baseline.oos_metrics == configured.oos_metrics
    assert baseline.oos_start == configured.oos_start
    assert baseline.purged_sessions == configured.purged_sessions
    assert baseline.equity_curve == configured.equity_curve
    assert baseline.trades == configured.trades
    assert _strategy(None).lookback is None
