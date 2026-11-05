from __future__ import annotations

import json
import math
import random
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from itertools import pairwise
from pathlib import Path
from statistics import fmean, stdev
from typing import Any, Literal

from yowayowa.backtest_models import (
    VOLATILITY_TARGET_SYMBOL,
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
MOMENTUM_LONG = 252
MOMENTUM_SKIP = 21
RISK_ON_RETURN_WINDOW = 20
RISK_ON_SMA = 100
DEFENSIVE_VOL_WINDOW = 60
DEFENSIVE_SUBSET = ("JNJ", "PG", "XOM", "JPM")
GROWTH_SELECT = 5
DEFENSIVE_SELECT = 3
GARP_MOMENTUM_CANDIDATE_SELECT = 8
GARP_PBR_SELECT = 5
_SPLIT_FACTORS = tuple(
    [1 / divisor for divisor in (2, 3, 4, 5, 7, 10, 15, 20, 25, 50, 100)]
    + [float(divisor) for divisor in (2, 3, 4, 5, 10, 20)]
)
_SPLIT_TOLERANCE = 0.08
# Fail-closed threshold for uncorroborated price discontinuities: an overnight
# open gap at or beyond this magnitude flips the run to data_quality="degraded"
# instead of passing with a warning only. A genuine >=2:1 split is <= -50%, so a
# gap this size with no calendar corroboration is an unregistered split or
# corrupt data and the numbers cannot be trusted. The 15-35% band stays
# warning-only because real earnings gaps live there. Mirrors the jump/watch
# thresholds of scripts/check_split_coverage.py
# (docs/US_STOCK_PRICE_ADJUSTMENT_POLICY_20261105.md).
_UNCORROBORATED_SPLIT_SCALE = 0.35
__all__ = ["get_strategy", "list_strategies", "run_backtest"]


def _load_split_calendar(
    split_calendar: Mapping[str, Any] | None = None,
    calendar_path: Path | None = None,
) -> tuple[dict[str, dict[date, float]], list[str]]:
    """Load or normalize the corroborated split calendar.

    Returns a mapping of uppercase symbol -> {effective_date: ratio} and any warnings.
    """
    cal_warnings: list[str] = []
    raw_cal: Any = None

    if split_calendar is not None:
        raw_cal = split_calendar
    else:
        pkg_path = (
            Path(__file__).resolve().parents[3]
            / "data"
            / "fundamentals"
            / "pbr"
            / "split-calendar.json"
        )
        cwd_path = Path("data/fundamentals/pbr/split-calendar.json")
        candidates = [calendar_path] if calendar_path is not None else [pkg_path, cwd_path]
        target_path: Path | None = None
        for p in candidates:
            if p is not None and p.is_file():
                target_path = p
                break

        if target_path is None:
            searched = str(calendar_path) if calendar_path is not None else str(pkg_path)
            cal_warnings.append(
                f"split calendar unavailable: file not found at {searched}; splits not adjusted"
            )
        else:
            try:
                raw_cal = json.loads(target_path.read_text(encoding="utf-8"))
            except Exception as exc:
                cal_warnings.append(
                    f"split calendar unavailable: failed to read {target_path} ({exc}); "
                    "splits not adjusted"
                )

    normalized: dict[str, dict[date, float]] = {}
    if isinstance(raw_cal, Mapping):
        for sym, events in raw_cal.items():
            sym_key = str(sym).strip().upper()
            sym_dict: dict[date, float] = {}
            if isinstance(events, Mapping):
                for d_str, r in events.items():
                    try:
                        sym_dict[_date(d_str)] = float(r)
                    except Exception as exc:
                        cal_warnings.append(
                            f"split calendar: skipped invalid entry for {sym_key} "
                            f"({d_str}={r}): {exc}"
                        )
            elif isinstance(events, Sequence):
                for item in events:
                    if isinstance(item, Mapping) and "effective_date" in item and "ratio" in item:
                        try:
                            sym_dict[_date(item["effective_date"])] = float(item["ratio"])
                        except Exception as exc:
                            cal_warnings.append(
                                f"split calendar: skipped invalid entry for {sym_key} "
                                f"({item}): {exc}"
                            )
                    else:
                        cal_warnings.append(
                            f"split calendar: skipped invalid entry structure for {sym_key}: {item}"
                        )
            else:
                cal_warnings.append(
                    f"split calendar: skipped invalid events collection for {sym_key}: "
                    f"{type(events).__name__}"
                )
            normalized[sym_key] = sym_dict
    elif raw_cal is not None:
        cal_warnings.append(
            f"split calendar unavailable: expected mapping but got {type(raw_cal).__name__}; "
            "splits not adjusted"
        )
    return normalized, cal_warnings


def _metric(value: float | None, count: int, *, undefined: bool = False) -> BacktestMetric:
    if undefined and count >= MIN_RETURN_SAMPLES:
        return BacktestMetric(value=None, sample_count=count, status="undefined")
    if value is not None and not math.isfinite(value):
        return BacktestMetric(value=None, sample_count=count, status="undefined")
    if count < MIN_RETURN_SAMPLES or value is None:
        return BacktestMetric(value=None, sample_count=count, status="insufficient")
    return BacktestMetric(value=value, sample_count=count, status="available")


def _metrics(
    returns: list[float],
    turnover: float,
    ci: tuple[float, float] | None,
    bootstrap_enabled: bool,
    annualization_days: int = TRADING_DAYS_PER_YEAR,
) -> BacktestMetrics:
    count = len(returns)
    years = count / annualization_days
    growth = math.prod(1 + value for value in returns)
    cagr = growth ** (1 / years) - 1 if years and growth > 0 else None
    average = fmean(returns) if returns else None
    volatility = stdev(returns) if count > 1 else None
    sharpe = (
        average / volatility * math.sqrt(annualization_days)
        if average is not None and volatility and volatility > 0
        else None
    )
    downside = math.sqrt(fmean(min(value, 0.0) ** 2 for value in returns)) if returns else None
    sortino = (
        average / downside * math.sqrt(annualization_days)
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
        annualization_days=annualization_days,
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
    returns: Sequence[float],
    samples: int,
    seed: int,
    annualization_days: int = TRADING_DAYS_PER_YEAR,
) -> tuple[float, float] | None:
    if samples <= 0 or len(returns) < MIN_RETURN_SAMPLES:
        return None
    rng = random.Random(seed)
    years = len(returns) / annualization_days
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


def _risk_on_regime(
    rows_by_symbol: Mapping[str, Mapping[date, Mapping[str, Any]]],
    decision_date: date,
) -> bool:
    """Lagged SPY gate; insufficient/invalid inputs fail closed, never imputed."""
    rows = rows_by_symbol.get(VOLATILITY_TARGET_SYMBOL, {})
    history = sorted(day for day in rows if day < decision_date)
    if len(history) < RISK_ON_SMA:
        return False
    closes = [float(rows[day]["close"]) for day in history[-RISK_ON_SMA:]]
    if not all(math.isfinite(value) and value > 0 for value in closes):
        return False
    return closes[-1] / closes[-(RISK_ON_RETURN_WINDOW + 1)] - 1 > 0 and closes[-1] > fmean(closes)


def _garp_momentum_candidates(
    strategy: BacktestStrategyDefinition,
    rows_by_symbol: Mapping[str, Mapping[date, Mapping[str, Any]]],
    decision_date: date,
    *,
    limit: int | None = GARP_MOMENTUM_CANDIDATE_SELECT,
    missing_fundamentals: list[tuple[str, str]] | None = None,
) -> list[tuple[str, float]]:
    candidates: list[tuple[str, float]] = []
    for symbol in strategy.universe:
        rows = rows_by_symbol.get(symbol, {})
        history = sorted(day for day in rows if day < decision_date)
        reason = None
        if len(history) < MOMENTUM_LONG + 1:
            reason = "insufficient prior momentum history"
        else:
            try:
                closes = [float(rows[day]["close"]) for day in history[-(MOMENTUM_LONG + 1) :]]
                if not all(math.isfinite(value) and value > 0 for value in closes):
                    reason = "invalid prior momentum prices"
                else:
                    momentum = closes[-(MOMENTUM_SKIP + 1)] / closes[0] - 1
                    if math.isfinite(momentum):
                        candidates.append((symbol, momentum))
                    else:
                        reason = "non-finite momentum"
            except (KeyError, TypeError, ValueError, OverflowError):
                reason = "invalid prior momentum prices"
        if reason is not None and missing_fundamentals is not None:
            missing_fundamentals.append((symbol, reason))
    ranked = sorted(candidates, key=lambda item: (-item[1], item[0]))
    return ranked if limit is None else ranked[:limit]


def _garp_point_in_time_pbr(
    rows: Mapping[date, Mapping[str, Any]], decision_date: date
) -> float | None:
    # Latest eligible BAR supplies the input; never fill its missing PBR from
    # another row. An undisclosed newer row is not eligible in the first place.
    eligible: list[date] = []
    for day, row in rows.items():
        if day >= decision_date:
            continue
        try:
            disclosed = _date(row["disclosure_date"])
        except (KeyError, TypeError, ValueError):
            continue
        if disclosed < decision_date:
            eligible.append(day)
    if not eligible:
        return None
    try:
        pbr = float(rows[max(eligible)]["pbr"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    return pbr if math.isfinite(pbr) and pbr > 0 else None


def _target_weights(
    strategy: BacktestStrategyDefinition,
    rows_by_symbol: Mapping[str, Mapping[date, Mapping[str, Any]]],
    decision_date: date,
    *,
    missing_fundamentals: list[tuple[str, str]] | None = None,
) -> dict[str, float]:
    if strategy.signal == "garp_momentum_hybrid":
        ranked = _garp_momentum_candidates(
            strategy,
            rows_by_symbol,
            decision_date,
            limit=None,
            missing_fundamentals=missing_fundamentals,
        )
        target = dict.fromkeys(strategy.universe, 0.0)
        if not ranked:
            if missing_fundamentals is not None:
                missing_fundamentals.append(
                    ("portfolio", "no eligible momentum candidates; full liquidation")
                )
            return target
        valued: list[tuple[str, float]] = []
        for symbol, _ in ranked[:GARP_MOMENTUM_CANDIDATE_SELECT]:
            pbr = _garp_point_in_time_pbr(rows_by_symbol[symbol], decision_date)
            if pbr is not None:
                valued.append((symbol, pbr))
            elif missing_fundamentals is not None:
                missing_fundamentals.append((symbol, "missing or invalid point-in-time PBR"))
        if valued:
            for symbol, _ in sorted(valued, key=lambda item: (item[1], item[0]))[:GARP_PBR_SELECT]:
                target[symbol] = 1 / GARP_PBR_SELECT
            return target
        if missing_fundamentals is not None:
            missing_fundamentals.append(
                ("portfolio", "no valid candidate PBR; momentum-only fallback")
            )
        # Reuse the frozen momentum path, including max_positions, with equal
        # weighting. Invalid momentum histories stay excluded. Explicit zeros
        # only expand its keys; execution/metrics remain exactly the same.
        eligible_symbols = {symbol for symbol, _ in ranked}
        fallback_rows = {
            symbol: rows_by_symbol.get(symbol, {}) if symbol in eligible_symbols else {}
            for symbol in strategy.universe
        }
        momentum = _target_weights(
            strategy.model_copy(update={"signal": "momentum_12_1", "weighting": "equal"}),
            fallback_rows,
            decision_date,
        )
        return {symbol: momentum.get(symbol, 0.0) for symbol in strategy.universe}

    if strategy.signal == "defensive_rotation":
        risk_on = _risk_on_regime(rows_by_symbol, decision_date)
        candidates: list[tuple[str, float]] = []
        for symbol in strategy.universe:
            if not risk_on and symbol not in DEFENSIVE_SUBSET:
                continue
            rows = rows_by_symbol.get(symbol, {})
            history = sorted(day for day in rows if day < decision_date)
            required = MOMENTUM_LONG + 1 if risk_on else DEFENSIVE_VOL_WINDOW + 1
            if len(history) < required:
                continue
            closes = [float(rows[day]["close"]) for day in history[-required:]]
            if not all(math.isfinite(value) and value > 0 for value in closes):
                continue
            if risk_on:
                value = closes[-(MOMENTUM_SKIP + 1)] / closes[-(MOMENTUM_LONG + 1)] - 1
            else:
                returns = _returns(closes)
                if len(returns) != DEFENSIVE_VOL_WINDOW or not all(
                    math.isfinite(value) for value in returns
                ):
                    continue
                value = stdev(returns)
            if math.isfinite(value):
                candidates.append((symbol, value))
        ranked = sorted(candidates, key=lambda item: (-item[1] if risk_on else item[1], item[0]))
        selected_symbols = {
            symbol for symbol, _ in ranked[: GROWTH_SELECT if risk_on else DEFENSIVE_SELECT]
        }
        # Growth slots are fixed at 20%; missing slots stay in cash. Defensive
        # candidates share 1/N even when fewer than three are eligible.
        weight = (
            1 / GROWTH_SELECT if risk_on else 1 / len(selected_symbols) if selected_symbols else 0.0
        )
        return {
            symbol: weight if symbol in selected_symbols else 0.0 for symbol in strategy.universe
        }

    if strategy.signal == "vol_target_defensive":
        lookback = strategy.lookback or 21
        target_vol = strategy.target_volatility if strategy.target_volatility is not None else 0.15
        # Frozen US-equity convention: annualization_days=252, not crypto's 365.
        sigma_star = target_vol / math.sqrt(252)
        rows = rows_by_symbol.get(VOLATILITY_TARGET_SYMBOL, {})
        history = sorted(day for day in rows if day < decision_date)
        closes = [float(rows[day]["close"]) for day in history]
        weight = 0.0
        if len(closes) >= lookback + 1:
            returns = _returns(closes[-(lookback + 1) :])
            # Invalid prior prices can omit returns in _returns; never impute.
            # statistics.stdev may raise on inf/nan, so fail closed first.
            if len(returns) == lookback and all(math.isfinite(value) for value in returns):
                sigma = stdev(returns)
                if math.isfinite(sigma):
                    weight = 1.0 if sigma == 0.0 else min(1.0, sigma_star / sigma)
        target = {symbol: weight / len(strategy.universe) for symbol in strategy.universe}
        assert sum(target.values()) <= 1 + 1e-12, "Volatility target must never use leverage"
        # Explicit zeros liquidate old holdings; an empty dict would retain them.
        return target

    if strategy.signal == "crypto_trend_dual_ma":
        lookback = strategy.lookback or 200
        active: list[str] = []
        for symbol in strategy.universe:
            rows = rows_by_symbol[symbol]
            history = sorted(day for day in rows if day < decision_date)
            closes = [float(rows[day]["close"]) for day in history]
            if len(closes) >= lookback and closes[-1] > fmean(closes[-lookback:]):
                active.append(symbol)
        # An empty target retains holdings in the execution loop. Explicit
        # zeros are essential to liquidate every holding when all gates fail.
        weight = 1 / len(active) if active else 0.0
        return {symbol: weight if symbol in active else 0.0 for symbol in strategy.universe}

    signals: list[tuple[str, float]] = []
    for symbol in strategy.universe:
        rows = rows_by_symbol[symbol]
        if strategy.signal in {"value_fundamental", "kiyohara_value"}:
            prior = sorted(day for day in rows if day < decision_date)
            eligible: list[tuple[date, Mapping[str, Any]]] = []
            for day in prior:
                row = rows[day]
                try:
                    disclosed = _date(row["disclosure_date"])
                except (KeyError, TypeError, ValueError):
                    if day == prior[-1] and missing_fundamentals is not None:
                        missing_fundamentals.append((symbol, "missing or invalid disclosure_date"))
                    continue
                if disclosed < decision_date:
                    eligible.append((day, row))
            if not eligible:
                if missing_fundamentals is not None:
                    missing_fundamentals.append(
                        (symbol, "no previously disclosed financial inputs")
                    )
                continue
            _, fundamental = max(eligible, key=lambda item: item[0])
            try:
                pbr = float(fundamental["pbr"])
                per = float(fundamental["per"])
                cash_ratio = float(
                    fundamental[
                        "kiyohara_net_cash_ratio"
                        if strategy.signal == "kiyohara_value"
                        else "net_cash_ratio"
                    ]
                )
            except (KeyError, TypeError, ValueError):
                if missing_fundamentals is not None:
                    missing_fundamentals.append(
                        (symbol, "missing or invalid valuation/net-cash inputs")
                    )
                continue
            values = (pbr, per, cash_ratio)
            if not all(math.isfinite(value) for value in values) or pbr <= 0 or per <= 0:
                if missing_fundamentals is not None:
                    missing_fundamentals.append(
                        (symbol, "non-finite inputs or non-positive pbr/per")
                    )
                continue
            # Bounded reciprocal valuation terms plus the supplied net-cash ratio.
            signal = 1 / pbr + 1 / per + cash_ratio
            signals.append((symbol, signal))
            continue
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
            if len(daily) < 60:
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
            if len(daily_returns) < 60:
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
        annualization_days=request.annualization_days,
        metrics=BacktestMetrics(
            annualization_days=request.annualization_days,
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
        assumptions=[
            "Missing observations are not filled, forward-filled, or treated as zero.",
            f"Annualization: {request.annualization_days} observations per year.",
        ],
        warnings=warnings,
    )


def run_backtest(
    request: BacktestRunRequest,
    strategy: BacktestStrategyDefinition,
    histories: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    _include_oos: bool = True,
    split_calendar: Mapping[str, Any] | None = None,
    split_calendar_path: Path | None = None,
) -> BacktestRunResponse:
    if request.strategy_id != strategy.id:
        raise ValueError("request strategy_id does not match strategy definition")
    rows_by_symbol: dict[str, dict[date, Mapping[str, Any]]] = {}
    provenance: list[BacktestProvenance] = []
    warnings: list[str] = []
    data_quality_degraded = False
    cal_by_symbol, cal_warnings = _load_split_calendar(
        split_calendar=split_calendar, calendar_path=split_calendar_path
    )
    warnings.extend(cal_warnings)
    for symbol in strategy.universe:
        raw_rows: list[dict[str, Any]] = []
        sessions_seen: set[date] = set()
        for row in histories.get(symbol, ()):
            if str(row.get("provider", request.provider)) != request.provider:
                continue
            session = _date(row["as_of"])
            if session > request.end:
                continue
            close = float(row.get("adj_close", row.get("adjusted_close", row["close"])))
            opening = float(row.get("adj_open", row.get("adjusted_open", row.get("open", close))))
            if not all(math.isfinite(price) and price > 0 for price in (close, opening)):
                continue
            if session in sessions_seen:
                raise ValueError(f"duplicate OHLCV session for {symbol} on {session}")
            sessions_seen.add(session)
            raw_rows.append(dict(row))

        raw_rows.sort(key=lambda r: _date(r["as_of"]))
        for r in raw_rows:
            r["raw_open"] = float(r.get("open", r.get("close", 0.0)))
        has_adj = any("adj_close" in r or "adjusted_close" in r for r in raw_rows)
        if has_adj:
            for r in raw_rows:
                v_close = r.get("adj_close")
                if v_close is None:
                    v_close = r.get("adjusted_close")
                if v_close is not None:
                    r["close"] = float(v_close)

                v_open = r.get("adj_open")
                if v_open is None:
                    v_open = r.get("adjusted_open")
                if v_open is not None:
                    r["open"] = float(v_open)

                v_high = r.get("adj_high")
                if v_high is None:
                    v_high = r.get("adjusted_high")
                if v_high is not None:
                    r["high"] = float(v_high)

                v_low = r.get("adj_low")
                if v_low is None:
                    v_low = r.get("adjusted_low")
                if v_low is not None:
                    r["low"] = float(v_low)
        else:
            sym_cal = cal_by_symbol.get(symbol.strip().upper(), {})
            for i in range(1, len(raw_rows)):
                prev = raw_rows[i - 1]
                curr = raw_rows[i]
                session_date = _date(curr["as_of"])
                prev_close = float(prev["close"])
                curr_open = float(curr.get("open", curr["close"]))
                curr_close = float(curr["close"])
                if prev_close <= 0 or curr_open <= 0 or curr_close <= 0:
                    continue
                ratio_open = curr_open / prev_close
                ratio_close = curr_close / prev_close

                factor = None
                basis = None
                ratio_val = None

                # Source 1: SEC-confirmed split calendar
                if session_date in sym_cal:
                    cal_ratio = sym_cal[session_date]
                    if cal_ratio > 0:
                        expected_factor = 1.0 / cal_ratio
                        if (
                            abs(ratio_open - expected_factor) / expected_factor <= 0.15
                            and abs(ratio_close - expected_factor) / expected_factor <= 0.15
                        ):
                            factor = expected_factor
                            basis = "sec_calendar"
                            ratio_val = cal_ratio

                # Source 2: Explicit provider metadata (split_factor / split_ratio)
                if factor is None and ("split_factor" in curr or "split_ratio" in curr):
                    v_sf = curr.get("split_factor")
                    if v_sf is None:
                        v_sf = curr.get("split_ratio")
                    if v_sf is not None:
                        raw_sf = float(v_sf)
                        if raw_sf > 1.0:
                            exp_factor = 1.0 / raw_sf
                            exp_ratio = raw_sf
                        elif 0 < raw_sf < 1.0:
                            exp_factor = raw_sf
                            exp_ratio = 1.0 / raw_sf
                        else:
                            exp_factor = None
                            exp_ratio = None

                        if exp_factor is not None:
                            if (
                                abs(ratio_open - exp_factor) / exp_factor <= 0.15
                                and abs(ratio_close - exp_factor) / exp_factor <= 0.15
                            ):
                                factor = exp_factor
                                basis = "explicit_metadata"
                                ratio_val = exp_ratio
                            else:
                                warnings.append(
                                    f"Skipped uncorroborated split_factor={raw_sf:g} for "
                                    f"{symbol} on {session_date}: observed ratio "
                                    f"open={ratio_open:.4f}, close={ratio_close:.4f} did not "
                                    f"match expected factor {exp_factor:.4f}"
                                )

                if factor is not None and abs(factor - 1.0) > 1e-4:
                    for j in range(i):
                        raw_rows[j]["close"] = float(raw_rows[j]["close"]) * factor
                        if "open" in raw_rows[j]:
                            raw_rows[j]["open"] = float(raw_rows[j]["open"]) * factor
                        if "high" in raw_rows[j]:
                            raw_rows[j]["high"] = float(raw_rows[j]["high"]) * factor
                        if "low" in raw_rows[j]:
                            raw_rows[j]["low"] = float(raw_rows[j]["low"]) * factor
                    warnings.append(
                        f"Split adjustment applied for {symbol} on {session_date}: "
                        f"ratio={ratio_val:g}, basis={basis}"
                    )
                else:
                    unexplained_gap = abs(ratio_open - 1.0)
                    if unexplained_gap > 0.15:
                        warnings.append(
                            f"Uncorroborated price discontinuity on {symbol} at {session_date}: "
                            f"overnight open ratio {ratio_open:.4f}, close ratio "
                            f"{ratio_close:.4f} (no adjustment applied)"
                        )
                        if unexplained_gap >= _UNCORROBORATED_SPLIT_SCALE:
                            # Fail-closed: a split-scale gap with no corroborated
                            # calendar entry means the series carries an
                            # unregistered split or corrupt data; the run must not
                            # present itself as clean (data_quality="degraded").
                            data_quality_degraded = True

        rows: dict[date, Mapping[str, Any]] = {_date(r["as_of"]): r for r in raw_rows}
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
    currencies: set[str] = set()
    for symbol, rows in rows_by_symbol.items():
        symbol_currencies = {
            str(row.get("currency", "")).upper()
            for row in rows.values()
            if _date(row["as_of"]) <= request.end
        }
        if not symbol_currencies.issubset({"USD", "USDT"}):
            raise ValueError(f"Non-USD/USDT OHLCV is not comparable in this portfolio: {symbol}")
        currencies.update(symbol_currencies)
    if len(currencies) > 1:
        raise ValueError("Universe symbols have mixed currencies; refusing to combine them")
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
        "value_fundamental": 0,
        "kiyohara_value": 0,
        "crypto_trend_dual_ma": strategy.lookback or 200,
        "vol_target_defensive": (strategy.lookback or 21) + 1,
        "defensive_rotation": MOMENTUM_LONG + 1,
        "garp_momentum_hybrid": MOMENTUM_LONG + 1,
    }[strategy.signal]
    if strategy.weighting == "inverse_volatility" and strategy.signal not in {
        "crypto_trend_dual_ma",
        "vol_target_defensive",
        "defensive_rotation",
    }:
        warmup = max(warmup, 61)
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
    financial_gaps: dict[tuple[str, str], list[date]] = {}
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
            missing_fundamentals: list[tuple[str, str]] = []
            target = _target_weights(
                strategy, rows_by_symbol, day, missing_fundamentals=missing_fundamentals
            )
            for issue in missing_fundamentals:
                financial_gaps.setdefault(issue, []).append(day)
            if missing_fundamentals and not target:
                financial_gaps.setdefault(
                    ("portfolio", "no eligible financial targets; existing holdings retained"), []
                ).append(day)
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
                                        "raw_open",
                                        rows_by_symbol[symbol][day].get(
                                            "open", rows_by_symbol[symbol][day]["close"]
                                        ),
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

    for (symbol, reason), affected_dates in sorted(financial_gaps.items()):
        warnings.append(
            f"Fundamental data unavailable for {symbol}: {reason}; "
            f"rebalance dates {affected_dates[0]} through {affected_dates[-1]} "
            f"({len(affected_dates)} affected decisions). Missing inputs are not imputed."
        )
    ci = _bootstrap_cagr(
        daily_returns, request.bootstrap_samples, request.bootstrap_seed, request.annualization_days
    )
    metrics = _metrics(
        daily_returns, turnover_sum, ci, request.bootstrap_samples > 0, request.annualization_days
    )
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
        f"Annualization: {request.annualization_days} observations per year for CAGR, "
        "Sharpe, Sortino, turnover and bootstrap CAGR; Calmar uses annualized CAGR.",
        "Signals use only data dated before execution; orders execute at next session open.",
        "Fundamental value signals require pbr, per, net_cash_ratio or "
        "kiyohara_net_cash_ratio, and disclosure_date in OHLCV rows; missing or "
        "not-yet-disclosed records are excluded, never imputed.",
        "When no targets are eligible, existing holdings are retained (cash if not invested); "
        "financial-input exclusions and affected rebalance date spans are reported in warnings.",
        "Pre-start OHLCV is only for lookback; returns use the requested interval.",
        "Overnight returns use previous weights; intraday returns use post-rebalance weights.",
        "Weights drift between rebalances; turnover and costs use gross buys plus sells.",
        f"Costs: {request.commission_bps:g} bps commission + "
        f"{request.slippage_bps:g} bps slippage, "
        "charged on one-way traded notional.",
        "Raw OHLCV is adjusted for stock splits when corroborated by SEC split calendar "
        "or explicit provider metadata; dividends, taxes, market impact, borrow costs, "
        "and delisting returns are not modeled.",
        "The configured universe is fixed, not point-in-time; survivorship bias may remain.",
        f"Bootstrap: IID daily-return resampling, 95% percentile interval, seed "
        f"{request.bootstrap_seed}; not a forecast interval.",
    ]
    oos_metrics = None
    if strategy.signal == "crypto_trend_dual_ma":
        assumptions[3] = (
            f"Crypto trend: close(t-1) > SMA({warmup})(t-1), using only prior-session "
            "closes; insufficient history and equality fail closed. Active assets "
            "receive equal 1/N weights; every inactive asset has explicit zero weight, "
            "and no active assets means full liquidation into interest-free cash. "
            f"Configured rebalance: {strategy.rebalance}; warmup/purge: {warmup} sessions."
        )
    is_metrics = None
    if strategy.signal == "vol_target_defensive":
        target_vol = strategy.target_volatility if strategy.target_volatility is not None else 0.15
        assumptions[3] = (
            f"Volatility target: sigma21 is the sample standard deviation of SPY's last "
            f"{strategy.lookback or 21} daily session returns strictly before execution; "
            f"sigma*={target_vol:g}/sqrt(252), w=min(1, sigma*/sigma21). "
            f"Equity assets receive equal w/{len(strategy.universe)} weights "
            "(four-asset S-02: w/4); residual 1-w is interest-free USD cash. "
            "No leverage (w<=1 hard cap); insufficient history or non-finite sigma "
            "fails closed to full liquidation; zero sigma receives w=1. "
            f"Configured rebalance: {strategy.rebalance}; warmup/purge: {warmup} closes/sessions."
        )
    if strategy.signal == "defensive_rotation":
        assumptions[3] = (
            "Defensive rotation: R = (SPY last 20-session return > 0) AND "
            "(SPY close(t-1) > SMA100(t-1)), using only closes strictly before execution. "
            "Equality, insufficient or invalid history fails closed to risk-off. "
            "Risk-on: top five 252-21 momentum assets receive 20% each; missing slots "
            "remain cash. Risk-off: lowest sample-sigma60 of JNJ/PG/XOM/JPM intersected "
            "with universe, up to three assets at equal 1/N; non-finite sigma excluded. "
            "Every nonselected asset has explicit zero weight; no candidates means "
            "full liquidation into interest-free USD cash. Generic weighting and "
            "max_positions are ignored; no leverage. "
            f"Configured rebalance: {strategy.rebalance}; warmup/purge: {warmup} closes/sessions."
        )
    if strategy.signal == "garp_momentum_hybrid":
        assumptions[3] = (
            "GARP two-stage ranking: top eight 252-21 momentum candidates, then up to "
            "five lowest point-in-time PBR assets (symbol-ascending ties). Both bar "
            "and disclosure dates must strictly precede execution; latest eligible "
            "bar supplies PBR, finite and positive, without imputation or row joins. "
            "Each selected PBR asset receives fixed 1/5; shortfall is interest-free "
            "USD cash. Generic weighting/max_positions are ignored in this branch. "
            "No valid candidate PBR delegates to equal-weight momentum using "
            "max_positions. Every nonselected asset has explicit zero weight; "
            "no momentum candidates means liquidation. No leverage. "
            f"Configured rebalance: {strategy.rebalance}; warmup/purge: {warmup} closes/sessions."
        )
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
        data_quality="degraded" if data_quality_degraded else "ok",
        annualization_days=request.annualization_days,
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
