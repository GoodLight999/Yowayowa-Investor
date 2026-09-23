"""Broker execution DOMAIN service: proposals, interlocks, audit (P2A).

There is deliberately NO submit/execute method and NO network or browser
transport anywhere in this package. This layer only proposes, previews,
evaluates fail-closed interlocks, and records an append-only audit
trail. The next task is the Rakuten submission connector, which will be
the only component permitted to translate an ALLOWED decision plus an
explicit operator arming state into a real broker submission.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from yowayowa.broker.execution.audit import AppendOnlyAuditLog, AuditEntry
from yowayowa.broker.execution.interlocks import (
    DuplicateCheckResult,
    ExecutionInterlockDecision,
    evaluate_execution_interlocks,
)
from yowayowa.broker.execution.models import OrderProposal
from yowayowa.config import Settings

REQUEST_STAGE_SUBMIT = "submit"
JST = ZoneInfo("Asia/Tokyo")


class OrderExecutionPreview(BaseModel):
    proposal: OrderProposal
    proposal_hash: str
    estimated_notional: Decimal | None = None
    currency: str
    warnings: list[str] = []


class DuplicateProposalError(RuntimeError):
    """Raised when propose() is called twice for the same client_order_id.

    The registry is strictly first-wins: an id already proposed can never
    be re-proposed (even with identical economic content), so the audit
    trail holds exactly one ``intent`` entry per client_order_id.
    """

    def __init__(self, client_order_id: str, existing_hash: str, incoming_hash: str) -> None:
        self.client_order_id = client_order_id
        self.existing_hash = existing_hash
        self.incoming_hash = incoming_hash
        super().__init__(
            f"duplicate client_order_id already proposed: {client_order_id} "
            f"(existing hash {existing_hash}, incoming hash {incoming_hash})"
        )


class _ProposalState(BaseModel):
    proposal_hash: str
    proposal_id: str


class _DailyCountEntry(BaseModel):
    ts: datetime


def _jst_date(ts: datetime) -> str:
    return ts.astimezone(JST).date().isoformat()


class BrokerExecutionDomainService:
    """Domain service for order proposals, interlocks, and audit replay.

    Restart-safe: the constructor replays the audit log to rebuild the
    duplicate registry and the JST-day submit counts.
    """

    def __init__(
        self,
        *,
        settings: Settings,
        audit_dir: Path | str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._settings = settings
        self._clock: Callable[[], datetime] = clock or (lambda: datetime.now(UTC))
        self._audit = AppendOnlyAuditLog(Path(audit_dir), clock=self._clock)
        self._proposals: dict[str, _ProposalState] = {}
        self._submits_by_jst_day: dict[str, list[_DailyCountEntry]] = {}
        self._replay_audit()

    # ------------------------------------------------------------------ state

    def _replay_audit(self) -> None:
        for entry in self._audit.entries():
            self._absorb(entry)

    def _absorb(self, entry: AuditEntry) -> None:
        payload = entry.payload
        if entry.kind == "intent":
            hash_value = payload.get("proposal_hash")
            # First-wins: only the first intent for an id ever registers,
            # matching the propose-path rejection semantics.
            if (
                isinstance(hash_value, str)
                and hash_value
                and entry.client_order_id not in self._proposals
            ):
                self._proposals[entry.client_order_id] = _ProposalState(
                    proposal_hash=hash_value,
                    proposal_id=str(payload.get("proposal_id", "")),
                )
        elif entry.kind == "request" and payload.get("stage") == REQUEST_STAGE_SUBMIT:
            self._submits_by_jst_day.setdefault(_jst_date(entry.ts), []).append(
                _DailyCountEntry(ts=entry.ts)
            )

    def _duplicate_check(self, proposal: OrderProposal) -> DuplicateCheckResult:
        existing = self._proposals.get(proposal.client_order_id)
        current_hash = proposal.proposal_hash()
        if existing is None:
            return DuplicateCheckResult(client_order_id=proposal.client_order_id)
        if existing.proposal_hash == current_hash:
            return DuplicateCheckResult(
                client_order_id=proposal.client_order_id,
                existing_hash=existing.proposal_hash,
                submitted=True,
                replay=True,
            )
        return DuplicateCheckResult(
            client_order_id=proposal.client_order_id,
            existing_hash=existing.proposal_hash,
            submitted=True,
            mismatch=True,
        )

    # --------------------------------------------------------------- propose

    def propose(self, **fields: object) -> OrderProposal:
        """Create and audit an OrderProposal (generates the proposal_id).

        Raises DuplicateProposalError when the client_order_id was already
        proposed (first-wins; nothing is appended to the audit trail).
        """

        proposal = OrderProposal.model_validate(fields)
        self._reject_reproposed(proposal)
        self._audit.append(
            "intent",
            proposal.client_order_id,
            {
                "proposal_id": proposal.proposal_id,
                "proposal_hash": proposal.proposal_hash(),
                "proposal": proposal.model_dump(mode="json"),
            },
        )
        self._proposals[proposal.client_order_id] = _ProposalState(
            proposal_hash=proposal.proposal_hash(),
            proposal_id=proposal.proposal_id,
        )
        return proposal

    def propose_model(self, proposal: OrderProposal) -> OrderProposal:
        """Register an already-constructed proposal in the audit trail.

        Raises DuplicateProposalError when the client_order_id was already
        proposed (first-wins; nothing is appended to the audit trail).
        """

        self._reject_reproposed(proposal)
        self._audit.append(
            "intent",
            proposal.client_order_id,
            {
                "proposal_id": proposal.proposal_id,
                "proposal_hash": proposal.proposal_hash(),
                "proposal": proposal.model_dump(mode="json"),
            },
        )
        self._proposals[proposal.client_order_id] = _ProposalState(
            proposal_hash=proposal.proposal_hash(),
            proposal_id=proposal.proposal_id,
        )
        return proposal

    def _reject_reproposed(self, proposal: OrderProposal) -> None:
        existing = self._proposals.get(proposal.client_order_id)
        if existing is not None:
            raise DuplicateProposalError(
                proposal.client_order_id, existing.proposal_hash, proposal.proposal_hash()
            )

    # ------------------------------------------------------------- reconstruct

    def find_audited_proposal(self, client_order_id: str) -> OrderProposal | None:
        """Rebuild the proposal from the newest intent audit entry.

        created_at is backfilled from the entry payload timestamp when the
        stored proposal payload predates the created_at audit field
        (provenance: proposal creation time is the intent entry ts, never
        'now'). Returns None when never proposed.
        """

        for entry in reversed(self.audit_entries()):
            if entry.kind != "intent" or entry.client_order_id != client_order_id:
                continue
            proposal_payload = entry.payload.get("proposal")
            if isinstance(proposal_payload, dict):
                created = entry.payload.get("created_at")
                if isinstance(created, str):
                    proposal_payload = dict(proposal_payload)
                    proposal_payload.setdefault("created_at", created)
                return OrderProposal.model_validate(proposal_payload)
        return None

    # --------------------------------------------------------------- preview

    def preview(self, proposal: OrderProposal) -> OrderExecutionPreview:
        """Pure, offline preview; never touches a network or a broker."""

        price = proposal.limit_price or proposal.reference_price
        notional = None if price is None else price * proposal.quantity
        warnings: list[str] = []
        if notional is None:
            warnings.append("notional cannot be estimated")
        return OrderExecutionPreview(
            proposal=proposal,
            proposal_hash=proposal.proposal_hash(),
            estimated_notional=notional,
            currency=proposal.currency,
            warnings=warnings,
        )

    # -------------------------------------------------------------- evaluate

    def evaluate(self, proposal: OrderProposal, *, armed: bool) -> ExecutionInterlockDecision:
        """Evaluate every interlock fail-closed; NEVER submits anything."""

        preview = self.preview(proposal)
        duplicate = self._duplicate_check(proposal)
        today_jst = _jst_date(self._clock())
        orders_submitted_today = len(self._submits_by_jst_day.get(today_jst, ()))
        decision = evaluate_execution_interlocks(
            proposal,
            preview.estimated_notional,
            self._settings,
            armed=armed,
            orders_submitted_today=orders_submitted_today,
            duplicate_check=duplicate,
        )
        self._audit.append(
            "state",
            proposal.client_order_id,
            {
                "stage": "evaluate",
                "armed": armed,
                "allowed": decision.allowed,
                "reasons": list(decision.reasons),
                "proposal_hash": proposal.proposal_hash(),
            },
        )
        return decision

    # ------------------------------------------------------------- recording

    def record_request(self, client_order_id: str, payload: dict[str, object]) -> AuditEntry:
        """Audit wrapper for the future submission connector (no transport)."""

        entry = self._audit.append("request", client_order_id, payload)
        self._absorb(entry)
        return entry

    def record_response(self, client_order_id: str, payload: dict[str, object]) -> AuditEntry:
        """Audit wrapper for the future submission connector (no transport)."""

        entry = self._audit.append("response", client_order_id, payload)
        self._absorb(entry)
        return entry

    def record_state(self, client_order_id: str, payload: dict[str, object]) -> AuditEntry:
        """Audit wrapper for arbitrary state transitions (no transport)."""

        entry = self._audit.append("state", client_order_id, payload)
        self._absorb(entry)
        return entry

    # ----------------------------------------------------------------- audit

    def audit_entries(self, limit: int | None = None) -> list[AuditEntry]:
        entries = self._audit.entries()
        if limit is not None:
            return entries[-limit:] if limit > 0 else []
        return entries

    def verify_audit(self) -> list[str]:
        return self._audit.verify()

    def duplicate_check(self, proposal: OrderProposal) -> DuplicateCheckResult:
        """Public idempotency check against the replayed registry."""

        return self._duplicate_check(proposal)

    def audit_log(self) -> AppendOnlyAuditLog:
        return self._audit


__all__ = [
    "JST",
    "REQUEST_STAGE_SUBMIT",
    "BrokerExecutionDomainService",
    "DuplicateProposalError",
    "OrderExecutionPreview",
]
