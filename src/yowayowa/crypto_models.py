"""Crypto daily-OHLCV analysis-surface models (P4-E phase 1).

Crypto is a personal-mode *analysis* surface only. This module carries no
order payloads and no execution instructions. Every persisted record keeps
full provenance (provider, source URL, license class, retrieval time, as-of)
because the two public sources (CoinGecko, Binance) are commercial
aggregators/exchanges, not official reference rates: their data never leaves
personal mode. Missing values stay ``None`` — they are never zero-filled.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from yowayowa.fx_models import SUPPORTED_CRYPTO_ASSETS
from yowayowa.symbols import InputValidationError

__all__ = [
    "BINANCE_SYMBOLS",
    "COINGECKO_COIN_IDS",
    "CryptoOhlcvPoint",
    "CryptoOhlcvRecord",
    "normalize_crypto_symbol",
    "utc_midnight",
]


COINGECKO_COIN_IDS: dict[str, str] = {"BTC": "bitcoin", "ETH": "ethereum"}
BINANCE_SYMBOLS: dict[str, str] = {"BTC": "BTCUSDT", "ETH": "ETHUSDT"}

_SYMBOL_PATTERN = re.compile(r"^[A-Z]{3,5}$")


def normalize_crypto_symbol(value: str) -> str:
    """Validate a supported crypto asset symbol (BTC/ETH) and return it.

    Uppercase-only, no separators: lowercase or decorated input is rejected
    rather than silently repaired (fail-closed symbol handling).
    """

    candidate = value.strip().upper()
    if not _SYMBOL_PATTERN.fullmatch(candidate):
        raise InputValidationError(
            f"Invalid crypto symbol: {value!r} (expected 3-5 uppercase letters, e.g. 'BTC')"
        )
    if candidate not in SUPPORTED_CRYPTO_ASSETS:
        raise InputValidationError(
            f"Unsupported crypto asset: {candidate!r} (supported: "
            f"{', '.join(sorted(SUPPORTED_CRYPTO_ASSETS))})"
        )
    return candidate


class CryptoOhlcvPoint(BaseModel):
    """One daily OHLC bar from one source."""

    as_of: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None
    quote_volume: float | None = None


class CryptoOhlcvRecord(BaseModel):
    """One daily OHLCV row with complete provenance (JSONL store line)."""

    symbol: str
    interval: Literal["1d"] = "1d"
    currency: str = Field(min_length=3, max_length=4, description="Quote currency code (USD/USDT)")
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
    quote_volume: float | None = None
    notes: list[str] = Field(default_factory=list)


def utc_midnight(day: datetime) -> datetime:
    """Normalize a bar timestamp to UTC midnight (Binance/CoinGecko ms epochs)."""

    return day.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
