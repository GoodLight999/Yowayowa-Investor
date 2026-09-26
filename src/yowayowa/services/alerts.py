from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Protocol, cast

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from yowayowa.calendar_models import (
    TrackedCalendarEvent,
    TrackedEventSubtype,
    TrackedEventType,
    TrackedScope,
)
from yowayowa.db import EventInboxRecord, EventSubscriptionRecord, PriceAlertRecord, utcnow
from yowayowa.domain import (
    AlertEvaluation,
    AlertOperator,
    LicenseClass,
    PriceAlert,
    PriceAlertCreate,
    Provenance,
)
from yowayowa.news_models import (
    EventInboxItem,
    EventSubscription,
    EventSubscriptionCreate,
    EventSubscriptionEvaluation,
)
from yowayowa.providers.base import MarketDataProvider
from yowayowa.services.calendar import resolve_tracked_symbols
from yowayowa.symbols import normalize_symbol


class TrackedEventProvider(Protocol):
    def events(
        self,
        symbols: list[str],
        start: date,
        end: date,
        event_types: list[TrackedEventType] | None = None,
    ) -> tuple[list[TrackedCalendarEvent], list[str], Provenance]: ...


def _to_model(row: PriceAlertRecord) -> PriceAlert:
    return PriceAlert(
        id=row.id,
        symbol=row.symbol,
        operator=AlertOperator(row.operator),
        target=row.target,
        enabled=row.enabled,
        triggered_at=row.triggered_at,
        last_price=row.last_price,
        last_checked_at=row.last_checked_at,
        created_at=row.created_at,
    )


def list_alerts(session: Session) -> list[PriceAlert]:
    rows = session.scalars(
        select(PriceAlertRecord).order_by(
            PriceAlertRecord.enabled.desc(),
            PriceAlertRecord.id.desc(),
        )
    ).all()
    return [_to_model(row) for row in rows]


