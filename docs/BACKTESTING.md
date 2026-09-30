# Backtesting

## Strategy definitions

The backtest catalog is stored in `src/yowayowa/backtest_strategies.json` and validated as `BacktestStrategyDefinition`. Current built-ins are 12-1 momentum, low-volatility selection with inverse-volatility weighting, 20-session mean reversion, and an equal-weight baseline. Definition fields expose the signal, symbol universe, rebalance frequency, maximum holdings, weighting rule, and execution timing. The catalog is intentionally separate from the research `strategy_presets` catalog: a research checklist is not itself a price-history trading signal. No fundamental value/quality or Kiyohara preset is included because this price-bar-only engine has no point-in-time fundamental rankings and must not imply such a simulation was measured.

## API and CLI

- `GET /v1/backtest/strategies`
- `POST /v1/backtest/run`
- `yowayowa backtest run --strategy <id> --start YYYY-MM-DD --end YYYY-MM-DD`

Runs use persisted daily stock OHLCV from the Alpaca provider; other sources are not accepted by this API. Rows are never sourced from live fetching during a run; missing histories fail closed or are reported insufficient. Costs default to 10 bps commission and 5 bps slippage on gross traded notional; both are request parameters. Bootstrap sample count and seed are explicit fields.

## Method and limits

Signal values use only sessions strictly before execution. Rebalances are executed at the session open; overnight returns apply old weights and intraday returns apply new weights. Available pre-start bars are used only for signal lookbacks; return observations stay within the requested interval. Gross buys plus sells determine turnover and modeled costs. For purged walk-forward reporting, the first 70% is the training/history segment and the following maximum signal-lookback number of sessions is purged; the remaining tail is reported separately as OOS. OOS signals may use only pre-split training history and prior OOS bars; the purge covers each signal's maximum lookback.

Metrics include CAGR, annualized Sharpe and Sortino, maximum drawdown, Calmar, positive-session hit rate, and annualized gross turnover. Full-window, in-sample, and purged OOS metrics are returned separately; each metric has a sample count/status. Unavailable or too-small samples are null, never zero-filled, and mathematically undefined ratios are identified as `undefined`. The seeded percentile bootstrap resamples daily returns IID; it is descriptive and not a forecast interval.

This baseline uses raw OHLCV and a fixed, non-point-in-time symbol universe. It does not model dividends, corporate-action adjustments, taxes, market impact, borrow costs, delisting returns, or point-in-time membership. Historical prices for securities no longer listed and point-in-time fundamental histories remain future prerequisites for a less biased value/quality/Kiyohara universe backtest. Do not interpret a result as a forecast or executable recommendation.
