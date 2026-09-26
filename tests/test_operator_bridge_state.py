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
    state.append_audit(
        "order_submit_attempt",
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
