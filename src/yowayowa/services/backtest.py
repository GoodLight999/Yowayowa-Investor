from __future__ import annotations

import math
import random
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from itertools import pairwise
from statistics import fmean, stdev
from typing import Any, Literal

from yowayowa.backtest_models import (
    BacktestEquityPoint,
    BacktestMetric,
    BacktestMetrics,
    BacktestProvenance,
    BacktestRunRequest,
    BacktestRunResponse,
    BacktestStrategyDefinition,
    BacktestTrade,
)
from yowayowa.domain import LicenseClass
from yowayowa.services.backtest_definitions import get_strategy, list_strategies

MIN_RETURN_SAMPLES = 20
TRADING_DAYS_PER_YEAR = 252
__all__ = ["get_strategy", "list_strategies", "run_backtest"]


def _metric(value: float | None, count: int, *, undefined: bool = False) -> BacktestMetric:
    if undefined and count >= MIN_RETURN_SAMPLES:
        return BacktestMetric(value=None, sample_count=count, status="undefined")
    if value is not None and not math.isfinite(value):
        return BacktestMetric(value=None, sample_count=count, status="undefined")
    if count < MIN_RETURN_SAMPLES or value is None:
        return BacktestMetric(value=None, sample_count=count, status="insufficient")
    return BacktestMetric(value=value, sample_count=count, status="available")


def _metrics(
    returns: list[float], turnover: float, ci: tuple[float, float] | None, bootstrap_enabled: bool
) -> BacktestMetrics:
    count = len(returns)
    years = count / TRADING_DAYS_PER_YEAR
    growth = math.prod(1 + value for value in returns)
    cagr = growth ** (1 / years) - 1 if years and growth > 0 else None
    average = fmean(returns) if returns else None
    volatility = stdev(returns) if count > 1 else None
    sharpe = (
        average / volatility * math.sqrt(TRADING_DAYS_PER_YEAR)
        if average is not None and volatility and volatility > 0
        else None
    )
    downside = math.sqrt(fmean(min(value, 0.0) ** 2 for value in returns)) if returns else None
    sortino = (
        average / downside * math.sqrt(TRADING_DAYS_PER_YEAR)
        if average is not None and downside and downside > 0
        else None
    )
    peak = equity = 1.0
    max_drawdown = 0.0
    for value in returns:
        equity *= 1 + value
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity / peak - 1)
    calmar = cagr / abs(max_drawdown) if cagr is not None and max_drawdown < 0 else None
    hit_rate = sum(value > 0 for value in returns) / count if count else None
    annual_turnover = turnover / years if years else None
    return BacktestMetrics(
        cagr=_metric(cagr, count),
        sharpe_ratio=_metric(sharpe, count, undefined=volatility == 0),
        sortino_ratio=_metric(sortino, count, undefined=downside == 0),
        max_drawdown=_metric(max_drawdown, count),
        calmar_ratio=_metric(calmar, count, undefined=max_drawdown == 0),
        hit_rate=_metric(hit_rate, count),
        annual_turnover=_metric(annual_turnover, count),
        bootstrap_ci_95=ci,
        bootstrap_status="disabled"
        if not bootstrap_enabled
        else ("available" if ci else "insufficient"),
    )


