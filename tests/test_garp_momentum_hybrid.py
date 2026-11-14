from __future__ import annotations

import importlib.util
import math
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
from typing import Any, get_args

import pytest

from yowayowa.backtest_models import BacktestRunRequest, BacktestSignal, BacktestStrategyDefinition
from yowayowa.services.backtest import (
    _garp_momentum_candidates,
    _garp_point_in_time_pbr,
    _target_weights,
    run_backtest,
)

START = date(2021, 1, 4)
DAY = START + timedelta(days=253)
SYMBOLS = [f"S{i:02}" for i in range(10)]


def strategy(**updates: Any) -> BacktestStrategyDefinition:
    return BacktestStrategyDefinition.model_validate(
        {
            "id": "s03_test",
            "name": "GARP: momentum with PBR filter",
            "description": "Synthetic unit-test inputs only.",
            "signal": "garp_momentum_hybrid",
            "universe": SYMBOLS,
            "max_positions": 5,
            **updates,
        }
    )


def fixture(count: int = 253) -> dict[str, dict[date, dict[str, Any]]]:
    return {
        symbol: {
            START + timedelta(days=i): {
                "as_of": (START + timedelta(days=i)).isoformat(),
                "open": 100 + (9 - j) * max(i - 1, 0) / 253 + (i % 3) * 0.01,
                "close": 100 + (9 - j) * i / 253 + (i % 3) * 0.01,
                "pbr": float(10 - j),
                "disclosure_date": START.isoformat(),
                "provider": "alpaca",
                "currency": "USD",
                "source_url": "https://example.invalid/unit-fixture",
                "license_class": "personal_only",
                "retrieved_at": "2026-09-30T00:00:00Z",
            }
            for i in range(count)
        }
        for j, symbol in enumerate(SYMBOLS)
    }


def chosen(target: dict[str, float]) -> set[str]:
    return {s for s, w in target.items() if w > 0}


def no_pbr(mapped: dict[str, dict[date, dict[str, Any]]]) -> None:
    for rows in mapped.values():
        for row in rows.values():
            row["pbr"] = None


def test_two_stage_top_eight_then_lowest_five_not_global_lowest_pbr() -> None:
    mapped = fixture()
    ranks = _garp_momentum_candidates(strategy(), mapped, DAY)
    assert [s for s, _ in ranks] == SYMBOLS[:8]
    assert ranks[0][1] == mapped["S00"][START + timedelta(days=231)]["close"] / 100 - 1
    target = _target_weights(strategy(), mapped, DAY)
    assert chosen(target) == set(SYMBOLS[3:8])
    assert target == {s: 0.2 if s in SYMBOLS[3:8] else 0.0 for s in SYMBOLS}


@pytest.mark.parametrize("count", range(9))
def test_valid_pbr_count_fixed_slots_and_cash(count: int) -> None:
    mapped = fixture()
    no_pbr(mapped)
    for symbol in SYMBOLS[:count]:
        mapped[symbol][DAY - timedelta(days=1)]["pbr"] = 1.0
    target = _target_weights(strategy(), mapped, DAY)
    selected = set(SYMBOLS[: min(count, 5)]) if count else set(SYMBOLS[:5])
    assert chosen(target) == selected
    assert set(target) == set(SYMBOLS)
    assert all(target[s] == (0.2 if s in selected else 0.0) for s in SYMBOLS)
    assert sum(target.values()) <= 1 + 1e-12
    if 0 < count < 5:
        assert sum(target.values()) == pytest.approx(count / 5)


@pytest.mark.parametrize("count", [0, 1, 21, 252, 253])
def test_momentum_history_boundary(count: int) -> None:
    target = _target_weights(strategy(), fixture(count), DAY)
    assert target == (
        {s: 0.2 if s in SYMBOLS[3:8] else 0.0 for s in SYMBOLS}
        if count == 253
        else dict.fromkeys(SYMBOLS, 0.0)
    )
    assert target  # Never empty-target retention.


def test_empty_history_liquidation_and_reason() -> None:
    issues: list[tuple[str, str]] = []
    assert _target_weights(strategy(), {}, DAY, missing_fundamentals=issues) == dict.fromkeys(
        SYMBOLS, 0.0
    )
    assert any("no eligible momentum candidates" in reason for _, reason in issues)


def test_ties_use_symbol_order_with_reversed_universe_and_rows() -> None:
    mapped = fixture()
    for rows in mapped.values():
        for row in rows.values():
            row["close"], row["pbr"] = 100.0, 1.0
    mapped = {s: dict(reversed(list(rows.items()))) for s, rows in mapped.items()}
    assert [s for s, _ in _garp_momentum_candidates(strategy(), mapped, DAY)] == SYMBOLS[:8]
    assert chosen(_target_weights(strategy(universe=list(reversed(SYMBOLS))), mapped, DAY)) == set(
        SYMBOLS[:5]
    )


