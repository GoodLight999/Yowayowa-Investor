#!/usr/bin/env python3
"""Reproduce S-05 from persisted Alpaca bars, with a shared 253-session holdout purge."""

from __future__ import annotations

import argparse
import json
import math
from datetime import date
from itertools import pairwise
from pathlib import Path
from typing import Any

from yowayowa.backtest_models import (
    BacktestMetrics,
    BacktestRunRequest,
    BacktestRunResponse,
    BacktestStrategyDefinition,
)
from yowayowa.services.backtest import (
    _rebalance_dates,
    _risk_on_regime,
    _target_weights,
    run_backtest,
)
from yowayowa.stock_acquisition import default_store

ROOT = Path(__file__).resolve().parent
UNIVERSE = ["SPY", "QQQ", "IWM", "JNJ", "PG", "XOM", "JPM", "AAPL", "MSFT", "NVDA"]
PURGE = 253
Histories = dict[str, list[dict[str, Any]]]


def definitions() -> list[BacktestStrategyDefinition]:
    return [
        BacktestStrategyDefinition.model_validate(
            {
                "id": identifier,
                "name": name,
                "description": (
                    "Frozen S-05 same-universe comparison; lagged signals and next-open execution."
                ),
                "signal": signal,
                "universe": UNIVERSE,
                "rebalance": frequency,
                "weighting": weighting,
                "max_positions": positions,
            }
        )
        for identifier, name, signal, frequency, weighting, positions in [
            (
                "s05_defensive_rotation",
                "Risk-on / defensive rotation",
                "defensive_rotation",
                "monthly",
                "equal",
                5,
            ),
            (
                "s05_defensive_rotation_daily",
                "Daily risk-on / defensive rotation",
                "defensive_rotation",
                "daily",
                "equal",
                5,
            ),
            (
                "s05_baseline_momentum_12_1",
                "Same-universe momentum",
                "momentum_12_1",
                "monthly",
                "equal",
                5,
            ),
            (
                "s05_baseline_low_volatility",
                "Same-universe low volatility",
                "low_volatility",
                "monthly",
                "inverse_volatility",
                5,
            ),
            (
                "s05_baseline_ew",
                "Same-universe equal weight",
                "equal_weight",
                "monthly",
                "equal",
                10,
            ),
        ]
    ]


def metric_values(metrics: BacktestMetrics) -> dict[str, Any]:
    return {
        key: item["value"]
        for key, item in metrics.model_dump(mode="json").items()
        if isinstance(item, dict) and "value" in item
    }


def segment(
    request: BacktestRunRequest,
    strategy: BacktestStrategyDefinition,
    histories: Histories,
    start: date,
    end: date,
    expected: BacktestMetrics | None = None,
) -> BacktestRunResponse:
    result = run_backtest(
        request.model_copy(update={"start": start, "end": end}),
        strategy,
        histories,
        _include_oos=False,
    )
    if expected is not None:
        assert (
            result.metrics.model_copy(
                update={"bootstrap_ci_95": None, "bootstrap_status": "disabled"}
            )
            == expected
        )
    return result


def intervals(path: list[dict[str, Any]], field: str, value: Any) -> list[dict[str, Any]]:
    periods: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    for row in [*path, {field: None}]:
        if row[field] == value:
            current.append(row)
        elif current:
            periods.append(
                {
                    "start": current[0]["date"],
                    "end": current[-1]["date"],
                    "sessions": len(current),
                    "net_return": math.prod(1 + point["daily_return"] for point in current) - 1,
                }
            )
            current = []
    return periods


