"""Stock daily-OHLCV analysis-surface models (P4-F).

US equities are a personal-mode *analysis* surface only. This module carries
no order payloads and no execution instructions. Every persisted record keeps
full provenance (provider, source URL, license class, retrieval time, as-of)
because Alpaca market data comes from a commercial broker/data-vendor whose
SIP feed is licensed for personal use only: its data never leaves personal
mode. Missing values stay ``None`` — they are never zero-filled.

Existing crypto models are intentionally untouched (CTO-fixed, 2026-09-24):
stock records live beside them in this module without altering any crypto
class, so the crypto API/CLI/store behavior cannot drift.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

__all__ = ["StockOhlcvRecord", "normalize_stock_symbol", "stock_bar_timestamp"]


# US-listed ticker grammar: 1-5 uppercase letters or a dotted class share
# (e.g. BRK.B — live-verified 2026-09-24 that Alpaca serves dotted tickers on
# the SIP feed). Deliberately no verified-symbol set: unknown-but-well-formed
# tickers are allowed through and fail closed upstream (Alpaca answers 422 for
# unknown symbols).
_STOCK_SYMBOL_PATTERN = re.compile(r"^[A-Z]{1,5}(?:\.[A-Z])?$")


def normalize_stock_symbol(value: str) -> str:
    """Validate a US equity ticker and return it (dotted class shares OK).

    Lowercase or decorated input is rejected rather than silently repaired
    (fail-closed symbol handling, matching ``crypto_models``).
    """

    candidate = value.strip().upper()
    if not _STOCK_SYMBOL_PATTERN.fullmatch(candidate):
        raise ValueError(
            f"Invalid stock symbol: {value!r} (expected 1-5 uppercase letters, "
            f"optionally with a single class-share suffix like 'BRK.B')"
        )
    return candidate


class StockOhlcvPoint(BaseModel):
    """One daily OHLC bar from one source."""

    as_of: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None
    vwap: float | None = None
    trade_count: int | None = None


class StockOhlcvRecord(BaseModel):
    """One daily OHLCV row with complete provenance (JSONL store line)."""

    symbol: str
    interval: Literal["1d"] = "1d"
    currency: str = Field(default="USD", min_length=3, max_length=3)
    provider: str
    source_url: str
    license_class: str = Field(description="LicenseClass value, e.g. personal_only")
    retrieved_at: datetime
    as_of: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None
    vwap: float | None = None
    trade_count: int | None = None
    notes: list[str] = Field(default_factory=list)


def stock_bar_timestamp(value: object) -> datetime:
    """Parse an Alpaca bar timestamp (RFC-3339, e.g. ``2026-09-23T04:00:00Z``).

    Returns the bar-start instant in UTC. ``BarStart`` timestamps mark when
    trading for the daily bar begins (04:00 UTC for US equities), which is the
    ``as_of`` convention persisted for every row.
    """

    if not isinstance(value, str):
        raise ValueError(f"bar timestamp is not a string: {value!r}")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(UTC)


def utc_bar_start(day: datetime) -> datetime:
    """Coarse bar-start normalization (UTC midnight) for synthetic test bars."""

    return day.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
