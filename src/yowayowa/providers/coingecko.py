"""CoinGecko public OHLC provider (P4-E phase 1, personal-only).

CoinGecko's public ``/api/v3`` endpoints are key-less but commercial: the
CTO-fixed licensing decision is ``LicenseClass.PERSONAL_ONLY`` — this is
**not** an official ECB-style reference rate, so the provider is never
public-mode safe. ``enforce_provider_policy`` refuses it in public mode at
construction.

Structure traces ``providers/frankfurter.py``: TTLCache + one shared
``httpx.Client`` + ``ProviderDescriptor`` + ``enforce_provider_policy``.
Missing data is never zero-filled: days absent from the source response stay
absent; malformed/absent rows raise ``LookupError`` when nothing usable remains.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
from cachetools import TTLCache

from yowayowa.config import Settings
from yowayowa.crypto_models import (
    COINGECKO_COIN_IDS,
    CryptoOhlcvPoint,
    CryptoOhlcvRecord,
    normalize_crypto_symbol,
    utc_midnight,
)
from yowayowa.domain import LicenseClass
from yowayowa.providers.base import ProviderDescriptor, enforce_provider_policy

COINGECKO_BASE_URL = "https://api.coingecko.com/api/v3"

_SOURCES_NOTE = (
    "CoinGecko public API daily OHLC (1d interval, USD); commercial aggregator "
    "data - NOT an official reference rate, personal-use classification."
)


class CoinGeckoTransportError(RuntimeError):
    """CoinGecko is unreachable (maps to HTTP 502)."""


class CoinGeckoOhlcProvider:
    """Daily crypto OHLC from the CoinGecko public API (personal mode only)."""

    descriptor = ProviderDescriptor(
        name="coingecko",
        license_class=LicenseClass.PERSONAL_ONLY,
        redistributable=False,
        description=(
            "CoinGecko public API daily OHLC; key-less commercial aggregator, "
            "personal-use classification (not an official reference rate)."
        ),
    )

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        enforce_provider_policy(
            self.descriptor,
            mode=settings.mode,
            allow_personal_in_public=settings.allow_personal_provider_in_public,
        )
        self.client = httpx.Client(
            base_url=COINGECKO_BASE_URL,
            timeout=settings.request_timeout_seconds,
            headers={"User-Agent": "Yowayowa-Investor/1.0 (+personal investment research)"},
        )
        ttl = max(300, settings.cache_ttl_seconds)
        self._history_cache: TTLCache[tuple[str, int], list[CryptoOhlcvPoint]] = TTLCache(
            maxsize=32, ttl=ttl
        )

    def ohlcv(self, symbol: str, *, days: int = 30) -> list[CryptoOhlcvRecord]:
        """Daily USD OHLC for one supported symbol, oldest first."""

        normalized = normalize_crypto_symbol(symbol)
        coin_id = COINGECKO_COIN_IDS[normalized]
        path = f"/coins/{coin_id}/ohlc"
        bars = self._fetch(normalized, path=path, days=days)
        source_url = f"{COINGECKO_BASE_URL}/coins/{coin_id}/ohlc"
        return [self._record(normalized, bar, source_url=source_url) for bar in bars]

    # ----------------------------------------------------------------- internal

    def _fetch(self, normalized: str, *, path: str, days: int) -> list[CryptoOhlcvPoint]:
        cache_key = (normalized, days)
        cached = self._history_cache.get(cache_key)
        if cached is not None:
            return cached
        try:
            response = self.client.get(path, params={"vs_currency": "usd", "days": str(days)})
        except httpx.HTTPError as exc:
            raise CoinGeckoTransportError(
                f"CoinGecko is unreachable: {type(exc).__name__}"
            ) from exc
        if response.status_code == 404:
            raise LookupError(f"CoinGecko has no OHLC data for {normalized!r}")
        if response.is_error:
            raise CoinGeckoTransportError(
                f"CoinGecko returned HTTP {response.status_code} for {normalized!r}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise CoinGeckoTransportError("CoinGecko returned invalid JSON") from exc
        bars = self._parse_payload(normalized, payload)
        self._history_cache[cache_key] = bars
        return bars

    def _parse_payload(self, normalized: str, payload: object) -> list[CryptoOhlcvPoint]:
        """Parse ``[[ts_ms, o, h, l, c], ...]``; skip malformed bars (no zero-fill)."""

        if not isinstance(payload, list) or not payload:
            raise LookupError(f"CoinGecko returned no OHLC payload for {normalized!r}")
        points: dict[datetime, CryptoOhlcvPoint] = {}
        for row in payload:
            if not isinstance(row, list) or len(row) < 5:
                continue
            try:
                as_of = utc_midnight(datetime.fromtimestamp(float(row[0]) / 1000.0, tz=UTC))
                o, h, low, c = (float(row[i]) for i in range(1, 5))
            except (TypeError, ValueError, OSError):
                continue
            if o == 0 and h == 0 and low == 0 and c == 0:
                continue  # all-zero bars are missing data, not a price
            points[as_of] = CryptoOhlcvPoint(as_of=as_of, open=o, high=h, low=low, close=c)
        if not points:
            raise LookupError(f"CoinGecko returned no usable OHLC bars for {normalized!r}")
        return [points[key] for key in sorted(points)]

    def _record(
        self, normalized: str, bar: CryptoOhlcvPoint, *, source_url: str
    ) -> CryptoOhlcvRecord:
        return CryptoOhlcvRecord(
            symbol=normalized,
            interval="1d",
            currency="USD",
            provider="coingecko",
            source_url=source_url,
            license_class=LicenseClass.PERSONAL_ONLY.value,
            retrieved_at=datetime.now(UTC),
            as_of=bar.as_of,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=None,
            quote_volume=None,
            notes=[_SOURCES_NOTE],
        )
