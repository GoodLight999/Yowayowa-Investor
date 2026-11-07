#!/usr/bin/env python3
"""decision_page.py の検収テスト（カード t_437a9f72）。

- 合格/不合格/判定待ち/キル（境界含む）をサンプルで実測する。
- 入力は隔離ディレクトリに合成した局面列（実データは親カードの添付サンプル1件のみ）。
- 判定日・評価時刻は固定して決定論にする。
"""
import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

import decision_page as dp

CUT = "2026-11-08T00:00:00Z"


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def event(eid, start, pnl, *, independent=True, hours=2.0):
    st = datetime.fromisoformat(start.replace("Z", "+00:00"))
    closed = st.timestamp() + hours * 3600
    cl = datetime.fromtimestamp(closed, timezone.utc)
    return {
        "schema_version": 2, "type": "episode_closed", "card": "test",
        "episode_id": eid, "day_key": start[:10],
        "episode_start_utc": start, "episode_end_utc": start,
        "episode_closed_utc": _iso(cl), "episode_closed_ms": int(closed * 1000),
        "pnl_basis": "all_trials_resolved", "n_events": 1, "n_trials": 1,
        "n_unresolved": 0, "n_filled": 1 if independent else 0,
        "independent": independent, "coins": ["TEST"], "cluster_pnl_usd": pnl,
        "kill": {"threshold_usd": -85.32, "cluster_pnl_usd": pnl,
                 "triggered": pnl < -85.32, "evaluated": True},
        "trials": [],
    }


def make_store(root, events, *, status="ARMED", entries=None, pending=0, boundary=0,
               replay=0, day_rows=True):
    root = Path(root)
    (root / "episodes").mkdir(parents=True, exist_ok=True)
    with (root / "closed_episodes.jsonl").open("w", encoding="utf-8") as fh:
        for ev in events:
            fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
    if day_rows:
        by_day = {}
        for ev in events:
            by_day.setdefault(ev["day_key"], []).append(ev)
        for day, evs in by_day.items():
            (root / "episodes" / f"{day}.json").write_text(json.dumps({
                "day_key": day, "episodes": [
                    {"episode_id": e["episode_id"], "episode_start_utc": e["episode_start_utc"],
                     "episode_end_utc": e["episode_end_utc"], "day_key": e["day_key"],
                     "independent": e["independent"], "cluster_pnl_usd": e["cluster_pnl_usd"]}
                    for e in evs]}, ensure_ascii=False), encoding="utf-8")
    (root / "kill_ledger.json").write_text(json.dumps({
        "schema_version": 2, "threshold_usd": -85.32, "cutover_utc": CUT,
        "status": status, "killed_at_utc": None, "entries": entries or []}),
        encoding="utf-8")
    (root / "exclusions.json").write_text(json.dumps({
        "schema_version": 2, "cutover_utc": CUT,
        "counts": {"episodes_total": len(events) + replay, "forward_final": len(events),
                   "replay": replay, "boundary_excluded": boundary, "pending": pending},
        "one_line": (f"excluded: replay={replay} boundary_excluded={boundary} pending={pending} "
                     f"(cutover={CUT})")}), encoding="utf-8")
    (root / "cumulative.json").write_text(json.dumps({
        "cumulative_independent_episodes": sum(1 for e in events if e["independent"])}),
        encoding="utf-8")
    return root


def series(n, pnl, start_day=8, independent=True):
    out = []
    for i in range(n):
        day = start_day + i // 12
        hour = i % 12
        st = f"2026-11-{day:02d}T{hour:02d}:00:00Z"
        out.append(event(f"ep-{i:03d}", st, pnl(i) if callable(pnl) else pnl,
                         independent=independent))
    return out


class ConstantTest(unittest.TestCase):
    def test_frozen_constants(self):
        self.assertEqual(dp.KILL_THRESHOLD_USD, -85.32)
        self.assertEqual(dp.DEADLINE_UTC, "2026-12-02")
        self.assertEqual(dp.TARGET_INDEPENDENT_EPISODES, 20)
        self.assertEqual(dp.CUTOVER_UTC, CUT)
        # 閾値がコード上に定数として明示されていること（ソース実測）
        src = Path(dp.__file__).read_text(encoding="utf-8")
        self.assertIn("KILL_THRESHOLD_USD = -85.32", src)

    def test_threshold_origin_is_not_rederived(self):
        self.assertIn("-42.657", dp.KILL_THRESHOLD_ORIGIN)


