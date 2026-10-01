from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from statistics import stdev
from typing import Any

from yowayowa.services.backtest_definitions import list_strategies

LOW_VOLATILITY_LOOKBACK = 60
MEAN_REVERSION_LOOKBACK = 20


def _session(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def compute_daily_strategy_signals(
    histories: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    """Compute point-in-time daily ranks from persisted, provenance-bearing OHLCV rows."""
    universe = tuple(sorted(symbol.upper() for symbol in histories))
    session_sets = [
        {_session(row["as_of"]) for row in histories.get(symbol, ())} for symbol in universe
    ]
    common_sessions = (
        set.intersection(*session_sets) if session_sets and all(session_sets) else set()
    )
    as_of = max(common_sessions, default=None)
    rows_by_symbol: dict[str, list[Mapping[str, Any]]] = {}
    for symbol in universe:
        rows = sorted(histories.get(symbol, ()), key=lambda row: _session(row["as_of"]))
        rows_by_symbol[symbol] = (
            [row for row in rows if _session(row["as_of"]) <= as_of] if as_of is not None else []
        )

    volatility: list[dict[str, Any]] = []
    mean_reversion: list[dict[str, Any]] = []
    unavailable: dict[str, str] = {}
    for symbol, rows in rows_by_symbol.items():
        closes = [float(row["close"]) for row in rows]
        if any(not math.isfinite(price) or price <= 0 for price in closes):
            unavailable[symbol] = "invalid_close"
            continue
        if len(closes) >= LOW_VOLATILITY_LOOKBACK + 1:
            returns = [
                current / previous - 1
                for previous, current in zip(closes[-61:-1], closes[-60:], strict=True)
            ]
            sigma = stdev(returns)
            if math.isfinite(sigma):
                volatility.append(
                    {"symbol": symbol, "daily_volatility": sigma, "observations": len(returns)}
                )
        if len(closes) >= MEAN_REVERSION_LOOKBACK + 1:
            change = closes[-1] / closes[-21] - 1
            mean_reversion.append({"symbol": symbol, "return_20d": change, "observations": 20})
        if len(closes) < LOW_VOLATILITY_LOOKBACK + 1:
            unavailable[symbol] = "insufficient_60_day_history"
        elif len(closes) < MEAN_REVERSION_LOOKBACK + 1:
            unavailable[symbol] = "insufficient_20_day_history"

    volatility.sort(key=lambda row: (row["daily_volatility"], row["symbol"]))
    mean_reversion.sort(key=lambda row: (row["return_20d"], row["symbol"]))
    return {
        "as_of": as_of,
        "universe": list(universe),
        "low_volatility": volatility[:5],
        "mean_reversion": mean_reversion,
        "alerts": [
            {
                "type": "mean_reversion_candidate",
                "symbol": row["symbol"],
                "return_20d": row["return_20d"],
                "as_of": as_of,
            }
            for row in mean_reversion
            if row["return_20d"] < 0
        ],
        "unavailable": unavailable,
        "provenance": {
            symbol: {
                "provider": rows[-1].get("provider"),
                "source_url": rows[-1].get("source_url"),
                "license_class": rows[-1].get("license_class"),
                "retrieved_at": rows[-1].get("retrieved_at"),
                "as_of": rows[-1].get("as_of"),
            }
            for symbol, rows in rows_by_symbol.items()
            if rows
        },
        "definitions": {
            "low_volatility": (
                "Lowest sample standard deviation of 60 daily close returns; top five."
            ),
            "mean_reversion": (
                "20-session close return, ascending; candidates are not buy recommendations."
            ),
        },
        "strategies": [strategy.model_dump(mode="json") for strategy in list_strategies()],
    }
