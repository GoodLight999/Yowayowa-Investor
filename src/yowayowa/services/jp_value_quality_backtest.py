"""Portfolio simulation for the Japan value x quality research mode.

The engine here is deliberately small: it reuses the repository's return /
metric machinery (:func:`yowayowa.services.backtest._metrics`) so that CAGR,
Sharpe, drawdown and turnover are computed exactly like every other backtest in
this project, while the *selection* is the cross-sectional score from
:mod:`yowayowa.services.jp_value_quality`.

Execution convention (identical to ``services.backtest``): a decision is taken
from data available strictly before the rebalance session, orders execute at
that session's open, costs charge one-way traded notional, and weights drift
between rebalances.  Prices are price-only closes (no dividends, no splits
adjustment beyond the vendor's own), and no fundamental input is ever
forward-filled or zero-filled.
"""

from __future__ import annotations

import itertools
import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from statistics import fmean
from typing import Any, cast

from yowayowa.services.backtest import _bootstrap_cagr, _metrics
from yowayowa.services.jp_value_quality import (
    AnnualFundamental,
    BaselineFilter,
    FactorInputs,
    MissingPolicy,
    OpSimpleBasis,
    ScoreWeights,
    SymbolFundamentals,
    UniverseScore,
    compute_factor_inputs,
    score_universe,
)

SelectionFn = Callable[[UniverseScore, "PortfolioParams"], dict[str, float]]


@dataclass(frozen=True, slots=True)
class PriceSeries:
    opens: dict[date, float]
    closes: dict[date, float]
    # Split-neutralised copies used only for *return* arithmetic.  Raw closes
    # stay untouched so that market capitalisation (price x period share count)
    # remains point-in-time correct.
    adj_opens: dict[date, float] = field(default_factory=dict)
    adj_closes: dict[date, float] = field(default_factory=dict)
    split_dates: tuple[date, ...] = ()
    repaired_bars: tuple[date, ...] = ()

    @property
    def dates(self) -> list[date]:
        return sorted(self.closes)

    def return_open(self, day: date) -> float | None:
        return (self.adj_opens or self.opens).get(day)

    def return_close(self, day: date) -> float | None:
        return (self.adj_closes or self.closes).get(day)


_SPLIT_FACTORS = tuple(
    [1 / divisor for divisor in (2, 3, 4, 5, 10, 20, 50, 100)]
    + [float(divisor) for divisor in (2, 3, 4, 5, 10)]
)
_SPLIT_TOLERANCE = 0.03
_GLITCH_TOLERANCE = 0.08


