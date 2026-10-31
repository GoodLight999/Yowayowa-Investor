"""Boundary regression net for ``yowayowa.db`` (schema, migrations, sessions).

Every test here exercises real behaviour: a real SQLite file in ``tmp_path``
(or the real in-process engine), real ``init_database`` migration paths and
real ORM round-trips. No network, no external service, no production database.

Covered boundaries (see ``src/yowayowa/db.py``):

* schema generation -- every declared table/column/unique key exists after
  ``init_database``;
* additive migrations -- the hypothesis table and the price-alert notification
  inbox are added to an existing installation without touching old rows;
* rebuild migrations -- legacy ``positions`` (no ``version``, no composite
  unique) and legacy ``screening_candidates`` (no ``document_id``) are rebuilt
  in place, preserving rows, and become no-ops on a second run;
* fail-closed migration -- duplicate ``(portfolio_id, symbol)`` rows abort the
  unique-constraint migration with the offending ids and keep the data;
* transaction boundaries -- commit survives across sessions, rollback discards
  pending writes, ``engine.begin()`` rolls back on exception;
* connection reuse -- ``get_session`` keeps one engine, ``init_database``
  replaces it, ``dispose_database`` clears the module state and the next
  ``get_session`` re-initialises from settings;
* URL handling -- ``postgres://``/``postgresql://`` normalisation, SQLite
  parent-directory creation, in-memory/relative URLs left alone;
* storage semantics that the rest of the codebase depends on (Decimal
  precision, naive-on-read datetimes, JSON/list defaults, unique keys).

Where a test documents a backend limitation rather than a desired guarantee it
says so in its docstring, so the limitation is visible and regression-checked
instead of being discovered by a future caller.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from yowayowa import db as dbm
from yowayowa.config import Settings, get_settings
from yowayowa.db import (
    Base,
    BrokerExecutionApplicationRecord,
    CreditMarginWeeklyRecord,
    EventInboxRecord,
    EventSubscriptionRecord,
    HypothesisRecord,
    JpxMarginBalanceRecord,
    PortfolioRecord,
    PortfolioSnapshotRecord,
    PositionRecord,
    PriceAlertNotificationRecord,
    PriceAlertRecord,
    ResearchBriefRecord,
    ResearchPresetRecord,
    ScreeningCandidateRecord,
    StrategyResearchSnapshotRecord,
    WatchlistItemRecord,
    WatchlistRecord,
    dispose_database,
    get_or_create_default_watchlist,
    get_session,
    init_database,
    make_engine,
    utcnow,
)

_NOW = datetime(2026, 10, 23, 3, 0, tzinfo=UTC)

_LEGACY_POSITIONS_COLUMNS = """
    id INTEGER NOT NULL PRIMARY KEY,
    portfolio_id INTEGER NOT NULL,
    symbol VARCHAR(32) NOT NULL,
    quantity NUMERIC(28, 10) NOT NULL,
    average_cost NUMERIC(28, 10),
    currency VARCHAR(3) NOT NULL
"""

_LEGACY_PORTFOLIOS = """
CREATE TABLE portfolios (
    id INTEGER NOT NULL PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE,
    base_currency VARCHAR(3) NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
);
INSERT INTO portfolios VALUES (1, 'Legacy', 'JPY', '2026-01-01 00:00:00', '2026-01-01 00:00:00');
"""

_LEGACY_SCREENING = """
CREATE TABLE screening_candidates (
    id INTEGER NOT NULL,
    run_date DATE NOT NULL,
    source VARCHAR(32) NOT NULL,
    code VARCHAR(5) NOT NULL,
    signal VARCHAR(40) NOT NULL,
    company_name VARCHAR(500),
    value JSON NOT NULL,
    reason VARCHAR(500) NOT NULL,
    provenance JSON NOT NULL,
    retrieved_at DATETIME NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_screening_candidates_run_source_code_signal
        UNIQUE (run_date, source, code, signal)
);
INSERT INTO screening_candidates VALUES (
    1, '2026-09-24', 'edinet_filing', '6758', 'filing_forecast_revision',
    NULL, '{"metric": 1}', 'legacy reason', '{"provider": "edinet"}',
    '2026-09-24 12:00:00'
);
"""


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _clean_process_state() -> Any:
    """Never let one test's global engine leak into the next one."""

    dispose_database()
    yield
    dispose_database()


def _legacy_db(tmp_path: Path, script: str, name: str = "legacy.db") -> Path:
    path = tmp_path / name
    connection = sqlite3.connect(path)
    try:
        connection.executescript(script)
        connection.commit()
    finally:
        connection.close()
    return path


def _settings(path: Path) -> Settings:
    return Settings(database_url=f"sqlite:///{path}")


