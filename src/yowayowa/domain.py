from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class LicenseClass(StrEnum):
    OFFICIAL_PUBLIC = "official_public"
    PERSONAL_ONLY = "personal_only"
    USER_KEY = "user_key"
    LICENSED_REDISTRIBUTABLE = "licensed_redistributable"


class Provenance(BaseModel):
    provider: str
    source: str
    source_url: str | None = None
    license_class: LicenseClass
    retrieved_at: datetime
    as_of: datetime | date | None = None
    notes: list[str] = Field(default_factory=list)


class Instrument(BaseModel):
    symbol: str
    name: str
    exchange: str | None = None
    instrument_type: str = "equity"
    currency: str | None = None
    cik: str | None = None


class PriceBar(BaseModel):
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None


class IndicatorSeries(BaseModel):
    name: str
    parameters: dict[str, Any]
    points: list[tuple[datetime, float | None]]
    pane: str = "price"
    render: Literal["line", "histogram"] = "line"
    reference_lines: list[float] = Field(default_factory=list)


class MarketHistory(BaseModel):
    symbol: str
    interval: str
    bars: list[PriceBar]
    indicators: list[IndicatorSeries] = Field(default_factory=list)
    provenance: Provenance


class MarketQuote(BaseModel):
    symbol: str
    price: float
    previous_close: float | None = None
    as_of: datetime


class MarketQuoteBatch(BaseModel):
    quotes: dict[str, MarketQuote]
    unavailable_symbols: list[str] = Field(default_factory=list)
    provenance: Provenance


class MarketOverviewItem(BaseModel):
    symbol: str
    label: str
    category: str
    unit: str
    value: float
    change_1d: float | None = None
    change_1m: float | None = None
    change_3m: float | None = None
    change_1y: float | None = None
    sparkline: list[tuple[datetime, float]] = Field(default_factory=list)
    as_of: datetime


class MarketOverview(BaseModel):
    items: list[MarketOverviewItem]
    unavailable_symbols: list[str] = Field(default_factory=list)
    provenance: Provenance


class MetricPoint(BaseModel):
    period_start: date | None = None
    period_end: date
    fiscal_year: int | None = None
    fiscal_period: str | None = None
    value: Decimal
    unit: str
    accession: str | None = None
    filed: date | None = None
    form: str | None = None


class MetricSeries(BaseModel):
    key: str
    label: str
    points: list[MetricPoint]


class Fundamentals(BaseModel):
    symbol: str
    cik: str
    company_name: str
    metrics: dict[str, MetricSeries]
    provenance: Provenance


class ValuationSnapshot(BaseModel):
    symbol: str
    company_name: str
    price: float
    shares_diluted: float | None = None
    market_cap: float | None = None
    annual_period_end: date | None = None
    metrics: dict[str, float | None]
    provenance: list[Provenance]
    evaluated_at: datetime


class NewsItem(BaseModel):
    id: str
    title: str
    publisher: str | None = None
    published_at: datetime | None = None
    url: str | None = None
    summary: str | None = None
    symbol: str | None = None


class NewsFeed(BaseModel):
    query: str
    items: list[NewsItem]
    provenance: Provenance


class CalendarEvent(BaseModel):
    event_type: Literal["earnings", "economic", "ipo", "split", "ticker"]
    starts_at: datetime | date | None = None
    title: str
    symbol: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class EventCalendar(BaseModel):
    start: date
    end: date
    events: list[CalendarEvent]
    provenance: Provenance


class WatchlistCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class Watchlist(BaseModel):
    id: int
    name: str
    symbols: list[str]
    created_at: datetime
    updated_at: datetime


class PortfolioCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    base_currency: str = Field(default="USD", min_length=3, max_length=3)


class PositionUpsert(BaseModel):
    symbol: str
    quantity: Decimal
    average_cost: Decimal | None = None
    currency: str = "USD"


class PositionBulkUpsert(BaseModel):
    positions: list[PositionUpsert] = Field(min_length=1, max_length=1000)
    replace: bool = False


class Position(BaseModel):
    symbol: str
    quantity: Decimal
    average_cost: Decimal | None = None
    currency: str


