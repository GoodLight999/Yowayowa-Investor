"""Alpaca Market Data provider: US stock daily bars (P4-F, personal-only).

Alpaca is a commercial broker/data-vendor: the CTO-fixed licensing decision
is ``LicenseClass.PERSONAL_ONLY`` — SIP feed history is licensed for personal
use within Alpaca's terms and is **not** an official reference rate, so the
provider is never public-mode safe. ``enforce_provider_policy`` refuses it in
public mode at construction.

Structure traces ``providers/binance.py``: TTLCache + one shared
``httpx.Client`` + ``ProviderDescriptor`` + ``enforce_provider_policy``.
Only the data API (``data.alpaca.markets``) is contacted — never the trading
API. Missing data is never zero-filled: symbols absent from the payload,
malformed bars, and all-zero OHLC rows are skipped (fail-closed).
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import httpx
from cachetools import TTLCache

from yowayowa.config import Settings
from yowayowa.domain import LicenseClass
from yowayowa.providers.base import ProviderDescriptor, enforce_provider_policy
from yowayowa.stock_models import (
    StockOhlcvPoint,
    StockOhlcvRecord,
    normalize_stock_symbol,
    stock_bar_timestamp,
)

ALPACA_STOCKS_BASE_URL = "https://data.alpaca.markets"

_SOURCES_NOTE = (
    "Alpaca Market Data daily bars (1Day, SIP feed); commercial broker/data-vendor "
    "data - NOT an official reference rate, personal-use classification. SIP history "
    "is used within the personal scope of Alpaca's data terms (15-minute delay for "
    "the free plan)."
)

# Safety margin for Alpaca's 200 requests/min free-plan limit: this job makes a
# handful of requests per day, so a paced sleep between consecutive symbol
# calls keeps even bursty manual runs far from the ceiling.
_REQUEST_PACING_SECONDS = 0.35


class AlpacaTransportError(RuntimeError):
    """Alpaca is unreachable or answered an unrecoverable status (maps to 502)."""


class AlpacaMarketDataProvider:
    """Daily US stock bars from the Alpaca Market Data API (personal mode only)."""

    descriptor = ProviderDescriptor(
        name="alpaca",
        license_class=LicenseClass.PERSONAL_ONLY,
        redistributable=False,
        description=(
            "Alpaca Market Data API daily stock bars (SIP feed); commercial broker/"
            "data-vendor, personal-use classification (not an official reference rate)."
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
            base_url=ALPACA_STOCKS_BASE_URL,
            timeout=settings.request_timeout_seconds,
            headers={
                "User-Agent": "Yowayowa-Investor/1.0 (+personal investment research)",
                "APCA-API-KEY-ID": settings.alpaca_key_id or "",
                "APCA-API-SECRET-KEY": settings.alpaca_secret_key or "",
            },
        )
        ttl = max(300, settings.cache_ttl_seconds)
        self._history_cache: TTLCache[tuple[str, int], list[StockOhlcvPoint]] = TTLCache(
            maxsize=32, ttl=ttl
        )
        self._last_request_at: float | None = None

    def ohlcv(self, symbol: str, *, days: int = 30) -> list[StockOhlcvRecord]:
        """Daily SIP bars for one ticker, oldest first, finalized bars only.

        The request window ends **yesterday** (UTC): the free plan forbids
        querying SIP data newer than ~15 minutes, and a request whose window
        includes the current calendar day is rejected outright with 403
        ("subscription does not permit querying recent SIP data" — live
        probe 2026-09-24). Ending at yesterday keeps the fetch allowed while
        guaranteeing every returned bar is a finalized session.
        """

        normalized = normalize_stock_symbol(symbol)
        days = max(1, int(days))
        start = (datetime.now(UTC) - timedelta(days=days + 1)).date().isoformat()
        end = (datetime.now(UTC) - timedelta(days=1)).date().isoformat()
        bars = self._fetch(normalized, start=start, end=end)
        source_url = (
            f"{ALPACA_STOCKS_BASE_URL}/v2/stocks/bars?symbols={normalized}&timeframe=1Day&feed=sip"
        )
        return [self._record(normalized, bar, source_url=source_url) for bar in bars]

    # ----------------------------------------------------------------- internal

    def _pace(self) -> None:
        """Sleep so consecutive Alpaca calls stay clearly under 200 req/min."""

        now = time.monotonic()
        if self._last_request_at is not None:
            elapsed = now - self._last_request_at
            if elapsed < _REQUEST_PACING_SECONDS:
                time.sleep(_REQUEST_PACING_SECONDS - elapsed)
        self._last_request_at = time.monotonic()

    def _bars_request(
        self, symbol: str, *, start: str, end: str, page_token: str | None
    ) -> httpx.Response:
        self._pace()
        params: dict[str, str] = {
            "symbols": symbol,
            "timeframe": "1Day",
            "feed": "sip",
            "start": start,
            "end": end,
            "limit": "10000",
        }
        if page_token is not None:
            params["page_token"] = page_token
        return self.client.get("/v2/stocks/bars", params=params)

    def _fetch(self, normalized: str, *, start: str, end: str) -> list[StockOhlcvPoint]:
        cache_key = (normalized, hash((start, end)))
        cached = self._history_cache.get(cache_key)
        if cached is not None:
            return cached

        bars: dict[datetime, StockOhlcvPoint] = {}
        page_token: str | None = None
        saw_response = False
        while True:
            try:
                response = self._bars_request(
                    normalized, start=start, end=end, page_token=page_token
                )
            except httpx.HTTPError as exc:
                raise AlpacaTransportError(f"Alpaca is unreachable: {type(exc).__name__}") from exc
            if response.status_code == 422 or response.status_code == 404:
                # Unknown symbol / bad params: missing data, not a transport failure.
                raise LookupError(
                    f"Alpaca has no bars data for {normalized!r} (HTTP {response.status_code})"
                )
            if response.is_error:
                raise AlpacaTransportError(
                    f"Alpaca returned HTTP {response.status_code} for {normalized!r}"
                )
            try:
                payload = response.json()
            except ValueError as exc:
                raise AlpacaTransportError("Alpaca returned invalid JSON") from exc
            saw_response = True
            page_token = self._absorb_payload(normalized, payload, bars)
            if page_token is None:
                break
        if not saw_response:
            raise LookupError(f"Alpaca returned no bars payload for {normalized!r}")
        points = [bars[key] for key in sorted(bars)]
        if not points:
            raise LookupError(f"Alpaca returned no usable bars for {normalized!r}")
        self._history_cache[cache_key] = points
        return points

    def _absorb_payload(
        self, normalized: str, payload: object, bars: dict[datetime, StockOhlcvPoint]
    ) -> str | None:
        """Parse ``{bars: {SYM: [...]}, next_page_token?}``; return the next token."""

        if not isinstance(payload, dict):
            raise LookupError(f"Alpaca returned no bars payload for {normalized!r}")
        symbol_bars = payload.get("bars")
        if isinstance(symbol_bars, dict):
            rows = symbol_bars.get(normalized, [])
        elif isinstance(symbol_bars, list):
            rows = symbol_bars
        else:
            rows = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                as_of = stock_bar_timestamp(row.get("t"))
                o, h, low, c = (float(row[key]) for key in ("o", "h", "l", "c"))
                volume = float(row["v"]) if row.get("v") is not None else None
                vwap = float(row["vw"]) if row.get("vw") is not None else None
                trade_count = int(row["n"]) if row.get("n") is not None else None
            except (KeyError, TypeError, ValueError):
                continue  # malformed rows are missing data, never repaired
            if o == 0 and h == 0 and low == 0 and c == 0:
                continue  # all-zero bars are missing data, not a price
            bars[as_of] = StockOhlcvPoint(
                as_of=as_of,
                open=o,
                high=h,
                low=low,
                close=c,
                volume=volume,
                vwap=vwap,
                trade_count=trade_count,
            )
        next_token = payload.get("next_page_token")
        return str(next_token) if next_token else None

    def _record(
        self, normalized: str, bar: StockOhlcvPoint, *, source_url: str
    ) -> StockOhlcvRecord:
        return StockOhlcvRecord(
            symbol=normalized,
            interval="1d",
            currency="USD",
            provider="alpaca",
            source_url=source_url,
            license_class=LicenseClass.PERSONAL_ONLY.value,
            retrieved_at=datetime.now(UTC),
            as_of=bar.as_of,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=bar.volume,
            vwap=bar.vwap,
            trade_count=bar.trade_count,
            notes=[_SOURCES_NOTE],
        )
