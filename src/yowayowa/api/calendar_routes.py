from __future__ import annotations

from datetime import date, timedelta
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from yowayowa.api.deps import db_session, require_api_token
from yowayowa.calendar_models import TrackedEventCalendar, TrackedEventType, TrackedScope
from yowayowa.news_models import (
    EventInboxItem,
    EventSubscription,
    EventSubscriptionCreate,
    EventSubscriptionEvaluation,
)
from yowayowa.providers.base import ProviderPolicyError
from yowayowa.providers.registry import yahoo_tracked_calendar_provider
from yowayowa.services.alerts import (
    acknowledge_event_inbox,
    create_event_subscription,
    delete_event_subscription,
    evaluate_event_subscriptions,
    list_event_inbox,
    list_event_subscriptions,
)
from yowayowa.services.calendar import tracked_event_calendar

router = APIRouter(prefix="/v1", dependencies=[Depends(require_api_token)])


@router.get("/calendar/tracked", response_model=TrackedEventCalendar)
def tracked_calendar(
    start: date | None = None,
    end: date | None = None,
    scope: TrackedScope = Query(default="all"),
    scope_id: int | None = Query(default=None, ge=1),
    types: str = Query(default="earnings,dividend", max_length=100),
    session: Session = Depends(db_session),
) -> TrackedEventCalendar:
    resolved_start = start or date.today()
    resolved_end = end or (resolved_start + timedelta(days=14))
    if resolved_end < resolved_start:
        raise HTTPException(status_code=422, detail="Calendar end must not be before start")
    if (resolved_end - resolved_start).days > 93:
        raise HTTPException(status_code=422, detail="Calendar range is limited to 93 days")
    if scope == "all" and scope_id is not None:
        raise HTTPException(status_code=422, detail="scope_id is not valid for all scope")
    if scope != "all" and scope_id is None:
        raise HTTPException(status_code=422, detail=f"scope_id is required for {scope} scope")

    allowed = {"earnings", "dividend", "ticker"}
    requested_raw = [token.strip().lower() for token in types.split(",") if token.strip()]
    unknown = sorted(set(requested_raw) - allowed)
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown tracked calendar event types: {', '.join(unknown)}",
        )
    requested = [cast(TrackedEventType, token) for token in dict.fromkeys(requested_raw)]
    try:
        return tracked_event_calendar(
            session,
            yahoo_tracked_calendar_provider(),
            resolved_start,
            resolved_end,
            scope,
            scope_id,
            requested,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ProviderPolicyError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/event-subscriptions", response_model=list[EventSubscription])
def get_event_subscriptions(session: Session = Depends(db_session)) -> list[EventSubscription]:
    return list_event_subscriptions(session)


@router.post("/event-subscriptions", response_model=EventSubscription, status_code=201)
def post_event_subscription(
    payload: EventSubscriptionCreate,
    session: Session = Depends(db_session),
) -> EventSubscription:
    try:
        return create_event_subscription(session, payload)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/event-subscriptions/{subscription_id}", status_code=204)
def remove_event_subscription(
    subscription_id: int,
    session: Session = Depends(db_session),
) -> Response:
    try:
        delete_event_subscription(session, subscription_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(status_code=204)


@router.get("/event-inbox", response_model=list[EventInboxItem])
def get_event_inbox(
    include_acknowledged: bool = False,
    session: Session = Depends(db_session),
) -> list[EventInboxItem]:
    return list_event_inbox(session, include_acknowledged=include_acknowledged)


@router.post("/event-inbox/{inbox_id}/ack", response_model=EventInboxItem)
def acknowledge_event(
    inbox_id: int,
    session: Session = Depends(db_session),
) -> EventInboxItem:
    try:
        return acknowledge_event_inbox(session, inbox_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/event-subscriptions/evaluate", response_model=EventSubscriptionEvaluation)
def evaluate_subscriptions(
    session: Session = Depends(db_session),
) -> EventSubscriptionEvaluation:
    try:
        return evaluate_event_subscriptions(session, yahoo_tracked_calendar_provider())
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ProviderPolicyError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
