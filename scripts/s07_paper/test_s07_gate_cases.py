#!/usr/bin/env python3
"""s07_gate.py の改修検証（合成stateで境界条件を実測する）。

実データ（data/s07_paper/state_*.json）は書き換えない。scratchに合成stateを作り、
`s07_gate.py --json --no-write` の戻り値を検証する。実弾0・注文0。
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
import tempfile
from pathlib import Path

GATE = Path("/root/current-work/yowayowa-investor/scripts/s07_paper/s07_gate.py")
END = dt.date(2026, 10, 29)


def make_state(ns, n_days, trips, book_bps=None, counters=None, start_equity=200.0, day_pnl=None):
    days = []
    eq = start_equity
    peak = start_equity
    for i in range(n_days):
        d = END - dt.timedelta(days=n_days - 1 - i)
        pnl = (day_pnl or {}).get(d.isoformat(), 0.0)
        eq = round(eq + pnl, 6)
        peak = max(peak, eq)
        dd = (eq / peak - 1) * 100.0
        days.append({
            "day": d.isoformat(), "status": "ok", "equity_close": eq,
            "day_pnl": pnl, "dd_pct": round(dd, 6), "executed": [],
        })
    rt = []
    for t in trips:
        rt.append({"entry_day": t[0], "exit_day": t[1], "net_pnl": t[2], "side": "long"})
    c = {"reversals": 0, "capacity_exceptions": 0, "liquidations": 0,
         "entries": len(rt), "exits": len(rt), "circuit_breaker_locks": 0, "skips": {}}
    c.update(counters or {})
    st = {
        "namespace": ns, "schema": 1, "equity_start_usdc": start_equity,
        "days": days, "round_trips": rt, "counters": c, "position": None, "pending": {},
    }
    if book_bps is not None:
        st["book_samples"] = [{"at": "2026-10-29T00:00:00Z", "half_spread_bps": b} for b in book_bps]
    return st


def run(tmp: Path, state: dict, *extra: str) -> dict:
    (tmp / f"state_{state['namespace']}.json").write_text(json.dumps(state))
    out = subprocess.run(
        [sys.executable, str(GATE), "--data-dir", str(tmp), "--namespace", state["namespace"],
         "--json", "--no-write", *extra],
        capture_output=True, text=True, check=True,
    ).stdout
    return json.loads(out.strip().splitlines()[-1])


def day(offset_from_end: int) -> str:
    return (END - dt.timedelta(days=offset_from_end)).isoformat()


CASES = []


def case(name, expect):
    def deco(fn):
        CASES.append((name, expect, fn))
        return fn
    return deco


@case("90日・4往復すべて勝ち・板実測あり → 合格/インフラ適格", {"verdict": "合格", "infra": "適格"})
def _(tmp):
    trips = [(day(80), day(70), 1.0), (day(60), day(50), 1.0),
             (day(40), day(30), 1.0), (day(20), day(10), 1.0)]
    return run(tmp, make_state("s_pass", 90, trips, book_bps=[0.1, 0.15, 0.2]))


@case("90日・5往復でPF<1.30 → 不合格", {"verdict": "不合格", "item4": "fail"})
def _(tmp):
    trips = [(day(85), day(80), 1.0), (day(70), day(65), 1.0), (day(50), day(45), 1.0),
             (day(30), day(25), 1.0), (day(20), day(10), -5.0)]
    return run(tmp, make_state("s_fail", 90, trips, book_bps=[0.1]))


@case("90日・3往復のみ → 未達（項目4は保留、不合格にしない）", {"verdict": "未達", "item4": "pending"})
def _(tmp):
    trips = [(day(80), day(70), 1.0), (day(60), day(50), 1.0), (day(40), day(30), 1.0)]
    return run(tmp, make_state("s_pending", 90, trips, book_bps=[0.1]))


@case("10日・清算1回 → 未達ではなく不合格（★は即時失格）", {"verdict": "不合格", "item_star": "fail"})
def _(tmp):
    return run(tmp, make_state("s_liq", 10, [], book_bps=[0.1], counters={"liquidations": 1}))


@case("30日・live・板実測なし → 未達（項目2は不合格表示、総合は材料不足）", {"verdict": "未達", "item2": "fail"})
def _(tmp):
    return run(tmp, make_state("s_nobook", 30, []))


@case("hist名前空間・板実測なし → 項目2はN/A", {"item2": "na"})
def _(tmp):
    return run(tmp, make_state("hist_test", 90, [(day(80), day(70), 1.0)]))


@case("live名前空間でも--hist指定 → 項目2はN/A", {"item2": "na"})
def _(tmp):
    return run(tmp, make_state("s_histflag", 90, [(day(80), day(70), 1.0)]), "--hist")


@case("120日・古い3往復+直近4往復 → 窓内4往復で合格（ロール窓）", {"verdict": "合格", "windowed": True, "trades": 4})
def _(tmp):
    trips = [(day(118), day(115), 1.0), (day(112), day(110), 1.0), (day(108), day(105), 1.0),
             (day(60), day(50), 1.0), (day(40), day(30), 1.0), (day(20), day(15), 1.0),
             (day(10), day(5), 1.0)]
    return run(tmp, make_state("s_window", 120, trips, book_bps=[0.1]))


@case("19日・黒字4往復 → 中間判定は継続（20営業日未到達）", {"midterm": "継続"})
def _(tmp):
    trips = [(day(18), day(16), 1.0), (day(14), day(12), 1.0),
             (day(10), day(8), 1.0), (day(6), day(4), 1.0)]
    return run(tmp, make_state("m_19d", 19, trips, book_bps=[0.1]))


@case("20日・4往復 PF>=1.30 → 中間判定は合格", {"midterm": "合格"})
def _(tmp):
    trips = [(day(18), day(16), 1.0), (day(14), day(12), 1.0),
             (day(10), day(8), 1.0), (day(6), day(4), -2.0)]
    return run(tmp, make_state("m_pass", 20, trips, book_bps=[0.1]))


@case("20日・3往復のみ → 中間判定は継続（サンプル不足）", {"midterm": "継続"})
def _(tmp):
    trips = [(day(18), day(16), 1.0), (day(14), day(12), 1.0), (day(10), day(8), 1.0)]
    return run(tmp, make_state("m_3trips", 20, trips, book_bps=[0.1]))


@case("20日・4往復 PF 1.00〜1.30 → 中間判定は継続", {"midterm": "継続"})
def _(tmp):
    trips = [(day(18), day(16), 1.0), (day(14), day(12), 1.0),
             (day(10), day(8), 1.0), (day(6), day(4), -2.7)]
    return run(tmp, make_state("m_mid", 20, trips, book_bps=[0.1]))


@case("20日・清算1回 → 中間判定は中止", {"midterm": "中止"})
def _(tmp):
    return run(tmp, make_state("m_liq", 20, [], book_bps=[0.1], counters={"liquidations": 1}))


@case("120日・窓外の板値10bps混入 → 窓内0.1bpsで項目2はpass（窓外を評価しない）",
      {"verdict": "合格", "item2": "pass"})
def _(tmp):
    trips = [(day(80), day(70), 1.0), (day(60), day(50), 1.0),
             (day(40), day(30), 1.0), (day(20), day(10), 1.0)]
    st = make_state("s_old_book", 120, trips)
    # 最初の30日（窓外）だけ半スプレッド10bps、直近90日は0.1bps
    st["book_samples"] = [
        {"at": d["day"] + "T01:00:00Z", "half_spread_bps": 10.0 if i < 30 else 0.1}
        for i, d in enumerate(st["days"])
    ]
    return run(tmp, st)


@case("120日・窓外ピーク持ち越し → 窓内DD0.000%で合格（DDを窓内で再計算）",
      {"verdict": "合格", "item3": "pass"})
def _(tmp):
    trips = [(day(80), day(70), 1.0), (day(60), day(50), 1.0),
             (day(40), day(30), 1.0), (day(20), day(10), 1.0)]
    pnls = {day(100): -10.0, **{t[1]: 1.0 for t in trips}}  # 窓外で200→190、窓内は右肩上がり
    return run(tmp, make_state("s_old_peak", 120, trips, book_bps=[0.1], day_pnl=pnls))


@case("90日・窓内の板値が10bps → 項目2は不合格（窓内の悪化は免除しない）",
      {"verdict": "不合格", "item2": "fail"})
def _(tmp):
    trips = [(day(80), day(70), 1.0), (day(60), day(50), 1.0),
             (day(40), day(30), 1.0), (day(20), day(10), 1.0)]
    st = make_state("s_bad_book_in_window", 90, trips)
    st["book_samples"] = [{"at": d["day"] + "T01:00:00Z", "half_spread_bps": 10.0}
                          for d in st["days"]]
    return run(tmp, st)


@case("120日・窓内で-5%のDD → 項目3は不合格（窓内DDは正しく効く）",
      {"verdict": "不合格", "item3": "fail"})
def _(tmp):
    trips = [(day(80), day(70), 1.0), (day(60), day(50), 1.0),
             (day(40), day(30), 1.0), (day(20), day(10), 1.0)]
    pnls = {t[1]: t[2] for t in trips}
    pnls[day(50)] = 10.0    # 窓内でピーク211
    pnls[day(40)] = -10.5   # 211→200.5（窓内DD -4.98%）
    return run(tmp, make_state("s_dd_in_window", 120, trips, book_bps=[0.1], day_pnl=pnls))


def check(name, expect, res) -> bool:
    ok = True
    if "midterm" in expect and (res.get("midterm") or {}).get("verdict") != expect["midterm"]:
        ok = False
    if "verdict" in expect and res["verdict"] != expect["verdict"]:
        ok = False
    if "infra" in expect and res["infra_verdict"] != expect["infra"]:
        ok = False
    if "trades" in expect and res["trades"] != expect["trades"]:
        ok = False
    if "windowed" in expect and res["window"]["windowed"] != expect["windowed"]:
        ok = False
    items = {str(c["no"]): c for c in res["checks"]}
    for key, val in expect.items():
        if key.startswith("item"):
            no = "★" if key == "item_star" else key.replace("item", "")
            if items[no]["status"] != val:
                ok = False
    print(("PASS " if ok else "FAIL ") + name)
    if not ok:
        print(f"   expect={expect}")
        print(f"   got verdict={res['verdict']} infra={res['infra_verdict']} "
              f"trades={res['trades']} windowed={res['window']['windowed']} "
              f"items={ {k: v['status'] for k, v in items.items()} }")
    return ok


def main() -> int:
    all_ok = True
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for name, expect, fn in CASES:
            all_ok &= check(name, expect, fn(tmp))
    print("RESULT:", "ALL PASS" if all_ok else "FAILURES")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