def adjust_prices(
    closes: dict[date, float], opens: dict[date, float]
) -> tuple[dict[date, float], dict[date, float], tuple[date, ...], tuple[date, ...]]:
    """Split-neutralise a price history for return arithmetic.

    Two vendor artefacts are handled, and nothing else:

    * a single-session move that matches a plain split ratio within 3%
      (Yahoo returns an unadjusted bar across some Japanese splits) is treated
      as a split: earlier prices are scaled by the split factor, so the return
      across that session becomes the genuine residual move;
    * a move that fully reverses on the next session (|r1*r2| < 0.5%) is a bad
      tick and is repaired by geometric interpolation, because no traded market
      moves 60% down and 900% up in two sessions.

    Every repair is reported so the run can state exactly how many were made.
    """

    dates = sorted(closes)
    raw = [closes[day] for day in dates]
    out = list(raw)
    split_dates: list[date] = []
    repaired: list[date] = []

    index = 1
    while index < len(out):
        prior, current = out[index - 1], out[index]
        if prior <= 0 or current <= 0:
            index += 1
            continue
        ratio = current / prior
        if ratio < 0.55 or ratio > 1.8:
            # A vendor artefact (one or a few bad bars) always reverts: look for
            # a run of at most three sessions whose compounded move is ~flat and
            # then interpolate the bad bars geometrically.
            revert_at = None
            compounded = 1.0
            for length in range(1, 4):
                position = index + length
                if position >= len(out) or out[position] <= 0:
                    break
                compounded *= out[position] / out[position - 1]
                if abs(compounded * ratio - 1) < _GLITCH_TOLERANCE:
                    revert_at = position
                    break
            if revert_at is not None:
                start_price = prior
                end_price = out[revert_at]
                steps = revert_at - index
                for offset in range(1, steps + 1):
                    fraction = offset / (steps + 1)
                    out[index + offset - 1] = start_price * (
                        (end_price / start_price) ** fraction
                    )
                    repaired.append(dates[index + offset - 1])
                index = revert_at + 1
                continue

        split_factor = None
        for factor in _SPLIT_FACTORS:
            if abs(ratio - factor) / factor < _SPLIT_TOLERANCE:
                split_factor = factor
                break
        if split_factor is not None:
            split_dates.append(dates[index])
            for position in range(index):
                out[position] *= split_factor
            index += 1
            continue
        index += 1

    scale = [
        value / reference if reference else 1.0
        for value, reference in zip(out, raw, strict=True)
    ]
    adjusted_closes = {
        day: out[position] for position, day in enumerate(dates)
    }
    adjusted_opens = {
        day: opens[day] * scale[position]
        for position, day in enumerate(dates)
        if day in opens
    }
    return adjusted_closes, adjusted_opens, tuple(split_dates), tuple(repaired)


def make_series(closes: dict[date, float], opens: dict[date, float]) -> PriceSeries:
    adjusted_closes, adjusted_opens, split_dates, repaired = adjust_prices(closes, opens)
    return PriceSeries(
        opens=opens,
        closes=closes,
        adj_opens=adjusted_opens,
        adj_closes=adjusted_closes,
        split_dates=split_dates,
        repaired_bars=repaired,
    )


@dataclass(frozen=True, slots=True)
class PortfolioParams:
    label: str
    top_n: int = 20
    weights: ScoreWeights = field(default_factory=ScoreWeights)
    missing_policy: MissingPolicy = "strict"
    op_simple_basis: OpSimpleBasis = "actual"
    commission_bps: float = 10.0
    slippage_bps: float = 5.0

    @property
    def cost_rate(self) -> float:
        return (self.commission_bps + self.slippage_bps) / 10_000


@dataclass(frozen=True, slots=True)
class BacktestOutcome:
    label: str
    start: date
    end: date
    sessions: int
    equity_curve: list[tuple[date, float]]
    daily_returns: list[float]
    turnover: float
    rebalances: int
    holdings: list[int]
    missing_rate: dict[str, float]
    lookahead_violations: int
    stale_marks: int
    metrics: dict[str, Any]
    yearly_returns: dict[int, float]
    notes: list[str]


def _last_close_strictly_before(series: PriceSeries, day: date) -> float | None:
    prior = [value for value in series.closes if value < day]
    if not prior:
        return None
    return series.closes[max(prior)]


