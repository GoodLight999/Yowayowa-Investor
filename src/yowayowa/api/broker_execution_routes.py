"""Broker execution API surface (P2A) — domain-only, fail-closed.

All endpoints operate on the local domain service: proposals, previews,
interlock decisions, and audit reads. NOTHING here submits an order to
a broker; a blocked interlock decision is a 200 response carrying
``allowed=false``, never an exception.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, ValidationError

from yowayowa.api.deps import (
    get_broker_execution_service,
    require_api_token,
    require_private_connectors,
)
from yowayowa.broker.execution.audit import AuditEntry
from yowayowa.broker.execution.models import OrderProposal
from yowayowa.broker.execution.service import BrokerExecutionDomainService, OrderExecutionPreview

router = APIRouter(
    prefix="/v1/broker-execution",
    dependencies=[Depends(require_api_token), Depends(require_private_connectors)],
)


class ProposalCreateRequest(BaseModel):
    client_order_id: str = Field(min_length=1, max_length=128)
    symbol: str = Field(min_length=1, max_length=32)
    market: str = Field(min_length=1, max_length=16)
    side: str = Field(pattern="^(buy|sell)$")
    quantity: int = Field(gt=0)
    order_type: str = Field(pattern="^(market|limit)$")
    limit_price: str | None = None
    reference_price: str | None = None
    currency: str = Field(default="JPY", min_length=3, max_length=3)
    motivation: str = Field(min_length=1, max_length=2000)
    source_research_link: str | None = None
    provenance: dict[str, str] = Field(default_factory=dict)


class EvaluateRequest(BaseModel):
    armed: bool


class AuditEntryPayload(BaseModel):
    seq: int
    ts: str
    kind: str
    client_order_id: str
    payload: dict[str, object]
    prev_hash: str
    entry_hash: str


class AuditResponse(BaseModel):
    entries: list[AuditEntryPayload]
    verify_problems: list[str]
    intact: bool


class ProposalResponse(BaseModel):
    proposal: OrderProposal
    proposal_hash: str
    preview: OrderExecutionPreview


class EvaluateResponse(BaseModel):
    allowed: bool
    reasons: tuple[str, ...]
    proposal_hash: str
    replay: bool


def _decimal_or_none(value: str | None) -> Decimal | None:
    if value is None or not value.strip():
        return None
    return Decimal(value)


@router.post(
    "/proposals",
    response_model=ProposalResponse,
    operation_id="broker_execution_create_proposal",
)
def create_proposal(
    body: ProposalCreateRequest,
    service: BrokerExecutionDomainService = Depends(get_broker_execution_service),
) -> ProposalResponse:
    fields: dict[str, object] = {
        "client_order_id": body.client_order_id,
        "symbol": body.symbol,
        "market": body.market,
        "side": body.side,
        "quantity": body.quantity,
        "order_type": body.order_type,
        "limit_price": _decimal_or_none(body.limit_price),
        "reference_price": _decimal_or_none(body.reference_price),
        "currency": body.currency,
        "motivation": body.motivation,
    }
    if body.source_research_link is not None:
        fields["source_research_link"] = body.source_research_link
    if body.provenance:
        fields["provenance"] = body.provenance
    try:
        proposal = service.propose(**fields)
    except (ValidationError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ProposalResponse(
        proposal=proposal,
        proposal_hash=proposal.proposal_hash(),
        preview=service.preview(proposal),
    )


@router.post(
    "/proposals/{client_order_id}/evaluate",
    response_model=EvaluateResponse,
    operation_id="broker_execution_evaluate_proposal",
)
def evaluate_proposal(
    client_order_id: str,
    body: EvaluateRequest,
    service: BrokerExecutionDomainService = Depends(get_broker_execution_service),
) -> EvaluateResponse:
    proposal = _find_proposal(service, client_order_id)
    decision = service.evaluate(proposal, armed=body.armed)
    duplicate = service.duplicate_check(proposal)
    return EvaluateResponse(
        allowed=decision.allowed,
        reasons=decision.reasons,
        proposal_hash=proposal.proposal_hash(),
        replay=duplicate.replay,
    )


@router.get("/audit", response_model=AuditResponse, operation_id="broker_execution_read_audit")
def read_audit(
    limit: Annotated[int, Query(ge=1, le=1000)] = 50,
    service: BrokerExecutionDomainService = Depends(get_broker_execution_service),
) -> AuditResponse:
    problems = service.verify_audit()
    entries = service.audit_entries(limit=limit)
    return AuditResponse(
        entries=[AuditEntryPayload.model_validate(_entry_to_payload(e)) for e in entries],
        verify_problems=problems,
        intact=not problems,
    )


def _entry_to_payload(entry: AuditEntry) -> dict[str, object]:
    return {
        "seq": entry.seq,
        "ts": entry.ts.isoformat(),
        "kind": entry.kind,
        "client_order_id": entry.client_order_id,
        "payload": entry.payload,
        "prev_hash": entry.prev_hash,
        "entry_hash": entry.entry_hash,
    }


def _find_proposal(
    service: BrokerExecutionDomainService,
    client_order_id: str,
) -> OrderProposal:
    """Rebuild the proposal from the audit trail; 404 when never proposed."""

    for entry in reversed(service.audit_entries()):
        if entry.client_order_id == client_order_id and entry.kind == "intent":
            payload = entry.payload
            proposal_payload = payload.get("proposal")
            if isinstance(proposal_payload, dict):
                created = payload.get("created_at")
                if isinstance(created, str):
                    proposal_payload = dict(proposal_payload)
                    proposal_payload.setdefault("created_at", created)
                return OrderProposal.model_validate(proposal_payload)
    raise HTTPException(status_code=404, detail=f"unknown client_order_id: {client_order_id}")
