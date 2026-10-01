from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from yowayowa.backtest_models import BacktestRunRequest, BacktestStrategyDefinition
from yowayowa.crypto_acquisition import default_store as default_crypto_store
from yowayowa.services.backtest import run_backtest

ROOT = Path(__file__).resolve().parent
symbols = ["BTC", "ETH"]
store = default_crypto_store(ROOT / "data")
histories = {symbol: store.read(symbol, provider="binance", limit=10_000) for symbol in symbols}
strategies = [
    BacktestStrategyDefinition(id="crypto_momentum_12_1", name="Crypto momentum (12-1)", description="Monthly trend-following across BTC and ETH using 12-1 momentum.", signal="momentum_12_1", universe=symbols, rebalance="monthly", max_positions=2, weighting="equal"),
    BacktestStrategyDefinition(id="crypto_low_volatility", name="Crypto inverse volatility", description="Monthly inverse-volatility weighting across BTC and ETH.", signal="low_volatility", universe=symbols, rebalance="monthly", max_positions=2, weighting="inverse_volatility"),
    BacktestStrategyDefinition(id="crypto_mean_reversion_20", name="Crypto 20-session mean reversion", description="Monthly rebalance toward assets with negative recent 20-session return.", signal="mean_reversion_20", universe=symbols, rebalance="monthly", max_positions=2, weighting="equal"),
    BacktestStrategyDefinition(id="crypto_equal_weight", name="Crypto 1/N", description="Monthly equal-weight BTC/ETH baseline.", signal="equal_weight", universe=symbols, rebalance="monthly", max_positions=2, weighting="equal"),
]
coverage = {}
for symbol, rows in histories.items():
    days = sorted(date.fromisoformat(str(row["as_of"])[:10]) for row in rows)
    coverage[symbol] = {"rows": len(rows), "start": days[0].isoformat() if days else None, "end": days[-1].isoformat() if days else None, "provider": "binance", "currency": sorted({str(row.get("currency")) for row in rows})}
results = []
for strategy in strategies:
    all_days = sorted({date.fromisoformat(str(row["as_of"])[:10]) for row in histories[strategy.universe[0]]})
    request = BacktestRunRequest(strategy_id=strategy.id, start=date(2023, 1, 1), end=all_days[-1], commission_bps=10, slippage_bps=5, bootstrap_samples=1000, bootstrap_seed=20260930, provider="binance")
    result = run_backtest(request, strategy, histories)
    results.append(result.model_dump(mode="json"))
output = {"data_coverage": coverage, "assumptions": {"provider": "binance", "quote_currency": "USDT", "commission_bps_per_one_way_turnover": 10, "slippage_bps_per_one_way_turnover": 5, "bootstrap_samples": 1000, "bootstrap_seed": 20260930, "execution": "next_open", "universe": symbols}, "results": results}
(ROOT / "crypto_backtest_results.json").write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
lines = ["# Crypto Strategy Backtest Report", "", "Personal research backtest; not investment advice. Prices are Binance daily BTCUSDT/ETHUSDT candles, quoted in USDT. USDT is treated as the portfolio numeraire and is not converted to USD.", "", "## Data coverage", "", "| Asset | Rows | First | Last | Source / quote |", "|---|---:|---|---|---|"]
for symbol, data in coverage.items():
    lines.append(f"| {symbol}/USDT | {data['rows']} | {data['start']} | {data['end']} | Binance / USDT |")
lines += ["", "## Results", "", "All strategies use monthly rebalancing, next-open execution, and 15 bps estimated one-way transaction cost (10 bps commission + 5 bps slippage). Metrics are net of modeled costs.", "", "| Strategy | Status | CAGR | Sharpe | Sortino | Max drawdown | Calmar | Hit rate | Annual turnover | OOS CAGR | OOS Sharpe | Bootstrap 95% CI (CAGR) |", "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
def val(metric: dict | None) -> str:
    if not metric or metric.get("value") is None:
        return "N/A"
    return f"{metric['value']:.4f}"
for r in results:
    m, o = r["metrics"], r.get("oos_metrics")
    name = r["strategy"]["name"]
    ci = m.get("bootstrap_ci_95")
    ci_text = f"[{ci[0]:.4f}, {ci[1]:.4f}]" if ci else "N/A"
    lines.append("| " + " | ".join([name, r["status"], val(m["cagr"]), val(m["sharpe_ratio"]), val(m["sortino_ratio"]), val(m["max_drawdown"]), val(m["calmar_ratio"]), val(m["hit_rate"]), val(m["annual_turnover"]), val(o["cagr"] if o else None), val(o["sharpe_ratio"] if o else None), ci_text]) + " |")
lines += ["", "## Method and caveats", "", "- The P2 backtest engine is reused: daily close-to-next-open execution, explicit transaction costs, portfolio weight drift, and the engine's purged chronological holdout/OOS calculation.", "- Metrics and provenance are preserved in `crypto_backtest_results.json`; bootstrap uses 1,000 resamples and fixed seed 20260930.", "- Binance data is exchange-specific BTC/USDT and ETH/USDT, not USD/reference pricing. Treating USDT as stable USD-equivalent is a simplifying assumption; no FX/depeg adjustment, funding, borrow, taxes, custody, exchange outages, or market impact beyond the fixed slippage assumption is modeled.", "- Daily data has 1,368 records per asset through 2026-09-29, but only 1,367 return intervals. Crypto trades 24/7; annualized metrics use the harness annualization convention and are not directly comparable with equity results that have fewer sessions.", "- Results are historical and sensitive to regime, strategy definitions, execution assumptions, and the short two-asset universe. They do not establish future performance.", "", "## Provenance", ""]
for r in results:
    for p in r["provenance"]:
        lines.append(f"- {p['symbol']}: provider `{p['provider']}`, source `{p['source_url']}`, license `{p['license_class']}`, retrieved {p['retrieved_at']}, coverage {p['as_of_start']}–{p['as_of_end']}.")
(ROOT / "docs" / "BACKTEST_REPORT_CRYPTO.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
print(json.dumps({"coverage": coverage, "results": [{"strategy": r["strategy"]["id"], "status": r["status"], "sessions": len(r["equity_curve"]), "trades": len(r["trades"])} for r in results]}, indent=2))
