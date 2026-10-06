#!/usr/bin/env python3
"""Reproduce S-01 lookback sensitivity from persisted, unmodified Binance bars."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any

from yowayowa.backtest_models import (
    BacktestMetrics,
    BacktestRunRequest,
    BacktestRunResponse,
    BacktestStrategyDefinition,
)
from yowayowa.crypto_acquisition import default_store
from yowayowa.services.backtest import _target_weights, run_backtest

ROOT = Path(__file__).resolve().parent
LOOKBACKS = (100, 150, 200, 300)
SYMBOLS = ["BTC", "ETH"]


def cash_shift_statistics(
    result: BacktestRunResponse, histories: dict[str, list[dict[str, Any]]]
) -> dict[str, Any]:
    """Count gate states and transitions, not drift-induced rebalance trades."""
    rows_by_symbol = {
        symbol: {
            date.fromisoformat(str(row["as_of"])[:10]): row
            for row in histories[symbol]
            if row.get("provider") == "binance"
            and date.fromisoformat(str(row["as_of"])[:10]) <= result.end
        }
        for symbol in result.strategy.universe
    }
    cash = dict.fromkeys(result.strategy.universe, 0)
    entries = dict.fromkeys(result.strategy.universe, 0)
    exits = dict.fromkeys(result.strategy.universe, 0)
    previous = dict.fromkeys(result.strategy.universe, False)
    cash_streaks = dict.fromkeys(result.strategy.universe, 0)
    longest_cash = dict.fromkeys(result.strategy.universe, 0)
    portfolio_streak = longest_portfolio_cash = 0
    active_counts = {0: 0, 1: 0, 2: 0}
    for point in result.equity_curve:
        target = _target_weights(result.strategy, rows_by_symbol, point.date)
        active_counts[sum(weight > 0 for weight in target.values())] += 1
        for symbol in result.strategy.universe:
            active = target[symbol] > 0
            cash[symbol] += int(not active)
            entries[symbol] += int(active and not previous[symbol])
            exits[symbol] += int(not active and previous[symbol])
            previous[symbol] = active
            cash_streaks[symbol] = 0 if active else cash_streaks[symbol] + 1
            longest_cash[symbol] = max(longest_cash[symbol], cash_streaks[symbol])
        portfolio_streak = portfolio_streak + 1 if not any(target.values()) else 0
        longest_portfolio_cash = max(longest_portfolio_cash, portfolio_streak)
        if not any(target.values()):
            assert abs(point.cash - point.equity) < 1e-10
    return {
        "sessions": len(result.equity_curve),
        "cash_sessions_by_asset": cash,
        "cash_asset_sessions_sum": sum(cash.values()),
        "all_cash_portfolio_sessions": active_counts[0],
        "longest_cash_streak_by_asset": longest_cash,
        "longest_all_cash_portfolio_streak": longest_portfolio_cash,
        "any_asset_inactive_sessions": active_counts[0] + active_counts[1],
        "active_asset_count_sessions": active_counts,
        "cash_to_hold_by_asset": entries,
        "hold_to_cash_by_asset": exits,
        "switches_by_asset": {symbol: entries[symbol] + exits[symbol] for symbol in cash},
        "definition": (
            "Zero target means the asset is inactive, not that a fixed 50% cash sleeve exists. "
            "One active asset receives 100%, so only zero active assets is portfolio cash. "
            "Counts include warmup. Initial state is cash; first investment counts as entry. "
            "Price-drift-only rebalances are not gate switches."
        ),
    }


def segment_metrics(
    request: BacktestRunRequest,
    strategy: BacktestStrategyDefinition,
    histories: dict[str, list[dict[str, Any]]],
    start: date,
    end: date,
    expected: BacktestMetrics,
) -> BacktestMetrics:
    segment = run_backtest(
        request.model_copy(update={"start": start, "end": end}),
        strategy,
        histories,
        _include_oos=False,
    )
    # Only the additive bootstrap fields may differ from engine holdout metrics.
    assert (
        segment.metrics.model_copy(update={"bootstrap_ci_95": None, "bootstrap_status": "disabled"})
        == expected
    )
    return segment.metrics


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    args = parser.parse_args()
    store = default_store(args.data_dir)
    histories = {symbol: store.read(symbol, provider="binance", limit=10_000) for symbol in SYMBOLS}
    end = max(date.fromisoformat(str(row["as_of"])[:10]) for row in histories["BTC"])
    results = []
    for lookback in LOOKBACKS:
        strategy = BacktestStrategyDefinition(
            id="crypto_trend_dual_ma",
            name="Crypto dual-MA trend filter",
            description="Daily absolute trend gate; active BTC/ETH equal weighted, else cash.",
            signal="crypto_trend_dual_ma",
            universe=SYMBOLS,
            rebalance="daily",
            max_positions=2,
            weighting="equal",
            lookback=lookback,
        )
        request = BacktestRunRequest(
            strategy_id=strategy.id,
            start=date(2023, 1, 1),
            end=end,
            commission_bps=10,
            slippage_bps=5,
            bootstrap_samples=1000,
            bootstrap_seed=20260930,
            provider="binance",
            annualization_days=365,
        )
        result = run_backtest(request, strategy, histories)
        assert result.in_sample_metrics is not None
        split_index = int(len(result.equity_curve) * 0.7)
        is_end = result.equity_curve[split_index - 1].date
        is_metrics = segment_metrics(
            request, strategy, histories, request.start, is_end, result.in_sample_metrics
        )
        oos_metrics = (
            segment_metrics(request, strategy, histories, result.oos_start, end, result.oos_metrics)
            if result.oos_start is not None and result.oos_metrics is not None
            else None
        )
        results.append(
            {
                "lookback": lookback,
                "strategy": strategy.model_dump(mode="json"),
                "start": result.start.isoformat(),
                "end": result.end.isoformat(),
                "status": result.status,
                "sessions": len(result.equity_curve),
                "trades": len(result.trades),
                "full_metrics": result.metrics.model_dump(mode="json"),
                "in_sample_metrics": is_metrics.model_dump(mode="json"),
                "oos_metrics": oos_metrics.model_dump(mode="json") if oos_metrics else None,
                "oos_start": result.oos_start.isoformat() if result.oos_start else None,
                "purged_sessions": result.purged_sessions,
                "cash_shift_statistics": cash_shift_statistics(result, histories),
                "provenance": [p.model_dump(mode="json") for p in result.provenance],
                "warnings": result.warnings,
            }
        )
    assert [row["lookback"] for row in results] == list(LOOKBACKS)
    output = {
        "assumptions": {
            "provider": "binance",
            "quote_currency": "USDT",
            "universe": SYMBOLS,
            "annualization_days": 365,
            "commission_bps_per_one_way_turnover": 10,
            "slippage_bps_per_one_way_turnover": 5,
            "execution": "next_open",
            "rebalance": "daily",
            "signal": "close(t-1) > SMA(lookback)(t-1); active 1/N; no active means cash",
            "lookbacks": LOOKBACKS,
            "bootstrap_samples": 1000,
            "bootstrap_seed": 20260930,
            "bootstrap": "IID daily-return percentile 95% CAGR CI for full, independent IS/OOS",
            "split": "70% IS, purge effective lookback, independently initialized OOS",
            "comparison_limit": "OOS start dates vary with lookback; no post-hoc best selection",
        },
        "results": results,
    }
    path = ROOT / "crypto_trend_sensitivity.json"
    path.write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(path),
                "results": [
                    {
                        "lookback": row["lookback"],
                        "status": row["status"],
                        "sessions": row["sessions"],
                        "trades": row["trades"],
                        "oos_start": row["oos_start"],
                        "purge": row["purged_sessions"],
                    }
                    for row in results
                ],
            },
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