def load_snapshot(
    directory: Path,
) -> tuple[
    dict[str, SymbolFundamentals],
    dict[str, PriceSeries],
    dict[str, PriceSeries],
    dict[str, Any],
]:
    """Read the jsonl snapshot written by the fetch script."""

    fundamentals: dict[str, SymbolFundamentals] = {}
    with (directory / "fundamentals.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            annual = tuple(
                AnnualFundamental(
                    fy_end=date.fromisoformat(item["fy_end"]),
                    revenue=item.get("revenue"),
                    operating_income=item.get("operating_income"),
                    net_income=item.get("net_income"),
                    interest_expense=item.get("interest_expense"),
                    eps_diluted=item.get("eps_diluted"),
                    equity=item.get("equity"),
                    assets=item.get("assets"),
                    current_assets=item.get("current_assets"),
                    current_liabilities=item.get("current_liabilities"),
                    liabilities=item.get("liabilities"),
                    shares_outstanding=item.get("shares_outstanding"),
                )
                for item in record.get("annual") or []
            )
            fundamentals[str(record["symbol"])] = SymbolFundamentals(
                symbol=str(record["symbol"]), annual=annual
            )

    def read_prices(path: Path) -> dict[str, PriceSeries]:
        series: dict[str, PriceSeries] = {}
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                record = json.loads(line)
                opens: dict[date, float] = {}
                closes: dict[date, float] = {}
                for bar in record.get("bars") or []:
                    if len(bar) == 3:
                        day = date.fromisoformat(bar[0])
                        opens[day] = float(bar[1])
                        closes[day] = float(bar[2])
                    else:
                        day = date.fromisoformat(bar[0])
                        opens[day] = float(bar[1])
                        closes[day] = float(bar[1])
                if closes:
                    series[str(record["symbol"])] = make_series(closes, opens)
        return series

    prices = read_prices(directory / "prices.jsonl")
    benchmarks = read_prices(directory / "benchmark.jsonl")
    manifest_path = directory / "manifest.json"
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.exists()
        else {}
    )
    return fundamentals, prices, benchmarks, manifest


def rebalance_sessions(calendar: Sequence[date]) -> list[date]:
    """First session of every month: a fixed, rule-based monthly rebalance."""

    selected: list[date] = []
    previous: tuple[int, int] | None = None
    for day in calendar:
        key = (day.year, day.month)
        if key != previous:
            selected.append(day)
            previous = key
    return selected


def build_inputs(
    fundamentals: Mapping[str, SymbolFundamentals],
    prices: Mapping[str, PriceSeries],
    decision_date: date,
    *,
    op_simple_basis: OpSimpleBasis = "actual",
) -> tuple[dict[str, FactorInputs], int]:
    """Point-in-time factor inputs for every symbol with a prior close."""

    inputs: dict[str, FactorInputs] = {}
    violations = 0
    for symbol, series in prices.items():
        snapshot = fundamentals.get(symbol)
        if snapshot is None:
            continue
        close = _last_close_strictly_before(series, decision_date)
        if close is None:
            continue
        item = compute_factor_inputs(
            snapshot, decision_date, close, op_simple_basis=op_simple_basis
        )
        if item is None:
            continue
        if item.disclosed_at >= decision_date:
            violations += 1
        inputs[symbol] = item
    return inputs, violations


def select_ranked(
    scores: UniverseScore, params: PortfolioParams
) -> dict[str, float]:
    ranked = [item for item in scores.ranked() if item.composite is not None]
    chosen = ranked[: params.top_n]
    if not chosen:
        return {}
    weight = 1 / len(chosen)
    return {item.symbol: weight for item in chosen}


def select_baseline(
    scores: UniverseScore, params: PortfolioParams
) -> dict[str, float]:
    rule = BaselineFilter()
    passing = [
        item
        for item in scores.scores.values()
        if rule.passes(item.inputs) and item.inputs.pbr is not None
    ]
    passing.sort(key=lambda item: (item.inputs.pbr or math.inf, item.symbol))
    chosen = passing[: params.top_n] if params.top_n else passing
    if not chosen:
        return {}
    weight = 1 / len(chosen)
    return {item.symbol: weight for item in chosen}


def _yearly_returns(equity_curve: Sequence[tuple[date, float]]) -> dict[int, float]:
    by_year: dict[int, list[float]] = {}
    for day, equity in equity_curve:
        by_year.setdefault(day.year, []).append(equity)
    yearly: dict[int, float] = {}
    prior_end = 1.0
    for year in sorted(by_year):
        values = by_year[year]
        yearly[year] = values[-1] / prior_end - 1
        prior_end = values[-1]
    return yearly


