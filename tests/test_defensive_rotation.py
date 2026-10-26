from __future__ import annotations

import hashlib
import importlib.util
import math
from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import date, timedelta
from itertools import pairwise
from pathlib import Path
from statistics import stdev
from types import ModuleType
from typing import Any

import pytest
from pydantic import ValidationError

from yowayowa.backtest_models import BacktestRunRequest, BacktestStrategyDefinition
from yowayowa.services import backtest
from yowayowa.services.backtest import _risk_on_regime, _target_weights, run_backtest

START = date(2021, 1, 4)
SYMBOLS = ["SPY", "QQQ", "IWM", "JNJ", "PG", "XOM", "JPM", "AAPL", "MSFT", "NVDA"]


def strategy(**updates: Any) -> BacktestStrategyDefinition:
    return BacktestStrategyDefinition.model_validate(
        {
            "id": "s05_test",
            "name": "S-05 fixture",
            "description": "Synthetic frozen contract fixture.",
            "signal": "defensive_rotation",
            "universe": SYMBOLS,
            "rebalance": "daily",
            **updates,
        }
    )


def rows(prices: Sequence[float]) -> list[dict[str, Any]]:
    return [
        {
            "as_of": (START + timedelta(days=i)).isoformat(),
            "open": prices[i - 1] if i else price,
            "close": price,
            "provider": "alpaca",
            "currency": "USD",
            "source_url": "https://example.invalid/alpaca-fixture",
            "license_class": "personal_only",
            "retrieved_at": "2026-09-30T00:00:00Z",
        }
        for i, price in enumerate(prices)
    ]


def mapping(prices: Mapping[str, Sequence[float]]) -> dict[str, dict[date, dict[str, Any]]]:
    return {
        symbol: {date.fromisoformat(row["as_of"]): row for row in rows(values)}
        for symbol, values in prices.items()
    }


def request(count: int, start: int = 0, **updates: Any) -> BacktestRunRequest:
    return BacktestRunRequest.model_validate(
        {
            "strategy_id": "s05_test",
            "start": START + timedelta(days=start),
            "end": START + timedelta(days=count - 1),
            "bootstrap_samples": 0,
            **updates,
        }
    )


def rising_gate() -> list[float]:
    # Both strict gates pass; momentum is exactly zero (index -22 still 100).
    return [100.0] * 232 + [100.0 + i for i in range(1, 22)]


@pytest.mark.parametrize(
    "prices, expected",
    [
        ([100.0] * 99, False),
        ([100.0] * 100, False),
        ([100.0 + i for i in range(100)], True),
        ([200.0 - i for i in range(100)], False),
        ([100.0] * 79 + [120.0] * 20 + [100.0], False),  # negative return
        ([100.0] * 79 + [120.0] + [90.0] * 19 + [120.0], False),  # return equality
        ([200.0] * 79 + [90.0] * 20 + [100.0], False),  # positive return but below SMA
        ([120.0] * 79 + [21.0] * 20 + [100.0], False),  # close equals SMA100
    ],
)
def test_risk_gate_and_strict_equalities(prices: list[float], expected: bool) -> None:
    assert (
        _risk_on_regime(mapping({"SPY": prices}), START + timedelta(days=len(prices))) is expected
    )


def test_gate_missing_spy_is_fail_closed() -> None:
    assert not _risk_on_regime({}, START)
    mapped = mapping({"SPY": [100 + i for i in range(100)]})
    assert _risk_on_regime(mapped, START + timedelta(days=100))
    del mapped["SPY"][START + timedelta(days=10)]
    assert not _risk_on_regime(mapped, START + timedelta(days=100))


@pytest.mark.parametrize("bad", [0.0, float("nan"), float("inf"), -1.0])
def test_invalid_regime_inputs_fail_closed(bad: float) -> None:
    prices = [100.0 + i for i in range(100)]
    prices[30] = bad
    assert not _risk_on_regime(mapping({"SPY": prices}), START + timedelta(days=100))


