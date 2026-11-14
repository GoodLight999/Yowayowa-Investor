from __future__ import annotations

import importlib.util
import math
from collections.abc import Sequence
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
from statistics import stdev
from types import ModuleType
from typing import Any

import pytest
from pydantic import ValidationError

from yowayowa.backtest_models import (
    VOLATILITY_TARGET_SYMBOL,
    BacktestRunRequest,
    BacktestSignal,
    BacktestStrategyDefinition,
)
from yowayowa.services import backtest
from yowayowa.services.backtest import _target_weights, run_backtest

START = date(2021, 1, 4)
SYMBOLS = ["SPY", "IWM", "JNJ", "PG"]


def _strategy(
    lookback: int | None = 2, target: float | None = None, **updates: Any
) -> BacktestStrategyDefinition:
    return BacktestStrategyDefinition.model_validate(
        {
            "id": "vol_target_test",
            "name": "Volatility target fixture",
            "description": "Synthetic cash-overlay regression fixture.",
            "signal": "vol_target_defensive",
            "universe": SYMBOLS,
            "rebalance": "daily",
            "max_positions": 4,
            "weighting": "equal",
            "lookback": lookback,
            "target_volatility": target,
            **updates,
        }
    )


def _rows(prices: Sequence[float], *, flat_open: bool = False) -> list[dict[str, Any]]:
    return [
        {
            "as_of": (START + timedelta(days=i)).isoformat(),
            "open": price if flat_open else prices[i - 1] if i else price,
            "close": price,
            "provider": "alpaca",
            "currency": "USD",
            "source_url": "https://example.invalid/alpaca-fixture",
            "license_class": "personal_only",
            "retrieved_at": "2026-09-30T00:00:00Z",
        }
        for i, price in enumerate(prices)
    ]


def _mapping(prices: Sequence[float]) -> dict[str, dict[date, dict[str, Any]]]:
    return {
        symbol: {date.fromisoformat(row["as_of"]): row for row in _rows(prices)}
        for symbol in SYMBOLS
    }


def _request(count: int, *, start: int = 0, **updates: Any) -> BacktestRunRequest:
    return BacktestRunRequest.model_validate(
        {
            "strategy_id": "vol_target_test",
            "start": START + timedelta(days=start),
            "end": START + timedelta(days=count - 1),
            "bootstrap_samples": 0,
            **updates,
        }
    )


@pytest.mark.parametrize("target", [None, 0.10, 0.15, 0.20])
def test_sample_sigma_hand_calculation_and_equal_sleeve(target: float | None) -> None:
    # Two returns +10%, -10%: sample sigma = sqrt((.1²+.1²)/(2-1)).
    rows = _mapping([100.0, 110.0, 99.0])
    sigma = math.sqrt(0.02)
    expected_w = (target if target is not None else 0.15) / math.sqrt(252) / sigma
    weights = _target_weights(_strategy(target=target), rows, START + timedelta(days=3))
    assert set(weights) == set(SYMBOLS)
    assert list(weights.values()) == pytest.approx([expected_w / 4] * 4)
    assert sum(weights.values()) == pytest.approx(expected_w)
    assert sum(weights.values()) <= 1 + 1e-12


@pytest.mark.parametrize("prices", [[100, 100, 100], [100, 100.001, 100.003]])
def test_zero_or_low_volatility_hard_cap_no_leverage(prices: list[float]) -> None:
    weights = _target_weights(_strategy(), _mapping(prices), START + timedelta(days=3))
    assert weights == dict.fromkeys(SYMBOLS, 0.25)
    assert sum(weights.values()) == 1.0


def test_exact_lookback_boundary_missing_history_explicit_zero() -> None:
    rows = _mapping([100, 110, 99])
    zero = dict.fromkeys(SYMBOLS, 0.0)
    assert _target_weights(_strategy(), rows, START + timedelta(days=2)) == zero
    assert sum(_target_weights(_strategy(), rows, START + timedelta(days=3)).values()) > 0
    del rows["SPY"][START + timedelta(days=1)]
    assert _target_weights(_strategy(), rows, START + timedelta(days=30)) == zero
    assert _target_weights(_strategy(), {}, START + timedelta(days=30)) == zero
    assert zero  # nonempty is essential for full liquidation


