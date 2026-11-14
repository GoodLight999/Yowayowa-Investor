"""P2C1/P2C2 order-inquiry tests (design memo section 3, items 1-10).

Every test uses a fake BrokerReadService (fixed AcquisitionOutcome) plus
a real BrokerExecutionDomainService on a tmp_path audit dir; the network
is never touched and the audit trail is never written by the inquiry.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from starlette.testclient import TestClient
from typer.testing import CliRunner

from yowayowa.acquisition.models import AcquisitionFetchState, AuthState
from yowayowa.broker.execution.models import OrderProposal
from yowayowa.broker.execution.service import BrokerExecutionDomainService
from yowayowa.broker.execution.transport import (
    BrokerConnectorFeatureError,
    RakutenWebSubmissionTransport,
)
from yowayowa.broker_execution_cli import app as cli_app
from yowayowa.broker_models import BrokerOrder, BrokerOrderSide, BrokerOrderStatus
from yowayowa.config import Settings, get_settings
from yowayowa.services.broker_read_service import BrokerReadOutcome, BrokerReadService

runner = CliRunner()

_FIXED_NOW = datetime(2026, 9, 24, 1, 0, tzinfo=UTC)


# --------------------------------------------------------------------- fakes


class _FakeBrokerReadService(BrokerReadService):
    """BrokerReadService substitute returning canned outcomes per resource."""

    def __init__(self, outcomes: dict[tuple[str, str], BrokerReadOutcome]) -> None:
        self._outcomes = outcomes
        self.fetch_calls: list[tuple[str, str, bool]] = []
        # Real __init__ is skipped: it would register connectors and build
        # an acquisition service; this fake only serves fetch().

    def fetch(
        self,
        resource: str,
        market: str,
        *,
        force_refresh: bool = False,
    ) -> BrokerReadOutcome:
        self.fetch_calls.append((resource, market, force_refresh))
        return self._outcomes[(resource, market)]


def _web_order(
    broker_order_id: str,
    *,
    symbol: str = "7203",
    side: BrokerOrderSide = BrokerOrderSide.BUY,
    quantity: int = 100,
    filled_quantity: int = 0,
    status: BrokerOrderStatus = BrokerOrderStatus.PENDING,
) -> BrokerOrder:
    return BrokerOrder(
        broker="rakuten-securities",
        broker_order_id=broker_order_id,
        symbol=symbol,
        side=side,
        quantity=quantity,
        filled_quantity=filled_quantity,
        average_fill_price=None,
        status=status,
    )


def _outcome(
    resource: str,
    orders: list[BrokerOrder],
    *,
    fetch_state: AcquisitionFetchState = AcquisitionFetchState.OK,
    auth_state: AuthState = AuthState.AUTHENTICATED,
    source_url: str | None = "https://www.rakuten-sec.co.jp/web/orders?tm=123",
    notes: list[str] | None = None,
) -> BrokerReadOutcome:
    return BrokerReadOutcome(
        connector_id="rakuten-web",
        resource=resource,
        market="jp",
        fetch_state=fetch_state,
        auth_state=auth_state,
        orders=orders,
        source_url=source_url,
        notes=notes or [],
    )


# ------------------------------------------------------------------ helpers


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


def _execution(
    tmp_path: Path,
    clock: Callable[[], datetime] | None = None,
) -> BrokerExecutionDomainService:
    return BrokerExecutionDomainService(
        settings=_armed_settings(),
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


def _audited_submit(
    service: BrokerExecutionDomainService,
    client_order_id: str,
    broker_order_id: str,
    *,
    accepted: bool = True,
    status: str = "accepted",
) -> OrderProposal:
    """Record the exact evidence the matching rule keys on."""
    proposal = service.propose_model(_proposal(client_order_id=client_order_id))
    service.record_request(
        client_order_id,
        {
            "stage": "submit",
            "proposal_hash": proposal.proposal_hash(),
            "armed": True,
            "transport": "authenticated-web-session",
        },
    )
    service.record_response(
        client_order_id,
        {
            "stage": "submit",
            "accepted": accepted,
            "broker_order_id": broker_order_id,
            "status": status,
            "message": "order accepted by broker web form",
            "confirmation_url": "https://www.rakuten-sec.co.jp/accept/orders",
            "proposal_hash": proposal.proposal_hash(),
        },
    )
    return proposal


def _service(
    fake_read: _FakeBrokerReadService,
    execution: BrokerExecutionDomainService,
):
    from yowayowa.services.order_inquiry_service import OrderInquiryService

    return OrderInquiryService(broker_read=fake_read, execution=execution)


def _read_service(
    open_orders: list[BrokerOrder],
    history_orders: list[BrokerOrder],
    **outcome_kwargs: Any,
) -> _FakeBrokerReadService:
    return _FakeBrokerReadService(
        {
            ("open_orders", "jp"): _outcome("open_orders", open_orders, **outcome_kwargs),
            ("order_history", "jp"): _outcome("order_history", history_orders, **outcome_kwargs),
        }
    )


# =========================================================================
# 1. merge: dedupe by broker_order_id (history wins), open_orders first
# =========================================================================


def test_01_merge_dedupes_by_broker_order_id_history_wins(tmp_path: Path) -> None:
    execution = _execution(tmp_path)
    open_row_dup = _web_order("9001", status=BrokerOrderStatus.PENDING, quantity=100)
    open_row_unique = _web_order("9002", symbol="8306", status=BrokerOrderStatus.PENDING)
    # Same broker order id in history: the NEWER state (partially filled) wins.
    history_row_newer = _web_order(
        "9001", status=BrokerOrderStatus.PARTIALLY_FILLED, quantity=100, filled_quantity=40
    )
    history_row_extra = _web_order("9003", symbol="6758", status=BrokerOrderStatus.FILLED)
    fake_read = _read_service(
        [open_row_dup, open_row_unique],
        [history_row_newer, history_row_extra],
    )
    report = _service(fake_read, execution).list_orders(market="jp")

    ids = [item.order.broker_order_id for item in report.items]
    assert ids == ["9001", "9002", "9003"], "dedupe keeps first-seen order, open first"
    merged = report.items[0].order
    assert merged.status is BrokerOrderStatus.PARTIALLY_FILLED
    assert merged.filled_quantity == 40, "order_history row (newer state) wins"
    assert report.items[1].order.symbol == "8306"
    assert report.items[2].order.symbol == "6758"
    assert report.fetch_state_open is AcquisitionFetchState.OK
    assert report.fetch_state_history is AcquisitionFetchState.OK
    assert report.intact_audit is True
    assert report.broker == "rakuten-securities"
    # exactly two read fetches: open_orders + order_history
    assert sorted(call[0] for call in fake_read.fetch_calls) == [
        "open_orders",
        "order_history",
    ]


# =========================================================================
# 2. audit_matched: submit response fills client_order_id/proposal_hash
# =========================================================================


def test_02_submit_response_row_is_audit_matched_with_meta(tmp_path: Path) -> None:
    execution = _execution(tmp_path)
    proposal = _audited_submit(execution, "co-1", "9001")
    fake_read = _read_service(
        [_web_order("9001")],
        [],
    )
    report = _service(fake_read, execution).list_orders(market="jp")

    assert len(report.items) == 1
    item = report.items[0]
    assert item.match == "audit_matched"
    assert item.client_order_id == "co-1"
    assert item.proposal_hash == proposal.proposal_hash()
    assert item.order.broker_order_id == "9001"


def test_02b_latest_submit_response_wins_for_same_broker_order_id(tmp_path: Path) -> None:
    execution = _execution(tmp_path)
    _audited_submit(execution, "co-old", "9001", accepted=False, status="unknown")
    proposal_second = _audited_submit(execution, "co-new", "9001")
    fake_read = _read_service([_web_order("9001")], [])
    report = _service(fake_read, execution).list_orders(market="jp")

    assert len(report.items) == 1
    assert report.items[0].client_order_id == "co-new"
    assert report.items[0].proposal_hash == proposal_second.proposal_hash()


# =========================================================================
# 3. unmatched_web: rows absent from the audit keep meta None
# =========================================================================


def test_03_unmatched_web_rows_keep_meta_none(tmp_path: Path) -> None:
    execution = _execution(tmp_path)
    fake_read = _read_service(
        [_web_order("8001", symbol="9984"), _web_order("8002", symbol="6758")],
        [],
    )
    report = _service(fake_read, execution).list_orders(market="jp")

    assert [item.match for item in report.items] == ["unmatched_web", "unmatched_web"]
    assert all(item.client_order_id is None for item in report.items)
    assert all(item.proposal_hash is None for item in report.items)


# =========================================================================
# 4. audit_only: audited-but-invisible order restored from the proposal
# =========================================================================


def test_04_audit_only_order_restored_from_proposal_and_response(tmp_path: Path) -> None:
    execution = _execution(tmp_path)
    proposal = _audited_submit(execution, "co-1", "9001")
    fake_read = _read_service(
        [],
        [],
    )
    report = _service(fake_read, execution).list_orders(market="jp")

    assert len(report.items) == 1
    item = report.items[0]
    assert item.match == "audit_only"
    assert item.client_order_id == "co-1"
    assert item.proposal_hash == proposal.proposal_hash()
    order = item.order
    # symbol/side/quantity restored from the audited proposal
    assert order.symbol == "7203"
    assert order.side is BrokerOrderSide.BUY
    assert order.quantity == 100
    # status restored from the recorded response
    assert order.status is BrokerOrderStatus.ACCEPTED
    assert order.broker_order_id == "9001"
    # the condition is also recorded as a note
    assert any("not visible in the web inquiry" in note for note in report.notes)


def test_04b_audit_only_skipped_when_proposal_payload_missing(tmp_path: Path) -> None:
    """A submit response whose intent entry was never recorded is not
    restorable; the row is skipped rather than invented."""
    execution = _execution(tmp_path)
    proposal = _proposal(client_order_id="co-x")
    execution.record_response(
        proposal.client_order_id,
        {
            "stage": "submit",
            "accepted": True,
            "broker_order_id": "9009",
            "status": "accepted",
        },
    )
    fake_read = _read_service([], [])
    report = _service(fake_read, execution).list_orders(market="jp")
    assert report.items == []


# =========================================================================
# 5. order_status: 1 item for a known id; unknown id flagged (API 404)
# =========================================================================


def test_05_order_status_known_id_returns_one_item(tmp_path: Path) -> None:
    execution = _execution(tmp_path)
    proposal = _audited_submit(execution, "co-1", "9001")
    fake_read = _read_service([_web_order("9001")], [])
    report = _service(fake_read, execution).order_status("co-1", market="jp")

    assert len(report.items) == 1
    item = report.items[0]
    assert item.match == "audit_matched"
    assert item.client_order_id == "co-1"
    assert item.proposal_hash == proposal.proposal_hash()


def test_05b_order_status_unknown_id_is_flagged_for_404(tmp_path: Path) -> None:
    execution = _execution(tmp_path)
    fake_read = _read_service([], [])
    report = _service(fake_read, execution).order_status("never-proposed", market="jp")

    assert report.items == []
    assert any(note.startswith("unknown client_order_id: never-proposed") for note in report.notes)


# =========================================================================
# 6. fail-closed: non-OK fetch state carries no invented orders
# =========================================================================


def test_06_non_ok_fetch_state_reports_reason_without_orders(tmp_path: Path) -> None:
    execution = _execution(tmp_path)
    fake_read = _FakeBrokerReadService(
        {
            ("open_orders", "jp"): _outcome(
                "open_orders",
                [],
                fetch_state=AcquisitionFetchState.AUTH_EXPIRED,
                auth_state=AuthState.UNAUTHENTICED,
                notes=["session expired; login required"],
            ),
            ("order_history", "jp"): _outcome(
                "order_history",
                [],
                fetch_state=AcquisitionFetchState.FAILED,
                auth_state=AuthState.UNKNOWN,
                notes=["browser session error"],
            ),
        }
    )
    report = _service(fake_read, execution).list_orders(market="jp")

    assert report.items == []
    assert report.fetch_state_open is AcquisitionFetchState.AUTH_EXPIRED
    assert report.fetch_state_history is AcquisitionFetchState.FAILED
    assert report.auth_state is AuthState.UNAUTHENTICED
    assert "session expired; login required" in report.notes
    assert "browser session error" in report.notes


def test_06b_expired_session_keeps_audited_order_unmatched_but_visible(tmp_path: Path) -> None:
    """With the reads down and no web row, order_status answers 0 items
    plus an inquiry note (audit_only is list_orders-only per D3); the
    unknown-id marker must NOT appear, so the API layer answers 200."""

    execution = _execution(tmp_path)
    _audited_submit(execution, "co-1", "9001")
    fake_read = _FakeBrokerReadService(
        {
            ("open_orders", "jp"): _outcome(
                "open_orders",
                [],
                fetch_state=AcquisitionFetchState.AUTH_EXPIRED,
                auth_state=AuthState.UNAUTHENTICED,
            ),
            ("order_history", "jp"): _outcome(
                "order_history",
                [],
                fetch_state=AcquisitionFetchState.AUTH_EXPIRED,
                auth_state=AuthState.UNAUTHENTICED,
            ),
        }
    )
    report = _service(fake_read, execution).order_status("co-1", market="jp")
    assert report.items == []
    assert not any(note.startswith("unknown client_order_id") for note in report.notes)
    assert any("verify the order" in note for note in report.notes)


# =========================================================================
# 7. audit tamper detection: intact_audit=False + leading warning note
# =========================================================================


def test_07_tampered_audit_sets_intact_audit_false(tmp_path: Path) -> None:
    execution = _execution(tmp_path)
    proposal = _audited_submit(execution, "co-1", "9001")
    _ = proposal
    log_path = tmp_path / "audit" / "audit.jsonl"
    lines = log_path.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[0])
    tampered["payload"]["proposal"]["quantity"] = 999999
    lines[0] = json.dumps(tampered, ensure_ascii=False, separators=(",", ":"))
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    fake_read = _read_service([_web_order("9001")], [])
    report = _service(fake_read, execution).list_orders(market="jp")

    assert report.intact_audit is False
    assert report.notes[0].startswith("audit chain verification reported problems")
    # reading continues (read-only is safe) but the classification evidence
    # still resolves; the tamper is surfaced, not silenced
    assert report.items


# =========================================================================
# 8. API: GET /orders 200, /orders/{id} 200, unknown id 404, no token 403
# =========================================================================


def _api_app(
    monkeypatch: Any,
    tmp_path: Path,
    fake_read: _FakeBrokerReadService,
    execution: BrokerExecutionDomainService,
) -> FastAPI:
    from yowayowa.api.deps import (
        get_broker_execution_service,
        get_broker_read_service,
        get_order_inquiry_service,
    )

    monkeypatch.setenv("YOWAYOWA_MODE", "personal")
    monkeypatch.setenv("YOWAYOWA_BROKER_EXECUTION_AUDIT_DIR", str(tmp_path / "audit"))
    monkeypatch.delenv("YOWAYOWA_API_TOKEN", raising=False)
    monkeypatch.delenv("YOWAYOWA_PRIVATE_CONNECTORS_ENABLED", raising=False)
    get_settings.cache_clear()
    get_broker_execution_service.cache_clear()
    get_broker_read_service.cache_clear()
    get_order_inquiry_service.cache_clear()

    from yowayowa.api.app import app

    app.dependency_overrides[get_order_inquiry_service] = lambda: _service(fake_read, execution)
    return app


def test_08_api_list_orders_200_and_order_status_200_404(monkeypatch: Any, tmp_path: Path) -> None:
    from yowayowa.api.deps import get_order_inquiry_service

    execution = _execution(tmp_path)
    _audited_submit(execution, "co-1", "9001")
    fake_read = _read_service([_web_order("9001")], [])
    app = _api_app(monkeypatch, tmp_path, fake_read, execution)

    with TestClient(app) as client:
        listed = client.get("/v1/broker-execution/orders", params={"market": "jp"})
        assert listed.status_code == 200, listed.text
        body = listed.json()
        assert body["broker"] == "rakuten-securities"
        assert body["items"][0]["match"] == "audit_matched"
        assert body["items"][0]["client_order_id"] == "co-1"

        status_ok = client.get("/v1/broker-execution/orders/co-1", params={"market": "jp"})
        assert status_ok.status_code == 200, status_ok.text
        assert len(status_ok.json()["items"]) == 1

        missing = client.get("/v1/broker-execution/orders/never-proposed")
        assert missing.status_code == 404, missing.text

    app.dependency_overrides.clear()
    get_order_inquiry_service.cache_clear()


def test_08b_api_public_mode_fails_closed_403(monkeypatch: Any, tmp_path: Path) -> None:
    from yowayowa.api.deps import (
        get_broker_execution_service,
        get_broker_read_service,
        get_order_inquiry_service,
    )

    monkeypatch.setenv("YOWAYOWA_MODE", "public")
    monkeypatch.setenv("YOWAYOWA_API_TOKEN", "token")
    monkeypatch.setenv("YOWAYOWA_BROKER_EXECUTION_AUDIT_DIR", str(tmp_path / "audit"))
    get_settings.cache_clear()
    get_broker_execution_service.cache_clear()
    get_broker_read_service.cache_clear()
    get_order_inquiry_service.cache_clear()

    from yowayowa.api.app import app

    with TestClient(app) as client:
        headers = {"Authorization": "Bearer token"}
        assert client.get("/v1/broker-execution/orders", headers=headers).status_code == 403
        assert client.get("/v1/broker-execution/orders/co-1", headers=headers).status_code == 403
        # and without the token at all: 401 (invalid bearer)
        assert client.get("/v1/broker-execution/orders").status_code == 401


# =========================================================================
# 9. CLI: orders / order-status via the API client (stubbed HTTP)
# =========================================================================


class _StubResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.status_code = 200
        self.text = json.dumps(payload)

    def raise_for_status(self) -> _StubResponse:
        return self

    def json(self) -> dict[str, Any]:
        return self._payload


class _Stub404Error(Exception):
    def __init__(self) -> None:
        super().__init__("Client error '404 Not Found' for url '/v1/broker-execution/orders/x'")


def _cli_report_payload() -> dict[str, Any]:
    return {
        "broker": "rakuten-securities",
        "market": "jp",
        "generated_at": "2026-09-24T01:00:00Z",
        "fetch_state_open": "ok",
        "fetch_state_history": "ok",
        "auth_state": "authenticated",
        "items": [
            {
                "order": {
                    "broker": "rakuten-securities",
                    "broker_order_id": "9001",
                    "symbol": "7203",
                    "side": "buy",
                    "quantity": 100,
                    "filled_quantity": 0,
                    "average_fill_price": None,
                    "status": "accepted",
                },
                "match": "audit_matched",
                "client_order_id": "co-1",
                "proposal_hash": "abc",
            }
        ],
        "notes": [],
        "intact_audit": True,
        "source_urls": [],
    }


def test_09_cli_orders_and_order_status(monkeypatch: Any) -> None:
    calls: list[tuple[str, dict[str, str] | None]] = []

    class _Client:
        def __init__(self, base_url: str, headers: dict[str, str], timeout: int) -> None:
            pass

        def __enter__(self) -> _Client:
            return self

        def __exit__(self, *_: object) -> None:
            return

        def get(self, path: str, params: dict[str, str] | None = None) -> _StubResponse:
            calls.append((path, params))
            if path == "/v1/broker-execution/orders":
                return _StubResponse(_cli_report_payload())
            raise _Stub404Error()

    monkeypatch.setattr("yowayowa.cli._client", lambda base_url, token: _Client(base_url, {}, 30))

    listed = runner.invoke(cli_app, ["orders", "--market", "jp", "--json"])
    assert listed.exit_code == 0, listed.output
    payload = json.loads(listed.output)
    assert payload["items"][0]["match"] == "audit_matched"
    assert calls and calls[0][0] == "/v1/broker-execution/orders"
    assert calls[0][1] == {"market": "jp", "force_refresh": "false"}

    # table mode renders the key columns (rich may truncate wide cells,
    # so assert on values that stay visible: the order id and client id)
    calls.clear()
    table_result = runner.invoke(cli_app, ["orders", "--market", "jp"])
    assert table_result.exit_code == 0, table_result.output
    assert "9001" in table_result.output
    assert "co-1" in table_result.output


def test_09b_cli_order_status_unknown_id_exits_nonzero(monkeypatch: Any) -> None:
    class _Client:
        def __init__(self, base_url: str, headers: dict[str, str], timeout: int) -> None:
            pass

        def __enter__(self) -> _Client:
            return self

        def __exit__(self, *_: object) -> None:
            return

        def get(self, path: str, params: dict[str, str] | None = None) -> _StubResponse:
            raise _Stub404Error()

    monkeypatch.setattr("yowayowa.cli._client", lambda base_url, token: _Client(base_url, {}, 30))

    result = runner.invoke(cli_app, ["order-status", "ghost", "--market", "jp"])
    assert result.exit_code != 0
    assert "unknown client_order_id" in result.output


# =========================================================================
# 10. transport.list_orders fail-closed with the NEW pointer message
# =========================================================================


def test_10_transport_list_orders_raises_with_new_message(tmp_path: Path) -> None:
    execution = _execution(tmp_path)
    transport = RakutenWebSubmissionTransport(
        session=_NullSession(),
        service=execution,
        settings=_armed_settings(),
        clock=lambda: _FIXED_NOW,
        submissions_enabled=False,
    )
    try:
        transport.list_orders()
    except BrokerConnectorFeatureError as exc:
        assert "list_orders is not served by the submission transport" in str(exc)
        assert "use GET /v1/broker-execution/orders" in str(exc)
    else:
        raise AssertionError("list_orders must keep failing closed")


class _NullSession:
    def open(self, path: str = "") -> Any:
        raise AssertionError("no session access in list_orders")

    def request(self, method: str, path: str) -> Any:
        raise AssertionError("no session access in list_orders")

    def page(self) -> Any:
        raise AssertionError("no session access in list_orders")
