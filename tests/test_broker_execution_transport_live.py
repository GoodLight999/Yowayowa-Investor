"""P2B live-probe tests: REAL browser, run only on explicit opt-in.

These tests start a real (headful, under Xvfb where needed) Chromium via
PersistentBrokerWebSession and exercise the transport against it. They are
skipped unless YOWAYOWA_P2B_LIVE_PROBE=1 so ordinary `make verify` and CI
never touch a real browser session through this path.
"""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path

import pytest

from yowayowa.broker.execution.models import OrderProposal
from yowayowa.broker.execution.service import BrokerExecutionDomainService
from yowayowa.broker.execution.transport import (
    STAGE_SUBMIT_FROZEN,
    RakutenWebSubmissionTransport,
)
from yowayowa.broker_models import BrokerOrderIntent, BrokerOrderStatus
from yowayowa.config import Settings

pytestmark = pytest.mark.skipif(
    os.getenv("YOWAYOWA_P2B_LIVE_PROBE") != "1",
    reason="live browser probe runs only with YOWAYOWA_P2B_LIVE_PROBE=1",
)


def _proposal(client_order_id: str) -> OrderProposal:
    return OrderProposal.model_validate(
        {
            "client_order_id": client_order_id,
            "symbol": "7203",
            "market": "jp",
            "side": "buy",
            "quantity": 100,
            "order_type": "limit",
            "limit_price": Decimal("3000"),
            "reference_price": Decimal("2990"),
            "currency": "JPY",
            "motivation": "P2B live probe (no real submission while gate frozen)",
        }
    )


def _intent(client_order_id: str) -> BrokerOrderIntent:
    return BrokerOrderIntent.model_validate(
        {
            "client_order_id": client_order_id,
            "symbol": "7203",
            "side": "buy",
            "quantity": 100,
            "order_type": "limit",
            "limit_price": Decimal("3000"),
            "reference_price": Decimal("2990"),
            "currency": "JPY",
        }
    )


def test_live_frozen_gate_never_opens_real_browser(tmp_path: Path) -> None:
    """Even with a REAL started session object present, the frozen gate
    rejects before any session/DNS/DOM access and audits submit-frozen."""

    from yowayowa.operator_bridge.rakuten_web import RAKUTEN_WEB_BASE_URL
    from yowayowa.operator_bridge.web_session import PersistentBrokerWebSession

    settings = Settings(
        mode="personal",
        broker_live_orders_enabled=True,
        broker_max_single_order_notional=500000,
        broker_max_orders_per_day=20,
        broker_rakuten_web_profile_dir=str(tmp_path / "profile"),
    )
    service = BrokerExecutionDomainService(settings=settings, audit_dir=tmp_path / "audit")
    proposal = _proposal("live-frozen-1")
    service.propose_model(proposal)
    session = PersistentBrokerWebSession(
        base_url=RAKUTEN_WEB_BASE_URL,
        profile_dir=tmp_path / "profile",
        headless=False,
    )
    transport = RakutenWebSubmissionTransport(
        session=session,
        service=service,
        settings=settings,
        submissions_enabled=False,
    )
    try:
        session.start()
        receipt = transport.submit_order(_intent("live-frozen-1"), armed=True)
    finally:
        session.close()
    assert receipt.accepted is False
    assert receipt.status is BrokerOrderStatus.REJECTED
    stages = [
        entry.payload.get("stage") for entry in service.audit_entries() if entry.kind == "state"
    ]
    assert stages == [STAGE_SUBMIT_FROZEN]


def test_live_unauthenticated_probe_blocked(tmp_path: Path) -> None:
    """Gate open + real session + fresh (never-logged-in) profile: the GET
    auth probe must observe an unauthenticated session and block BEFORE any
    DOM access or request audit."""

    from yowayowa.operator_bridge.rakuten_web import RAKUTEN_WEB_BASE_URL
    from yowayowa.operator_bridge.web_session import PersistentBrokerWebSession

    settings = Settings(
        mode="personal",
        broker_live_orders_enabled=True,
        broker_max_single_order_notional=500000,
        broker_max_orders_per_day=20,
        broker_rakuten_web_profile_dir=str(tmp_path / "profile"),
    )
    service = BrokerExecutionDomainService(settings=settings, audit_dir=tmp_path / "audit")
    proposal = _proposal("live-unauth-1")
    service.propose_model(proposal)
    session = PersistentBrokerWebSession(
        base_url=RAKUTEN_WEB_BASE_URL,
        profile_dir=tmp_path / "profile",
        headless=False,
    )
    transport = RakutenWebSubmissionTransport(
        session=session,
        service=service,
        settings=settings,
        submissions_enabled=True,
    )
    try:
        session.start()
        receipt = transport.submit_order(_intent("live-unauth-1"), armed=True)
    finally:
        session.close()
    assert receipt.accepted is False
    assert receipt.status is BrokerOrderStatus.REJECTED
    assert receipt.broker_order_id is None
    stages = [
        entry.payload.get("stage") for entry in service.audit_entries() if entry.kind == "state"
    ]
    assert "submit-blocked" in stages
    assert not any(
        entry.kind == "request" and entry.payload.get("stage") == "submit"
        for entry in service.audit_entries()
    )
