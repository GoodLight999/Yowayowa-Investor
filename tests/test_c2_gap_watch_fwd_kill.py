"""カード t_f2c6f343: キル成立の1行が「流れて終わる」のを防ぐ出力の検証。

判定文書が無いときは起票を促す文言まで同じ1行に含め、判定文書があるときは
KILLED 行を毎日繰り返さない (facts に判定文書のパスを残す)。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from scripts.paper_c2 import c2_gap_watch

NOW = datetime(2026, 11, 12, 0, 30, tzinfo=UTC)
CUTOVER = datetime(2026, 11, 8, 0, 0, tzinfo=UTC)
GRACE_END = datetime(2026, 11, 9, 1, 0, tzinfo=UTC)


def _build_s07_dir(root: Path) -> Path:
    """健全な前方局面 (cumulative も episodes も新しい) + KILLED 台帳を作る。"""
    s07_dir = root / "funding_top20"
    fwd = s07_dir / "fwd_item5"
    (fwd / "episodes").mkdir(parents=True)
    (fwd / "cumulative.json").write_text(
        json.dumps({"generated_at_utc": NOW.isoformat()}), encoding="utf-8"
    )
    (fwd / "episodes" / "2026-11-11.json").write_text("{}", encoding="utf-8")
    (fwd / "kill_ledger.json").write_text(
        json.dumps(
            {"status": "KILLED", "killed_at_utc": "2026-11-11T01:03:17Z", "threshold_usd": -85.32}
        ),
        encoding="utf-8",
    )
    return s07_dir


def test_killed_line_asks_to_file_a_card_when_no_decision_doc(tmp_path: Path, monkeypatch) -> None:
    s07_dir = _build_s07_dir(tmp_path)
    monkeypatch.setattr(c2_gap_watch, "KILL_DECISION_DOC", tmp_path / "docs" / "none.md")

    problems, facts = c2_gap_watch.collect_forward_problems(s07_dir, CUTOVER, GRACE_END, NOW)

    kill_lines = [p for p in problems if "KILLED" in p]
    assert len(kill_lines) == 1, problems
    assert "起票" in kill_lines[0]
    assert "除外" in kill_lines[0]
    assert facts["fwd_kill_decision_doc"] is None


def test_killed_line_is_not_repeated_once_decision_doc_exists(tmp_path: Path, monkeypatch) -> None:
    s07_dir = _build_s07_dir(tmp_path)
    doc = tmp_path / "docs" / "S07_ITEM5_KILL_DECISION_20261112.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("# decision\n", encoding="utf-8")
    monkeypatch.setattr(c2_gap_watch, "KILL_DECISION_DOC", doc)

    problems, facts = c2_gap_watch.collect_forward_problems(s07_dir, CUTOVER, GRACE_END, NOW)

    assert not [p for p in problems if "KILLED" in p], problems
    assert facts["fwd_kill_status"] == "KILLED"
    assert facts["fwd_kill_decision_doc"] == str(doc)