def _bootstrap_cagr(
    returns: Sequence[float], samples: int, seed: int
) -> tuple[float, float] | None:
    if samples <= 0 or len(returns) < MIN_RETURN_SAMPLES:
        return None
    rng = random.Random(seed)
    years = len(returns) / TRADING_DAYS_PER_YEAR
    values: list[float] = []
    for _ in range(samples):
        growth = math.prod(1 + returns[rng.randrange(len(returns))] for _ in returns)
        if growth > 0:
            values.append(growth ** (1 / years) - 1)
    if len(values) < max(20, samples // 2):
        return None
    values.sort()
    return values[int(0.025 * (len(values) - 1))], values[int(0.975 * (len(values) - 1))]


def _date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _rebalance_dates(dates: list[date], frequency: str) -> set[date]:
    selected: set[date] = set()
    previous: tuple[int, ...] | None = None
    for current in dates:
        iso = current.isocalendar()
        key = (current.year, current.month) if frequency == "monthly" else (iso.year, iso.week)
        if frequency == "daily" or key != previous:
            selected.add(current)
        previous = key
    return selected


def _returns(prices: Sequence[float]) -> list[float]:
    return [current / prior - 1 for prior, current in pairwise(prices) if prior > 0]


def _target_weights(
    strategy: BacktestStrategyDefinition,
    rows_by_symbol: Mapping[str, Mapping[date, Mapping[str, Any]]],
    decision_date: date,
) -> dict[str, float]:
    signals: list[tuple[str, float]] = []
    for symbol in strategy.universe:
        rows = rows_by_symbol[symbol]
        history = sorted(day for day in rows if day < decision_date)
        closes = [float(rows[day]["close"]) for day in history]
        if strategy.signal == "equal_weight":
            signal = 0.0
        elif strategy.signal == "momentum_12_1":
            if len(closes) < 253:
                continue
            signal = closes[-22] / closes[-253] - 1
        elif strategy.signal == "low_volatility":
            daily = _returns(closes[-61:])
            if len(daily) < 20:
                continue
            signal = stdev(daily)
        else:
            if len(closes) < 21:
                continue
            signal = -(closes[-1] / closes[-21] - 1)
        if math.isfinite(signal):
            signals.append((symbol, signal))
    if not signals:
        return {}
    if strategy.signal == "equal_weight":
        chosen = [symbol for symbol, _ in signals]
        return {symbol: 1 / len(chosen) for symbol in chosen}
    if strategy.signal == "low_volatility":
        chosen_vols = sorted(signals, key=lambda item: (item[1], item[0]))[: strategy.max_positions]
        raw = [(symbol, value) for symbol, value in chosen_vols]
    else:
        chosen_signals = sorted(signals, key=lambda item: (-item[1], item[0]))[
            : strategy.max_positions
        ]
        raw = [(symbol, 1.0) for symbol, _ in chosen_signals]
    if strategy.weighting == "inverse_volatility":
        weighted: list[tuple[str, float]] = []
        for symbol, _ in raw:
            history_dates = sorted(day for day in rows_by_symbol[symbol] if day < decision_date)
            closes = [float(rows_by_symbol[symbol][day]["close"]) for day in history_dates[-61:]]
            daily_returns = _returns(closes)
            if len(daily_returns) < 20:
                continue
            volatility = stdev(daily_returns)
            weighted.append((symbol, 1 / max(volatility, 1e-9)))
        raw = weighted
    total = sum(weight for _, weight in raw)
    return {symbol: weight / total for symbol, weight in raw} if total > 0 else {}


def _insufficient_response(
    request: BacktestRunRequest,
    strategy: BacktestStrategyDefinition,
    provenance: list[BacktestProvenance],
    warnings: list[str],
) -> BacktestRunResponse:
    metric = BacktestMetric(value=None, sample_count=0, status="insufficient")
    return BacktestRunResponse(
        strategy=strategy,
        start=request.start,
        end=request.end,
        status="insufficient",
        metrics=BacktestMetrics(
            cagr=metric,
            sharpe_ratio=metric,
            sortino_ratio=metric,
            max_drawdown=metric,
            calmar_ratio=metric,
            hit_rate=metric,
            annual_turnover=metric,
            bootstrap_status="disabled" if request.bootstrap_samples == 0 else "insufficient",
        ),
        equity_curve=[],
        trades=[],
        provenance=provenance,
        assumptions=["Missing observations are not filled, forward-filled, or treated as zero."],
        warnings=warnings,
    )


def run_backtest(
    request: BacktestRunRequest,
    strategy: BacktestStrategyDefinition,
    histories: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    _include_oos: bool = True,
) -> BacktestRunResponse:
    if request.strategy_id != strategy.id:
        raise ValueError("request strategy_id does not match strategy definition")
    rows_by_symbol: dict[str, dict[date, Mapping[str, Any]]] = {}
    provenance: list[BacktestProvenance] = []
    warnings: list[str] = []
    for symbol in strategy.universe:
        rows: dict[date, Mapping[str, Any]] = {}
        for row in histories.get(symbol, ()):
            if str(row.get("provider", request.provider)) != request.provider:
                continue
            session = _date(row["as_of"])
            close = float(row["close"])
            opening = float(row.get("open", close))
            if not all(math.isfinite(price) and price > 0 for price in (close, opening)):
                continue
            if session in rows:
                raise ValueError(f"duplicate OHLCV session for {symbol} on {session}")
            rows[session] = row
        rows_by_symbol[symbol] = rows
        for source in sorted({str(row.get("provider", request.provider)) for row in rows.values()}):
            source_rows = [
                row
                for day, row in rows.items()
                if str(row.get("provider", request.provider)) == source and day <= request.end
            ]
            if not source_rows:
                continue
            required = ("source_url", "license_class", "retrieved_at")
            for row in source_rows:
                missing = [field for field in required if not row.get(field)]
                if missing:
                    raise ValueError(
                        f"Incomplete OHLCV provenance for {symbol}/{source}: {', '.join(missing)}"
                    )
            retrieved = max(
                datetime.fromisoformat(str(row["retrieved_at"]).replace("Z", "+00:00"))
                for row in source_rows
            )
            source_pairs = {
                (str(row["source_url"]), str(row["license_class"])) for row in source_rows
            }
            if len(source_pairs) != 1:
                raise ValueError(
                    f"Mixed provenance for {symbol}/{source}; refusing to combine rows"
                )
            provenance.append(
                BacktestProvenance(
                    symbol=symbol,
                    provider=source,
                    source_url=str(source_rows[0]["source_url"]),
                    license_class=LicenseClass(str(source_rows[0]["license_class"])),
                    retrieved_at=retrieved,
                    as_of_start=min(_date(row["as_of"]) for row in source_rows),
                    as_of_end=max(_date(row["as_of"]) for row in source_rows),
                )
            )
        if not rows:
            warnings.append(f"No {request.provider} OHLCV rows for {symbol}")

    requested_sessions = [
        {day for day in rows if request.start <= day <= request.end}
        for rows in rows_by_symbol.values()
    ]
    if any(not sessions for sessions in requested_sessions):
        return _insufficient_response(
            request,
            strategy,
            provenance,
            [*warnings, "At least one universe member has no requested-window price history."],
        )
    if not requested_sessions or any(
        sessions != requested_sessions[0] for sessions in requested_sessions[1:]
    ):
        raise ValueError(
            "Universe symbols do not have identical OHLCV sessions in the requested window"
        )
    for symbol, rows in rows_by_symbol.items():
        if any(
            str(row.get("currency", "")).upper() != "USD"
            for row in rows.values()
            if request.start <= _date(row["as_of"]) <= request.end
        ):
            raise ValueError(f"Non-USD OHLCV is not comparable in this portfolio: {symbol}")
    window_dates = sorted(requested_sessions[0])
    if len(window_dates) < 2:
        return _insufficient_response(
            request,
            strategy,
            provenance,
            [*warnings, "Fewer than two sessions; no portfolio return was calculated."],
        )
    if window_dates[0] > request.start or window_dates[-1] < request.end:
        warnings.append("The available sessions do not cover both requested date boundaries.")

    warmup = {
        "momentum_12_1": 253,
        "low_volatility": 61,
        "mean_reversion_20": 21,
        "equal_weight": 0,
    }[strategy.signal]
    for symbol, rows in rows_by_symbol.items():
        if sum(day < window_dates[0] for day in rows) < warmup:
            warnings.append(
                f"Insufficient pre-window lookback for {symbol}; it will not be selected "
                "until sufficient history exists."
            )
    common_dates = sorted(set.intersection(*(set(rows) for rows in rows_by_symbol.values())))
    # Pre-start bars are visible only as lagged signals. The return loop itself
    # remains in the exact requested interval.
    dates = [day for day in common_dates if day <= request.end]
    rebalance_dates = _rebalance_dates(window_dates, strategy.rebalance)
    weights = {symbol: 0.0 for symbol in strategy.universe}
    equity = 1.0
    turnover_sum = 0.0
    cost_rate = (request.commission_bps + request.slippage_bps) / 10_000
    equity_curve: list[BacktestEquityPoint] = []
    trades: list[BacktestTrade] = []
    daily_returns: list[float] = []
    prior_session: date | None = None
    for day in dates:
        if day < window_dates[0]:
            prior_session = day
            continue
        overnight = 0.0
        open_weights = dict(weights)
        if prior_session is not None:
            asset_overnight = {
                symbol: float(
                    rows_by_symbol[symbol][day].get("open", rows_by_symbol[symbol][day]["close"])
                )
                / float(rows_by_symbol[symbol][prior_session]["close"])
                - 1
                for symbol in strategy.universe
            }
            overnight = sum(
                weights[symbol] * asset_overnight[symbol] for symbol in strategy.universe
            )
            equity *= 1 + overnight
            if 1 + overnight > 0:
                open_weights = {
                    symbol: weights[symbol] * (1 + asset_overnight[symbol]) / (1 + overnight)
                    for symbol in strategy.universe
                }
        # Carry overnight price drift into the pre-trade portfolio weights.
        # On non-rebalance sessions this is the portfolio used for intraday P&L.
        weights = dict(open_weights)
        transaction_cost = 0.0
        turnover = 0.0
        if day in rebalance_dates:
            target = _target_weights(strategy, rows_by_symbol, day)
            if target:
                turnover = sum(
                    abs(target.get(symbol, 0.0) - open_weights[symbol])
                    for symbol in strategy.universe
                )
                transaction_cost = turnover * cost_rate
                equity *= 1 - transaction_cost
                for symbol in strategy.universe:
                    old_weight = open_weights[symbol]
                    new_weight = target.get(symbol, 0.0)
                    if old_weight != new_weight:
                        traded = abs(new_weight - old_weight)
                        trades.append(
                            BacktestTrade(
                                date=day,
                                symbol=symbol,
                                previous_weight=old_weight,
                                target_weight=new_weight,
                                turnover=traded,
                                transaction_cost=traded * cost_rate,
                                execution_price=float(
                                    rows_by_symbol[symbol][day].get(
                                        "open", rows_by_symbol[symbol][day]["close"]
                                    )
                                ),
                            )
                        )
                weights = {symbol: target.get(symbol, 0.0) for symbol in strategy.universe}
                turnover_sum += turnover
        intraday_asset = {
            symbol: float(rows_by_symbol[symbol][day]["close"])
            / float(rows_by_symbol[symbol][day].get("open", rows_by_symbol[symbol][day]["close"]))
            - 1
            for symbol in strategy.universe
        }
        intraday = sum(weights[symbol] * intraday_asset[symbol] for symbol in strategy.universe)
        daily_return = (1 + overnight) * (1 - transaction_cost) * (1 + intraday) - 1
        equity *= 1 + intraday
        if 1 + intraday > 0:
            weights = {
                symbol: weights[symbol] * (1 + intraday_asset[symbol]) / (1 + intraday)
                for symbol in strategy.universe
            }
        daily_returns.append(daily_return)
        equity_curve.append(
            BacktestEquityPoint(
                date=day,
                equity=equity,
                cash=equity * (1 - sum(weights.values())),
                daily_return=daily_return,
                turnover=turnover,
                transaction_cost=transaction_cost,
            )
        )
        prior_session = day

    ci = _bootstrap_cagr(daily_returns, request.bootstrap_samples, request.bootstrap_seed)
    metrics = _metrics(daily_returns, turnover_sum, ci, request.bootstrap_samples > 0)
    metric_items = (
        metrics.cagr,
        metrics.sharpe_ratio,
        metrics.sortino_ratio,
        metrics.max_drawdown,
        metrics.calmar_ratio,
        metrics.hit_rate,
        metrics.annual_turnover,
    )
    status: Literal["complete", "insufficient"] = (
        "complete"
        if all(metric.status == "available" for metric in metric_items)
        else "insufficient"
    )
    assumptions = [
        "Signals use only data dated before execution; orders execute at next session open.",
        "Pre-start OHLCV is only for lookback; returns use the requested interval.",
        "Overnight returns use previous weights; intraday returns use post-rebalance weights.",
        "Weights drift between rebalances; turnover and costs use gross buys plus sells.",
        f"Costs: {request.commission_bps:g} bps commission + "
        f"{request.slippage_bps:g} bps slippage, "
        "charged on one-way traded notional.",
        "Raw OHLCV is used; dividends, split adjustments, taxes, market impact, borrow costs, "
        "and delisting returns are not modeled.",
        "The configured universe is fixed, not point-in-time; survivorship bias may remain.",
        f"Bootstrap: IID daily-return resampling, 95% percentile interval, seed "
        f"{request.bootstrap_seed}; not a forecast interval.",
    ]
    oos_metrics = None
    is_metrics = None
    oos_start = None
    purged_sessions = 0
    if _include_oos:
        split_index = int(len(window_dates) * 0.7)
        oos_index = split_index + warmup
        purged_sessions = min(warmup, max(len(window_dates) - split_index - 1, 0))
        is_end_index = max(split_index - 1, 0)
        is_start = window_dates[0]
        is_end = window_dates[is_end_index]
        is_request = request.model_copy(
            update={"start": is_start, "end": is_end, "bootstrap_samples": 0}
        )
        is_metrics = run_backtest(is_request, strategy, histories, _include_oos=False).metrics
        if oos_index < len(window_dates) - 1:
            oos_start = window_dates[oos_index]
            oos_request = request.model_copy(update={"start": oos_start, "bootstrap_samples": 0})
            oos_metrics = run_backtest(oos_request, strategy, histories, _include_oos=False).metrics
        else:
            warnings.append(
                "Insufficient sessions remain for a separate out-of-sample period after purge."
            )
    return BacktestRunResponse(
        strategy=strategy,
        start=request.start,
        end=request.end,
        status=status,
        metrics=metrics,
        in_sample_metrics=is_metrics if _include_oos else None,
        oos_metrics=oos_metrics,
        oos_start=oos_start,
        purged_sessions=purged_sessions,
        equity_curve=equity_curve,
        trades=trades,
        provenance=provenance,
        assumptions=assumptions,
        warnings=warnings,
    )
