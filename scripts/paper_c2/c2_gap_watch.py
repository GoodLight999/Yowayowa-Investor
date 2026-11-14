"""Read-only gap watchdog for the C2 paper daily batch.

Purpose
-------
Once a day (09:30 JST, after the 09:00 JST daily batch) cross-check the daily
job's execution status against the MTM ledger and the paper state, so a missed
trading day / duplicate bar / pre-phase contamination is reported the next
morning instead of being discovered weeks later.

Contract
--------
* Never writes to the paper data: state.json, mtm_history.jsonl,
  decisions.jsonl and friends are opened read-only.
* Silent on success (zero lines on stdout). On any anomaly prints exactly one
  line and still exits 0 (the cron delivery is the alarm, not a failure).
* Appends one JSON line per run to its own log
  (/root/.hermes/logs/c2_gap_watch.jsonl) for post-hoc diagnosis.
* "Did the daily job run today?" is judged on durable evidence (t_cb7127a7):
  the per-run stdout capture under /root/.hermes/cron/output/<job_id>/
  (one file per finished run, kept newest-50 per job) is the primary basis.
  executions.db is auxiliary only: the table keeps the newest 1000 terminal
  rows across ALL jobs (cron/executions.py MAX_TERMINAL_EXECUTIONS=1000,
  ~11h at the current fire rate), so an absent row proves nothing and must
  not alarm; a present row still feeds the status checks (failed alarms).
  The 09:35 JST gate verdict (mtime + sha256) is recorded in facts, no alarm.
* Also (card t_c5e394b0) detects a silent stop of the S-07 item-5 forward
  measurement (`data/s07_paper/funding_top20/fwd_item5/`): stale `days/` file,
  stale `cumulative.json`, no new forward episode for 48h after the cutover,
  and an unsuppressed line as soon as `kill_ledger.json` says KILLED — while the
  kill decision document (docs/S07_ITEM5_KILL_DECISION_20261112.md, card
  t_f2c6f343) is missing, that one line also says to file a card; once the
  document exists the KILLED line is no longer repeated every day.
  Keys are appended to the same log line (backward compatible); nothing in the
  forward directories is written.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import sqlite3
import sys
from collections import Counter
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from yowayowa.trading_sessions import is_session_day

JST = timezone(timedelta(hours=9))
DEFAULT_DATA_DIR = Path("/root/current-work/yowayowa-investor/data/paper_c2")
DEFAULT_OHLCV_DIR = Path("/root/current-work/yowayowa-investor/data/stock-ohlcv")
DEFAULT_DB = Path("/root/.hermes/cron/executions.db")
DEFAULT_LOG = Path("/root/.hermes/logs/c2_gap_watch.jsonl")
JOB_ID = "9f57d9154cfb"
# Per-run stdout captures written by cron.jobs.save_job_output (one file per
# finished run, filename <YYYY-MM-DD>_HH-MM-SS.md in server-local = JST days;
# pruned to the newest cron.output_retention = 50 files per job). This is the
# durable "did it run today" evidence, unlike the executions.db rolling window.
DEFAULT_OUTPUT_DIR = Path("/root/.hermes/cron/output") / JOB_ID
PHASE_START = date(2026, 11, 3)
UNIVERSE = ("SPY", "IWM", "JNJ", "PG")

# --- S-07 項目5 前方測定（funding_top20/fwd_item5/）の途絶検知（カード t_c5e394b0） ---
# 閾値は 2026-11-07 の実測から導出した。推測で縮めない・伸ばさない。
#
# 日次run = hermes cron e183d8ab2716（expr `0 10 * * *` = 10:00 JST 起動、
# last_dispatch 10:00:40 / 完了 10:03:47）。2026-11-07 実測の days/*.json mtime:
#   11-04 10:01:17 / 11-05 10:01:08 / 11-06 16:46:57（手動backfill）/ 11-07 10:03:15 JST
# 定常runの遅れは最大 3.3 分。この監視は 09:30 JST に走るため、健全時の最新ファイル
# age は約 23.5h（前日 10:03 に書かれたファイル）。1日 run が落ちると 47.5h。
# 26h = 23.5h + 2.5h（定常遅れ 3.3 分に対して十分な余裕・1日落ち 47.5h より十分小さい）。
DAYS_MAX_AGE_HOURS = 26.0
# fwd_item5/cumulative.json は同じ日次run の `--daily-hook` が書き換える。
# 実測 generated_at_utc=2026-11-07T07:27:29Z（16:27 JST）・mtime も同時刻で、
# 健全時の age は days/ と同じ 23.5h。30h は 6.5h の余裕を見た位置で、
# 1日落ち（47.5h）では確実に鳴る。
CUMULATIVE_MAX_AGE_HOURS = 30.0
# fwd_item5/episodes/YYYY-MM-DD.json は「翌日 10:00 JST の日次run」が1日1本だけ追加し、
# 既存ファイルは書き換えない（fwd_item5_pnl.py の仕様: day_key >= cutover のみ生成）。
# よって最新ファイルの age = 新しい前方局面が増えていない時間。カード指定の 48h
# （1日落ち 47.5h では鳴らず、2日連続で落ちた 71.5h で鳴る）。
EPISODES_STALE_HOURS = 48.0
DEFAULT_S07_DIR = Path("/root/current-work/yowayowa-investor/data/s07_paper/funding_top20")
CUTOVER = datetime(2026, 11, 8, 0, 0, tzinfo=UTC)
# 最初の前方 day（UTC 11-08）が書かれるのは 2026-11-09 10:00 JST の日次run。
# それまでは「forward 0」を異常扱いしない（カード条件3）。
FORWARD_GRACE_END = datetime(2026, 11, 9, 10, 0, tzinfo=JST)
# キル成立の判定文書（カード t_f2c6f343）。存在すれば「判定済み」とみなし、
# 毎日「起票しろ」を出さない（実装は既存スクリプト内の最小変更に限る）。
KILL_DECISION_DOC = (Path(__file__).resolve().parents[2]
                     / "docs" / "S07_ITEM5_KILL_DECISION_20261112.md")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            rows.append({"_invalid": line[:80]})
    return rows


def _zero_exposure_streak(mtm_rows: list[dict[str, Any]], target_abs_sum: float) -> int:
    """Trailing count of sessions with zero exposure while the target is invested.

    A day counts only when the broker book held no position and the target weights say
    it should be invested (``Σ|target_weights_15| > 0``). Unknown exposure breaks the
    streak so a missing field can never fabricate an alarm.
    """
    if target_abs_sum <= 0:
        return 0
    streak = 0
    for row in reversed(mtm_rows):
        if not isinstance(row, dict) or not row.get("date"):
            break
        exposure = _session_exposure(row)
        if exposure is None or exposure > 0.0:
            break
        streak += 1
    return streak


NON_FRESH_POSITION_SOURCES = frozenset({"carried_forward", "unknown"})


def _session_exposure(row: dict[str, Any]) -> float | None:
    """Gross exposure for one MTM session (None when the row carries no fresh evidence).

    Rows written before ``gross_exposure_15`` existed have no exposure field. For
    those, lane15 equity is the broker equity, so an exactly flat day is the
    cash-only signature (no position held); a non-zero move means a position existed.

    ``positions_source`` (added together with the field) says where the number came
    from. ``carried_forward`` reuses the previous session's observation and
    ``unknown`` has none at all: neither proves the book was flat that day, so both
    break the streak instead of feeding a false "no fills" alarm.
    """
    source = row.get("positions_source")
    if isinstance(source, str) and source in NON_FRESH_POSITION_SOURCES:
        return None
    raw = row.get("gross_exposure_15")
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if row.get("daily_return_15") == 0.0:
        return 0.0
    return None


def _last_ohlcv_date(path: Path) -> date | None:
    """Read only the tail of the OHLCV file to find the newest as_of date."""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 4096))
            tail = f.read().decode("utf-8", "replace")
        lines = [ln for ln in tail.splitlines() if ln.strip()]
        if not lines:
            return None
        row = json.loads(lines[-1])
        raw = str(row.get("as_of") or row.get("t") or "")[:10]
        return date.fromisoformat(raw)
    except Exception:
        return None


def _local_bars(ohlcv_dir: Path) -> dict[str, date | None]:
    return {sym: _last_ohlcv_date(ohlcv_dir / sym / "ohlcv.jsonl") for sym in UNIVERSE}


def _latest_local_bar(bars: dict[str, date | None]) -> date | None:
    """Oldest of the four symbols' latest bars (a partial update must not look fresh)."""
    dates = [d for d in bars.values() if d is not None]
    return min(dates) if dates else None


