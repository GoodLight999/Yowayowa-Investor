"""Fail-closed execution interlocks for live broker orders (P2A).

Every unknown or missing input blocks execution. Reasons are stable,
lowercase English strings; tests assert on them verbatim.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel

from yowayowa.broker.execution.models import OrderProposal
from yowayowa.broker_models import (
    BrokerOrderIntent,
    BrokerOrderPreview,
    BrokerTransport,
)
from yowayowa.config import Settings
from yowayowa.services.broker_execution import evaluate_broker_execution

REASON_NOT_ARMED = "execution is not armed; live orders require an explicit arming state"
REASON_NOTIONAL_UNKNOWN = "order notional cannot be estimated; missing data is not zero"
REASON_DUPLICATE_MISMATCH = "duplicate client_order_id with different proposal content"


class DuplicateCheckResult(BaseModel):
    """Outcome of the idempotency check against the audit registry."""

    client_order_id: str
    existing_hash: str | None = None
    submitted: bool = False
    replay: bool = False
    mismatch: bool = False


class ExecutionInterlockDecision(BaseModel):
    allowed: bool
    reasons: tuple[str, ...]


def _as_preview(proposal: OrderProposal) -> BrokerOrderPreview:
    intent = BrokerOrderIntent(
        client_order_id=proposal.client_order_id,
        symbol=proposal.symbol,
        side=proposal.side,
        quantity=proposal.quantity,
        order_type=proposal.order_type,
        limit_price=proposal.limit_price,
        reference_price=proposal.reference_price,
        currency=proposal.currency,
    )
    price = proposal.limit_price or proposal.reference_price
    notional = None if price is None else price * proposal.quantity
    return BrokerOrderPreview(
        broker="rakuten-securities",
        transport=BrokerTransport.AUTHENTICATED_WEB_SESSION,
        intent=intent,
        estimated_notional=notional,
        currency=proposal.currency,
    )


def evaluate_execution_interlocks(
    proposal: OrderProposal,
    preview_notional: Decimal | None,
    settings: Settings,
    *,
    armed: bool,
    orders_submitted_today: int,
    duplicate_check: DuplicateCheckResult,
) -> ExecutionInterlockDecision:
    """Compose every fail-closed gate; ANY unknown/missing input blocks."""

    reasons: list[str] = []

    gate = evaluate_broker_execution(
        settings,
        _as_preview(proposal),
        orders_submitted_today=orders_submitted_today,
    )
    reasons.extend(gate.reasons)

    if not armed:
        reasons.append(REASON_NOT_ARMED)

    if preview_notional is None:
        reasons.append(REASON_NOTIONAL_UNKNOWN)

    if duplicate_check.mismatch:
        reasons.append(REASON_DUPLICATE_MISMATCH)

    return ExecutionInterlockDecision(allowed=not reasons, reasons=tuple(reasons))
