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


def test_runner_idempotency_no_duplicate_equity_drag(tmp_path: Path, monkeypatch) -> None:
    """Verify that calling run_daily_cycle multiple times is strictly idempotent and does not multiply equity."""
    from datetime import date
    from scripts.paper_c2 import c2_runner

    state_file = tmp_path / "state.json"
    mtm_file = tmp_path / "mtm_history.jsonl"
    ledger_file = tmp_path / "decisions.jsonl"
    monkeypatch.setattr(c2_runner, "STATE_FILE", state_file)
    monkeypatch.setattr(c2_runner, "MTM_FILE", mtm_file)
    # Redirect the attribution/orders outputs too: they are module-level constants bound
    # to data/paper_c2 at import time, so run_daily_cycle would overwrite production files.
    monkeypatch.setattr(c2_runner, "ATTRIBUTION_JSON", tmp_path / "attribution_summary.json")
    monkeypatch.setattr(c2_runner, "ATTRIBUTION_MD", tmp_path / "attribution_summary.md")
    monkeypatch.setattr(c2_runner, "ORDERS_FILE", tmp_path / "orders.jsonl")

    class MockAlpaca:
        def get_account(self):
            class Acct:
                equity = 100000.0
            return Acct()

    monkeypatch.setattr(c2_runner, "AlpacaPaperClient", MockAlpaca)

    # Initial state
    st = c2_runner.load_state()
    assert st["current_equity_10"] == 100000.0
    assert st["current_equity_15"] == 100000.0

    # Provide mock 2026-11-03 bar for session 2026-11-03 run
    base_hist = c2_runner.load_local_history()
    mock_hist = {}
    for s, bars in base_hist.items():
        b_list = list(bars)
        b_list.append({"t": "2026-11-03T04:00:00Z", "o": 100.0, "h": 100.0, "l": 100.0, "c": 100.0, "v": 1000.0})
        mock_hist[s] = b_list

    # First daily run on 2026-11-03
    d1 = c2_runner.run_daily_cycle(as_of_date=date(2026, 11, 3), ledger_path=ledger_file, override_history=mock_hist)
    assert d1["status"] == "success"
    eq10_first = d1["mtm_record"]["equity_10"]
    eq15_first = d1["mtm_record"]["equity_15"]

    # Read MTM file lines
    lines1 = mtm_file.read_text().splitlines()
    assert len(lines1) == 1

    # Second daily run on the SAME date 2026-11-03 (should be idempotent no-op)
    d2 = c2_runner.run_daily_cycle(as_of_date=date(2026, 11, 3), ledger_path=ledger_file, override_history=mock_hist)
    assert d2["status"] == "already_executed"
    assert round(d2["equity_10"], 2) == eq10_first
    assert round(d2["equity_15"], 2) == eq15_first

    # Third daily run on the SAME date 2026-11-03
    d3 = c2_runner.run_daily_cycle(as_of_date=date(2026, 11, 3), ledger_path=ledger_file, override_history=mock_hist)
    assert d3["status"] == "already_executed"

    # Verify MTM file still only has 1 line (not 3!)
    lines2 = mtm_file.read_text().splitlines()
    assert len(lines2) == 1

    # State equity did not drop or multiply
    st_final = c2_runner.load_state()
    assert round(st_final["current_equity_10"], 2) == eq10_first
    assert round(st_final["current_equity_15"], 2) == eq15_first