def test_only_spy_estimates_sigma_and_lookback_counts_returns() -> None:
    rows = _mapping([100, 110, 99, 99, 99])
    day = START + timedelta(days=5)
    assert _target_weights(_strategy(2), rows, day) == dict.fromkeys(SYMBOLS, 0.25)
    assert sum(_target_weights(_strategy(4), rows, day).values()) < 1
    for symbol in SYMBOLS[1:]:
        rows[symbol] = {}
    assert _target_weights(_strategy(2), rows, day) == dict.fromkeys(SYMBOLS, 0.25)


def test_default_lookback_and_target_are_21_and_15_percent() -> None:
    rows = _mapping([100 + i + (4 if i % 3 == 0 else 0) for i in range(22)])
    assert _target_weights(_strategy(None), rows, START + timedelta(days=21)) == dict.fromkeys(
        SYMBOLS, 0.0
    )
    day = START + timedelta(days=22)
    assert _target_weights(_strategy(None), rows, day) == _target_weights(
        _strategy(21, 0.15), rows, day
    )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_sigma_fails_closed(value: float, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backtest, "stdev", lambda returns: value)
    assert _target_weights(_strategy(), _mapping([100, 110, 99]), START + timedelta(days=3)) == {
        symbol: 0.0 for symbol in SYMBOLS
    }


@pytest.mark.parametrize("value", [float("nan"), float("inf"), 0.0])
def test_invalid_close_returns_are_not_imputed(value: float) -> None:
    assert _target_weights(
        _strategy(), _mapping([100, value, 99]), START + timedelta(days=3)
    ) == dict.fromkeys(SYMBOLS, 0.0)


def test_current_and_future_closes_cannot_change_current_decision() -> None:
    rows = _mapping([100, 110, 99, 102, 107])
    day = START + timedelta(days=3)
    expected = _target_weights(_strategy(), rows, day)
    for history in rows.values():
        for session, row in history.items():
            if session >= day:
                row["close"] = 0.001
    assert _target_weights(_strategy(), rows, day) == expected


def test_future_mutation_leaves_past_equity_trades_decisions_unchanged() -> None:
    prices = [100 + i + (4 if i % 5 == 0 else 0) for i in range(120)]
    histories = {symbol: _rows(prices) for symbol in SYMBOLS}
    changed = deepcopy(histories)
    cutoff = START + timedelta(days=80)
    for rows in changed.values():
        for row in rows:
            if date.fromisoformat(row["as_of"]) >= cutoff:
                row["open"] *= 10
                row["close"] *= 0.001
    original = run_backtest(_request(len(prices)), _strategy(), histories)
    altered = run_backtest(_request(len(prices)), _strategy(), changed)
    assert [p for p in original.equity_curve if p.date < cutoff] == [
        p for p in altered.equity_curve if p.date < cutoff
    ]
    assert [t for t in original.trades if t.date < cutoff] == [
        t for t in altered.trades if t.date < cutoff
    ]
    mapped = {
        symbol: {date.fromisoformat(row["as_of"]): row for row in rows}
        for symbol, rows in changed.items()
    }
    for i in range(81):
        day = START + timedelta(days=i)
        assert _target_weights(_strategy(), _mapping(prices), day) == _target_weights(
            _strategy(), mapped, day
        )