def test_growth_exact_formula_top_five_equal_weight_and_ties() -> None:
    prices = {symbol: [100.0] * 253 for symbol in SYMBOLS}
    prices["SPY"] = rising_gate()
    prices["NVDA"][231] = 180.0
    prices["QQQ"][231] = 160.0
    prices["MSFT"][231] = 140.0
    prices["AAPL"][231] = 120.0
    day = START + timedelta(days=253)
    mapped = mapping(prices)
    assert _risk_on_regime(mapped, day)
    target = _target_weights(strategy(), mapped, day)
    assert target == {
        s: 0.2 if s in {"NVDA", "QQQ", "MSFT", "AAPL", "IWM"} else 0.0 for s in SYMBOLS
    }
    assert sum(target.values()) == 1.0
    # Recent skipped session changes cannot alter momentum rankings.
    for symbol in SYMBOLS[1:]:
        for i in range(232, 253):
            mapped[symbol][START + timedelta(days=i)]["close"] = 10000.0
    assert _target_weights(strategy(), mapped, day) == target


def test_growth_253_boundary_empty_and_shortfall_fixed_twenty_percent() -> None:
    mapped = mapping({"SPY": rising_gate(), "AAPL": [100.0] * 252})
    day = START + timedelta(days=253)
    assert _target_weights(strategy(), mapped, day) == {
        s: 0.2 if s == "SPY" else 0.0 for s in SYMBOLS
    }
    mapped["SPY"].pop(START)
    assert _risk_on_regime(mapped, day)
    assert _target_weights(strategy(), mapped, day) == dict.fromkeys(SYMBOLS, 0.0)
    assert _target_weights(strategy(), {}, day) == dict.fromkeys(SYMBOLS, 0.0)


def test_growth_ties_use_symbol_order_not_universe_order() -> None:
    mapped = mapping({s: rising_gate() if s == "SPY" else [100.0] * 253 for s in SYMBOLS})
    target = _target_weights(
        strategy(universe=list(reversed(SYMBOLS))), mapped, START + timedelta(days=253)
    )
    assert {s for s, weight in target.items() if weight} == {"AAPL", "IWM", "JNJ", "JPM", "MSFT"}


def test_defensive_sample_sigma_top_three_subset_and_equal_weight() -> None:
    prices = {s: [100.0] * 61 for s in SYMBOLS}
    for s, amplitude in [("JNJ", 0.001), ("PG", 0.002), ("XOM", 0.003), ("JPM", 0.004)]:
        series = [100.0]
        for i in range(60):
            series.append(series[-1] * (1 + amplitude * (-1 if i % 2 else 1)))
        prices[s] = series
        sigma = stdev([series[i + 1] / series[i] - 1 for i in range(60)])
        assert sigma == pytest.approx(amplitude * math.sqrt(60 / 59))
    mapped = mapping(prices)
    day = START + timedelta(days=61)
    assert not _risk_on_regime(mapped, day)
    target = _target_weights(strategy(), mapped, day)
    assert target == {s: 1 / 3 if s in {"JNJ", "PG", "XOM"} else 0.0 for s in SYMBOLS}


@pytest.mark.parametrize("count", [0, 1, 2, 3, 4])
def test_defensive_candidate_counts_equal_share_or_full_cash(count: int) -> None:
    subset = ["JNJ", "PG", "XOM", "JPM"][:count]
    mapped = mapping({"SPY": [100.0] * 61, **{s: [100.0] * 61 for s in subset}})
    target = _target_weights(strategy(), mapped, START + timedelta(days=61))
    selected = sorted(subset)[:3]
    assert target == {s: 1 / len(selected) if s in selected else 0.0 for s in SYMBOLS}
    assert target and set(target) == set(SYMBOLS)
    assert sum(target.values()) <= 1 + 1e-12


def test_defensive_61_boundary_and_no_history_imputation() -> None:
    mapped = mapping({s: [100.0] * 61 for s in SYMBOLS})
    assert _target_weights(strategy(), mapped, START + timedelta(days=60)) == dict.fromkeys(
        SYMBOLS, 0.0
    )
    for s in backtest.DEFENSIVE_SUBSET:
        del mapped[s][START + timedelta(days=20)]
    assert _target_weights(strategy(), mapped, START + timedelta(days=61)) == dict.fromkeys(
        SYMBOLS, 0.0
    )