def diagnostic_segment(result: BacktestRunResponse, histories: Histories) -> dict[str, Any]:
    """Distinguish daily hypothetical R, scheduled R, carried targets and actual exposure."""
    mapped = {
        symbol: {
            date.fromisoformat(str(row["as_of"])[:10]): row
            for row in rows
            if math.isfinite(float(row["close"]))
            and float(row["close"]) > 0
            and math.isfinite(float(row.get("open", row["close"])))
            and float(row.get("open", row["close"])) > 0
        }
        for symbol, rows in histories.items()
    }
    rebalances = _rebalance_dates([p.date for p in result.equity_curve], result.strategy.rebalance)
    path: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    held = dict.fromkeys(result.strategy.universe, 0.0)
    held_r: bool | None = None
    previous_r: bool | None = None
    previous_sleeve: str | None = None
    switches = sleeve_switches = 0
    for point in result.equity_curve:
        daily_r = _risk_on_regime(mapped, point.date)
        if point.date in rebalances:
            held = _target_weights(result.strategy, mapped, point.date)
            held_r = daily_r
            sleeve = "growth" if daily_r else "defensive"
            if sum(held.values()) == 0:
                sleeve = "cash"
            switches += int(previous_r is not None and daily_r != previous_r)
            sleeve_switches += int(previous_sleeve is not None and sleeve != previous_sleeve)
            previous_r, previous_sleeve = daily_r, sleeve
            decisions.append(
                {
                    "date": point.date.isoformat(),
                    "R": daily_r,
                    "selected": sorted(symbol for symbol, weight in held.items() if weight > 0),
                    "target_weights": dict(held),
                    "sleeve": sleeve,
                }
            )
        assert held_r is not None and previous_sleeve is not None
        path.append(
            {
                "date": point.date.isoformat(),
                "rebalance": point.date in rebalances,
                "daily_R": daily_r,
                "held_R": held_r,
                "held_sleeve": previous_sleeve,
                "held_target_weights": dict(held),
                "actual_equity_exposure": 1 - point.cash / point.equity,
                "cash_ratio": point.cash / point.equity,
                "daily_return": point.daily_return,
                "turnover": point.turnover,
                "transaction_cost": point.transaction_cost,
            }
        )
    n = len(path)
    defensive = sum(row["held_sleeve"] == "defensive" for row in path)
    cash = sum(row["held_sleeve"] == "cash" for row in path)
    cost = sum(point.transaction_cost for point in result.equity_curve)
    return {
        "window": [result.start.isoformat(), result.end.isoformat()],
        "sessions": n,
        "switch_count": switches,
        "sleeve_switch_count": sleeve_switches,
        "defensive_sessions": defensive,
        "defensive_ratio": defensive / n,
        "risk_off_held_sessions": sum(not row["held_R"] for row in path),
        "full_cash_sessions": cash,
        "partial_cash_sessions": sum(
            0 < sum(row["held_target_weights"].values()) < 1 - 1e-12 for row in path
        ),
        "daily_R_risk_on_sessions": sum(row["daily_R"] for row in path),
        "daily_R_risk_off_sessions": sum(not row["daily_R"] for row in path),
        "daily_R_switch_count": sum(a["daily_R"] != b["daily_R"] for a, b in pairwise(path)),
        "daily_R_risk_off_periods": intervals(path, "daily_R", False),
        "defensive_retreat_periods": intervals(path, "held_sleeve", "defensive"),
        "sum_transaction_cost_fraction": cost,
        "annualized_cost_rate_drag": cost / (n / 252),
        "compounded_cost_factor_drag": 1
        - math.prod(1 - p.transaction_cost for p in result.equity_curve),
        "rebalance_decisions": decisions,
        "daily_path": path,
    }


