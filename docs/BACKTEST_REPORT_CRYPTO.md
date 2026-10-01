# Crypto Strategy Backtest Report

Personal research backtest; not investment advice. Prices are Binance daily BTCUSDT/ETHUSDT candles, quoted in USDT. USDT is treated as the portfolio numeraire and is not converted to USD.

## Data coverage

| Asset | Rows | First | Last | Source / quote |
|---|---:|---|---|---|
| BTC/USDT | 1368 | 2023-01-01 | 2026-09-29 | Binance / USDT |
| ETH/USDT | 1368 | 2023-01-01 | 2026-09-29 | Binance / USDT |

## Results

All strategies use monthly rebalancing, next-open execution, and 15 bps estimated one-way transaction cost (10 bps commission + 5 bps slippage). Metrics are net of modeled costs.

| Strategy | Status | CAGR | Sharpe | Sortino | Max drawdown | Calmar | Hit rate | Annual turnover | OOS CAGR | OOS Sharpe | Bootstrap 95% CI (CAGR) |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| Crypto momentum (12-1) | complete | 0.1714 | 0.5930 | 0.8974 | -0.6015 | 0.2850 | 0.4196 | 0.4800 | 0.1963 | 0.6679 | [-0.1743, 0.6196] |
| Crypto inverse volatility | complete | 0.1941 | 0.6339 | 0.9605 | -0.5847 | 0.3320 | 0.4993 | 0.7110 | -0.2198 | -0.3864 | [-0.1587, 0.6863] |
| Crypto 20-session mean reversion | complete | 0.1944 | 0.6271 | 0.9504 | -0.6015 | 0.3231 | 0.5022 | 0.5051 | -0.2142 | -0.3412 | [-0.1688, 0.6873] |
| Crypto 1/N | complete | 0.2643 | 0.7563 | 1.1530 | -0.6015 | 0.4393 | 0.5168 | 0.5100 | -0.2332 | -0.3925 | [-0.1256, 0.7761] |

## Method and caveats

- The P2 backtest engine is reused: daily close-to-next-open execution, explicit transaction costs, portfolio weight drift, and the engine's purged chronological holdout/OOS calculation.
- Metrics and provenance are preserved in `crypto_backtest_results.json`; bootstrap uses 1,000 resamples and fixed seed 20260930.
- Binance data is exchange-specific BTC/USDT and ETH/USDT, not USD/reference pricing. Treating USDT as stable USD-equivalent is a simplifying assumption; no FX/depeg adjustment, funding, borrow, taxes, custody, exchange outages, or market impact beyond the fixed slippage assumption is modeled.
- Daily data has 1,368 records per asset through 2026-09-29, but only 1,367 return intervals. Crypto trades 24/7; annualized metrics use the harness annualization convention and are not directly comparable with equity results that have fewer sessions.
- Results are historical and sensitive to regime, strategy definitions, execution assumptions, and the short two-asset universe. They do not establish future performance.

## Provenance

- BTC: provider `binance`, source `https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1d`, license `personal_only`, retrieved 2026-09-30T02:48:27.373052Z, coverage 2023-01-01–2026-09-29.
- ETH: provider `binance`, source `https://api.binance.com/api/v3/klines?symbol=ETHUSDT&interval=1d`, license `personal_only`, retrieved 2026-09-30T02:48:27.636646Z, coverage 2023-01-01–2026-09-29.
- BTC: provider `binance`, source `https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1d`, license `personal_only`, retrieved 2026-09-30T02:48:27.373052Z, coverage 2023-01-01–2026-09-29.
- ETH: provider `binance`, source `https://api.binance.com/api/v3/klines?symbol=ETHUSDT&interval=1d`, license `personal_only`, retrieved 2026-09-30T02:48:27.636646Z, coverage 2023-01-01–2026-09-29.
- BTC: provider `binance`, source `https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1d`, license `personal_only`, retrieved 2026-09-30T02:48:27.373052Z, coverage 2023-01-01–2026-09-29.
- ETH: provider `binance`, source `https://api.binance.com/api/v3/klines?symbol=ETHUSDT&interval=1d`, license `personal_only`, retrieved 2026-09-30T02:48:27.636646Z, coverage 2023-01-01–2026-09-29.
- BTC: provider `binance`, source `https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1d`, license `personal_only`, retrieved 2026-09-30T02:48:27.373052Z, coverage 2023-01-01–2026-09-29.
- ETH: provider `binance`, source `https://api.binance.com/api/v3/klines?symbol=ETHUSDT&interval=1d`, license `personal_only`, retrieved 2026-09-30T02:48:27.636646Z, coverage 2023-01-01–2026-09-29.
