"""Append-only, hash-chained JSONL audit trail for broker execution (P2A).

Every line is `{seq, ts, kind, client_order_id, payload, prev_hash,
entry_hash}` where `entry_hash` is a SHA-256 over the canonical JSON of
the line without `entry_hash`. The file is only ever opened in append
mode with fsync after each write; `verify()` recomputes the whole chain
to detect gaps, tampering, or reordering.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

AUDIT_FILE_NAME = "audit.jsonl"
_AUDIT_KINDS: frozenset[str] = frozenset({"intent", "request", "response", "state"})
_GENESIS_PREV_HASH = "0" * 64


def canonical_json(payload: object) -> str:
    """Canonical JSON encoding used for every hash in the audit chain."""

    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def entry_hash(line_without_hash: dict[str, Any]) -> str:
    """SHA-256 hex of the canonical JSON of a line without ``entry_hash``."""

    return hashlib.sha256(canonical_json(line_without_hash).encode("utf-8")).hexdigest()


class AuditEntry(BaseModel):
    seq: int
    ts: datetime
    kind: str
    client_order_id: str
    payload: dict[str, Any]
    prev_hash: str
    entry_hash: str


def _now_utc() -> datetime:
    return datetime.now(UTC)


class AppendOnlyAuditLog:
    """Hash-chained, append-only JSONL audit log stored in a directory."""

    def __init__(
        self,
        directory: Path | str,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._dir = Path(directory)
        self._path = self._dir / AUDIT_FILE_NAME
        self._clock: Callable[[], datetime] = clock or _now_utc

    @property
    def path(self) -> Path:
        return self._path

    def append(self, kind: str, client_order_id: str, payload: dict[str, Any]) -> AuditEntry:
        if kind not in _AUDIT_KINDS:
            raise ValueError(f"unknown audit kind: {kind!r}")
        if not client_order_id:
            raise ValueError("client_order_id must not be empty")
        entries = self.entries()
        seq = (entries[-1].seq + 1) if entries else 1
        prev_hash = entries[-1].entry_hash if entries else _GENESIS_PREV_HASH
        line: dict[str, Any] = {
            "seq": seq,
            "ts": self._clock().isoformat(),
            "kind": kind,
            "client_order_id": client_order_id,
            "payload": payload,
            "prev_hash": prev_hash,
        }
        line["entry_hash"] = entry_hash(line)
        self._dir.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(line, ensure_ascii=False, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return AuditEntry.model_validate(line)

    def entries(self) -> list[AuditEntry]:
        """Parse the log file; returns [] when no audit file exists yet."""

        if not self._path.exists():
            return []
        parsed: list[AuditEntry] = []
        with self._path.open("r", encoding="utf-8") as handle:
            for line_text in handle:
                stripped = line_text.strip()
                if not stripped:
                    continue
                parsed.append(AuditEntry.model_validate_json(stripped))
        return parsed

    def verify(self) -> list[str]:
        """Recompute the full chain; return human-readable problems ([] = intact)."""

        problems: list[str] = []
        entries = self.entries()
        expected_prev = _GENESIS_PREV_HASH
        expected_seq = 1
        for entry in entries:
            label = f"entry seq={entry.seq}"
            if entry.seq != expected_seq:
                problems.append(f"{label}: expected seq {expected_seq}, found {entry.seq}")
            if entry.prev_hash != expected_prev:
                problems.append(f"{label}: prev_hash does not match the previous entry hash")
            recompute: dict[str, Any] = {
                "seq": entry.seq,
                "ts": entry.ts.isoformat(),
                "kind": entry.kind,
                "client_order_id": entry.client_order_id,
                "payload": entry.payload,
                "prev_hash": entry.prev_hash,
            }
            if entry_hash(recompute) != entry.entry_hash:
                problems.append(f"{label}: entry_hash does not match recomputed content hash")
            expected_prev = entry.entry_hash
            expected_seq = entry.seq + 1
        return problems

    def replay(self) -> list[AuditEntry]:
        """Replay hook for restart-safe state rebuilds (alias of entries())."""

        return self.entries()


__all__ = [
    "AUDIT_FILE_NAME",
    "AppendOnlyAuditLog",
    "AuditEntry",
    "canonical_json",
    "entry_hash",
]