def _expected_session(today: date, phase_start: date) -> date | None:
    """Most recent NYSE session that was already closed at the 09:30 JST check.

    09:30 JST is 00:30 UTC, so the previous US session (today - 1 day in the
    JST calendar) has closed. Weekends and NYSE holidays rewind to the prior
    session. Returns None when that session predates the phase start.
    """
    day = today - timedelta(days=1)
    for _ in range(10):
        if is_session_day(day):
            return day if day >= phase_start else None
        day -= timedelta(days=1)
    return None


def _today_execution(db: Path, job_id: str, today_jst: date) -> dict[str, Any] | None:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            """
            SELECT id, status, claimed_at, started_at, finished_at, error
            FROM executions
            WHERE job_id = ?
            ORDER BY claimed_at DESC
            LIMIT 50
            """,
            (job_id,),
        ).fetchall()
    finally:
        con.close()
    for row in rows:
        claimed = str(row["claimed_at"] or "")
        try:
            claimed_dt = datetime.fromisoformat(claimed)
        except ValueError:
            continue
        if claimed_dt.tzinfo is None:
            claimed_dt = claimed_dt.replace(tzinfo=UTC)
        if claimed_dt.astimezone(JST).date() == today_jst:
            return dict(row)
    return None


def _today_output_file(output_dir: Path, today_jst: date) -> dict[str, Any] | None:
    """Newest per-run stdout capture (``<YYYY-MM-DD>_HH-MM-SS.md``) written today (JST).

    The cron runner writes one file per finished run (cron.jobs.save_job_output) and
    prunes only beyond the newest ``cron.output_retention`` files per job (default 50
    — about 50 days for a daily job), so today's capture cannot slide out of reach the
    way an executions.db row can. Filename dates are server-local days (JST here:
    hermes_time.now has no timezone override on this host).
    """
    if not output_dir.is_dir():
        return None
    best: dict[str, Any] | None = None
    best_mtime = -1.0
    with contextlib.suppress(OSError):
        for path in output_dir.glob("*.md"):
            try:
                run_day = date.fromisoformat(path.name[:10])
            except ValueError:
                continue  # not a per-run capture (foreign/legacy layout)
            if run_day != today_jst:
                continue
            try:
                stat = path.stat()
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                continue
            row = {
                "name": path.name,
                "mtime_utc": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
                "sha256": digest,
            }
            if stat.st_mtime > best_mtime:
                best, best_mtime = row, stat.st_mtime
    return best