def period_comparison(
    primary: BacktestRunResponse, baseline: BacktestRunResponse, periods: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Observed interval P&L/DD, not a causal or reset/rebalanced counterfactual."""
    evidence = []
    for period in periods:
        metrics = {}
        for name, result in [("rotation", primary), ("momentum", baseline)]:
            growth = peak = 1.0
            worst = 0.0
            count = 0
            for point in result.equity_curve:
                if period["start"] <= point.date.isoformat() <= period["end"]:
                    growth *= 1 + point.daily_return
                    peak = max(peak, growth)
                    worst = min(worst, growth / peak - 1)
                    count += 1
            assert count == period["sessions"]
            metrics[name] = {"net_return": growth - 1, "local_max_drawdown": worst}
        evidence.append({**period, "comparison": metrics})
    return evidence


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--output", type=Path, default=ROOT / "backtest_results_s05.json")
    args = parser.parse_args()
    store = default_store(args.data_dir)
    histories = {symbol: store.read(symbol, provider="alpaca", limit=10_000) for symbol in UNIVERSE}
    member_dates = [
        {date.fromisoformat(str(row["as_of"])[:10]) for row in histories[s]} for s in UNIVERSE
    ]
    common = sorted(set.intersection(*member_dates))
    if len(common) < 2:
        raise ValueError("No common Alpaca session window")
    start, end = max(common[0], date(2021, 1, 4)), common[-1]
    sessions = [day for day in common if start <= day <= end]
    split = int(len(sessions) * 0.7)
    if split + PURGE >= len(sessions) - 1:
        raise ValueError("Insufficient sessions for shared 253-session-purged OOS")
    oos_start = sessions[split + PURGE]
    results = []
    diagnostics: dict[str, Any] = {}
    ab: dict[str, Any] = {}
    oos_runs: dict[str, BacktestRunResponse] = {}
    for strategy in definitions():
        request = BacktestRunRequest(
            strategy_id=strategy.id,
            start=start,
            end=end,
            commission_bps=10,
            slippage_bps=5,
            bootstrap_samples=1000,
            bootstrap_seed=20260930,
            provider="alpaca",
            annualization_days=252,
        )
        full = run_backtest(request, strategy, histories)
        assert full.status == "complete", (strategy.id, full.status)
        assert len(full.equity_curve) == len(sessions)
        shared_native = strategy.signal in {"defensive_rotation", "momentum_12_1"}
        if shared_native:
            assert full.oos_start == oos_start and full.purged_sessions == PURGE
        segments = {
            "full": full,
            "IS": segment(
                request, strategy, histories, start, sessions[split - 1], full.in_sample_metrics
            ),
            "OOS": segment(
                request,
                strategy,
                histories,
                oos_start,
                end,
                full.oos_metrics if shared_native else None,
            ),
        }
        dumped = full.model_dump(mode="json")
        oos_runs[strategy.id] = segments["OOS"]
        results.append(
            {
                "strategy_id": strategy.id,
                "name": strategy.name,
                "strategy": dumped["strategy"],
                "status": full.status,
                "requested_window": [start.isoformat(), end.isoformat()],
                "sessions": len(full.equity_curve),
                "trades": len(full.trades),
                "purged_sessions": PURGE,
                "oos_start": oos_start.isoformat(),
                "engine_native_purged_sessions": full.purged_sessions,
                "engine_native_oos_start": dumped["oos_start"],
                "metrics": metric_values(full.metrics),
                "is_metrics": metric_values(segments["IS"].metrics),
                "oos_metrics": metric_values(segments["OOS"].metrics),
                "bootstrap_ci_95": full.metrics.bootstrap_ci_95,
                "segment_metrics": {
                    name: {
                        "window": [run.start.isoformat(), run.end.isoformat()],
                        "sessions": len(run.equity_curve),
                        "status": run.status,
                        "metrics": run.metrics.model_dump(mode="json"),
                    }
                    for name, run in segments.items()
                },
                "provenance": dumped["provenance"],
                "warnings": full.warnings,
                "assumptions": full.assumptions,
            }
        )
        if strategy.signal == "defensive_rotation":
            diagnostics[strategy.id] = {
                name: diagnostic_segment(run, histories) for name, run in segments.items()
            }
            ab[strategy.id] = {}
            for name, run in segments.items():
                zero_cost = run_backtest(
                    request.model_copy(
                        update={
                            "start": run.start,
                            "end": run.end,
                            "commission_bps": 0,
                            "slippage_bps": 0,
                            "bootstrap_samples": 0,
                        }
                    ),
                    strategy,
                    histories,
                    _include_oos=False,
                )
                assert (
                    zero_cost.metrics.cagr.value is not None and run.metrics.cagr.value is not None
                )
                ab[strategy.id][name] = {
                    "net_metrics": metric_values(run.metrics),
                    "zero_cost_metrics": metric_values(zero_cost.metrics),
                    "cagr_cost_drag": zero_cost.metrics.cagr.value - run.metrics.cagr.value,
                    "sum_transaction_cost_fraction": diagnostics[strategy.id][name][
                        "sum_transaction_cost_fraction"
                    ],
                    "annualized_cost_rate_drag": diagnostics[strategy.id][name][
                        "annualized_cost_rate_drag"
                    ],
                }
    indexed = {row["strategy_id"]: row for row in results}
    assert len(results) == len(indexed) == 5
    primary = indexed["s05_defensive_rotation"]
    momentum = indexed["s05_baseline_momentum_12_1"]
    for identifier in ["s05_defensive_rotation", "s05_defensive_rotation_daily"]:
        diagnostics[identifier]["OOS"]["retreat_comparison_vs_momentum"] = period_comparison(
            oos_runs[identifier],
            oos_runs["s05_baseline_momentum_12_1"],
            diagnostics[identifier]["OOS"]["defensive_retreat_periods"],
        )
    cagr = primary["oos_metrics"]["cagr"]
    criteria = {
        "a_oos_cagr_verdict": "positive_pass"
        if cagr >= 0
        else "near_zero_pass"
        if cagr >= -0.02
        else "not_met",
        "a_delta_oos_cagr_vs_same_universe_momentum": cagr - momentum["oos_metrics"]["cagr"],
        "b_full_drawdown_shallower_than_same_universe_momentum": primary["metrics"]["max_drawdown"]
        > momentum["metrics"]["max_drawdown"],
        "b_full_drawdown_shallower_than_cio_reference": primary["metrics"]["max_drawdown"]
        > -0.3464,
        "c_oos_sharpe_gt_0_5": primary["oos_metrics"]["sharpe_ratio"] > 0.5,
        "d_full_bootstrap_lower_ge_minus_0_15": primary["bootstrap_ci_95"][0] >= -0.15,
    }
    output = {
        "coverage": {
            symbol: {
                "row_count": len(histories[symbol]),
                "first": min(days).isoformat(),
                "last": max(days).isoformat(),
                "provider": "alpaca",
                "currency": "USD",
            }
            for symbol, days in zip(UNIVERSE, member_dates, strict=True)
        },
        "primary_strategy_id": "s05_defensive_rotation",
        "assumptions": {
            "universe": UNIVERSE,
            "provider": "alpaca",
            "annualization_days": 252,
            "commission_bps": 10,
            "slippage_bps": 5,
            "bootstrap_samples": 1000,
            "bootstrap_seed": 20260930,
            "cash": "interest-free USD",
            "leverage": False,
            "split": (
                "70% IS; shared 253-session purge; all five OOS segments independently "
                "initialized in cash"
            ),
            "baseline_native_splits": (
                "Engine remains unchanged: low-vol native purge 61, EW 0; runner uses "
                "matched 253 for reported OOS"
            ),
            "switch_count": (
                "Scheduled R changes, excluding initial cash-to-sleeve entry; "
                "sleeve changes separately reported"
            ),
            "diagnostics": (
                "Daily R is hypothetical between rebalances; held target sleeve is carried. "
                "Session return includes old-sleeve overnight P&L on switch day. "
                "Cost-rate sums are equity fractions, not dollars; CAGR drag is measured "
                "by a zero-cost rerun."
            ),
            "growth_shortfall": (
                "Each selected growth asset has fixed 20%; fewer than five leaves residual cash"
            ),
            "daily_variant": (
                "Diagnostic full daily re-selection/rebalancing, not a one-way emergency "
                "exit and not primary adoption candidate"
            ),
            "cio_reference": {
                "momentum_oos_cagr": -0.0660,
                "momentum_full_max_drawdown": -0.3464,
                "different_universe": True,
            },
        },
        "evaluation_window": [start.isoformat(), end.isoformat()],
        "sessions": len(sessions),
        "split": {
            "is_sessions": split,
            "purged_sessions": PURGE,
            "oos_start": oos_start.isoformat(),
            "oos_sessions": len(sessions) - split - PURGE,
        },
        "results": results,
        "diagnostics": diagnostics,
        "monthly_vs_daily": ab,
        "primary_criteria": criteria,
    }
    args.output.write_text(
        json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "saved": str(args.output),
                "split": output["split"],
                "results": [
                    {
                        k: row[k]
                        for k in ["strategy_id", "metrics", "oos_metrics", "bootstrap_ci_95"]
                    }
                    for row in results
                ],
                "criteria": criteria,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
