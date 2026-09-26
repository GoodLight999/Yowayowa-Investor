from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, Literal

import pandas as pd
import yfinance as yf

from yowayowa.config import Settings
from yowayowa.domain import (
    CalendarEvent,
    EventCalendar,
    LicenseClass,
    NewsFeed,
    NewsItem,
    Provenance,
)
from yowayowa.providers.base import ProviderDescriptor, enforce_provider_policy
from yowayowa.symbols import normalize_symbol

EventType = Literal["earnings", "economic", "ipo", "split"]


class YahooResearchProvider:
    descriptor = ProviderDescriptor(
        name="yahoo-research",
        license_class=LicenseClass.PERSONAL_ONLY,
        redistributable=False,
        description="Yahoo Finance search/news/calendar data via yfinance; personal use only.",
    )

    def __init__(self, settings: Settings) -> None:
        enforce_provider_policy(
            self.descriptor,
            mode=settings.mode,
            allow_personal_in_public=settings.allow_personal_provider_in_public,
        )
        self.settings = settings

    def news(self, query: str, limit: int = 12) -> NewsFeed:
        search = yf.Search(
            query,
            max_results=0,
            news_count=limit,
            lists_count=0,
            include_cb=False,
            include_nav_links=False,
            include_research=False,
            include_cultural_assets=False,
            timeout=self.settings.request_timeout_seconds,
        )
        items: list[NewsItem] = []
        for offset, raw in enumerate(search.news[:limit]):
            if not isinstance(raw, dict):
                continue
            content_raw = raw.get("content")
            content: dict[str, Any]
            if isinstance(content_raw, dict):
                content = {str(key): value for key, value in content_raw.items()}
            else:
                content = {str(key): value for key, value in raw.items()}
            title = str(content.get("title") or "").strip()
            if not title:
                continue
            provider = content.get("provider")
            publisher = (
                str(provider.get("displayName"))
                if isinstance(provider, dict) and provider.get("displayName")
                else self._string(content.get("publisher"))
            )
            published = self._datetime(
                content.get("pubDate")
                or content.get("providerPublishTime")
                or content.get("published_at")
            )
            url = self._news_url(content)
            item_id = str(raw.get("id") or content.get("id") or url or f"{query}-{offset}")
            items.append(
                NewsItem(
                    id=item_id,
                    title=title,
                    publisher=publisher,
                    published_at=published,
                    url=url,
                    summary=self._string(content.get("summary") or content.get("description")),
                    symbol=normalize_symbol(query) if self._looks_like_symbol(query) else None,
                )
            )
        now = datetime.now(UTC)
        return NewsFeed(
            query=query,
            items=items,
            provenance=Provenance(
                provider="yahoo/yfinance",
                source="Yahoo Finance",
                source_url="https://finance.yahoo.com/news/",
                license_class=LicenseClass.PERSONAL_ONLY,
                retrieved_at=now,
                as_of=max((item.published_at for item in items if item.published_at), default=now),
                notes=["News search is provided for personal research use."],
            ),
        )

    def calendar(
        self,
        start: date,
        end: date,
        event_types: list[EventType] | None = None,
        symbol: str | None = None,
        limit: int = 100,
    ) -> EventCalendar:
        requested = event_types or ["earnings", "economic", "ipo", "split"]
        calendars = yf.Calendars(start=start, end=end)
        events: list[CalendarEvent] = []
        loaders = {
            "earnings": lambda: calendars.get_earnings_calendar(
                filter_most_active=False, limit=limit
            ),
            "economic": lambda: calendars.get_economic_events_calendar(limit=limit),
            "ipo": lambda: calendars.get_ipo_info_calendar(limit=limit),
            "split": lambda: calendars.get_splits_calendar(limit=limit),
        }
        for event_type in requested:
            frame = loaders[event_type]()
            events.extend(self._frame_events(frame, event_type))
        if symbol:
            events.extend(self._ticker_events(symbol, start, end))
        events.sort(key=self._event_sort_key)
        now = datetime.now(UTC)
        return EventCalendar(
            start=start,
            end=end,
            events=events,
            provenance=Provenance(
                provider="yahoo/yfinance",
                source="Yahoo Finance",
                source_url="https://finance.yahoo.com/calendar/",
                license_class=LicenseClass.PERSONAL_ONLY,
                retrieved_at=now,
                as_of=now,
                notes=[
                    "Calendar event fields are normalized from Yahoo Finance calendar tables.",
                    "Ticker calendar entries include earnings/dividend-related dates when Yahoo "
                    "provides them.",
                ],
            ),
        )

    def _ticker_events(self, symbol: str, start: date, end: date) -> list[CalendarEvent]:
        normalized = normalize_symbol(symbol)
        raw = yf.Ticker(normalized).calendar or {}
        events: list[CalendarEvent] = []
        for key, value in raw.items():
            values = value if isinstance(value, list) else [value]
            for item in values:
                event_date = self._datetime(item)
                if event_date is None or not (start <= event_date.date() <= end):
                    continue
                events.append(
                    CalendarEvent(
                        event_type="ticker",
                        starts_at=event_date,
                        title=str(key),
                        symbol=normalized,
                        details={"value": self._json_value(item)},
                    )
                )
        return events

    @classmethod
    def _frame_events(
        cls,
        frame: pd.DataFrame | None,
        event_type: EventType,
    ) -> list[CalendarEvent]:
        if frame is None or frame.empty:
            return []
        reset = frame.reset_index()
        events: list[CalendarEvent] = []
        for _, row in reset.iterrows():
            details = {
                str(key): cls._json_value(value) for key, value in row.items() if pd.notna(value)
            }
            starts_at = cls._row_datetime(row)
            symbol = cls._first_string(row, "Symbol", "Ticker", "symbol")
            label = cls._first_string(
                row,
                "Company",
                "Company Name",
                "Event",
                "Event Name",
                "Country",
                "Symbol",
                "Ticker",
            )
            title = label or event_type.title()
            events.append(
                CalendarEvent(
                    event_type=event_type,
                    starts_at=starts_at,
                    title=title,
                    symbol=symbol.upper() if symbol else None,
                    details=details,
                )
            )
        return events

    @classmethod
    def _row_datetime(cls, row: pd.Series) -> datetime | date | None:
        for key in row.index:
            name = str(key).casefold()
            if any(token in name for token in ("date", "time", "start")) or name == "index":
                parsed = cls._datetime(row[key])
                if parsed is not None:
                    return parsed
        return None

    @staticmethod
    def _event_sort_key(event: CalendarEvent) -> tuple[datetime, str, str]:
        value = event.starts_at
        if isinstance(value, datetime):
            timestamp = value if value.tzinfo else value.replace(tzinfo=UTC)
        elif isinstance(value, date):
            timestamp = datetime.combine(value, datetime.min.time(), tzinfo=UTC)
        else:
            timestamp = datetime.max.replace(tzinfo=UTC)
        return timestamp, event.event_type, event.title

    @staticmethod
    def _first_string(row: pd.Series, *keys: str) -> str | None:
        by_name = {str(key).casefold(): row[key] for key in row.index}
        for key in keys:
            value = by_name.get(key.casefold())
            text = YahooResearchProvider._string(value)
            if text:
                return text
        return None

    @staticmethod
    def _news_url(content: dict[str, Any]) -> str | None:
        for key in ("clickThroughUrl", "canonicalUrl"):
            value = content.get(key)
            if isinstance(value, dict) and value.get("url"):
                return str(value["url"])
        return YahooResearchProvider._string(content.get("link") or content.get("url"))

    @staticmethod
    def _string(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _datetime(value: Any) -> datetime | None:
        if value is None or value == "":
            return None
        try:
            if isinstance(value, (int, float)):
                return datetime.fromtimestamp(float(value), tz=UTC)
            timestamp = pd.Timestamp(value)
            if pd.isna(timestamp):
                return None
            if timestamp.tzinfo is None:
                timestamp = timestamp.tz_localize("UTC")
            else:
                timestamp = timestamp.tz_convert("UTC")
            return timestamp.to_pydatetime()
        except (TypeError, ValueError, OverflowError):
            return None

    @staticmethod
    def _json_value(value: Any) -> Any:
        if isinstance(value, pd.Timestamp):
            return value.isoformat()
        if hasattr(value, "item"):
            try:
                return value.item()
            except (ValueError, AttributeError):
                pass
        return value

    @staticmethod
    def _looks_like_symbol(value: str) -> bool:
        stripped = value.strip()
        return bool(stripped) and " " not in stripped and len(stripped) <= 32
