from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from yowayowa.domain import Provenance

TrackedScope = Literal["all", "watchlist", "portfolio"]
TrackedEventType = Literal["earnings", "dividend", "ticker"]
TrackedEventSubtype = Literal["earnings", "ex_dividend", "dividend_payment", "other"]


class TrackedCalendarEvent(BaseModel):
    event_type: TrackedEventType
    subtype: TrackedEventSubtype
    starts_at: datetime
    ends_at: datetime | None = None
    title: str
    symbol: str
    details: dict[str, Any] = Field(default_factory=dict)


class TrackedEventCalendar(BaseModel):
    start: date
    end: date
    scope: TrackedScope
    scope_id: int | None = None
    symbols: list[str]
    events: list[TrackedCalendarEvent]
    unavailable_symbols: list[str] = Field(default_factory=list)
    provenance: Provenance