def create_alert(session: Session, payload: PriceAlertCreate) -> PriceAlert:
    row = PriceAlertRecord(
        symbol=normalize_symbol(payload.symbol),
        operator=payload.operator.value,
        target=payload.target,
        enabled=True,
        created_at=utcnow(),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return _to_model(row)


def delete_alert(session: Session, alert_id: int) -> None:
    row = session.get(PriceAlertRecord, alert_id)
    if row is None:
        raise LookupError(f"Price alert {alert_id} not found")
    session.delete(row)
    session.commit()


def _triggered(operator: AlertOperator, price: Decimal, target: Decimal) -> bool:
    if operator == AlertOperator.ABOVE:
        return price >= target
    return price <= target


def evaluate_alerts(session: Session, provider: MarketDataProvider) -> AlertEvaluation:
    rows = session.scalars(
        select(PriceAlertRecord)
        .where(PriceAlertRecord.enabled.is_(True))
        .order_by(PriceAlertRecord.id)
    ).all()
    checked_at = datetime.now(UTC)
    symbols = list(dict.fromkeys(row.symbol for row in rows))
    if not symbols:
        return AlertEvaluation(
            alerts=list_alerts(session),
            provenance=Provenance(
                provider="yahoo/yfinance",
                source="Yahoo Finance",
                license_class=LicenseClass.PERSONAL_ONLY,
                retrieved_at=checked_at,
                as_of=checked_at,
                notes=["No enabled alerts; no market-data request was made."],
            ),
            evaluated_at=checked_at,
        )

    batch = provider.quotes(symbols)
    for row in rows:
        quote = batch.quotes.get(row.symbol)
        if quote is None:
            continue
        price = Decimal(str(quote.price))
        row.last_price = price
        row.last_checked_at = checked_at
        if _triggered(AlertOperator(row.operator), price, row.target):
            row.triggered_at = checked_at
            row.enabled = False
    session.commit()
    return AlertEvaluation(
        alerts=list_alerts(session),
        provenance=batch.provenance,
        evaluated_at=checked_at,
    )


def _event_types(row: EventSubscriptionRecord) -> list[TrackedEventType]:
    return [cast(TrackedEventType, value) for value in row.event_types.split(",") if value]


def _subscription_model(row: EventSubscriptionRecord) -> EventSubscription:
    return EventSubscription(
        id=row.id,
        scope=cast(TrackedScope, row.scope),
        scope_id=row.scope_id,
        event_types=_event_types(row),
        lead_days=row.lead_days,
        enabled=row.enabled,
        last_checked_at=row.last_checked_at,
        created_at=row.created_at,
    )


def _inbox_model(row: EventInboxRecord) -> EventInboxItem:
    return EventInboxItem(
        id=row.id,
        subscription_id=row.subscription_id,
        event_type=cast(TrackedEventType, row.event_type),
        subtype=cast(TrackedEventSubtype, row.subtype),
        symbol=row.symbol,
        title=row.title,
        starts_at=row.starts_at,
        ends_at=row.ends_at,
        created_at=row.created_at,
        acknowledged_at=row.acknowledged_at,
    )


def list_event_subscriptions(session: Session) -> list[EventSubscription]:
    rows = session.scalars(
        select(EventSubscriptionRecord).order_by(
            EventSubscriptionRecord.enabled.desc(),
            EventSubscriptionRecord.id.desc(),
        )
    ).all()
    return [_subscription_model(row) for row in rows]


def create_event_subscription(
    session: Session,
    payload: EventSubscriptionCreate,
) -> EventSubscription:
    if payload.scope == "all" and payload.scope_id is not None:
        raise ValueError("scope_id is not valid for all scope")
    if payload.scope != "all" and payload.scope_id is None:
        raise ValueError(f"scope_id is required for {payload.scope} scope")
    if "ticker" in payload.event_types:
        raise ValueError("Event subscriptions support earnings and dividend event types")
    resolve_tracked_symbols(session, payload.scope, payload.scope_id)
    event_types = list(dict.fromkeys(payload.event_types))
    row = EventSubscriptionRecord(
        scope=payload.scope,
        scope_id=payload.scope_id,
        event_types=",".join(event_types),
        lead_days=payload.lead_days,
        enabled=True,
        created_at=utcnow(),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return _subscription_model(row)


def delete_event_subscription(session: Session, subscription_id: int) -> None:
    row = session.get(EventSubscriptionRecord, subscription_id)
    if row is None:
        raise LookupError(f"Event subscription {subscription_id} not found")
    session.execute(
        delete(EventInboxRecord).where(EventInboxRecord.subscription_id == subscription_id)
    )
    session.delete(row)
    session.commit()


def list_event_inbox(
    session: Session,
    *,
    include_acknowledged: bool = False,
) -> list[EventInboxItem]:
    statement = select(EventInboxRecord)
    if not include_acknowledged:
        statement = statement.where(EventInboxRecord.acknowledged_at.is_(None))
    rows = session.scalars(
        statement.order_by(EventInboxRecord.starts_at, EventInboxRecord.id.desc())
    ).all()
    return [_inbox_model(row) for row in rows]


def acknowledge_event_inbox(session: Session, inbox_id: int) -> EventInboxItem:
    row = session.get(EventInboxRecord, inbox_id)
    if row is None:
        raise LookupError(f"Event inbox item {inbox_id} not found")
    if row.acknowledged_at is None:
        row.acknowledged_at = utcnow()
        session.commit()
        session.refresh(row)
    return _inbox_model(row)


def _event_key(event: TrackedCalendarEvent) -> str:
    raw = "|".join(
        [
            event.event_type,
            event.subtype,
            event.symbol,
            event.starts_at.isoformat(),
            event.ends_at.isoformat() if event.ends_at else "",
            event.title,
        ]
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def evaluate_event_subscriptions(
    session: Session,
    provider: TrackedEventProvider,
    *,
    today: date | None = None,
) -> EventSubscriptionEvaluation:
    rows = session.scalars(
        select(EventSubscriptionRecord)
        .where(EventSubscriptionRecord.enabled.is_(True))
        .order_by(EventSubscriptionRecord.id)
    ).all()
    checked_at = datetime.now(UTC)
    current_date = today or checked_at.date()
    if not rows:
        return EventSubscriptionEvaluation(
            subscriptions=list_event_subscriptions(session),
            inbox=list_event_inbox(session),
            provenance=Provenance(
                provider="yahoo/yfinance",
                source="Yahoo Finance ticker calendar",
                license_class=LicenseClass.PERSONAL_ONLY,
                retrieved_at=checked_at,
                as_of=checked_at,
                notes=["No enabled event subscriptions; no calendar request was made."],
            ),
            evaluated_at=checked_at,
        )

    resolved: dict[int, set[str]] = {}
    all_symbols: set[str] = set()
    requested_types: set[TrackedEventType] = set()
    max_lead_days = 0
    for row in rows:
        symbols = set(
            resolve_tracked_symbols(
                session,
                cast(TrackedScope, row.scope),
                row.scope_id,
            )
        )
        resolved[row.id] = symbols
        all_symbols.update(symbols)
        requested_types.update(_event_types(row))
        max_lead_days = max(max_lead_days, row.lead_days)

    if not all_symbols:
        for row in rows:
            row.last_checked_at = checked_at
        session.commit()
        return EventSubscriptionEvaluation(
            subscriptions=list_event_subscriptions(session),
            inbox=list_event_inbox(session),
            provenance=Provenance(
                provider="yahoo/yfinance",
                source="Yahoo Finance ticker calendar",
                license_class=LicenseClass.PERSONAL_ONLY,
                retrieved_at=checked_at,
                as_of=checked_at,
                notes=["Enabled subscriptions resolved no symbols; no calendar request was made."],
            ),
            evaluated_at=checked_at,
        )

    events, unavailable, provenance = provider.events(
        sorted(all_symbols),
        current_date,
        current_date + timedelta(days=max_lead_days),
        sorted(requested_types),
    )
    for row in rows:
        own_symbols = resolved[row.id]
        own_types = set(_event_types(row))
        end_date = current_date + timedelta(days=row.lead_days)
        existing_keys = set(
            session.scalars(
                select(EventInboxRecord.event_key).where(EventInboxRecord.subscription_id == row.id)
            ).all()
        )
        for event in events:
            if event.symbol not in own_symbols or event.event_type not in own_types:
                continue
            if not current_date <= event.starts_at.date() <= end_date:
                continue
            key = _event_key(event)
            if key in existing_keys:
                continue
            session.add(
                EventInboxRecord(
                    subscription_id=row.id,
                    event_key=key,
                    event_type=event.event_type,
                    subtype=event.subtype,
                    symbol=event.symbol,
                    title=event.title,
                    starts_at=event.starts_at,
                    ends_at=event.ends_at,
                    created_at=checked_at,
                )
            )
            existing_keys.add(key)
        row.last_checked_at = checked_at
    session.commit()
    return EventSubscriptionEvaluation(
        subscriptions=list_event_subscriptions(session),
        inbox=list_event_inbox(session),
        unavailable_symbols=unavailable,
        provenance=provenance,
        evaluated_at=checked_at,
    )