def run_backtest(
    *,
    fundamentals: Mapping[str, SymbolFundamentals],
    prices: Mapping[str, PriceSeries],
    benchmark: PriceSeries,
    start: date,
    end: date,
    params: PortfolioParams,
    selection: SelectionFn,
    bootstrap_samples: int = 0,
    bootstrap_seed: int = 20261021,
    inputs_cache: dict[tuple[date, str], tuple[dict[str, FactorInputs], int]] | None = None,
) -> BacktestOutcome:
    calendar = [day for day in benchmark.dates if start <= day <= end]
    if len(calendar) < 2:
        raise ValueError("benchmark has fewer than two sessions in the window")
    rebalances = set(rebalance_sessions(calendar))

    # Marks fall back to the last known close (documented, counted as stale).
    last_close: dict[str, float] = {}
    weights: dict[str, float] = {}
    equity = 1.0
    equity_curve: list[tuple[date, float]] = []
    daily_returns: list[float] = []
    turnover_sum = 0.0
    holdings: list[int] = []
    missing_rate_samples: dict[str, list[float]] = {}
    lookahead_violations = 0
    stale_marks = 0
    rebalance_count = 0
    previous_session: date | None = None

    def mark_close(series: PriceSeries, symbol: str, day: date) -> float | None:
        """Latest split-neutralised close at or before ``day``."""

        nonlocal stale_marks
        close = series.return_close(day)
        if close is not None:
            last_close[symbol] = close
            return close
        prior = last_close.get(symbol)
        if prior is not None:
            stale_marks += 1
        return prior

    def session_open(series: PriceSeries, day: date, fallback: float) -> float:
        opened = series.return_open(day)
        return opened if opened is not None and opened > 0 else fallback

    for day in calendar:
        opens: dict[str, float] = {}
        closes: dict[str, float] = {}
        for symbol in weights:
            series = prices.get(symbol)
            if series is None:
                continue
            close = mark_close(series, symbol, day)
            if close is None:
                continue
            closes[symbol] = close
            opens[symbol] = session_open(series, day, close)

        overnight = 0.0
        if previous_session is not None and weights:
            asset_returns: dict[str, float] = {}
            for symbol, weight in weights.items():
                series = prices.get(symbol)
                if series is None or weight <= 0:
                    continue
                prior_close = series.return_close(previous_session)
                if prior_close is None or prior_close <= 0:
                    continue
                today_open = opens.get(symbol)
                if today_open is None:
                    continue
                asset_returns[symbol] = today_open / prior_close - 1
            if asset_returns:
                overnight = sum(
                    weights[symbol] * value for symbol, value in asset_returns.items()
                )
                equity *= 1 + overnight
                if 1 + overnight > 0:
                    weights = {
                        symbol: weight
                        * (1 + asset_returns.get(symbol, 0.0))
                        / (1 + overnight)
                        for symbol, weight in weights.items()
                    }

        transaction_cost = 0.0
        if day in rebalances:
            cache_key = (day, params.op_simple_basis)
            cached = inputs_cache.get(cache_key) if inputs_cache is not None else None
            if cached is None:
                cached = build_inputs(
                    fundamentals,
                    prices,
                    day,
                    op_simple_basis=params.op_simple_basis,
                )
                if inputs_cache is not None:
                    inputs_cache[cache_key] = cached
            inputs, violations = cached
            lookahead_violations += violations
            scores = score_universe(
                inputs,
                weights=params.weights,
                missing_policy=params.missing_policy,
                decision_date=day,
            )
            coverage_rates = scores.coverage.get("missing_rate_by_dimension") or {}
            for dimension, rate in dict(coverage_rates).items():
                missing_rate_samples.setdefault(str(dimension), []).append(float(rate))
            target = selection(scores, params)
            rebalance_count += 1
            turnover = sum(
                abs(target.get(symbol, 0.0) - weights.get(symbol, 0.0))
                for symbol in set(target) | set(weights)
            )
            transaction_cost = turnover * params.cost_rate
            equity *= 1 - transaction_cost
            turnover_sum += turnover
            weights = dict(target)
            holdings.append(len([value for value in weights.values() if value > 0]))
            # Entry prices for freshly bought names use the same session open.
            for symbol in weights:
                series = prices.get(symbol)
                if series is None:
                    continue
                close = mark_close(series, symbol, day)
                if close is None:
                    continue
                closes[symbol] = close
                opens[symbol] = session_open(series, day, close)

        intraday = 0.0
        if weights:
            intraday = sum(
                weight * (closes[symbol] / opens[symbol] - 1)
                for symbol, weight in weights.items()
                if symbol in opens and symbol in closes and opens[symbol] > 0
            )
        daily_return = (1 + overnight) * (1 - transaction_cost) * (1 + intraday) - 1
        equity *= 1 + intraday
        if weights and 1 + intraday > 0:
            weights = {
                symbol: weight
                * (closes.get(symbol, opens.get(symbol, 1.0)) / opens.get(symbol, 1.0))
                / (1 + intraday)
                for symbol, weight in weights.items()
            }
        daily_returns.append(daily_return)
        equity_curve.append((day, equity))
        previous_session = day

    ci = None
    if bootstrap_samples:
        ci = _bootstrap_cagr(daily_returns, bootstrap_samples, bootstrap_seed)
    metrics = _metrics(daily_returns, turnover_sum, ci, bootstrap_samples > 0)
    return BacktestOutcome(
        label=params.label,
        start=calendar[0],
        end=calendar[-1],
        sessions=len(calendar),
        equity_curve=equity_curve,
        daily_returns=daily_returns,
        turnover=turnover_sum,
        rebalances=rebalance_count,
        holdings=holdings,
        missing_rate=(
            {key: fmean(values) for key, values in missing_rate_samples.items()}
            if missing_rate_samples
            else {}
        ),
        lookahead_violations=lookahead_violations,
        stale_marks=stale_marks,
        metrics={
            "cagr": metrics.cagr.model_dump(),
            "sharpe_ratio": metrics.sharpe_ratio.model_dump(),
            "sortino_ratio": metrics.sortino_ratio.model_dump(),
            "max_drawdown": metrics.max_drawdown.model_dump(),
            "calmar_ratio": metrics.calmar_ratio.model_dump(),
            "hit_rate": metrics.hit_rate.model_dump(),
            "annual_turnover": metrics.annual_turnover.model_dump(),
            "bootstrap_ci_95": ci,
            "bootstrap_status": metrics.bootstrap_status,
        },
        yearly_returns=_yearly_returns(equity_curve),
        notes=[],
    )


