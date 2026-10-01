# Crypto Strategy Backtest Report

Personal research backtest; not investment advice. Prices are Binance daily BTCUSDT/ETHUSDT candles, quoted in USDT. USDT is treated as the portfolio numeraire and is not converted to USD.

## Data coverage

| Asset | Rows | First | Last | Source / quote |
|---|---:|---|---|---|
| BTC/USDT | 1368 | 2023-01-01 | 2026-09-29 | Binance / USDT |
| ETH/USDT | 1368 | 2023-01-01 | 2026-09-29 | Binance / USDT |

## Results

All strategies use monthly rebalancing, next-open execution, and 15 bps estimated one-way transaction cost (10 bps commission + 5 bps slippage). Metrics are net of modeled costs. Annualization uses `annualization_days=365` for daily 24/7 crypto: CAGR, Sharpe, Sortino, gross turnover, bootstrap CAGR confidence intervals, and derived Calmar (including IS/OOS).

| Strategy | Status | CAGR | Sharpe | Sortino | Max drawdown | Calmar | Hit rate | Annual turnover | OOS CAGR | OOS Sharpe | Bootstrap 95% CI (CAGR) |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| Crypto momentum (12-1) | complete | 0.2576 | 0.7137 | 1.0801 | -0.6015 | 0.4282 | 0.4196 | 0.6952 | 0.2965 | 0.8038 | [-0.2422, 1.0105] |
| Crypto inverse volatility | complete | 0.2340 | 0.6751 | 1.0145 | -0.5847 | 0.4003 | 0.4810 | 0.9904 | -0.3020 | -0.4651 | [-0.2559, 0.9926] |
| Crypto 20-session mean reversion | complete | 0.2934 | 0.7547 | 1.1438 | -0.6015 | 0.4877 | 0.5022 | 0.7316 | -0.2947 | -0.4106 | [-0.2349, 1.1333] |
| Crypto 1/N | complete | 0.4044 | 0.9102 | 1.3877 | -0.6015 | 0.6723 | 0.5168 | 0.7387 | -0.3192 | -0.4724 | [-0.1766, 1.2980] |

## Warmup and selection

| Strategy | First trade | Minimum prior closes at first trade |
|---|---|---:|
| Crypto momentum (12-1) | 2023-10-01 | 273 |
| Crypto inverse volatility | 2023-04-01 | 90 |
| Crypto 20-session mean reversion | 2023-02-01 | 31 |
| Crypto 1/N | 2023-01-01 | 0 |

- Low volatility requires 60 prior close returns (61 closes), for both selection and inverse-volatility weighting. No trade occurs before warmup; once ready, execution waits for the next scheduled monthly rebalance.
- Momentum uses 253 prior closes and a 22-session skip; mean reversion uses 21 prior closes. These are session lookbacks, not calendar-month definitions.
- `crypto_momentum_12_1` and `crypto_mean_reversion_20` rank assets but do not filter by return sign. With universe=2, max_positions=2 and equal weighting, both BTC and ETH are held at 50% each at every rebalance after warmup, irrespective of ranking. They are delayed equal-weight baselines, not selective trend-following or negative-return-only portfolios; rankings do not change their fully warmed holdings.

## Method and caveats

- The P2 backtest engine is reused: prior-session signals, next-open execution, explicit transaction costs, portfolio weight drift, and purged chronological holdout/OOS calculation.
- Metrics and provenance are preserved in `crypto_backtest_results.json`; bootstrap uses 1,000 IID daily-return resamples and fixed seed 20260930. Confidence intervals are descriptive, not forecast intervals.
- Binance data is exchange-specific BTC/USDT and ETH/USDT, not USD/reference pricing. USDT is the numeraire, not converted to USD; no FX/depeg adjustment, funding, borrow, taxes, custody, exchange outages, or market impact beyond fixed slippage is modeled.
- Return sample count equals requested-window equity observations, including cash/warmup days and the initial day with zero overnight return when no prior bar exists. This is an observation-count annualization, not an elapsed-calendar-time CAGR; results are not directly comparable with 252-session equity metrics.
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
