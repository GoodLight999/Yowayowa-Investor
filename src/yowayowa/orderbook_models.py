from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from yowayowa.domain import LicenseClass, Provenance


class OrderbookLevel(BaseModel):
    """Single price-quantity level in an orderbook."""

    price: float = Field(gt=0, description="Order level price")
    size: float = Field(gt=0, description="Available quantity at this price")
    order_count: int | None = Field(default=None, description="Number of orders at this level")


class OrderbookMetrics(BaseModel):
    """Microstructure and liquidity metrics computed from an orderbook."""

    best_bid: float | None = None
    best_ask: float | None = None
    mid_price: float | None = None
    spread: float | None = None
    spread_bps: float | None = None
    bid_depth_total: float = 0.0
    ask_depth_total: float = 0.0
    total_depth: float = 0.0
    order_flow_imbalance: float | None = None  # (bid - ask) / (bid + ask) in [-1.0, 1.0]
    micro_price: float | None = None  # Volume-weighted mid price
    hft_activity_indicator: Literal["HIGH", "MODERATE", "LOW", "UNKNOWN"] = "UNKNOWN"


class OrderbookSnapshot(BaseModel):
    """Complete Level-2 orderbook snapshot with bids and asks."""

    symbol: str
    as_of: datetime = Field(default_factory=lambda: datetime.now(UTC))
    bids: list[OrderbookLevel] = Field(default_factory=list)
    asks: list[OrderbookLevel] = Field(default_factory=list)
    metrics: OrderbookMetrics = Field(default_factory=OrderbookMetrics)
    provenance: Provenance = Field(
        default_factory=lambda: Provenance(
            provider="orderbook-engine",
            source="Yowayowa Orderbook Engine",
            license_class=LicenseClass.OFFICIAL_PUBLIC,
            retrieved_at=datetime.now(UTC),
        )
    )


class DepthLevel(BaseModel):
    """Cumulative depth level for depth-of-market ladder visualisation."""

    price: float
    size: float
    cumulative_size: float


class OrderbookDepthLadder(BaseModel):
    """Ladder representation of market depth with cumulative volumes."""

    symbol: str
    as_of: datetime
    mid_price: float | None = None
    bids_cumulative: list[DepthLevel] = Field(default_factory=list)
    asks_cumulative: list[DepthLevel] = Field(default_factory=list)
    provenance: Provenance


class ExecutionImpactRequest(BaseModel):
    """Request payload to simulate a market-order execution impact."""

    side: Literal["buy", "sell"]
    quantity: float = Field(gt=0, description="Quantity to execute")


class ExecutionImpactResult(BaseModel):
    """Result of walking the orderbook to estimate market-order slippage and impact."""

    symbol: str
    side: Literal["buy", "sell"]
    requested_quantity: float
    fillable_quantity: float
    fully_filled: bool
    average_price: float | None = None
    best_quote_price: float | None = None
    slippage: float | None = None
    slippage_bps: float | None = None
    total_cost: float | None = None
    levels_swept: int = 0
    warning: str | None = None
