from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from yowayowa.domain import Provenance


class EstatTableSummary(BaseModel):
    stats_data_id: str
    stats_code: str | None = None
    stat_name: str
    gov_org_code: str | None = None
    gov_org: str | None = None
    statistics_name: str | None = None
    title: str
    table_no: str | None = None
    cycle: str | None = None
    survey_date: str | None = None
    open_date: date | None = None
    collect_area: str | None = None
    main_category_code: str | None = None
    main_category: str | None = None
    sub_category_code: str | None = None
    sub_category: str | None = None
    total_number: int | None = None
    updated_at: datetime | date | None = None


class EstatTableSearch(BaseModel):
    query: str
    tables: list[EstatTableSummary]
    matched_count: int
    next_key: int | None = None
    provenance: Provenance


class EstatClassItem(BaseModel):
    code: str
    name: str
    level: int | None = None
    parent_code: str | None = None
    unit: str | None = None


class EstatDimension(BaseModel):
    id: str
    name: str
    items: list[EstatClassItem]


class EstatMetadata(BaseModel):
    stats_data_id: str
    table: EstatTableSummary | None = None
    dimensions: list[EstatDimension]
    provenance: Provenance


class EstatValue(BaseModel):
    value: str
    numeric_value: Decimal | None = None
    unit: str | None = None
    annotation: str | None = None
    dimensions: dict[str, str] = Field(default_factory=dict)


class EstatData(BaseModel):
    stats_data_id: str
    total_number: int
    from_number: int | None = None
    to_number: int | None = None
    next_key: int | None = None
    table: EstatTableSummary | None = None
    dimensions: list[EstatDimension]
    values: list[EstatValue]
    notes: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)
    provenance: Provenance