class EvaluateTest(unittest.TestCase):
    def ev(self, pnls, separate=0, **kw):
        evs = series(len(pnls), lambda i: pnls[i])
        if separate:
            for e in series(separate, -999.0):
                e["independent"] = False
                evs.append(e)
        return dp.evaluate(evs, kill_ledger_status=kw.get("status", "ARMED"),
                           decision_status=kw.get("dstatus", "ARMED"),
                           as_of=kw.get("as_of", date(2026, 11, 10)))

    def test_wait_before_trigger(self):
        r = self.ev([1.0] * 5)
        self.assertEqual(r["verdict"], "判定待ち")
        self.assertEqual(r["independent_episodes"], 5)
        self.assertIsNotNone(r["ci95"])

    def test_pass_on_20(self):
        r = self.ev([10.0] * 20)
        self.assertEqual(r["verdict"], "合格")
        self.assertGreater(r["ci95"][0], 0)

    def test_fail_at_deadline(self):
        r = self.ev([-1.0] * 5, as_of=date(2026, 12, 2))
        self.assertEqual(r["verdict"], "不合格")

    def test_fail_when_ci_lower_not_positive(self):
        # 期待値はプラス（+10）だが、ばらつきで 95%区間下限が 0 以下になる例
        pnls = [50.0] * 10 + [-30.0] * 10
        r = self.ev(pnls)
        self.assertGreater(r["mean_pnl_per_episode_usd"], 0)
        self.assertLessEqual(r["ci95"][0], 0)
        self.assertEqual(r["verdict"], "不合格")

    def test_fail_when_too_few_for_ci(self):
        r = self.ev([1.0] * 4, as_of=date(2026, 12, 2))
        self.assertEqual(r["verdict"], "不合格")
        self.assertIn("n<5", r["reason"])
        self.assertIsNone(r["ci95"])

    def test_kill_strict_boundary(self):
        self.assertEqual(self.ev([-85.32])["verdict"], "判定待ち")
        self.assertEqual(self.ev([-85.33])["verdict"], "キル発動")
        self.assertEqual(self.ev([-42.66])["verdict"], "判定待ち")

    def test_kill_ignores_deadline(self):
        r = self.ev([-85.33], as_of=date(2026, 11, 10))
        self.assertEqual(r["verdict"], "キル発動")
        self.assertFalse(r["deadline_reached"])

    def test_kill_from_ledger_status(self):
        r = self.ev([1.0] * 5, status="KILLED")
        self.assertEqual(r["verdict"], "キル発動")

    def test_non_independent_not_counted_but_kill_applies(self):
        r = self.ev([1.0] * 20, separate=1)
        self.assertEqual(r["independent_episodes"], 20)
        self.assertEqual(r["verdict"], "キル発動")  # 非独立でも -999 はキル
        self.assertEqual(r["worst_episode_usd"], -999.0)
        self.assertEqual(r["worst_independent_episode_usd"], 1.0)


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_end_to_end_pass_and_ledger(self):
        make_store(self.root, series(20, 10.0))
        now = datetime(2026, 11, 20, 0, 0, tzinfo=timezone.utc)
        out = dp.run(self.root, as_of=date(2026, 11, 20), now=now)
        self.assertEqual(out["payload"]["result"]["verdict"], "合格")
        page = (self.root / dp.PAGE_FILE).read_text(encoding="utf-8")
        self.assertIn("合格", page)
        self.assertIn("-85.32", page)
        self.assertIn("boundary_excluded", page)
        led = json.loads((self.root / dp.LEDGER_FILE).read_text(encoding="utf-8"))
        self.assertEqual(led["status"], "ARMED")
        self.assertEqual(led["counts"]["evaluated"], 20)
        first = json.loads((self.root / "closed_episodes.jsonl").read_text(encoding="utf-8").splitlines()[0])
        closed = datetime.fromisoformat(first["episode_closed_utc"].replace("Z", "+00:00"))
        self.assertEqual(led["entries"][0]["latency_hours"],
                         round((now - closed).total_seconds() / 3600, 2))
        # 再実行しても追記されない（冪等）
        dp.run(self.root, as_of=date(2026, 11, 20), now=now)
        led2 = json.loads((self.root / dp.LEDGER_FILE).read_text(encoding="utf-8"))
        self.assertEqual(len(led2["entries"]), 20)

    def test_pending_excluded_from_everything(self):
        make_store(self.root, series(5, 3.0), pending=2, boundary=1)
        out = dp.run(self.root, as_of=date(2026, 11, 15),
                     now=datetime(2026, 11, 15, tzinfo=timezone.utc))
        self.assertEqual(out["payload"]["result"]["independent_episodes"], 5)
        page = (self.root / dp.PAGE_FILE).read_text(encoding="utf-8")
        self.assertIn("pending", page)
        self.assertIn("boundary_excluded=1", page)
        self.assertIn("pending=2", page)

    def test_replay_contamination_is_rejected(self):
        evs = series(3, 1.0)
        bad = event("ep-replay", "2026-11-07T12:00:00Z", 1.0)
        make_store(self.root, evs + [bad])
        with self.assertRaises(dp.DecisionInputError):
            dp.run(self.root, as_of=date(2026, 11, 15),
                   now=datetime(2026, 11, 15, tzinfo=timezone.utc))
        rc = dp.main(["--data-dir", str(self.root.parent), "--top-dir-name", self.root.name,
                      "--out-dir-name", ".", "--no-write"])
        self.assertEqual(rc, 2)
        self.assertFalse((self.root / dp.PAGE_FILE).exists())

    def test_pending_line_rejected_if_event_unresolved(self):
        evs = series(3, 1.0)
        evs[0]["n_unresolved"] = 1
        make_store(self.root, evs)
        with self.assertRaises(dp.DecisionInputError):
            dp.run(self.root, as_of=date(2026, 11, 15),
                   now=datetime(2026, 11, 15, tzinfo=timezone.utc))

    def test_threshold_mismatch_in_ledger_rejected(self):
        make_store(self.root, series(3, 1.0))
        led = json.loads((self.root / "kill_ledger.json").read_text())
        led["threshold_usd"] = -100.0
        (self.root / "kill_ledger.json").write_text(json.dumps(led))
        with self.assertRaises(dp.DecisionInputError):
            dp.run(self.root, as_of=date(2026, 11, 15),
                   now=datetime(2026, 11, 15, tzinfo=timezone.utc))

    def test_day_file_pnl_mismatch_rejected(self):
        evs = series(3, 1.0)
        make_store(self.root, evs)
        day = evs[0]["day_key"]
        rec = json.loads((self.root / "episodes" / f"{day}.json").read_text())
        rec["episodes"][0]["cluster_pnl_usd"] = 99.0
        (self.root / "episodes" / f"{day}.json").write_text(json.dumps(rec))
        with self.assertRaises(dp.DecisionInputError):
            dp.run(self.root, as_of=date(2026, 11, 15),
                   now=datetime(2026, 11, 15, tzinfo=timezone.utc))


