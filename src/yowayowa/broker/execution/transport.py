"""Rakuten Securities web submission transport (P2B).

The only component permitted to translate an ALLOWED interlock decision
plus an explicit operator arming state into a real broker submission
through the operator's authenticated browser session.

DOUBLE GATE (COO ruling, spec revision 2): ``submissions_enabled`` ships
as False and the CLI does not enable it by default. While it is False,
``submit_order`` rejects EVERY submission path before proposal lookup,
session access, DOM interaction, or any ``stage=submit`` request audit,
and records a ``stage=submit-frozen`` state entry instead. The gate is
evaluated first, so the frozen decision cannot be bypassed by any
argument combination (armed settings, authenticated session, replayed
intent, or otherwise).

No playwright import lives here: the session is injected and satisfies a
structural protocol (``open`` / ``request`` / ``page``); the CLI starts
the real ``PersistentBrokerWebSession`` lazily.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

from yowayowa.acquisition.auth import (
    AuthSignal,
    HeuristicAuthDetector,
    strip_url_query,
)
from yowayowa.acquisition.models import AuthState
from yowayowa.broker.execution.interlocks import REASON_DUPLICATE_MISMATCH
from yowayowa.broker.execution.models import OrderProposal
from yowayowa.broker.execution.service import (
    REQUEST_STAGE_SUBMIT,
    BrokerExecutionDomainService,
)
from yowayowa.broker_models import (
    RAKUTEN_LOGIN_URL_MARKERS,
    RAKUTEN_SECURITIES_BROKER,
    BrokerAccountSnapshot,
    BrokerCapabilities,
    BrokerOrder,
    BrokerOrderIntent,
    BrokerOrderPreview,
    BrokerOrderReceipt,
    BrokerOrderStatus,
    BrokerPosition,
    BrokerQuote,
    BrokerTransport,
)
from yowayowa.config import Settings
from yowayowa.operator_bridge.rakuten_web import RAKUTEN_WEB_LOGIN_PATH

if TYPE_CHECKING:
    from yowayowa.broker.session_notify import SessionExpiryNotifier

RAKUTEN_SUBMISSION_BROKER = RAKUTEN_SECURITIES_BROKER
TRANSPORT_NAME = "authenticated-web-session"

STAGE_SUBMIT = REQUEST_STAGE_SUBMIT
STAGE_SUBMIT_FROZEN = "submit-frozen"
STAGE_SUBMIT_BLOCKED = "submit-blocked"
STAGE_SUBMIT_REPLAYED = "submit-replayed"
STAGE_SUBMIT_FAILED = "submit-failed"

REASON_NO_AUDITED_PROPOSAL = "no audited proposal for client_order_id"
REASON_INTENT_MISMATCH = "intent does not match the audited proposal content"
REASON_NOT_AUTHENTICATED = "broker web session is not authenticated; operator login required"

SUBMIT_FROZEN_REASON = (
    "submission recording frozen by COO ruling until audit integrity rework lands"
)
SUBMIT_FROZEN_MESSAGE = (
    "real order submission is frozen by COO ruling while submissions_enabled=False; "
    "every submission path is rejected before proposal lookup, DOM access, or "
    "stage=submit audit until the gate is explicitly reopened"
)
REPLAY_INQUIRY_PHRASE = "\u6ce8\u6587\u7167\u4f1a\u3067\u78ba\u8a8d\u305b\u3088"
RESEND_INQUIRY_PHRASE = (
    "\u767a\u6ce8\u6210\u7acb\u306e\u53ef\u80fd\u6027\u304c\u3042\u308b\u305f\u3081"
    "\u518d\u9001\u4fe1\u524d\u306b\u6ce8\u6587\u7167\u4f1a\u3067\u78ba\u8a8d\u305b\u3088"
)

_BODY_MARKER_SCAN_LIMIT = 2048
_AUTH_DETECTOR = HeuristicAuthDetector(login_url_markers=RAKUTEN_LOGIN_URL_MARKERS)
_BROKER_ORDER_ID_RE = re.compile(
    "(?:\u6ce8\u6587\u756a\u53f7|\u53d7\u4ed8\u756a\u53f7)\\s*[:\uff1a]?\\s*([0-9A-Za-z-]{4,})"
)


class BrokerConnectorFeatureError(RuntimeError):
    """Raised for BrokerConnector methods this transport does not serve.

    The submission transport intentionally implements only preview and
    submit; reads belong to the P1B broker-read connector and cancel is
    out of scope. Fail-closed by construction.
    """


class SubmissionWebSession(Protocol):
    """Structural protocol for the injected broker web session.

    Deliberately minimal: the transport issues GET probes via ``request``
    and drives the order form via ``open``/``page``. The production
    implementation is ``PersistentBrokerWebSession`` (playwright lives
    there, never here).
    """

    def open(self, path: str = "") -> Any: ...

    def request(self, method: str, path: str) -> Any: ...

    def page(self) -> Any: ...


# ---------------------------------------------------------------------------
# Order form catalog
# ---------------------------------------------------------------------------

# URL PROVENANCE NOTICE
# Every URL and selector in RAKUTEN_WEB_ORDER_FORM is an UNVERIFIED INITIAL
# ASSUMPTION authored before any real submission session (verified=False),
# exactly like the read-side catalog in operator_bridge/rakuten_web.py.
# Confirm the actual order-flow URLs and DOM selectors with browser devtools
# during the first real operator session, update this constant, and only
# then flip verified=True. Until then these values must not be treated as
# confirmed fact, and any real run is expected to fail at the DOM step and
# be audited as stage=submit-failed (fail-closed) rather than "succeed".
RAKUTEN_WEB_ORDER_FORM: dict[str, Any] = {
    "verified": False,
    # VERIFIED 2026-09-23 (CTO live check): the probe targets the pinned real
    # login page. order_entry_path and selectors below remain UNVERIFIED
    # initial assumptions; "verified" continues to describe the order-form
    # selector validation state only.
    "auth_probe_path": RAKUTEN_WEB_LOGIN_PATH,
    "order_entry_path": "app/order_entry.do",
    "selectors": {
        "symbol": "#input_symbol",
        "quantity": "#input_quantity",
        "limit_price": "#input_limit_price",
        "submit": "#button_submit_order",
        "confirmation_order_id": "#confirm_order_number",
    },
    "market_codes": {"jp": "1", "us": "2"},
    "side_codes": {"buy": "B", "sell": "S"},
    "order_type_codes": {"market": "M", "limit": "L"},
}


def _strip_query(url: str) -> str:
    """Return the URL without its query string (audit-safe form)."""

    return strip_url_query(url)


def _extract_broker_order_id(page: Any) -> tuple[str | None, str]:
    """Broker order number from the confirmation page, or None (not found).

    Tries the dedicated confirmation selector first, then a text marker
    over the rendered content. A missing number is a parse failure, never
    an acceptance.
    """

    selectors = RAKUTEN_WEB_ORDER_FORM["selectors"]
    try:
        raw = page.text_content(selectors["confirmation_order_id"])
    except Exception:
        raw = None
    if raw:
        candidate = str(raw).strip()
        if candidate:
            return candidate, "confirmation_selector"
    try:
        content = page.content()
    except Exception:
        content = ""
    match = _BROKER_ORDER_ID_RE.search(content or "")
    if match:
        return match.group(1), "text_marker"
    return None, "not_found"


class RakutenWebSubmissionTransport:
    """BrokerConnector over the operator's authenticated Rakuten web session.

    Submissions are gated twice: the COO freeze gate
    (``submissions_enabled``, evaluated first) and the P2A interlock
    chain (settings gates, arming, notional, duplicates). Every blocked
    or failed path is audited and returns a non-accepted receipt; no
    path can bypass the audit trail.
    """

    def __init__(
        self,
        *,
        session: SubmissionWebSession,
        service: BrokerExecutionDomainService,
        settings: Settings,
        clock: Callable[[], datetime] | None = None,
        submissions_enabled: bool = False,
        expiry_notifier: SessionExpiryNotifier | None = None,
    ) -> None:
        self._session = session
        self._service = service
        self._settings = settings
        self._clock: Callable[[], datetime] = clock or (lambda: datetime.now(UTC))
        self._submissions_enabled = submissions_enabled
        self._expiry_notifier = expiry_notifier

    # ------------------------------------------------------------ capabilities

    @property
    def capabilities(self) -> BrokerCapabilities:
        """Capabilities that never overstate the gate: frozen means no submit."""

        return BrokerCapabilities(
            broker=RAKUTEN_SUBMISSION_BROKER,
            transport=BrokerTransport.AUTHENTICATED_WEB_SESSION,
            order_submission=self._submissions_enabled,
            scraping=True,
        )

    # ----------------------------------------------------------------- preview

    def preview_order(self, intent: BrokerOrderIntent) -> BrokerOrderPreview:
        """Pure, offline preview. Never touches the session or the network."""

        notional = intent.estimated_notional()
        warnings: list[str] = []
        if notional is None:
            warnings.append("notional cannot be estimated")
        return BrokerOrderPreview(
            broker=RAKUTEN_SUBMISSION_BROKER,
            transport=BrokerTransport.AUTHENTICATED_WEB_SESSION,
            intent=intent,
            estimated_notional=notional,
            currency=intent.currency,
            warnings=warnings,
        )

    # ----------------------------------------------------------------- submit

    def submit_order(
        self,
        intent: BrokerOrderIntent,
        *,
        armed: bool = False,
    ) -> BrokerOrderReceipt:
        """Submit one audited proposal through the web session (fail-closed).

        Step order is binding (spec revision 2): the COO freeze gate is
        evaluated FIRST so that no argument combination can reach the
        proposal lookup, the session, the DOM, or the stage=submit audit
        while submissions_enabled=False.
        """

        client_order_id = intent.client_order_id
        # 1. Submission master gate (COO freeze): first and absolute.
        if not self._submissions_enabled:
            self._service.record_state(
                client_order_id,
                {"stage": STAGE_SUBMIT_FROZEN, "reason": SUBMIT_FROZEN_REASON, "armed": armed},
            )
            return self._rejected_receipt(client_order_id, SUBMIT_FROZEN_MESSAGE)

        entries = self._service.audit_entries()
        # 2. Proposal discovery from the audit trail (no re-input).
        proposal = self._find_audited_proposal(client_order_id)
        if proposal is None:
            return self._blocked_receipt(client_order_id, (REASON_NO_AUDITED_PROPOSAL,))

        # 3. Intent integration check (economic fields + proposal hash).
        if not self._intent_matches_proposal(intent, proposal):
            return self._blocked_receipt(client_order_id, (REASON_INTENT_MISMATCH,))
        audited_hash = self._audited_proposal_hash(entries, client_order_id)
        if audited_hash is not None and audited_hash != proposal.proposal_hash():
            return self._blocked_receipt(client_order_id, (REASON_INTENT_MISMATCH,))

        # 4. Idempotent replay: never auto-resubmit a known submission.
        duplicate = self._service.duplicate_check(proposal)
        if duplicate.mismatch:
            return self._blocked_receipt(client_order_id, (REASON_DUPLICATE_MISMATCH,))
        if self._has_prior_submit(entries, client_order_id):
            return self._replayed_receipt(entries, client_order_id, proposal.proposal_hash())

        # 5. Interlock evaluation (settings gates, arming, notional).
        decision = self._service.evaluate(proposal, armed=armed)
        if not decision.allowed:
            return self._blocked_receipt(client_order_id, decision.reasons)

        # 6. Authentication probe (GET only; never a POST).
        if not self._probe_authenticated():
            if self._expiry_notifier is not None:
                self._expiry_notifier.notify_session_expired(source="broker-exec")
            return self._blocked_receipt(client_order_id, (REASON_NOT_AUTHENTICATED,))

        # 7. Request audit: the ONLY site that writes stage=submit.
        self._service.record_request(
            client_order_id,
            {
                "stage": STAGE_SUBMIT,
                "proposal_hash": proposal.proposal_hash(),
                "armed": True,
                "transport": TRANSPORT_NAME,
                "order_form": self._order_form_view(proposal),
            },
        )

        # 8. DOM submission (exceptions are audited, never swallowed).
        try:
            page = self._session.open(RAKUTEN_WEB_ORDER_FORM["order_entry_path"])
            self._fill_order_form(page, proposal)
            page.click(RAKUTEN_WEB_ORDER_FORM["selectors"]["submit"])
            broker_order_id, evidence = _extract_broker_order_id(page)
            confirmation_url = _strip_query(str(getattr(page, "url", "") or ""))
        except Exception as exc:  # audited failure boundary (no bare-except swallow)
            error_text = f"{type(exc).__name__}: {exc}"[:400]
            self._service.record_state(
                client_order_id,
                {"stage": STAGE_SUBMIT_FAILED, "error": error_text},
            )
            return BrokerOrderReceipt(
                broker=RAKUTEN_SUBMISSION_BROKER,
                client_order_id=client_order_id,
                accepted=False,
                status=BrokerOrderStatus.UNKNOWN,
                submitted_at=self._clock(),
                message=f"submission failed ({type(exc).__name__}); {RESEND_INQUIRY_PHRASE}",
            )

        # 10. Receipt: accepted ONLY with a broker order number in hand.
        accepted = broker_order_id is not None
        if accepted:
            status = BrokerOrderStatus.ACCEPTED
            message = f"order accepted by broker web form (order number {broker_order_id})"
        else:
            status = BrokerOrderStatus.UNKNOWN
            message = (
                "could not read the broker order number from the confirmation page; "
                f"{RESEND_INQUIRY_PHRASE}"
            )

        # 9. Response audit (no raw HTML, query-stripped URL only).
        self._service.record_response(
            client_order_id,
            {
                "stage": STAGE_SUBMIT,
                "accepted": accepted,
                "broker_order_id": broker_order_id,
                "status": status.value,
                "message": message,
                "confirmation_url": confirmation_url,
                "evidence": {"order_id_evidence": evidence, "form_verified": False},
            },
        )
        return BrokerOrderReceipt(
            broker=RAKUTEN_SUBMISSION_BROKER,
            client_order_id=client_order_id,
            broker_order_id=broker_order_id,
            accepted=accepted,
            status=status,
            submitted_at=self._clock(),
            message=message,
        )

    # ------------------------------------------------ fail-closed rest of protocol

    def list_orders(self) -> list[BrokerOrder]:
        raise BrokerConnectorFeatureError(
            "list_orders is not served by the submission transport; "
            "use the P1B broker-read connector (yowayowa broker-read)"
        )

    def list_positions(self) -> list[BrokerPosition]:
        raise BrokerConnectorFeatureError(
            "list_positions is not served by the submission transport; "
            "use the P1B broker-read connector (yowayowa broker-read)"
        )

    def quote(self, symbol: str) -> BrokerQuote:
        raise BrokerConnectorFeatureError(
            "quote is not served by the submission transport; "
            "use the P1B broker-read connector or a market data provider"
        )

    def cancel_order(self, broker_order_id: str, *, client_order_id: str) -> BrokerOrderReceipt:
        raise BrokerConnectorFeatureError(
            "cancel_order is out of scope for the submission transport (not implemented in P2B)"
        )

    def account_snapshot(self) -> BrokerAccountSnapshot:
        raise BrokerConnectorFeatureError(
            "account_snapshot is not served by the submission transport; "
            "use the P1B broker-read connector (yowayowa broker-read)"
        )

    # ------------------------------------------------------------------ helpers

    def _rejected_receipt(self, client_order_id: str, message: str) -> BrokerOrderReceipt:
        return BrokerOrderReceipt(
            broker=RAKUTEN_SUBMISSION_BROKER,
            client_order_id=client_order_id,
            accepted=False,
            status=BrokerOrderStatus.REJECTED,
            submitted_at=self._clock(),
            message=message,
        )

    def _blocked_receipt(self, client_order_id: str, reasons: Sequence[str]) -> BrokerOrderReceipt:
        self._service.record_state(
            client_order_id,
            {"stage": STAGE_SUBMIT_BLOCKED, "reasons": list(reasons)},
        )
        return self._rejected_receipt(client_order_id, "; ".join(reasons))

    def _replayed_receipt(
        self,
        entries: list[Any],
        client_order_id: str,
        proposal_hash: str,
    ) -> BrokerOrderReceipt:
        """Rebuild a receipt from the recorded response; never resend."""

        prior = self._latest_submit_response(entries, client_order_id)
        restored_from_response = prior is not None
        if prior is not None:
            accepted = bool(prior.get("accepted", False))
            raw_id = prior.get("broker_order_id")
            broker_order_id = str(raw_id) if raw_id is not None else None
            try:
                status = BrokerOrderStatus(str(prior.get("status", "")))
            except ValueError:
                status = BrokerOrderStatus.UNKNOWN
            message = str(prior.get("message") or "") or "replayed prior broker response"
        else:
            accepted = False
            broker_order_id = None
            status = BrokerOrderStatus.UNKNOWN
            message = (
                "this proposal was already submitted but no broker response is recorded; "
                f"{REPLAY_INQUIRY_PHRASE}"
            )
        self._service.record_state(
            client_order_id,
            {
                "stage": STAGE_SUBMIT_REPLAYED,
                "proposal_hash": proposal_hash,
                "restored_from_response": restored_from_response,
            },
        )
        return BrokerOrderReceipt(
            broker=RAKUTEN_SUBMISSION_BROKER,
            client_order_id=client_order_id,
            broker_order_id=broker_order_id,
            accepted=accepted,
            status=status,
            submitted_at=self._clock(),
            message=message,
        )

    def _find_audited_proposal(self, client_order_id: str) -> OrderProposal | None:
        return self._service.find_audited_proposal(client_order_id)

    def _audited_proposal_hash(self, entries: list[Any], client_order_id: str) -> str | None:
        for entry in reversed(entries):
            if entry.kind != "intent" or entry.client_order_id != client_order_id:
                continue
            value = entry.payload.get("proposal_hash")
            return str(value) if isinstance(value, str) and value else None
        return None

    @staticmethod
    def _intent_matches_proposal(intent: BrokerOrderIntent, proposal: OrderProposal) -> bool:
        return (
            intent.client_order_id == proposal.client_order_id
            and intent.symbol == proposal.symbol
            and intent.side == proposal.side
            and intent.quantity == proposal.quantity
            and intent.order_type == proposal.order_type
            and intent.limit_price == proposal.limit_price
            and intent.reference_price == proposal.reference_price
            and intent.currency == proposal.currency
        )

    @staticmethod
    def _has_prior_submit(entries: list[Any], client_order_id: str) -> bool:
        return any(
            entry.kind == "request"
            and entry.client_order_id == client_order_id
            and entry.payload.get("stage") == STAGE_SUBMIT
            for entry in entries
        )

    @staticmethod
    def _latest_submit_response(entries: list[Any], client_order_id: str) -> dict[str, Any] | None:
        for entry in reversed(entries):
            if (
                entry.kind == "response"
                and entry.client_order_id == client_order_id
                and entry.payload.get("stage") == STAGE_SUBMIT
            ):
                payload = entry.payload
                return payload if isinstance(payload, dict) else None
        return None

    def _probe_authenticated(self) -> bool:
        """Detector-backed GET-only probe; anything unclear means unauthenticated.

        The probe targets the pinned real login page (RAKUTEN_WEB_LOGIN_PATH,
        VERIFIED 2026-09-23) and delegates the verdict to the shared
        HeuristicAuthDetector (login/signin/sign-in URL-path markers +
        default text markers; AUTHENTICATED only for a 2xx/3xx response).
        The URL-path scan is the single source of truth because
        PersistentBrokerWebSession.request uses max_redirects=0: when the
        session is alive the broker redirects the login-page GET away and
        the 30x response URL carries no login marker, while an expired
        session gets the login page itself back (login markers in the URL
        path and/or body). The former Location-header check is therefore
        redundant and was removed with the detector unification. Anything
        unclear stays fail-closed.
        """

        try:
            response = self._session.request("GET", str(RAKUTEN_WEB_ORDER_FORM["auth_probe_path"]))
        except Exception:
            # Network/DNS/session failures are NOT proof of authentication:
            # fail closed (blocked, unauthenticated) without raising.
            return False
        status = int(getattr(response, "status", 0) or 0)
        url = str(getattr(response, "url", "") or "")
        text = ""
        text_getter = getattr(response, "text", None)
        if callable(text_getter):
            try:
                text = str(text_getter() or "")[:_BODY_MARKER_SCAN_LIMIT]
            except Exception:
                text = ""
        try:
            state = _AUTH_DETECTOR.detect(AuthSignal(url=url, status_code=status, body_text=text))
        except Exception:
            # Detection must never raise its way into a submission: fail closed.
            return False
        return state is AuthState.AUTHENTICATED

    @staticmethod
    def _order_form_view(proposal: OrderProposal) -> dict[str, Any]:
        """Economic input values for the request audit (nothing private)."""

        return {
            "client_order_id": proposal.client_order_id,
            "symbol": proposal.symbol,
            "market": proposal.market,
            "side": proposal.side.value,
            "quantity": proposal.quantity,
            "order_type": proposal.order_type.value,
            "limit_price": None if proposal.limit_price is None else str(proposal.limit_price),
            "reference_price": (
                None if proposal.reference_price is None else str(proposal.reference_price)
            ),
            "currency": proposal.currency,
            "form_verified": RAKUTEN_WEB_ORDER_FORM["verified"],
        }

    @staticmethod
    def _fill_order_form(page: Any, proposal: OrderProposal) -> None:
        """Fill the (unverified-selector) order form; raises on any DOM error.

        No selector fallback exists by design (speculative fills against an
        unverified form are forbidden): instead, every fill failure is
        re-raised as the SAME exception type with the offending selector
        prefixed, so the stage=submit-failed audit entry carries selector
        context.
        """

        form = RAKUTEN_WEB_ORDER_FORM
        selectors = form["selectors"]

        def _fill(selector: str, value: str) -> None:
            try:
                page.fill(selector, value)
            except Exception as exc:
                raise type(exc)(f"selector {selector!r}: {exc}") from exc

        _fill(selectors["symbol"], proposal.symbol)
        _fill(selectors["quantity"], str(proposal.quantity))
        market_code = form["market_codes"].get(proposal.market)
        side_code = form["side_codes"].get(proposal.side.value)
        type_code = form["order_type_codes"].get(proposal.order_type.value)
        if market_code is not None:
            _fill(selectors.get("market", "#input_market"), str(market_code))
        if side_code is not None:
            _fill(selectors.get("side", "#input_side"), str(side_code))
        if type_code is not None:
            _fill(selectors.get("order_type", "#input_order_type"), str(type_code))
        if proposal.limit_price is not None:
            _fill(selectors["limit_price"], str(proposal.limit_price))


__all__ = [
    "RAKUTEN_SUBMISSION_BROKER",
    "RAKUTEN_WEB_ORDER_FORM",
    "REASON_INTENT_MISMATCH",
    "REASON_NOT_AUTHENTICATED",
    "REASON_NO_AUDITED_PROPOSAL",
    "STAGE_SUBMIT",
    "STAGE_SUBMIT_BLOCKED",
    "STAGE_SUBMIT_FAILED",
    "STAGE_SUBMIT_FROZEN",
    "STAGE_SUBMIT_REPLAYED",
    "SUBMIT_FROZEN_REASON",
    "TRANSPORT_NAME",
    "BrokerConnectorFeatureError",
    "RakutenWebSubmissionTransport",
    "SubmissionWebSession",
]