def test_defensive_intersects_universe() -> None:
    mapped = mapping({s: [100.0] * 61 for s in SYMBOLS})
    assert _target_weights(
        strategy(universe=["SPY", "XOM"]), mapped, START + timedelta(days=61)
    ) == {"SPY": 0.0, "XOM": 1.0}


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_sigma_is_excluded(bad: float, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backtest, "stdev", lambda values: bad)
    mapped = mapping({s: [100.0] * 61 for s in SYMBOLS})
    assert _target_weights(strategy(), mapped, START + timedelta(days=61)) == dict.fromkeys(
        SYMBOLS, 0.0
    )


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), 0.0])
def test_invalid_defensive_close_not_imputed(bad: float) -> None:
    mapped = mapping({s: [100.0] * 61 for s in SYMBOLS})
    for s in backtest.DEFENSIVE_SUBSET:
        mapped[s][START + timedelta(days=10)]["close"] = bad
    assert _target_weights(strategy(), mapped, START + timedelta(days=61)) == dict.fromkeys(
        SYMBOLS, 0.0
    )


@pytest.mark.parametrize("risk_on", [True, False])
def test_weighting_lookback_and_max_positions_ignored(risk_on: bool) -> None:
    mapped = mapping(
        {s: rising_gate() if risk_on and s == "SPY" else [100.0] * 253 for s in SYMBOLS}
    )
    day = START + timedelta(days=253)
    assert _target_weights(
        strategy(weighting="inverse_volatility", max_positions=1, lookback=2), mapped, day
    ) == _target_weights(strategy(), mapped, day)


@pytest.mark.parametrize("risk_on", [True, False])
def test_same_day_and_future_mutation_do_not_change_gate_or_target(risk_on: bool) -> None:
    prices = {
        s: (rising_gate() if risk_on and s == "SPY" else [100.0] * 253) + [100.0] * 10
        for s in SYMBOLS
    }
    mapped = mapping(prices)
    day = START + timedelta(days=253)
    target = _target_weights(strategy(), mapped, day)
    gate = _risk_on_regime(mapped, day)
    for history in mapped.values():
        for session, row in history.items():
            if session >= day:
                row["close"] = 100000.0
    assert _risk_on_regime(mapped, day) is gate
    assert _target_weights(strategy(), mapped, day) == target


def test_future_mutation_preserves_past_curve_trades() -> None:
    histories = {s: rows([100 + i + (3 if i % 7 == 0 else 0) for i in range(400)]) for s in SYMBOLS}
    changed = deepcopy(histories)
    cutoff = START + timedelta(days=300)
    for history in changed.values():
        for row in history:
            if date.fromisoformat(row["as_of"]) >= cutoff:
                row["close"] *= 0.01
                row["open"] *= 100
    a = run_backtest(request(400), strategy(), histories)
    b = run_backtest(request(400), strategy(), changed)
    assert [p for p in a.equity_curve if p.date < cutoff] == [
        p for p in b.equity_curve if p.date < cutoff
    ]
    assert [t for t in a.trades if t.date < cutoff] == [t for t in b.trades if t.date < cutoff]


def test_engine_risk_on_to_cash_liquidates_and_costs_are_real() -> None:
    prices = [100.0 + i for i in range(270)] + [100.0] * 30
    s = strategy(universe=["SPY"])
    result = run_backtest(request(300, start=253), s, {"SPY": rows(prices)}, _include_oos=False)
    exits = [t for t in result.trades if t.target_weight == 0.0]
    assert exits
    liquidation = next(p for p in result.equity_curve if p.date == exits[0].date)
    assert liquidation.cash == liquidation.equity
    assert liquidation.turnover == exits[0].previous_weight
    assert liquidation.transaction_cost == liquidation.turnover * 0.0015
    assert all(
        p.cash == p.equity and p.daily_return == 0 and p.turnover == 0
        for p in result.equity_curve
        if p.date > liquidation.date
    )


