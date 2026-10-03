from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from yowayowa.db import WatchlistItemRecord, WatchlistRecord, utcnow
from yowayowa.domain import Watchlist
from yowayowa.symbols import normalize_symbol


def _to_model(row: WatchlistRecord) -> Watchlist:
    return Watchlist(
        id=row.id,
        name=row.name,
        symbols=[item.symbol for item in row.items],
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def list_watchlists(session: Session) -> list[Watchlist]:
    rows = session.scalars(select(WatchlistRecord).order_by(WatchlistRecord.name)).unique().all()
    return [_to_model(row) for row in rows]


def create_watchlist(session: Session, name: str) -> Watchlist:
    now = utcnow()
    row = WatchlistRecord(name=name.strip(), created_at=now, updated_at=now)
    session.add(row)
    session.commit()
    session.refresh(row)
    return _to_model(row)


def add_symbols(session: Session, watchlist_id: int, symbols: list[str]) -> Watchlist:
    row = session.get(WatchlistRecord, watchlist_id)
    if row is None:
        raise LookupError(f"Watchlist {watchlist_id} not found")
    existing = {item.symbol for item in row.items}
    for symbol in symbols:
        normalized = normalize_symbol(symbol)
        if normalized not in existing:
            row.items.append(WatchlistItemRecord(symbol=normalized))
            existing.add(normalized)
    row.updated_at = utcnow()
    session.commit()
    session.refresh(row)
    return _to_model(row)


def remove_symbol(session: Session, watchlist_id: int, symbol: str) -> Watchlist:
    row = session.get(WatchlistRecord, watchlist_id)
    if row is None:
        raise LookupError(f"Watchlist {watchlist_id} not found")
    normalized = normalize_symbol(symbol)
    row.items[:] = [item for item in row.items if item.symbol != normalized]
    row.updated_at = utcnow()
    session.commit()
    session.refresh(row)
    return _to_model(row)
