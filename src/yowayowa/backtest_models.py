from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

BacktestSignal = Literal[
    "momentum_12_1",
    "low_volatility",
    "mean_reversion_20",
    "equal_weight",
]
RebalanceFrequency = Literal["daily", "weekly", "monthly"]
ExecutionPrice = Literal["next_open"]


class BacktestStrategyDefinition(BaseModel):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=500)
    signal: BacktestSignal
    universe: list[str] = Field(min_length=1, max_length=100)
    rebalance: RebalanceFrequency = "monthly"
    max_positions: int = Field(default=10, ge=1, le=100)
    weighting: Literal["equal", "inverse_volatility"] = "equal"
    execution: ExecutionPrice = "next_open"

    @model_validator(mode="after")
    def validate_universe(self) -> BacktestStrategyDefinition:
        normalized = [symbol.strip().upper() for symbol in self.universe]
        if any(not symbol for symbol in normalized):
            raise ValueError("universe symbols must not be empty")
        if len(set(normalized)) != len(normalized):
            raise ValueError("universe symbols must be unique")
        self.universe = normalized
        return self


class BacktestRunRequest(BaseModel):
    strategy_id: str = Field(min_length=1, max_length=80)
    start: date
    end: date
    commission_bps: float = Field(default=10.0, ge=0, le=1000)
    slippage_bps: float = Field(default=5.0, ge=0, le=1000)
    bootstrap_samples: int = Field(default=1000, ge=0, le=20000)
    bootstrap_seed: int = 20260930
    provider: Literal["alpaca", "binance"] = "alpaca"

    @model_validator(mode="after")
    def validate_dates(self) -> BacktestRunRequest:
        if self.start >= self.end:
            raise ValueError("start must be before end")
        return self


class BacktestMetric(BaseModel):
    value: float | None = None
    sample_count: int = Field(ge=0)
    status: Literal["available", "insufficient", "undefined"]


class BacktestMetrics(BaseModel):
    cagr: BacktestMetric
    sharpe_ratio: BacktestMetric
    sortino_ratio: BacktestMetric
    max_drawdown: BacktestMetric
    calmar_ratio: BacktestMetric
    hit_rate: BacktestMetric
    annual_turnover: BacktestMetric
    bootstrap_ci_95: tuple[float, float] | None = None
    bootstrap_status: Literal["available", "insufficient", "undefined", "disabled"]


class BacktestEquityPoint(BaseModel):
    date: date
    equity: float
    cash: float
    daily_return: float
    turnover: float
    transaction_cost: float


class BacktestTrade(BaseModel):
    date: date
    symbol: str
    previous_weight: float
    target_weight: float
    turnover: float
    transaction_cost: float
    execution_price: float


class BacktestProvenance(BaseModel):
    symbol: str
    provider: str
    source_url: str
    license_class: str
    retrieved_at: datetime
    as_of_start: date
    as_of_end: date


class BacktestRunResponse(BaseModel):
    strategy: BacktestStrategyDefinition
    start: date
    end: date
    status: Literal["complete", "insufficient"]
    metrics: BacktestMetrics
    in_sample_metrics: BacktestMetrics | None = None
    oos_metrics: BacktestMetrics | None = None
    oos_start: date | None = None
    purged_sessions: int = Field(default=0, ge=0)
    equity_curve: list[BacktestEquityPoint]
    trades: list[BacktestTrade]
    provenance: list[BacktestProvenance]
    assumptions: list[str]
    warnings: list[str] = Field(default_factory=list)