@pytest.mark.parametrize("weighting", ["equal", "inverse_volatility"])
def test_engine_purge_oos_and_assumptions(weighting: str) -> None:
    prices = [100 + i + (5 if i % 7 == 0 else 0) for i in range(1100)]
    result = run_backtest(
        request(1100), strategy(weighting=weighting, lookback=2), {s: rows(prices) for s in SYMBOLS}
    )
    assert result.status == "complete"
    assert result.purged_sessions == 253
    assert result.oos_start == START + timedelta(days=int(1100 * 0.7) + 253)
    assert result.oos_metrics is not None and result.oos_metrics.cagr.sample_count == 77
    for phrase in [
        "SMA100",
        "20-session",
        "252-21",
        "sigma60",
        "explicit zero",
        "interest-free USD",
        "weighting",
        "no leverage",
        "warmup/purge: 253",
    ]:
        assert phrase in result.assumptions[3]


def test_missing_requested_session_rejected_and_empty_history_insufficient() -> None:
    histories = {s: rows([100 + i for i in range(100)]) for s in SYMBOLS}
    del histories["JNJ"][50]
    with pytest.raises(ValueError, match="identical OHLCV sessions"):
        run_backtest(request(100), strategy(), histories)
    histories["JNJ"] = []
    result = run_backtest(request(100), strategy(), histories)
    assert result.status == "insufficient" and result.equity_curve == result.trades == []


def test_model_spy_required_normalized_no_new_fields() -> None:
    with pytest.raises(ValidationError, match="defensive_rotation requires SPY"):
        strategy(universe=["JNJ"])
    assert strategy(universe=[" spy ", "jnj"]).universe == ["SPY", "JNJ"]
    assert strategy(signal="equal_weight", universe=["JNJ"]).universe == ["JNJ"]
    with pytest.raises(ValidationError):
        strategy(signal="risk_on_switch")
    assert set(BacktestStrategyDefinition.model_fields) == {
        "id",
        "name",
        "description",
        "signal",
        "universe",
        "rebalance",
        "max_positions",
        "weighting",
        "execution",
        "lookback",
        "target_volatility",
    }


@pytest.fixture
def runner() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "s05_runner", Path(__file__).resolve().parents[1] / "run_backtests_defensive_rotation.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_runner_five_frozen_definitions(runner: ModuleType) -> None:
    strategies = runner.definitions()
    assert [s.id for s in strategies] == [
        "s05_defensive_rotation",
        "s05_defensive_rotation_daily",
        "s05_baseline_momentum_12_1",
        "s05_baseline_low_volatility",
        "s05_baseline_ew",
    ]
    assert all(s.universe == SYMBOLS for s in strategies)
    assert [s.rebalance for s in strategies] == [
        "monthly",
        "daily",
        "monthly",
        "monthly",
        "monthly",
    ]
    assert strategies[3].weighting == "inverse_volatility" and strategies[4].max_positions == 10


def test_diagnostics_carried_sleeve_not_hypothetical_daily_R(runner: ModuleType) -> None:
    prices = [100.0 + i for i in range(280)] + [100.0] * 40
    histories = {s: rows(prices) for s in SYMBOLS}
    result = run_backtest(
        request(320, start=253), strategy(rebalance="monthly"), histories, _include_oos=False
    )
    diagnostic = runner.diagnostic_segment(result, histories)
    path = diagnostic["daily_path"]
    decisions = diagnostic["rebalance_decisions"]
    assert any(row["daily_R"] != row["held_R"] for row in path)
    assert diagnostic["switch_count"] == sum(a["R"] != b["R"] for a, b in pairwise(decisions))
    assert diagnostic["defensive_sessions"] == sum(p["held_sleeve"] == "defensive" for p in path)
    assert (
        sum(p["sessions"] for p in diagnostic["defensive_retreat_periods"])
        == diagnostic["defensive_sessions"]
    )
    assert diagnostic["full_cash_sessions"] == 0
    assert len(decisions) == sum(row["rebalance"] for row in path)
    assert result.metrics.annual_turnover.value is not None
    assert diagnostic["annualized_cost_rate_drag"] == pytest.approx(
        result.metrics.annual_turnover.value * 0.0015
    )
    for prior, current in pairwise(path):
        if not current["rebalance"]:
            assert prior["held_target_weights"] == current["held_target_weights"]
    for point, row in zip(result.equity_curve, path, strict=True):
        assert row["actual_equity_exposure"] == 1 - point.cash / point.equity
        assert set(row["held_target_weights"]) == set(SYMBOLS)


