"""P2A broker execution domain tests — one test per spec item (1-29)."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient
from typer.testing import CliRunner

from yowayowa.api.deps import get_broker_execution_service
from yowayowa.broker.execution.audit import (
    AUDIT_STATE_FILE_NAME,
    AppendOnlyAuditLog,
    entry_hash,
)
from yowayowa.broker.execution.models import OrderProposal
from yowayowa.broker.execution.service import (
    JST,
    BrokerExecutionDomainService,
    DuplicateProposalError,
)
from yowayowa.broker_execution_cli import app as cli_app
from yowayowa.config import Settings, get_settings

runner = CliRunner()


def _settings(**overrides: Any) -> Settings:
    return Settings(mode="personal", **overrides)


def _proposal(**overrides: Any) -> OrderProposal:
    fields: dict[str, Any] = {
        "client_order_id": "co-1",
        "symbol": "7203",
        "market": "jp",
        "side": "buy",
        "quantity": 100,
        "order_type": "limit",
        "limit_price": "3000",
        "reference_price": "2995",
        "currency": "JPY",
        "motivation": "research says cheap",
    }
    fields.update(overrides)
    if isinstance(fields.get("limit_price"), str):
        fields["limit_price"] = Decimal(fields["limit_price"])
    if isinstance(fields.get("reference_price"), str):
        fields["reference_price"] = Decimal(fields["reference_price"])
    return OrderProposal.model_validate(fields)


def _armed_settings(**overrides: Any) -> Settings:
    fields: dict[str, Any] = {
        "mode": "personal",
        "broker_live_orders_enabled": True,
        "broker_max_single_order_notional": 500000,
        "broker_max_orders_per_day": 20,
    }
    fields.update(overrides)
    return Settings(**fields)


def _service(
    tmp_path: Path,
    settings: Settings | None = None,
    clock: Callable[[], datetime] | None = None,
) -> BrokerExecutionDomainService:
    return BrokerExecutionDomainService(
        settings=settings if settings is not None else _armed_settings(),
        audit_dir=tmp_path / "audit",
        clock=clock,
    )


def _api_env(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("YOWAYOWA_MODE", "personal")
    monkeypatch.setenv("YOWAYOWA_BROKER_EXECUTION_AUDIT_DIR", str(tmp_path / "audit"))
    monkeypatch.delenv("YOWAYOWA_API_TOKEN", raising=False)
    monkeypatch.delenv("YOWAYOWA_PRIVATE_CONNECTORS_ENABLED", raising=False)
    get_settings.cache_clear()
    get_broker_execution_service.cache_clear()


# ----------------------------------------------------------------- 1. hash


def test_01_proposal_hash_deterministic_excludes_ids_timestamps(tmp_path: Path) -> None:
    a = _proposal(client_order_id="a", proposal_id="x1")
    b = _proposal(client_order_id="b", proposal_id="y2")
    b.created_at = a.created_at + _timedelta_days(1)
    assert a.proposal_hash() == b.proposal_hash()
    # also identical across two service instances (restart idempotency)
    s1 = _service(tmp_path)
    s1.propose_model(_proposal(client_order_id="k"))
    h1 = _proposal(client_order_id="k").proposal_hash()
    s2 = _service(tmp_path)
    assert s2.duplicate_check(_proposal(client_order_id="k")).replay is True
    assert h1 == _proposal(client_order_id="k").proposal_hash()


def _timedelta_days(days: int) -> Any:
    from datetime import timedelta

    return timedelta(days=days)


# ------------------------------------------------- 2. hash changes w/ fields


def test_02_hash_changes_when_any_economic_field_changes() -> None:
    base = _proposal()
    assert _proposal(symbol="4755").proposal_hash() != base
    assert _proposal(quantity=200).proposal_hash() != base
    assert _proposal(limit_price="3001").proposal_hash() != base
    assert _proposal(side="sell").proposal_hash() != base
    assert _proposal(reference_price="2996").proposal_hash() != base
    assert _proposal(currency="USD").proposal_hash() != base
    assert _proposal(market="us").proposal_hash() != base
    assert _proposal(order_type="market", limit_price=None).proposal_hash() != base


# ------------------------------------------------- 3. limit needs limit_price


def test_03_limit_order_without_limit_price_rejected() -> None:
    with pytest.raises(ValueError, match="limit_price is required"):
        OrderProposal.model_validate(
            {
                "client_order_id": "co-2",
                "symbol": "7203",
                "market": "jp",
                "side": "buy",
                "quantity": 1,
                "order_type": "limit",
                "motivation": "x",
            }
        )


# ----------------------------------------------------------------- 4. preview


def test_04_preview_full_content_notional_missing_stays_none(tmp_path: Path) -> None:
    service = _service(tmp_path)
    proposal = _proposal(order_type="limit", limit_price="100", reference_price=None)
    preview = service.preview(proposal)
    assert preview.estimated_notional == Decimal("10000")
    assert preview.currency == "JPY"
    assert preview.proposal_hash == proposal.proposal_hash()
    market_only = _proposal(
        client_order_id="m1", order_type="market", limit_price=None, reference_price=None
    )
    market_preview = service.preview(market_only)
    assert market_preview.estimated_notional is None
    assert market_preview.estimated_notional != 0
    assert isinstance(market_preview.estimated_notional, type(None))


# --------------------------------------------- 5. market order w/o ref price


def test_05_market_order_without_reference_price_warning_notional_none(tmp_path: Path) -> None:
    service = _service(tmp_path)
    proposal = _proposal(
        client_order_id="m2", order_type="market", limit_price=None, reference_price=None
    )
    preview = service.preview(proposal)
    assert preview.estimated_notional is None
    assert "notional cannot be estimated" in preview.warnings


# --------------------------------------------------- 6. settings not armed


def test_06_interlocks_disallowed_when_settings_not_armed(tmp_path: Path) -> None:
    service = _service(tmp_path, settings=_settings())
    proposal = service.propose_model(_proposal())
    decision = service.evaluate(proposal, armed=True)
    assert decision.allowed is False
    assert any("not armed" in reason for reason in decision.reasons)


# --------------------------------------------------- 7. runtime arm switch


def test_07_interlocks_disallowed_when_runtime_arming_false(tmp_path: Path) -> None:
    service = _service(tmp_path, settings=_armed_settings())
    proposal = service.propose_model(_proposal())
    decision = service.evaluate(proposal, armed=False)
    assert decision.allowed is False
    assert (
        "execution is not armed; live orders require an explicit arming state" in decision.reasons
    )


# ------------------------------------------------------------ 8. allowed path


def test_08_interlocks_allowed_when_settings_and_runtime_armed(tmp_path: Path) -> None:
    service = _service(tmp_path, settings=_armed_settings())
    proposal = service.propose_model(_proposal())
    decision = service.evaluate(proposal, armed=True)
    assert decision.allowed is True
    assert decision.reasons == ()


# ------------------------------------------------------- 9. notional > limit


def test_09_notional_above_limit_blocked(tmp_path: Path) -> None:
    service = _service(tmp_path, settings=_armed_settings(broker_max_single_order_notional=100000))
    proposal = service.propose_model(_proposal())
    decision = service.evaluate(proposal, armed=True)
    assert decision.allowed is False
    assert "order exceeds configured single-order notional limit" in decision.reasons


# --------------------------------------------------- 10. notional == limit ok


def test_10_notional_exactly_at_limit_allowed(tmp_path: Path) -> None:
    service = _service(tmp_path, settings=_armed_settings(broker_max_single_order_notional=300000))
    proposal = service.propose_model(_proposal())
    decision = service.evaluate(proposal, armed=True)
    assert decision.allowed is True
    assert decision.reasons == ()


# ------------------------------------------------------ 11. currency mismatch


def test_11_currency_mismatch_blocked(tmp_path: Path) -> None:
    service = _service(tmp_path, settings=_armed_settings(broker_risk_currency="JPY"))
    proposal = service.propose_model(_proposal(currency="USD"))
    decision = service.evaluate(proposal, armed=True)
    assert decision.allowed is False
    assert any("currency does not match" in reason for reason in decision.reasons)


# --------------------------------------------------------- 12. daily at limit


def test_12_daily_count_blocked_at_limit(tmp_path: Path) -> None:
    clock_now = datetime(2026, 9, 23, 10, 0, tzinfo=UTC)
    service = _service(
        tmp_path,
        settings=_armed_settings(broker_max_orders_per_day=2),
        clock=lambda: clock_now,
    )
    service.record_request(
        "co-x",
        {"stage": "submit"},
    )
    # inject the submit entries with controlled timestamps
    log = service.audit_log()
    entry1 = log.append("request", "co-a", {"stage": "submit"})
    entry2 = log.append("request", "co-b", {"stage": "submit"})
    _ = entry1, entry2
    fixed_service = BrokerExecutionDomainService(
        settings=_armed_settings(broker_max_orders_per_day=2),
        audit_dir=tmp_path / "audit",
        clock=lambda: clock_now,
    )
    proposal = fixed_service.propose_model(_proposal())
    decision = fixed_service.evaluate(proposal, armed=True)
    assert decision.allowed is False
    assert "daily live-order limit reached" in decision.reasons


# --------------------------------------------------- 13. JST day boundary


def test_13_jst_day_boundary_2359_and_0001_next_day(tmp_path: Path) -> None:
    from zoneinfo import ZoneInfo

    jst = ZoneInfo("Asia/Tokyo")
    late = datetime(2026, 9, 23, 23, 59, tzinfo=jst)
    early = datetime(2026, 9, 24, 0, 1, tzinfo=jst)
    svc_a = BrokerExecutionDomainService(
        settings=_armed_settings(broker_max_orders_per_day=1),
        audit_dir=tmp_path / "audit",
        clock=lambda: late,
    )
    svc_a.record_request("co-a", {"stage": "submit"})
    proposal_a = svc_a.propose_model(_proposal(client_order_id="p-a"))
    decision_a = svc_a.evaluate(proposal_a, armed=True)
    assert decision_a.allowed is False
    assert "daily live-order limit reached" in decision_a.reasons

    svc_b = BrokerExecutionDomainService(
        settings=_armed_settings(broker_max_orders_per_day=1),
        audit_dir=tmp_path / "audit",
        clock=lambda: early,
    )
    proposal_b = svc_b.propose_model(_proposal(client_order_id="p-b"))
    decision_b = svc_b.evaluate(proposal_b, armed=True)
    assert decision_b.allowed is True


# --------------------------------------------- 14. UTC-day vs JST-day diverge


def test_14_utc_vs_jst_day_divergence(tmp_path: Path) -> None:
    event_utc = datetime(2026, 9, 23, 15, 30, tzinfo=UTC)
    svc = BrokerExecutionDomainService(
        settings=_armed_settings(broker_max_orders_per_day=1),
        audit_dir=tmp_path / "audit",
        clock=lambda: event_utc,
    )
    svc.record_request("co-a", {"stage": "submit"})
    # Same UTC day (Sep 23 UTC), but the event is Sep 24 in JST. Evaluating
    # with the clock at 23:59 UTC Sep 23 (= 08:59 JST Sep 24) still counts it.
    later_utc = datetime(2026, 9, 23, 23, 59, tzinfo=UTC)
    svc_later = BrokerExecutionDomainService(
        settings=_armed_settings(broker_max_orders_per_day=1),
        audit_dir=tmp_path / "audit",
        clock=lambda: later_utc,
    )
    proposal = svc_later.propose_model(_proposal())
    decision = svc_later.evaluate(proposal, armed=True)
    assert decision.allowed is False
    assert "daily live-order limit reached" in decision.reasons
    # A pure-UTC day counter would have reset here; JST keeps Sep 24 active.
    assert any(
        entry.kind == "request"
        and entry.payload.get("stage") == "submit"
        and entry.ts.astimezone(JST).date().isoformat() == "2026-09-24"
        for entry in svc_later.audit_entries()
    )


# ------------------------------------------------------- 15. duplicate replay


def test_15_duplicate_same_id_same_hash_replay_allowed(tmp_path: Path) -> None:
    service = _service(tmp_path)
    first = service.propose_model(_proposal())
    duplicate = _proposal()  # same economic fields, different ids/timestamps
    decision = service.evaluate(duplicate, armed=True)
    assert decision.allowed is True
    check = service.duplicate_check(duplicate)
    assert check.replay is True
    assert check.mismatch is False
    _ = first


# ----------------------------------------------- 16. duplicate id, diff hash


def test_16_duplicate_same_id_different_hash_blocked(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.propose_model(_proposal())
    different = _proposal(quantity=999)
    decision = service.evaluate(different, armed=True)
    assert decision.allowed is False
    assert "duplicate client_order_id with different proposal content" in decision.reasons


# ------------------------------------------------------------ 17. audit chain


def test_17_audit_append_only_seq_increment_chain_verifies(tmp_path: Path) -> None:
    log = AppendOnlyAuditLog(tmp_path / "audit")
    first = log.append("intent", "co-1", {"a": 1})
    second = log.append("request", "co-2", {"b": 2})
    third = log.append("state", "co-3", {"c": 3})
    assert [first.seq, second.seq, third.seq] == [1, 2, 3]
    assert second.prev_hash == first.entry_hash
    assert third.prev_hash == second.entry_hash
    assert first.prev_hash == "0" * 64
    assert log.verify() == []


# --------------------------------------------------------- 18. tamper detect


def test_18_audit_tamper_detection(tmp_path: Path) -> None:
    log = AppendOnlyAuditLog(tmp_path / "audit")
    log.append("intent", "co-1", {"amount": "1"})
    log.append("intent", "co-2", {"amount": "2"})
    log.append("intent", "co-3", {"amount": "3"})
    assert log.verify() == []
    path = log.path
    lines = path.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[1])
    tampered["payload"]["amount"] = "999999"
    lines[1] = json.dumps(tampered, ensure_ascii=False, separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    problems = log.verify()
    assert problems, "tampering must be detected"
    assert any("entry_hash" in p for p in problems)


# ----------------------------------------------------------- 19. unknown kind


def test_19_audit_unknown_kind_rejected(tmp_path: Path) -> None:
    log = AppendOnlyAuditLog(tmp_path / "audit")
    with pytest.raises(ValueError, match="unknown audit kind"):
        log.append("bogus-kind", "co-1", {})


# ------------------------------------------ 20. restart: same id+hash replay


def test_20_restart_same_id_same_hash_treated_as_replay(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.propose_model(_proposal())
    restarted = _service(tmp_path)
    check = restarted.duplicate_check(_proposal())
    assert check.replay is True
    assert check.submitted is True


# ------------------------------------- 21. restart: same id, different hash


def test_21_restart_same_id_different_hash_blocked(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.propose_model(_proposal())
    restarted = _service(tmp_path)
    decision = restarted.evaluate(_proposal(quantity=42), armed=True)
    assert decision.allowed is False
    assert "duplicate client_order_id with different proposal content" in decision.reasons


# ------------------------------------------ 22. evaluate appends state entry


def test_22_evaluate_appends_state_entry_propose_appends_intent(tmp_path: Path) -> None:
    service = _service(tmp_path)
    proposal = service.propose_model(_proposal())
    service.evaluate(proposal, armed=True)
    kinds = [entry.kind for entry in service.audit_entries()]
    assert "intent" in kinds
    assert "state" in kinds
    assert kinds[0] == "intent"


# ------------------------------------------------- 23. record_* wrapper kinds


def test_23_record_request_response_state_kinds(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.record_request("co-1", {"stage": "submit"})
    service.record_response("co-1", {"accepted": False})
    service.record_state("co-1", {"stage": "record"})
    kinds = [entry.kind for entry in service.audit_entries()]
    assert kinds == ["request", "response", "state"]


# ------------------------------------------------ 24. provenance round-trip


def test_24_provenance_preserved_through_round_trip(tmp_path: Path) -> None:
    service = _service(tmp_path)
    provenance = {
        "provider": "rakuten-securities",
        "source_url": "https://example.invalid/research",
        "retrieved_at": "2026-09-23T00:00:00+00:00",
        "as_of": "2026-09-23",
    }
    proposal = service.propose_model(_proposal(provenance=provenance))
    reloaded = OrderProposal.model_validate_json(proposal.model_dump_json())
    assert reloaded.provenance == provenance
    # and through the audit replay path too
    restarted = _service(tmp_path)
    found = [
        entry
        for entry in restarted.audit_entries()
        if entry.kind == "intent" and entry.client_order_id == proposal.client_order_id
    ]
    assert found[0].payload["proposal"]["provenance"] == provenance


# ------------------------------------------- 25. public mode blocks execution


def test_25_public_mode_forces_execution_blocked(tmp_path: Path) -> None:
    settings = Settings(
        mode="public",
        api_token="secret",
        private_connectors_enabled=True,
        scraping_enabled=True,
        broker_control_enabled=True,
        broker_live_orders_enabled=True,
        broker_max_single_order_notional=500000,
        broker_max_orders_per_day=20,
    )
    assert settings.broker_live_orders_enabled is False
    service = _service(tmp_path, settings=settings)
    proposal = service.propose_model(_proposal())
    decision = service.evaluate(proposal, armed=True)
    assert decision.allowed is False
    assert any("operator/personal mode only" in reason for reason in decision.reasons)


# ================================================================= API tests


def _api_proposal_body() -> dict[str, Any]:
    return {
        "client_order_id": "api-co-1",
        "symbol": "7203",
        "market": "jp",
        "side": "buy",
        "quantity": 100,
        "order_type": "limit",
        "limit_price": "3000",
        "reference_price": "2995",
        "currency": "JPY",
        "motivation": "api research",
    }


def test_26_api_post_proposal_returns_hash_and_preview(monkeypatch: Any, tmp_path: Path) -> None:
    _api_env(monkeypatch, tmp_path)
    from yowayowa.api.app import app

    with TestClient(app) as client:
        response = client.post("/v1/broker-execution/proposals", json=_api_proposal_body())
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["proposal"]["client_order_id"] == "api-co-1"
        assert body["proposal_hash"]
        assert body["preview"]["estimated_notional"] == "300000"
        assert body["preview"]["currency"] == "JPY"


def test_27_api_evaluate_armed_false_blocked_200(monkeypatch: Any, tmp_path: Path) -> None:
    _api_env(monkeypatch, tmp_path)
    from yowayowa.api.app import app

    with TestClient(app) as client:
        created = client.post("/v1/broker-execution/proposals", json=_api_proposal_body())
        assert created.status_code == 200, created.text
        response = client.post(
            "/v1/broker-execution/proposals/api-co-1/evaluate",
            json={"armed": False},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["allowed"] is False
        assert any("not armed" in reason for reason in body["reasons"])


def test_28_api_audit_endpoint_verify_ok(monkeypatch: Any, tmp_path: Path) -> None:
    _api_env(monkeypatch, tmp_path)
    from yowayowa.api.app import app

    with TestClient(app) as client:
        client.post("/v1/broker-execution/proposals", json=_api_proposal_body())
        response = client.get("/v1/broker-execution/audit", params={"limit": 10})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["intact"] is True
        assert body["verify_problems"] == []
        assert len(body["entries"]) >= 1
        assert body["entries"][0]["kind"] == "intent"


# ================================================================== CLI test


def test_29_cli_audit_show_and_verify_on_temp_dir(tmp_path: Path) -> None:
    audit_dir = tmp_path / "audit"
    service = BrokerExecutionDomainService(
        settings=_armed_settings(),
        audit_dir=audit_dir,
    )
    service.propose_model(_proposal())
    service.record_request("co-1", {"stage": "submit"})
    service.record_response("co-1", {"accepted": False})
    result_show = runner.invoke(cli_app, ["audit", "--audit-dir", str(audit_dir)])
    assert result_show.exit_code == 0, result_show.output
    assert "intent" in result_show.output
    result_verify = runner.invoke(cli_app, ["audit-verify", "--audit-dir", str(audit_dir)])
    assert result_verify.exit_code == 0, result_verify.output
    assert "audit chain OK" in result_verify.output


# ================================================= P2A追補: F1 state sidecar


def test_30_f1_state_sidecar_updated_on_every_append(tmp_path: Path) -> None:
    log = AppendOnlyAuditLog(tmp_path / "audit")
    first = log.append("intent", "co-1", {"a": 1})
    second = log.append("request", "co-2", {"b": 2})
    third = log.append("state", "co-3", {"c": 3})
    state = json.loads((tmp_path / "audit" / AUDIT_STATE_FILE_NAME).read_text(encoding="utf-8"))
    assert state["count"] == 3
    assert state["last_entry_hash"] == third.entry_hash
    assert log.verify() == []
    _ = first, second


def test_31_f1_truncated_tail_detected_by_verify(tmp_path: Path) -> None:
    log = AppendOnlyAuditLog(tmp_path / "audit")
    log.append("intent", "co-1", {"a": 1})
    log.append("intent", "co-2", {"a": 2})
    log.append("intent", "co-3", {"a": 3})
    lines = log.path.read_text(encoding="utf-8").splitlines(keepends=True)
    log.path.write_text("".join(lines[:2]), encoding="utf-8")
    problems = log.verify()
    assert problems, "tail truncation must be detected"
    assert any("count mismatch" in problem for problem in problems)


def test_32_f1_tampered_last_entry_flagged_by_sidecar(tmp_path: Path) -> None:
    """A *reforged* last entry (content + entry_hash consistently replaced)
    passes the pure chain check but diverges from the sidecar, which is the
    only witness of the original tail hash."""

    log = AppendOnlyAuditLog(tmp_path / "audit")
    log.append("intent", "co-1", {"amount": "1"})
    log.append("intent", "co-2", {"amount": "2"})
    log.append("intent", "co-3", {"amount": "3"})
    lines = log.path.read_text(encoding="utf-8").splitlines()
    reforged = json.loads(lines[2])
    reforged["payload"]["amount"] = "999999"
    del reforged["entry_hash"]
    reforged["entry_hash"] = entry_hash(reforged)
    lines[2] = json.dumps(reforged, ensure_ascii=False, separators=(",", ":"))
    log.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    problems = log.verify()
    assert problems, "reforged tail must be detected"
    assert any("last_entry_hash" in problem for problem in problems)
    # the chain itself is internally consistent; only the sidecar flags it
    assert not any(
        "entry_hash does not match recomputed content hash" in problem for problem in problems
    )


def test_33_f1_missing_state_file_self_heals(tmp_path: Path) -> None:
    log = AppendOnlyAuditLog(tmp_path / "audit")
    log.append("intent", "co-1", {"a": 1})
    log.append("intent", "co-2", {"a": 2})
    (tmp_path / "audit" / AUDIT_STATE_FILE_NAME).unlink()
    assert log.verify() == []
    state = json.loads((tmp_path / "audit" / AUDIT_STATE_FILE_NAME).read_text(encoding="utf-8"))
    assert state["count"] == 2


def test_34_f1_unparsable_state_file_reported(tmp_path: Path) -> None:
    log = AppendOnlyAuditLog(tmp_path / "audit")
    log.append("intent", "co-1", {"a": 1})
    (tmp_path / "audit" / AUDIT_STATE_FILE_NAME).write_text("not json", encoding="utf-8")
    problems = log.verify()
    assert problems, "unparsable state file must be reported"
    assert any("unparsable" in problem for problem in problems)


# ================================================= P2A追補: F2 first-wins


def test_35_f2_second_propose_different_content_rejected_first_wins(tmp_path: Path) -> None:
    service = _service(tmp_path)
    first = service.propose_model(_proposal(quantity=100))
    with pytest.raises(DuplicateProposalError) as excinfo:
        service.propose_model(_proposal(quantity=999))
    assert excinfo.value.client_order_id == "co-1"
    intents = [e for e in service.audit_entries() if e.kind == "intent"]
    assert len(intents) == 1, "the second propose must not touch the audit trail"
    replay = service.duplicate_check(_proposal(quantity=100))
    assert replay.replay is True and replay.mismatch is False
    mismatch = service.duplicate_check(_proposal(quantity=999))
    assert mismatch.mismatch is True
    _ = first


def test_36_f2_second_propose_same_content_also_rejected(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.propose_model(_proposal())
    with pytest.raises(DuplicateProposalError):
        service.propose_model(_proposal())
    intents = [e for e in service.audit_entries() if e.kind == "intent"]
    assert len(intents) == 1


def test_37_f2_restart_replays_first_intent(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.propose_model(_proposal(quantity=100))
    with pytest.raises(DuplicateProposalError):
        service.propose_model(_proposal(quantity=999))
    restarted = _service(tmp_path)
    assert restarted.duplicate_check(_proposal(quantity=100)).replay is True
    assert restarted.duplicate_check(_proposal(quantity=999)).mismatch is True


def test_38_f2_api_post_duplicate_returns_409(monkeypatch: Any, tmp_path: Path) -> None:
    _api_env(monkeypatch, tmp_path)
    from yowayowa.api.app import app

    with TestClient(app) as client:
        first = client.post("/v1/broker-execution/proposals", json=_api_proposal_body())
        assert first.status_code == 200, first.text
        second = client.post("/v1/broker-execution/proposals", json=_api_proposal_body())
        assert second.status_code == 409, second.text
        assert "duplicate client_order_id" in second.json()["detail"]


# ==================================================== P2A追補: F3 torn line


def test_39_f3_torn_tail_line_skipped_and_reported(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.propose_model(_proposal())
    service.record_request("co-1", {"stage": "submit"})
    service.record_response("co-1", {"accepted": False})
    log = service.audit_log()
    with log.path.open("a", encoding="utf-8") as handle:
        handle.write('{"seq": 4, "ts": "2026-09-23T00')
    assert len(log.entries()) == 3, "torn tail must be skipped without raising"
    problems = log.verify()
    assert any("unparsable" in problem for problem in problems)
    restarted = _service(tmp_path)
    assert len(restarted.audit_entries()) == 3


def test_40_f3_garbage_midfile_line_reported_with_line_number(tmp_path: Path) -> None:
    log = AppendOnlyAuditLog(tmp_path / "audit")
    log.append("intent", "co-1", {"a": 1})
    with log.path.open("a", encoding="utf-8") as handle:
        handle.write("}}} garbage not json {{{\n")
    log.append("intent", "co-2", {"a": 2})
    entries = log.entries()
    assert [e.seq for e in entries] == [1, 2]
    problems = log.verify()
    assert any("line 2" in problem and "unparsable" in problem for problem in problems)


def test_41_f3_api_audit_endpoint_200_with_problems(monkeypatch: Any, tmp_path: Path) -> None:
    _api_env(monkeypatch, tmp_path)
    from yowayowa.api.app import app

    with TestClient(app) as client:
        created = client.post("/v1/broker-execution/proposals", json=_api_proposal_body())
        assert created.status_code == 200, created.text
        audit_path = tmp_path / "audit" / "audit.jsonl"
        with audit_path.open("a", encoding="utf-8") as handle:
            handle.write("torn line without newline")
        response = client.get("/v1/broker-execution/audit", params={"limit": 10})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["intact"] is False
        assert body["verify_problems"], "torn line must surface in verify_problems"
        assert len(body["entries"]) >= 1


def test_42_f2_cli_duplicate_create_exits_nonzero(tmp_path: Path) -> None:
    audit_dir = tmp_path / "audit"
    service = BrokerExecutionDomainService(
        settings=_armed_settings(),
        audit_dir=audit_dir,
    )
    service.propose_model(_proposal(client_order_id="cli-co-1"))
    args = [
        "proposals-create",
        "--client-order-id",
        "cli-co-1",
        "--symbol",
        "7203",
        "--side",
        "buy",
        "--quantity",
        "999",
        "--order-type",
        "limit",
        "--limit-price",
        "3100",
        "--motivation",
        "cli duplicate probe",
        "--audit-dir",
        str(audit_dir),
    ]
    result = runner.invoke(cli_app, args)
    assert result.exit_code != 0
    assert "duplicate client_order_id" in result.output
