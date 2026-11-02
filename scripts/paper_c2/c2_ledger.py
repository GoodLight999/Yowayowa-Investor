"""Atomic, tamper-evident hash-chained JSONL ledger for C2 decision records.

Guarantees:
- Every record is appended atomically with fsync.
- Line i contains `prev_line_sha256` matching sha256(raw_bytes(line i-1)).
- Genesis line uses 64 zeros as prev_line_sha256.
- Full verification function verify_hash_chain() to audit provenance.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

GENESIS_HASH = "0" * 64
DEFAULT_LEDGER_PATH = Path("/root/current-work/yowayowa-investor/data/paper_c2/decisions.jsonl")
V6_PROVENANCE_SHA = "363f9c9880d982ae463bc82a516dbe4cf08ef767001a9b0162678837c6ac6149"


@dataclass(frozen=True)
class DecisionRecord:
    decision_id: str
    timestamp_utc: str
    candidate: str
    target_vol: float
    inputs: dict[str, Any]
    rule_output: dict[str, Any]
    llm_decision: dict[str, Any]
    final_weights: dict[str, float]
    attribution_label: str
    prev_line_sha256: str
    provenance_source_sha256: str = V6_PROVENANCE_SHA

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "timestamp_utc": self.timestamp_utc,
            "candidate": self.candidate,
            "target_vol": self.target_vol,
            "inputs": self.inputs,
            "rule_output": self.rule_output,
            "llm_decision": self.llm_decision,
            "final_weights": self.final_weights,
            "attribution_label": self.attribution_label,
            "prev_line_sha256": self.prev_line_sha256,
            "provenance_source_sha256": self.provenance_source_sha256,
        }


def get_last_line_sha256(ledger_path: Path = DEFAULT_LEDGER_PATH) -> tuple[str, int]:
    """Return (sha256_of_last_line, line_count). If file empty/missing, return (GENESIS_HASH, 0)."""
    if not ledger_path.exists():
        return GENESIS_HASH, 0

    lines = ledger_path.read_text(encoding="utf-8").splitlines()
    non_empty = [ln for ln in lines if ln.strip()]
    if not non_empty:
        return GENESIS_HASH, 0

    last_line = non_empty[-1]
    # Hash the exact line string as UTF-8 bytes
    h = hashlib.sha256(last_line.encode("utf-8")).hexdigest()
    return h, len(non_empty)


def append_decision_record(
    record_dict: dict[str, Any],
    ledger_path: Path = DEFAULT_LEDGER_PATH,
) -> str:
    """Atomically append a single decision record to the JSONL ledger, chaining hashes."""
    ledger_path.parent.mkdir(parents=True, exist_ok=True)

    prev_hash, _ = get_last_line_sha256(ledger_path)
    record_dict["prev_line_sha256"] = prev_hash

    # Validate essential fields
    for req in [
        "decision_id",
        "timestamp_utc",
        "inputs",
        "rule_output",
        "llm_decision",
        "final_weights",
        "attribution_label",
        "prev_line_sha256",
    ]:
        if req not in record_dict:
            raise ValueError(f"Missing mandatory ledger field: {req}")

    line = json.dumps(record_dict, ensure_ascii=False, sort_keys=True)
    with open(ledger_path, "a", encoding="utf-8") as f:
        f.write(line + "\n")
        f.flush()
        os.fsync(f.fileno())

    new_hash = hashlib.sha256(line.encode("utf-8")).hexdigest()
    return new_hash


def verify_hash_chain(ledger_path: Path = DEFAULT_LEDGER_PATH) -> tuple[bool, list[str]]:
    """Verify hash chain integrity from line 1 to end."""
    if not ledger_path.exists():
        return False, [f"Ledger file not found: {ledger_path}"]

    lines = ledger_path.read_text(encoding="utf-8").splitlines()
    non_empty = [ln for ln in lines if ln.strip()]
    if not non_empty:
        return True, ["Ledger is empty (0 records)."]

    issues = []
    expected_prev = GENESIS_HASH

    for idx, raw_line in enumerate(non_empty, start=1):
        try:
            data = json.loads(raw_line)
        except Exception as exc:
            issues.append(f"Line {idx}: JSON parse error: {exc}")
            continue

        prev_in_rec = data.get("prev_line_sha256")
        if prev_in_rec != expected_prev:
            issues.append(
                f"Line {idx} ({data.get('decision_id')}): prev_line_sha256 mismatch! "
                f"Got '{prev_in_rec}', expected '{expected_prev}'"
            )

        # Update expected_prev for next line
        expected_prev = hashlib.sha256(raw_line.encode("utf-8")).hexdigest()

    is_valid = len(issues) == 0
    return is_valid, issues
