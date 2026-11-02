# ruff: noqa: RUF002, RUF003 — 日本語の句読点（全角）は本文テキストであり誤字ではない
"""hl_exec.journal の単体テスト（三段リンケージ・追記専用性・改竄検出）。"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

import pytest

from yowayowa.hl_exec.journal import (
    STAGE_INTENT,
    STAGE_ORDER,
    STAGE_SIGNAL,
    ExecutionJournal,
    JournalIntegrityError,
    default_log_path,
    entries,
    verify_chain,
)

FIXED_NOW = _dt.datetime(2026, 11, 2, 3, 4, 5, tzinfo=_dt.UTC)


def make_journal(tmp_path: Path) -> ExecutionJournal:
    return ExecutionJournal(tmp_path / "hl_testnet_20261102.jsonl", clock=lambda: FIXED_NOW)


def test_three_stage_linkage_and_stages(tmp_path: Path) -> None:
    journal = make_journal(tmp_path)
    sig = journal.log_decision({"decision": "BUY", "recommended_size": 0.0007})
    intent = journal.log_intent(
        sig.event_id,
        {"coin": "BTC", "side": "buy", "size": 0.0007, "price": 78012, "sl_trigger": 76000},
    )
    order = journal.log_order(
        intent.event_id,
        {"wire": {"a": 0, "b": True, "p": "78012", "s": "0.0007"}},
    )
    assert (sig.stage, intent.stage, order.stage) == (STAGE_SIGNAL, STAGE_INTENT, STAGE_ORDER)
    assert (sig.seq, intent.seq, order.seq) == (1, 2, 3)
    assert sig.event_id == "SIGNAL-000001"
    assert intent.event_id == "INTENT-000002"
    assert order.event_id == "ORDER-000003"
    assert intent.parent_event_id == sig.event_id
    assert order.parent_event_id == intent.event_id
    assert all(e.ts == FIXED_NOW.isoformat(timespec="milliseconds") for e in (sig, intent, order))


def test_append_only_prefix_is_byte_identical_after_more_appends(tmp_path: Path) -> None:
    path = tmp_path / "log.jsonl"
    journal = ExecutionJournal(path, clock=lambda: FIXED_NOW)
    journal.log_decision({"n": 1})
    before = path.read_bytes()
    journal.log_intent("SIGNAL-000001", {"n": 2})
    journal.log_order("INTENT-000002", {"n": 3})
    after = path.read_bytes()
    # 追記後に既存行が1バイトも変わらないこと（追記専用の直接証明）
    assert after.startswith(before)
    assert len(after.decode("utf-8").splitlines()) == 3


def test_verify_chain_passes_on_fresh_log(tmp_path: Path) -> None:
    journal = make_journal(tmp_path)
    journal.log_decision({"n": 1})
    journal.log_intent("SIGNAL-000001", {"n": 2})
    result = verify_chain(journal._path)
    assert result.ok is True
    assert result.line_count == 2


def test_verify_chain_detects_rewritten_middle_row(tmp_path: Path) -> None:
    path = tmp_path / "log.jsonl"
    journal = ExecutionJournal(path, clock=lambda: FIXED_NOW)
    journal.log_decision({"n": 1})
    journal.log_intent("SIGNAL-000001", {"n": 2})
    journal.log_order("INTENT-000002", {"n": 3})

    lines = path.read_text(encoding="utf-8").splitlines()
    row = json.loads(lines[1])
    row["payload"]["n"] = 999  # 途中行を書き換える
    lines[1] = json.dumps(row, ensure_ascii=False, sort_keys=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = verify_chain(path)
    assert result.ok is False
    assert result.first_bad_line == 2
    assert "hash mismatch" in result.reason


def test_verify_chain_detects_removed_middle_row(tmp_path: Path) -> None:
    path = tmp_path / "log.jsonl"
    journal = ExecutionJournal(path, clock=lambda: FIXED_NOW)
    journal.log_decision({"n": 1})
    journal.log_intent("SIGNAL-000001", {"n": 2})
    journal.log_order("INTENT-000002", {"n": 3})

    lines = path.read_text(encoding="utf-8").splitlines()
    del lines[1]  # 中央の行を削除する
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = verify_chain(path)
    assert result.ok is False
    assert "seq gap" in result.reason


def test_refuses_to_append_to_corrupted_journal(tmp_path: Path) -> None:
    path = tmp_path / "log.jsonl"
    journal = ExecutionJournal(path, clock=lambda: FIXED_NOW)
    journal.log_decision({"n": 1})
    journal.log_intent("SIGNAL-000001", {"n": 2})

    lines = path.read_text(encoding="utf-8").splitlines()
    row = json.loads(lines[0])
    row["payload"]["n"] = -1
    lines[0] = json.dumps(row, ensure_ascii=False, sort_keys=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(JournalIntegrityError, match="refusing to append"):
        journal.log_order("INTENT-000002", {"n": 3})


def test_entries_roundtrip_payload(tmp_path: Path) -> None:
    journal = make_journal(tmp_path)
    payload = {"decision": "EXIT", "atr14": 1234.5}
    journal.log_decision(payload)
    loaded = entries(journal._path)
    assert len(loaded) == 1
    assert loaded[0].payload == payload
    assert loaded[0].line_no == 1


def test_default_log_path_follows_design_contract() -> None:
    path = default_log_path("testnet", day=_dt.date(2026, 11, 2), root="/tmp/whatever")
    assert str(path) == "/tmp/whatever/execution_logs/hl_testnet_20261102.jsonl"


def test_default_log_path_rejects_bad_env() -> None:
    with pytest.raises(ValueError):
        default_log_path("../evil")
    with pytest.raises(ValueError):
        default_log_path("a b")
