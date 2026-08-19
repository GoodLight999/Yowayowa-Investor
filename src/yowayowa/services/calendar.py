from __future__ import annotations

from datetime import date
from typing import Protocol

from sqlalchemy.orm import Session

from yowayowa.calendar_models import (
    TrackedCalendarEvent,
    TrackedEventCalendar,
    TrackedEventType,
    TrackedScope,
)
from yowayowa.domain import Provenance
from yowayowa.services.portfolios import get_portfolio, list_portfolios
from yowayowa.services.watchlists import list_watchlists


class TrackedCalendarProvider(Protocol):
    def events(
        self,
        symbols: list[str],
        start: date,
        end: date,
        event_types: list[TrackedEventType] | None = None,
    ) -> tuple[list[TrackedCalendarEvent], list[str], Provenance]: ...


def resolve_tracked_symbols(
    session: Session,
    scope: TrackedScope,
    scope_id: int | None,
) -> list[str]:
    if scope == "all":
        symbols: set[str] = set()
        for saved_watchlist in list_watchlists(session):
            symbols.update(saved_watchlist.symbols)
        for portfolio in list_portfolios(session):
            symbols.update(
                position.symbol for position in portfolio.positions if position.quantity != 0
            )
        return sorted(symbols)

    if scope_id is None:
        raise ValueError(f"scope_id is required for {scope} scope")

    if scope == "watchlist":
        selected_watchlist = next(
            (item for item in list_watchlists(session) if item.id == scope_id),
            None,
        )
        if selected_watchlist is None:
            raise LookupError(f"Watchlist {scope_id} not found")
        return list(dict.fromkeys(selected_watchlist.symbols))

    portfolio = get_portfolio(session, scope_id)
    return list(
        dict.fromkeys(position.symbol for position in portfolio.positions if position.quantity != 0)
    )


def tracked_event_calendar(
    session: Session,
    provider: TrackedCalendarProvider,
    start: date,
    end: date,
    scope: TrackedScope,
    scope_id: int | None,
    event_types: list[TrackedEventType] | None = None,
) -> TrackedEventCalendar:
    symbols = resolve_tracked_symbols(session, scope, scope_id)
    if len(symbols) > 100:
        raise ValueError(f"Tracked calendar resolved {len(symbols)} symbols; maximum is 100")
    events, unavailable, provenance = provider.events(symbols, start, end, event_types)
    return TrackedEventCalendar(
        start=start,
        end=end,
        scope=scope,
        scope_id=scope_id,
        symbols=symbols,
        events=events,
        unavailable_symbols=unavailable,
        provenance=provenance,
    )
