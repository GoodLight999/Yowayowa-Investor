from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from yowayowa.domain import Provenance


class PortfolioSizingIdea(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(min_length=1, max_length=32)
    entry_price: Decimal = Field(gt=0)
    stop_price: Decimal = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    lot_size: int = Field(ge=1, le=1_000_000)
    price_provenance: Provenance

    @model_validator(mode="after")
    def require_as_of_price_provenance(self) -> PortfolioSizingIdea:
        if self.price_provenance.as_of is None:
            raise ValueError("Price provenance must include an as_of date or timestamp")
        if not self.price_provenance.provider.strip() or not self.price_provenance.source.strip():
            raise ValueError("Price provenance must identify its provider and source")
        return self


class PortfolioSizingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ideas: list[PortfolioSizingIdea] = Field(min_length=1, max_length=25)
    risk_budget_pct: Decimal = Field(gt=0, le=1, max_digits=9, decimal_places=8)
    max_position_pct: Decimal = Field(gt=0, le=1, max_digits=9, decimal_places=8)


class SizingIdeaProposal(BaseModel):
    symbol: str
    currency: str
    entry_price: Decimal
    stop_price: Decimal
    lot_size: int
    current_position_value_base: Decimal
    risk_budget_base: Decimal
    risk_per_share_base: Decimal
    risk_limited_quantity: int
    position_limited_quantity: int
    quantity: int
    proposed_notional_base: Decimal
    estimated_stop_loss_base: Decimal
    projected_position_value_base: Decimal
    limiting_constraints: list[Literal["risk_budget", "position_limit"]]
    price_provenance: Provenance


class PortfolioSizingProposal(BaseModel):
    portfolio_id: int
    base_currency: str
    gross_market_value_base: Decimal
    risk_budget_pct: Decimal
    total_risk_budget_base: Decimal
    per_idea_risk_budget_base: Decimal
    max_position_pct: Decimal
    max_position_value_base: Decimal
    ideas: list[SizingIdeaProposal]
    provenance: list[Provenance]
    notes: list[str]
    calculated_at: datetime
    executable: Literal[False] = False
