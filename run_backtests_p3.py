from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from yowayowa.backtest_models import BacktestRunRequest
from yowayowa.services.backtest import run_backtest
from yowayowa.services.backtest_definitions import get_strategy, list_strategies
from yowayowa.stock_acquisition import default_store

store = default_store()
strategies = list_strategies()
all_symbols = sorted({symbol for strategy in strategies for symbol in strategy.universe})
histories = {symbol: store.read(symbol, provider="alpaca", limit=10_000) for symbol in all_symbols}
coverage = {}
for symbol, rows in histories.items():
    dates = sorted(date.fromisoformat(str(row["as_of"])[:10]) for row in rows)
    coverage[symbol] = {
        "row_count": len(rows),
        "first": dates[0].isoformat() if dates else None,
        "last": dates[-1].isoformat() if dates else None,
        "providers": sorted({str(row.get("provider")) for row in rows}),
        "currencies": sorted({str(row.get("currency")) for row in rows}),
    }

results = []
for strategy in strategies:
    member_dates = [
        {date.fromisoformat(str(row["as_of"])[:10]) for row in histories[symbol]}
        for symbol in strategy.universe
    ]
    common = sorted(set.intersection(*member_dates)) if member_dates else []
    if len(common) < 2:
        results.append({"strategy_id": strategy.id, "error": "no common session window"})
        continue
    evaluation_start = max(common[0], date(2021, 1, 4))
    request = BacktestRunRequest(
        strategy_id=strategy.id,
        start=evaluation_start,
        end=common[-1],
        commission_bps=10,
        slippage_bps=5,
        bootstrap_samples=1000,
        bootstrap_seed=20260930,
        provider="alpaca",
    )
    result = run_backtest(request, get_strategy(strategy.id), histories)
    results.append(result.model_dump(mode="json"))

summary = []
for result in results:
    if "error" in result:
        summary.append(result)
        continue
    metrics = result["metrics"]
    is_metrics = result["in_sample_metrics"]
    oos = result["oos_metrics"]
    summary.append(
        {
            "strategy_id": result["strategy"]["id"],
            "name": result["strategy"]["name"],
            "requested_window": [result["start"], result["end"]],
            "status": result["status"],
            "sessions": len(result["equity_curve"]),
            "trades": len(result["trades"]),
            "provenance": result["provenance"],
            "purged_sessions": result["purged_sessions"],
            "oos_start": result["oos_start"],
            "metrics": {
                name: item["value"]
                for name, item in metrics.items()
                if name != "bootstrap_ci_95" and isinstance(item, dict) and "value" in item
            },
            "metric_status": {
                name: item["status"]
                for name, item in metrics.items()
                if isinstance(item, dict) and "status" in item
            },
            "metric_sample_counts": {
                name: item["sample_count"]
                for name, item in metrics.items()
                if isinstance(item, dict) and "sample_count" in item
            },
            "bootstrap_status": metrics["bootstrap_status"],
            "bootstrap_ci_95": metrics["bootstrap_ci_95"],
            "is_cagr": is_metrics["cagr"]["value"] if is_metrics else None,
            "is_sharpe": is_metrics["sharpe_ratio"]["value"] if is_metrics else None,
            "is_sessions": is_metrics["cagr"]["sample_count"] if is_metrics else None,
            "oos_sessions": oos["cagr"]["sample_count"] if oos else None,
            "oos_metrics": {
                name: item["value"]
                for name, item in (oos or {}).items()
                if name != "bootstrap_ci_95" and isinstance(item, dict) and "value" in item
            },
            "warnings": result["warnings"],
            "assumptions": result["assumptions"],
        }
    )
output = {"coverage": coverage, "results": summary}
result_path = Path(__file__).with_name("backtest_results_p3.json")
result_path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(
    f"saved={result_path} strategies={len(summary)} statuses="
    + ",".join(item.get("status", "error") for item in summary)
)