def _initialized_engine(tmp_path: Path, name: str = "app.db") -> Engine:
    init_database(_settings(tmp_path / name))
    engine = dbm._engine
    assert engine is not None
    return engine


def _positions_legacy_script(*, unique: bool, version: bool, extra: str = "") -> str:
    columns = _LEGACY_POSITIONS_COLUMNS
    if version:
        columns = columns.rstrip() + ",\n    version INTEGER NOT NULL DEFAULT 1\n"
    script = (
        _LEGACY_PORTFOLIOS
        + f"CREATE TABLE positions (\n{columns});\n"
        + "INSERT INTO positions VALUES (7, 1, '7203', 10, 900.5, 'JPY'"
        + (", 4" if version else "")
        + ");\n"
    )
    if unique:
        script += (
            "CREATE UNIQUE INDEX uq_positions_portfolio_symbol "
            "ON positions (portfolio_id, symbol);\n"
        )
    return script + extra


# ---------------------------------------------------------------------------
# schema generation
# ---------------------------------------------------------------------------
def test_init_database_creates_every_declared_table(tmp_path: Path) -> None:
    engine = _initialized_engine(tmp_path)

    assert set(inspect(engine).get_table_names()) == set(Base.metadata.tables)


def test_init_database_supports_the_full_update_read_compute_flow(tmp_path: Path) -> None:
    """A portfolio row is writable, readable and updatable with real types."""

    engine = _initialized_engine(tmp_path)
    with Session(engine, expire_on_commit=False) as session:
        portfolio = PortfolioRecord(
            name="Main", base_currency="JPY", created_at=_NOW, updated_at=_NOW
        )
        session.add(portfolio)
        session.commit()
        session.add(
            PositionRecord(
                portfolio_id=portfolio.id,
                symbol="7203",
                quantity=Decimal("100.0000000001"),
                average_cost=Decimal("2500.5000000000"),
                currency="JPY",
            )
        )
        session.commit()

        read_back = session.scalars(select(PositionRecord)).one()
        read_back.quantity = Decimal("50")
        session.commit()

        assert session.scalars(select(PositionRecord)).one().quantity == Decimal("50.0000000000")


def test_positions_schema_has_version_and_composite_unique(tmp_path: Path) -> None:
    engine = _initialized_engine(tmp_path)
    inspector = inspect(engine)

    columns = {column["name"] for column in inspector.get_columns("positions")}
    uniques = inspector.get_unique_constraints("positions")

    assert {"portfolio_id", "symbol", "quantity", "average_cost", "currency", "version"} <= columns
    assert any(
        set(constraint["column_names"]) == {"portfolio_id", "symbol"} for constraint in uniques
    )
    assert {index["name"] for index in inspector.get_indexes("positions")} >= {
        "ix_positions_portfolio_id",
        "ix_positions_symbol",
    }


def test_screening_candidates_schema_has_document_id_and_five_column_key(
    tmp_path: Path,
) -> None:
    engine = _initialized_engine(tmp_path)
    inspector = inspect(engine)

    columns = {column["name"] for column in inspector.get_columns("screening_candidates")}
    uniques = inspector.get_unique_constraints("screening_candidates")

    assert "document_id" in columns
    assert any(
        set(constraint["column_names"]) == {"run_date", "source", "code", "signal", "document_id"}
        for constraint in uniques
    )


def test_init_database_is_idempotent_and_replaces_the_previous_engine(
    tmp_path: Path,
) -> None:
    path = tmp_path / "repeat.db"
    first_engine = _initialized_engine(tmp_path, "repeat.db")
    with Session(first_engine) as session:
        session.add(WatchlistRecord(name="Main", created_at=_NOW, updated_at=_NOW))
        session.commit()

    init_database(_settings(path))
    second_engine = dbm._engine

    assert second_engine is not None
    assert second_engine is not first_engine
    with Session(second_engine) as session:
        assert session.scalars(select(WatchlistRecord)).one().name == "Main"


def test_init_database_creates_missing_sqlite_parent_directory(tmp_path: Path) -> None:
    nested = tmp_path / "deep" / "nested" / "app.db"
    assert not nested.parent.exists()

    init_database(_settings(nested))

    assert nested.exists()


