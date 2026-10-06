from datetime import UTC, datetime

import pytest

from yowayowa.acquisition.models import AcquisitionFetchState, SnapshotRecord
from yowayowa.acquisition.snapshots import (
    SnapshotStore,
    canonical_json,
    diff_payloads,
    payload_sha256,
)


def test_canonical_json_is_key_order_independent() -> None:
    left = {"b": 2, "a": {"y": 1, "x": 2}}
    right = {"a": {"x": 2, "y": 1}, "b": 2}
    assert canonical_json(left) == canonical_json(right)
    assert payload_sha256(left) == payload_sha256(right)


def _record(connector: str, resource: str, snapshot_id: str, at: datetime) -> SnapshotRecord:
    return SnapshotRecord(
        snapshot_id=snapshot_id,
        connector_id=connector,
        resource=resource,
        captured_at=at,
        parser_version="json-v1",
        schema_version="json-v1",
        fetch_state=AcquisitionFetchState.OK,
        payload_sha256=snapshot_id,
    )


def test_append_and_history_newest_first_with_limit(tmp_path) -> None:
    store = SnapshotStore(tmp_path)
    base = datetime(2026, 9, 23, tzinfo=UTC)
    for index in range(5):
        store.append(
            "conn",
            "res",
            _record("conn", "res", f"snap-{index}", base.replace(hour=index + 1)),
            {"i": index},
        )
    history = store.history("conn", "res", 20)
    assert [record.snapshot_id for record in history] == [
        "snap-4",
        "snap-3",
        "snap-2",
        "snap-1",
        "snap-0",
    ]
    limited = store.history("conn", "res", 2)
    assert [record.snapshot_id for record in limited] == ["snap-4", "snap-3"]


def test_history_missing_file_returns_empty(tmp_path) -> None:
    store = SnapshotStore(tmp_path)
    assert store.history("ghost", "res", 10) == []
    assert store.latest_payload("ghost", "res") is None


def test_latest_payload_round_trip(tmp_path) -> None:
    store = SnapshotStore(tmp_path)
    at = datetime(2026, 9, 23, tzinfo=UTC)
    store.append("conn", "res", _record("conn", "res", "s1", at), {"a": 1})
    store.append("conn", "res", _record("conn", "res", "s2", at), {"a": 2})
    pair = store.latest_payload("conn", "res")
    assert pair is not None
    record, payload = pair
    assert record.snapshot_id == "s2"
    assert payload == {"a": 2}


def test_path_traversal_resource_rejected(tmp_path) -> None:
    store = SnapshotStore(tmp_path)
    with pytest.raises(ValueError, match="unsafe"):
        store.append(
            "conn",
            "../evil",
            _record("conn", "../evil", "s1", datetime(2026, 9, 23, tzinfo=UTC)),
            {"a": 1},
        )


def test_path_traversal_connector_rejected(tmp_path) -> None:
    store = SnapshotStore(tmp_path)
    with pytest.raises(ValueError, match="unsafe"):
        store.history("../../etc", "passwd")


def test_diff_payloads_no_change() -> None:
    changed, changes = diff_payloads({"a": 1, "b": [1, 2]}, {"b": [1, 2], "a": 1})
    assert changed is False
    assert changes == []


def test_diff_payloads_scalar_change() -> None:
    changed, changes = diff_payloads({"a": 1}, {"a": 2})
    assert changed is True
    assert changes[0].path == "$.a"
    assert changes[0].previous == "1"
    assert changes[0].current == "2"


def test_diff_payloads_nested_change() -> None:
    changed, changes = diff_payloads({"a": {"b": 1}}, {"a": {"b": 3}})
    assert changed is True
    assert changes[0].path == "$.a.b"


def test_diff_payloads_list_change() -> None:
    changed, changes = diff_payloads({"l": [1, 2]}, {"l": [1, 3]})
    assert changed is True
    paths = {change.path for change in changes}
    assert "$.l[1]" in paths


def test_diff_payloads_added_and_removed_keys() -> None:
    changed, changes = diff_payloads({"a": 1, "gone": 2}, {"a": 1, "new": 3})
    assert changed is True
    by_path = {change.path: change for change in changes}
    assert by_path["$.gone"].current is None
    assert by_path["$.new"].previous is None


def test_diff_payloads_caps_at_200_changes() -> None:
    previous = {f"k{i}": i for i in range(300)}
    current = {f"k{i}": i + 1 for i in range(300)}
    changed, changes = diff_payloads(previous, current)
    assert changed is True
    assert len(changes) == 200