@pytest.mark.parametrize("lookback", [None, 2, 5, 21, 40])
def test_engine_complete_warmup_purge_oos_and_assumptions(lookback: int | None) -> None:
    prices = [100.0]
    for i in range(1, 400):
        prices.append(prices[-1] * (0.97 if i % 7 == 0 else 1.004))
    result = run_backtest(
        _request(len(prices)), _strategy(lookback), {s: _rows(prices) for s in SYMBOLS}
    )
    warmup = (lookback or 21) + 1
    assert result.status == "complete"
    assert result.purged_sessions == warmup
    assert result.oos_start == START + timedelta(days=int(len(prices) * 0.7) + warmup)
    assert result.oos_metrics is not None
    assert result.oos_metrics.cagr.sample_count == len(prices) - int(len(prices) * 0.7) - warmup
    assert all(p.cash == p.equity for p in result.equity_curve[:warmup])
    assert all(t.date >= START + timedelta(days=warmup) for t in result.trades)
    assert result.trades
    for text in ["interest-free USD cash", "w<=1 hard cap", "full liquidation", "SPY"]:
        assert text in result.assumptions[3]
    assert f"warmup/purge: {warmup}" in result.assumptions[3]


def test_inverse_volatility_and_position_ranking_are_not_used() -> None:
    rows = _mapping([100, 110, 99])
    day = START + timedelta(days=3)
    strategy = _strategy(weighting="inverse_volatility", max_positions=1)
    assert _target_weights(strategy, rows, day) == _target_weights(_strategy(), rows, day)
    prices = [100 + i + (4 if i % 5 == 0 else 0) for i in range(120)]
    result = run_backtest(_request(len(prices)), strategy, {s: _rows(prices) for s in SYMBOLS})
    assert result.purged_sessions == 3


def test_actual_partial_investment_cash_is_exact_at_flat_rebalance_close() -> None:
    prices = [100, 110, 99, 99, 99]
    histories = {s: _rows(prices, flat_open=True) for s in SYMBOLS}
    result = run_backtest(
        _request(len(prices), start=3), _strategy(), histories, _include_oos=False
    )
    w = sum(_target_weights(_strategy(), _mapping(prices), START + timedelta(days=3)).values())
    point = result.equity_curve[0]
    assert 0 < w < 1
    assert point.cash == point.equity * (1 - w)  # strict, not approximate
    assert point.turnover == w
    assert point.transaction_cost == w * 0.0015


def test_flat_prices_cash_is_interest_free_cost_only(monkeypatch: pytest.MonkeyPatch) -> None:
    # Flat window alone has sigma=0 and w=1. A controlled nonzero sigma keeps
    # w<1 throughout, isolating the engine's cash/cost mechanics from the signal.
    monkeypatch.setattr(backtest, "stdev", lambda returns: 0.02)
    prices = [100.0] * 50
    result = run_backtest(
        _request(len(prices), start=3),
        _strategy(),
        {s: _rows(prices) for s in SYMBOLS},
        _include_oos=False,
    )
    w = 0.15 / math.sqrt(252) / 0.02
    for point in result.equity_curve:
        assert point.cash == point.equity * (1 - w)
        assert point.daily_return == (1 - point.transaction_cost) - 1
    assert all(p.daily_return == 0.0 for p in result.equity_curve[1:])
    assert all(p.equity == result.equity_curve[0].equity for p in result.equity_curve)


def test_full_to_half_turnover_cost_and_full_liquidation(monkeypatch: pytest.MonkeyPatch) -> None:
    # Only sigma is controlled; the real target branch and execution loop run.
    sigma_star = 0.15 / math.sqrt(252)
    sigmas = iter([0.0, 2 * sigma_star, float("inf"), float("inf")])
    monkeypatch.setattr(
        backtest, "stdev", lambda returns: next(sigmas) if len(returns) == 2 else stdev(returns)
    )
    prices = [100.0] * 7
    result = run_backtest(
        _request(7, start=3),
        _strategy(),
        {s: _rows(prices) for s in SYMBOLS},
        _include_oos=False,
    )
    first, half, cash, retained = result.equity_curve
    assert first.cash == 0.0
    assert first.turnover == 1.0
    assert half.turnover == 0.5
    assert half.transaction_cost == 0.5 * 0.0015
    assert half.cash == half.equity * 0.5
    assert cash.turnover == 0.5
    assert cash.transaction_cost == 0.5 * 0.0015
    assert cash.cash == cash.equity
    exits = [t for t in result.trades if t.date == cash.date]
    assert len(exits) == 4
    assert all(t.previous_weight == 0.125 and t.target_weight == 0.0 for t in exits)
    assert all(t.turnover == 0.125 and t.transaction_cost == 0.125 * 0.0015 for t in exits)
    assert retained.turnover == 0.0
    assert retained.daily_return == 0.0
    assert retained.cash == retained.equity == cash.cash


