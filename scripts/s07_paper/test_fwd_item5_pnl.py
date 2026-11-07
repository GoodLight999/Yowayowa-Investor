#!/usr/bin/env python3
"""【項目5】fwd_item5_pnl v2 の単体テスト（API不使用・合成データ・決定的）

カード t_25c7da31（v1 は t_d4fdcfc4）。既存スイート（test_hl_liq_obs.py /
test_s07_gate_cases.py / test_jpy_premium_obs.py）には触れない別ファイル。
実行: python3 test_fwd_item5_pnl.py → RESULT: ALL PASS / RC=0

v2 で検証する核（カードの受入条件に対応）
  * cutover=2026-11-08T00:00:00Z の3分類（forward / replay / boundary_excluded）
  * 全 trial 確定のときだけ確定（censored を外した部分集計をしない＝pending は使わない）
  * 確定時刻 episode_closed_utc（出口足の終値時刻）と episode_end_utc（最後の事象時刻）の分離
  * KILLED の即時永続・再起動維持・単調（取り消さない）・重複追記なし
  * 生成器へ渡す closed_episodes.jsonl が追記のみで重複しない
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cascade_sim  # noqa: E402
import fwd_item5_pnl as F  # noqa: E402

CASES: list[tuple[str, Callable[[], None]]] = []
HERE = Path(__file__).resolve().parent
MEASURE_1M = Path("/root/.hermes/kanban/attachments/t_88d455cb/results_1m_primary.json")
CUTOVER_DAY = "2026-11-08"


def case(name: str):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


def day_ms(day: str) -> int:
    return int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def crash_path(i: int, at: int, exit_crash: bool) -> float:
    """at で 10分 -3%（100→97）＋出来高急増、at+40 まで 89 へ下落して指値(98%)に浸透、
    at+40..at+100 で出口側へ動き、at+100 以降は一定（約定足+60分の終値が出口）。"""
    if i <= at - 10:
        return 100.0
    if i <= at:
        return 100.0 - 3.0 * (i - (at - 10)) / 10.0
    if i <= at + 40:
        return 97.0 - 8.0 * (i - at) / 40.0
    if i <= at + 100:
        return 89.0 + ((78.0 if exit_crash else 95.0) - 89.0) * (i - (at + 40)) / 60.0
    return 78.0 if exit_crash else 95.0


def vol_at(i: int, at: int) -> float:
    return 60.0 if at - 9 <= i <= at else 1.0


def make_store(root: Path, *, day0: str = "2026-11-08", n_days: int = 2, at: int = 1000,
               exit_crash: bool = False, truncate_at: int | None = None,
               coin: str = "TEST") -> Path:
    """合成の前向き収集ストア（candles_1m n_days 日分＋既存 days/ のダミー旧行）を作る。"""
    top = root / "funding_top20"
    cdir = top / "candles_1m"
    ddir = top / "days"
    cdir.mkdir(parents=True, exist_ok=True)
    ddir.mkdir(parents=True, exist_ok=True)
    total = n_days * 1440
    t0 = day_ms(day0)
    rows = []
    for i in range(total):
        c = crash_path(i, at, exit_crash)
        o = crash_path(i - 1, at, exit_crash) if i else c
        rows.append({"t": t0 + i * 60_000, "o": o, "h": max(o, c) * 1.0002,
                     "l": min(o, c) * 0.9998, "c": c, "v": vol_at(i, at)})
    if truncate_at is not None:
        rows = rows[:truncate_at]
    for d in range(n_days):
        part = rows[d * 1440:(d + 1) * 1440]
        if not part:
            continue
        day = datetime.fromtimestamp((t0 + d * 86_400_000) / 1000, timezone.utc).strftime("%Y-%m-%d")
        (cdir / f"{day}.json").write_text(json.dumps({
            "schema_version": 1, "card": "t_7a53946d", "day_key": day, "interval": "1m",
            "source": "synthetic", "coins": {coin: part}, "updated_at_utc": "2026-11-10T00:00:00Z",
            "coin_minute_counts": {coin: len(part)},
            "day_completeness": {"expected_minutes": 1440, "min_coin_minutes": min(1440, len(part))},
        }, ensure_ascii=False, indent=1))
    (ddir / f"{day0}.json").write_text(json.dumps({
        "schema_version": 1, "card": "t_00e2c1d7", "day_key": day0,
        "coins": [{"coin": coin, "ann_net_pct": 1.23}],
        "b2_liquidation_observation": {"thresholds": {"wick_threshold": 0.6}},
    }, ensure_ascii=False, indent=1))
    return root


def now_after(root: Path, days: int = 3) -> int:
    """合成ストアの最終日より後に来た「今」（テストを決定的にする）。"""
    days_present = sorted(p.stem for p in (root / "funding_top20/candles_1m").glob("*.json"))
    last = days_present[-1]
    return day_ms(last) + days * 86_400_000


def scan_outside_fwd(root: Path) -> dict[str, str]:
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and "fwd_item5" not in p.parts:
            out[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def fwd_dir(root: Path) -> Path:
    return root / "funding_top20/fwd_item5"


def ledger_of(root: Path) -> dict:
    return json.loads((fwd_dir(root) / "kill_ledger.json").read_text())


def events_of(root: Path) -> list[dict]:
    p = fwd_dir(root) / "closed_episodes.jsonl"
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]


# --------------------------------------------------------------------- キル条件（凍結・不変）
@case("kill: -85.32 は非発動・-85.33 で発動（境界は「より悪い」で発動）")
def _():
    assert F.evaluate_kill(-85.32)["triggered"] is False
    assert F.evaluate_kill(-85.33)["triggered"] is True
    assert F.evaluate_kill(-42.66)["triggered"] is False
    assert F.evaluate_kill(0.0)["triggered"] is False
    assert F.evaluate_kill(-1e9)["triggered"] is True
    assert F.evaluate_kill(-85.32)["threshold_usd"] == -85.32


@case("kill: 閾値は測定時最悪局面の2倍で、定数として明示されている")
def _():
    assert F.KILL_THRESHOLD_USD == -85.32
    assert abs(F.KILL_THRESHOLD_USD - 2 * F.WORST_EPISODE_AT_MEASUREMENT_USD) < 1e-9
    assert "42.657" in F.KILL_THRESHOLD_ORIGIN


# --------------------------------------------------------------------- 凍結仕様（不変）
@case("spec: 深さ2%/遅延30分/待ち30/保有60/手数料4.5bps/1,000USD は測定時と同一")
def _():
    assert F.FROZEN_DEPTH_PCT == 2.0 and F.FROZEN_DELAY_MIN == 30
    assert F.FROZEN_DEPTH_PCT in cascade_sim.DEPTHS and F.FROZEN_DELAY_MIN in cascade_sim.DELAYS_MIN
    assert F.FROZEN_REST_MIN == 30 and F.FROZEN_HOLD_MIN == 60
    assert F.FROZEN_FEE_BPS_PER_SIDE == 4.5 and F.FROZEN_NOTIONAL_USD == 1000.0
    assert F.EPISODE_GAP_MIN == 60 and F.EVENT_DROP_PCT == 2.0 and F.EVENT_VOL_MULT == 3.0


@case("spec: 流用した cascade_sim.py は測定実行と同一バイト（sha256）")
def _():
    ident = F.cascade_sim_identity()
    assert ident["identical_to_measurement"] is True, ident
    assert ident["sha256"] == F.CASCADE_SIM_SHA256


@case("spec: cutover は 2026-11-08T00:00:00Z で固定（COO裁定・再導出しない）")
def _():
    assert F.CUTOVER_UTC == "2026-11-08T00:00:00Z"
    assert F.CUTOVER_DAY == "2026-11-08"


# --------------------------------------------------------------------- 局面の数え方（不変）
@case("episode: 直前の局面開始から60分ちょうどは同一局面・61分で新規")
def _():
    base = 1793491200000
    evs = [{"t_close_ms": base}, {"t_close_ms": base + 60 * 60_000},
           {"t_close_ms": base + 61 * 60_000}, {"t_close_ms": base + 200 * 60_000}]
    n = F.assign_episodes(evs)
    assert n == 3, [e["episode"] for e in evs]
    assert [e["episode"] for e in evs] == [0, 0, 1, 2]


@case("episode: 測定実行の事象リストで局面番号が完全一致（実物の results_1m_primary.json）")
def _():
    if not MEASURE_1M.exists():
        print("    SKIP: 測定物が無い（t_88d455cb 添付）")
        return
    d = json.loads(MEASURE_1M.read_text())
    evs = [dict(e) for e in d["events"]]
    n = F.assign_episodes(evs)
    assert n == d["n_episodes"], (n, d["n_episodes"])
    assert [e["episode"] for e in evs] == [e["episode"] for e in d["events"]]


# --------------------------------------------------------------------- cutover 3分類
@case("cutover: 開始>=cutover は forward / 終了<=cutover は replay / 跨ぎは boundary_excluded")
def _():
    cut = day_ms(CUTOVER_DAY)
    assert F.classify_episode(cut, cut + 3_600_000) == "forward"
    assert F.classify_episode(cut - 7_200_000, cut) == "replay"
    assert F.classify_episode(cut - 7_200_000, cut - 60_000) == "replay"
    assert F.classify_episode(cut - 60_000, cut + 60_000) == "boundary_excluded"
    assert F.classify_episode(cut - 3 * 86_400_000, cut + 1) == "boundary_excluded"


# --------------------------------------------------------------------- 合成データでの前方（確定）
@case("build: cutover 後の局面は forward・全trial確定・損益が出る（利益側）")
def _():
    root = make_store(Path(tempfile.mkdtemp(prefix="fwd5f_")), day0="2026-11-08", n_days=2,
                      at=1000, exit_crash=False)
    built = F.build(root, now_ms=now_after(root))
    assert built["n_forward_final"] == 1, (built["n_forward_final"], built["n_pending"])
    ep = built["episodes"][0]
    assert ep["classification"] == "forward" and ep["status"] == "final"
    assert ep["counts_for_forward"] is True
    assert ep["n_filled"] == 1 and ep["cluster_pnl_usd"] > 0
    assert ep["kill"]["triggered"] is False and ep["kill"]["evaluated"] is True
    assert built["day_records"][0]["day_key"] == "2026-11-08"
    assert built["day_records"][0]["cumulative"]["independent_episodes"] == 1
    assert built["cumulative"]["cumulative_independent_episodes"] == 1
    # 生成器へ渡すイベントに全trial確定と確定時刻が入る
    ev = built["closed_events"][0]
    assert ev["pnl_basis"] == "all_trials_resolved" and ev["n_unresolved"] == 0
    assert ev["episode_closed_utc"] is not None


@case("確定時刻: episode_closed_utc は出口足の終値時刻で、episode_end_utc（最後の事象）より後")
def _():
    root = make_store(Path(tempfile.mkdtemp(prefix="fwd5t_")), day0="2026-11-08", n_days=2, at=1000)
    ep = F.build(root, now_ms=now_after(root))["episodes"][0]
    end_ms = F._iso_ms(ep["episode_end_utc"])
    assert ep["episode_closed_ms"] > end_ms, (ep["episode_end_utc"], ep["episode_closed_utc"])
    # 事象close + 遅延30 + 待ち内約定 + 保有60 以上あと
    assert ep["episode_closed_ms"] - end_ms >= 90 * 60_000
    fill = ep["fills"][0]
    assert F._iso_ms(fill["determined_utc"]) == ep["episode_closed_ms"]
    assert fill["determined_rule"] == "exit_bar_close"


@case("pending: trial が censored なら未確定（部分損益を出さない・kill 未評価・累積に入れない）")
def _():
    root = make_store(Path(tempfile.mkdtemp(prefix="fwd5c_")), day0="2026-11-08", n_days=2,
                      at=730, truncate_at=800)
    built = F.build(root, now_ms=now_after(root))
    assert built["n_pending"] == 1 and built["n_forward_final"] == 0, (
        built["n_pending"], built["n_forward_final"])
    ep = built["episodes"][0]
    assert ep["status"] == "pending" and ep["n_unresolved"] >= 1
    assert ep["cluster_pnl_usd"] is None
    assert ep["kill"]["triggered"] is None and ep["kill"]["evaluated"] is False
    assert ep["counts_for_forward"] is False
    assert ep["episode_closed_utc"] is None
    assert built["cumulative"]["cumulative_independent_episodes"] == 0
    assert built["cumulative"]["kill"]["triggered"] is False
    assert built["closed_events"] == []
    assert built["exclusions"]["counts"]["pending"] == 1
    assert built["exclusions"]["one_line"].startswith("excluded: ")


@case("boundary: 開始<cutover<=終了 の局面は boundary_excluded（時刻を残し、累積・killに使わない）")
def _():
    # 2026-11-07 23:00 に事象 → 出口は 2026-11-08 00:40 頃（cutover 跨ぎ）
    root = make_store(Path(tempfile.mkdtemp(prefix="fwd5b_")), day0="2026-11-06", n_days=3,
                      at=2820, exit_crash=True)
    built = F.build(root, now_ms=day_ms("2026-11-12"))
    assert built["n_boundary_excluded"] == 1, [e["classification"] for e in built["episodes"]]
    ep = built["episodes"][0]
    assert ep["classification"] == "boundary_excluded"
    assert F._iso_ms(ep["episode_start_utc"]) < F._iso_ms(F.CUTOVER_UTC) <= ep["episode_closed_ms"]
    assert ep["counts_for_forward"] is False
    assert built["cumulative"]["cumulative_independent_episodes"] == 0
    assert built["cumulative"]["kill"]["triggered"] is False
    assert built["closed_events"] == []
    be = built["exclusions"]["boundary_excluded_episodes"]
    assert len(be) == 1 and be[0]["episode_start_utc"] and be[0]["episode_closed_utc"]


@case("replay: cutover 前の局面は replay（記録するが累積・killに使わない）")
def _():
    root = make_store(Path(tempfile.mkdtemp(prefix="fwd5r_")), day0="2026-11-01", n_days=2,
                      at=1000, exit_crash=True)
    built = F.build(root, now_ms=day_ms("2026-11-12"))
    assert built["n_replay"] == 1 and built["n_forward_final"] == 0
    assert built["episodes"][0]["classification"] == "replay"
    assert built["cumulative"]["cumulative_independent_episodes"] == 0
    assert built["cumulative"]["kill"]["triggered"] is False
    assert built["day_records"] == []          # cutover 前の day ファイルは書かない


# --------------------------------------------------------------------- キル台帳（即時・永続・単調）
@case("kill: forward 局面が -85.32 を割れば1回の実行で KILLED が台帳に永続化される")
def _():
    root = make_store(Path(tempfile.mkdtemp(prefix="fwd5k_")), day0="2026-11-08", n_days=2,
                      at=1000, exit_crash=True)
    rc = F.cmd_daily_hook(root, now_ms=now_after(root))
    assert rc == 0
    led = ledger_of(root)
    assert led["status"] == "KILLED", led
    assert led["trigger"]["cluster_pnl_usd"] < F.KILL_THRESHOLD_USD
    assert led["killed_at_utc"] and led["state_source"] == "recomputed"
    assert len(led["entries"]) == 1 and led["entries"][0]["triggered"] is True
    assert led["entries"][0]["latency_s"] >= 0
    assert led["latency"]["max_s"] is not None
    assert led["threshold_usd"] == F.KILL_THRESHOLD_USD
    cum = json.loads((fwd_dir(root) / "cumulative.json").read_text())
    assert cum["kill"]["triggered"] is True and cum["kill_ledger"]["status"] == "KILLED"
    evs = events_of(root)
    assert len(evs) == 1 and evs[0]["episode_id"] == led["trigger"]["episode_id"]
    assert evs[0]["kill"]["triggered"] is True


@case("kill: 別プロセスで再起動しても KILLED が維持され、追記が重複しない")
def _():
    root = make_store(Path(tempfile.mkdtemp(prefix="fwd5p_")), day0="2026-11-08", n_days=2,
                      at=1000, exit_crash=True)
    cmd = [sys.executable, str(HERE / "fwd_item5_pnl.py"), "--daily-hook", "--data-dir", str(root)]
    r1 = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    assert r1.returncode == 0, r1.stderr[-500:]
    led1 = ledger_of(root)
    assert led1["status"] == "KILLED"
    r2 = subprocess.run(cmd, capture_output=True, text=True, timeout=120)   # 完全な別プロセス
    assert r2.returncode == 0, r2.stderr[-500:]
    led2 = ledger_of(root)
    assert led2["status"] == "KILLED"
    assert len(led2["entries"]) == len(led1["entries"]) == 1
    assert led2["killed_at_utc"] == led1["killed_at_utc"]
    assert len(events_of(root)) == 1
    assert led2["counts"]["evaluated"] == 1 and led2["counts"]["triggered"] == 1


@case("kill: 台帳は単調（データが変わって再計算で未満が出なくても KILLED を取り消さない）")
def _():
    root = make_store(Path(tempfile.mkdtemp(prefix="fwd5m_")), day0="2026-11-08", n_days=2,
                      at=1000, exit_crash=True)
    F.cmd_daily_hook(root, now_ms=now_after(root))
    assert ledger_of(root)["status"] == "KILLED"
    # 合成データを「暴落しない」形に差し替える（= 再計算では閾値未満が出ない状況）
    make_store(root, day0="2026-11-08", n_days=2, at=1000, exit_crash=False)
    F.cmd_daily_hook(root, now_ms=now_after(root))
    led = ledger_of(root)
    assert led["status"] == "KILLED", led
    assert led["state_source"] == "ledger_persisted"
    assert led["recompute_mismatch"] is True
    assert len(led["entries"]) == 1
    assert "単調" in led["recompute_note"]


@case("kill: 閾値未満でなければ ARMED のまま（空振りで KILLED にしない）")
def _():
    root = make_store(Path(tempfile.mkdtemp(prefix="fwd5a_")), day0="2026-11-08", n_days=2, at=1000)
    F.cmd_daily_hook(root, now_ms=now_after(root))
    led = ledger_of(root)
    assert led["status"] == "ARMED" and led["entries"][0]["triggered"] is False
    assert led["killed_at_utc"] is None


# --------------------------------------------------------------------- 既存ファイル非破壊・冪等
@case("write: 既存ファイル（days/*.json）を1バイトも変えない・書くのは fwd_item5/ だけ")
def _():
    root = make_store(Path(tempfile.mkdtemp(prefix="fwd5w_")), day0="2026-11-08", n_days=2, at=1000)
    before = scan_outside_fwd(root)
    F.cmd_daily_hook(root, now_ms=now_after(root))
    assert before == scan_outside_fwd(root)


@case("write: cutover 前の再生 day ファイルは削除も書き換えもしない（sha256 で証明）")
def _():
    root = make_store(Path(tempfile.mkdtemp(prefix="fwd5rp_")), day0="2026-11-08", n_days=2, at=1000)
    out = fwd_dir(root)
    (out / "episodes").mkdir(parents=True, exist_ok=True)
    sentinel = out / "episodes/2026-11-05.json"
    sentinel.write_text('{"sentinel": "replay v1", "n": 1}')
    h_before = hashlib.sha256(sentinel.read_bytes()).hexdigest()
    F.cmd_daily_hook(root, now_ms=now_after(root))
    assert hashlib.sha256(sentinel.read_bytes()).hexdigest() == h_before
    ex = json.loads((out / "exclusions.json").read_text())
    assert ex["replay_day_files"]["2026-11-05"] == h_before
    assert (out / "episodes/2026-11-08.json").exists()


@case("write: 2回実行しても中身が同じ（生成時刻以外は冪等）")
def _():
    root = make_store(Path(tempfile.mkdtemp(prefix="fwd5i_")), day0="2026-11-08", n_days=2, at=1000)
    F.cmd_daily_hook(root, now_ms=now_after(root))
    first = {p.name: json.loads(p.read_text()) for p in sorted(fwd_dir(root).glob("*.json"))}
    first_ev = (fwd_dir(root) / "closed_episodes.jsonl").read_text()
    F.cmd_daily_hook(root, now_ms=now_after(root))
    second = {p.name: json.loads(p.read_text()) for p in sorted(fwd_dir(root).glob("*.json"))}
    second_ev = (fwd_dir(root) / "closed_episodes.jsonl").read_text()
    assert set(first) == set(second)

    def strip(o):
        if isinstance(o, dict):
            return {k: strip(v) for k, v in o.items()
                    if k not in ("generated_at_utc", "updated_at_utc", "recorded_at_utc")}
        if isinstance(o, list):
            return [strip(x) for x in o]
        return o

    assert strip(first) == strip(second)
    assert first_ev == second_ev          # 追記のみ・重複なし


@case("cli: --check-kill は判定JSONを出して exit0")
def _():
    assert F.main(["--check-kill", "-90"]) == 0
    assert F.main(["--check-kill", "-80"]) == 0


def main() -> int:
    bad = 0
    for name, fn in CASES:
        try:
            fn()
            print(f"PASS  {name}")
        except AssertionError as exc:  # noqa: PERF203
            bad += 1
            print(f"FAIL  {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            bad += 1
            print(f"ERROR {name}: {type(exc).__name__}: {exc}")
    print(f"RESULT: {'ALL PASS' if not bad else f'{bad} FAILED'} ({len(CASES)} cases)")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
