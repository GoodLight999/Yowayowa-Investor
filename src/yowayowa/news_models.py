from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from yowayowa.calendar_models import (
    TrackedEventSubtype,
    TrackedEventType,
    TrackedScope,
)
from yowayowa.domain import Provenance


class SavedNewsItem(BaseModel):
    id: str
    title: str
    publisher: str | None = None
    published_at: datetime | None = None
    url: str | None = None
    summary: str | None = None
    symbols: list[str] = Field(default_factory=list)


class SavedNewsFeed(BaseModel):
    scope: TrackedScope
    scope_id: int | None = None
    symbols: list[str]
    items: list[SavedNewsItem]
    unavailable_symbols: list[str] = Field(default_factory=list)
    provenance: Provenance


class EventSubscriptionCreate(BaseModel):
    scope: TrackedScope = "all"
    scope_id: int | None = Field(default=None, ge=1)
    event_types: list[TrackedEventType] = ["earnings", "dividend"]
    lead_days: int = Field(default=7, ge=0, le=30)


class EventSubscription(BaseModel):
    id: int
    scope: TrackedScope
    scope_id: int | None = None
    event_types: list[TrackedEventType]
    lead_days: int
    enabled: bool
    last_checked_at: datetime | None = None
    created_at: datetime


class EventInboxItem(BaseModel):
    id: int
    subscription_id: int
    event_type: TrackedEventType
    subtype: TrackedEventSubtype
    symbol: str
    title: str
    starts_at: datetime
    ends_at: datetime | None = None
    created_at: datetime
    acknowledged_at: datetime | None = None


class EventSubscriptionEvaluation(BaseModel):
    subscriptions: list[EventSubscription]
    inbox: list[EventInboxItem]
    unavailable_symbols: list[str] = Field(default_factory=list)
    provenance: Provenance
    evaluated_at: datetime