def benchmark_outcome(
    benchmark: PriceSeries, start: date, end: date, total_days: int | None = None
) -> BacktestOutcome:
    dates = [day for day in benchmark.dates if start <= day <= end]
    closes = [benchmark.return_close(day) for day in dates]
    closes = [value for value in closes if value is not None]
    returns = [
        current / prior - 1
        for prior, current in itertools.pairwise(cast("list[float]", closes))
        if prior > 0
    ]
    equity = 1.0
    curve: list[tuple[date, float]] = [(dates[0], 1.0)]
    for day, value in zip(dates[1:], returns, strict=True):
        equity *= 1 + value
        curve.append((day, equity))
    metrics = _metrics(returns, 0.0, None, False)
    return BacktestOutcome(
        label="benchmark",
        start=dates[0],
        end=dates[-1],
        sessions=len(dates),
        equity_curve=curve,
        daily_returns=returns,
        turnover=0.0,
        rebalances=0,
        holdings=[],
        missing_rate={},
        lookahead_violations=0,
        stale_marks=0,
        metrics={
            "cagr": metrics.cagr.model_dump(),
            "sharpe_ratio": metrics.sharpe_ratio.model_dump(),
            "max_drawdown": metrics.max_drawdown.model_dump(),
            "hit_rate": metrics.hit_rate.model_dump(),
        },
        yearly_returns=_yearly_returns(curve),
        notes=["price-only buy-and-hold (dividends excluded)"],
    )