def test_non_rebalance_drift_preserves_cash_balance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backtest, "stdev", lambda returns: 2 * 0.15 / math.sqrt(252))
    # Jan 11 Monday invests half; both overnight and intraday price moves follow.
    prices = [100.0] * 8 + [121.0, 108.9, 130.0, 140.0, 150.0]
    histories = {s: _rows(prices) for s in SYMBOLS}
    for rows in histories.values():
        for i in range(8, len(prices)):
            rows[i]["open"] = prices[i - 1] * 1.05
    result = run_backtest(
        _request(len(prices), start=7),
        _strategy(rebalance="weekly"),
        histories,
        _include_oos=False,
    )
    initial = result.equity_curve[0]
    assert initial.cash == initial.equity * 0.5
    assert len({p.equity for p in result.equity_curve}) > 1
    for point in result.equity_curve[1:]:
        assert point.turnover == point.transaction_cost == 0.0
        # Floating-point weight drift reconstructs dollars within machine error.
        assert point.cash == pytest.approx(initial.cash, rel=0, abs=1e-15)


@pytest.mark.parametrize("target", [0.0, -0.1, 1.00001, float("nan"), float("inf")])
def test_invalid_target_volatility_rejected(target: float) -> None:
    with pytest.raises(ValidationError):
        _strategy(target=target)


@pytest.mark.parametrize("target", [None, 1.0, 0.00001])
def test_valid_target_bounds_and_normalized_spy(target: float | None) -> None:
    strategy = _strategy(target=target, universe=[" spy ", "IWM"])
    assert strategy.universe == [VOLATILITY_TARGET_SYMBOL, "IWM"]
    assert strategy.target_volatility == target


def test_spy_required_only_for_vol_target() -> None:
    with pytest.raises(ValidationError, match="requires SPY"):
        _strategy(universe=["IWM", "PG"])
    assert _strategy(signal="equal_weight", universe=["IWM"]).universe == ["IWM"]


def test_none_parameter_preserves_existing_serialized_contract() -> None:
    strategy = _strategy(signal="equal_weight")
    assert strategy.target_volatility is None
    assert "target_volatility" not in strategy.model_dump(mode="json")
    assert "target_volatility" not in strategy.model_dump()
    assert _strategy(target=0.15).model_dump(mode="json")["target_volatility"] == 0.15


@pytest.mark.parametrize(
    "signal",
    [
        "momentum_12_1",
        "low_volatility",
        "mean_reversion_20",
        "equal_weight",
        "value_fundamental",
        "kiyohara_value",
        "crypto_trend_dual_ma",
    ],
)
@pytest.mark.parametrize("target", [0.10, 0.15, 0.20])
def test_optional_target_does_not_change_existing_signal_results(
    signal: BacktestSignal, target: float
) -> None:
    strategy = _strategy(lookback=None, signal=signal, rebalance="monthly")
    prices = [100 + i + (4 if i % 5 == 0 else 0) for i in range(900)]
    histories = {s: _rows(prices) for s in SYMBOLS}
    for rows in histories.values():
        for row in rows:
            row.update(
                disclosure_date="2020-01-01",
                pbr=1,
                per=10,
                net_cash_ratio=0.2,
                kiyohara_net_cash_ratio=0.3,
            )
    baseline = run_backtest(_request(len(prices)), strategy, histories)
    changed = run_backtest(
        _request(len(prices)), strategy.model_copy(update={"target_volatility": target}), histories
    )
    baseline_data, changed_data = baseline.model_dump(), changed.model_dump()
    changed_data["strategy"].pop("target_volatility")
    assert baseline_data == changed_data  # all metrics, curve, trades, warnings and split


