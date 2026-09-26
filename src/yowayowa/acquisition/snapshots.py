from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from yowayowa.acquisition.models import FieldChange, SnapshotRecord

_SAFE_COMPONENT_RE = re.compile(r"[^A-Za-z0-9_-]+")
_MAX_CHANGES = 200
_MAX_HISTORY = 1000


def canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )


def payload_sha256(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _safe_component(value: str) -> str:
    # Dot-dot can never appear in a legitimate flat storage name; reject it
    # outright. Everything else non-filename-safe is substituted (api/v2/x ->
    # api-v2-x), so a resource path can only ever address one file under root.
    if ".." in value:
        raise ValueError(f"unsafe path component: {value!r}")
    sanitized = _SAFE_COMPONENT_RE.sub("-", value).strip("-")
    if not sanitized or sanitized.startswith("."):
        raise ValueError(f"unsafe path component: {value!r}")
    return sanitized


class SnapshotStore:
    """Append-only JSONL snapshot history under a local data directory."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, connector_id: str, resource: str) -> Path:
        connector_dir = self.root / _safe_component(connector_id)
        connector_dir.mkdir(parents=True, exist_ok=True)
        return connector_dir / f"{_safe_component(resource)}.jsonl"

    def append(
        self,
        connector_id: str,
        resource: str,
        record: SnapshotRecord,
        payload: dict[str, Any],
    ) -> None:
        path = self._path(connector_id, resource)
        line = json.dumps(
            {"record": record.model_dump(mode="json"), "payload": payload},
            ensure_ascii=False,
            default=str,
        )
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def history(self, connector_id: str, resource: str, limit: int = 20) -> list[SnapshotRecord]:
        capped = max(1, min(limit, _MAX_HISTORY))
        path = self.root / _safe_component(connector_id) / f"{_safe_component(resource)}.jsonl"
        if not path.exists():
            return []
        with path.open("r", encoding="utf-8") as handle:
            lines = handle.readlines()
        records: list[SnapshotRecord] = []
        for line in reversed(lines[-capped:]):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                entry = json.loads(stripped)
                records.append(SnapshotRecord.model_validate(entry["record"]))
            except (json.JSONDecodeError, KeyError, ValueError):
                continue
        return records

    def latest_payload(
        self, connector_id: str, resource: str
    ) -> tuple[SnapshotRecord, dict[str, Any]] | None:
        entries = self.recent_entries(connector_id, resource, 1)
        return entries[0] if entries else None

    def recent_entries(
        self, connector_id: str, resource: str, count: int = 2
    ) -> list[tuple[SnapshotRecord, dict[str, Any]]]:
        """Newest-first (record, payload) pairs, at most `count`, from the tail."""
        path = self.root / _safe_component(connector_id) / f"{_safe_component(resource)}.jsonl"
        if not path.exists() or count <= 0:
            return []
        with path.open("r", encoding="utf-8") as handle:
            lines = handle.readlines()
        entries: list[tuple[SnapshotRecord, dict[str, Any]]] = []
        for line in reversed(lines):
            if len(entries) >= count:
                break
            stripped = line.strip()
            if not stripped:
                continue
            try:
                parsed = json.loads(stripped)
                record = SnapshotRecord.model_validate(parsed["record"])
                payload = parsed["payload"]
            except (json.JSONDecodeError, KeyError, ValueError):
                continue
            if isinstance(payload, dict):
                entries.append((record, payload))
        return entries


def _render(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


def diff_payloads(
    previous: dict[str, Any], current: dict[str, Any]
) -> tuple[bool, list[FieldChange]]:
    """Recursive structural diff over the union of keys; caps at 200 changes."""
    changes: list[FieldChange] = []

    def walk(prev: Any, cur: Any, path: str) -> None:
        if len(changes) >= _MAX_CHANGES:
            return
        if isinstance(prev, dict) and isinstance(cur, dict):
            for key in sorted(set(prev) | set(cur), key=str):
                child = f"{path}.{key}"
                if key not in prev:
                    changes.append(
                        FieldChange(path=child, previous=None, current=_render(cur[key]))
                    )
                elif key not in cur:
                    changes.append(
                        FieldChange(path=child, previous=_render(prev[key]), current=None)
                    )
                else:
                    walk(prev[key], cur[key], child)
            return
        if isinstance(prev, list) and isinstance(cur, list):
            for index in range(max(len(prev), len(cur))):
                child = f"{path}[{index}]"
                if index >= len(prev):
                    changes.append(
                        FieldChange(path=child, previous=None, current=_render(cur[index]))
                    )
                elif index >= len(cur):
                    changes.append(
                        FieldChange(path=child, previous=_render(prev[index]), current=None)
                    )
                else:
                    walk(prev[index], cur[index], child)
            return
        prev_json = _render(prev)
        cur_json = _render(cur)
        if prev_json != cur_json:
            changes.append(FieldChange(path=path, previous=prev_json, current=cur_json))

    walk(previous, current, "$")
    return (bool(changes), changes[:_MAX_CHANGES])