@pytest.mark.parametrize("offset, valid", [(-1, True), (0, False), (1, False)])
def test_disclosure_strict_boundary(offset: int, valid: bool) -> None:
    rows = fixture()["S00"]
    for row in rows.values():
        row["disclosure_date"] = (DAY + timedelta(days=offset)).isoformat()
    assert (_garp_point_in_time_pbr(rows, DAY) is not None) is valid


@pytest.mark.parametrize("bad", [None, 0, -1, math.nan, math.inf, -math.inf, "bad"])
def test_latest_disclosed_pbr_invalid_no_older_row_fill(bad: Any) -> None:
    rows = fixture()["S00"]
    rows[DAY - timedelta(days=1)]["pbr"] = bad
    assert _garp_point_in_time_pbr(rows, DAY) is None
    mapped = fixture()
    mapped["S00"] = rows
    target = _target_weights(strategy(), mapped, DAY)
    assert target["S00"] == 0.0


@pytest.mark.parametrize(
    "disclosure", [None, "bad", DAY.isoformat(), (DAY + timedelta(days=1)).isoformat()]
)
def test_latest_ineligible_disclosure_uses_older_eligible_whole_row(disclosure: Any) -> None:
    rows = fixture()["S00"]
    rows[DAY - timedelta(days=1)].update(pbr=0.001, disclosure_date=disclosure)
    assert _garp_point_in_time_pbr(rows, DAY) == 10.0


def test_latest_by_bar_day_not_disclosure_date_and_no_field_join() -> None:
    rows = fixture()["S00"]
    rows[DAY - timedelta(days=2)].update(
        pbr=1, disclosure_date=(DAY - timedelta(days=1)).isoformat()
    )
    rows[DAY - timedelta(days=1)].update(pbr=3, disclosure_date=START.isoformat(), per=None)
    assert _garp_point_in_time_pbr(dict(reversed(list(rows.items()))), DAY) == 3
    rows[DAY - timedelta(days=1)].pop("pbr")
    assert _garp_point_in_time_pbr(rows, DAY) is None


@pytest.mark.parametrize("bad", [0, -1, math.nan, math.inf, -math.inf, None, "bad"])
def test_invalid_momentum_price_is_excluded(bad: Any) -> None:
    mapped = fixture()
    mapped["S00"][START + timedelta(days=100)]["close"] = bad
    assert "S00" not in {s for s, _ in _garp_momentum_candidates(strategy(), mapped, DAY)}
    assert _target_weights(strategy(), mapped, DAY)["S00"] == 0


def test_future_bars_and_current_bar_never_affect_signal_or_pbr() -> None:
    mapped = fixture()
    expected = _target_weights(strategy(), mapped, DAY)
    for symbol in SYMBOLS:
        for offset in [0, 1, 100]:
            mapped[symbol][DAY + timedelta(days=offset)] = {
                "close": 1e20,
                "pbr": 0.001,
                "disclosure_date": START.isoformat(),
            }
    assert _target_weights(strategy(), mapped, DAY) == expected


def test_recent_skip_window_not_used_in_momentum_score() -> None:
    mapped = fixture()
    before = _garp_momentum_candidates(strategy(), mapped, DAY)
    for rows in mapped.values():
        for i in range(232, 253):
            rows[START + timedelta(days=i)]["close"] = 1e8
    assert _garp_momentum_candidates(strategy(), mapped, DAY) == before


@pytest.mark.parametrize("positions", [1, 3, 5, 8, 10])
def test_all_missing_fallback_normalized_exact_and_equal(positions: int) -> None:
    mapped = fixture()
    no_pbr(mapped)
    garp = strategy(max_positions=positions, weighting="inverse_volatility")
    baseline = strategy(signal="momentum_12_1", max_positions=positions, weighting="equal")
    issues: list[tuple[str, str]] = []
    target = _target_weights(garp, mapped, DAY, missing_fundamentals=issues)
    momentum = _target_weights(baseline, mapped, DAY)
    assert target == {s: momentum.get(s, 0.0) for s in SYMBOLS}
    assert chosen(target) == chosen(momentum)
    assert set(target) == set(SYMBOLS)
    assert (
        len([reason for _, reason in issues if reason == "missing or invalid point-in-time PBR"])
        == 8
    )
    assert any("momentum-only fallback" in reason for _, reason in issues)


@pytest.mark.parametrize(
    "weighting, positions",
    [("equal", 1), ("inverse_volatility", 1), ("equal", 10), ("inverse_volatility", 10)],
)
def test_valid_pbr_ignores_generic_weighting_and_positions(weighting: str, positions: int) -> None:
    assert _target_weights(
        strategy(weighting=weighting, max_positions=positions), fixture(), DAY
    ) == {s: 0.2 if s in SYMBOLS[3:8] else 0.0 for s in SYMBOLS}


def test_pbr_outside_first_eight_does_not_prevent_fallback() -> None:
    mapped = fixture()
    no_pbr(mapped)
    mapped["S09"][DAY - timedelta(days=1)]["pbr"] = 0.001
    assert chosen(_target_weights(strategy(), mapped, DAY)) == set(SYMBOLS[:5])


