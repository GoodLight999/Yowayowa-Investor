from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from yowayowa.db import Base, WatchlistRecord, utcnow
from yowayowa.services.watchlists import add_symbols, remove_symbol


def test_watchlist_add_is_idempotent_and_normalized() -> None:
    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        with Session(engine, expire_on_commit=False) as session:
            now = utcnow()
            row = WatchlistRecord(name="Main", created_at=now, updated_at=now)
            session.add(row)
            session.commit()
            updated = add_symbols(session, row.id, ["rklb", "RKLB", " asts "])
            assert updated.symbols == ["ASTS", "RKLB"]
            updated = remove_symbol(session, row.id, "rklb")
            assert updated.symbols == ["ASTS"]
    finally:
        engine.dispose()