def test_missing_requested_session_is_rejected_not_filled() -> None:
    prices = [100 + i for i in range(50)]
    histories = {s: _rows(prices) for s in SYMBOLS}
    del histories["SPY"][20]
    with pytest.raises(ValueError, match="identical OHLCV sessions"):
        run_backtest(_request(len(prices)), _strategy(), histories)


def test_empty_spy_history_returns_insufficient_without_inventing_prices() -> None:
    prices = [100 + i for i in range(50)]
    histories = {s: _rows(prices) for s in SYMBOLS}
    histories["SPY"] = []
    result = run_backtest(_request(len(prices)), _strategy(), histories)
    assert result.status == "insufficient"
    assert result.equity_curve == result.trades == []


@pytest.fixture
def runner() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "s02_runner", Path(__file__).resolve().parents[1] / "run_backtests_vol_target.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_runner_has_six_frozen_inline_definitions(runner: ModuleType) -> None:
    strategies = runner.definitions()
    assert [s.id for s in strategies] == [
        "s02_vol_target_10",
        "s02_vol_target_15",
        "s02_vol_target_20",
        "s02_vol_target_15_daily",
        "s02_baseline_ew_weekly",
        "s02_baseline_ew_monthly",
    ]
    assert [s.target_volatility for s in strategies] == [0.10, 0.15, 0.20, 0.15, None, None]
    assert [s.rebalance for s in strategies] == [
        "weekly",
        "weekly",
        "weekly",
        "daily",
        "weekly",
        "monthly",
    ]
    assert all(s.universe == SYMBOLS and s.weighting == "equal" for s in strategies)


def test_runner_diagnostics_track_actual_not_hypothetical_daily_w(runner: ModuleType) -> None:
    prices = [100 + i + (10 if i % 5 == 0 else 0) for i in range(90)]
    strategy = _strategy(rebalance="weekly", target=0.15)
    histories = {s: _rows(prices) for s in SYMBOLS}
    request = _request(len(prices), start=7)
    result = run_backtest(request, strategy, histories, _include_oos=False)
    diagnostic = runner.diagnostic_segment(result, histories)
    path = diagnostic["exposure_path"]
    assert any(row["daily_signal_w"] != row["held_target_w"] for row in path)
    previous = 0.0
    changes = 0
    for point, row in zip(result.equity_curve, path, strict=True):
        assert row["actual_w"] == 1 - point.cash / point.equity
        if row["rebalance"]:
            assert row["held_target_w"] == row["daily_signal_w"]
            changes += row["held_target_w"] != previous
        else:
            assert row["held_target_w"] == previous
        previous = row["held_target_w"]
    assert diagnostic["changed_w_rebalances"] == changes
    assert diagnostic["high_sigma_sessions"] == len(diagnostic["high_sigma_evidence"])
    assert all(row["spy_sigma_annualized"] > 0.30 for row in diagnostic["high_sigma_evidence"])
    assert result.metrics.annual_turnover.value is not None
    assert diagnostic["annualized_cost_drag"] == pytest.approx(
        result.metrics.annual_turnover.value * 0.0015
    )
    assert diagnostic["realized_annual_volatility"] == stdev(
        p.daily_return for p in result.equity_curve
    ) * math.sqrt(252)
    assert diagnostic["targeting_error"] == diagnostic["realized_annual_volatility"] - 0.15


def test_runner_segment_metrics_keep_engine_values_except_ci(runner: ModuleType) -> None:
    prices = [100 + i + (5 if i % 5 == 0 else 0) for i in range(120)]
    histories = {s: _rows(prices) for s in SYMBOLS}
    strategy, request = _strategy(), _request(len(prices))
    full = run_backtest(request, strategy, histories)
    split = int(len(prices) * 0.7)
    result = runner.segment(
        request,
        strategy,
        histories,
        request.start,
        START + timedelta(days=split - 1),
        full.in_sample_metrics,
    )
    assert result.metrics.bootstrap_status == "available"
    assert (
        result.metrics.model_copy(update={"bootstrap_ci_95": None, "bootstrap_status": "disabled"})
        == full.in_sample_metrics
    )
