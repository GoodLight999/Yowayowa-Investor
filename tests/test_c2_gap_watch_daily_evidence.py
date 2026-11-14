"""カード t_cb7127a7: 「本日実行されたか」の判定根拠を消えない証跡へ差し替えた検証。

主根拠 = cron runner の per-run stdout 証跡 (cron/output/<job>/<YYYY-MM-DD>_*.md、
1ジョブ直近50本保持)。executions.db は全ジョブ合計で直近1000行のローリング窓
(実測で約11時間分) なので、行が無いことは警報にならない (補助根拠に降格)。
在る行の status=failed と「output も executions 行も無い日」は今まで通り警報する。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from scripts.paper_c2 import c2_gap_watch

NOW = datetime(2026, 11, 12, 0, 30, tzinfo=UTC)  # 09:30 JST
TODAY = NOW.astimezone(c2_gap_watch.JST).date()  # 2026-11-12 (木)
SESSION = "2026-11-11"  # 09:30 JST 時点で「既に閉じた」NYSE session
OLD_CLAIMED = "2026-11-11T09:00:57+09:00"
TODAY_CLAIMED = "2026-11-12T09:00:57+09:00"


def _make_db(path: Path, rows: list[dict]) -> Path:
    con = sqlite3.connect(path)
    con.execute(
        """CREATE TABLE executions (
             id TEXT PRIMARY KEY, job_id TEXT NOT NULL, source TEXT NOT NULL,
             process_id TEXT NOT NULL, pid INTEGER NOT NULL, process_started_at INTEGER,
             status TEXT NOT NULL, claimed_at TEXT NOT NULL, started_at TEXT,
             finished_at TEXT, error TEXT)"""
    )
    for row in rows:
        con.execute(
            "INSERT INTO executions (id, job_id, source, process_id, pid, status,"
            " claimed_at, started_at, finished_at, error)"
            " VALUES (?, ?, 'cron', 'p', 1, ?, ?, ?, ?, ?)",
            (
                row["id"],
                row["job_id"],
                row["status"],
                row["claimed_at"],
                row["claimed_at"],
                row["claimed_at"],
                None,
            ),
        )
    con.commit()
    con.close()
    return path


def _old_completed_row() -> dict:
    return {
        "id": "old1",
        "job_id": c2_gap_watch.JOB_ID,
        "status": "completed",
        "claimed_at": OLD_CLAIMED,
    }


def _build_data_dir(root: Path) -> Path:
    data_dir = root / "paper_c2"
    data_dir.mkdir(parents=True)
    (data_dir / "state.json").write_text(
        json.dumps(
            {
                "last_mtm_date": SESSION,
                "last_mtm_bar_date": SESSION,
                "completed_trading_days": [SESSION],
                "kill_switch_triggered": False,
            }
        ),
        encoding="utf-8",
    )
    (data_dir / "mtm_history.jsonl").write_text(
        json.dumps(
            {
                "date": SESSION,
                "last_bar_date": SESSION,
                "daily_return_15": 0.001,
                "positions_source": "fresh",
                "gross_exposure_15": 0.5,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return data_dir


def _build_ohlcv(root: Path) -> Path:
    ohlcv = root / "stock-ohlcv"
    for sym in c2_gap_watch.UNIVERSE:
        sym_dir = ohlcv / sym
        sym_dir.mkdir(parents=True)
        (sym_dir / "ohlcv.jsonl").write_text(
            json.dumps({"as_of": SESSION, "c": 1.0}) + "\n", encoding="utf-8"
        )
    return ohlcv


def _build_output_dir(root: Path, *, with_today: bool, content: str = "C2_DAILY_OK\n") -> Path:
    out_dir = root / "cron_output" / c2_gap_watch.JOB_ID
    out_dir.mkdir(parents=True)
    (out_dir / "2026-11-10_09-01-13.md").write_text("old run\n", encoding="utf-8")
    if with_today:
        (out_dir / f"{TODAY.isoformat()}_09-00-57.md").write_text(content, encoding="utf-8")
    return out_dir


def _collect(root: Path, *, output_dir: Path, db: Path, now: datetime = NOW):
    return c2_gap_watch.collect_problems(
        _build_data_dir(root),
        _build_ohlcv(root),
        db,
        c2_gap_watch.JOB_ID,
        c2_gap_watch.PHASE_START,
        now,
        output_dir=output_dir,
    )


def test_output_file_proves_today_when_executions_row_pruned(tmp_path: Path) -> None:
    """偽警報0の核心: 実行証跡 (output) が在り executions の当日行だけが窓外の場合、
    旧実装の「daily exec missing」は出ない。"""
    out_dir = _build_output_dir(tmp_path, with_today=True)
    content = (out_dir / f"{TODAY.isoformat()}_09-00-57.md").read_bytes()
    db = _make_db(tmp_path / "executions.db", [_old_completed_row()])

    problems, facts = _collect(tmp_path, output_dir=out_dir, db=db)

    assert problems == [], problems
    assert facts["daily_run_evidence"] == "cron_output"
    assert facts["daily_output_file"] == f"{TODAY.isoformat()}_09-00-57.md"
    assert facts["daily_output_sha256"] == hashlib.sha256(content).hexdigest()
    assert facts["exec_status"] is None
    assert facts["exec_aux"] == "row pruned from 1000-row rolling window"


def test_truly_missing_day_still_alarms(tmp_path: Path) -> None:
    """真警報1の核心: output も executions の当日行も無い日は今まで通り警報する。"""
    out_dir = _build_output_dir(tmp_path, with_today=False)
    db = _make_db(tmp_path / "executions.db", [_old_completed_row()])

    problems, facts = _collect(tmp_path, output_dir=out_dir, db=db)

    assert problems == [
        f"daily exec {c2_gap_watch.JOB_ID} missing for {TODAY.isoformat()} (no cron output file)"
    ], problems
    assert "daily_run_evidence" not in facts
    assert facts["exec_aux"] == "row absent"


def test_failed_row_alarms_even_with_output_file(tmp_path: Path) -> None:
    """失敗行は本物の信号なので、output 証跡が在っても警報する (旧動作を維持)。"""
    out_dir = _build_output_dir(tmp_path, with_today=True)
    db = _make_db(
        tmp_path / "executions.db",
        [
            _old_completed_row(),
            {
                "id": "today1",
                "job_id": c2_gap_watch.JOB_ID,
                "status": "failed",
                "claimed_at": TODAY_CLAIMED,
            },
        ],
    )

    problems, facts = _collect(tmp_path, output_dir=out_dir, db=db)

    assert problems == ["daily exec status=failed"], problems
    assert facts["exec_status"] == "failed"
    assert facts["daily_run_evidence"] == "cron_output"


def test_unreadable_db_fails_closed_when_no_output(tmp_path: Path) -> None:
    """主根拠が無く補助根拠も読めない場合は fail-closed で警報する。"""
    out_dir = _build_output_dir(tmp_path, with_today=False)
    db = tmp_path / "executions.db"
    db.write_bytes(b"definitely not a sqlite database")

    problems, facts = _collect(tmp_path, output_dir=out_dir, db=db)

    assert len(problems) == 1, problems
    assert "missing for" in problems[0] and "executions.db unreadable" in problems[0]
    assert "exec_db_error" in facts


def test_db_absent_fails_closed_when_no_output(tmp_path: Path) -> None:
    out_dir = _build_output_dir(tmp_path, with_today=False)
    db = tmp_path / "missing.db"  # 存在しない

    problems, _facts = _collect(tmp_path, output_dir=out_dir, db=db)

    assert problems == [
        f"daily exec {c2_gap_watch.JOB_ID} missing for {TODAY.isoformat()} (no cron output file)"
    ], problems


def test_output_file_covers_db_error(tmp_path: Path) -> None:
    """DB が読めなくても主根拠 (output) が在れば警報しない (DB は補助に降格)。"""
    out_dir = _build_output_dir(tmp_path, with_today=True)
    db = tmp_path / "executions.db"
    db.write_bytes(b"not a database")

    problems, facts = _collect(tmp_path, output_dir=out_dir, db=db)

    assert problems == [], problems
    assert facts["daily_run_evidence"] == "cron_output"
    assert "exec_db_error" in facts


def test_gate_verdict_recorded_as_facts_only(tmp_path: Path) -> None:
    """判定書 sha256/mtime は facts に残す (カード例の「判定書 sha256」)。
    判定書そのものは警報条件にしない。"""
    data_dir = _build_data_dir(tmp_path)
    verdict = data_dir / "c2_gate_verdict.md"
    verdict.write_text("# gate verdict\n", encoding="utf-8")
    out_dir = _build_output_dir(tmp_path, with_today=True)
    db = _make_db(tmp_path / "executions.db", [_old_completed_row()])
    ohlcv = _build_ohlcv(tmp_path)

    problems, facts = c2_gap_watch.collect_problems(
        data_dir,
        ohlcv,
        db,
        c2_gap_watch.JOB_ID,
        c2_gap_watch.PHASE_START,
        NOW,
        output_dir=out_dir,
    )

    assert problems == [], problems
    assert facts["gate_verdict_sha256"] == hashlib.sha256(b"# gate verdict\n").hexdigest()
    assert facts["gate_verdict_mtime_utc"] is not None

    # 判定書が無くても問題行にはしない (gate ジョブ自身の failure channel の領分)。
    data_dir2 = _build_data_dir(tmp_path / "no_verdict")
    problems2, facts2 = c2_gap_watch.collect_problems(
        data_dir2,
        ohlcv,
        db,
        c2_gap_watch.JOB_ID,
        c2_gap_watch.PHASE_START,
        NOW,
        output_dir=out_dir,
    )
    assert problems2 == [], problems2
    assert facts2["gate_verdict_mtime_utc"] is None


def test_saturday_run_is_silent(tmp_path: Path) -> None:
    """週末も output 証跡は書かれる (本番実測: 11-07/11-08 の file 有り)。
    土曜朝の健全 run は problems 0 行 (週末の偽警報がないことの回帰)。"""
    sat_now = datetime(2026, 11, 7, 0, 30, tzinfo=UTC)  # 11-07 09:30 JST (土)
    expected_session = "2026-11-06"  # 金曜 session (土曜 09:30 時点で既に閉じた)
    data_dir = tmp_path / "paper_c2"
    data_dir.mkdir(parents=True)
    (data_dir / "state.json").write_text(
        json.dumps(
            {
                "last_mtm_date": expected_session,
                "last_mtm_bar_date": expected_session,
                "completed_trading_days": [expected_session],
                "kill_switch_triggered": False,
            }
        ),
        encoding="utf-8",
    )
    (data_dir / "mtm_history.jsonl").write_text(
        json.dumps(
            {
                "date": expected_session,
                "last_bar_date": expected_session,
                "daily_return_15": 0.001,
                "positions_source": "fresh",
                "gross_exposure_15": 0.5,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "cron_output" / c2_gap_watch.JOB_ID
    out_dir.mkdir(parents=True)
    (out_dir / "2026-11-07_09-01-09.md").write_text("C2_DAILY_OK\n", encoding="utf-8")
    db = _make_db(
        tmp_path / "executions.db",
        [
            {
                "id": "fri",
                "job_id": c2_gap_watch.JOB_ID,
                "status": "completed",
                "claimed_at": "2026-11-06T09:00:57+09:00",
            },
        ],
    )
    ohlcv = tmp_path / "stock-ohlcv"
    for sym in c2_gap_watch.UNIVERSE:
        sym_dir = ohlcv / sym
        sym_dir.mkdir(parents=True)
        (sym_dir / "ohlcv.jsonl").write_text(
            json.dumps({"as_of": expected_session, "c": 1.0}) + "\n", encoding="utf-8"
        )

    problems, facts = c2_gap_watch.collect_problems(
        data_dir,
        ohlcv,
        db,
        c2_gap_watch.JOB_ID,
        c2_gap_watch.PHASE_START,
        sat_now,
        output_dir=out_dir,
    )

    assert problems == [], problems
    assert facts["daily_run_evidence"] == "cron_output"
    assert facts["daily_output_file"] == "2026-11-07_09-01-09.md"


def test_night_run_judges_literal_capture_name(tmp_path: Path) -> None:
    """カード t_8910825b: 夜間の再実行でも本番と同じ名前の当日採取
    (``2026-11-12_09-00-57.md``) が当日判定され、「daily exec missing」の
    偽警報が出ない。日付で始まらない外来名は skip され、判定は緩めない
    (当日証跡が本当に無ければ test_truly_missing_day_still_alarms の通り警報)。"""
    night_now = datetime(2026, 11, 12, 20, 40, tzinfo=c2_gap_watch.JST)  # 11-12 夜
    out_dir = tmp_path / "cron_output" / c2_gap_watch.JOB_ID
    out_dir.mkdir(parents=True)
    # 本番実在の名前 (先頭10文字が ISO 日付) + 他日の採取 + 外来名を混在させる
    (out_dir / "2026-11-12_09-00-57.md").write_text("C2_DAILY_OK\n", encoding="utf-8")
    (out_dir / "2026-11-11_09-01-13.md").write_text("yesterday\n", encoding="utf-8")
    (out_dir / "gap_watch_notes.md").write_text("foreign\n", encoding="utf-8")
    db = _make_db(tmp_path / "executions.db", [_old_completed_row()])

    problems, facts = _collect(tmp_path, output_dir=out_dir, db=db, now=night_now)

    assert problems == [], problems
    assert facts["daily_run_evidence"] == "cron_output"
    assert facts["daily_output_file"] == "2026-11-12_09-00-57.md"
    assert facts["daily_output_sha256"] == hashlib.sha256(b"C2_DAILY_OK\n").hexdigest()
    assert facts["exec_aux"] == "row pruned from 1000-row rolling window"
