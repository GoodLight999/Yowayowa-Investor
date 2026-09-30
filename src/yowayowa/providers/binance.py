"""Binance public klines provider (P4-E phase 1, personal-only).

Binance's public ``/api/v3/klines`` endpoint is key-less but the data comes
from a commercial exchange: the CTO-fixed licensing decision is
``LicenseClass.PERSONAL_ONLY`` — **not** an official reference rate, so the
provider is never public-mode safe. ``enforce_provider_policy`` refuses it in
public mode at construction.

Structure traces ``providers/frankfurter.py``: TTLCache + one shared
``httpx.Client`` + ``ProviderDescriptor`` + ``enforce_provider_policy``.
Missing data is never zero-filled: klines with all-zero OHLC values are
treated as missing bars and skipped.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
from cachetools import TTLCache

from yowayowa.config import Settings
from yowayowa.crypto_models import (
    BINANCE_SYMBOLS,
    CryptoOhlcvPoint,
    CryptoOhlcvRecord,
    normalize_crypto_symbol,
    utc_midnight,
)
from yowayowa.domain import LicenseClass
from yowayowa.providers.base import ProviderDescriptor, enforce_provider_policy

BINANCE_BASE_URL = "https://api.binance.com"

_SOURCES_NOTE = (
    "Binance public API daily klines (1d interval, USDT quote); commercial "
    "exchange data - NOT an official reference rate, personal-use classification."
)
_DAY_MS = 86_400_000
_MAX_KLINES_PER_REQUEST = 1000


class BinanceTransportError(RuntimeError):
    """Binance is unreachable (maps to HTTP 502)."""


class BinanceKlinesProvider:
    """Daily crypto klines from the Binance public API (personal mode only)."""

    descriptor = ProviderDescriptor(
        name="binance",
        license_class=LicenseClass.PERSONAL_ONLY,
        redistributable=False,
        description=(
            "Binance public API daily klines; key-less commercial exchange, "
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
            base_url=BINANCE_BASE_URL,
            timeout=settings.request_timeout_seconds,
            headers={"User-Agent": "Yowayowa-Investor/1.0 (+personal investment research)"},
        )
        ttl = max(300, settings.cache_ttl_seconds)
        self._history_cache: TTLCache[tuple[str, int], list[CryptoOhlcvPoint]] = TTLCache(
            maxsize=32, ttl=ttl
        )

    def ohlcv(self, symbol: str, *, days: int = 30) -> list[CryptoOhlcvRecord]:
        """Daily USDT klines for one supported symbol, oldest first.

        Binance caps a single ``klines`` call at 1000 bars. Older history is
        paginated by open time, and the requested window ends at yesterday's
        UTC close so a still-forming UTC day is never persisted.
        """

        normalized = normalize_crypto_symbol(symbol)
        market_symbol = BINANCE_SYMBOLS[normalized]
        bars = self._fetch(normalized, market_symbol, days=days)
        source_url = f"{BINANCE_BASE_URL}/api/v3/klines?symbol={market_symbol}&interval=1d"
        return [self._record(normalized, bar, source_url=source_url) for bar in bars]

    # ----------------------------------------------------------------- internal

    def _fetch(self, normalized: str, market_symbol: str, *, days: int) -> list[CryptoOhlcvPoint]:
        days = min(3650, max(1, int(days)))
        cache_key = (normalized, days)
        cached = self._history_cache.get(cache_key)
        if cached is not None:
            return cached
        end = datetime.now(UTC).date() - timedelta(days=1)
        start = end - timedelta(days=days - 1)
        cursor_ms = int(datetime.combine(start, datetime.min.time(), tzinfo=UTC).timestamp() * 1000)
        end_ms = (
            int(
                datetime.combine(
                    end + timedelta(days=1), datetime.min.time(), tzinfo=UTC
                ).timestamp()
                * 1000
            )
            - 1
        )
        points: dict[datetime, CryptoOhlcvPoint] = {}
        while cursor_ms <= end_ms:
            try:
                response = self.client.get(
                    "/api/v3/klines",
                    params={
                        "symbol": market_symbol,
                        "interval": "1d",
                        "startTime": str(cursor_ms),
                        "endTime": str(end_ms),
                        "limit": str(_MAX_KLINES_PER_REQUEST),
                    },
                )
            except httpx.HTTPError as exc:
                raise BinanceTransportError(
                    f"Binance is unreachable: {type(exc).__name__}"
                ) from exc
            if response.status_code == 404 or response.status_code == 400:
                # Binance signals unknown symbol / bad params as 4xx: treated as
                # missing data (HTTP 404 upstream), not a transport failure.
                raise LookupError(
                    f"Binance has no klines data for {normalized!r} ({market_symbol})"
                )
            if response.is_error:
                raise BinanceTransportError(
                    f"Binance returned HTTP {response.status_code} for {normalized!r}"
                )
            try:
                payload = response.json()
            except ValueError as exc:
                raise BinanceTransportError("Binance returned invalid JSON") from exc
            if not isinstance(payload, list) or not payload:
                if not points:
                    raise LookupError(f"Binance returned no klines payload for {normalized!r}")
                break
            page = self._parse_payload(normalized, payload, drop_last=False)
            if not page:
                raise LookupError(f"Binance returned no usable klines for {normalized!r}")
            for point in page:
                point_ms = int(point.as_of.timestamp() * 1000)
                if cursor_ms <= point_ms < end_ms and start <= point.as_of.date() <= end:
                    points[point.as_of] = point
            last_open_ms = int(page[-1].as_of.timestamp() * 1000)
            next_cursor_ms = last_open_ms + _DAY_MS
            if next_cursor_ms <= cursor_ms or len(payload) < _MAX_KLINES_PER_REQUEST:
                break
            cursor_ms = next_cursor_ms
        bars = [points[key] for key in sorted(points)]
        if not bars:
            raise LookupError(f"Binance returned no usable klines for {normalized!r}")
        self._history_cache[cache_key] = bars
        return bars

    def _parse_payload(
        self, normalized: str, payload: object, *, drop_last: bool = True
    ) -> list[CryptoOhlcvPoint]:
        """Parse klines ``[open_ts, o, h, l, c, volume, close_ts, quote_vol, ...]``.

        The last row is the currently-forming bar; it is dropped. Rows with
        all-zero OHLC are treated as missing data and skipped (no zero-fill).
        """

        if not isinstance(payload, list) or not payload:
            raise LookupError(f"Binance returned no klines payload for {normalized!r}")
        points: dict[datetime, CryptoOhlcvPoint] = {}
        rows = payload[:-1] if drop_last and len(payload) > 1 else payload
        for row in rows:
            if not isinstance(row, list) or len(row) < 5:
                continue
            try:
                as_of = utc_midnight(datetime.fromtimestamp(float(row[0]) / 1000.0, tz=UTC))
                o, h, low, c = (float(row[i]) for i in range(1, 5))
                volume = float(row[5]) if len(row) > 5 else None
                quote_volume = float(row[7]) if len(row) > 7 else None
            except (TypeError, ValueError, OSError):
                continue
            if o == 0 and h == 0 and low == 0 and c == 0:
                continue  # all-zero bars are missing data, not a price
            points[as_of] = CryptoOhlcvPoint(
                as_of=as_of,
                open=o,
                high=h,
                low=low,
                close=c,
                volume=volume,
                quote_volume=quote_volume,
            )
        if not points:
            raise LookupError(f"Binance returned no usable klines for {normalized!r}")
        return [points[key] for key in sorted(points)]

    def _record(
        self, normalized: str, bar: CryptoOhlcvPoint, *, source_url: str
    ) -> CryptoOhlcvRecord:
        return CryptoOhlcvRecord(
            symbol=normalized,
            interval="1d",
            currency="USDT",
            provider="binance",
            source_url=source_url,
            license_class=LicenseClass.PERSONAL_ONLY.value,
            retrieved_at=datetime.now(UTC),
            as_of=bar.as_of,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=bar.volume,
            quote_volume=bar.quote_volume,
            notes=[_SOURCES_NOTE],
        )
