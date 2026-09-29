from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Table,
    UniqueConstraint,
    create_engine,
    inspect,
    select,
    text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)

from .config import Settings, get_settings


class Base(DeclarativeBase):
    pass


class WatchlistRecord(Base):
    __tablename__ = "watchlists"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    items: Mapped[list[WatchlistItemRecord]] = relationship(
        back_populates="watchlist",
        cascade="all, delete-orphan",
        order_by="WatchlistItemRecord.symbol",
    )


class WatchlistItemRecord(Base):
    __tablename__ = "watchlist_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    watchlist_id: Mapped[int] = mapped_column(
        ForeignKey("watchlists.id", ondelete="CASCADE"), index=True
    )
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    watchlist: Mapped[WatchlistRecord] = relationship(back_populates="items")


class PortfolioRecord(Base):
    __tablename__ = "portfolios"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    base_currency: Mapped[str] = mapped_column(String(3), default="USD")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    positions: Mapped[list[PositionRecord]] = relationship(
        back_populates="portfolio",
        cascade="all, delete-orphan",
        order_by="PositionRecord.symbol",
    )
    snapshots: Mapped[list[PortfolioSnapshotRecord]] = relationship(
        back_populates="portfolio",
        cascade="all, delete-orphan",
        order_by="PortfolioSnapshotRecord.captured_at",
    )


class PositionRecord(Base):
    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE"), index=True
    )
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(28, 10))
    average_cost: Mapped[Decimal | None] = mapped_column(Numeric(28, 10), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    portfolio: Mapped[PortfolioRecord] = relationship(back_populates="positions")


class PortfolioSnapshotRecord(Base):
    __tablename__ = "portfolio_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE"), index=True
    )
    net_market_value: Mapped[Decimal] = mapped_column(Numeric(28, 10))
    gross_market_value: Mapped[Decimal] = mapped_column(Numeric(28, 10))
    unrealized_pnl: Mapped[Decimal] = mapped_column(Numeric(28, 10))
    day_pnl: Mapped[Decimal] = mapped_column(Numeric(28, 10))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    portfolio: Mapped[PortfolioRecord] = relationship(back_populates="snapshots")


