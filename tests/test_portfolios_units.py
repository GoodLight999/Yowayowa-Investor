"""Boundary and failure-path tests for ``yowayowa.services.portfolios``.

Scope: the portfolio ledger itself (create / read / update / delete, position
and snapshot rows, optimistic versioning). Pricing and analytics arithmetic is
covered by ``tests/test_portfolio_analytics.py``; API/CLI surfaces by
``tests/test_portfolio_delete.py`` and friends. Every test runs against a
throwaway in-memory SQLite engine, so no file or real database is written.

Boundary -> test correspondence
------------------------------
create / read
  name stripping, currency case                 test_create_strips_name_and_normalizes_currency
  malformed currency, nothing persisted         test_create_rejects_malformed_currency
  duplicate name -> IntegrityError, 1 row       test_create_duplicate_name_raises_integrity_error
  duplicate name -> IntegrityError, then the    test_duplicate_name_poisons_caller_session
    session stays usable (no manual rollback)
  blank name -> InputValidationError            test_blank_name_is_rejected
  list ordering by name                         test_list_portfolios_is_sorted_by_name
  unknown id -> LookupError                     test_get_unknown_portfolio_raises_lookup_error
empty portfolio
  freshly created portfolio owns no rows        test_new_portfolio_is_empty
  delete without force succeeds, reports 0      test_empty_portfolio_delete_reports_zero_rows
  second delete -> LookupError (not silent)     test_second_delete_raises_lookup_error
position upsert
  unknown portfolio -> LookupError              test_upsert_on_unknown_portfolio_raises_lookup_error
  invalid symbol -> InputValidationError        test_upsert_rejects_invalid_symbol
  invalid currency -> InputValidationError      test_upsert_rejects_invalid_currency
  symbol normalization, version starts at 1     test_upsert_normalizes_symbol_and_starts_at_one
  new symbol with expected_version=0 accepted   test_upsert_new_symbol_accepts_version_zero
  new symbol with expected_version!=0 conflict  test_upsert_new_symbol_rejects_nonzero_version
  stale expected_version keeps old value        test_upsert_stale_version_keeps_previous_value
  matching expected_version bumps exactly once  test_upsert_matching_version_bumps_once
bulk upsert
  empty payload rejected by the domain model    test_bulk_upsert_rejects_empty_payload
  duplicate symbols in one payload, last wins   test_bulk_upsert_last_symbol_wins
  replace=True drops absent symbols             test_bulk_replace_drops_absent_symbols
  failed replace restores deleted rows           test_failed_bulk_replace_restores_rows
delete
  dependents present -> refuse, counts reported  test_delete_refuses_while_rows_remain
  refused delete leaves the session usable       test_refused_delete_keeps_session_usable
  force -> all three dependent tables cleared    test_force_delete_clears_every_dependent_table
  failing statement -> full rollback (atomic)    test_force_delete_rolls_back_when_a_statement_fails
  unknown id -> LookupError                      test_delete_unknown_portfolio_raises_lookup_error
  other portfolios untouched                     test_force_delete_leaves_other_portfolios_intact
concurrent update
  second session wins, stale writer rejected     test_concurrent_writer_rejects_stale_version
remove position
  unknown portfolio -> LookupError               test_remove_position_unknown_portfolio_raises
  absent symbol, no expected_version -> no-op    test_remove_absent_symbol_without_version_is_a_noop
  absent symbol, expected_version -> conflict    test_remove_absent_symbol_with_version_conflicts
  stale version -> conflict, row kept            test_remove_stale_version_keeps_row
  normalization keeps sibling rows               test_remove_position_keeps_siblings
snapshots
  unknown portfolio -> LookupError               test_list_snapshots_unknown_portfolio_raises
  limit keeps the newest window, chronological   test_snapshot_limit_returns_newest_window
  negative limit -> ValueError                   test_negative_snapshot_limit_is_rejected
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from yowayowa.db import (
    Base,
    BrokerExecutionApplicationRecord,
    PortfolioRecord,
    PortfolioSnapshotRecord,
    PositionRecord,
)
from yowayowa.domain import PositionBulkUpsert, PositionUpsert
from yowayowa.services import portfolios as portfolio_service
from yowayowa.services.portfolios import (
    PortfolioNotEmptyError,
    PositionVersionConflictError,
    bulk_upsert_positions,
    create_portfolio,
    delete_portfolio,
    get_portfolio,
    list_portfolio_snapshots,
    list_portfolios,
    remove_position,
    upsert_position,
)
from yowayowa.symbols import InputValidationError

_NOW = datetime(2026, 10, 23, 3, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
@pytest.fixture()
def session() -> Iterator[Session]:
    """A throwaway in-memory ledger; one shared connection so sessions can race."""
    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    try:
        with Session(engine, expire_on_commit=False) as db:
            yield db
    finally:
        engine.dispose()


def _second_session(db: Session) -> Session:
    """Another session on the same engine, i.e. a concurrent request."""
    return Session(db.get_bind(), expire_on_commit=False)


def _count(db: Session, model: Any, portfolio_id: int) -> int:
    total = db.scalar(
        select(func.count()).select_from(model).where(model.portfolio_id == portfolio_id)
    )
    return int(total or 0)


def _seed_dependents(db: Session, portfolio_id: int) -> None:
    """Give a portfolio one row in every table that makes deletion destructive."""
    db.add(
        PortfolioSnapshotRecord(
            portfolio_id=portfolio_id,
            net_market_value=Decimal("1000"),
            gross_market_value=Decimal("1000"),
            unrealized_pnl=Decimal("0"),
            day_pnl=Decimal("0"),
            captured_at=_NOW,
        )
    )
    db.add(
        BrokerExecutionApplicationRecord(
            portfolio_id=portfolio_id,
            market="US",
            execution_id=f"exec-{portfolio_id}",
            execution_fingerprint="f" * 64,
            preview_id="preview-1",
            applied_at=_NOW,
        )
    )
    db.commit()


def _add_snapshot(db: Session, portfolio_id: int, value: str, captured_at: datetime) -> None:
    db.add(
        PortfolioSnapshotRecord(
            portfolio_id=portfolio_id,
            net_market_value=Decimal(value),
            gross_market_value=Decimal(value),
            unrealized_pnl=Decimal("0"),
            day_pnl=Decimal("0"),
            captured_at=captured_at,
        )
    )
    db.commit()


# ---------------------------------------------------------------------------
# create / read
# ---------------------------------------------------------------------------
def test_create_strips_name_and_normalizes_currency(session: Session) -> None:
    created = create_portfolio(session, "  Core Book  ", " usd ")

    assert created.name == "Core Book"
    assert created.base_currency == "USD"
    assert created.positions == []
    assert created.id > 0
    assert get_portfolio(session, created.id).name == "Core Book"


def test_create_rejects_malformed_currency(session: Session) -> None:
    with pytest.raises(InputValidationError):
        create_portfolio(session, "Bad", "US")

    # Validation happens before anything is added to the session.
    assert session.scalar(select(func.count()).select_from(PortfolioRecord)) == 0
    assert list_portfolios(session) == []


def test_create_duplicate_name_raises_integrity_error(session: Session) -> None:
    create_portfolio(session, "Core", "USD")

    with pytest.raises(IntegrityError):
        create_portfolio(session, "Core", "JPY")

    session.rollback()
    names = [portfolio.name for portfolio in list_portfolios(session)]
    assert names == ["Core"]


def test_duplicate_name_poisons_caller_session(session: Session) -> None:
    create_portfolio(session, "Core", "USD")

    with pytest.raises(IntegrityError):
        create_portfolio(session, "Core", "USD")

    # Desired: the service owns its failed transaction and the caller can carry on.
    assert [portfolio.name for portfolio in list_portfolios(session)] == ["Core"]


def test_blank_name_is_rejected(session: Session) -> None:
    with pytest.raises(InputValidationError):
        create_portfolio(session, "   ", "USD")


def test_list_portfolios_is_sorted_by_name(session: Session) -> None:
    for name in ("Zeta", "Alpha", "Mist"):
        create_portfolio(session, name, "USD")

    assert [portfolio.name for portfolio in list_portfolios(session)] == [
        "Alpha",
        "Mist",
        "Zeta",
    ]


def test_get_unknown_portfolio_raises_lookup_error(session: Session) -> None:
    with pytest.raises(LookupError):
        get_portfolio(session, 424242)


# ---------------------------------------------------------------------------
# empty portfolio
# ---------------------------------------------------------------------------
def test_new_portfolio_is_empty(session: Session) -> None:
    created = create_portfolio(session, "Empty", "USD")

    stored = get_portfolio(session, created.id)
    assert stored.positions == []
    assert list_portfolio_snapshots(session, created.id) == []
    for model in (PositionRecord, PortfolioSnapshotRecord, BrokerExecutionApplicationRecord):
        assert _count(session, model, created.id) == 0


def test_empty_portfolio_delete_reports_zero_rows(session: Session) -> None:
    created = create_portfolio(session, "Empty", "USD")

    deletion = delete_portfolio(session, created.id)

    assert deletion.portfolio_id == created.id
    assert deletion.name == "Empty"
    assert deletion.force is False
    assert deletion.deleted_positions == 0
    assert deletion.deleted_snapshots == 0
    assert deletion.deleted_applied_executions == 0
    assert session.get(PortfolioRecord, created.id) is None


def test_second_delete_raises_lookup_error(session: Session) -> None:
    created = create_portfolio(session, "Empty", "USD")
    delete_portfolio(session, created.id)

    with pytest.raises(LookupError):
        delete_portfolio(session, created.id)


# ---------------------------------------------------------------------------
# position upsert
# ---------------------------------------------------------------------------
def test_upsert_on_unknown_portfolio_raises_lookup_error(session: Session) -> None:
    with pytest.raises(LookupError):
        upsert_position(session, 987654, PositionUpsert(symbol="AAPL", quantity="1"))


def test_upsert_rejects_invalid_symbol(session: Session) -> None:
    created = create_portfolio(session, "Core", "USD")

    with pytest.raises(InputValidationError):
        upsert_position(session, created.id, PositionUpsert(symbol="bad symbol!", quantity="1"))

    assert _count(session, PositionRecord, created.id) == 0
    assert get_portfolio(session, created.id).positions == []


def test_upsert_rejects_invalid_currency(session: Session) -> None:
    created = create_portfolio(session, "Core", "USD")

    with pytest.raises(InputValidationError):
        upsert_position(
            session,
            created.id,
            PositionUpsert(symbol="AAPL", quantity="1", currency="US"),
        )

    assert _count(session, PositionRecord, created.id) == 0


def test_upsert_normalizes_symbol_and_starts_at_one(session: Session) -> None:
    created = create_portfolio(session, "Core", "USD")

    updated = upsert_position(
        session,
        created.id,
        PositionUpsert(symbol=" aapl ", quantity="10", average_cost="100"),
    )

    stored = [
        (item.symbol, item.quantity, item.currency, item.version) for item in updated.positions
    ]
    assert stored == [("AAPL", Decimal("10"), "USD", 1)]


def test_upsert_new_symbol_accepts_version_zero(session: Session) -> None:
    created = create_portfolio(session, "Core", "USD")

    updated = upsert_position(
        session,
        created.id,
        PositionUpsert(symbol="AAPL", quantity="1"),
        expected_version=0,
    )

    assert [(item.symbol, item.version) for item in updated.positions] == [("AAPL", 1)]


def test_upsert_new_symbol_rejects_nonzero_version(session: Session) -> None:
    created = create_portfolio(session, "Core", "USD")

    with pytest.raises(PositionVersionConflictError):
        upsert_position(
            session,
            created.id,
            PositionUpsert(symbol="AAPL", quantity="1"),
            expected_version=3,
        )

    assert _count(session, PositionRecord, created.id) == 0


def test_upsert_stale_version_keeps_previous_value(session: Session) -> None:
    created = create_portfolio(session, "Core", "USD")
    upsert_position(session, created.id, PositionUpsert(symbol="AAPL", quantity="10"))

    with pytest.raises(PositionVersionConflictError):
        upsert_position(
            session,
            created.id,
            PositionUpsert(symbol="AAPL", quantity="99"),
            expected_version=7,
        )

    positions = get_portfolio(session, created.id).positions
    assert [(item.quantity, item.version) for item in positions] == [(Decimal("10"), 1)]


def test_upsert_matching_version_bumps_once(session: Session) -> None:
    created = create_portfolio(session, "Core", "USD")
    upsert_position(session, created.id, PositionUpsert(symbol="AAPL", quantity="10"))

    updated = upsert_position(
        session,
        created.id,
        PositionUpsert(symbol="AAPL", quantity="11", average_cost="50"),
        expected_version=1,
    )

    assert [(item.quantity, item.version) for item in updated.positions] == [(Decimal("11"), 2)]


# ---------------------------------------------------------------------------
# bulk upsert
# ---------------------------------------------------------------------------
def test_bulk_upsert_rejects_empty_payload() -> None:
    with pytest.raises(ValidationError):
        PositionBulkUpsert(positions=[])


def test_bulk_upsert_last_symbol_wins(session: Session) -> None:
    created = create_portfolio(session, "Bulk", "USD")

    updated = bulk_upsert_positions(
        session,
        created.id,
        PositionBulkUpsert(
            positions=[
                PositionUpsert(symbol="aapl", quantity="1"),
                PositionUpsert(symbol="AAPL", quantity="2", currency="jpy"),
            ]
        ),
    )

    # A duplicate symbol inside one payload collapses to a single row (last wins)
    # instead of tripping the (portfolio_id, symbol) unique constraint.
    stored = [
        (item.symbol, item.quantity, item.currency, item.version) for item in updated.positions
    ]
    assert stored == [("AAPL", Decimal("2"), "JPY", 1)]


def test_bulk_replace_drops_absent_symbols(session: Session) -> None:
    created = create_portfolio(session, "Bulk", "USD")
    bulk_upsert_positions(
        session,
        created.id,
        PositionBulkUpsert(
            positions=[
                PositionUpsert(symbol="AAPL", quantity="1"),
                PositionUpsert(symbol="MSFT", quantity="2"),
            ]
        ),
    )

    replaced = bulk_upsert_positions(
        session,
        created.id,
        PositionBulkUpsert(
            positions=[PositionUpsert(symbol="SOFI", quantity="3")],
            replace=True,
        ),
    )

    assert [item.symbol for item in replaced.positions] == ["SOFI"]


def test_failed_bulk_replace_restores_rows(session: Session) -> None:
    created = create_portfolio(session, "Bulk", "USD")
    bulk_upsert_positions(
        session,
        created.id,
        PositionBulkUpsert(
            positions=[
                PositionUpsert(symbol="AAPL", quantity="1"),
                PositionUpsert(symbol="MSFT", quantity="2"),
            ]
        ),
    )

    # replace=True would drop MSFT and then hit AAPL's stale version; the whole
    # statement must roll back so no row disappears on a failed import.
    with pytest.raises(PositionVersionConflictError):
        bulk_upsert_positions(
            session,
            created.id,
            PositionBulkUpsert(
                positions=[
                    PositionUpsert(symbol="SOFI", quantity="3"),
                    PositionUpsert(symbol="AAPL", quantity="9", expected_version=99),
                ],
                replace=True,
            ),
        )

    positions = get_portfolio(session, created.id).positions
    assert [(item.symbol, item.quantity, item.version) for item in positions] == [
        ("AAPL", Decimal("1"), 1),
        ("MSFT", Decimal("2"), 1),
    ]


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------
def test_delete_refuses_while_rows_remain(session: Session) -> None:
    created = create_portfolio(session, "Core", "USD")
    upsert_position(session, created.id, PositionUpsert(symbol="AAPL", quantity="1"))
    _seed_dependents(session, created.id)

    with pytest.raises(PortfolioNotEmptyError) as excinfo:
        delete_portfolio(session, created.id)

    assert excinfo.value.portfolio_id == created.id
    assert excinfo.value.counts == {
        "positions": 1,
        "snapshots": 1,
        "applied_executions": 1,
    }
    assert "positions=1" in str(excinfo.value)
    assert "force=true" in str(excinfo.value)
    # A refused delete must not be a partial delete.
    assert _count(session, PositionRecord, created.id) == 1
    assert _count(session, PortfolioSnapshotRecord, created.id) == 1
    assert _count(session, BrokerExecutionApplicationRecord, created.id) == 1
    assert session.get(PortfolioRecord, created.id) is not None


def test_refused_delete_keeps_session_usable(session: Session) -> None:
    created = create_portfolio(session, "Core", "USD")
    upsert_position(session, created.id, PositionUpsert(symbol="AAPL", quantity="1"))

    with pytest.raises(PortfolioNotEmptyError):
        delete_portfolio(session, created.id)

    # The refusal rolls back, so the caller's session is not poisoned.
    assert get_portfolio(session, created.id).name == "Core"


def test_force_delete_clears_every_dependent_table(session: Session) -> None:
    created = create_portfolio(session, "Core", "USD")
    upsert_position(session, created.id, PositionUpsert(symbol="AAPL", quantity="1"))
    _seed_dependents(session, created.id)

    deletion = delete_portfolio(session, created.id, force=True)

    assert deletion.force is True
    assert deletion.name == "Core"
    assert deletion.deleted_positions == 1
    assert deletion.deleted_snapshots == 1
    assert deletion.deleted_applied_executions == 1
    for model in (PositionRecord, PortfolioSnapshotRecord, BrokerExecutionApplicationRecord):
        assert _count(session, model, created.id) == 0
    assert session.get(PortfolioRecord, created.id) is None
    with pytest.raises(LookupError):
        get_portfolio(session, created.id)


def test_force_delete_rolls_back_when_a_statement_fails(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = create_portfolio(session, "Core", "USD")
    upsert_position(session, created.id, PositionUpsert(symbol="AAPL", quantity="1"))
    _seed_dependents(session, created.id)

    original_delete = portfolio_service.delete
    calls = {"count": 0}

    def failing_delete(model: Any) -> Any:
        calls["count"] += 1
        if calls["count"] == 4:  # 4th statement removes the portfolio row itself
            raise RuntimeError("simulated failure on the final delete")
        return original_delete(model)

    monkeypatch.setattr(portfolio_service, "delete", failing_delete)

    with pytest.raises(RuntimeError):
        delete_portfolio(session, created.id, force=True)

    assert calls["count"] == 4
    # Nothing may survive half-deleted: the three dependent deletes and the
    # portfolio delete share one transaction and all of them roll back.
    assert _count(session, PositionRecord, created.id) == 1
    assert _count(session, PortfolioSnapshotRecord, created.id) == 1
    assert _count(session, BrokerExecutionApplicationRecord, created.id) == 1
    assert session.get(PortfolioRecord, created.id) is not None


def test_delete_unknown_portfolio_raises_lookup_error(session: Session) -> None:
    with pytest.raises(LookupError):
        delete_portfolio(session, 424242)
    with pytest.raises(LookupError):
        delete_portfolio(session, 424242, force=True)


def test_force_delete_leaves_other_portfolios_intact(session: Session) -> None:
    doomed = create_portfolio(session, "Doomed", "USD")
    survivor = create_portfolio(session, "Survivor", "JPY")
    for portfolio in (doomed, survivor):
        upsert_position(
            session,
            portfolio.id,
            PositionUpsert(symbol="AAPL", quantity="1", currency=portfolio.base_currency),
        )
        _seed_dependents(session, portfolio.id)

    delete_portfolio(session, doomed.id, force=True)

    assert _count(session, PositionRecord, survivor.id) == 1
    assert _count(session, PortfolioSnapshotRecord, survivor.id) == 1
    assert _count(session, BrokerExecutionApplicationRecord, survivor.id) == 1
    assert get_portfolio(session, survivor.id).positions[0].symbol == "AAPL"


# ---------------------------------------------------------------------------
# concurrent update
# ---------------------------------------------------------------------------
def test_concurrent_writer_rejects_stale_version(session: Session) -> None:
    created = create_portfolio(session, "Race", "USD")
    upsert_position(session, created.id, PositionUpsert(symbol="AAPL", quantity="10"))

    other = _second_session(session)
    try:
        observed = get_portfolio(other, created.id)
        observed_version = observed.positions[0].version
        assert observed_version == 1

        # Second writer commits first.
        upsert_position(
            session,
            created.id,
            PositionUpsert(symbol="AAPL", quantity="11"),
            expected_version=observed_version,
        )

        # The stale writer must not clobber it.
        with pytest.raises(PositionVersionConflictError):
            upsert_position(
                other,
                created.id,
                PositionUpsert(symbol="AAPL", quantity="12"),
                expected_version=observed_version,
            )
    finally:
        other.close()

    positions = get_portfolio(session, created.id).positions
    assert [(item.quantity, item.version) for item in positions] == [(Decimal("11"), 2)]


# ---------------------------------------------------------------------------
# remove position
# ---------------------------------------------------------------------------
def test_remove_position_unknown_portfolio_raises(session: Session) -> None:
    with pytest.raises(LookupError):
        remove_position(session, 999999, "AAPL")


def test_remove_absent_symbol_without_version_is_a_noop(session: Session) -> None:
    created = create_portfolio(session, "Core", "USD")
    upsert_position(session, created.id, PositionUpsert(symbol="AAPL", quantity="1"))

    # No expected_version means "idempotent removal"; the ledger is returned as is.
    unchanged = remove_position(session, created.id, "ZZZZ")

    assert [item.symbol for item in unchanged.positions] == ["AAPL"]
    assert _count(session, PositionRecord, created.id) == 1


def test_remove_absent_symbol_with_version_conflicts(session: Session) -> None:
    created = create_portfolio(session, "Core", "USD")
    upsert_position(session, created.id, PositionUpsert(symbol="AAPL", quantity="1"))

    with pytest.raises(PositionVersionConflictError):
        remove_position(session, created.id, "ZZZZ", expected_version=1)

    assert _count(session, PositionRecord, created.id) == 1


def test_remove_stale_version_keeps_row(session: Session) -> None:
    created = create_portfolio(session, "Core", "USD")
    upsert_position(session, created.id, PositionUpsert(symbol="AAPL", quantity="1"))

    with pytest.raises(PositionVersionConflictError):
        remove_position(session, created.id, "AAPL", expected_version=7)

    assert [item.symbol for item in get_portfolio(session, created.id).positions] == ["AAPL"]


def test_remove_position_keeps_siblings(session: Session) -> None:
    created = create_portfolio(session, "Core", "USD")
    bulk_upsert_positions(
        session,
        created.id,
        PositionBulkUpsert(
            positions=[
                PositionUpsert(symbol="AAPL", quantity="1"),
                PositionUpsert(symbol="MSFT", quantity="2"),
            ]
        ),
    )

    remaining = remove_position(session, created.id, " aapl ", expected_version=1)

    assert [(item.symbol, item.version) for item in remaining.positions] == [("MSFT", 1)]


# ---------------------------------------------------------------------------
# snapshots
# ---------------------------------------------------------------------------
def test_list_snapshots_unknown_portfolio_raises(session: Session) -> None:
    with pytest.raises(LookupError):
        list_portfolio_snapshots(session, 424242)


def test_snapshot_limit_returns_newest_window(session: Session) -> None:
    created = create_portfolio(session, "Core", "USD")
    for index, value in enumerate(("100", "110", "120")):
        _add_snapshot(session, created.id, value, _NOW.replace(hour=index))

    window = list_portfolio_snapshots(session, created.id, limit=2)

    # The limit is applied to the newest rows, then returned chronologically.
    assert [snapshot.net_market_value for snapshot in window] == [110.0, 120.0]
    assert list_portfolio_snapshots(session, created.id, limit=0) == []
    assert len(list_portfolio_snapshots(session, created.id)) == 3


def test_negative_snapshot_limit_is_rejected(session: Session) -> None:
    created = create_portfolio(session, "Core", "USD")
    _add_snapshot(session, created.id, "100", _NOW)

    with pytest.raises(ValueError):
        list_portfolio_snapshots(session, created.id, limit=-1)
