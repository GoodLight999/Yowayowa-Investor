from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from yowayowa.domain import Provenance

JpxMarginSignalKind = Literal[
    "crowded-long",
    "crowded-short",
    "long-unwind",
    "short-cover",
    "borrow-stress",
    "flow-buy",
    "flow-sell",
    "buy-flow-divergence",
    "sell-flow-divergence",
    "squeeze-watch",
    "watch-flags",
]


class JpxMarginDailyDetail(BaseModel):
    application_date: date
    code: str
    issue: str
    section: str
    margin_marker: str
    unit_marker: str | None = None
    isin: str
    short_source_change: int | None = None
    long_source_change: int | None = None
    short_listed_ratio_pct: float | None = None
    long_listed_ratio_pct: float | None = None
    short_negotiable_source_change: int | None = None
    short_standardized_source_change: int | None = None
    long_negotiable_source_change: int | None = None
    long_standardized_source_change: int | None = None
    short_value_source_change: int | None = None
    long_value_source_change: int | None = None
    short_negotiable_value_source_change: int | None = None
    short_standardized_value_source_change: int | None = None
    long_negotiable_value_source_change: int | None = None
    long_standardized_value_source_change: int | None = None
    published_at: datetime
    retrieved_at: datetime
    source_sha256: str
    provenance: Provenance


class JpxMarginWatch(BaseModel):
    application_date: date
    code: str
    unit_marker: str | None = None
    primary_status: str | None = None
    jsf_status: str | None = None
    company_name: str | None = None
    section: str | None = None
    margin_marker: str | None = None
    isin: str
    short_total: int
    short_change: int | None = None
    short_listed_ratio_pct: float | None = None
    long_total: int
    long_change: int | None = None
    long_listed_ratio_pct: float | None = None
    sale_purchase_ratio_pct: float | None = None
    short_negotiable: int
    short_negotiable_change: int | None = None
    short_standardized: int
    short_standardized_change: int | None = None
    long_negotiable: int
    long_negotiable_change: int | None = None
    long_standardized: int
    long_standardized_change: int | None = None
    published_at: datetime
    retrieved_at: datetime
    source_sha256: str
    provenance: Provenance


class JpxPremiumCharge(BaseModel):
    trade_date: date
    source_code: str
    resolved_jpx_code: str | None = None
    company_name: str | None = None
    exchange: str | None = None
    over_lent_shares: int
    maximum_premium_charge: float
    premium_charge: float | None = None
    published_at: datetime
    retrieved_at: datetime
    source_sha256: str
    provenance: Provenance


class JpxMarginFlow(BaseModel):
    trade_date: date
    code: str
    company_name: str
    section: str
    margin_marker: str
    status_marker: str | None = None
    new_sales_ratio_pct: float | None = None
    new_purchase_ratio_pct: float | None = None
    sales_star: bool = False
    purchase_star: bool = False
    published_at: datetime
    retrieved_at: datetime
    source_sha256: str
    provenance: Provenance


class JpxPublicIngestResult(BaseModel):
    kind: Literal["balance", "watch", "premium", "flow"]
    row_count: int
    as_of_dates: list[date] = Field(default_factory=list)
    source_url: str
    source_sha256: str
    cached_path: str | None = None


class JpxMarginSignal(BaseModel):
    signal: JpxMarginSignalKind
    as_of_date: date
    code: str
    company_name: str | None = None
    metrics: dict[str, int | float | str | bool | None] = Field(default_factory=dict)
    reason: str
    available_at: datetime
    provenance: list[Provenance] = Field(default_factory=list)