class Portfolio(BaseModel):
    id: int
    name: str
    base_currency: str
    positions: list[Position]
    created_at: datetime
    updated_at: datetime


class PositionAnalytics(BaseModel):
    symbol: str
    quantity: Decimal
    currency: str
    average_cost: Decimal | None = None
    price: float
    previous_close: float | None = None
    fx_to_base: float
    previous_fx_to_base: float | None = None
    market_value_base: float
    cost_basis_base: float | None = None
    unrealized_pnl_base: float | None = None
    unrealized_pnl_pct: float | None = None
    day_pnl_base: float | None = None
    day_change_pct: float | None = None
    weight: float = 0
    as_of: datetime


class CurrencyExposure(BaseModel):
    currency: str
    market_value_base: float
    weight: float


class PortfolioAnalytics(BaseModel):
    portfolio_id: int
    name: str
    base_currency: str
    net_market_value: float
    gross_market_value: float
    known_cost_basis: float
    known_cost_market_value: float
    unrealized_pnl: float
    unrealized_pnl_pct: float | None = None
    day_pnl: float
    day_change_pct: float | None = None
    largest_position_weight: float
    concentration_hhi: float
    positions: list[PositionAnalytics]
    currency_exposure: list[CurrencyExposure]
    unavailable_symbols: list[str] = Field(default_factory=list)
    provenance: Provenance
    evaluated_at: datetime


class PortfolioSnapshot(BaseModel):
    id: int
    portfolio_id: int
    net_market_value: float
    gross_market_value: float
    unrealized_pnl: float
    day_pnl: float
    captured_at: datetime


class AlertOperator(StrEnum):
    ABOVE = "above"
    BELOW = "below"


class PriceAlertCreate(BaseModel):
    symbol: str
    operator: AlertOperator
    target: Decimal = Field(gt=0)


class PriceAlert(BaseModel):
    id: int
    symbol: str
    operator: AlertOperator
    target: Decimal
    enabled: bool
    triggered_at: datetime | None = None
    last_price: Decimal | None = None
    last_checked_at: datetime | None = None
    created_at: datetime


class AlertEvaluation(BaseModel):
    alerts: list[PriceAlert]
    provenance: Provenance
    evaluated_at: datetime


class FilterOperator(StrEnum):
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    EQ = "eq"
    BETWEEN = "between"


class ScreenFilter(BaseModel):
    metric: str
    operator: FilterOperator
    value: float | list[float]


class ScreenRequest(BaseModel):
    symbols: list[str] = Field(min_length=1, max_length=250)
    filters: list[ScreenFilter] = Field(default_factory=list)


class ScreenRow(BaseModel):
    symbol: str
    metrics: dict[str, float | None]
    matched: bool
    failures: list[str] = Field(default_factory=list)


class ScreenResponse(BaseModel):
    rows: list[ScreenRow]
    evaluated_at: datetime


class ComparisonRequest(BaseModel):
    symbols: list[str] = Field(min_length=2, max_length=20)
    metrics: list[str] = Field(default_factory=list, max_length=20)


class ComparisonMetric(BaseModel):
    key: str
    label: str
    display: Literal["percent", "ratio", "compact", "number"] = "number"
    higher_is_better: bool | None = None
    default_selected: bool = False


class ComparisonRow(BaseModel):
    symbol: str
    company_name: str
    metrics: dict[str, float | None]
    provenance: Provenance


class ComparisonResponse(BaseModel):
    metrics: list[ComparisonMetric]
    rows: list[ComparisonRow]
    evaluated_at: datetime


class OperationKind(StrEnum):
    WATCHLIST_ADD = "watchlist.add"
    WATCHLIST_REMOVE = "watchlist.remove"
    CHART_SET_INDICATORS = "chart.set_indicators"
    SCREEN_SET_FILTERS = "screen.set_filters"
    COMPARE_SYMBOLS = "compare.symbols"


class Operation(BaseModel):
    kind: OperationKind
    arguments: dict[str, Any]


class OperationPlan(BaseModel):
    summary: str
    operations: list[Operation]
    confidence: float = Field(ge=0, le=1)
    requires_confirmation: bool = False
    source: Literal["deterministic", "llm"] = "deterministic"
