"""Broker execution API surface (P2A) — domain-only, fail-closed.

All endpoints operate on the local domain service: proposals, previews,
interlock decisions, and audit reads. NOTHING here submits an order to
a broker; a blocked interlock decision is a 200 response carrying
``allowed=false``, never an exception.

The two GET order-inquiry endpoints (P2C2) are read-only: they query
the P1B broker-read connector and match rows against the audit trail.
No POST/DELETE order path exists by design — cancellation is not
implemented and has no endpoint at all (not even a 405).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session

from yowayowa.api.deps import (
    db_session,
    get_broker_execution_service,
    get_executions_reconciliation_service,
    get_order_inquiry_service,
    require_api_token,
    require_private_connectors,
)
from yowayowa.broker.execution.audit import AuditEntry
from yowayowa.broker.execution.models import OrderProposal
from yowayowa.broker.execution.service import (
    AuditIntegrityCompromisedError,
    BrokerExecutionDomainService,
    DuplicateProposalError,
    OrderExecutionPreview,
)
from yowayowa.services.broker_read_service import BrokerReadOutcome
from yowayowa.services.executions_reconciliation import (
    ExecutionPreviewChangedError,
    ExecutionReconciliationApplyResult,
    ExecutionReconciliationPreview,
    ExecutionsReconciliationService,
)
from yowayowa.services.order_inquiry_service import OrderInquiryReport, OrderInquiryService

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


class ExecutionReconciliationApplyRequest(BaseModel):
    portfolio_id: int = Field(gt=0)
    market: Literal["jp", "us"] = "jp"
    preview_id: str = Field(min_length=64, max_length=64)
    operator_approved: bool = False


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
    total_entries: int
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
    except AuditIntegrityCompromisedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except DuplicateProposalError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
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
    try:
        decision = service.evaluate(proposal, armed=body.armed)
    except AuditIntegrityCompromisedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
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
    all_entries = service.audit_entries()
    return AuditResponse(
        entries=[
            AuditEntryPayload.model_validate(_entry_to_payload(entry))
            for entry in all_entries[-limit:]
        ],
        total_entries=len(all_entries),
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

    proposal = service.find_audited_proposal(client_order_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail=f"unknown client_order_id: {client_order_id}")
    return proposal


# ---------------------------------------------------------------------
# P2C2: read-only order inquiry (audit-matched). No write path exists.
# ---------------------------------------------------------------------


@router.get(
    "/orders",
    response_model=OrderInquiryReport,
    operation_id="broker_execution_list_orders",
)
def list_orders(
    market: Annotated[str, Query(pattern="^(jp|us)$")] = "jp",
    force_refresh: bool = False,
    service: OrderInquiryService = Depends(get_order_inquiry_service),
) -> OrderInquiryReport:
    """Orders from the authenticated web session, audit-matched."""

    return service.list_orders(market=market, force_refresh=force_refresh)


@router.get(
    "/orders/{client_order_id}",
    response_model=OrderInquiryReport,
    operation_id="broker_execution_order_status",
)
def order_status(
    client_order_id: str,
    market: Annotated[str, Query(pattern="^(jp|us)$")] = "jp",
    service: OrderInquiryService = Depends(get_order_inquiry_service),
) -> OrderInquiryReport:
    """One audited client_order_id; 404 when the id was never proposed."""

    report = service.order_status(client_order_id, market=market)
    if not report.items and any(
        note.startswith("unknown client_order_id") for note in report.notes
    ):
        raise HTTPException(
            status_code=404,
            detail=f"unknown client_order_id: {client_order_id}",
        )
    return report


@router.get(
    "/executions",
    response_model=BrokerReadOutcome,
    operation_id="broker_execution_list_executions",
)
def list_executions(
    market: Annotated[str, Query(pattern="^(jp|us)$")] = "jp",
    force_refresh: bool = False,
    service: OrderInquiryService = Depends(get_order_inquiry_service),
) -> BrokerReadOutcome:
    """Read normalized broker fills with acquisition provenance; never applies them."""

    return service.list_executions(market=market, force_refresh=force_refresh)


@router.get(
    "/reconciliation/preview",
    response_model=ExecutionReconciliationPreview,
    operation_id="broker_execution_reconciliation_preview",
)
def preview_execution_reconciliation(
    portfolio_id: Annotated[int, Query(gt=0)],
    market: Annotated[str, Query(pattern="^(jp|us)$")] = "jp",
    force_refresh: bool = False,
    service: ExecutionsReconciliationService = Depends(get_executions_reconciliation_service),
    session: Session = Depends(db_session),
) -> ExecutionReconciliationPreview:
    try:
        return service.preview(session, portfolio_id, market, force_refresh=force_refresh)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/reconciliation/apply",
    response_model=ExecutionReconciliationApplyResult,
    operation_id="broker_execution_reconciliation_apply",
)
def apply_execution_reconciliation(
    body: ExecutionReconciliationApplyRequest,
    service: ExecutionsReconciliationService = Depends(get_executions_reconciliation_service),
    session: Session = Depends(db_session),
) -> ExecutionReconciliationApplyResult:
    try:
        return service.apply(
            session,
            body.portfolio_id,
            body.market,
            body.preview_id,
            operator_approved=body.operator_approved,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ExecutionPreviewChangedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