@pytest.mark.parametrize(
    "signal, expected",
    [
        ("momentum_12_1", "fcf97389b5f1f566ffcde967cf6deac23c10f2c4d9a38af1156dd128f4a180d6"),
        ("low_volatility", "82056cbd5dcaf9d74bf6986a587d6bd43721669c56d909994c969a98139f2a34"),
        ("mean_reversion_20", "6f0bb498bdaed751a1ba295456be289ce43049bae1c224e8d9653cd62c80d137"),
        ("equal_weight", "db8715444afebff8d352c2706425ec97f1748f6d60aa23a7263e28e6091ae600"),
        ("value_fundamental", "bf0389bada6bf47b6729b9b80a55e9194940dfe798162588036d71eb8342f900"),
        ("kiyohara_value", "93820bfef5ea6d7fefa13746fe0aa5770f2fe2abcb8899998172924b174e2488"),
        (
            "crypto_trend_dual_ma",
            "a3014314a9849cf5d00b5556c8c8cbfea268c307d92709cfdad21b93916f6fad",
        ),
        (
            "vol_target_defensive",
            "586f9f138a0eb476b60197733d0a9b476b52a98cde5ee87a49bd60361d9935ee",
        ),
    ],
)
def test_existing_eight_full_responses_match_preimplementation_head(
    signal: str, expected: str
) -> None:
    # Originally measured at archived HEAD 4ade31e, re-measured at f3aed65
    # (t_a84ba708): the only change in the whole response is the $.assumptions
    # disclosure wording ("Raw OHLCV is adjusted for stock splits..."), every
    # numeric field is byte-identical, so this is an intentional wording change,
    # not a regression (t_3b044506, classification in t_a261a85a). The run passes
    # split_calendar={} explicitly so the git-ignored split-calendar.json cannot
    # alter the warnings text (or the hash) on a different checkout.
    prices = [100.0 + i + (5 if i % 7 == 0 else 0) for i in range(1100)]
    histories = {s: rows(prices) for s in SYMBOLS}
    for history in histories.values():
        for row in history:
            row.update(
                disclosure_date="2020-01-01",
                pbr=1,
                per=10,
                net_cash_ratio=0.2,
                kiyohara_net_cash_ratio=0.3,
            )
    result = run_backtest(
        request(1100),
        strategy(signal=signal, rebalance="monthly"),
        histories,
        split_calendar={},
    )
    assert hashlib.sha256(result.model_dump_json().encode()).hexdigest() == expected


def test_runner_segment_bootstrap_does_not_change_engine_metrics(runner: ModuleType) -> None:
    prices = [100.0 + i + (5 if i % 7 == 0 else 0) for i in range(1100)]
    histories = {s: rows(prices) for s in SYMBOLS}
    req = request(1100, bootstrap_samples=20)
    full = run_backtest(req, strategy(rebalance="monthly"), histories)
    assert full.oos_start is not None and full.oos_metrics is not None
    oos = runner.segment(
        req, strategy(rebalance="monthly"), histories, full.oos_start, req.end, full.oos_metrics
    )
    assert oos.metrics.bootstrap_status == "available"
    assert (
        oos.metrics.model_copy(update={"bootstrap_ci_95": None, "bootstrap_status": "disabled"})
        == full.oos_metrics
    )


def test_runner_period_evidence_is_observed_curve_not_reset_backtest(runner: ModuleType) -> None:
    prices = [100.0 + i + (5 if i % 7 == 0 else 0) for i in range(100)]
    histories = {s: rows(prices) for s in SYMBOLS}
    req = request(100, start=61)
    result = run_backtest(req, strategy(), histories, _include_oos=False)
    periods = [{"start": req.start.isoformat(), "end": req.end.isoformat(), "sessions": 39}]
    evidence = runner.period_comparison(result, result, periods)
    assert evidence[0]["comparison"]["rotation"] == evidence[0]["comparison"]["momentum"]
    observed = evidence[0]["comparison"]["rotation"]
    assert observed["net_return"] == pytest.approx(result.equity_curve[-1].equity - 1)
    assert observed["local_max_drawdown"] == pytest.approx(result.metrics.max_drawdown.value)
