from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from yowayowa.domain import Provenance
from yowayowa.research_models import MarketScreenRequest


class StrategySource(BaseModel):
    label: str
    url: str
    note: str


class StrategyPresetDefinition(BaseModel):
    id: str
    name_ja: str
    name_en: str
    description_ja: str
    description_en: str
    default_region: str
    region_required: bool = True
    discovery: MarketScreenRequest
    research_metrics: list[str] = Field(default_factory=list)
    qualitative_review_ja: list[str] = Field(default_factory=list)
    qualitative_review_en: list[str] = Field(default_factory=list)
    sources: list[StrategySource] = Field(default_factory=list)


class StrategyCandidateInput(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    market_cap: float = Field(gt=0)
    pe_ratio: float | None = None
    investment_securities: float | None = Field(default=None, ge=0)


NetCashBasis = Literal[
    "kiyohara_formula_with_investment_securities",
    "conservative_floor_ex_investment_securities",
]


class StrategyCandidateEvaluation(BaseModel):
    symbol: str
    company_name: str
    market_cap: float
    pe_ratio: float | None = None
    current_assets: float | None = None
    liabilities: float | None = None
    investment_securities: float | None = None
    yowayowa_conservative_net_cash: float | None = None
    yowayowa_conservative_net_cash_ratio: float | None = None
    net_cash: float | None = None
    net_cash_ratio: float | None = None
    net_cash_ratio_is_lower_bound: bool = False
    cash_neutral_pe: float | None = None
    cash_neutral_pe_is_upper_bound: bool = False
    revenue_growth_yoy: float | None = None
    net_income_growth_yoy: float | None = None
    free_cash_flow: float | None = None
    return_on_equity: float | None = None
    deep_value_net_cash: bool | None = None
    basis: NetCashBasis
    missing: list[str] = Field(default_factory=list)
    provenance: Provenance
    supplemental_provenance: list[Provenance] = Field(default_factory=list)


class StrategyEvaluationRequest(BaseModel):
    candidates: list[StrategyCandidateInput] = Field(min_length=1, max_length=50)


class StrategyEvaluationResponse(BaseModel):
    strategy_id: str
    evaluations: list[StrategyCandidateEvaluation]
    errors: dict[str, str] = Field(default_factory=dict)
    supplement_errors: dict[str, str] = Field(default_factory=dict)
    evaluated_at: datetime
