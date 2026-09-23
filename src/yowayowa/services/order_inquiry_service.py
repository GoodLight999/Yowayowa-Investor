"""Order inquiry service: audit-matched read-only order status (P2C2).

Composes the P1B broker-read connector (the only network path) with the
P2A execution audit trail. The web session is queried twice per report
(open_orders + order_history) and every returned row is matched against
the submit-time audit evidence, so the operator sees, for each broker
order, whether it corresponds to a locally audited submission.

Read-only by construction: this service never calls record_*/append on
the audit trail — an inquiry must never change the evidence it reads.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel

from yowayowa.acquisition.auth import strip_url_query
from yowayowa.acquisition.models import AcquisitionFetchState, AuthState
from yowayowa.broker.execution.models import OrderProposal
from yowayowa.broker.execution.service import REQUEST_STAGE_SUBMIT, BrokerExecutionDomainService
from yowayowa.broker_models import (
    RAKUTEN_SECURITIES_BROKER,
    BrokerOrder,
    BrokerOrderSide,
    BrokerOrderStatus,
)
from yowayowa.services.broker_read_service import BrokerReadService

MARKET_PATTERN = Literal["jp", "us"]

OrderMatchKind = Literal["audit_matched", "unmatched_web", "audit_only"]

_AUDIT_ONLY_NOTE = (
    "order is audited as submitted but not visible in the web inquiry; "
    "verify the order on the broker's order status page"
)


class OrderInquiryItem(BaseModel):
    order: BrokerOrder
    match: OrderMatchKind
    client_order_id: str | None = None
    proposal_hash: str | None = None


class OrderInquiryReport(BaseModel):
    broker: str
    market: str
    generated_at: datetime
    fetch_state_open: AcquisitionFetchState
    fetch_state_history: AcquisitionFetchState
    auth_state: AuthState
    items: list[OrderInquiryItem]
    notes: list[str]
    intact_audit: bool
    source_urls: list[str]


class _AuditOrderMeta(BaseModel):
    """Submit-response evidence for one client_order_id."""

    client_order_id: str
    broker_order_id: str
    proposal_hash: str | None
    status: BrokerOrderStatus


class OrderInquiryService:
    """Match web-queried orders against the execution audit trail."""

    def __init__(
        self,
        *,
        broker_read: BrokerReadService,
        execution: BrokerExecutionDomainService,
    ) -> None:
        self._broker_read = broker_read
        self._execution = execution

    # ---------------------------------------------------------------- queries

    def list_orders(
        self,
        market: str = "jp",
        force_refresh: bool = False,
    ) -> OrderInquiryReport:
        """Query open_orders + order_history and audit-match every row."""

        open_outcome = self._broker_read.fetch("open_orders", market, force_refresh=force_refresh)
        history_outcome = self._broker_read.fetch(
            "order_history", market, force_refresh=force_refresh
        )

        notes: list[str] = []
        intact = not self._execution.verify_audit()
        if not intact:
            notes.append(
                "audit chain verification reported problems; audit matching may be unreliable"
            )

        source_urls: list[str] = []
        for outcome in (open_outcome, history_outcome):
            if outcome.source_url:
                source_urls.append(strip_url_query(outcome.source_url))
            notes.extend(outcome.notes)

        audit_meta = self._audit_response_meta()
        items = self._build_items(
            open_outcome.orders,
            history_outcome.orders,
            audit_meta,
            notes,
        )

        return OrderInquiryReport(
            broker=RAKUTEN_SECURITIES_BROKER,
            market=market,
            generated_at=datetime.now(UTC),
            fetch_state_open=open_outcome.fetch_state,
            fetch_state_history=history_outcome.fetch_state,
            auth_state=_worst_auth_state(open_outcome.auth_state, history_outcome.auth_state),
            items=items,
            notes=notes,
            intact_audit=intact,
            source_urls=source_urls,
        )

    def order_status(self, client_order_id: str, market: str = "jp") -> OrderInquiryReport:
        """One audited client_order_id, matched the same way as list_orders.

        The audit trail is the identity source: an id that was never
        proposed yields an unknown report (the API layer turns this into
        a 404), and an audited id is answered with 0 or 1 item. Per the
        design (D3), ``audit_only`` does NOT apply here: a proposal that
        never reached a web row in the inquiry yields 0 items — the
        operator is told to re-check the order on the broker's inquiry
        pages instead of being shown a reconstructed row.
        """

        proposal = self._execution.find_audited_proposal(client_order_id)
        intact = not self._execution.verify_audit()
        if proposal is None:
            return OrderInquiryReport(
                broker=RAKUTEN_SECURITIES_BROKER,
                market=market,
                generated_at=datetime.now(UTC),
                fetch_state_open=AcquisitionFetchState.OK,
                fetch_state_history=AcquisitionFetchState.OK,
                auth_state=AuthState.UNKNOWN,
                items=[],
                notes=[f"unknown client_order_id: {client_order_id}"],
                intact_audit=intact,
                source_urls=[],
            )

        report = self.list_orders(market=market)
        matched = [
            item
            for item in report.items
            if item.client_order_id == client_order_id and item.match != "audit_only"
        ]
        if matched:
            return _report_with(report, items=matched)
        return _report_with(
            report,
            items=[],
            notes=[
                *report.notes,
                "no row for this client_order_id in the web inquiry; "
                "verify the order on the broker's order status page",
            ],
        )

    # -------------------------------------------------------------- matching

    def _audit_response_meta(self) -> dict[str, _AuditOrderMeta]:
        """One pass over the audit trail keyed by BROKER order id (D3).

        Only kind="response" entries with payload.stage=="submit" and a
        non-None broker_order_id contribute. When the same broker_order_id
        appears more than once the LATEST response entry wins (later write
        in trail order overwrites the map).
        """

        by_broker_id: dict[str, _AuditOrderMeta] = {}
        for entry in self._execution.audit_entries():
            if entry.kind != "response":
                continue
            if entry.payload.get("stage") != REQUEST_STAGE_SUBMIT:
                continue
            raw_broker_id = entry.payload.get("broker_order_id")
            if raw_broker_id is None:
                continue
            raw_hash = entry.payload.get("proposal_hash")
            by_broker_id[str(raw_broker_id)] = _AuditOrderMeta(
                client_order_id=entry.client_order_id,
                broker_order_id=str(raw_broker_id),
                proposal_hash=str(raw_hash) if isinstance(raw_hash, str) and raw_hash else None,
                status=_status_from_payload(entry.payload),
            )
        return by_broker_id

    def _build_items(
        self,
        open_orders: list[BrokerOrder],
        history_orders: list[BrokerOrder],
        audit_meta: dict[str, _AuditOrderMeta],
        notes: list[str],
    ) -> list[OrderInquiryItem]:
        """Merge, dedupe by broker_order_id (history wins), and classify.

        ``audit_meta`` is keyed by broker_order_id. Order: open_orders
        rows first (in query order), then history rows not already seen.
        On a duplicate broker_order_id the order-history row (newer
        state) replaces the open-orders row in place. Audited
        submissions with no visible web row are appended as audit_only
        items and also noted.
        """

        deduped = _merge_orders(open_orders, history_orders)

        items: list[OrderInquiryItem] = []
        matched_client_ids: set[str] = set()
        for order in deduped:
            meta = audit_meta.get(order.broker_order_id)
            if meta is not None:
                matched_client_ids.add(meta.client_order_id)
                items.append(
                    OrderInquiryItem(
                        order=order,
                        match="audit_matched",
                        client_order_id=meta.client_order_id,
                        proposal_hash=meta.proposal_hash,
                    )
                )
            else:
                items.append(
                    OrderInquiryItem(
                        order=order,
                        match="unmatched_web",
                        client_order_id=None,
                        proposal_hash=None,
                    )
                )

        for meta in audit_meta.values():
            if meta.client_order_id in matched_client_ids:
                continue
            proposal = self._execution.find_audited_proposal(meta.client_order_id)
            if proposal is None:
                continue
            notes.append(f"{meta.client_order_id}: {_AUDIT_ONLY_NOTE}")
            items.append(
                OrderInquiryItem(
                    order=_audit_only_order(proposal, meta),
                    match="audit_only",
                    client_order_id=meta.client_order_id,
                    proposal_hash=meta.proposal_hash,
                )
            )
        return items


def _merge_orders(*sources: Sequence[BrokerOrder]) -> list[BrokerOrder]:
    """First-seen order preserved; duplicates keep the LATER row (newer)."""

    deduped: list[BrokerOrder] = []
    positions: dict[str, int] = {}
    for source in sources:
        for order in source:
            if not order.broker_order_id:
                deduped.append(order)
                continue
            existing = positions.get(order.broker_order_id)
            if existing is None:
                positions[order.broker_order_id] = len(deduped)
                deduped.append(order)
            else:
                deduped[existing] = order
    return deduped


def _status_from_payload(payload: dict[str, object]) -> BrokerOrderStatus:
    try:
        return BrokerOrderStatus(str(payload.get("status", "")))
    except ValueError:
        return BrokerOrderStatus.UNKNOWN


def _audit_only_order(proposal: OrderProposal, meta: _AuditOrderMeta) -> BrokerOrder:
    """Restore a BrokerOrder from audit evidence (no web row available).

    symbol/side/quantity come from the audited proposal; status comes
    from the recorded submit response. broker_order_id is the audit's
    recorded response value. filled fields stay at their zero defaults:
    the audit holds no fill evidence (missing data is not zero).
    """

    return BrokerOrder(
        broker=RAKUTEN_SECURITIES_BROKER,
        client_order_id=proposal.client_order_id,
        broker_order_id=meta.broker_order_id,
        symbol=proposal.symbol,
        side=BrokerOrderSide(proposal.side.value),
        quantity=proposal.quantity,
        filled_quantity=0,
        average_fill_price=None,
        status=meta.status,
    )


def _worst_auth_state(*states: AuthState) -> AuthState:
    """Fail-closed summary: any doubt about authentication wins."""

    if AuthState.UNAUTHENTICED in states:
        return AuthState.UNAUTHENTICED
    if all(state == AuthState.AUTHENTICATED for state in states):
        return AuthState.AUTHENTICATED
    return AuthState.UNKNOWN


def _report_with(
    report: OrderInquiryReport,
    *,
    items: list[OrderInquiryItem],
    notes: list[str] | None = None,
) -> OrderInquiryReport:
    return report.model_copy(update={"items": items, "notes": notes or report.notes})


__all__ = [
    "OrderInquiryItem",
    "OrderInquiryReport",
    "OrderInquiryService",
]
