from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from yowayowa.acquisition.models import FieldChange, SnapshotRecord

_SAFE_COMPONENT_RE = re.compile(r"[^A-Za-z0-9_-]+")
_MAX_CHANGES = 200
_MAX_HISTORY = 1000
# Row numbers / reasons kept per read. A file with more corrupt rows than this
# still reports the full count; the lists are capped and truncated-flagged.
_MAX_CORRUPT_ROWS_REPORTED = 20


def canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )


def payload_sha256(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def reject_dot_dot(value: str) -> None:
    """Shared dot-dot rejection rule for flat filename stores.

    The orderbook snapshot store (services/orderbook_service.py) reuses this
    exact rule so a symbol can never traverse out of either store. Raises the
    same ValueError with the same message as when this check lived inline in
    ``_safe_component``; callers that must not leak the raw value catch it and
    re-raise a sanitized error.
    """
    if ".." in value:
        raise ValueError(f"unsafe path component: {value!r}")


def _safe_component(value: str) -> str:
    # Dot-dot can never appear in a legitimate flat storage name; reject it
    # outright. Everything else non-filename-safe is substituted (api/v2/x ->
    # api-v2-x), so a resource path can only ever address one file under root.
    reject_dot_dot(value)
    sanitized = _SAFE_COMPONENT_RE.sub("-", value).strip("-")
    if not sanitized or sanitized.startswith("."):
        raise ValueError(f"unsafe path component: {value!r}")
    return sanitized


# ------------------------------------------- JSONL row reading (best-effort)
#
# Append-only JSONL stores share one read contract (silent-failure audit rule
# E7): a corrupt row is skipped, it never drops the file. These helpers keep
# that contract *and* make the skip observable — the caller passes a dict and
# receives the count, the 1-based line numbers and the reasons.


def jsonl_read_diagnostics(path: Path) -> dict[str, Any]:
    """Zeroed, JSON-friendly diagnostics dict for one JSONL read.

    The caller owns the dict and passes it back into the read helpers; every
    skipped row increments ``corrupt_rows`` and records its physical line
    number and reason. ``row_count`` counts the non-empty lines examined.
    """

    return {
        "path": str(path),
        "row_count": 0,
        "corrupt_rows": 0,
        "corrupt_row_numbers": [],
        "corrupt_row_numbers_truncated": False,
        "corrupt_reasons": [],
    }


def mark_corrupt_row(diagnostics: dict[str, Any] | None, row_number: int, reason: str) -> None:
    """Count one skipped row. ``None`` diagnostics is a no-op."""

    if diagnostics is None:
        return
    diagnostics["corrupt_rows"] = int(diagnostics.get("corrupt_rows") or 0) + 1
    numbers = diagnostics.setdefault("corrupt_row_numbers", [])
    if len(numbers) < _MAX_CORRUPT_ROWS_REPORTED:
        numbers.append(row_number)
        numbers.sort()  # keeps the report ascending even for a reversed tail read
    else:
        diagnostics["corrupt_row_numbers_truncated"] = True
    reasons = diagnostics.setdefault("corrupt_reasons", [])
    if len(reasons) < _MAX_CORRUPT_ROWS_REPORTED:
        reasons.append(f"line {row_number}: {reason}")


def parse_jsonl_row(
    line: str,
    *,
    row_number: int,
    diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """One JSONL line -> object, or ``None`` for a blank/corrupt row.

    Corrupt means unparseable JSON *or* JSON that is not a top-level object
    (a bare list/string survives ``json.loads`` but cannot carry provenance;
    it used to raise ``AttributeError``/``TypeError`` in the callers). Both
    are skipped, counted and never raised.
    """

    stripped = line.strip()
    if not stripped:
        return None
    if diagnostics is not None:
        diagnostics["row_count"] = int(diagnostics.get("row_count") or 0) + 1
    try:
        item = json.loads(stripped)
    except ValueError as exc:  # json.JSONDecodeError is a ValueError subclass
        mark_corrupt_row(diagnostics, row_number, f"json_decode_error: {exc}")
        return None
    if not isinstance(item, dict):
        mark_corrupt_row(diagnostics, row_number, f"not_a_json_object: {type(item).__name__}")
        return None
    return item


def iter_jsonl_rows(
    lines: Iterable[str],
    *,
    first_row_number: int = 1,
    diagnostics: dict[str, Any] | None = None,
) -> Iterator[tuple[int, dict[str, Any]]]:
    """Yield ``(row_number, object)`` for every readable row, skipping corrupt."""

    for index, line in enumerate(lines):
        row_number = first_row_number + index
        item = parse_jsonl_row(line, row_number=row_number, diagnostics=diagnostics)
        if item is not None:
            yield row_number, item


def read_jsonl_rows(
    path: Path,
    *,
    diagnostics: dict[str, Any] | None = None,
) -> list[tuple[int, dict[str, Any]]]:
    """All readable object rows of one JSONL file with their line numbers.

    A missing file yields ``[]`` (with ``reason: file_missing`` in the
    diagnostics), never an exception.
    """

    rows: list[tuple[int, dict[str, Any]]] = []
    if not path.exists():
        if diagnostics is not None:
            diagnostics["reason"] = "file_missing"
        return rows
    with path.open("r", encoding="utf-8") as handle:
        rows.extend(iter_jsonl_rows(handle, diagnostics=diagnostics))
    return rows


def corrupt_rows_note(diagnostics: dict[str, Any] | None) -> str | None:
    """One-line operator note for skipped rows, or ``None`` for a clean read.

    Single wording shared by the acquisition outcome notes and the CLI
    surfaces, so the same degradation is described the same way wherever the
    operator meets it.
    """

    if not diagnostics:
        return None
    corrupt = int(diagnostics.get("corrupt_rows") or 0)
    if not corrupt:
        return None
    where = diagnostics.get("path") or "JSONL store"
    rows = diagnostics.get("corrupt_row_numbers") or []
    suffix = "..." if diagnostics.get("corrupt_row_numbers_truncated") else ""
    return (
        f"{corrupt} corrupt line(s) skipped in {where} (rows {rows}{suffix}): "
        "those records are treated as unseen"
    )


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

    def history(
        self,
        connector_id: str,
        resource: str,
        limit: int = 20,
        *,
        diagnostics: dict[str, Any] | None = None,
    ) -> list[SnapshotRecord]:
        """Newest-first records from the tail; corrupt rows are skipped, not fatal.

        Pass a dict as ``diagnostics`` to receive the skip count, the 1-based
        line numbers and the reasons instead of losing them (a skipped row
        used to be indistinguishable from a row that never existed). The
        counts cover the tail window this call examined (``limit`` lines).
        """

        capped = max(1, min(limit, _MAX_HISTORY))
        path = self.root / _safe_component(connector_id) / f"{_safe_component(resource)}.jsonl"
        if diagnostics is not None:
            diagnostics.clear()
            diagnostics.update(jsonl_read_diagnostics(path))
        if not path.exists():
            if diagnostics is not None:
                diagnostics["reason"] = "file_missing"
            return []
        with path.open("r", encoding="utf-8") as handle:
            lines = handle.readlines()
        window_start = max(0, len(lines) - capped)
        records: list[SnapshotRecord] = []
        for index in range(len(lines) - 1, window_start - 1, -1):
            row_number = index + 1
            entry = parse_jsonl_row(lines[index], row_number=row_number, diagnostics=diagnostics)
            if entry is None:
                continue
            try:
                records.append(SnapshotRecord.model_validate(entry.get("record")))
            except (TypeError, ValueError) as exc:
                mark_corrupt_row(
                    diagnostics, row_number, f"snapshot record unreadable: {type(exc).__name__}"
                )
        return records

    def latest_payload(
        self,
        connector_id: str,
        resource: str,
        *,
        diagnostics: dict[str, Any] | None = None,
    ) -> tuple[SnapshotRecord, dict[str, Any]] | None:
        entries = self.recent_entries(connector_id, resource, 1, diagnostics=diagnostics)
        return entries[0] if entries else None

    def recent_entries(
        self,
        connector_id: str,
        resource: str,
        count: int = 2,
        *,
        diagnostics: dict[str, Any] | None = None,
    ) -> list[tuple[SnapshotRecord, dict[str, Any]]]:
        """Newest-first (record, payload) pairs, at most `count`, from the tail.

        Corrupt rows (unparseable JSON, a non-object row, an unreadable
        record, a non-object payload) are skipped. ``diagnostics`` receives
        their count and line numbers, so a caller that fell back to an older
        snapshot can see ``corrupt_rows > 0`` rather than silently treating
        the older version as the latest one. The counts cover the lines this
        call walked (from the newest line until ``count`` readable entries).
        """

        path = self.root / _safe_component(connector_id) / f"{_safe_component(resource)}.jsonl"
        if diagnostics is not None:
            diagnostics.clear()
            diagnostics.update(jsonl_read_diagnostics(path))
        if not path.exists() or count <= 0:
            if diagnostics is not None and not path.exists():
                diagnostics["reason"] = "file_missing"
            return []
        with path.open("r", encoding="utf-8") as handle:
            lines = handle.readlines()
        entries: list[tuple[SnapshotRecord, dict[str, Any]]] = []
        for index in range(len(lines) - 1, -1, -1):
            if len(entries) >= count:
                break
            row_number = index + 1
            entry = parse_jsonl_row(lines[index], row_number=row_number, diagnostics=diagnostics)
            if entry is None:
                continue
            try:
                record = SnapshotRecord.model_validate(entry.get("record"))
            except (TypeError, ValueError) as exc:
                mark_corrupt_row(
                    diagnostics, row_number, f"snapshot record unreadable: {type(exc).__name__}"
                )
                continue
            payload = entry.get("payload")
            if not isinstance(payload, dict):
                mark_corrupt_row(
                    diagnostics, row_number, f"payload is not an object: {type(payload).__name__}"
                )
                continue
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
