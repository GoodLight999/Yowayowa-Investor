#!/usr/bin/env python3
"""Reproduce S-02 from persisted Alpaca bars; never fetch or modify market data."""

from __future__ import annotations

import argparse
import json
import math
from datetime import date
from pathlib import Path
from statistics import fmean, stdev
from typing import Any

from yowayowa.backtest_models import (
    VOLATILITY_TARGET_SYMBOL,
    BacktestMetrics,
    BacktestRunRequest,
    BacktestRunResponse,
    BacktestStrategyDefinition,
    RebalanceFrequency,
)
from yowayowa.services.backtest import _rebalance_dates, _returns, _target_weights, run_backtest
from yowayowa.stock_acquisition import default_store

ROOT = Path(__file__).resolve().parent
SYMBOLS = ["SPY", "IWM", "JNJ", "PG"]
Histories = dict[str, list[dict[str, Any]]]


def definitions() -> list[BacktestStrategyDefinition]:
    variants: list[tuple[float, str, RebalanceFrequency]] = [
        (0.10, "", "weekly"),
        (0.15, "", "weekly"),
        (0.20, "", "weekly"),
        (0.15, "_daily", "daily"),
    ]
    strategies = [
        BacktestStrategyDefinition(
            id=f"s02_vol_target_{int(target * 100)}{suffix}",
            name=f"Volatility target {target:.0%} {frequency}",
            description=(
                "Lagged SPY sample volatility; equal equity sleeve and interest-free USD cash."
            ),
            signal="vol_target_defensive",
            universe=SYMBOLS,
            rebalance=frequency,
            max_positions=4,
            weighting="equal",
            lookback=21,
            target_volatility=target,
        )
        for target, suffix, frequency in variants
    ]
    baseline_frequencies: list[RebalanceFrequency] = ["weekly", "monthly"]
    strategies.extend(
        BacktestStrategyDefinition(
            id=f"s02_baseline_ew_{frequency}",
            name=f"Equal weight {frequency}",
            description="Same four-asset fully invested equal-weight baseline.",
            signal="equal_weight",
            universe=SYMBOLS,
            rebalance=frequency,
            max_positions=4,
            weighting="equal",
        )
        for frequency in baseline_frequencies
    )
    return strategies


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
    *,
    bootstrap: bool = True,
) -> BacktestRunResponse:
    result = run_backtest(
        request.model_copy(
            update={"start": start, "end": end, "bootstrap_samples": 1000 if bootstrap else 0}
        ),
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


def diagnostic_segment(result: BacktestRunResponse, histories: Histories) -> dict[str, Any]:
    """Keep scheduled target w distinct from price-drifted end-of-session exposure."""
    strategy = result.strategy
    mapped = {
        symbol: {date.fromisoformat(str(row["as_of"])[:10]): row for row in rows}
        for symbol, rows in histories.items()
    }
    dates = [point.date for point in result.equity_curve]
    rebalances = _rebalance_dates(dates, strategy.rebalance)
    lookback = strategy.lookback or 21
    target_vol = strategy.target_volatility
    path: list[dict[str, Any]] = []
    previous = held = 0.0
    changes = 0
    for point in result.equity_curve:
        suggested = sum(_target_weights(strategy, mapped, point.date).values())
        if point.date in rebalances:
            changes += int(suggested != previous)
            previous = held = suggested
        closes = [
            float(mapped[VOLATILITY_TARGET_SYMBOL][day]["close"])
            for day in sorted(mapped[VOLATILITY_TARGET_SYMBOL])
            if day < point.date
        ]
        sigma = stdev(_returns(closes[-(lookback + 1) :])) if len(closes) >= lookback + 1 else None
        path.append(
            {
                "date": point.date.isoformat(),
                "rebalance": point.date in rebalances,
                "daily_signal_w": suggested,
                "held_target_w": held,
                "actual_w": 1 - point.cash / point.equity,
                "cash_ratio": point.cash / point.equity,
                "spy_sigma_daily": sigma,
                "spy_sigma_annualized": sigma * math.sqrt(252) if sigma is not None else None,
                "daily_return": point.daily_return,
            }
        )
    volatility = stdev(point.daily_return for point in result.equity_curve) * math.sqrt(252)
    years = len(dates) / 252
    sum_cost = sum(point.transaction_cost for point in result.equity_curve)
    high = [
        row
        for row in path
        if target_vol is not None
        and row["spy_sigma_annualized"] is not None
        and row["spy_sigma_annualized"] > 2 * target_vol
    ]
    return {
        "window": [result.start.isoformat(), result.end.isoformat()],
        "sessions": len(dates),
        "realized_annual_volatility": volatility,
        "target_annual_volatility": target_vol,
        "targeting_error": volatility - target_vol if target_vol is not None else None,
        "relative_targeting_error": volatility / target_vol - 1 if target_vol is not None else None,
        "average_held_target_w": fmean(row["held_target_w"] for row in path),
        "minimum_held_target_w": min(row["held_target_w"] for row in path),
        "average_actual_w": fmean(row["actual_w"] for row in path),
        "minimum_actual_w": min(row["actual_w"] for row in path),
        "changed_w_rebalances": changes,
        "average_cash_ratio": fmean(row["cash_ratio"] for row in path),
        "maximum_cash_ratio": max(row["cash_ratio"] for row in path),
        "high_sigma_sessions": len(high),
        "high_sigma_evidence": high,
        "annual_turnover": result.metrics.annual_turnover.value,
        "sum_transaction_cost_fraction": sum_cost,
        "annualized_cost_drag": sum_cost / years,
        "compounded_cost_factor_drag": 1
        - math.prod(1 - point.transaction_cost for point in result.equity_curve),
        "exposure_path": path,
    }


def summarize(
    result: BacktestRunResponse, segments: dict[str, BacktestRunResponse]
) -> dict[str, Any]:
    dumped = result.model_dump(mode="json")
    metrics = dumped["metrics"]
    return {
        "strategy_id": result.strategy.id,
        "name": result.strategy.name,
        "strategy": dumped["strategy"],
        "requested_window": [dumped["start"], dumped["end"]],
        "status": result.status,
        "sessions": len(result.equity_curve),
        "trades": len(result.trades),
        "provenance": dumped["provenance"],
        "purged_sessions": result.purged_sessions,
        "oos_start": dumped["oos_start"],
        "metrics": metric_values(result.metrics),
        "metric_status": {k: v["status"] for k, v in metrics.items() if isinstance(v, dict)},
        "metric_sample_counts": {
            k: v["sample_count"] for k, v in metrics.items() if isinstance(v, dict)
        },
        "bootstrap_status": result.metrics.bootstrap_status,
        "bootstrap_ci_95": result.metrics.bootstrap_ci_95,
        "is_cagr": segments["IS"].metrics.cagr.value,
        "is_sharpe": segments["IS"].metrics.sharpe_ratio.value,
        "is_sessions": len(segments["IS"].equity_curve),
        "oos_sessions": len(segments["OOS"].equity_curve),
        "is_metrics": metric_values(segments["IS"].metrics),
        "oos_metrics": metric_values(segments["OOS"].metrics),
        "segment_metrics": {
            name: {
                "window": [run.start.isoformat(), run.end.isoformat()],
                "sessions": len(run.equity_curve),
                "metrics": run.metrics.model_dump(mode="json"),
            }
            for name, run in segments.items()
        },
        "warnings": result.warnings,
        "assumptions": result.assumptions,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--output", type=Path, default=ROOT / "backtest_results_s02.json")
    args = parser.parse_args()
    store = default_store(args.data_dir)
    histories = {symbol: store.read(symbol, provider="alpaca", limit=10_000) for symbol in SYMBOLS}
    member_dates = [
        {date.fromisoformat(str(row["as_of"])[:10]) for row in histories[symbol]}
        for symbol in SYMBOLS
    ]
    common = sorted(set.intersection(*member_dates))
    if len(common) < 2:
        raise ValueError("No common Alpaca session window")
    start, end = max(common[0], date(2021, 1, 4)), common[-1]
    results = []
    sensitivity = []
    diagnostics: dict[str, Any] = {}
    runs: dict[str, dict[str, BacktestRunResponse]] = {}
    strategies = definitions()
    for strategy in strategies:
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
        assert full.in_sample_metrics is not None and full.oos_metrics is not None
        assert full.oos_start is not None
        split = int(len(full.equity_curve) * 0.7)
        segments = {
            "full": full,
            "IS": segment(
                request,
                strategy,
                histories,
                start,
                full.equity_curve[split - 1].date,
                full.in_sample_metrics,
            ),
            "OOS": segment(request, strategy, histories, full.oos_start, end, full.oos_metrics),
        }
        if full.purged_sessions:
            segments["purge"] = segment(
                request,
                strategy,
                histories,
                full.equity_curve[split].date,
                full.equity_curve[split + full.purged_sessions - 1].date,
                bootstrap=False,
            )
        runs[strategy.id] = segments
        results.append(summarize(full, segments))
        diagnostics[strategy.id] = {
            name: diagnostic_segment(run, histories)
            for name, run in segments.items()
            if name != "purge"
        }
        if strategy.id in {"s02_vol_target_10", "s02_vol_target_15", "s02_vol_target_20"}:
            sensitivity.append(
                {
                    "strategy_id": strategy.id,
                    "target_volatility": strategy.target_volatility,
                    "segments": {
                        name: run.metrics.model_dump(mode="json")
                        for name, run in segments.items()
                        if name != "purge"
                    },
                }
            )
    # Same-start comparison is supplementary; primary criterion uses frozen baseline OOS.
    primary = runs["s02_vol_target_15"]["OOS"]
    baseline = strategies[4]
    matched_request = BacktestRunRequest(
        strategy_id=baseline.id,
        start=primary.start,
        end=end,
        bootstrap_samples=0,
    )
    matched = run_backtest(matched_request, baseline, histories, _include_oos=False)
    diagnostics["matched_oos_weekly_baseline"] = {
        "window": [primary.start.isoformat(), end.isoformat()],
        "metrics": matched.metrics.model_dump(mode="json"),
    }
    # Baseline worst drawdown peak/trough plus actual S-02 exposure throughout that decline.
    peak = 1.0
    peak_date = primary.start
    worst = 0.0
    worst_peak = worst_trough = primary.start
    for point in matched.equity_curve:
        if point.equity > peak:
            peak, peak_date = point.equity, point.date
        dd = point.equity / peak - 1
        if dd < worst:
            worst, worst_peak, worst_trough = dd, peak_date, point.date
    path = diagnostics["s02_vol_target_15"]["OOS"]["exposure_path"]
    diagnostics["oos_downturn_evidence"] = {
        "definition": (
            "Worst same-start weekly equal-weight OOS peak-to-trough drawdown; "
            "primary actual exposure, not hypothetical daily targets."
        ),
        "peak_date": worst_peak.isoformat(),
        "trough_date": worst_trough.isoformat(),
        "baseline_drawdown": worst,
        "exposure": [
            row for row in path if worst_peak.isoformat() <= row["date"] <= worst_trough.isoformat()
        ],
    }
    rows = {row["strategy_id"]: row for row in results}
    primary_m = rows["s02_vol_target_15"]
    criteria = {
        "a_oos_sharpe_gt_0_5": primary_m["oos_metrics"]["sharpe_ratio"] > 0.5,
        "b_oos_drawdown_shallower_than_weekly_baseline": primary_m["oos_metrics"]["max_drawdown"]
        > rows["s02_baseline_ew_weekly"]["oos_metrics"]["max_drawdown"],
        "c_full_bootstrap_lower_ge_minus_0_15": primary_m["bootstrap_ci_95"][0] >= -0.15,
        "full_drawdown_shallower_than_external_minus_0_5021": primary_m["metrics"]["max_drawdown"]
        > -0.5021,
    }
    assert len(results) == 6 and len({row["strategy_id"] for row in results}) == 6
    assert len(sensitivity) == 3
    coverage = {
        symbol: {
            "row_count": len(histories[symbol]),
            "first": min(days).isoformat(),
            "last": max(days).isoformat(),
            "provider": "alpaca",
            "currency": "USD",
        }
        for symbol, days in zip(SYMBOLS, member_dates, strict=True)
    }
    output = {
        "coverage": coverage,
        "primary_strategy_id": "s02_vol_target_15",
        "assumptions": {
            "universe": SYMBOLS,
            "provider": "alpaca",
            "annualization_days": 252,
            "commission_bps": 10,
            "slippage_bps": 5,
            "bootstrap_samples": 1000,
            "bootstrap_seed": 20260930,
            "cash": "interest-free USD",
            "leverage": False,
            "split": (
                "70% IS; purge lookback+1; independently initialized OOS; purge is diagnostic only"
            ),
            "diagnostics": (
                "Targeting error = realized net portfolio volatility minus annual SPY target. "
                "Target w is carried between scheduled rebalances; actual w includes price drift. "
                "Initial cash-to-target counts as a change. Cost sums are equity-fraction rates "
                "(not dollar fees), annualized by observations/252; compounded cost factor is "
                "multiplicative rate drag, not a counterfactual return difference."
            ),
            "daily_variant": "Cost diagnostic only, not eligible for adoption criteria",
        },
        "results": results,
        "sensitivity": sensitivity,
        "diagnostics": diagnostics,
        "primary_criteria": criteria,
    }
    args.output.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "saved": str(args.output),
                "results": [
                    {
                        "id": row["strategy_id"],
                        "status": row["status"],
                        "sessions": row["sessions"],
                        "metrics": row["metrics"],
                        "oos_metrics": row["oos_metrics"],
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
