"""Verification tests for F4 remediation:
1. Thread-safe order proposal serialization (first-wins, second rejected with 409).
2. AppendOnlyAuditLog concurrent append serialization (flock + thread lock, strict seq & chain).
3. Audit log integrity compromise fail-closed (503 / AuditIntegrityCompromisedError).
"""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from yowayowa.api.broker_execution_routes import router
from yowayowa.api.deps import get_broker_execution_service, get_settings
from yowayowa.broker.execution.audit import AppendOnlyAuditLog
from yowayowa.broker.execution.service import (
    AuditIntegrityCompromisedError,
    BrokerExecutionDomainService,
)
from yowayowa.config import Settings

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
HEADERS = {"Authorization": "Bearer test-token"}


def _settings(**kw) -> Settings:
    return Settings(
        _env_file=None,
        mode="personal",
        api_token="test-token",
        private_connectors_enabled=True,
        broker_control_enabled=True,
        **kw,
    )


def _proposal_payload(cid: str, quantity: int = 100) -> dict:
    return {
        "client_order_id": cid,
        "symbol": "7203",
        "market": "jp",
        "side": "buy",
        "quantity": quantity,
        "order_type": "limit",
        "limit_price": "3000",
        "currency": "JPY",
        "motivation": "synthetic-test",
    }


def test_concurrent_proposals_first_wins(tmp_path: Path) -> None:
    """Concurrent proposals with the same client_order_id must result in
    exactly one 200 and one 409, with only one intent recorded."""

    service = BrokerExecutionDomainService(
        settings=_settings(),
        audit_dir=tmp_path / "domain",
        clock=lambda: NOW,
    )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_settings] = lambda: _settings()
    app.dependency_overrides[get_broker_execution_service] = lambda: service
    client = TestClient(app)

    barrier = threading.Barrier(2)

    def request(qty: int):
        barrier.wait(timeout=5)
        return client.post(
            "/v1/broker-execution/proposals",
            headers=HEADERS,
            json=_proposal_payload("concurrent-order", qty),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(request, [100, 200]))

    statuses = sorted(r.status_code for r in responses)
    assert statuses == [200, 409], f"Expected [200, 409], got {statuses}"

    entries = service.audit_entries()
    assert len(entries) == 1
    assert entries[0].kind == "intent"
    assert entries[0].client_order_id == "concurrent-order"
    assert service.verify_audit() == []


def test_concurrent_audit_log_appends_preserve_chain(tmp_path: Path) -> None:
    """Concurrent appends to AppendOnlyAuditLog must maintain strictly sequential seq
    and unbroken prev_hash chain."""

    audit_log = AppendOnlyAuditLog(tmp_path / "chain", clock=lambda: NOW)
    n_threads = 8
    n_per_thread = 10
    total = n_threads * n_per_thread

    barrier = threading.Barrier(n_threads)

    def worker(worker_id: int):
        barrier.wait(timeout=5)
        for i in range(n_per_thread):
            audit_log.append("state", f"w{worker_id}-{i}", {"val": i})

    with ThreadPoolExecutor(max_workers=n_threads) as pool:
        list(pool.map(worker, range(n_threads)))

    entries = audit_log.entries()
    assert len(entries) == total
    seqs = [e.seq for e in entries]
    assert seqs == list(range(1, total + 1)), f"Sequences not sequential: {seqs[:20]}"

    problems = audit_log.verify()
    assert problems == [], f"Audit verification failed: {problems}"


def test_audit_integrity_compromised_fails_closed(tmp_path: Path) -> None:
    """When the audit trail is corrupted, proposing a new order fails closed."""

    service = BrokerExecutionDomainService(
        settings=_settings(),
        audit_dir=tmp_path / "domain",
        clock=lambda: NOW,
    )
    # Propose first order
    p1 = service.propose(**_proposal_payload("order-1"))
    assert p1.client_order_id == "order-1"

    # Corrupt the audit trail by tampering with the file
    audit_file = tmp_path / "domain" / "audit.jsonl"
    content = audit_file.read_text(encoding="utf-8")
    # Invalidate hash
    lines = content.strip().split("\n")
    data = json.loads(lines[0])
    data["entry_hash"] = "deadbeef" * 8
    audit_file.write_text(json.dumps(data) + "\n", encoding="utf-8")

    # Now attempt to propose another order: must fail closed!
    with pytest.raises(AuditIntegrityCompromisedError):
        service.propose(**_proposal_payload("order-2"))

    # Also via API endpoint: should return 503
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_settings] = lambda: _settings()
    app.dependency_overrides[get_broker_execution_service] = lambda: service
    client = TestClient(app)

    resp = client.post(
        "/v1/broker-execution/proposals",
        headers=HEADERS,
        json=_proposal_payload("order-3"),
    )
    assert resp.status_code == 503
    assert "integrity compromised" in resp.json()["detail"].lower()


@pytest.mark.parametrize(
    "operation", ["evaluate", "record_request", "record_response", "record_state", "direct_append"]
)
def test_audit_corruption_blocks_evaluate_record_and_append(tmp_path: Path, operation: str) -> None:
    service = BrokerExecutionDomainService(
        settings=_settings(),
        audit_dir=tmp_path / "domain",
        clock=lambda: NOW,
    )
    p = service.propose(**_proposal_payload("order-1"))
    service.record_state(p.client_order_id, {"step": "init"})

    log = service.audit_log()
    original_lines = log.path.read_text(encoding="utf-8").splitlines()
    # Truncate second entry
    log.path.write_text(original_lines[0] + "\n", encoding="utf-8")
    before_problems = service.verify_audit()
    assert len(before_problems) > 0, "Truncation must be detected"

    with pytest.raises(AuditIntegrityCompromisedError):
        if operation == "evaluate":
            service.evaluate(p, armed=True)
        elif operation == "direct_append":
            log.append("state", p.client_order_id, {"replacement": True})
        else:
            getattr(service, operation)(p.client_order_id, {"replacement": True})

    # Verification problems must persist and not be laundered
    after_problems = service.verify_audit()
    assert len(after_problems) > 0
    with pytest.raises(AuditIntegrityCompromisedError):
        service.propose(**_proposal_payload("order-blocked"))