def test_init_database_adds_new_tables_to_an_existing_installation(tmp_path: Path) -> None:
    """Additive migration: old tables/rows survive, the new tables appear."""

    path = tmp_path / "old-install.db"
    old_engine = create_engine(f"sqlite:///{path}")
    try:
        legacy_tables = [
            table
            for name, table in Base.metadata.tables.items()
            if name not in {"price_alert_notifications", "investment_hypotheses"}
        ]
        Base.metadata.create_all(old_engine, tables=legacy_tables)
        with Session(old_engine) as session:
            session.add(
                PriceAlertRecord(
                    symbol="AAA",
                    operator="above",
                    target=Decimal("100"),
                    enabled=True,
                    created_at=_NOW,
                )
            )
            session.commit()
    finally:
        old_engine.dispose()

    engine = _initialized_engine(tmp_path, "old-install.db")
    tables = set(inspect(engine).get_table_names())

    assert {"price_alert_notifications", "investment_hypotheses"} <= tables
    with Session(engine) as session:
        assert session.scalars(select(PriceAlertRecord)).one().symbol == "AAA"
        assert session.scalars(select(PriceAlertNotificationRecord)).all() == []


# ---------------------------------------------------------------------------
# positions migration: additive version column
# ---------------------------------------------------------------------------
def test_legacy_positions_gain_version_column_without_rebuilding(tmp_path: Path) -> None:
    """A legacy table that already has the unique key only needs the column."""

    path = _legacy_db(tmp_path, _positions_legacy_script(unique=True, version=False))
    engine = _initialized_engine(tmp_path, path.name)

    columns = {column["name"] for column in inspect(engine).get_columns("positions")}
    with Session(engine) as session:
        row = session.scalars(select(PositionRecord)).one()

    assert "version" in columns
    assert row.id == 7
    assert row.quantity == Decimal("10.0000000000")
    assert row.version == 1


