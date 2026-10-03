from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from itertools import pairwise
from statistics import stdev
from typing import Any
from urllib.parse import urlsplit

from yowayowa.domain import LicenseClass
from yowayowa.services.backtest_definitions import list_strategies

LOW_VOLATILITY_LOOKBACK = 60
MEAN_REVERSION_LOOKBACK = 20
MIN_CLOSE = 1e-12
PROVENANCE_FIELDS = ("provider", "source_url", "license_class", "retrieved_at", "as_of", "currency")
SOURCE_FIELDS = ("provider", "source_url", "license_class", "retrieved_at")


def _quote_error(rows: Sequence[Mapping[str, Any]]) -> str | None:
    for row in rows:
        quote = row.get("currency")
        if quote is None or (isinstance(quote, str) and not quote.strip()):
            return "missing_currency"
        # Explicit uppercase ASCII codes (fiat or crypto); never infer/convert units.
        if not isinstance(quote, str) or not (
            3 <= len(quote) <= 8 and quote.isascii() and quote.isalpha() and quote.isupper()
        ):
            return "invalid_currency"
    if len({row["currency"] for row in rows}) > 1:
        return "mixed_currencies"
    return None


def _derived_metrics(closes: Sequence[float]) -> tuple[float | None, float | None]:
    sigma = None
    change = None
    if len(closes) >= LOW_VOLATILITY_LOOKBACK + 1:
        window = closes[-(LOW_VOLATILITY_LOOKBACK + 1) :]
        returns = [current / previous - 1 for previous, current in pairwise(window)]
        if not all(math.isfinite(value) for value in returns):
            raise ValueError("nonfinite returns")
        sigma = stdev(returns)
        if not math.isfinite(sigma):
            raise ValueError("nonfinite volatility")
    if len(closes) >= MEAN_REVERSION_LOOKBACK + 1:
        change = closes[-1] / closes[-(MEAN_REVERSION_LOOKBACK + 1)] - 1
        if not math.isfinite(change):
            raise ValueError("nonfinite endpoint return")
    return sigma, change


def _session(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _provenance_error(rows: Sequence[Mapping[str, Any]]) -> str | None:
    for row in rows:
        if any(
            row.get(field) is None or (isinstance(row[field], str) and not row[field].strip())
            for field in SOURCE_FIELDS
        ):
            return "missing_provenance"
        try:
            if not isinstance(row["provider"], str):
                return "invalid_provenance"
            source = urlsplit(row["source_url"])
            if source.scheme not in {"https", "http"} or not source.netloc:
                return "invalid_provenance"
            LicenseClass(row["license_class"])
            retrieved_at = row["retrieved_at"]
            if not isinstance(retrieved_at, datetime):
                retrieved_at = datetime.fromisoformat(str(retrieved_at))
            if retrieved_at.utcoffset() is None:
                return "invalid_provenance"
        except (TypeError, ValueError, AttributeError):
            return "invalid_provenance"
    if len({row["provider"] for row in rows}) > 1:
        return "mixed_providers"
    return None


def _provenance(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    observations = [{field: row[field] for field in PROVENANCE_FIELDS} for row in rows]
    return {**observations[-1], "observations": observations}


def compute_daily_strategy_signals(
    histories: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    """Rank the latest observed session; never interpolate or rewind for stale members.

    The union of source/quote/derived-valid dates is the shared session calendar
    (not civil days). Validate local metrics before constructing that calendar.
    An interior gap in the trailing 61 sessions invalidates the symbol. Without
    an exchange calendar, dates absent from every member cannot be detected.
    """
    rows_by_symbol: dict[str, list[Mapping[str, Any]]] = {}
    for symbol, history in histories.items():
        rows_by_symbol.setdefault(symbol.upper(), []).extend(history)
    universe = tuple(sorted(rows_by_symbol))
    unavailable: dict[str, str] = {}
    sessions_by_symbol: dict[str, list[date]] = {}
    closes_by_symbol: dict[str, list[float]] = {}
    metrics_by_symbol: dict[str, tuple[float | None, float | None]] = {}
    provenance: dict[str, dict[str, Any]] = {}
    for symbol in universe:
        try:
            dated_rows = sorted(
                ((_session(row["as_of"]), row) for row in rows_by_symbol[symbol]),
                key=lambda item: item[0],
            )
        except (KeyError, TypeError, ValueError):
            unavailable[symbol] = "invalid_session"
            continue
        sessions = [session for session, _ in dated_rows]
        rows_by_symbol[symbol] = [row for _, row in dated_rows]
        rows = rows_by_symbol[symbol]
        if not rows:
            unavailable[symbol] = "insufficient_60_day_history"
            continue
        if len(set(sessions)) != len(sessions):
            unavailable[symbol] = "duplicate_sessions"
            continue
        try:
            closes = [float(row["close"]) for row in rows]
        except (KeyError, TypeError, ValueError, OverflowError):
            unavailable[symbol] = "invalid_close"
            continue
        if any(
            isinstance(row["close"], bool) or not math.isfinite(price) or price < MIN_CLOSE
            for row, price in zip(rows, closes, strict=True)
        ):
            unavailable[symbol] = "invalid_close"
            continue
        error = _provenance_error(rows)
        if error:
            unavailable[symbol] = error
            continue
        error = _quote_error(rows)
        if error:
            unavailable[symbol] = error
            continue
        try:
            metrics_by_symbol[symbol] = _derived_metrics(closes)
        except (ArithmeticError, ValueError):
            unavailable[symbol] = "invalid_returns"
            continue
        sessions_by_symbol[symbol] = sessions
        closes_by_symbol[symbol] = closes
        provenance[symbol] = _provenance(rows[-(LOW_VOLATILITY_LOOKBACK + 1) :])

    # Rejected rows are not authoritative calendar observations. In particular,
    # a bad symbol with future-dated prices must not mark healthy symbols stale.
    calendar = sorted({session for sessions in sessions_by_symbol.values() for session in sessions})
    as_of = calendar[-1] if calendar else None
    lookback_sessions = calendar[-(LOW_VOLATILITY_LOOKBACK + 1) :]
    volatility: list[dict[str, Any]] = []
    mean_reversion: list[dict[str, Any]] = []
    for symbol, sessions in sessions_by_symbol.items():
        closes = closes_by_symbol[symbol]
        if sessions[-1] != as_of:
            unavailable[symbol] = "stale_data"
            continue
        session_set = set(sessions)
        if any(
            session >= sessions[0] and session not in session_set for session in lookback_sessions
        ):
            unavailable[symbol] = "interior_gaps"
            continue
        sigma, change = metrics_by_symbol[symbol]
        if sigma is not None:
            volatility.append(
                {
                    "symbol": symbol,
                    "daily_volatility": sigma,
                    "observations": LOW_VOLATILITY_LOOKBACK,
                }
            )
        if change is not None:
            mean_reversion.append(
                {"symbol": symbol, "return_20d": change, "observations": MEAN_REVERSION_LOOKBACK}
            )
        if len(closes) < LOW_VOLATILITY_LOOKBACK + 1:
            unavailable[symbol] = "insufficient_60_day_history"

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
                "provenance": _provenance(
                    rows_by_symbol[row["symbol"]][-(MEAN_REVERSION_LOOKBACK + 1) :]
                ),
            }
            for row in mean_reversion
            if row["return_20d"] < 0
        ],
        "unavailable": unavailable,
        "provenance": provenance,
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
