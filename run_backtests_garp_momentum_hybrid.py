#!/usr/bin/env python3
"""Reproduce S-03 using only persisted Alpaca bars and supplied point-in-time PBR."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

from yowayowa.backtest_models import (
    BacktestMetrics,
    BacktestRunRequest,
    BacktestRunResponse,
    BacktestStrategyDefinition,
)
from yowayowa.services.backtest import (
    _garp_momentum_candidates,
    _garp_point_in_time_pbr,
    _rebalance_dates,
    _target_weights,
    run_backtest,
)
from yowayowa.stock_acquisition import default_store

ROOT = Path(__file__).resolve().parent
UNIVERSE = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "JPM", "XOM", "SPY"]
PURGE = 253
Histories = dict[str, list[dict[str, Any]]]


def definitions() -> list[BacktestStrategyDefinition]:
    return [
        BacktestStrategyDefinition.model_validate(
            {
                "id": identifier,
                "name": name,
                "description": "S-03 matched persisted-input comparison.",
                "signal": signal,
                "universe": UNIVERSE,
                "rebalance": "monthly",
                "max_positions": positions,
                "weighting": "equal",
            }
        )
        for identifier, name, signal, positions in [
            (
                "s03_garp_momentum_hybrid",
                "GARP: momentum with PBR filter",
                "garp_momentum_hybrid",
                5,
            ),
            ("s03_baseline_momentum_12_1", "Same-universe momentum", "momentum_12_1", 5),
            ("s03_reference_ew", "Same-universe equal weight reference", "equal_weight", 10),
        ]
    ]


def metric_values(metrics: BacktestMetrics) -> dict[str, Any]:
    return {
        key: item["value"]
        for key, item in metrics.model_dump(mode="json").items()
        if isinstance(item, dict) and "value" in item
    }


def map_histories(histories: Histories, end: date) -> dict[str, dict[date, dict[str, Any]]]:
    # Match engine retention, including provider, positive finite open/close and
    # end boundary; do not fabricate financial fields or alter the input store.
    return {
        symbol: {
            date.fromisoformat(str(row["as_of"])[:10]): row
            for row in rows
            if row.get("provider", "alpaca") == "alpaca"
            and date.fromisoformat(str(row["as_of"])[:10]) <= end
            and math.isfinite(float(row["close"]))
            and float(row["close"]) > 0
            and math.isfinite(float(row["open"] if "open" in row else row["close"]))
            and float(row["open"] if "open" in row else row["close"]) > 0
        }
        for symbol, rows in histories.items()
    }


def diagnostic_segment(result: BacktestRunResponse, histories: Histories) -> dict[str, Any]:
    mapped = map_histories(histories, result.end)
    rebalances = _rebalance_dates([p.date for p in result.equity_curve], result.strategy.rebalance)
    decisions = []
    held = dict.fromkeys(result.strategy.universe, 0.0)
    fallback = False
    previous: tuple[str, ...] | None = None
    previous_fallback: bool | None = None
    switches = fallback_switches = fallback_days = cash_days = 0
    selected_sessions: Counter[str] = Counter()
    valid_rebalances: Counter[str] = Counter()
    candidate_valid_rebalances: Counter[str] = Counter()
    exclusions: dict[str, Counter[str]] = {s: Counter() for s in result.strategy.universe}
    for point in result.equity_curve:
        if point.date in rebalances:
            issues: list[tuple[str, str]] = []
            candidates = _garp_momentum_candidates(
                result.strategy, mapped, point.date, missing_fundamentals=issues
            )
            candidate_symbols = {s for s, _ in candidates}
            valid = {
                s: pbr
                for s, _ in candidates
                if (pbr := _garp_point_in_time_pbr(mapped[s], point.date)) is not None
            }
            for symbol in result.strategy.universe:
                if _garp_point_in_time_pbr(mapped.get(symbol, {}), point.date) is not None:
                    valid_rebalances[symbol] += 1
                if symbol in valid:
                    candidate_valid_rebalances[symbol] += 1
                elif symbol in candidate_symbols:
                    exclusions[symbol]["missing or invalid point-in-time PBR"] += 1
                elif symbol not in {s for s, _ in issues}:
                    exclusions[symbol]["outside top-eight momentum candidates"] += 1
            for symbol, reason in issues:
                exclusions[symbol][reason] += 1
            held = _target_weights(result.strategy, mapped, point.date)
            fallback = bool(candidates) and not valid
            selected = tuple(sorted(s for s, w in held.items() if w > 0))
            switches += int(previous is not None and selected != previous)
            fallback_switches += int(
                previous_fallback is not None and fallback != previous_fallback
            )
            previous, previous_fallback = selected, fallback
            decisions.append(
                {
                    "date": point.date.isoformat(),
                    "momentum_candidates": [
                        {"symbol": s, "momentum": value} for s, value in candidates
                    ],
                    "valid_pbr_candidates": [
                        {"symbol": s, "pbr": value}
                        for s, value in sorted(valid.items(), key=lambda x: (x[1], x[0]))
                    ],
                    "selected": list(selected),
                    "target_weights": dict(held),
                    "fallback": fallback,
                    "no_momentum_candidates": not candidates,
                }
            )
        fallback_days += int(fallback)
        cash_days += int(sum(held.values()) == 0)
        selected_sessions.update(s for s, weight in held.items() if weight > 0)
    return {
        "window": [result.start.isoformat(), result.end.isoformat()],
        "sessions": len(result.equity_curve),
        "rebalance_count": len(decisions),
        "fallback_rebalances": sum(d["fallback"] for d in decisions),
        "fallback_sessions": fallback_days,
        "no_momentum_rebalances": sum(d["no_momentum_candidates"] for d in decisions),
        "full_cash_sessions": cash_days,
        "selection_switch_count": switches,
        "fallback_switch_count": fallback_switches,
        "selected_symbol_sessions": {s: selected_sessions[s] for s in result.strategy.universe},
        "coverage": {
            s: {
                "valid_pbr_rebalances": valid_rebalances[s],
                "valid_candidate_pbr_rebalances": candidate_valid_rebalances[s],
                "exclusion_reasons": dict(sorted(exclusions[s].items())),
            }
            for s in result.strategy.universe
        },
        "rebalance_decisions": decisions,
        "interpretation": (
            "Between rebalances, counts carry scheduled targets, not hypothetical daily "
            "decisions or drifted exposure; switches exclude initial entry."
        ),
    }


def field_digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--output", type=Path, default=ROOT / "backtest_results_s03.json")
    parser.add_argument("--p3-reference", type=Path, default=ROOT / "backtest_results_p3.json")
    args = parser.parse_args()
    store = default_store(args.data_dir)
    histories = {s: store.read(s, provider="alpaca", limit=10_000) for s in UNIVERSE}
    member_dates = [
        {date.fromisoformat(str(r["as_of"])[:10]) for r in histories[s]} for s in UNIVERSE
    ]
    common = sorted(set.intersection(*member_dates))
    if len(common) < 2:
        raise ValueError("No common persisted Alpaca window")
    start, end = max(common[0], date(2021, 1, 4)), common[-1]
    sessions = [d for d in common if start <= d <= end]
    split = int(len(sessions) * 0.7)
    if split + PURGE >= len(sessions) - 1:
        raise ValueError("Insufficient sessions for shared 253-session purged OOS")
    oos_start = sessions[split + PURGE]
    results = []
    runs: dict[str, dict[str, BacktestRunResponse]] = {}
    diagnostics = {}
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
        assert full.status == "complete" and len(full.equity_curve) == len(sessions)
        segments = {"full": full}
        for label, a, b in [("IS", start, sessions[split - 1]), ("OOS", oos_start, end)]:
            run = run_backtest(
                request.model_copy(update={"start": a, "end": b}),
                strategy,
                histories,
                _include_oos=False,
            )
            assert run.status == "complete"
            if label == "IS" or strategy.signal != "equal_weight":
                expected = full.in_sample_metrics if label == "IS" else full.oos_metrics
                assert (
                    run.metrics.model_copy(
                        update={"bootstrap_ci_95": None, "bootstrap_status": "disabled"}
                    )
                    == expected
                )
            segments[label] = run
        if strategy.signal != "equal_weight":
            assert full.oos_start == oos_start and full.purged_sessions == PURGE
        runs[strategy.id] = segments
        dumped = full.model_dump(mode="json")
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
                "bootstrap_ci_95": dumped["metrics"]["bootstrap_ci_95"],
                "segment_metrics": {
                    label: {
                        "window": [run.start.isoformat(), run.end.isoformat()],
                        "sessions": len(run.equity_curve),
                        "status": run.status,
                        "metrics": run.metrics.model_dump(mode="json"),
                    }
                    for label, run in segments.items()
                },
                "provenance": dumped["provenance"],
                "warnings": full.warnings,
                "assumptions": full.assumptions,
            }
        )
        if strategy.signal == "garp_momentum_hybrid":
            diagnostics = {
                label: diagnostic_segment(run, histories) for label, run in segments.items()
            }
    indexed = {r["strategy_id"]: r for r in results}
    assert len(indexed) == len(results) == 3
    primary = indexed["s03_garp_momentum_hybrid"]
    baseline = indexed["s03_baseline_momentum_12_1"]
    ab = {}
    no_usable_pbr = not any(
        d["valid_pbr_candidates"]
        for segment in diagnostics.values()
        for d in segment["rebalance_decisions"]
    )
    for label in ["full", "IS", "OOS"]:
        g = runs[primary["strategy_id"]][label].model_dump(mode="json")
        m = runs[baseline["strategy_id"]][label].model_dump(mode="json")
        equal = {field: g[field] == m[field] for field in ["metrics", "equity_curve", "trades"]}
        if no_usable_pbr:
            assert all(equal.values()), (label, equal)
        gm, mm = (
            metric_values(runs[primary["strategy_id"]][label].metrics),
            metric_values(runs[baseline["strategy_id"]][label].metrics),
        )
        ab[label] = {
            "delta_metrics": {
                k: gm[k] - mm[k] if gm[k] is not None and mm[k] is not None else None for k in gm
            },
            "delta_bootstrap_ci_95": [
                a - b
                for a, b in zip(
                    g["metrics"]["bootstrap_ci_95"], m["metrics"]["bootstrap_ci_95"], strict=True
                )
            ],
            "exact_field_equality": equal,
            "field_sha256": {
                f: {"garp": field_digest(g[f]), "baseline": field_digest(m[f])} for f in equal
            },
        }
    p3 = json.loads(args.p3_reference.read_text())
    p3_momentum = next(r for r in p3["results"] if r["strategy_id"] == "momentum_12_1")
    checks = {
        "metrics": baseline["metrics"] == p3_momentum["metrics"],
        "metric_status": {
            k: item["status"]
            for k, item in baseline["segment_metrics"]["full"]["metrics"].items()
            if isinstance(item, dict) and "status" in item
        }
        == p3_momentum["metric_status"],
        "metric_sample_counts": {
            k: item["sample_count"]
            for k, item in baseline["segment_metrics"]["full"]["metrics"].items()
            if isinstance(item, dict) and "sample_count" in item
        }
        == p3_momentum["metric_sample_counts"],
        "bootstrap_status": baseline["segment_metrics"]["full"]["metrics"]["bootstrap_status"]
        == p3_momentum["bootstrap_status"],
        "oos_metrics": baseline["oos_metrics"] == p3_momentum["oos_metrics"],
        "bootstrap_ci_95": baseline["bootstrap_ci_95"] == p3_momentum["bootstrap_ci_95"],
        "is_cagr": baseline["is_metrics"]["cagr"] == p3_momentum["is_cagr"],
        "is_sharpe": baseline["is_metrics"]["sharpe_ratio"] == p3_momentum["is_sharpe"],
        "requested_window": baseline["requested_window"] == p3_momentum["requested_window"],
        "oos_start": baseline["oos_start"] == p3_momentum["oos_start"],
        "sessions": baseline["sessions"] == p3_momentum["sessions"],
        "trades": baseline["trades"] == p3_momentum["trades"],
        "is_sessions": baseline["segment_metrics"]["IS"]["sessions"] == p3_momentum["is_sessions"],
        "oos_sessions": baseline["segment_metrics"]["OOS"]["sessions"]
        == p3_momentum["oos_sessions"],
        "purged_sessions": baseline["purged_sessions"] == p3_momentum["purged_sessions"],
    }
    assert all(checks.values()), (
        "P3 reference is stale or settings differ; regenerate P3 without altering legacy signals",
        checks,
    )
    output = {
        "primary_strategy_id": primary["strategy_id"],
        "evaluation_window": [start.isoformat(), end.isoformat()],
        "sessions": len(sessions),
        "split": {
            "is_sessions": split,
            "purged_sessions": PURGE,
            "oos_start": oos_start.isoformat(),
            "oos_sessions": len(sessions) - split - PURGE,
        },
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
            "synthetic_run_data": False,
            "split": (
                "70% IS; shared 253-session purge; independent cash initialization; "
                "EW native purge stays 0, reported OOS is matched 253"
            ),
            "coverage": (
                "Counts use supplied store rows; validity uses latest eligible engine-retained "
                "bar, no finance join, imputation or forward-fill"
            ),
        },
        "coverage": {
            s: {
                "row_count": len(histories[s]),
                "first": min(days).isoformat(),
                "last": max(days).isoformat(),
                "pbr_nonnull_count": sum(r.get("pbr") is not None for r in histories[s]),
                "disclosure_date_nonnull_count": sum(
                    r.get("disclosure_date") is not None for r in histories[s]
                ),
                **diagnostics["full"]["coverage"][s],
            }
            for s, days in zip(UNIVERSE, member_dates, strict=True)
        },
        "results": results,
        "diagnostics": diagnostics,
        "ab_comparison": ab,
        "p3_baseline_equality": checks,
        "cio_reference_comparison": {
            "interpretation": (
                "External, different-universe reference; not an acceptance criterion."
            ),
            "oos_cagr": {
                "reference": -0.0660,
                "measured_baseline": baseline["oos_metrics"]["cagr"],
                "delta": baseline["oos_metrics"]["cagr"] + 0.0660,
            },
            "full_max_drawdown": {
                "reference": -0.3464,
                "measured_baseline": baseline["metrics"]["max_drawdown"],
                "delta": baseline["metrics"]["max_drawdown"] + 0.3464,
            },
        },
        "primary_criteria": {
            "a_oos_sharpe_improvement": "not_evaluable"
            if no_usable_pbr
            else "pass"
            if primary["oos_metrics"]["sharpe_ratio"] > baseline["oos_metrics"]["sharpe_ratio"]
            else "not_met",
            "b_is_oos_cagr_gap_reduction": "not_evaluable"
            if no_usable_pbr
            else "pass"
            if abs(primary["is_metrics"]["cagr"] - primary["oos_metrics"]["cagr"])
            < abs(baseline["is_metrics"]["cagr"] - baseline["oos_metrics"]["cagr"])
            else "not_met",
            "c_full_max_drawdown_reduction": "not_evaluable"
            if no_usable_pbr
            else "pass"
            if primary["metrics"]["max_drawdown"] > baseline["metrics"]["max_drawdown"]
            else "not_met",
            "d_full_bootstrap_lower_ge_minus_0_15": "not_evaluable"
            if no_usable_pbr
            else "pass"
            if primary["bootstrap_ci_95"][0] >= -0.15
            else "not_met",
            "reason": (
                "No usable candidate point-in-time PBR: momentum-only self-comparison, "
                "not evidence of GARP efficacy."
            )
            if no_usable_pbr
            else (
                "Measured supplied-input GARP vs matched momentum; inspect coverage and OOS length."
            ),
        },
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
                    {k: r[k] for k in ["strategy_id", "metrics", "oos_metrics", "bootstrap_ci_95"]}
                    for r in results
                ],
                "ab": ab,
                "criteria": output["primary_criteria"],
            },
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
    )


if __name__ == "__main__":
    main()
