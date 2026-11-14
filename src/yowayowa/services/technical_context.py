"""Read calculated technical indicators through the supported market-history provider."""

from __future__ import annotations

import re
from typing import Any

from yowayowa.providers.base import MarketDataProvider
from yowayowa.technical import parse_indicators

_DEFAULT_INDICATORS = ["sma20", "rsi14"]
_PERIOD = re.compile(r"^(?:[0-9]+(?:d|mo|y)|max)$")
_MAX_POINTS = 60


def read_technical_context(
    provider: MarketDataProvider,
    *,
    symbol: str,
    period: str = "1y",
    interval: str = "1d",
    indicators: list[str] | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """Return the provider's calculated indicator series, without raw bars.

    Indicator calculation remains inside the existing market-history provider
    and ``technical.py`` path. This function only validates the request and
    projects that result into a bounded, agent-readable payload.
    """

    normalized_symbol = symbol.strip().upper()
    if not normalized_symbol:
        raise ValueError("A symbol is required")
    if not _PERIOD.fullmatch(period):
        raise ValueError("Period must be a duration such as 1y or 'max'")
    if not interval or len(interval) > 10:
        raise ValueError("Interval must contain 1-10 characters")
    if not 1 <= limit <= _MAX_POINTS:
        raise ValueError(f"Limit must be between 1 and {_MAX_POINTS}")

    requested_indicators = list(indicators) if indicators else list(_DEFAULT_INDICATORS)
    # Validate supported indicator tokens before the provider performs I/O.
    parse_indicators(requested_indicators)

    try:
        history = provider.history(
            normalized_symbol,
            period,
            interval,
            requested_indicators,
        )
    except LookupError as exc:
        return {
            "status": "missing",
            "symbol": normalized_symbol,
            "period": period,
            "interval": interval,
            "indicators": [],
            "provenance": {
                "provider": provider.descriptor.name,
                "as_of": None,
            },
            "missing_reason": str(exc),
        }

    indicator_payload: list[dict[str, Any]] = []
    for series in history.indicators:
        points = series.points[-limit:]
        latest_timestamp, latest_value = series.points[-1] if series.points else (None, None)
        indicator_payload.append(
            {
                "name": series.name,
                "parameters": series.parameters,
                "pane": series.pane,
                "render": series.render,
                "reference_lines": series.reference_lines,
                "points": [
                    {"as_of": timestamp.isoformat(), "value": value} for timestamp, value in points
                ],
                "latest_value": latest_value,
                "as_of": latest_timestamp.isoformat() if latest_timestamp else None,
                "status": "available" if latest_value is not None else "missing",
            }
        )

    return {
        "status": "available" if history.bars else "missing",
        "symbol": history.symbol,
        "period": period,
        "interval": history.interval,
        "indicators": indicator_payload,
        "provenance": history.provenance.model_dump(mode="json"),
        "missing_reason": None if history.bars else "No market history returned",
    }