class RealSampleTest(unittest.TestCase):
    """親カード t_25c7da31 の実サンプル（合成局面 -125.8666・到着遅延75.7h）で実測する。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        att = Path("/root/.hermes/kanban/attachments/t_25c7da31")
        if not att.exists():
            self.skipTest("親カードの添付サンプルが無い")
        (self.root / "closed_episodes.jsonl").write_text(
            (att / "closed_episodes.jsonl").read_text(encoding="utf-8"), encoding="utf-8")
        (self.root / "kill_ledger.json").write_text(
            (att / "kill_ledger.json").read_text(encoding="utf-8"), encoding="utf-8")
        (self.root / "exclusions.json").write_text(json.dumps({
            "schema_version": 2, "cutover_utc": CUT,
            "counts": {"forward_final": 1, "replay": 19, "boundary_excluded": 0, "pending": 0},
            "one_line": "excluded: replay=19 boundary_excluded=0 pending=0 (cutover=2026-11-08T00:00:00Z)"}),
            encoding="utf-8")
        (self.root / "cumulative.json").write_text(json.dumps(
            {"cumulative_independent_episodes": 1}), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_kill_and_latency_are_recorded(self):
        now = datetime(2026, 11, 12, 0, 0, tzinfo=timezone.utc)  # 到着を故意に遅らせた想定
        out = dp.run(self.root, as_of=date(2026, 11, 12), now=now)
        res = out["payload"]["result"]
        self.assertEqual(res["verdict"], "キル発動")
        self.assertEqual(res["worst_episode_usd"], -125.8666)
        led = json.loads((self.root / dp.LEDGER_FILE).read_text(encoding="utf-8"))
        self.assertEqual(led["status"], "KILLED")
        trig = led["trigger"]
        self.assertEqual(trig["episode_id"], "ep-2026-11-08T163800Z")
        self.assertEqual(trig["evaluated_at_utc"], "2026-11-12T00:00:00Z")
        self.assertEqual(trig["latency_hours"], 77.7)   # 2026-11-08T18:18Z -> 11-12T00:00Z = 77.7h
        page = (self.root / dp.PAGE_FILE).read_text(encoding="utf-8")
        self.assertIn("キル発動", page)
        self.assertIn("77.7", page)
        # KILLED は単調（局面が消えても維持）
        (self.root / "closed_episodes.jsonl").write_text("", encoding="utf-8")
        out2 = dp.run(self.root, as_of=date(2026, 11, 12), now=now)
        self.assertEqual(out2["payload"]["result"]["verdict"], "キル発動")


if __name__ == "__main__":
    unittest.main()
