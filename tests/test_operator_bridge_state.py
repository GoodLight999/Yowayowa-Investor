from datetime import UTC, datetime
from pathlib import Path

from yowayowa.operator_bridge.state import SQLiteOperatorState


def test_operator_state_reuses_rss_order_id_across_instances(tmp_path: Path) -> None:
    path = tmp_path / "operator.db"
    first = SQLiteOperatorState(path)
    first_id = first.allocate_rss_order_id("client-1")
    second_id = first.allocate_rss_order_id("client-2")

    reopened = SQLiteOperatorState(path)

    assert first_id == 1
    assert second_id == 2
    assert reopened.allocate_rss_order_id("client-1") == 1
    assert reopened.allocate_rss_order_id("client-3") == 3


def test_operator_state_audit_counts_submit_attempts(tmp_path: Path) -> None:
    state = SQLiteOperatorState(tmp_path / "operator.db")
    state.import_legacy_submit_attempt(
        created_at=datetime.now(UTC),
        client_order_id="client-1",
        payload={"symbol": "4755.T"},
    )
    state.append_audit(
        "order_submit_result",
        client_order_id="client-1",
        broker_order_id="1",
        payload={"accepted": True},
    )

    assert state.count_submission_attempts_today() == 1
    events = state.audit_events()
    assert [event["event_type"] for event in events] == [
        "order_submit_attempt",
        "order_submit_result",
    ]
    assert events[1]["broker_order_id"] == "1"


def test_legacy_audit_migration_completed_timeout_inflight(tmp_path: Path) -> None:
    import json
    import sqlite3
    from datetime import UTC, datetime

    db_path = tmp_path / "legacy_operator.db"
    now = datetime.now(UTC).isoformat()
    # Create legacy schema without order_reservations
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE rss_order_ids (
                client_order_id TEXT PRIMARY KEY,
                rss_order_id INT,
                created_at TEXT
            );
            CREATE TABLE broker_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT,
                event_type TEXT,
                client_order_id TEXT,
                broker_order_id TEXT,
                payload_json TEXT
            );
            """
        )
        # Completed
        intent_payload = {"symbol": "4755.T", "quantity": 100, "side": "buy"}
        conn.execute(
            "INSERT INTO broker_audit("
            "created_at, event_type, client_order_id, payload_json) VALUES (?, ?, ?, ?)",
            (now, "order_submit_attempt", "c-1", json.dumps({"intent": intent_payload})),
        )
        conn.execute(
            "INSERT INTO broker_audit("
            "created_at, event_type, client_order_id, broker_order_id, payload_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                now,
                "order_submit_result",
                "c-1",
                "B-1",
                json.dumps({"accepted": True, "broker_order_id": "B-1"}),
            ),
        )

        # Timeout
        conn.execute(
            "INSERT INTO broker_audit("
            "created_at, event_type, client_order_id, payload_json) VALUES (?, ?, ?, ?)",
            (now, "order_submit_attempt", "t-1", json.dumps({"intent": intent_payload})),
        )
        conn.execute(
            "INSERT INTO broker_audit("
            "created_at, event_type, client_order_id, payload_json) VALUES (?, ?, ?, ?)",
            (now, "order_submit_error", "t-1", json.dumps({"error": "Timeout"})),
        )

        # In-flight (attempt only)
        conn.execute(
            "INSERT INTO broker_audit("
            "created_at, event_type, client_order_id, payload_json) VALUES (?, ?, ?, ?)",
            (now, "order_submit_attempt", "i-1", json.dumps({"intent": intent_payload})),
        )

    # Initializing upgraded state must migrate all 3 safely
    state = SQLiteOperatorState(db_path)
    assert state.count_submission_attempts_today() == 3

    import hashlib

    serialized = json.dumps(intent_payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    intent_hash = hashlib.sha256(serialized).hexdigest()

    # c-1 completed
    status, receipt = state.reserve_order_submission("c-1", intent_hash)
    assert status == "ALREADY_COMPLETED"
    assert receipt == {"accepted": True, "broker_order_id": "B-1"}

    # t-1 unknown
    status_t, _ = state.reserve_order_submission("t-1", intent_hash)
    assert status_t == "UNCERTAIN_OR_IN_FLIGHT"

    # i-1 unknown (in-flight)
    status_i, _ = state.reserve_order_submission("i-1", intent_hash)
    assert status_i == "UNCERTAIN_OR_IN_FLIGHT"


def test_rejected_retry_atomic_daily_limit(tmp_path: Path) -> None:
    db_path = tmp_path / "reject_operator.db"
    state = SQLiteOperatorState(db_path)

    # Initial reservation rejected
    status, _ = state.reserve_order_submission("rej-1", "hash-1")
    assert status == "RESERVED"
    state.record_submission_failure("rej-1", status="REJECTED")

    # Retry when daily limit is 1
    # First retry gets slot
    status_r, _ = state.reserve_order_submission("rej-1", "hash-1", max_orders_per_day=1)
    assert status_r == "RESERVED"

    # Concurrent retry of another rejected order is blocked by daily limit
    state.record_submission_failure("rej-2", status="REJECTED")
    status_r2, _ = state.reserve_order_submission("rej-2", "hash-2", max_orders_per_day=1)
    assert status_r2 == "DAILY_LIMIT_EXCEEDED"
