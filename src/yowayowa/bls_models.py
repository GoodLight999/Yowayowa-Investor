from __future__ import annotations

from datetime import date as Date

from pydantic import BaseModel, Field

from yowayowa.domain import Provenance


class BlsCatalogItem(BaseModel):
    series_id: str
    title: str
    unit: str
    seasonal_adjustment: str
    category: str


class BlsCatalog(BaseModel):
    series: list[BlsCatalogItem]
    provenance: Provenance


class BlsObservation(BaseModel):
    year: int
    period: str
    period_name: str
    date: Date | None = None
    value: float
    footnotes: list[str] = Field(default_factory=list)


class BlsSeries(BaseModel):
    series_id: str
    title: str
    unit: str | None = None
    seasonal_adjustment: str | None = None
    observations: list[BlsObservation]
    provenance: Provenance