def _gate_verdict_facts(data_dir: Path, now: datetime) -> dict[str, Any]:
    """Record the 09:35 JST gate verdict (c2_gate --cron-mode rewrites it on every
    daily gate run) as durable corroboration of the day chain: mtime, sha256, age.

    Facts only, never a problem: the gate job has its own failure channel
    (cron incidents / ``cron list`` failure_streak), and this watchdog's alarm
    surface must not grow beyond the daily-run evidence check (t_cb7127a7).
    """
    facts: dict[str, Any] = {}
    path = data_dir / "c2_gate_verdict.md"
    try:
        stat = path.stat()
    except OSError:
        facts["gate_verdict_mtime_utc"] = None
        return facts
    mtime_utc = datetime.fromtimestamp(stat.st_mtime, UTC)
    facts["gate_verdict_mtime_utc"] = mtime_utc.isoformat()
    facts["gate_verdict_age_hours"] = round(_age_hours(now.astimezone(UTC), mtime_utc), 2)
    with contextlib.suppress(OSError):
        facts["gate_verdict_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return facts


def collect_problems(
    data_dir: Path,
    ohlcv_dir: Path,
    db: Path,
    job_id: str,
    phase_start: date,
    now: datetime,
    output_dir: Path | None = None,
) -> tuple[list[str], dict[str, Any]]:
    today = now.astimezone(JST).date()
    problems: list[str] = []
    facts: dict[str, Any] = {"today_jst": today.isoformat()}

    state_path = data_dir / "state.json"
    state: dict[str, Any] = {}
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception as exc:
        problems.append(f"state.json unreadable ({exc})")

    mtm_rows = _read_jsonl(data_dir / "mtm_history.jsonl")
    dates: list[str] = []
    for row in mtm_rows:
        raw = row.get("date") if isinstance(row, dict) else None
        if raw:
            dates.append(str(raw)[:10])
        else:
            problems.append("mtm_history row without date")
    facts["mtm_rows"] = len(mtm_rows)

    if output_dir is None:
        output_dir = DEFAULT_OUTPUT_DIR
    if today >= phase_start:
        # --- 本日実行の判定根拠（t_cb7127a7）-----------------------------------
        # 主根拠: cron runner 自身の per-run stdout 証跡
        # (/root/.hermes/cron/output/<job_id>/<YYYY-MM-DD>_HH-MM-SS.md)。各 run の
        # 終了時に1本書かれ、保持は「1ジョブあたり直近 cron.output_retention=50 本」
        # （cron/jobs.py）なので日次ジョブでは約50日分残る = 消えない証跡。
        # 補助根拠: executions.db。全ジョブ合計で端末状態の直近1000行だけのローリング
        # 窓（cron/executions.py MAX_TERMINAL_EXECUTIONS=1000、書込ごとに _prune_unlocked）
        # で、現在の発火レートでは窓は約11時間分。行が無いことは「本日実行なし」を
        # 意味しないので、行の欠落だけでは警報しない。在る行の status=failed 等は
        # 本物の失敗信号として今まで通り警報する。
        out_row = _today_output_file(output_dir, today)
        if out_row is not None:
            facts["daily_run_evidence"] = "cron_output"
            facts["daily_output_file"] = out_row["name"]
            facts["daily_output_mtime_utc"] = out_row["mtime_utc"]
            facts["daily_output_sha256"] = out_row["sha256"]
        exec_row: dict[str, Any] | None = None
        if db.exists():
            try:
                exec_row = _today_execution(db, job_id, today)
            except Exception as exc:
                facts["exec_db_error"] = str(exc)
        if exec_row is not None:
            facts["exec_status"] = exec_row.get("status")
            if exec_row.get("status") in ("running", "claimed"):
                pass  # still in flight; a real miss surfaces via the MTM bar check
            elif exec_row.get("status") != "completed":
                problems.append(f"daily exec status={exec_row.get('status')}")
        else:
            facts["exec_status"] = None
            facts["exec_aux"] = (
                "row pruned from 1000-row rolling window" if out_row is not None
                else "row absent"
            )
        if out_row is None and exec_row is None:
            # 主根拠が無いときだけ executions.db を存在証拠として使う（補助根拠）。
            # どちらの証跡も無い = 本当に実行が無い可能性が高いので警報（fail-closed）。
            detail = (
                f"; executions.db unreadable ({facts['exec_db_error']})"
                if "exec_db_error" in facts else ""
            )
            problems.append(
                f"daily exec {job_id} missing for {today} (no cron output file{detail})"
            )

    bar_dates: list[str] = []
    for row in mtm_rows:
        if not isinstance(row, dict):
            continue
        raw = row.get("last_bar_date")
        if row.get("date") is None:
            continue
        if raw is None:
            problems.append(f"mtm row {str(row.get('date'))[:10]} without last_bar_date")
            continue
        bar = str(raw)[:10]
        bar_dates.append(bar)
        d = str(row.get("date"))[:10]
        if bar != d:
            problems.append(f"mtm date {d} != last_bar_date {bar}")

    dups = [d for d, n in Counter(dates).items() if n > 1]
    if dups:
        problems.append(f"duplicate mtm bar dates {sorted(dups)}")
    dup_bars = [d for d, n in Counter(bar_dates).items() if n > 1]
    if dup_bars:
        problems.append(f"duplicate mtm last_bar_date {sorted(dup_bars)}")

    pre_phase = sorted({d for d in dates + bar_dates if d < phase_start.isoformat()})
    if pre_phase:
        problems.append(f"pre-phase mtm rows {pre_phase}")

    last_row_date = dates[-1] if dates else None
    facts["last_mtm_row"] = last_row_date
    if state:
        if last_row_date is None:
            if state.get("last_mtm_date") is not None:
                problems.append(
                    f"state.last_mtm_date={state.get('last_mtm_date')} but mtm history is empty"
                )
        else:
            if state.get("last_mtm_date") != last_row_date:
                problems.append(
                    f"state.last_mtm_date={state.get('last_mtm_date')} != last row {last_row_date}"
                )
            if state.get("last_mtm_bar_date") != last_row_date:
                problems.append(
                    f"state.last_mtm_bar_date={state.get('last_mtm_bar_date')} "
                    f"!= last row {last_row_date}"
                )
        completed = sorted(set(state.get("completed_trading_days") or []))
        if completed != sorted(set(dates)):
            problems.append("state.completed_trading_days != mtm dates")
        if state.get("kill_switch_triggered"):
            problems.append("kill switch triggered")
        # c2_runner increments this when the session bar is stale/missing and the
        # MTM row is skipped (the daily job still exits 0 / "completed").
        missed = state.get("missed_trading_days") or 0
        facts["missed_trading_days"] = missed
        if missed > 0:
            problems.append(f"state.missed_trading_days={missed}")

    # Non-fill surveillance: the target says "invested" but the broker book is flat.
    # target_weights_15 falls back to current_weights_15 for pre-split state.json.
    # USD is the cash weight, not a position, so it is excluded from the sum.
    target_weights = state.get("target_weights_15")
    if not isinstance(target_weights, dict):
        target_weights = state.get("current_weights_15") or {}
    target_abs_sum = sum(
        abs(float(weight))
        for symbol, weight in target_weights.items()
        if str(symbol).upper() != "USD"
        and isinstance(weight, (int, float))
        and not isinstance(weight, bool)
    )
    zero_exposure_streak = _zero_exposure_streak(mtm_rows, target_abs_sum)
    facts["target_weight_abs_sum"] = round(target_abs_sum, 6)
    facts["zero_exposure_streak"] = zero_exposure_streak
    if zero_exposure_streak >= 2 and target_abs_sum > 0:
        problems.append(
            f"zero exposure for {zero_exposure_streak} consecutive sessions "
            f"while target gross weight={target_abs_sum:.4f}"
        )

    decision_ids: list[str] = []
    for row in _read_jsonl(data_dir / "decisions.jsonl"):
        if isinstance(row, dict) and row.get("decision_id"):
            decision_ids.append(str(row["decision_id"]))
    dup_decisions = sorted(d for d, n in Counter(decision_ids).items() if n > 1)
    if dup_decisions:
        problems.append(f"duplicate decision_id {dup_decisions}")
    facts["decisions"] = len(decision_ids)

    bars = _local_bars(ohlcv_dir)
    missing_syms = sorted(sym for sym, d in bars.items() if d is None)
    facts["missing_ohlcv_symbols"] = missing_syms
    if missing_syms:
        problems.append(f"no local OHLCV bars for {missing_syms}")
    latest_bar = _latest_local_bar(bars)
    facts["latest_local_bar"] = latest_bar.isoformat() if latest_bar else None
    # Most recent NYSE session already closed at the 09:30 JST check. Computed
    # unconditionally (outside the latest_bar branch) so the phase-start edge
    # case cannot short-circuit the MTM-row check. On the morning of the phase
    # start day this is None: the first session has not closed yet, so silence
    # is correct (no false alarm) and the bar is still pre-phase by design.
    expected = _expected_session(today, phase_start)
    facts["expected_session"] = expected.isoformat() if expected else None
    if latest_bar is None:
        problems.append("no local OHLCV bars found")
    elif expected is not None:
        if latest_bar < expected:
            problems.append(
                f"latest local bar {latest_bar} has not reached expected session {expected}"
            )
        if last_row_date is None:
            problems.append(f"no MTM row for expected session {expected}")
        elif last_row_date < expected.isoformat():
            problems.append(
                f"no new MTM row for expected session {expected} (last={last_row_date})"
            )
    elif latest_bar >= phase_start and latest_bar.isoformat() > str(
        state.get("last_mtm_bar_date") or ""
    ):
        problems.append(
            f"new bar {latest_bar} not recorded (state.last_mtm_bar_date="
            f"{state.get('last_mtm_bar_date')})"
        )

    facts.update(_gate_verdict_facts(data_dir, now))
    return problems, facts


def _parse_utc_dt(raw: str) -> datetime | None:
    """Parse an ISO8601 timestamp (``Z`` or offset) into an aware UTC datetime."""
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _age_hours(now_utc: datetime, then: datetime) -> float:
    """Hours since ``then``, clamped at 0.

    The clamp only protects the ``--now`` test override: when the override is
    earlier than the on-disk data (regression replay), a negative age must not
    be read as "healthy but somehow negative"; 0 means "as fresh as possible".
    """
    return max(0.0, (now_utc - then).total_seconds() / 3600.0)


def collect_forward_problems(
    s07_dir: Path,
    cutover: datetime,
    grace_end: datetime,
    now: datetime,
) -> tuple[list[str], dict[str, Any]]:
    """Read-only detection of a silent stop of the S-07 item-5 forward run.

    Four checks, each anchored on a measured artifact of the existing daily run
    (10:00 JST cron e183d8ab2716). Nothing is written here; ``main`` writes the
    single log line. ``problems`` is appended to the C2 findings, so the C2
    verdict itself is unchanged when the forward lane is healthy.
    """
    problems: list[str] = []
    facts: dict[str, Any] = {}
    now_utc = now.astimezone(UTC)
    cutover_day = cutover.astimezone(UTC).date()

    # 1) days/ の最新ファイルが日次runの想定時刻から古すぎないか。
    days_dir = s07_dir / "days"
    day_files: list[Path] = []
    with contextlib.suppress(OSError):
        day_files = [p for p in days_dir.iterdir() if p.is_file() and p.suffix == ".json"]
    if not day_files:
        facts["s07_days_latest"] = None
        problems.append(f"S-07 days/ に日次ファイルが無い ({days_dir})")
    else:
        try:
            newest = max(day_files, key=lambda p: p.stat().st_mtime)
            mtime = datetime.fromtimestamp(newest.stat().st_mtime, UTC)
        except OSError as exc:
            problems.append(f"S-07 days/ の mtime 読取不能 ({exc})")
        else:
            age = _age_hours(now_utc, mtime)
            facts["s07_days_latest"] = newest.name
            facts["s07_days_latest_mtime_utc"] = mtime.isoformat()
            facts["s07_days_age_hours"] = round(age, 2)
            if age > DAYS_MAX_AGE_HOURS:
                problems.append(
                    f"S-07 days/ 最新 {newest.name} が {age:.1f}h 更新されていない"
                    f"（閾値 {DAYS_MAX_AGE_HOURS:.0f}h・日次run は 10:00 JST）"
                )

    fwd_dir = s07_dir / "fwd_item5"

    # 2) cumulative.json の generated_at_utc が進んでいない。
    cum_path = fwd_dir / "cumulative.json"
    if not cum_path.is_file():
        facts["fwd_cumulative_generated_at_utc"] = None
        problems.append(f"fwd_item5/cumulative.json が無い ({cum_path})")
    else:
        cum: Any = None
        try:
            cum = json.loads(cum_path.read_text(encoding="utf-8"))
        except Exception as exc:
            problems.append(f"fwd_item5/cumulative.json 読取不能 ({exc})")
        if isinstance(cum, dict):
            raw = str(cum.get("generated_at_utc") or "")
            facts["fwd_cumulative_generated_at_utc"] = raw or None
            gen = _parse_utc_dt(raw) if raw else None
            if gen is None:
                problems.append(f"fwd_item5/cumulative.json generated_at_utc 不正 ({raw!r})")
            else:
                age = _age_hours(now_utc, gen)
                facts["fwd_cumulative_age_hours"] = round(age, 2)
                if age > CUMULATIVE_MAX_AGE_HOURS:
                    problems.append(
                        f"fwd_item5 cumulative が {age:.1f}h 進んでいない"
                        f"（generated_at_utc={raw}・閾値 {CUMULATIVE_MAX_AGE_HOURS:.0f}h）"
                    )

    # 3) episodes/ の前方ファイル（day_key >= cutover）が48h増えていない。
    #    cutover 前と、最初の前方 day が書かれる前（grace_end）は判定しない。
    ep_dir = fwd_dir / "episodes"
    forward_files: list[tuple[date, Path]] = []
    total_files = 0
    if ep_dir.is_dir():
        for path in sorted(ep_dir.glob("*.json")):
            total_files += 1
            try:
                day_key = date.fromisoformat(path.stem)
            except ValueError:
                continue
            if day_key >= cutover_day:
                forward_files.append((day_key, path))
    facts["fwd_episode_files"] = total_files
    facts["fwd_forward_episodes"] = len(forward_files)
    facts["fwd_forward_latest_day"] = (
        max(day for day, _ in forward_files).isoformat() if forward_files else None
    )
    if now >= grace_end:
        if not forward_files:
            problems.append(
                "fwd_item5 前方局面が0（cutover="
                f"{cutover.isoformat()} 以降の episode ファイルが無い）"
            )
        else:
            latest_day, latest_path = max(forward_files, key=lambda item: item[0])
            try:
                latest_mtime = datetime.fromtimestamp(latest_path.stat().st_mtime, UTC)
            except OSError:
                latest_mtime = None
            if latest_mtime is not None:
                age = _age_hours(now_utc, latest_mtime)
                facts["fwd_forward_latest_mtime_utc"] = latest_mtime.isoformat()
                facts["fwd_forward_age_hours"] = round(age, 2)
                if age > EPISODES_STALE_HOURS:
                    problems.append(
                        f"fwd_item5 前方局面が {age:.1f}h 増えていない"
                        f"（最新 {latest_day.isoformat()}・閾値 {EPISODES_STALE_HOURS:.0f}h）"
                    )

    # 4) kill_ledger.json が KILLED になった瞬間は抑制しない（grace も見ない）。
    kill_path = fwd_dir / "kill_ledger.json"
    if not kill_path.is_file():
        facts["fwd_kill_status"] = None
        problems.append(f"fwd_item5/kill_ledger.json が無い ({kill_path})")
    else:
        ledger: Any = None
        try:
            ledger = json.loads(kill_path.read_text(encoding="utf-8"))
        except Exception as exc:
            problems.append(f"fwd_item5/kill_ledger.json 読取不能 ({exc})")
        if isinstance(ledger, dict):
            status = str(ledger.get("status") or "")
            facts["fwd_kill_status"] = status
            facts["fwd_kill_killed_at_utc"] = ledger.get("killed_at_utc")
            if status == "KILLED":
                # キル成立の1行が「流れて終わる」のを防ぐ（カード t_f2c6f343）。
                # 判定文書が無い間は、起票を促す文言まで同じ1行に含めて出す。
                if KILL_DECISION_DOC.is_file():
                    facts["fwd_kill_decision_doc"] = str(KILL_DECISION_DOC)
                else:
                    facts["fwd_kill_decision_doc"] = None
                    problems.append(
                        "fwd_item5 キル条件成立: status=KILLED"
                        f"（killed_at_utc={ledger.get('killed_at_utc')}・"
                        f"閾値 {ledger.get('threshold_usd')} USD）"
                        "→ 判定を文書化し、実資金・判定の候補から除外して再挑戦禁止を固定すること"
                        "（未起票なら /root/.hermes/scripts/kanban-create.sh で起票）"
                    )

    return problems, facts


def _append_log(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="C2 paper gap watchdog (read-only)")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--ohlcv-dir", type=Path, default=DEFAULT_OHLCV_DIR)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--job-id", default=JOB_ID)
    parser.add_argument("--phase-start", type=date.fromisoformat, default=PHASE_START)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    parser.add_argument("--s07-dir", type=Path, default=DEFAULT_S07_DIR)
    parser.add_argument(
        "--cutover",
        type=_parse_utc_dt,
        default=CUTOVER,
        help="Forward-lane cutover (ISO8601); episodes with day_key >= this date count as forward.",
    )
    parser.add_argument(
        "--forward-grace-end",
        type=_parse_utc_dt,
        default=FORWARD_GRACE_END,
        help="Until this instant a zero forward-episode count is not an anomaly (ISO8601).",
    )
    parser.add_argument(
        "--now",
        default=None,
        help="Override current time (ISO8601) for testing; JST is used for the day boundary.",
    )
    args = parser.parse_args(argv)

    now = datetime.fromisoformat(args.now) if args.now else datetime.now(JST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=JST)

    problems: list[str] = []
    facts: dict[str, Any] = {}
    try:
        problems, facts = collect_problems(
            args.data_dir, args.ohlcv_dir, args.db, args.job_id, args.phase_start, now,
            output_dir=args.output_dir,
        )
    except Exception as exc:  # never fail silently
        problems = [f"watchdog error: {exc}"]

    # S-07 項目5 前方測定の途絶検知（カード t_c5e394b0）。既存 C2 の判定の後ろに
    # 追加するだけで、ここで落ちても上の problems は失わない。
    try:
        forward_problems, forward_facts = collect_forward_problems(
            args.s07_dir, args.cutover, args.forward_grace_end, now
        )
    except Exception as exc:  # never fail silently
        forward_problems, forward_facts = [f"forward watchdog error: {exc}"], {}
    problems = problems + forward_problems
    facts = {**facts, **forward_facts}

    write_log = str(args.log) not in ("", "/dev/null", "none")
    with contextlib.suppress(Exception):
        if write_log:
            _append_log(
                args.log,
                {
                    "ts_utc": datetime.now(UTC).isoformat(),
                    "data_dir": str(args.data_dir),
                    **facts,
                    "problems": problems,
                },
            )

    if problems:
        print("C2ペーパー監視: " + " / ".join(problems))
    return 0


if __name__ == "__main__":
    sys.exit(main())
