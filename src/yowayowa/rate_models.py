from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from yowayowa.domain import Provenance


class YieldCurvePoint(BaseModel):
    maturity: str
    years: float
    yield_percent: float


class YieldCurveSnapshot(BaseModel):
    date: date
    points: list[YieldCurvePoint]
    spread_10y_2y: float | None = None
    spread_10y_3m: float | None = None


class TreasuryYieldCurve(BaseModel):
    latest: YieldCurveSnapshot
    history: list[YieldCurveSnapshot] = Field(default_factory=list)
    provenance: Provenance