def test_literal_only_no_new_fields_or_spy_requirement() -> None:
    assert "garp_momentum_hybrid" in get_args(BacktestSignal)
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
    assert strategy(universe=["AAPL"]).universe == ["AAPL"]


def test_engine_complete_purge_and_synthetic_fallback_equivalence() -> None:
    mapped = fixture(1000)
    no_pbr(mapped)
    histories = {s: list(rows.values()) for s, rows in mapped.items()}
    request = BacktestRunRequest(
        strategy_id="s03_test", start=START, end=START + timedelta(days=999), bootstrap_samples=20
    )
    garp = run_backtest(request, strategy(), histories)
    baseline = run_backtest(request, strategy(signal="momentum_12_1"), histories)
    assert garp.status == baseline.status == "complete"
    assert garp.metrics == baseline.metrics
    assert garp.equity_curve == baseline.equity_curve
    assert garp.trades == baseline.trades
    assert garp.oos_metrics == baseline.oos_metrics
    assert garp.in_sample_metrics == baseline.in_sample_metrics
    assert garp.purged_sessions == baseline.purged_sessions == 253
    assert garp.oos_start == baseline.oos_start == START + timedelta(days=953)
    assert any("momentum-only fallback" in w for w in garp.warnings)
    assert "two-stage" in garp.assumptions[3]


@pytest.mark.parametrize("valid_count", [1, 2, 3, 4])
def test_engine_pbr_partial_cash_and_exclusion_warnings(valid_count: int) -> None:
    mapped = fixture(300)
    no_pbr(mapped)
    for symbol in SYMBOLS[:valid_count]:
        for row in mapped[symbol].values():
            row["pbr"] = 1.0
    histories = {s: list(rows.values()) for s, rows in mapped.items()}
    result = run_backtest(
        BacktestRunRequest(
            strategy_id="s03_test",
            start=DAY,
            end=START + timedelta(days=299),
            bootstrap_samples=0,
        ),
        strategy(),
        histories,
        _include_oos=False,
    )
    assert result.status == "complete"
    first = result.equity_curve[0]
    intraday_factor = (1 + first.daily_return) / (1 - first.transaction_cost)
    assert first.cash / first.equity == pytest.approx((1 - valid_count / 5) / intraday_factor)
    assert all(t.target_weight == 0.2 for t in result.trades if t.date == DAY)
    assert first.turnover == pytest.approx(valid_count / 5)
    assert first.transaction_cost == pytest.approx(valid_count / 5 * 0.0015)
    assert any("missing or invalid point-in-time PBR" in w for w in result.warnings)
    assert not any("momentum-only fallback" in w for w in result.warnings)


def test_engine_actual_liquidation_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    from yowayowa.services import backtest

    histories = {s: list(rows.values()) for s, rows in fixture(300).items()}
    original = backtest._target_weights

    def gate(definition: Any, mapped: Any, day: date, **kwargs: Any) -> dict[str, float]:
        if day >= START + timedelta(days=270):
            return original(definition, {}, day, **kwargs)
        return original(definition, mapped, day, **kwargs)

    monkeypatch.setattr(backtest, "_target_weights", gate)
    result = run_backtest(
        BacktestRunRequest(
            strategy_id="s03_test", start=DAY, end=START + timedelta(days=299), bootstrap_samples=0
        ),
        strategy(rebalance="daily"),
        histories,
        _include_oos=False,
    )
    sells = [t for t in result.trades if t.date == START + timedelta(days=270)]
    assert sells and all(t.target_weight == 0 for t in sells)
    assert sum(t.transaction_cost for t in sells) > 0
    assert all(
        p.cash == p.equity for p in result.equity_curve if p.date >= START + timedelta(days=270)
    )


def test_runner_inline_catalog_and_diagnostics_on_unit_fixtures() -> None:
    path = Path(__file__).resolve().parents[1] / "run_backtests_garp_momentum_hybrid.py"
    spec = importlib.util.spec_from_file_location("s03_runner", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    definitions = module.definitions()
    assert len(definitions) == 3
    assert all(s.rebalance == "monthly" for s in definitions)
    mapped = fixture(300)
    histories = {s: list(rows.values()) for s, rows in mapped.items()}
    result = run_backtest(
        BacktestRunRequest(
            strategy_id="s03_test", start=DAY, end=START + timedelta(days=299), bootstrap_samples=0
        ),
        strategy(),
        histories,
        _include_oos=False,
    )
    diagnostic = module.diagnostic_segment(result, histories)
    assert diagnostic["rebalance_decisions"]
    assert all(len(d["momentum_candidates"]) <= 8 for d in diagnostic["rebalance_decisions"])
    assert diagnostic["fallback_sessions"] == 0
    missing = deepcopy(histories)
    for rows in missing.values():
        for row in rows:
            row["pbr"] = None
    fallback = module.diagnostic_segment(result, missing)
    assert fallback["fallback_sessions"] == len(result.equity_curve)
