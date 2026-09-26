from __future__ import annotations

from pydantic import BaseModel, Field

from yowayowa.domain import Provenance


class BeaNipaCatalogItem(BaseModel):
    table_name: str
    title: str
    category: str
    default_frequency: str = "Q"
    default_line_number: int = 1


class BeaNipaCatalog(BaseModel):
    tables: list[BeaNipaCatalogItem]
    provenance: Provenance


class BeaNipaRow(BaseModel):
    table_name: str
    series_code: str | None = None
    line_number: int | None = None
    line_description: str
    time_period: str
    metric_name: str | None = None
    unit: str | None = None
    unit_mult: int | None = None
    value: float | None = None
    note_ref: str | None = None


class BeaNipaTable(BaseModel):
    table_name: str
    frequency: str
    years: list[str]
    rows: list[BeaNipaRow]
    notes: list[str] = Field(default_factory=list)
    provenance: Provenance
