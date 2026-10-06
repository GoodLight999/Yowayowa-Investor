"""Execution admission regressions: local SQLite and synthetic transport only."""

import multiprocessing
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from yowayowa.operator_bridge import state as state_module
from yowayowa.operator_bridge.state import BrokerDispatchBlocked, SQLiteOperatorState
from yowayowa.services.broker_execution import BrokerExecutionBlocked


def _clock(monkeypatch: pytest.MonkeyPatch) -> list[datetime]:
    current = [datetime(2026, 10, 2, 23, 59, 59, tzinfo=UTC)]

    class Clock(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return current[0].astimezone(tz) if tz else current[0].replace(tzinfo=None)

    monkeypatch.setattr(state_module, "datetime", Clock)
    return current


@pytest.mark.parametrize("limit", [None, 1])
@pytest.mark.parametrize("status", ["ABSENT", "REJECTED", "UNKNOWN", "COMPLETED"])
def test_attempt_always_requires_owned_submitting_slot(
    tmp_path: Path, limit: int | None, status: str
) -> None:
    state = SQLiteOperatorState(tmp_path / "state.db", max_orders_per_day=limit)
    if status != "ABSENT":
        assert state.reserve_order_submission("a", "hash", limit)[0] == "RESERVED"
        if status == "COMPLETED":
            state.record_submission_success("a", {"synthetic": True})
        else:
            state.record_submission_failure("a", status)
    with pytest.raises(BrokerExecutionBlocked):
        state.append_audit("order_submit_attempt", client_order_id="a")
    assert state.audit_events() == []


def test_missing_id_is_blocked_even_without_limit(tmp_path: Path) -> None:
    state = SQLiteOperatorState(tmp_path / "state.db")
    with pytest.raises(BrokerExecutionBlocked):
        state.append_audit("order_submit_attempt", client_order_id=None)


def test_explicit_legacy_import_is_unknown_and_cannot_dispatch(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    state = SQLiteOperatorState(path)
    state.import_legacy_submit_attempt(created_at=datetime.now(UTC), client_order_id="legacy")
    reopened = SQLiteOperatorState(path)
    assert reopened.count_submission_attempts_today() == 1
    assert reopened.reserve_order_submission("legacy", "LEGACY_UNKNOWN_HASH")[0] == (
        "UNCERTAIN_OR_IN_FLIGHT"
    )
    with pytest.raises(BrokerDispatchBlocked):
        reopened.dispatch_submission("legacy", lambda: pytest.fail("must not dispatch"))
    with pytest.raises(BrokerExecutionBlocked):
        reopened.import_legacy_submit_attempt(
            created_at=datetime.now(UTC), client_order_id="legacy"
        )
    with pytest.raises(ValueError):
        reopened.import_legacy_submit_attempt(
            created_at=datetime(2026, 10, 2), client_order_id="naive"
        )


def test_attempt_time_is_fresh_after_real_sqlite_writer_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    current = _clock(monkeypatch)
    state = SQLiteOperatorState(tmp_path / "state.db", max_orders_per_day=1)
    assert state.reserve_order_submission("a", "hash", 1)[0] == "RESERVED"
    waiting = threading.Event()
    original_connect = state._connect

    def connect() -> sqlite3.Connection:
        connection = original_connect()
        connection.set_trace_callback(
            lambda sql: waiting.set() if sql == "BEGIN IMMEDIATE" else None
        )
        return connection

    monkeypatch.setattr(state, "_connect", connect)
    with original_connect() as holder, ThreadPoolExecutor(max_workers=1) as pool:
        holder.execute("BEGIN IMMEDIATE")
        future = pool.submit(state.append_audit, "order_submit_attempt", client_order_id="a")
        try:
            assert waiting.wait(5)
            current[0] += timedelta(seconds=2)
        finally:
            holder.commit()
        future.result(5)
    assert state.audit_events()[0]["created_at"][:10] == current[0].date().isoformat()
    assert state.count_submission_attempts_today() == 1


def test_post_commit_rollover_blocks_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    current = _clock(monkeypatch)
    state = SQLiteOperatorState(tmp_path / "state.db", max_orders_per_day=1)
    assert state.reserve_order_submission("a", "hash", 1)[0] == "RESERVED"
    state.append_audit("order_submit_attempt", client_order_id="a")
    original_day = SQLiteOperatorState.current_day.fget
    assert original_day is not None
    reads = 0

    def day(self: SQLiteOperatorState) -> str:
        nonlocal reads
        reads += 1
        if reads == 3:  # fresh after BEGIN; pre-commit; post-commit handoff
            current[0] += timedelta(seconds=2)
        return original_day(self)

    monkeypatch.setattr(SQLiteOperatorState, "current_day", property(day))
    with pytest.raises(BrokerDispatchBlocked):
        state.dispatch_submission("a", lambda: pytest.fail("must not dispatch"))
    assert state.audit_events()[0]["created_at"][:10] == "2026-10-02"
    assert state.count_submission_attempts_today() == 0


def _claim_dispatch(path: str, start: Any, results: Any) -> None:
    state = SQLiteOperatorState(path, max_orders_per_day=1)
    assert start.wait(8)
    try:
        state.dispatch_submission("a", lambda: "synthetic-handoff")
        results.put("sent")
    except BrokerDispatchBlocked:
        results.put("blocked")


def test_dispatch_single_use_claim_across_real_processes(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    state = SQLiteOperatorState(path, max_orders_per_day=1)
    assert state.reserve_order_submission("a", "hash", 1)[0] == "RESERVED"
    state.append_audit("order_submit_attempt", client_order_id="a")
    ctx = multiprocessing.get_context("spawn")
    start, results = ctx.Event(), ctx.Queue()
    processes = [
        ctx.Process(target=_claim_dispatch, args=(str(path), start, results)) for _ in range(2)
    ]
    try:
        for process in processes:
            process.start()
        start.set()
        assert sorted(results.get(timeout=10) for _ in processes) == ["blocked", "sent"]
        for process in processes:
            process.join(10)
            assert process.exitcode == 0
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(5)
    assert SQLiteOperatorState(path).count_submission_attempts_today() == 1
    with pytest.raises(BrokerDispatchBlocked):
        state.dispatch_submission("a", lambda: pytest.fail("restart must not dispatch"))


def test_transport_failure_preserves_attempt_and_durable_claim(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    state = SQLiteOperatorState(path, max_orders_per_day=1)
    assert state.reserve_order_submission("a", "hash", 1)[0] == "RESERVED"
    state.append_audit("order_submit_attempt", client_order_id="a")

    def unknown() -> object:
        raise TimeoutError("synthetic unknown")

    with pytest.raises(TimeoutError):
        state.dispatch_submission("a", unknown)
    reopened = SQLiteOperatorState(path)
    assert reopened.count_submission_attempts_today() == 1
    with pytest.raises(BrokerDispatchBlocked):
        reopened.dispatch_submission("a", lambda: pytest.fail("must not retry"))
    assert reopened.reserve_order_submission("a", "hash", 1)[0] == "UNCERTAIN_OR_IN_FLIGHT"


def test_duplicate_attempt_cannot_exceed_daily_limit(tmp_path: Path) -> None:
    state = SQLiteOperatorState(tmp_path / "state.db", max_orders_per_day=1)
    assert state.reserve_order_submission("a", "hash", 1)[0] == "RESERVED"
    state.append_audit("order_submit_attempt", client_order_id="a")
    with pytest.raises(BrokerExecutionBlocked):
        state.append_audit("order_submit_attempt", client_order_id="a")
    assert state.count_submission_attempts_today() == 1


@pytest.mark.parametrize("independent", [False, True])
@pytest.mark.parametrize("unknown", [False, True])
def test_api_rollover_during_rss_id_preparation_is_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, independent: bool, unknown: bool
) -> None:
    from test_operator_bridge_app import _app, _intent

    from yowayowa.config import Settings

    current = _clock(monkeypatch)
    entered, release = threading.Event(), threading.Event()
    original_allocate = SQLiteOperatorState.allocate_rss_order_id

    def allocate(self: SQLiteOperatorState, cid: str) -> int:
        if cid == "old":
            entered.set()
            assert release.wait(8)
        return original_allocate(self, cid)

    monkeypatch.setattr(SQLiteOperatorState, "allocate_rss_order_id", allocate)
    cfg = Settings(
        _env_file=None,
        mode="personal",
        broker_live_orders_enabled=True,
        broker_max_single_order_notional=1_000_000,
        broker_max_orders_per_day=1,
    )
    client, runner, state = _app(tmp_path, cfg)
    competitor, runner_b, _ = _app(tmp_path, cfg) if independent else (client, runner, state)
    physical: list[str] = []
    fail = [False]

    def macro(name: str, args: Any) -> object:
        physical.append(current[0].date().isoformat())
        if fail[0]:
            raise TimeoutError("synthetic unknown")
        return "発注済み"

    monkeypatch.setattr(runner, "run_macro", macro)
    monkeypatch.setattr(runner_b, "run_macro", macro)
    headers = {"Authorization": "Bearer bridge-secret"}
    url = "/v1/brokers/rakuten/orders"
    old_intent = dict(_intent(), client_order_id="old")
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(client.post, url, headers=headers, json=old_intent)
        try:
            assert entered.wait(8)
            current[0] += timedelta(seconds=2)
            winner = competitor.post(
                url, headers=headers, json=dict(_intent(), client_order_id="winner")
            )
            assert winner.status_code == 200
            fail[0] = unknown
        finally:
            release.set()
        response = future.result(8)
    assert response.status_code == 409
    assert physical == [current[0].date().isoformat()]
    assert state.count_submission_attempts_today() == 1
    restarted, fresh_runner, reopened = _app(tmp_path, cfg)
    assert restarted.post(url, headers=headers, json=old_intent).status_code == 409
    assert fresh_runner.calls == []
    assert reopened.count_submission_attempts_today() == 1
