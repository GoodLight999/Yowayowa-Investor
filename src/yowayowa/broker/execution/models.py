"""Order-proposal domain model for live broker execution (P2A).

An OrderProposal is the operator-facing representation of one intended
order. The proposal hash covers ONLY the immutable economic fields so
the same economic intent always yields the same hash (idempotency key).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator, model_validator

from yowayowa.broker_models import BrokerOrderSide, BrokerOrderType


def _utc_now() -> datetime:
    return datetime.now(UTC)


class OrderProposal(BaseModel):
    """A single proposed live order, with research traceability."""

    proposal_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    client_order_id: str = Field(min_length=1, max_length=128)
    symbol: str = Field(min_length=1, max_length=32)
    market: str = Field(min_length=1, max_length=16)
    side: BrokerOrderSide
    quantity: int = Field(gt=0)
    order_type: BrokerOrderType
    limit_price: Decimal | None = Field(default=None, gt=0)
    reference_price: Decimal | None = Field(default=None, gt=0)
    currency: str = Field(default="JPY", min_length=3, max_length=3)
    motivation: str = Field(min_length=1, max_length=2000)
    source_research_link: str | None = None
    provenance: dict[str, str] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utc_now)

    @field_validator("created_at")
    @classmethod
    def _created_at_must_be_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _limit_order_requires_limit_price(self) -> OrderProposal:
        if self.order_type == BrokerOrderType.LIMIT and self.limit_price is None:
            raise ValueError("limit_price is required for limit orders")
        return self

    def proposal_hash(self) -> str:
        """SHA-256 over canonical JSON of the immutable economic fields.

        Deliberately excludes ids and timestamps so the same economic
        intent always hashes identically across restarts and replays.
        """

        payload = {
            "symbol": self.symbol,
            "market": self.market,
            "side": str(self.side.value),
            "quantity": self.quantity,
            "order_type": str(self.order_type.value),
            "limit_price": None if self.limit_price is None else str(self.limit_price),
            "reference_price": None if self.reference_price is None else str(self.reference_price),
            "currency": self.currency,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
