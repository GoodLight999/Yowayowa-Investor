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


class StrategyBalanceSheetSupplement(BaseModel):
    current_assets: float
    liabilities: float
    investment_securities: float | None = None
    provenance: Provenance


class StrategyCandidateInput(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    market_cap: float = Field(gt=0)
    pe_ratio: float | None = None
    investment_securities: float | None = Field(default=None, ge=0)


NetCashBasis = Literal[
    "kiyohara_formula_with_investment_securities",
    "conservative_floor_ex_investment_securities",
]
StrategyPriorityFactorKey = Literal["value", "growth", "quality", "evidence"]
StrategyPrioritySignal = float | bool | str | None


class StrategyPriorityFactor(BaseModel):
    key: StrategyPriorityFactorKey
    score: float = Field(ge=0)
    max_score: float = Field(gt=0)
    signals: dict[str, StrategyPrioritySignal] = Field(default_factory=dict)


class StrategyResearchPriority(BaseModel):
    scoring_version: Literal["kiyohara_priority_v1"] = "kiyohara_priority_v1"
    score: float = Field(ge=0, le=100)
    max_score: float = 100
    confidence: float = Field(ge=0, le=1)
    factors: list[StrategyPriorityFactor] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    next_checks: list[str] = Field(default_factory=list)
    interpretation: Literal["research_priority_not_return_forecast"] = (
        "research_priority_not_return_forecast"
    )


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
    research_priority: StrategyResearchPriority | None = None
    basis: NetCashBasis
    missing: list[str] = Field(default_factory=list)
    provenance: Provenance
    supplemental_provenance: list[Provenance] = Field(default_factory=list)


class StrategyEvaluationRequest(BaseModel):
    candidates: list[StrategyCandidateInput] = Field(min_length=1, max_length=50)
    region: str | None = Field(default=None, min_length=2, max_length=16)
    record: bool = False


class StrategyResearchSnapshot(BaseModel):
    id: int
    strategy_id: str
    scoring_version: str
    region: str
    symbol: str
    score: float
    confidence: float
    evaluation: StrategyCandidateEvaluation
    captured_at: datetime


StrategyOutcomeStatus = Literal["pending", "available", "unavailable"]


class StrategyForwardOutcome(BaseModel):
    snapshot_id: int
    strategy_id: str
    scoring_version: str
    region: str
    symbol: str
    research_priority_score: float
    captured_at: datetime
    horizon_trading_days: int = Field(gt=0)
    status: StrategyOutcomeStatus
    entry_at: datetime | None = None
    exit_at: datetime | None = None
    entry_price: float | None = None
    exit_price: float | None = None
    total_return: float | None = None
    benchmark_symbol: str | None = None
    benchmark_return: float | None = None
    excess_return: float | None = None


class StrategyForwardOutcomeReport(BaseModel):
    outcomes: list[StrategyForwardOutcome]
    provenance: list[Provenance] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    evaluated_at: datetime


StrategyCalibrationMinimumSampleThreshold = 30
StrategyCalibrationDecileMinimumSample = 10


class StrategyOutcomeDecileStatistics(BaseModel):
    sample_count: int = Field(ge=0)
    median_total_return: float | None = None
    mean_total_return: float | None = None
    median_excess_return: float | None = None
    mean_excess_return: float | None = None
    positive_excess_hit_rate: float | None = None


class StrategyScoreDecileSummary(StrategyOutcomeDecileStatistics):
    decile: int = Field(ge=1, le=10)
    score_min: float
    score_max: float


class StrategyFactorDecileSummary(StrategyOutcomeDecileStatistics):
    factor_key: StrategyPriorityFactorKey
    decile: int = Field(ge=1, le=10)
    factor_score_fraction_min: float
    factor_score_fraction_max: float


class StrategyCalibrationBucket(BaseModel):
    strategy_id: str
    scoring_version: str
    horizon_trading_days: int = Field(gt=0)
    sample_total: int = Field(ge=0)
    sample_available: int = Field(ge=0)
    sample_pending: int = Field(ge=0)
    sample_unavailable: int = Field(ge=0)
    minimum_sample_warning: bool
    score_deciles: list[StrategyScoreDecileSummary] = Field(default_factory=list)
    factor_deciles: list[StrategyFactorDecileSummary] = Field(default_factory=list)
    median_total_return: float | None = None
    mean_total_return: float | None = None
    median_excess_return: float | None = None
    mean_excess_return: float | None = None
    positive_excess_hit_rate: float | None = None
    rank_ic: float | None = None
    ic_sample_count: int = Field(ge=0, default=0)
    ic_insufficient: bool = True
    notes: list[str] = Field(default_factory=list)


class StrategyCalibrationReport(BaseModel):
    buckets: list[StrategyCalibrationBucket]
    provenance: list[Provenance] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    evaluated_at: datetime


class StrategyEvaluationResponse(BaseModel):
    strategy_id: str
    evaluations: list[StrategyCandidateEvaluation]
    errors: dict[str, str] = Field(default_factory=dict)
    supplement_errors: dict[str, str] = Field(default_factory=dict)
    snapshot_ids: list[int] = Field(default_factory=list)
    evaluated_at: datetime
