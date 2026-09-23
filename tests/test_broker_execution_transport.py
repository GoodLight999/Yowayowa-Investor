"""P2B RakutenWebSubmissionTransport tests (spec: 20+ required cases).

Every test injects a FakeBrokerWebSession; playwright is never needed.
The frozen-gate test (case 1) is the essence of the COO ruling: even the
fully-armed, fully-authenticated happy path must be rejected before any
stage=submit audit or DOM access while submissions_enabled=False.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from yowayowa.broker.execution.interlocks import (
    REASON_DUPLICATE_MISMATCH,
    REASON_NOT_ARMED,
    REASON_NOTIONAL_UNKNOWN,
)
from yowayowa.broker.execution.models import OrderProposal
from yowayowa.broker.execution.service import BrokerExecutionDomainService
from yowayowa.broker.execution.transport import (
    RAKUTEN_WEB_ORDER_FORM,
    REASON_INTENT_MISMATCH,
    REASON_NO_AUDITED_PROPOSAL,
    REASON_NOT_AUTHENTICATED,
    STAGE_SUBMIT,
    STAGE_SUBMIT_BLOCKED,
    STAGE_SUBMIT_FAILED,
    STAGE_SUBMIT_FROZEN,
    STAGE_SUBMIT_REPLAYED,
    SUBMIT_FROZEN_REASON,
    BrokerConnectorFeatureError,
    RakutenWebSubmissionTransport,
)
from yowayowa.broker_execution_cli import app as cli_app
from yowayowa.broker_models import BrokerOrderIntent, BrokerOrderStatus
from yowayowa.config import Settings

runner = CliRunner()


# --------------------------------------------------------------------- fakes


class _FakeResponse:
    def __init__(
        self,
        *,
        status: int = 200,
        url: str = "https://www.rakuten-sec.co.jp/accept/orders",
        body: str = "ok",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self.url = url
        self.body = body
        self.headers = headers or {}

    def text(self) -> str:
        return self.body


class FakeBrokerWebSession:
    """Minimal scriptable session fake: records every open/request/click."""

    def __init__(
        self,
        *,
        probe_response: _FakeResponse | None = None,
        order_id_on_confirm: str | None = "12345678",
        dom_error: Exception | None = None,
    ) -> None:
        self.probe_response = probe_response or _FakeResponse()
        self.order_id_on_confirm = order_id_on_confirm
        self.dom_error = dom_error
        self.open_calls: list[str] = []
        self.request_calls: list[tuple[str, str]] = []
        self.fill_calls: list[tuple[str, str]] = []
        self.click_calls: list[str] = []
        self.page_url = "https://www.rakuten-sec.co.jp/app/order_entry.do?foo=bar"

    def open(self, path: str = "") -> _FakePage:
        self.open_calls.append(path)
        if self.dom_error is not None:
            raise self.dom_error
        return _FakePage(self, self.order_id_on_confirm, self.page_url)

    def request(self, method: str, path: str) -> _FakeResponse:
        self.request_calls.append((method, path))
        return self.probe_response

    def start(self) -> None:
        return None

    def close(self) -> None:
        return None

    def page(self) -> Any:
        raise AssertionError("page() must not be called before open() in the submit flow")


class _FakePage:
    def __init__(self, session: FakeBrokerWebSession, order_id: str | None, url: str) -> None:
        self._session = session
        self._order_id = order_id
        self.url = url

    def fill(self, selector: str, value: str) -> None:
        self._session.fill_calls.append((selector, value))

    def click(self, selector: str) -> None:
        self._session.click_calls.append(selector)

    def text_content(self, selector: str) -> str | None:
        return self._order_id

    def content(self) -> str:
        if self._order_id is not None:
            return f"<html>ご注文ありがとうございます 注文番号: {self._order_id}</html>"
        return "<html>エラー</html>"


# -------------------------------------------------------------------- helpers


def _settings(**overrides: Any) -> Settings:
    fields: dict[str, Any] = {"mode": "personal"}
    fields.update(overrides)
    return Settings(**fields)


def _armed_settings(**overrides: Any) -> Settings:
    fields: dict[str, Any] = {
        "mode": "personal",
        "broker_live_orders_enabled": True,
        "broker_max_single_order_notional": 500000,
        "broker_max_orders_per_day": 20,
    }
    fields.update(overrides)
    return Settings(**fields)


_FIXED_NOW = datetime(2026, 9, 23, 2, 0, tzinfo=UTC)


def _service(
    tmp_path: Path,
    settings: Settings | None = None,
    clock: Callable[[], datetime] | None = None,
) -> BrokerExecutionDomainService:
    return BrokerExecutionDomainService(
        settings=settings if settings is not None else _armed_settings(),
        audit_dir=tmp_path / "audit",
        clock=clock or (lambda: _FIXED_NOW),
    )


def _proposal(**overrides: Any) -> OrderProposal:
    fields: dict[str, Any] = {
        "client_order_id": "co-1",
        "symbol": "7203",
        "market": "jp",
        "side": "buy",
        "quantity": 100,
        "order_type": "limit",
        "limit_price": Decimal("3000"),
        "reference_price": Decimal("2995"),
        "currency": "JPY",
        "motivation": "research says cheap",
    }
    fields.update(overrides)
    if isinstance(fields.get("limit_price"), str):
        fields["limit_price"] = Decimal(fields["limit_price"])
    if isinstance(fields.get("reference_price"), str):
        fields["reference_price"] = Decimal(fields["reference_price"])
    return OrderProposal.model_validate(fields)


def _intent(**overrides: Any) -> BrokerOrderIntent:
    fields: dict[str, Any] = {
        "client_order_id": "co-1",
        "symbol": "7203",
        "side": "buy",
        "quantity": 100,
        "order_type": "limit",
        "limit_price": Decimal("3000"),
        "reference_price": Decimal("2995"),
        "currency": "JPY",
    }
    fields.update(overrides)
    if isinstance(fields.get("limit_price"), str):
        fields["limit_price"] = Decimal(fields["limit_price"])
    if isinstance(fields.get("reference_price"), str):
        fields["reference_price"] = Decimal(fields["reference_price"])
    return BrokerOrderIntent.model_validate(fields)


def _transport(
    service: BrokerExecutionDomainService,
    session: FakeBrokerWebSession | None = None,
    *,
    submissions_enabled: bool = False,
    settings: Settings | None = None,
) -> RakutenWebSubmissionTransport:
    return RakutenWebSubmissionTransport(
        session=session if session is not None else FakeBrokerWebSession(),
        service=service,
        settings=settings if settings is not None else _armed_settings(),
        clock=lambda: _FIXED_NOW,
        submissions_enabled=submissions_enabled,
    )


def _audited_proposal_service(
    tmp_path: Path, **proposal_overrides: Any
) -> tuple[BrokerExecutionDomainService, OrderProposal]:
    service = _service(tmp_path)
    proposal = service.propose_model(_proposal(**proposal_overrides))
    return service, proposal


def _state_stages(service: BrokerExecutionDomainService) -> list[str]:
    return [
        str(entry.payload.get("stage"))
        for entry in service.audit_entries()
        if entry.kind == "state"
    ]


def _request_stages(service: BrokerExecutionDomainService) -> list[str]:
    return [
        str(entry.payload.get("stage"))
        for entry in service.audit_entries()
        if entry.kind == "request"
    ]


# =========================================================================
# 1. THE frozen gate: even the fully-correct path never reaches DOM/submit
# =========================================================================


def test_01_frozen_gate_rejects_full_correct_path_before_everything(tmp_path: Path) -> None:
    """armed=True + armed settings + authenticated fake + correct proposal:
    still REJECTED receipt + state(submit-frozen), no stage=submit, no DOM."""

    service, _proposal_obj = _audited_proposal_service(tmp_path)
    session = FakeBrokerWebSession()
    transport = _transport(service, session, submissions_enabled=False, settings=_armed_settings())
    receipt = transport.submit_order(_intent(), armed=True)
    assert receipt.accepted is False
    assert receipt.status is BrokerOrderStatus.REJECTED
    assert "frozen by COO ruling" in (receipt.message or "")
    assert "submissions_enabled=False" in (receipt.message or "")
    assert _state_stages(service) == [STAGE_SUBMIT_FROZEN]
    assert STAGE_SUBMIT not in _request_stages(service)
    assert session.open_calls == [] and session.request_calls == []
    assert session.fill_calls == [] and session.click_calls == []
    state_entry = next(entry for entry in service.audit_entries() if entry.kind == "state")
    assert state_entry.payload["reason"] == SUBMIT_FROZEN_REASON
    assert state_entry.payload["armed"] is True


def test_01b_frozen_gate_blocks_even_without_any_audited_proposal(tmp_path: Path) -> None:
    """The gate fires before proposal lookup: unknown id still submit-frozen."""

    service = _service(tmp_path)
    session = FakeBrokerWebSession()
    transport = _transport(service, session, submissions_enabled=False)
    receipt = transport.submit_order(_intent(client_order_id="never-proposed"))
    assert receipt.accepted is False
    assert receipt.status is BrokerOrderStatus.REJECTED
    assert _state_stages(service) == [STAGE_SUBMIT_FROZEN]
    assert session.open_calls == []


def test_01c_frozen_gate_blocks_replayed_and_duplicate_paths_too(tmp_path: Path) -> None:
    """A previously-submitted id re-submitted while frozen: submit-frozen only,
    never submit-replayed — the gate precedes the replay branch."""

    service, _p = _audited_proposal_service(tmp_path)
    # Simulate a prior real submission in the audit trail.
    service.record_request("co-1", {"stage": STAGE_SUBMIT, "proposal_hash": "h"})
    service.record_response("co-1", {"stage": STAGE_SUBMIT, "accepted": True, "status": "accepted"})
    session = FakeBrokerWebSession()
    transport = _transport(service, session, submissions_enabled=False)
    receipt = transport.submit_order(_intent(), armed=True)
    assert receipt.accepted is False
    assert receipt.status is BrokerOrderStatus.REJECTED
    assert STAGE_SUBMIT_FROZEN in _state_stages(service)
    assert STAGE_SUBMIT_REPLAYED not in _state_stages(service)


# =========================================================================
# 2. proposal missing (enabled config) -> submit-blocked, DOM untouched
# =========================================================================


def test_02_missing_audited_proposal_blocks_and_never_touches_dom(tmp_path: Path) -> None:
    service = _service(tmp_path)
    session = FakeBrokerWebSession()
    transport = _transport(service, session, submissions_enabled=True)
    receipt = transport.submit_order(_intent(client_order_id="ghost"), armed=True)
    assert receipt.accepted is False
    assert receipt.status is BrokerOrderStatus.REJECTED
    assert REASON_NO_AUDITED_PROPOSAL in (receipt.message or "")
    assert STAGE_SUBMIT_BLOCKED in _state_stages(service)
    assert session.open_calls == [] and session.request_calls == []
    assert session.fill_calls == [] and session.click_calls == []


# =========================================================================
# 3. intent mismatch (economic fields) -> blocked
# =========================================================================


def test_03_intent_mismatch_blocks(tmp_path: Path) -> None:
    service, _p = _audited_proposal_service(tmp_path)
    session = FakeBrokerWebSession()
    transport = _transport(service, session, submissions_enabled=True)
    receipt = transport.submit_order(_intent(quantity=999), armed=True)
    assert receipt.accepted is False
    assert REASON_INTENT_MISMATCH in (receipt.message or "")
    assert STAGE_SUBMIT_BLOCKED in _state_stages(service)
    assert session.open_calls == []


def test_03b_intent_hash_divergence_blocks(tmp_path: Path) -> None:
    """A doctored intent entry whose proposal_hash disagrees with its own
    proposal content is rejected by the hash cross-check."""

    service = _service(tmp_path)
    proposal = _proposal()
    service.record_state("co-1", {"note": "unrelated first entry"})
    service.propose_model(proposal)
    # Doctor the audit trail by hand: rewrite the intent payload hash.
    log = service.audit_log()
    lines = log.path.read_text(encoding="utf-8").splitlines()
    entries = [json.loads(line) for line in lines]
    for entry in entries:
        if entry["kind"] == "intent":
            entry["payload"]["proposal_hash"] = "0" * 64
    # Rewrite the whole file (verify() will flag chain breaks; the transport
    # must still refuse on hash mismatch before any session access).
    log.path.write_text(
        "\n".join(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) for entry in entries)
        + "\n",
        encoding="utf-8",
    )
    session = FakeBrokerWebSession()
    transport = _transport(service, session, submissions_enabled=True)
    receipt = transport.submit_order(_intent(), armed=True)
    assert receipt.accepted is False
    assert REASON_INTENT_MISMATCH in (receipt.message or "")
    assert session.open_calls == []


# =========================================================================
# 4. armed=False -> blocked(REASON_NOT_ARMED)
# =========================================================================


def test_04_not_armed_blocks_with_not_armed_reason(tmp_path: Path) -> None:
    service, _p = _audited_proposal_service(tmp_path)
    session = FakeBrokerWebSession()
    transport = _transport(service, session, submissions_enabled=True)
    receipt = transport.submit_order(_intent(), armed=False)
    assert receipt.accepted is False
    assert REASON_NOT_ARMED in (receipt.message or "")
    assert session.open_calls == []


# =========================================================================
# 5. settings gate not lifted -> blocked
# =========================================================================


def test_05_settings_gate_not_armed_blocks(tmp_path: Path) -> None:
    service, _p = _audited_proposal_service(tmp_path)
    service_settings = _settings()  # broker_live_orders_enabled defaults False
    plain_service = BrokerExecutionDomainService(
        settings=service_settings, audit_dir=tmp_path / "audit2", clock=lambda: _FIXED_NOW
    )
    plain_service.propose_model(_proposal())
    session = FakeBrokerWebSession()
    transport = RakutenWebSubmissionTransport(
        session=session,
        service=plain_service,
        settings=service_settings,
        clock=lambda: _FIXED_NOW,
        submissions_enabled=True,
    )
    receipt = transport.submit_order(_intent(), armed=True)
    assert receipt.accepted is False
    assert "live broker order submission is not armed" in (receipt.message or "")
    assert session.open_calls == []
    _ = service


# =========================================================================
# 6. notional not estimable -> blocked
# =========================================================================


def test_06_notional_unknown_blocks(tmp_path: Path) -> None:
    service = _service(tmp_path)
    proposal = service.propose_model(
        _proposal(
            order_type="market",
            limit_price=None,
            reference_price=None,
        )
    )
    intent = _intent(order_type="market", limit_price=None, reference_price=None)
    session = FakeBrokerWebSession()
    transport = _transport(service, session, submissions_enabled=True)
    receipt = transport.submit_order(intent, armed=True)
    assert receipt.accepted is False
    assert REASON_NOTIONAL_UNKNOWN in (receipt.message or "")
    assert session.open_calls == []
    _ = proposal


# =========================================================================
# 7. same id same hash already submitted -> replay, no extra send
# =========================================================================


def test_07_duplicate_same_hash_replays_without_resending(tmp_path: Path) -> None:
    # stage is attached manually here; the real transport write path is verified by test_07c.
    service, _p = _audited_proposal_service(tmp_path)
    service.record_request("co-1", {"stage": STAGE_SUBMIT, "proposal_hash": "h"})
    service.record_response(
        "co-1",
        {
            "stage": STAGE_SUBMIT,
            "accepted": True,
            "status": "accepted",
            "broker_order_id": "77777",
            "message": "order accepted by broker web form (order number 77777)",
        },
    )
    session = FakeBrokerWebSession()
    transport = _transport(service, session, submissions_enabled=True)
    receipt_before = len(service.audit_entries())
    receipt = transport.submit_order(_intent(), armed=True)
    assert receipt.accepted is True
    assert receipt.broker_order_id == "77777"
    assert receipt.status is BrokerOrderStatus.ACCEPTED
    assert STAGE_SUBMIT_REPLAYED in _state_stages(service)
    # No new request or response audit entries, no session traffic.
    kinds_after = [entry.kind for entry in service.audit_entries()]
    assert kinds_after.count("request") == 1
    assert kinds_after.count("response") == 1
    assert len(service.audit_entries()) == receipt_before + 1  # + state only
    assert session.open_calls == [] and session.request_calls == []


def test_07c_transport_writes_stage_key_and_replay_restores_receipt(tmp_path: Path) -> None:
    """The REAL transport write path: record_response must store stage=submit so
    the replay route (_latest_submit_response) can restore the prior receipt."""

    service, _p = _audited_proposal_service(tmp_path)
    session = FakeBrokerWebSession()
    transport = _transport(service, session, submissions_enabled=True)

    # 1st submit: real DOM path through the fake session.
    receipt = transport.submit_order(_intent(), armed=True)
    assert receipt.accepted is True
    assert receipt.broker_order_id == "12345678"

    # Core assertion: the response entry written by transport carries stage=submit.
    responses = [entry for entry in service.audit_entries() if entry.kind == "response"]
    assert len(responses) == 1
    assert responses[0].payload.get("stage") == STAGE_SUBMIT
    # Existing convention: the transport never issues non-GET HTTP requests.
    assert all(method == "GET" for method, _path in session.request_calls)

    # 2nd submit: same id, same hash -> receipt restored, no resend.
    session2 = FakeBrokerWebSession()
    transport2 = _transport(service, session2, submissions_enabled=True)
    receipt2 = transport2.submit_order(_intent(), armed=True)
    assert receipt2.accepted is True
    assert receipt2.broker_order_id == "12345678"
    assert receipt2.status is BrokerOrderStatus.ACCEPTED
    assert STAGE_SUBMIT_REPLAYED in _state_stages(service)

    # No new request or response audit entries, no session traffic.
    kinds_after = [entry.kind for entry in service.audit_entries()]
    assert kinds_after.count("request") == 1
    assert kinds_after.count("response") == 1
    assert session2.open_calls == [] and session2.request_calls == []


def test_07b_replay_without_recorded_response_returns_unknown_inquiry(tmp_path: Path) -> None:
    service, _p = _audited_proposal_service(tmp_path)
    service.record_request("co-1", {"stage": STAGE_SUBMIT, "proposal_hash": "h"})
    session = FakeBrokerWebSession()
    transport = _transport(service, session, submissions_enabled=True)
    receipt = transport.submit_order(_intent(), armed=True)
    assert receipt.accepted is False
    assert receipt.status is BrokerOrderStatus.UNKNOWN
    assert "注文照会" in (receipt.message or "")
    assert STAGE_SUBMIT_REPLAYED in _state_stages(service)
    assert session.open_calls == []


# =========================================================================
# 8. same id different hash -> blocked(REASON_DUPLICATE_MISMATCH)
# =========================================================================


def test_08_duplicate_mismatch_blocks(tmp_path: Path) -> None:
    """Registry hash for this id differs from the live proposal hash: the
    duplicate interlock (not the intent-match check) must reject it."""

    service, _p = _audited_proposal_service(tmp_path)
    # Absorb a *different* hash for the same id into the registry, exactly as
    # a tampered/replayed trail would present it.
    real_hash = _proposal().proposal_hash()
    different = _proposal(quantity=42)
    assert different.proposal_hash() != real_hash
    service.record_state("co-1", {"note": "unrelated"})
    service._proposals["co-1"] = type(service._proposals["co-1"])(
        proposal_hash=different.proposal_hash(), proposal_id="x"
    )
    session = FakeBrokerWebSession()
    transport = _transport(service, session, submissions_enabled=True)
    receipt = transport.submit_order(_intent(), armed=True)
    assert receipt.accepted is False
    assert REASON_DUPLICATE_MISMATCH in (receipt.message or "")
    assert session.open_calls == []


# =========================================================================
# 9. auth probe failure -> blocked(not authenticated), GET only
# =========================================================================


def test_09_auth_probe_failure_blocks(tmp_path: Path) -> None:
    service, _p = _audited_proposal_service(tmp_path)
    unauthenticated = _FakeResponse(
        status=200,
        url="https://www.rakuten-sec.co.jp/Auth/Login?b=1",
        body="",
    )
    session = FakeBrokerWebSession(probe_response=unauthenticated)
    transport = _transport(service, session, submissions_enabled=True)
    receipt = transport.submit_order(_intent(), armed=True)
    assert receipt.accepted is False
    assert REASON_NOT_AUTHENTICATED in (receipt.message or "")
    assert session.request_calls == [("GET", RAKUTEN_WEB_ORDER_FORM["auth_probe_path"])]
    assert all(method == "GET" for method, _path in session.request_calls)
    assert session.open_calls == []  # no DOM before auth
    assert STAGE_SUBMIT not in _request_stages(service)


def test_09b_auth_probe_text_marker_blocks(tmp_path: Path) -> None:
    service, _p = _audited_proposal_service(tmp_path)
    login_page = _FakeResponse(
        status=200, url="https://www.rakuten-sec.co.jp/accept", body="ログイン"
    )
    session = FakeBrokerWebSession(probe_response=login_page)
    transport = _transport(service, session, submissions_enabled=True)
    receipt = transport.submit_order(_intent(), armed=True)
    assert receipt.accepted is False
    assert REASON_NOT_AUTHENTICATED in (receipt.message or "")


# =========================================================================
# 10. full happy path -> request/response/state audited, accepted
# =========================================================================


def test_10_happy_path_full_audit_chain_and_acceptance(tmp_path: Path) -> None:
    service, _p = _audited_proposal_service(tmp_path)
    session = FakeBrokerWebSession(order_id_on_confirm="90123456")
    transport = _transport(service, session, submissions_enabled=True)
    receipt = transport.submit_order(_intent(), armed=True)
    assert receipt.accepted is True
    assert receipt.broker_order_id == "90123456"
    assert receipt.status is BrokerOrderStatus.ACCEPTED
    request_entries = [entry for entry in service.audit_entries() if entry.kind == "request"]
    assert len(request_entries) == 1
    payload = request_entries[0].payload
    assert payload["stage"] == STAGE_SUBMIT
    assert payload["armed"] is True
    assert payload["transport"] == "authenticated-web-session"
    assert payload["proposal_hash"] == _proposal().proposal_hash()
    assert payload["order_form"]["symbol"] == "7203"
    assert payload["order_form"]["quantity"] == 100
    response_entries = [entry for entry in service.audit_entries() if entry.kind == "response"]
    assert len(response_entries) == 1
    assert response_entries[0].payload["accepted"] is True
    assert response_entries[0].payload["broker_order_id"] == "90123456"
    assert "submit-replay" not in str(_state_stages(service))
    # Form fill happened exactly as catalogued, submit clicked once.
    assert session.click_calls == [RAKUTEN_WEB_ORDER_FORM["selectors"]["submit"]]
    assert (RAKUTEN_WEB_ORDER_FORM["selectors"]["symbol"], "7203") in session.fill_calls


# =========================================================================
# 11. confirmation parse failure -> UNKNOWN + inquiry guidance
# =========================================================================


def test_11_confirmation_parse_failure_unknown(tmp_path: Path) -> None:
    service, _p = _audited_proposal_service(tmp_path)
    session = FakeBrokerWebSession(order_id_on_confirm=None)
    transport = _transport(service, session, submissions_enabled=True)
    receipt = transport.submit_order(_intent(), armed=True)
    assert receipt.accepted is False
    assert receipt.status is BrokerOrderStatus.UNKNOWN
    assert receipt.broker_order_id is None
    assert "注文照会" in (receipt.message or "")
    response_entries = [entry for entry in service.audit_entries() if entry.kind == "response"]
    assert response_entries[0].payload["accepted"] is False
    assert response_entries[0].payload["broker_order_id"] is None


# =========================================================================
# 12. DOM exception -> state(submit-failed) + UNKNOWN receipt, no leak
# =========================================================================


def test_12_dom_exception_audited_as_submit_failed(tmp_path: Path) -> None:
    service, _p = _audited_proposal_service(tmp_path)
    session = FakeBrokerWebSession(dom_error=RuntimeError("selector not found: #input_symbol"))
    transport = _transport(service, session, submissions_enabled=True)
    receipt = transport.submit_order(_intent(), armed=True)
    assert receipt.accepted is False
    assert receipt.status is BrokerOrderStatus.UNKNOWN
    assert "RuntimeError" in (receipt.message or "")
    assert "再送信前" in (receipt.message or "")
    assert STAGE_SUBMIT_FAILED in _state_stages(service)
    failed_entry = next(
        entry
        for entry in service.audit_entries()
        if entry.kind == "state" and entry.payload.get("stage") == STAGE_SUBMIT_FAILED
    )
    assert "selector not found" in failed_entry.payload["error"]
    # The request was audited (submit instant passed) but no response entry.
    assert STAGE_SUBMIT in _request_stages(service)
    assert [entry.kind for entry in service.audit_entries()].count("response") == 0


# =========================================================================
# 13. no blocked path ever records request audit
# =========================================================================


def test_13_blocked_paths_never_write_request_audit(tmp_path: Path) -> None:
    # 2. missing proposal
    service_a = _service(tmp_path / "a")
    transport_a = _transport(service_a, FakeBrokerWebSession(), submissions_enabled=True)
    transport_a.submit_order(_intent(client_order_id="ghost"), armed=True)
    # 3. mismatch (registry hash differs from live proposal hash)
    service_b, _p = _audited_proposal_service(tmp_path / "b")
    different_b = _proposal(quantity=42)
    service_b._proposals["co-1"] = type(service_b._proposals["co-1"])(
        proposal_hash=different_b.proposal_hash(), proposal_id="x"
    )
    transport_b = _transport(service_b, FakeBrokerWebSession(), submissions_enabled=True)
    transport_b.submit_order(_intent(), armed=True)
    # 4. not armed
    service_c, _p2 = _audited_proposal_service(tmp_path / "c")
    transport_c = _transport(service_c, FakeBrokerWebSession(), submissions_enabled=True)
    transport_c.submit_order(_intent(), armed=False)
    # 5. settings gate
    plain = BrokerExecutionDomainService(
        settings=_settings(), audit_dir=tmp_path / "d", clock=lambda: _FIXED_NOW
    )
    plain.propose_model(_proposal())
    transport_d = _transport(
        plain, FakeBrokerWebSession(), submissions_enabled=True, settings=_settings()
    )
    transport_d.submit_order(_intent(), armed=True)
    # 9. unauthenticated
    service_e, _p3 = _audited_proposal_service(tmp_path / "e")
    session_e = FakeBrokerWebSession(
        probe_response=_FakeResponse(status=200, url="https://x.example/Auth/Login", body="")
    )
    transport_e = _transport(service_e, session_e, submissions_enabled=True)
    transport_e.submit_order(_intent(), armed=True)
    for service in (service_a, service_b, service_c, plain, service_e):
        assert _request_stages(service) == [], (
            f"blocked path wrote a request audit entry: {_request_stages(service)}"
        )


# =========================================================================
# 14. after successful flow, verify_audit() == []
# =========================================================================


def test_14_happy_path_audit_chain_verifies_clean(tmp_path: Path) -> None:
    service, _p = _audited_proposal_service(tmp_path)
    session = FakeBrokerWebSession(order_id_on_confirm="55500011")
    transport = _transport(service, session, submissions_enabled=True)
    receipt = transport.submit_order(_intent(), armed=True)
    assert receipt.accepted is True
    assert service.verify_audit() == []


# =========================================================================
# 15. audit payload carries no secret-like keys
# =========================================================================


def test_15_audit_payloads_contain_no_secret_like_keys(tmp_path: Path) -> None:
    service, _p = _audited_proposal_service(tmp_path)
    session = FakeBrokerWebSession(order_id_on_confirm="60000012")
    transport = _transport(service, session, submissions_enabled=True)
    receipt = transport.submit_order(_intent(), armed=True)
    assert receipt.accepted is True
    forbidden = ("password", "passwd", "cookie", "token", "secret", "credential", "authorization")
    for entry in service.audit_entries():
        blob = json.dumps(entry.payload, ensure_ascii=False).lower()
        for word in forbidden:
            assert word not in blob, f"secret-like key {word!r} leaked into audit payload"
        assert "profile" not in blob


# =========================================================================
# 16. capabilities reflect the gate truthfully
# =========================================================================


def test_16_capabilities_reflect_submission_gate(tmp_path: Path) -> None:
    service = _service(tmp_path)
    frozen = _transport(service, submissions_enabled=False)
    assert frozen.capabilities.order_submission is False
    assert frozen.capabilities.scraping is True
    assert frozen.capabilities.broker == "rakuten-securities"
    assert frozen.capabilities.transport.value == "authenticated_web_session"
    enabled = _transport(service, submissions_enabled=True)
    assert enabled.capabilities.order_submission is True


# =========================================================================
# 17. preview_order purity
# =========================================================================


def test_17_preview_order_is_pure_and_never_touches_session(tmp_path: Path) -> None:
    service = _service(tmp_path)
    session = FakeBrokerWebSession()
    transport = _transport(service, session, submissions_enabled=True)
    preview = transport.preview_order(_intent())
    assert preview.estimated_notional == Decimal("300000")
    assert preview.currency == "JPY"
    assert preview.warnings == []
    assert preview.intent.client_order_id == "co-1"
    market_preview = transport.preview_order(
        _intent(order_type="market", limit_price=None, reference_price=None)
    )
    assert market_preview.estimated_notional is None
    assert "notional cannot be estimated" in market_preview.warnings
    assert session.open_calls == [] and session.request_calls == []


# =========================================================================
# 18. structural compatibility with the BrokerConnector protocol
# =========================================================================


def test_18_transport_satisfies_broker_connector_protocol(tmp_path: Path) -> None:
    from yowayowa.broker_models import BrokerCapabilities, BrokerConnector

    service = _service(tmp_path)
    transport: BrokerConnector = _transport(service, submissions_enabled=False)
    capabilities: BrokerCapabilities = transport.capabilities
    assert capabilities.broker == "rakuten-securities"
    receipt_preview = transport.preview_order(_intent())
    assert receipt_preview.broker == "rakuten-securities"
    # submit_order's extra armed keyword keeps structural compatibility.
    result = transport.submit_order(_intent(), armed=True)
    assert result.accepted is False


# =========================================================================
# 19. fail-closed protocol methods raise BrokerConnectorFeatureError
# =========================================================================


def test_19_feature_methods_raise_fail_closed(tmp_path: Path) -> None:
    service = _service(tmp_path)
    transport = _transport(service, submissions_enabled=True)
    for call in (
        transport.list_orders,
        transport.list_positions,
        transport.account_snapshot,
        lambda: transport.quote("7203"),
        lambda: transport.cancel_order("b-1", client_order_id="co-1"),
    ):
        try:
            call()
        except BrokerConnectorFeatureError as exc:
            assert "broker-read" in str(exc) or "out of scope" in str(exc)
        else:
            raise AssertionError(f"{call} must raise BrokerConnectorFeatureError")


# =========================================================================
# 20. CLI submit: default invocation is frozen before any DOM
# =========================================================================


def test_20_cli_submit_default_is_frozen_without_browser(tmp_path: Path) -> None:
    audit_dir = tmp_path / "audit"
    service = BrokerExecutionDomainService(
        settings=_armed_settings(), audit_dir=audit_dir, clock=lambda: _FIXED_NOW
    )
    proposal = service.propose_model(_proposal(client_order_id="cli-co-1"))
    _ = proposal
    result = runner.invoke(
        cli_app,
        ["submit", "cli-co-1", "--audit-dir", str(audit_dir), "--json"],
    )
    assert result.exit_code == 0, result.output
    body = json.loads(result.output)
    assert body["accepted"] is False
    assert body["status"] == "rejected"
    assert "submissions_enabled=False" in body["message"]
    entries = [json.loads(line) for line in (audit_dir / "audit.jsonl").read_text().splitlines()]
    stages = [entry["payload"].get("stage") for entry in entries if entry["kind"] == "state"]
    assert stages == [STAGE_SUBMIT_FROZEN]
    assert not any(
        entry["kind"] == "request" and entry["payload"].get("stage") == STAGE_SUBMIT
        for entry in entries
    )


def test_20b_cli_submit_gate_open_not_armed_blocks_before_dom(
    monkeypatch: Any, tmp_path: Path
) -> None:
    """--submissions-enabled without --armed reaches the interlock layer and
    is blocked there (NOT armed). The real browser session is replaced by the
    scriptable fake, so the test proves the CLI path never needs the DOM."""

    audit_dir = tmp_path / "audit"
    service = BrokerExecutionDomainService(
        settings=_armed_settings(), audit_dir=audit_dir, clock=lambda: _FIXED_NOW
    )
    service.propose_model(_proposal(client_order_id="cli-co-2"))

    fake_session = FakeBrokerWebSession()

    def _fake_builder(
        audit_dir_override: str | None, enabled: bool
    ) -> tuple[BrokerExecutionDomainService, RakutenWebSubmissionTransport, Any]:
        _ = audit_dir_override
        transport = RakutenWebSubmissionTransport(
            session=fake_session,
            service=service,
            settings=_armed_settings(),
            clock=lambda: _FIXED_NOW,
            submissions_enabled=enabled,
        )
        return service, transport, fake_session

    import yowayowa.broker_execution_cli as cli_module

    monkeypatch.setattr(cli_module, "_submission_transport", _fake_builder)
    result = runner.invoke(
        cli_app,
        [
            "submit",
            "cli-co-2",
            "--submissions-enabled",
            "--audit-dir",
            str(audit_dir),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    body = json.loads(result.output)
    assert body["accepted"] is False
    assert "not armed" in body["message"]
    entries = [json.loads(line) for line in (audit_dir / "audit.jsonl").read_text().splitlines()]
    assert not any(
        entry["kind"] == "request" and entry["payload"].get("stage") == STAGE_SUBMIT
        for entry in entries
    )
    stages = [entry["payload"].get("stage") for entry in entries if entry["kind"] == "state"]
    assert "evaluate" in stages and STAGE_SUBMIT_BLOCKED in stages
    # Blocked before any session traffic: no open, no probe.
    assert fake_session.open_calls == []
    assert fake_session.request_calls == []


def test_20c_cli_submit_unknown_id_fails_with_bad_parameter(tmp_path: Path) -> None:
    result = runner.invoke(
        cli_app,
        ["submit", "no-such-id", "--audit-dir", str(tmp_path / "audit")],
    )
    assert result.exit_code != 0
    assert "unknown client_order_id" in result.output


def test_20d_cli_help_still_lists_existing_commands(tmp_path: Path) -> None:
    result = runner.invoke(cli_app, ["--help"])
    assert result.exit_code == 0
    for command in ("audit", "audit-verify", "proposals-create", "proposals-evaluate", "submit"):
        assert command in result.output


# =========================================================================
# 21. form catalog provenance stays explicitly unverified
# =========================================================================


def test_21_order_form_catalog_marked_unverified() -> None:
    assert RAKUTEN_WEB_ORDER_FORM["verified"] is False
    selectors = RAKUTEN_WEB_ORDER_FORM["selectors"]
    for key in ("symbol", "quantity", "limit_price", "submit", "confirmation_order_id"):
        assert key in selectors


# =========================================================================
# 22. frozen receipt never claims a broker order id
# =========================================================================


def test_22_frozen_receipt_carries_no_broker_order_id(tmp_path: Path) -> None:
    service, _p = _audited_proposal_service(tmp_path)
    transport = _transport(service, FakeBrokerWebSession(), submissions_enabled=False)
    receipt = transport.submit_order(_intent(), armed=True)
    assert receipt.broker_order_id is None
    assert receipt.transport_order_id is None
    assert receipt.accepted is False


# =========================================================================
# 23. P2C: auth-probe failure fires the expiry notifier exactly once
# =========================================================================


class _RecordingExpiryNotifier:
    """Test double recording notify_session_expired/notify_authenticated."""

    def __init__(self) -> None:
        self.expiry_calls: list[str] = []
        self.authenticated_calls: list[str] = []

    def notify_session_expired(
        self,
        *,
        source: str,
        connector_id: str = "rakuten-web",
        detail: str = "",
    ) -> bool:
        self.expiry_calls.append(source)
        return True

    def notify_authenticated(self, connector_id: str = "rakuten-web") -> None:
        self.authenticated_calls.append(connector_id)


def test_23_auth_probe_failure_fires_expiry_notifier_broker_exec(tmp_path: Path) -> None:
    service, _p = _audited_proposal_service(tmp_path)
    unauthenticated = _FakeResponse(
        status=200,
        url="https://www.rakuten-sec.co.jp/ITS/V_ACT_Login.html",
        body="",
    )
    session = FakeBrokerWebSession(probe_response=unauthenticated)
    notifier = _RecordingExpiryNotifier()
    transport = RakutenWebSubmissionTransport(
        session=session,
        service=service,
        settings=_armed_settings(),
        clock=lambda: _FIXED_NOW,
        submissions_enabled=True,
        expiry_notifier=notifier,
    )
    receipt = transport.submit_order(_intent(), armed=True)
    assert receipt.accepted is False
    assert receipt.status is BrokerOrderStatus.REJECTED
    assert REASON_NOT_AUTHENTICATED in (receipt.message or "")
    # Notifier fired exactly once with the broker-exec source.
    assert notifier.expiry_calls == ["broker-exec"]
    assert notifier.authenticated_calls == []
    # Blocked audit shape is unchanged: stage=submit-blocked, no stage=submit.
    assert STAGE_SUBMIT_BLOCKED in _state_stages(service)
    assert STAGE_SUBMIT not in _request_stages(service)
    assert session.open_calls == []  # no DOM before auth


def test_23b_frozen_gate_never_fires_expiry_notifier(tmp_path: Path) -> None:
    """Step-order regression: the frozen gate precedes the probe, so a frozen
    submission must not notify (probe is never reached)."""

    service, _p = _audited_proposal_service(tmp_path)
    session = FakeBrokerWebSession()
    notifier = _RecordingExpiryNotifier()
    transport = RakutenWebSubmissionTransport(
        session=session,
        service=service,
        settings=_armed_settings(),
        clock=lambda: _FIXED_NOW,
        submissions_enabled=False,
        expiry_notifier=notifier,
    )
    receipt = transport.submit_order(_intent(), armed=True)
    assert receipt.accepted is False
    assert "frozen by COO ruling" in (receipt.message or "")
    assert notifier.expiry_calls == [] and notifier.authenticated_calls == []
    assert _state_stages(service) == [STAGE_SUBMIT_FROZEN]
    assert session.open_calls == [] and session.request_calls == []


def test_23c_auth_probe_uses_pinned_login_path(tmp_path: Path) -> None:
    """The probe GET must target the pinned real login page (P2C), not the
    former unverified accept/orders assumption."""

    from yowayowa.operator_bridge.rakuten_web import RAKUTEN_WEB_LOGIN_PATH

    assert RAKUTEN_WEB_LOGIN_PATH == "ITS/V_ACT_Login.html"
    service, _p = _audited_proposal_service(tmp_path)
    session = FakeBrokerWebSession()
    transport = _transport(service, session, submissions_enabled=True)
    transport.submit_order(_intent(), armed=True)
    assert ("GET", "ITS/V_ACT_Login.html") in session.request_calls
    assert all(method == "GET" for method, _path in session.request_calls)
