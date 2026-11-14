from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, date, datetime
from threading import Lock
from typing import Any

import pandas as pd
import yfinance as yf
from cachetools import TTLCache

from yowayowa.calendar_models import (
    TrackedCalendarEvent,
    TrackedEventSubtype,
    TrackedEventType,
)
from yowayowa.config import Settings
from yowayowa.domain import LicenseClass, Provenance
from yowayowa.providers.base import ProviderDescriptor, enforce_provider_policy
from yowayowa.symbols import normalize_symbol


class YahooTrackedCalendarProvider:
    descriptor = ProviderDescriptor(
        name="yahoo-tracked-calendar",
        license_class=LicenseClass.PERSONAL_ONLY,
        redistributable=False,
        description="Yahoo Finance ticker calendars via yfinance; personal use only.",
    )

    def __init__(self, settings: Settings) -> None:
        enforce_provider_policy(
            self.descriptor,
            mode=settings.mode,
            allow_personal_in_public=settings.allow_personal_provider_in_public,
        )
        self.settings = settings
        ttl = max(60, settings.cache_ttl_seconds)
        self._calendar_cache: TTLCache[str, dict[str, Any]] = TTLCache(maxsize=512, ttl=ttl)
        self._cache_lock = Lock()

    def events(
        self,
        symbols: list[str],
        start: date,
        end: date,
        event_types: list[TrackedEventType] | None = None,
    ) -> tuple[list[TrackedCalendarEvent], list[str], Provenance]:
        normalized = list(dict.fromkeys(normalize_symbol(symbol) for symbol in symbols))
        if len(normalized) > 100:
            raise ValueError("Tracked calendar supports at most 100 symbols per request")
        requested: set[TrackedEventType] = (
            {"earnings", "dividend"} if event_types is None else set(event_types)
        )
        events: list[TrackedCalendarEvent] = []
        unavailable: list[str] = []

        if normalized:
            workers = min(6, len(normalized))
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(self._calendar_for_symbol, symbol): symbol
                    for symbol in normalized
                }
                for future in as_completed(futures):
                    symbol = futures[future]
                    try:
                        raw = future.result()
                    except Exception:
                        unavailable.append(symbol)
                        continue
                    events.extend(self._normalize_events(symbol, raw, start, end, requested))

        unique: dict[tuple[str, str, str, datetime, datetime | None], TrackedCalendarEvent] = {}
        for event in events:
            key = (
                event.symbol,
                event.event_type,
                event.subtype,
                event.starts_at,
                event.ends_at,
            )
            unique[key] = event
        ordered = sorted(
            unique.values(),
            key=lambda event: (event.starts_at, event.symbol, event.event_type, event.subtype),
        )
        now = datetime.now(UTC)
        provenance = Provenance(
            provider="yahoo/yfinance",
            source="Yahoo Finance ticker calendars",
            source_url="https://finance.yahoo.com/",
            license_class=LicenseClass.PERSONAL_ONLY,
            retrieved_at=now,
            as_of=now,
            notes=[
                "Upcoming tracked-symbol events are normalized from Yahoo ticker calendars.",
                "Ticker calendars are cached briefly to avoid repeated per-symbol provider calls.",
                "Unavailable symbols are reported explicitly instead of being silently omitted.",
            ],
        )
        return ordered, sorted(set(unavailable)), provenance

    def _calendar_for_symbol(self, symbol: str) -> dict[str, Any]:
        with self._cache_lock:
            cached = self._calendar_cache.get(symbol)
            if cached is not None:
                return cached
        raw = yf.Ticker(symbol).calendar or {}
        if not isinstance(raw, dict):
            raise LookupError(f"Ticker calendar unavailable for {symbol}")
        normalized = {str(key): value for key, value in raw.items()}
        with self._cache_lock:
            self._calendar_cache[symbol] = normalized
        return normalized

    @classmethod
    def _normalize_events(
        cls,
        symbol: str,
        raw: dict[str, Any],
        start: date,
        end: date,
        requested: set[TrackedEventType],
    ) -> list[TrackedCalendarEvent]:
        events: list[TrackedCalendarEvent] = []
        for key, value in raw.items():
            classification = cls._classify_key(key)
            if classification is None:
                continue
            event_type, subtype, title = classification
            if event_type not in requested:
                continue
            parsed = sorted(
                {
                    timestamp
                    for item in cls._values(value)
                    if (timestamp := cls._datetime(item)) is not None
                }
            )
            if not parsed:
                continue
            starts_at = parsed[0]
            ends_at = parsed[-1] if event_type == "earnings" and len(parsed) > 1 else None
            range_end = ends_at or starts_at
            if starts_at.date() > end or range_end.date() < start:
                continue
            events.append(
                TrackedCalendarEvent(
                    event_type=event_type,
                    subtype=subtype,
                    starts_at=starts_at,
                    ends_at=ends_at,
                    title=title,
                    symbol=symbol,
                    details={"calendar_key": key},
                )
            )
        return events

    @staticmethod
    def _classify_key(
        key: str,
    ) -> tuple[TrackedEventType, TrackedEventSubtype, str] | None:
        normalized = " ".join(key.casefold().replace("_", " ").replace("-", " ").split())
        if "earnings date" in normalized:
            return "earnings", "earnings", "Earnings date"
        if "ex dividend date" in normalized:
            return "dividend", "ex_dividend", "Ex-dividend date"
        if "dividend date" in normalized:
            return "dividend", "dividend_payment", "Dividend payment date"
        if "date" in normalized:
            return "ticker", "other", key
        return None

    @staticmethod
    def _values(value: Any) -> list[Any]:
        if isinstance(value, (list, tuple, set)):
            return list(value)
        return [value]

    @staticmethod
    def _datetime(value: Any) -> datetime | None:
        if value is None or value == "":
            return None
        try:
            timestamp = pd.Timestamp(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if pd.isna(timestamp):
            return None
        if timestamp.tzinfo is None:
            timestamp = timestamp.tz_localize("UTC")
        else:
            timestamp = timestamp.tz_convert("UTC")
        return timestamp.to_pydatetime()