# ---------------------------------------------------------------------------
# positions migration: composite unique rebuild
# ---------------------------------------------------------------------------
def test_legacy_positions_rebuild_preserves_rows_and_enforces_unique(
    tmp_path: Path,
) -> None:
    path = _legacy_db(tmp_path, _positions_legacy_script(unique=False, version=False))
    engine = _initialized_engine(tmp_path, path.name)
    inspector = inspect(engine)

    with Session(engine) as session:
        row = session.scalars(select(PositionRecord)).one()
        assert (row.id, row.portfolio_id, row.symbol) == (7, 1, "7203")
        assert row.currency == "JPY"
        assert row.version == 1
        session.add(
            PositionRecord(
                portfolio_id=1,
                symbol="7203",
                quantity=Decimal("1"),
                currency="JPY",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

    assert any(
        set(constraint["column_names"]) == {"portfolio_id", "symbol"}
        for constraint in inspector.get_unique_constraints("positions")
    )
    assert {index["name"] for index in inspector.get_indexes("positions")} >= {
        "ix_positions_portfolio_id",
        "ix_positions_symbol",
    }


def test_legacy_positions_rebuild_keeps_existing_version_values(tmp_path: Path) -> None:
    path = _legacy_db(tmp_path, _positions_legacy_script(unique=False, version=True))
    engine = _initialized_engine(tmp_path, path.name)

    with Session(engine) as session:
        assert session.scalars(select(PositionRecord)).one().version == 4


def test_legacy_positions_duplicates_fail_closed_and_keep_data(tmp_path: Path) -> None:
    duplicate_row = "INSERT INTO positions VALUES (8, 1, '7203', 5, 1, 'JPY');\n"
    path = _legacy_db(
        tmp_path,
        _positions_legacy_script(unique=False, version=False, extra=duplicate_row),
    )

    with pytest.raises(RuntimeError) as error:
        init_database(_settings(path))

    message = str(error.value)
    assert "existing duplicates found" in message
    assert "portfolio_id=1" in message
    assert "count=2" in message

    connection = sqlite3.connect(path)
    try:
        rows = connection.execute("SELECT id FROM positions ORDER BY id").fetchall()
        uniques = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND name = ?",
            ("uq_positions_portfolio_symbol",),
        ).fetchall()
    finally:
        connection.close()

    assert rows == [(7,), (8,)]
    assert uniques == []


def test_positions_migration_is_a_noop_when_the_table_is_absent(tmp_path: Path) -> None:
    engine = make_engine(_settings(tmp_path / "empty.db"))
    try:
        dbm._ensure_positions_unique_constraint(engine)

        assert inspect(engine).get_table_names() == []
    finally:
        engine.dispose()


def test_positions_rebuild_fills_version_for_a_table_that_never_had_it(
    tmp_path: Path,
) -> None:
    """Direct call (bypassing the version step) still ends with version = 1."""

    path = _legacy_db(tmp_path, _positions_legacy_script(unique=False, version=False))
    engine = create_engine(f"sqlite:///{path}")
    try:
        dbm._ensure_positions_unique_constraint(engine)

        columns = {column["name"] for column in inspect(engine).get_columns("positions")}
        with engine.connect() as connection:
            rows = connection.execute(
                text("SELECT id, symbol, quantity, version FROM positions")
            ).fetchall()

        assert "version" in columns
        assert rows == [(7, "7203", 10, 1)]
    finally:
        engine.dispose()


def test_non_sqlite_positions_branch_is_not_exercised_by_sqlite(tmp_path: Path) -> None:
    """The raw ALTER ... ADD CONSTRAINT branch is a Postgres-only path.

    It is listed here so the coverage report is honest: on SQLite that
    statement is a syntax error. Only the rebuild path above is real for the
    production backend.
    """

    path = _legacy_db(tmp_path, _positions_legacy_script(unique=False, version=True))
    engine = create_engine(f"sqlite:///{path}")
    try:
        engine.dialect.name = "postgresql"
        with pytest.raises(OperationalError):
            dbm._ensure_positions_unique_constraint(engine)
    finally:
        engine.dispose()


def test_sqlite_does_not_enforce_the_declared_foreign_keys(tmp_path: Path) -> None:
    """Documented limitation: ``ondelete="CASCADE"`` is not active on SQLite.

    The pragmas inside the rebuild run inside an explicit transaction, where
    SQLite ignores them, and nothing enables ``PRAGMA foreign_keys`` on the
    pooled connections. Child rows are therefore removed by the ORM cascade or
    by an explicit delete in the service layer, not by the database.
    """

    engine = _initialized_engine(tmp_path)

    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA foreign_keys")).scalar() == 0


# ---------------------------------------------------------------------------
# screening_candidates migration: document_id rebuild
# ---------------------------------------------------------------------------
def test_legacy_screening_candidates_rebuilt_with_null_document_id(tmp_path: Path) -> None:
    path = _legacy_db(tmp_path, _LEGACY_SCREENING, name="legacy-screening.db")
    engine = _initialized_engine(tmp_path, path.name)

    with Session(engine) as session:
        row = session.scalars(select(ScreeningCandidateRecord)).one()

    assert row.id == 1
    assert row.document_id is None
    assert row.value == {"metric": 1}
    assert row.provenance == {"provider": "edinet"}
    assert row.run_date == date(2026, 9, 24)
    assert inspect(engine).get_indexes("screening_candidates") != []

    init_database(_settings(path))
    again = dbm._engine
    assert again is not None
    with Session(again) as session:
        assert len(session.scalars(select(ScreeningCandidateRecord)).all()) == 1


def test_screening_rebuild_allows_two_documents_but_blocks_one_duplicate(
    tmp_path: Path,
) -> None:
    path = _legacy_db(tmp_path, _LEGACY_SCREENING, name="legacy-key.db")
    engine = _initialized_engine(tmp_path, path.name)

    with Session(engine) as session:
        for document_id in ("S1", "S2"):
            session.add(
                ScreeningCandidateRecord(
                    run_date=date(2026, 9, 24),
                    source="edinet_filing",
                    code="6758",
                    signal="filing_forecast_revision",
                    document_id=document_id,
                    value={},
                    reason="r",
                    provenance={},
                    retrieved_at=_NOW,
                )
            )
        session.commit()
        assert len(session.scalars(select(ScreeningCandidateRecord)).all()) == 3

        session.add(
            ScreeningCandidateRecord(
                run_date=date(2026, 9, 24),
                source="edinet_filing",
                code="6758",
                signal="filing_forecast_revision",
                document_id="S1",
                value={},
                reason="r",
                provenance={},
                retrieved_at=_NOW,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


def test_non_sqlite_screening_path_adds_column_without_rebuilding_constraint(
    tmp_path: Path,
) -> None:
    """The ALTER-only branch for non-SQLite backends (documented gap included)."""

    path = _legacy_db(tmp_path, _LEGACY_SCREENING, name="alter-only.db")
    engine = create_engine(f"sqlite:///{path}")
    try:
        engine.dialect.name = "postgresql"
        dbm._ensure_screening_candidates_document_id(engine)

        columns = {column["name"] for column in inspect(engine).get_columns("screening_candidates")}
        uniques = inspect(engine).get_unique_constraints("screening_candidates")

        assert "document_id" in columns
        # Deliberate: the old 4-column key is not rebuilt on this backend.
        assert any(
            set(constraint["column_names"]) == {"run_date", "source", "code", "signal"}
            for constraint in uniques
        )
    finally:
        engine.dispose()


def test_non_sqlite_screening_path_swallows_an_existing_column(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A backend refusing ADD COLUMN must not abort startup."""

    engine = _initialized_engine(tmp_path, "swallow.db")
    engine.dialect.name = "postgresql"
    real_inspect = dbm.inspect
    monkeypatch.setattr(
        dbm,
        "inspect",
        lambda _engine: SimpleNamespace(get_columns=lambda _table: [{"name": "id"}]),
    )
    try:
        dbm._ensure_screening_candidates_document_id(engine)
    finally:
        monkeypatch.setattr(dbm, "inspect", real_inspect)


# ---------------------------------------------------------------------------
# URL handling and engine construction
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("postgres://u:p@h:5432/db", "postgresql+psycopg://u:p@h:5432/db"),
        ("postgresql://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
        ("postgresql+psycopg://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
        ("sqlite:///./data/app.db", "sqlite:///./data/app.db"),
        ("sqlite:///:memory:", "sqlite:///:memory:"),
        ("", ""),
    ],
)
def test_normalize_database_url(url: str, expected: str) -> None:
    assert dbm._normalize_database_url(url) == expected


def test_make_engine_normalizes_postgres_url_without_connecting() -> None:
    engine = make_engine(Settings(database_url="postgres://u:p@localhost:5432/app"))

    assert engine.url.drivername == "postgresql+psycopg"
    assert engine.url.database == "app"
    engine.dispose()


def test_make_engine_sqlite_url_is_usable_from_another_thread(tmp_path: Path) -> None:
    """``check_same_thread=False`` is what lets FastAPI threadpool workers share it."""

    path = tmp_path / "thread.db"
    engine = make_engine(_settings(path))
    init_database(_settings(path))
    try:
        with Session(dbm._engine) as session:
            session.add(WatchlistRecord(name="Main", created_at=_NOW, updated_at=_NOW))
            session.commit()

        observed: dict[str, Any] = {}

        def query() -> None:
            try:
                with engine.connect() as connection:
                    observed["count"] = connection.execute(
                        text("SELECT COUNT(*) FROM watchlists")
                    ).scalar()
            except Exception as error:  # pragma: no cover - failure diagnostic only
                observed["error"] = repr(error)

        worker = threading.Thread(target=query)
        worker.start()
        worker.join(timeout=10)

        assert observed == {"count": 1}
    finally:
        engine.dispose()


def test_make_engine_creates_parent_directory_for_sqlite(tmp_path: Path) -> None:
    target = tmp_path / "made" / "by" / "engine.db"
    engine = make_engine(_settings(target))
    try:
        assert target.parent.is_dir()
    finally:
        engine.dispose()


@pytest.mark.parametrize("url", ["sqlite:///:memory:", "sqlite://", "sqlite:///relative.db"])
def test_ensure_sqlite_parent_leaves_memory_and_relative_urls_alone(
    url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    dbm._ensure_sqlite_parent(url)

    assert list(tmp_path.iterdir()) == []


def test_make_engine_falls_back_to_settings_when_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "from-env.db"
    monkeypatch.setenv("YOWAYOWA_DATABASE_URL", f"sqlite:///{target}")
    get_settings.cache_clear()
    try:
        engine = make_engine()
        try:
            assert engine.url.database is not None
            assert engine.url.database.endswith("from-env.db")
        finally:
            engine.dispose()
    finally:
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# session / global state boundaries
# ---------------------------------------------------------------------------
def test_get_session_auto_initializes_after_dispose(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "auto.db"
    monkeypatch.setenv("YOWAYOWA_DATABASE_URL", f"sqlite:///{target}")
    get_settings.cache_clear()
    dispose_database()
    assert dbm._engine is None and dbm._SessionLocal is None

    with get_session() as session:
        assert session.scalars(select(WatchlistRecord)).all() == []

    assert target.exists()
    assert dbm._engine is not None
    get_settings.cache_clear()


def test_get_session_reuses_one_engine_but_returns_distinct_sessions(tmp_path: Path) -> None:
    engine = _initialized_engine(tmp_path)

    first = get_session()
    second = get_session()
    try:
        assert first is not second
        assert first.get_bind() is engine
        assert second.get_bind() is engine
        assert dbm._engine is engine
    finally:
        first.close()
        second.close()


def test_commit_is_visible_to_a_later_session_and_rollback_is_not(tmp_path: Path) -> None:
    engine = _initialized_engine(tmp_path)

    with Session(engine) as session:
        session.add(WatchlistRecord(name="Kept", created_at=_NOW, updated_at=_NOW))
        session.add(WatchlistRecord(name="Dropped", created_at=_NOW, updated_at=_NOW))
        session.flush()
        session.rollback()

    with Session(engine) as session:
        assert session.scalars(select(WatchlistRecord)).all() == []
        session.add(WatchlistRecord(name="Kept", created_at=_NOW, updated_at=_NOW))
        session.commit()

    with Session(engine) as session:
        assert [row.name for row in session.scalars(select(WatchlistRecord)).all()] == ["Kept"]


def test_engine_begin_rolls_back_on_exception(tmp_path: Path) -> None:
    engine = _initialized_engine(tmp_path)

    with pytest.raises(RuntimeError), engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO watchlists (name, created_at, updated_at) VALUES "
                "('tx', '2026-10-23 00:00:00', '2026-10-23 00:00:00')"
            )
        )
        raise RuntimeError("boom")

    with Session(engine) as session:
        assert session.scalars(select(WatchlistRecord)).all() == []


def test_dispose_database_clears_state_and_is_safe_twice(tmp_path: Path) -> None:
    old_engine = _initialized_engine(tmp_path)

    dispose_database()
    dispose_database()

    assert dbm._engine is None
    assert dbm._SessionLocal is None

    # Re-initialising after dispose builds a brand-new engine (no stale pool).
    init_database(_settings(tmp_path / "app.db"))
    assert dbm._engine is not None
    assert dbm._engine is not old_engine


def test_utcnow_returns_timezone_aware_utc() -> None:
    now = utcnow()

    assert now.tzinfo is UTC
    assert now.utcoffset() is not None
    assert abs((now - datetime.now(UTC)).total_seconds()) < 5


# ---------------------------------------------------------------------------
# ORM boundaries: defaults, uniqueness, cascade
# ---------------------------------------------------------------------------
def test_get_or_create_default_watchlist_is_idempotent(tmp_path: Path) -> None:
    engine = _initialized_engine(tmp_path)

    with Session(engine, expire_on_commit=False) as session:
        first = get_or_create_default_watchlist(session)
        second = get_or_create_default_watchlist(session)
        session.commit()

        assert first.id == second.id
        assert first.name == "Main"
        assert first.created_at is not None
        assert len(session.scalars(select(WatchlistRecord)).all()) == 1


def test_watchlist_name_is_unique(tmp_path: Path) -> None:
    engine = _initialized_engine(tmp_path)

    with Session(engine) as session:
        session.add(WatchlistRecord(name="Main", created_at=_NOW, updated_at=_NOW))
        session.commit()
        session.add(WatchlistRecord(name="Main", created_at=_NOW, updated_at=_NOW))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


def test_deleting_a_watchlist_cascades_to_its_items(tmp_path: Path) -> None:
    engine = _initialized_engine(tmp_path)

    with Session(engine) as session:
        watchlist = WatchlistRecord(name="Main", created_at=_NOW, updated_at=_NOW)
        session.add(watchlist)
        session.flush()
        session.add(WatchlistItemRecord(watchlist_id=watchlist.id, symbol="7203"))
        session.commit()

        session.delete(session.get(WatchlistRecord, watchlist.id))
        session.commit()

        assert session.scalars(select(WatchlistItemRecord)).all() == []


def test_deleting_a_portfolio_cascades_to_positions_and_snapshots(tmp_path: Path) -> None:
    engine = _initialized_engine(tmp_path)

    with Session(engine) as session:
        portfolio = PortfolioRecord(name="Main", created_at=_NOW, updated_at=_NOW)
        session.add(portfolio)
        session.flush()
        session.add(
            PositionRecord(
                portfolio_id=portfolio.id,
                symbol="7203",
                quantity=Decimal("1"),
                currency="JPY",
            )
        )
        session.add(
            PortfolioSnapshotRecord(
                portfolio_id=portfolio.id,
                net_market_value=Decimal("1"),
                gross_market_value=Decimal("1"),
                unrealized_pnl=Decimal("0"),
                day_pnl=Decimal("0"),
                captured_at=_NOW,
            )
        )
        session.commit()

        session.delete(session.get(PortfolioRecord, portfolio.id))
        session.commit()

        assert session.scalars(select(PositionRecord)).all() == []
        assert session.scalars(select(PortfolioSnapshotRecord)).all() == []


def test_broker_execution_application_is_unique_per_portfolio_market_execution(
    tmp_path: Path,
) -> None:
    engine = _initialized_engine(tmp_path)

    def application(portfolio_id: int, execution_id: str) -> BrokerExecutionApplicationRecord:
        return BrokerExecutionApplicationRecord(
            portfolio_id=portfolio_id,
            market="JP",
            execution_id=execution_id,
            execution_fingerprint="f" * 64,
            preview_id="p" * 64,
            applied_at=_NOW,
        )

    with Session(engine) as session:
        session.add(PortfolioRecord(name="A", created_at=_NOW, updated_at=_NOW))
        session.commit()
        session.add(application(1, "E1"))
        session.commit()

        session.add(application(1, "E1"))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        session.add(application(1, "E2"))
        session.commit()
        assert len(session.scalars(select(BrokerExecutionApplicationRecord)).all()) == 2


def test_price_alert_notification_is_unique_per_trigger(tmp_path: Path) -> None:
    engine = _initialized_engine(tmp_path)

    def notification() -> PriceAlertNotificationRecord:
        return PriceAlertNotificationRecord(
            alert_id=1,
            alert_created_at=_NOW,
            symbol="AAA",
            operator="above",
            target=Decimal("100"),
            triggered_price=Decimal("101"),
            triggered_at=_NOW,
            provenance={"provider": "yahoo"},
            created_at=_NOW,
        )

    with Session(engine) as session:
        session.add(notification())
        session.commit()
        session.add(notification())
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


def test_price_alert_notification_outlives_its_alert_row(tmp_path: Path) -> None:
    """The inbox deliberately has no foreign key to the rule it came from."""

    engine = _initialized_engine(tmp_path)

    with Session(engine) as session:
        session.add(
            PriceAlertRecord(symbol="AAA", operator="above", target=Decimal("100"), created_at=_NOW)
        )
        session.commit()
        session.add(
            PriceAlertNotificationRecord(
                alert_id=1,
                alert_created_at=_NOW,
                symbol="AAA",
                operator="above",
                target=Decimal("100"),
                triggered_price=Decimal("101"),
                triggered_at=_NOW,
                provenance={},
                created_at=_NOW,
            )
        )
        session.commit()

        session.delete(session.get(PriceAlertRecord, 1))
        session.commit()

        surviving = session.scalars(select(PriceAlertNotificationRecord)).one()
        assert surviving.alert_id == 1
        assert surviving.acknowledged_at is None


def test_research_brief_is_unique_per_run_date(tmp_path: Path) -> None:
    engine = _initialized_engine(tmp_path)

    with Session(engine) as session:
        session.add(
            ResearchBriefRecord(
                run_date=date(2026, 10, 23), payload={}, provenance={}, generated_at=_NOW
            )
        )
        session.commit()
        session.add(
            ResearchBriefRecord(
                run_date=date(2026, 10, 23), payload={}, provenance={}, generated_at=_NOW
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


def test_events_research_presets_jpx_margin_and_hypotheses_round_trip(
    tmp_path: Path,
) -> None:
    """Every remaining declared table accepts and returns a real row."""

    engine = _initialized_engine(tmp_path)

    with Session(engine) as session:
        session.add(
            EventSubscriptionRecord(
                scope="watchlist",
                scope_id=1,
                event_types="earnings,dividend",
                lead_days=7,
                created_at=_NOW,
            )
        )
        session.add(
            ResearchPresetRecord(
                name="Kiyohara",
                kind="strategy",
                payload={"region": "JP"},
                created_at=_NOW,
                updated_at=_NOW,
            )
        )
        session.add(
            JpxMarginBalanceRecord(
                application_date=date(2026, 10, 23),
                code="7203",
                company_name=None,
                short_total=10,
                long_total=20,
                short_negotiable=1,
                short_standardized=2,
                long_negotiable=3,
                long_standardized=4,
                retrieved_at=_NOW,
            )
        )
        session.add(
            HypothesisRecord(
                hypothesis="net cash exceeds market cap",
                falsification_criteria=["net cash below zero", "buyback cancelled"],
                symbol="7203",
                evidence_links=[{"kind": "filing", "doc_id": "S1"}],
                created_at=_NOW,
            )
        )
        session.commit()

        subscription = session.scalars(select(EventSubscriptionRecord)).one()
        session.add(
            EventInboxRecord(
                subscription_id=subscription.id,
                event_key="earnings-2026-11-01",
                event_type="earnings",
                subtype="quarterly",
                symbol="7203",
                title="Q2 earnings",
                starts_at=_NOW,
                created_at=_NOW,
            )
        )
        session.commit()

        inbox = session.scalars(select(EventInboxRecord)).one()
        preset = session.scalars(select(ResearchPresetRecord)).one()
        margin = session.scalars(select(JpxMarginBalanceRecord)).one()
        hypothesis = session.scalars(select(HypothesisRecord)).one()

    assert inbox.ends_at is None and inbox.acknowledged_at is None
    assert preset.payload == {"region": "JP"}
    assert margin.company_name is None
    assert margin.short_total_value is None
    assert hypothesis.falsification_criteria == ["net cash below zero", "buyback cancelled"]
    assert hypothesis.evidence_links == [{"kind": "filing", "doc_id": "S1"}]


def test_event_inbox_is_unique_per_subscription_and_event_key(tmp_path: Path) -> None:
    engine = _initialized_engine(tmp_path)

    def inbox(subscription_id: int, event_key: str) -> EventInboxRecord:
        return EventInboxRecord(
            subscription_id=subscription_id,
            event_key=event_key,
            event_type="earnings",
            subtype="quarterly",
            symbol="7203",
            title="Q2 earnings",
            starts_at=_NOW,
            created_at=_NOW,
        )

    with Session(engine) as session:
        session.add(inbox(1, "k1"))
        session.commit()
        session.add(inbox(1, "k1"))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        session.add(inbox(1, "k2"))
        session.commit()
        assert len(session.scalars(select(EventInboxRecord)).all()) == 2


def test_research_presets_and_jpx_margin_rows_are_unique_per_their_keys(
    tmp_path: Path,
) -> None:
    engine = _initialized_engine(tmp_path)

    def preset() -> ResearchPresetRecord:
        return ResearchPresetRecord(
            name="Kiyohara", kind="strategy", payload={}, created_at=_NOW, updated_at=_NOW
        )

    def margin() -> JpxMarginBalanceRecord:
        return JpxMarginBalanceRecord(
            application_date=date(2026, 10, 23),
            code="7203",
            short_total=1,
            long_total=2,
            short_negotiable=0,
            short_standardized=1,
            long_negotiable=0,
            long_standardized=2,
            retrieved_at=_NOW,
        )

    with Session(engine) as session:
        session.add(preset())
        session.add(margin())
        session.commit()

        session.add(preset())
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        session.add(margin())
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


# ---------------------------------------------------------------------------
# storage semantics the rest of the codebase relies on
# ---------------------------------------------------------------------------
def test_numeric_columns_round_trip_decimals_at_ten_places(tmp_path: Path) -> None:
    engine = _initialized_engine(tmp_path)

    with Session(engine) as session:
        session.add(
            StrategyResearchSnapshotRecord(
                strategy_id="kiyohara",
                scoring_version="v1",
                region="JP",
                symbol="7203",
                score=Decimal("1.2345"),
                confidence=Decimal("0.5000"),
                evaluation={"net_cash": "1"},
                captured_on=date(2026, 10, 23),
                captured_at=_NOW,
            )
        )
        session.commit()

        row = session.scalars(select(StrategyResearchSnapshotRecord)).one()
        assert row.score == Decimal("1.2345")
        assert row.confidence == Decimal("0.5000")
        assert row.evaluation == {"net_cash": "1"}
        assert row.captured_on == date(2026, 10, 23)


def test_numeric_large_values_lose_precision_beyond_float64(tmp_path: Path) -> None:
    """Documented limitation: SQLite stores NUMERIC as REAL (float64).

    ``Numeric(28, 10)`` promises 28 digits but the SQLite backend keeps only
    ~15-17 significant digits, so a 24-digit quantity comes back silently
    altered. Callers that need exactness at that magnitude must not rely on
    this column. The assertion pins the current, measured behaviour so a
    backend change is noticed rather than assumed.
    """

    engine = _initialized_engine(tmp_path)
    written = Decimal("12345678901234.1234567890")

    with Session(engine) as session:
        session.add(
            PositionRecord(
                portfolio_id=1,
                symbol="7203",
                quantity=written,
                currency="JPY",
            )
        )
        session.commit()

        read_back = session.scalars(select(PositionRecord)).one().quantity

    assert read_back != written
    assert read_back == Decimal("12345678901234.1230468750")


def test_datetime_columns_come_back_naive_utc_on_sqlite(tmp_path: Path) -> None:
    """Documented limitation: SQLite drops tzinfo, so reads are naive.

    Rows are written with ``utcnow()`` (aware); a value loaded into a fresh
    session has no tzinfo, so comparing it with an aware datetime raises
    ``TypeError``. Consumers must re-attach UTC (or use SQL-side comparisons).
    """

    engine = _initialized_engine(tmp_path)
    with Session(engine) as session:
        session.add(
            PriceAlertRecord(symbol="AAA", operator="above", target=Decimal("1"), created_at=_NOW)
        )
        session.commit()

    with Session(engine) as session:
        read_back = session.scalars(select(PriceAlertRecord)).one().created_at

    assert read_back.tzinfo is None
    assert read_back == datetime(2026, 10, 23, 3, 0)
    with pytest.raises(TypeError):
        _ = read_back > utcnow()


def test_boolean_and_json_defaults_are_applied_on_insert(tmp_path: Path) -> None:
    engine = _initialized_engine(tmp_path)

    with Session(engine) as session:
        session.add(
            PriceAlertRecord(symbol="AAA", operator="above", target=Decimal("1"), created_at=_NOW)
        )
        session.add(
            CreditMarginWeeklyRecord(
                as_of_date=date(2026, 10, 23),
                code="7203",
                short_total=1,
                long_total=2,
                provider="yahoo",
                retrieved_at=_NOW,
            )
        )
        session.commit()

        alert = session.scalars(select(PriceAlertRecord)).one()
        margin = session.scalars(select(CreditMarginWeeklyRecord)).one()

    assert alert.enabled is True
    assert alert.triggered_at is None
    assert alert.last_price is None
    assert margin.notes == []
    assert margin.source_url is None
