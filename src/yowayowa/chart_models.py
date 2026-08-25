from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from yowayowa.domain import Provenance

ChartSourceKind = Literal["price", "fundamental", "fred"]
ChartTransformKind = Literal["ratio", "spread", "rolling_correlation"]


class ChartSourceSpec(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")
    source: ChartSourceKind
    symbol: str | None = Field(default=None, max_length=32)
    metric: str | None = Field(default=None, max_length=64)
    series_id: str | None = Field(default=None, max_length=64)
    period: str = Field(default="5y", pattern=r"^(?:[0-9]+(?:d|mo|y)|max)$")
    label: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def validate_source_fields(self) -> ChartSourceSpec:
        if self.source == "price" and not self.symbol:
            raise ValueError("price source requires symbol")
        if self.source == "fundamental" and (not self.symbol or not self.metric):
            raise ValueError("fundamental source requires symbol and metric")
        if self.source == "fred" and not self.series_id:
            raise ValueError("fred source requires series_id")
        return self


class ChartTransformSpec(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")
    kind: ChartTransformKind
    left: str = Field(min_length=1, max_length=32)
    right: str = Field(min_length=1, max_length=32)
    window: int = Field(default=60, ge=2, le=500)
    label: str | None = Field(default=None, max_length=120)


class ChartComposeRequest(BaseModel):
    sources: list[ChartSourceSpec] = Field(min_length=1, max_length=8)
    transforms: list[ChartTransformSpec] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def validate_unique_ids(self) -> ChartComposeRequest:
        ids = [item.id for item in self.sources] + [item.id for item in self.transforms]
        if len(ids) != len(set(ids)):
            raise ValueError("Chart source and transform ids must be unique")
        known = {item.id for item in self.sources}
        for transform in self.transforms:
            if transform.left not in known or transform.right not in known:
                raise ValueError(
                    f"Transform {transform.id} references an unknown source; "
                    "transforms currently reference source ids"
                )
        return self


class ChartPoint(BaseModel):
    date: date
    value: float


class ComposedChartSeries(BaseModel):
    id: str
    label: str
    kind: Literal["source", "derived"]
    source: ChartSourceKind | None = None
    transform: ChartTransformKind | None = None
    unit: str | None = None
    points: list[ChartPoint]
    provenance: list[Provenance] = Field(default_factory=list)


class ChartComposeResponse(BaseModel):
    series: list[ComposedChartSeries]
    errors: dict[str, str] = Field(default_factory=dict)
    composed_at: datetime
    notes: list[str] = Field(default_factory=list)
