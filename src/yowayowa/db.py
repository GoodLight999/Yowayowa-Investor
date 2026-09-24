from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    UniqueConstraint,
    create_engine,
    select,
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
    Base.metadata.create_all(_engine)


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
