"""Unit tests for C2 hash-chained decision ledger."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.paper_c2.c2_ledger import (
    GENESIS_HASH,
    append_decision_record,
    get_last_line_sha256,
    verify_hash_chain,
)


def test_c2_ledger_chaining_and_tamper_detection(tmp_path: Path) -> None:
    ledger_file = tmp_path / "test_decisions.jsonl"

    # Step 1: Initial state
    h0, cnt0 = get_last_line_sha256(ledger_file)
    assert h0 == GENESIS_HASH
    assert cnt0 == 0

    # Step 2: Append record 1 (10% series)
    rec1 = {
        "decision_id": "C2:10:2026-11-03:001",
        "timestamp_utc": "2026-11-03T14:30:00Z",
        "candidate": "C2",
        "target_vol": 0.10,
        "inputs": {"spy_close": 755.72},
        "rule_output": {"w_rule": 0.95},
        "llm_decision": {"called": True, "parsed_intent": "HOLD_RULE"},
        "final_weights": {"SPY": 0.2375, "USD": 0.05},
        "attribution_label": "RULE",
    }
    h1 = append_decision_record(rec1, ledger_file)
    assert rec1["prev_line_sha256"] == GENESIS_HASH

    # Verify line 1 hash
    line1 = ledger_file.read_text(encoding="utf-8").strip()
    assert hashlib.sha256(line1.encode("utf-8")).hexdigest() == h1

    # Step 3: Append record 2 (15% series)
    rec2 = {
        "decision_id": "C2:15:2026-11-03:002",
        "timestamp_utc": "2026-11-03T14:30:00Z",
        "candidate": "C2",
        "target_vol": 0.15,
        "inputs": {"spy_close": 755.72},
        "rule_output": {"w_rule": 1.00},
        "llm_decision": {"called": True, "parsed_intent": "HOLD_RULE"},
        "final_weights": {"SPY": 0.25, "USD": 0.00},
        "attribution_label": "RULE",
    }
    _h2 = append_decision_record(rec2, ledger_file)

    # Condition (b) verification: rec2.prev_line_sha256 MUST equal h1!
    assert rec2["prev_line_sha256"] == h1

    # Step 4: Verify full chain
    is_valid, issues = verify_hash_chain(ledger_file)
    assert is_valid is True
    assert len(issues) == 0

    # Step 5: Tamper test: modify record 1
    lines = ledger_file.read_text(encoding="utf-8").splitlines()
    tampered_rec = json.loads(lines[0])
    tampered_rec["inputs"]["spy_close"] = 999.99
    tampered_line = json.dumps(tampered_rec, ensure_ascii=False, sort_keys=True)
    lines[0] = tampered_line
    ledger_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Step 6: Verify chain detects tamper on record 2
    is_valid_after, issues_after = verify_hash_chain(ledger_file)
    assert is_valid_after is False
    assert any("prev_line_sha256 mismatch" in iss for iss in issues_after)