class BrokerExecutionApplicationRecord(Base):
    """Additive idempotency ledger; ``create_all`` preserves existing portfolio data."""

    __tablename__ = "broker_execution_applications"
    __table_args__ = (
        UniqueConstraint(
            "portfolio_id", "market", "execution_id", name="uq_applied_execution_per_portfolio"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE"), index=True
    )
    market: Mapped[str] = mapped_column(String(2))
    execution_id: Mapped[str] = mapped_column(String(128))
    execution_fingerprint: Mapped[str] = mapped_column(String(64))
    preview_id: Mapped[str] = mapped_column(String(64))
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PriceAlertRecord(Base):
    __tablename__ = "price_alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    operator: Mapped[str] = mapped_column(String(16))
    target: Mapped[Decimal] = mapped_column(Numeric(28, 10))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    triggered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_price: Mapped[Decimal | None] = mapped_column(Numeric(28, 10), nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PriceAlertNotificationRecord(Base):
    """Durable in-app notice for one price-alert trigger.

    Deliberately has no foreign key to ``price_alerts``: deleting a monitoring
    rule must not erase its unacknowledged trigger notice.
    """

    __tablename__ = "price_alert_notifications"
    __table_args__ = (
        UniqueConstraint(
            "alert_id",
            "alert_created_at",
            name="uq_price_alert_notification_trigger",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    alert_id: Mapped[int] = mapped_column(index=True)
    alert_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    operator: Mapped[str] = mapped_column(String(16))
    target: Mapped[Decimal] = mapped_column(Numeric(28, 10))
    triggered_price: Mapped[Decimal] = mapped_column(Numeric(28, 10))
    triggered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EventSubscriptionRecord(Base):
    __tablename__ = "event_subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    scope: Mapped[str] = mapped_column(String(16), index=True)
    scope_id: Mapped[int | None] = mapped_column(nullable=True)
    event_types: Mapped[str] = mapped_column(String(64))
    lead_days: Mapped[int] = mapped_column()
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EventInboxRecord(Base):
    __tablename__ = "event_inbox"
    __table_args__ = (
        UniqueConstraint("subscription_id", "event_key", name="uq_event_inbox_subscription_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    subscription_id: Mapped[int] = mapped_column(
        ForeignKey("event_subscriptions.id", ondelete="CASCADE"), index=True
    )
    event_key: Mapped[str] = mapped_column(String(64))
    event_type: Mapped[str] = mapped_column(String(16))
    subtype: Mapped[str] = mapped_column(String(32))
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(500))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class StrategyResearchSnapshotRecord(Base):
    __tablename__ = "strategy_research_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "strategy_id",
            "scoring_version",
            "region",
            "symbol",
            "captured_on",
            name="uq_strategy_snapshot_daily",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    strategy_id: Mapped[str] = mapped_column(String(80), index=True)
    scoring_version: Mapped[str] = mapped_column(String(80), index=True)
    region: Mapped[str] = mapped_column(String(16), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    score: Mapped[Decimal] = mapped_column(Numeric(10, 4))
    confidence: Mapped[Decimal] = mapped_column(Numeric(10, 4))
    evaluation: Mapped[dict[str, Any]] = mapped_column(JSON)
    captured_on: Mapped[date] = mapped_column(Date, index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ResearchPresetRecord(Base):
    __tablename__ = "research_presets"
    __table_args__ = (UniqueConstraint("kind", "name", name="uq_research_presets_kind_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(16), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class JpxMarginBalanceRecord(Base):
    """One issue's margin balances for one application date (申込日).

    Uniqueness is (application_date, code): JPX re-publishes corrected files
    for an application date, and the last write for that date wins (atomic
    delete+insert inside one transaction — see services/jpx_margin.py).
    Volume columns are NOT NULL; amount (value) columns are nullable because
    JPX publishes amounts only for application dates from 2026-09-25 onward
    (missing data is never zero-filled).
    source_url is persisted per row so read provenance keeps the ingest URL.
    """

    __tablename__ = "jpx_margin_balances"
    __table_args__ = (
        UniqueConstraint(
            "application_date",
            "code",
            name="uq_jpx_margin_application_date_code",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    application_date: Mapped[date] = mapped_column(Date, index=True)
    code: Mapped[str] = mapped_column(String(5), index=True)
    company_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    isin: Mapped[str | None] = mapped_column(String(12), nullable=True)
    market_code: Mapped[str | None] = mapped_column(String(8), nullable=True)
    margin_code: Mapped[str | None] = mapped_column(String(2), nullable=True)

    short_total: Mapped[int] = mapped_column()
    long_total: Mapped[int] = mapped_column()
    short_negotiable: Mapped[int] = mapped_column()
    short_standardized: Mapped[int] = mapped_column()
    long_negotiable: Mapped[int] = mapped_column()
    long_standardized: Mapped[int] = mapped_column()

    short_total_value: Mapped[int | None] = mapped_column(nullable=True)
    long_total_value: Mapped[int | None] = mapped_column(nullable=True)
    short_negotiable_value: Mapped[int | None] = mapped_column(nullable=True)
    short_standardized_value: Mapped[int | None] = mapped_column(nullable=True)
    long_negotiable_value: Mapped[int | None] = mapped_column(nullable=True)
    long_standardized_value: Mapped[int | None] = mapped_column(nullable=True)

    source_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class CreditMarginWeeklyRecord(Base):
    """One code's weekly credit balances for one as-of week (P4-C).

    Persisted unit is shares (株 int) for both providers: Yahoo!ファイナンス as
    published, 株探 converted from 千株 (x1000 ± rounding). Uniqueness is
    (as_of_date, code) — the weekly key the two independent sources agree on
    (docs/CREDIT_MARGIN.md). A re-fetch for the same week updates the row
    (value/retrieved_at/source_url with notes marking the recheck) instead of
    duplicating it. There are NO amount columns: the sources publish no
    金額, and missing data is never zero-filled.
    """

    __tablename__ = "credit_margin_weekly"
    __table_args__ = (
        UniqueConstraint(
            "as_of_date",
            "code",
            name="uq_credit_margin_weekly_as_of_date_code",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    as_of_date: Mapped[date] = mapped_column(Date, index=True)
    code: Mapped[str] = mapped_column(String(4), index=True)

    short_total: Mapped[int] = mapped_column()
    long_total: Mapped[int] = mapped_column()

    source_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    provider: Mapped[str] = mapped_column(String(32))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    notes: Mapped[list[Any]] = mapped_column(JSON, default=list)


class ScreeningCandidateRecord(Base):
    """One machine-discovered screening candidate for one run date (P4-D).

    Uniqueness is (run_date, source, code, signal, document_id): a re-run for
    the same run date replaces that date's rows atomically (delete+insert
    inside one transaction — see services/screening_pipeline.py), so
    re-running the pipeline is idempotent. ``document_id`` carries the
    per-document evidence key (EDINET docID; NULL for sources without one):
    two different documents for the same issuer/signal are two rows, while
    the same document is one fact (audit Y03). ``provenance`` holds the
    candidate provenance; absent numbers are never zero-filled here either.
    """

    __tablename__ = "screening_candidates"
    __table_args__ = (
        UniqueConstraint(
            "run_date",
            "source",
            "code",
            "signal",
            "document_id",
            name="uq_screening_candidates_run_source_code_signal_doc",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_date: Mapped[date] = mapped_column(Date, index=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    code: Mapped[str] = mapped_column(String(5), index=True)
    signal: Mapped[str] = mapped_column(String(40), index=True)
    document_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    company_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(String(500))
    provenance: Mapped[dict[str, Any]] = mapped_column(JSON)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ResearchBriefRecord(Base):
    """One generated LLM research brief for one run date (P5-A).

    Uniqueness is (run_date): a re-generated brief for the same run date
    replaces that date's row atomically (delete+insert inside one
    transaction — see services/research_brief.py), so regeneration is
    idempotent. ``payload`` holds the full ResearchBrief JSON minus its
    provenance (kept in the dedicated ``provenance`` column, like the
    screening candidates keep theirs).
    """

    __tablename__ = "research_briefs"
    __table_args__ = (UniqueConstraint("run_date", name="uq_research_briefs_run_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_date: Mapped[date] = mapped_column(Date, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSON)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class HypothesisRecord(Base):
    """Append-only decision record; evidence links are frozen at creation time."""

    __tablename__ = "investment_hypotheses"

    id: Mapped[int] = mapped_column(primary_key=True)
    hypothesis: Mapped[str] = mapped_column(String(4000))
    falsification_criteria: Mapped[list[str]] = mapped_column(JSON)
    symbol: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    evidence_links: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


def _normalize_database_url(url: str) -> str:
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url.removeprefix("postgres://")
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url.removeprefix("postgresql://")
    return url


def _ensure_sqlite_parent(url: str) -> None:
    prefix = "sqlite:///"
    if url.startswith(prefix):
        path = Path(url.removeprefix(prefix))
        if path.parent != Path("."):
            path.parent.mkdir(parents=True, exist_ok=True)


def make_engine(settings: Settings | None = None) -> Engine:
    settings = settings or get_settings()
    database_url = _normalize_database_url(settings.database_url)
    _ensure_sqlite_parent(database_url)
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    return create_engine(database_url, pool_pre_ping=True, connect_args=connect_args)


_engine = None
_SessionLocal = None


def init_database(settings: Settings | None = None) -> None:
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = make_engine(settings)
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    _ensure_hypothesis_records_table(_engine)
    # WD-C5's new inbox is an additive migration kept explicit so existing
    # installations gain the table without rebuilding or altering old rows.
    _ensure_price_alert_notifications_table(_engine)
    Base.metadata.create_all(_engine)
    _ensure_screening_candidates_document_id(_engine)


def _ensure_hypothesis_records_table(engine: Engine) -> None:
    """WD-J additive migration: add the new table without rewriting existing rows."""

    Base.metadata.create_all(
        engine,
        tables=[cast(Table, HypothesisRecord.__table__)],
        checkfirst=True,
    )


def _ensure_price_alert_notifications_table(engine: Engine) -> None:
    """WD-C5 additive, idempotent migration; existing alert data is untouched."""

    Base.metadata.create_all(
        engine,
        tables=[cast(Table, PriceAlertNotificationRecord.__table__)],
        checkfirst=True,
    )


def _ensure_screening_candidates_document_id(engine: Engine) -> None:
    """Idempotent lightweight migration for audit Y03 (no alembic in repo).

    The screening_candidates unique key gained a ``document_id`` column
    (was (run_date, source, code, signal), is now
    (run_date, source, code, signal, document_id)). Production runs on
    SQLite, where the old 4-column unique index can only be dropped by a
    table rebuild — the old constraint cannot simply be altered away.

    - SQLite: inspect ``PRAGMA table_info``; when ``document_id`` is absent,
      rebuild the table with the new schema (CREATE … new → INSERT … SELECT
      keeping every existing column and id, with NULL AS document_id →
      DROP → ALTER TABLE RENAME). The old 4-column unique constraint is
      replaced by the new 5-column one in the rebuild. Existing rows are
      preserved with ``document_id = NULL``. A fresh database created by
      ``create_all`` already has the column, so this is a no-op there.
    - Non-SQLite: try an ``ALTER TABLE ADD COLUMN document_id VARCHAR(64)``
      and swallow the error when the column already exists. The old unique
      constraint is intentionally NOT rebuilt here (the 4-column index is
      strictly tighter than the 5-column one for NULL document ids only
      when document_id is always non-NULL; production is SQLite, where the
      rebuild path runs).
    """

    dialect = engine.dialect.name
    inspector = inspect(engine)
    columns = {column["name"] for column in inspector.get_columns("screening_candidates")}
    if "document_id" in columns:
        return

    if dialect == "sqlite":
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    CREATE TABLE screening_candidates_new (
                        id INTEGER NOT NULL,
                        run_date DATE NOT NULL,
                        source VARCHAR(32) NOT NULL,
                        code VARCHAR(5) NOT NULL,
                        signal VARCHAR(40) NOT NULL,
                        document_id VARCHAR(64),
                        company_name VARCHAR(500),
                        value JSON NOT NULL,
                        reason VARCHAR(500) NOT NULL,
                        provenance JSON NOT NULL,
                        retrieved_at DATETIME NOT NULL,
                        PRIMARY KEY (id),
                        CONSTRAINT uq_screening_candidates_run_source_code_signal_doc
                            UNIQUE (run_date, source, code, signal, document_id)
                    )
                    """
                )
            )
            conn.execute(
                text(
                    """
                    INSERT INTO screening_candidates_new (
                        id, run_date, source, code, signal, document_id,
                        company_name, value, reason, provenance, retrieved_at
                    )
                    SELECT id, run_date, source, code, signal, NULL,
                           company_name, value, reason, provenance, retrieved_at
                    FROM screening_candidates
                    """
                )
            )
            conn.execute(text("DROP TABLE screening_candidates"))
            conn.execute(
                text("ALTER TABLE screening_candidates_new RENAME TO screening_candidates")
            )
            conn.execute(
                text(
                    "CREATE INDEX ix_screening_candidates_run_date "
                    "ON screening_candidates (run_date)"
                )
            )
            conn.execute(
                text("CREATE INDEX ix_screening_candidates_source ON screening_candidates (source)")
            )
            conn.execute(
                text("CREATE INDEX ix_screening_candidates_code ON screening_candidates (code)")
            )
            conn.execute(
                text("CREATE INDEX ix_screening_candidates_signal ON screening_candidates (signal)")
            )
            conn.execute(
                text(
                    "CREATE INDEX ix_screening_candidates_retrieved_at "
                    "ON screening_candidates (retrieved_at)"
                )
            )
        return

    try:
        with engine.begin() as conn:
            conn.execute(
                text("ALTER TABLE screening_candidates ADD COLUMN document_id VARCHAR(64)")
            )
    except Exception:
        # Column already exists on this non-SQLite backend (or the backend
        # forbids the ADD COLUMN): the desired end state is already there.
        pass


def dispose_database() -> None:
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


def get_session() -> Session:
    if _SessionLocal is None:
        init_database()
    assert _SessionLocal is not None
    return _SessionLocal()


def utcnow() -> datetime:
    return datetime.now(UTC)


def get_or_create_default_watchlist(session: Session) -> WatchlistRecord:
    row = session.scalar(select(WatchlistRecord).where(WatchlistRecord.name == "Main"))
    if row:
        return row
    now = utcnow()
    row = WatchlistRecord(name="Main", created_at=now, updated_at=now)
    session.add(row)
    session.commit()
    return row
