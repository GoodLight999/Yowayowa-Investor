from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from yowayowa.backtest_models import BacktestRunRequest, BacktestStrategyDefinition
from yowayowa.crypto_acquisition import default_store as default_crypto_store
from yowayowa.services.backtest import run_backtest

ROOT = Path(__file__).resolve().parent
symbols = ["BTC", "ETH"]
parser = argparse.ArgumentParser(
    description="Regenerate crypto backtests from persisted Binance bars."
)
parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
args = parser.parse_args()
store = default_crypto_store(args.data_dir)
histories = {symbol: store.read(symbol, provider="binance", limit=10_000) for symbol in symbols}
strategies = [
    BacktestStrategyDefinition(
        id="crypto_momentum_12_1",
        name="Crypto momentum (12-1)",
        description="Monthly 12-1 momentum ranking; both BTC and ETH held equally after warmup (two assets, two positions; no positive-momentum filter).",
        signal="momentum_12_1",
        universe=symbols,
        rebalance="monthly",
        max_positions=2,
        weighting="equal",
    ),
    BacktestStrategyDefinition(
        id="crypto_low_volatility",
        name="Crypto inverse volatility",
        description="Monthly inverse-volatility weighting across BTC and ETH.",
        signal="low_volatility",
        universe=symbols,
        rebalance="monthly",
        max_positions=2,
        weighting="inverse_volatility",
    ),
    BacktestStrategyDefinition(
        id="crypto_mean_reversion_20",
        name="Crypto 20-session mean reversion",
        description="Monthly negative 20-session return ranking; both BTC and ETH held equally after warmup (two assets, two positions; no negative-return filter).",
        signal="mean_reversion_20",
        universe=symbols,
        rebalance="monthly",
        max_positions=2,
        weighting="equal",
    ),
    BacktestStrategyDefinition(
        id="crypto_equal_weight",
        name="Crypto 1/N",
        description="Monthly equal-weight BTC/ETH baseline.",
        signal="equal_weight",
        universe=symbols,
        rebalance="monthly",
        max_positions=2,
        weighting="equal",
    ),
]
coverage = {}
for symbol, rows in histories.items():
    days = sorted(date.fromisoformat(str(row["as_of"])[:10]) for row in rows)
    coverage[symbol] = {
        "rows": len(rows),
        "start": days[0].isoformat() if days else None,
        "end": days[-1].isoformat() if days else None,
        "provider": "binance",
        "currency": sorted({str(row.get("currency")) for row in rows}),
    }
results = []
for strategy in strategies:
    all_days = sorted(
        {date.fromisoformat(str(row["as_of"])[:10]) for row in histories[strategy.universe[0]]}
    )
    request = BacktestRunRequest(
        strategy_id=strategy.id,
        start=date(2023, 1, 1),
        end=all_days[-1],
        commission_bps=10,
        slippage_bps=5,
        bootstrap_samples=1000,
        bootstrap_seed=20260930,
        provider="binance",
        annualization_days=365,
    )
    result = run_backtest(request, strategy, histories)
    results.append(result.model_dump(mode="json"))
