from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from yowayowa.domain import Provenance


class PositionRisk(BaseModel):
    symbol: str
    signed_weight: float
    volatility_annualized: float | None = None
    beta: float | None = None
    correlation_to_portfolio: float | None = None
    variance_contribution: float | None = None
    observations: int = 0


class CorrelationCell(BaseModel):
    left: str
    right: str
    correlation: float | None = None


class PortfolioRiskAnalytics(BaseModel):
    portfolio_id: int
    name: str
    base_currency: str
    benchmark: str
    period: str
    observations: int
    annualized_return: float | None = None
    annualized_volatility: float | None = None
    sharpe_ratio: float | None = None
    max_drawdown: float | None = None
    value_at_risk_95: float | None = None
    expected_shortfall_95: float | None = None
    beta: float | None = None
    benchmark_correlation: float | None = None
    gross_exposure: float
    net_exposure: float
    largest_position_weight: float
    concentration_hhi: float
    covered_gross_weight: float = 0.0
    positions: list[PositionRisk] = Field(default_factory=list)
    correlations: list[CorrelationCell] = Field(default_factory=list)
    unavailable_symbols: list[str] = Field(default_factory=list)
    risk_free_rate: float = 0.0
    notes: list[str] = Field(default_factory=list)
    provenance: list[Provenance] = Field(default_factory=list)
    evaluated_at: datetime
