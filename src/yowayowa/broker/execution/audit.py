"""Append-only, hash-chained JSONL audit trail for broker execution (P2A).

Every line is `{seq, ts, kind, client_order_id, payload, prev_hash,
entry_hash}` where `entry_hash` is a SHA-256 over the canonical JSON of
the line without `entry_hash`. The file is only ever opened in append
mode with fsync after each write; `verify()` recomputes the whole chain
to detect gaps, tampering, or reordering.

A sidecar state file (`audit.state.json`) records `{count,
last_entry_hash}` after every append via atomic replace, so tail
truncation of the JSONL file is also detected by `verify()`. Torn or
unparsable lines never raise: they are reported as verify problems and
skipped by `entries()`.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

AUDIT_FILE_NAME = "audit.jsonl"
AUDIT_STATE_FILE_NAME = "audit.state.json"
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
        self._state_path = self._dir / AUDIT_STATE_FILE_NAME
        self._clock: Callable[[], datetime] = clock or _now_utc

    @property
    def path(self) -> Path:
        return self._path

    @property
    def state_path(self) -> Path:
        return self._state_path

    def _write_state(self, count: int, last_entry_hash: str | None) -> None:
        """Atomically persist {count, last_entry_hash} to the sidecar file."""

        self._dir.mkdir(parents=True, exist_ok=True)
        tmp_path = self._dir / (AUDIT_STATE_FILE_NAME + ".tmp")
        payload = {"count": count, "last_entry_hash": last_entry_hash}
        with tmp_path.open("w", encoding="utf-8") as handle:
            handle.write(canonical_json(payload))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, self._state_path)

    def _backfill_state(self, entries: list[AuditEntry]) -> None:
        """Self-heal a missing sidecar from the current chain (upgrade path)."""

        self._write_state(len(entries), entries[-1].entry_hash if entries else None)

    def append(self, kind: str, client_order_id: str, payload: dict[str, Any]) -> AuditEntry:
        if kind not in _AUDIT_KINDS:
            raise ValueError(f"unknown audit kind: {kind!r}")
        if not client_order_id:
            raise ValueError("client_order_id must not be empty")
        entries, _problems = self._scan()
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
        self._write_state(seq, line["entry_hash"])
        return AuditEntry.model_validate(line)

    def _scan(self) -> tuple[list[AuditEntry], list[str]]:
        """Parse the log file, reporting unparsable lines instead of raising.

        Returns (entries, problems): `entries` holds every cleanly parsed
        line in file order; `problems` describes torn/garbage lines that
        were skipped.
        """

        if not self._path.exists():
            return [], []
        parsed: list[AuditEntry] = []
        problems: list[str] = []
        with self._path.open("r", encoding="utf-8") as handle:
            for line_number, line_text in enumerate(handle, start=1):
                stripped = line_text.strip()
                if not stripped:
                    continue
                try:
                    parsed.append(AuditEntry.model_validate_json(stripped))
                except (ValidationError, ValueError):
                    problems.append(f"line {line_number}: unparsable audit line (skipped)")
        return parsed, problems

    def entries(self) -> list[AuditEntry]:
        """Parse the log file; returns [] when no audit file exists yet.

        Never raises on a torn or unparsable line: such lines are skipped
        and reported by `verify()`.
        """

        return self._scan()[0]

    def verify(self) -> list[str]:
        """Recompute the full chain; return human-readable problems ([] = intact).

        Also cross-checks the sidecar state file so tail truncation and
        in-place tampering of the last entry are detected. Never raises.
        """

        problems: list[str] = []
        entries, scan_problems = self._scan()
        problems.extend(scan_problems)
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
        problems.extend(self._verify_state(entries))
        return problems

    def _verify_state(self, entries: list[AuditEntry]) -> list[str]:
        """Cross-check the sidecar state file against the parsed chain."""

        if not self._path.exists():
            return []
        if not self._state_path.exists():
            # Upgrade path: an audit directory written before the sidecar
            # existed self-heals instead of failing verification.
            self._backfill_state(entries)
            return []
        try:
            state = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError):
            return ["audit state file unparsable"]
        if not isinstance(state, dict):
            return ["audit state file unparsable"]
        problems: list[str] = []
        chain_count = len(entries)
        state_count = state.get("count")
        if state_count != chain_count:
            problems.append(
                f"audit state count mismatch: state records {state_count!r}, "
                f"chain has {chain_count} entries"
            )
        expected_last = entries[-1].entry_hash if entries else None
        if state.get("last_entry_hash") != expected_last:
            problems.append("audit state last_entry_hash does not match the last entry hash")
        return problems

    def replay(self) -> list[AuditEntry]:
        """Replay hook for restart-safe state rebuilds (alias of entries())."""

        return self.entries()


__all__ = [
    "AUDIT_FILE_NAME",
    "AUDIT_STATE_FILE_NAME",
    "AppendOnlyAuditLog",
    "AuditEntry",
    "canonical_json",
    "entry_hash",
]