output = {
    "data_coverage": coverage,
    "assumptions": {
        "provider": "binance",
        "quote_currency": "USDT",
        "annualization_days": 365,
        "commission_bps_per_one_way_turnover": 10,
        "slippage_bps_per_one_way_turnover": 5,
        "bootstrap_samples": 1000,
        "bootstrap_seed": 20260930,
        "execution": "next_open",
        "universe": symbols,
        "max_positions": 2,
        "low_volatility_prior_closes_required": 61,
    },
    "results": results,
}
(ROOT / "crypto_backtest_results.json").write_text(
    json.dumps(output, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
)
lines = [
    "# Crypto Strategy Backtest Report",
    "",
    "Personal research backtest; not investment advice. Prices are Binance daily BTCUSDT/ETHUSDT candles, quoted in USDT. USDT is treated as the portfolio numeraire and is not converted to USD.",
    "",
    "## Data coverage",
    "",
    "| Asset | Rows | First | Last | Source / quote |",
    "|---|---:|---|---|---|",
]
for symbol, data in coverage.items():
    lines.append(
        f"| {symbol}/USDT | {data['rows']} | {data['start']} | {data['end']} | Binance / USDT |"
    )
lines += [
    "",
    "## Results",
    "",
    "All strategies use monthly rebalancing, next-open execution, and 15 bps estimated one-way transaction cost (10 bps commission + 5 bps slippage). Metrics are net of modeled costs. Annualization uses `annualization_days=365` for daily 24/7 crypto: CAGR, Sharpe, Sortino, gross turnover, bootstrap CAGR confidence intervals, and derived Calmar (including IS/OOS).",
    "",
    "| Strategy | Status | CAGR | Sharpe | Sortino | Max drawdown | Calmar | Hit rate | Annual turnover | OOS CAGR | OOS Sharpe | Bootstrap 95% CI (CAGR) |",
    "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
]


def val(metric: dict | None) -> str:
    if not metric or metric.get("value") is None:
        return "N/A"
    return f"{metric['value']:.4f}"


for r in results:
    m, o = r["metrics"], r.get("oos_metrics")
    name = r["strategy"]["name"]
    ci = m.get("bootstrap_ci_95")
    ci_text = f"[{ci[0]:.4f}, {ci[1]:.4f}]" if ci else "N/A"
    lines.append(
        "| "
        + " | ".join(
            [
                name,
                r["status"],
                val(m["cagr"]),
                val(m["sharpe_ratio"]),
                val(m["sortino_ratio"]),
                val(m["max_drawdown"]),
                val(m["calmar_ratio"]),
                val(m["hit_rate"]),
                val(m["annual_turnover"]),
                val(o["cagr"] if o else None),
                val(o["sharpe_ratio"] if o else None),
                ci_text,
            ]
        )
        + " |"
    )
lines += [
    "",
    "## Warmup and selection",
    "",
    "| Strategy | First trade | Minimum prior closes at first trade |",
    "|---|---|---:|",
]
for r in results:
    first_trade = r["trades"][0]["date"] if r["trades"] else None
    prior = (
        min(
            sum(str(row["as_of"])[:10] < first_trade for row in rows) for rows in histories.values()
        )
        if first_trade
        else None
    )
    if r["strategy"]["signal"] == "low_volatility":
        assert prior is not None and prior >= 61, "Incomplete 60-return volatility warmup"
    lines.append(
        f"| {r['strategy']['name']} | {first_trade or 'None'} | {prior if prior is not None else 'N/A'} |"
    )
lines += [
    "",
    "- Low volatility requires 60 prior close returns (61 closes), for both selection and inverse-volatility weighting. No trade occurs before warmup; once ready, execution waits for the next scheduled monthly rebalance.",
    "- Momentum uses 253 prior closes and a 22-session skip; mean reversion uses 21 prior closes. These are session lookbacks, not calendar-month definitions.",
    "- `crypto_momentum_12_1` and `crypto_mean_reversion_20` rank assets but do not filter by return sign. With universe=2, max_positions=2 and equal weighting, both BTC and ETH are held at 50% each at every rebalance after warmup, irrespective of ranking. They are delayed equal-weight baselines, not selective trend-following or negative-return-only portfolios; rankings do not change their fully warmed holdings.",
    "",
    "## Method and caveats",
    "",
    "- The P2 backtest engine is reused: prior-session signals, next-open execution, explicit transaction costs, portfolio weight drift, and purged chronological holdout/OOS calculation.",
    "- Metrics and provenance are preserved in `crypto_backtest_results.json`; bootstrap uses 1,000 IID daily-return resamples and fixed seed 20260930. Confidence intervals are descriptive, not forecast intervals.",
    "- Binance data is exchange-specific BTC/USDT and ETH/USDT, not USD/reference pricing. USDT is the numeraire, not converted to USD; no FX/depeg adjustment, funding, borrow, taxes, custody, exchange outages, or market impact beyond fixed slippage is modeled.",
    "- Return sample count equals requested-window equity observations, including cash/warmup days and the initial day with zero overnight return when no prior bar exists. This is an observation-count annualization, not an elapsed-calendar-time CAGR; results are not directly comparable with 252-session equity metrics.",
    "- Results are historical and sensitive to regime, strategy definitions, execution assumptions, and the short two-asset universe. They do not establish future performance.",
    "",
    "## Provenance",
    "",
]
for r in results:
    for p in r["provenance"]:
        lines.append(
            f"- {p['symbol']}: provider `{p['provider']}`, source `{p['source_url']}`, license `{p['license_class']}`, retrieved {p['retrieved_at']}, coverage {p['as_of_start']}–{p['as_of_end']}."
        )
(ROOT / "docs" / "BACKTEST_REPORT_CRYPTO.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
print(
    json.dumps(
        {
            "coverage": coverage,
            "results": [
                {
                    "strategy": r["strategy"]["id"],
                    "status": r["status"],
                    "sessions": len(r["equity_curve"]),
                    "trades": len(r["trades"]),
                }
                for r in results
            ],
        },
        indent=2,
    )
)
