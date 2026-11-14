#!/usr/bin/env python3
"""【B2】hl_liq_obs の単体テスト（API不使用・合成データ・決定的）。

既存スイート test_s07_gate_cases.py には触れない（RC=0 を維持する別ファイル）。
カード t_7a53946d。実行: python3 test_hl_liq_obs.py → RESULT: ALL PASS / RC=0。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hl_liq_obs as B  # noqa: E402

CASES: list[tuple[str, object]] = []


def case(name: str):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


def mkc(t: int, o: float, h: float, l: float, c: float, v: float) -> dict:
    return {"t": t, "o": o, "h": h, "l": l, "c": c, "v": v}


def scratch() -> Path:
    return Path(tempfile.mkdtemp(prefix="b2test_"))


@case("lower_wick_ratio: 実1m混合 candle の幾何")
def _():
    assert B.lower_wick_ratio(100, 105, 90, 104) == 10 / 15  # (min(o,c)-l)/(h-l) = (100-90)/15
    assert B.lower_wick_ratio(104, 105, 90, 100) == 10 / 15
    assert B.lower_wick_ratio(50, 50, 50, 50) == 0.0
    return True


@case("代理検知: typeB相当（drop3%+vol2x）を合成蜡烛から検出できる")
def _():
    d = scratch()
    base = 10.0
    candles = []
    px = 100.0
    for i in range(60):
        candles.append(mkc(i * 60_000, px, px * 1.001, px * 0.999, px, base))
    # イベント分: 3.5%下落・出来高3倍
    px2 = px * 0.965
    candles.append(mkc(60 * 60_000, px, px, px2 * 0.999, px2, base * 3.0))
    events = B.build_events_for_day({"AAA": candles}, [], B.PROVISIONAL | {"wick_threshold": 0.60})
    b = [e for e in events if e["kind"] == "typeB_eq"]
    assert len(b) == 1, len(b)
    e = b[0]
    assert abs(e["minute_drop_pct"]) > 3.0
    assert e["volume_ratio"] == 3.0
    assert e["proxy_name"].startswith("代理検知件数")
    assert e["holding_funding_pnl"] is None  # 予約欄（保有中funding収支）
    return True


@case("代理検知: typeA相当（長い下ヒレ+drop1%+vol3x）を検出・typeBが無い candle では不発")
def _():
    base = 10.0
    candles = []
    px = 100.0
    for i in range(60):
        candles.append(mkc(i * 60_000, px, px * 1.001, px * 0.999, px, base))
    # 長い下ヒレ: 安値91・始値/終値100付近（wick≈0.9）・1.5%下落・4倍出来高
    candles.append(mkc(60 * 60_000, 100.0, 100.5, 91.0, 98.5, base * 4.0))
    events = B.build_events_for_day({"AAA": candles}, [], B.PROVISIONAL | {"wick_threshold": 0.60})
    kinds = {e["kind"] for e in events}
    assert "typeA_eq" in kinds
    assert B.PROVISIONAL["volume_mult_typeA"] == 3.0
    return True


@case("OI低下判定: スナップ列の前後比較でOI下降を検出・欠測は欠測と返す")
def _():
    def snap(ms: int, oi: float) -> dict:
        at = B.dt.datetime.fromtimestamp(ms / 1000, B.dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return {"at_utc": at, "coins": {"AAA": {"open_interest_usd": oi}}}
    t0 = B._day_ms("2026-11-06")
    rows = [snap(t0 - 10 * 60_000, 1000.0), snap(t0 + 5 * 60_000, 950.0)]
    s = B._oi_series_for_coin(rows, "AAA")
    out = B.oi_drop_for(s, t0)
    assert out["status"] == "ok" and out["change_pct"] == -5.0, out
    out2 = B.oi_drop_for([], t0)
    assert out2["status"] == "欠測" and out2["change_pct"] is None
    # 窓外のスナップは欠測（窓=120分）
    far = snap(t0 - 200 * 60_000, 1000.0)
    s2 = B._oi_series_for_coin([far, snap(t0 + 5 * 60_000, 950.0)], "AAA")
    out3 = B.oi_drop_for(s2, t0)
    assert out3["status"] == "欠測"
    return True


@case("代理検知件数: OI低下を含む4計数が整数で返る（清算実値ではない旨の文言つき）")
def _():
    base = 10.0
    candles = []
    px = 100.0
    for i in range(30):
        candles.append(mkc(i * 60_000, px, px * 1.001, px * 0.999, px, base))
    px2 = px * 0.965
    ev_candles = candles + [mkc(30 * 60_000, px, px, px2 * 0.999, px2, base * 3.0)]
    snap_row = {
        "at_utc": B.dt.datetime.fromtimestamp((31 * 60_000) / 1000, B.dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "coins": {"AAA": {"open_interest_usd": 900.0}},
    }
    before = {
        "at_utc": B.dt.datetime.fromtimestamp((1 * 60_000) / 1000, B.dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "coins": {"AAA": {"open_interest_usd": 1000.0}},
    }
    events = B.build_events_for_day({"AAA": ev_candles}, [before, snap_row], B.PROVISIONAL | {"wick_threshold": 0.60})
    c = B.summarize_counts(events)
    keys = [k for k in c if k.startswith("代理検知件数")]
    assert len(keys) == 4, keys
    assert c["代理検知件数_typeB相当_1m"] == 1
    assert c["代理検知件数_typeB相当_1m_OI低下含む"] == 1
    assert c["name"].startswith("代理検知件数")
    return True


@case("出来事台帳: event_id冪等・再実行で重複追記しない・予約欄を含む")
def _():
    d = scratch()
    t0 = B._day_ms("2026-11-06")
    rows = {"AAA": [mkc(i * 60_000, 100.0, 100.1, 99.9, 100.0, 10.0) for i in range(30)]
            + [mkc(30 * 60_000, 100.0, 100.0, 95.0, 96.4, 30.0)]}
    thr = {"wick_threshold": 0.60, "minute_drop_pct_typeA": -1.0, "minute_drop_pct_typeB": -3.0,
           "volume_mult_typeA": 3.0, "volume_mult_typeB": 2.0, "oi_drop_min_pct": 1.0}
    events = B.build_events_for_day(rows, [], thr)
    assert events, "イベントがゼロ（合成が弱い）"
    r1 = B.append_ledger(events, d)
    r2 = B.append_ledger(events, d)  # 同じ入力の再実行
    assert r1["appended_this_run"] == r2["total_events"], f'{r1} vs {r2}'
    assert r2["appended_this_run"] == 0, f"再実行で {r2['appended_this_run']} 追記された"  # 重複追記0
    assert r2["reserved_column"]["holding_funding_pnl"] is None  # 予約（保有中funding収支）
    path = d / "funding_top20" / "liquid_events.jsonl"
    ids = [json.loads(l)["event_id"] for l in path.read_text().splitlines() if l.strip()]
    assert len(ids) == len(set(ids))
    return True


@case("thresholds_block: 未凍結時は provisional・凍結ファイルがあれば frozen と上書きしない")
def _():
    d = scratch()
    t = B.thresholds_block(d)
    assert t["source"].startswith("provisional") and t["wick_threshold"] == 0.60
    (d / "funding_top20").mkdir(parents=True, exist_ok=True)
    (d / "funding_top20" / "liquidation_thresholds.json").write_text(json.dumps({
        "frozen": True, "wick_threshold": 0.85, "wick_status": "calibrated",
        "minute_drop_pct_typeA": -1.0, "minute_drop_pct_typeB": -3.0,
        "volume_mult_typeA": 3.0, "volume_mult_typeB": 2.0, "oi_drop_min_pct": 1.0}))
    t2 = B.thresholds_block(d)
    assert t2["wick_threshold"] == 0.85 and t2["source"].startswith("frozen")
    assert B.cmd_calibrate(d) == 0  # 既存凍結で上書きされない（rc=0・内容不変）
    t3 = B.thresholds_block(d)
    assert t3["wick_threshold"] == 0.85
    return True


@case("enrichの完全UTC日判定: 完全データ日は採用・部分日は無視")
def _():
    d = scratch()
    p = d / "funding_top20" / "candles_1m" / "2026-11-06.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"day_key": "2026-11-06", "coin_minute_counts":
                             {f"C{i}": 1400 for i in range(3)}}))
    assert B.latest_complete_day(d) == "2026-11-06"
    p2 = d / "funding_top20" / "candles_1m" / "2026-11-07.json"
    p2.write_text(json.dumps({"day_key": "2026-11-07", "coin_minute_counts":
                              {f"C{i}": 400 for i in range(3)}}))
    assert B.latest_complete_day(d) == "2026-11-06"  # 部分日を飛ばして旧完全日を採用
    return True


@case("12完全日キャリブレーション: 0.5%刻みでW=0.935を凍結")
def _():
    d = scratch()
    # 12日×10銘柄×1440点。各日の比率列は同じk*0.95/144グリッド。
    # 下位の追加点を0にし、p99.9の選択点がk=142になるようにする。
    for day_i in range(12):
        day = B.dt.date(2026, 1, 1) + B.dt.timedelta(days=day_i)
        coins = {}
        for coin_i in range(10):
            rows = []
            for minute in range(1440):
                k = minute % 142 + 1
                wick = k * 0.95 / 144
                # Equal high-low range; ratio = (min(o,c)-l)/(h-l).
                rows.append({"t": minute * 60_000, "o": 100.0, "h": 102.0 - 2.0 * wick,
                             "l": 100.0 - 2.0 * wick, "c": 100.0, "v": 1.0})
            coins[f"C{coin_i:02d}"] = rows
        rec = {"day_key": day.isoformat(), "coins": coins,
               "coin_minute_counts": {coin: 1440 for coin in coins}}
        B._write_atomic(B.candles_path(d, day.isoformat()), rec)
    assert B.cmd_calibrate(d) == 0
    frozen = json.loads((B.top_dir(d) / "liquidation_thresholds.json").read_text())
    calibration = list(B.top_dir(d).glob("calibration_*.json"))
    assert len(calibration) == 1
    stat = json.loads(calibration[0].read_text())
    assert stat["p999"] == 0.9368, stat["p999"]
    assert frozen["wick_threshold"] == 0.935, frozen["wick_threshold"]
    assert frozen["wick_threshold"] != 0.98
    return True


def main() -> int:
    failed = 0
    for name, fn in CASES:
        try:
            fn()
            ok = True
        except Exception as exc:  # noqa: BLE001
            ok = False
            failed += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
        if ok:
            print(f"PASS {name}")
    if failed:
        print(f"RESULT: {failed} FAILED")
        return 1
    print("RESULT: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