def total_return(equity_curve: Sequence[tuple[date, float]]) -> float:
    if not equity_curve:
        return 0.0
    return equity_curve[-1][1] / equity_curve[0][1] - 1


def annual_return(equity_curve: Sequence[tuple[date, float]]) -> float:
    if len(equity_curve) < 2:
        return 0.0
    years = (equity_curve[-1][0] - equity_curve[0][0]).days / 365.25
    if years <= 0:
        return 0.0
    growth = equity_curve[-1][1] / equity_curve[0][1]
    return growth ** (1 / years) - 1 if growth > 0 else -1.0


def slice_outcome(outcome: BacktestOutcome, start: date, end: date) -> BacktestOutcome:
    """Rebase an outcome onto a sub-window (used for in-sample / out-of-sample)."""

    curve = [(day, value) for day, value in outcome.equity_curve if start <= day <= end]
    if len(curve) < 2:
        raise ValueError("sub-window has too few sessions")
    base = curve[0][1]
    rebased = [(day, value / base) for day, value in curve]
    returns = [
        current[1] / prior[1] - 1
        for prior, current in itertools.pairwise(rebased)
        if prior[1] > 0
    ]
    metrics = _metrics(returns, 0.0, None, False)
    return BacktestOutcome(
        label=outcome.label,
        start=curve[0][0],
        end=curve[-1][0],
        sessions=len(curve),
        equity_curve=rebased,
        daily_returns=returns,
        turnover=outcome.turnover,
        rebalances=outcome.rebalances,
        holdings=outcome.holdings,
        missing_rate=outcome.missing_rate,
        lookahead_violations=outcome.lookahead_violations,
        stale_marks=outcome.stale_marks,
        metrics={
            "cagr": metrics.cagr.model_dump(),
            "sharpe_ratio": metrics.sharpe_ratio.model_dump(),
            "sortino_ratio": metrics.sortino_ratio.model_dump(),
            "max_drawdown": metrics.max_drawdown.model_dump(),
            "calmar_ratio": metrics.calmar_ratio.model_dump(),
            "hit_rate": metrics.hit_rate.model_dump(),
            "annual_turnover": metrics.annual_turnover.model_dump(),
            "bootstrap_ci_95": None,
            "bootstrap_status": metrics.bootstrap_status,
        },
        yearly_returns=_yearly_returns(rebased),
        notes=list(outcome.notes),
    )


def summarise(outcome: BacktestOutcome) -> dict[str, Any]:
    def metric_value(key: str) -> Any:
        return (outcome.metrics.get(key) or {}).get("value")

    return {
        "label": outcome.label,
        "start": outcome.start.isoformat(),
        "end": outcome.end.isoformat(),
        "sessions": outcome.sessions,
        "rebalances": outcome.rebalances,
        "total_return": total_return(outcome.equity_curve),
        "annual_return": annual_return(outcome.equity_curve),
        "cagr": metric_value("cagr"),
        "sharpe": metric_value("sharpe_ratio"),
        "sortino": metric_value("sortino_ratio"),
        "max_drawdown": metric_value("max_drawdown"),
        "hit_rate": metric_value("hit_rate"),
        "annual_turnover": metric_value("annual_turnover"),
        "average_holdings": fmean(outcome.holdings) if outcome.holdings else None,
        "max_holdings": max(outcome.holdings) if outcome.holdings else None,
        "missing_rate": outcome.missing_rate,
        "lookahead_violations": outcome.lookahead_violations,
        "stale_marks": outcome.stale_marks,
        "yearly_returns": {str(year): value for year, value in outcome.yearly_returns.items()},
        "notes": outcome.notes,
    }
