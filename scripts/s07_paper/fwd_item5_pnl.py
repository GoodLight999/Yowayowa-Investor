#!/usr/bin/env python3
"""【項目5】HL影観測の前向き「局面単位損益」記録 v2（凍結仕様のまま・読み取りのみ・実弾0・入金0）

カード t_25c7da31（先行 t_d4fdcfc4 / 測定の出所 t_88d455cb）。
v1 は「閉じたUTC日しか書かない」「censored な trial を外して途中の損益を集計する」
「episode_end_utc が最後の事象時刻で、全約定の出口時刻ではない」という3点で、
前向き合否ゲートの材料としては使えなかった。v2 はその3点だけを直し、
事象定義・局面規則・約定/出口/手数料の凍結仕様は一切変えない
（実装は測定に使った `cascade_sim.py` の関数を無改変のまま呼ぶ。写経しない）。

## 仕様（凍結。ここを変えると「仕様の再調整」になるので変えない）
  事象      : 10分終値変化 <= -2.0% かつ 10分出来高 >= 直近24h同指標中央値の 3.0倍
              同一銘柄 120分以内の再発火は初回のみ（cascade_sim.find_events が実装）
  局面      : 全銘柄を通し、直前の局面開始から 60分を超えて離れた事象は別局面
              （cascade_sim.py:269-275 と同一規則。独立標本の単位）
  受け指値  : 基準価格 = 発火+遅延30分 時点の足終値、指値 = 基準 × (1 - 2.0/100)
  約定/出口 : 待ち30分の各足の安値 < 指値（strict penetration）で約定、約定足の60分後終値で成行
  コスト    : 片道 4.5bps（往復 9.0bps）、1試行 1,000 USD（cascade_sim.simulate_event が実装）
  キル      : 1局面のクラスタ損益 < -85.32 USD で KILLED
              （測定時最悪局面 -42.657 USD の2倍。値はカードで確定済み・再導出しない）

## v2 で足した3点（COO 裁定 2026-11-07 / カード t_25c7da31）
 1) cutover = 2026-11-08T00:00:00Z（固定・変更禁止）
    - 局面開始 >= cutover            -> "forward"（累積・期待値・局面数・キル判定に使う）
    - 局面終了 <= cutover            -> "replay"（記録は残すが一切使わない）
    - 開始 < cutover <= 終了         -> "boundary_excluded"（時刻と件数を台帳に残し、一切使わない）
    除外件数は必ず1行で標準出力に出す（隠さない）。
 2) 局面の確定 = **全 trial が確定**したときだけ。
    - trial が 1 本でも censored（simulate_event が None = 未来足不足）なら status="pending"。
      pending は cluster_pnl_usd=null で、累積・独立局面数・キル判定に一切使わない。
    - 確定時刻 episode_closed_utc = 全 trial の確定時刻の最大
      （約定あり=出口足の終値時刻 / 約定なし=待ち窓の最終足の終値時刻。全 trial が確定して初めて
       その局面の損益が確定する）。episode_end_utc（最後の事象時刻）とは別物として両方残す。
 3) キルは局面損益が確定した時点で即時に台帳へ永続化する。
    - 台帳 kill_ledger.json は単調（KILLED になったら再計算が下回らなくても KILLED を維持）。
    - 再起動・再実行しても KILLED を失わない（起動時に読み、OR で合成する）。
    - 生成器へ渡すイベント列 closed_episodes.jsonl は追記のみ（既出 episode_id は再追記しない）。

## 出力（既存ファイルは書かない・新規ディレクトリだけ）
  <data>/funding_top20/fwd_item5/episodes/YYYY-MM-DD.json  day_key >= cutover の閉じたUTC日のみ
  <data>/funding_top20/fwd_item5/closed_episodes.jsonl     確定局面イベント（追記のみ・生成器の入力）
  <data>/funding_top20/fwd_item5/kill_ledger.json          キル台帳（単調・再起動で維持）
  <data>/funding_top20/fwd_item5/cumulative.json           累積（forward のみ）・日別要約・遅延実測
  <data>/funding_top20/fwd_item5/exclusions.json           除外台帳（replay/boundary/pending の件数と時刻）
既存の `days/YYYY-MM-DD.json`（B1/B2 の日次ファイル）は**読まないし書かない**。
既存の episodes/*.json は day_key < cutover の再生分なので**削除も書き換えもしない**（sha256 を記録する）。

## 呼び出し
  python3 fwd_item5_pnl.py --daily-hook --data-dir /root/current-work/yowayowa-investor/data/s07_paper
  （既存の日次ランナー s07_daily_run.py から best-effort で1回だけ呼ぶ。cron の追加・変更はしない）
  python3 fwd_item5_pnl.py --check-kill -85.33      # キル判定だけを機械確認
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import cascade_sim  # noqa: E402  測定実行のシミュレータ本体（無改変で流用）

CARD = "t_25c7da31"
CARD_V1 = "t_d4fdcfc4"
SCHEMA_VERSION = 2
TOP_DIR_NAME = "funding_top20"
OUT_DIR_NAME = "fwd_item5"
DEFAULT_DATA_DIR = Path("/root/current-work/yowayowa-investor/data/s07_paper")

# 測定実行 cascade_sim.py の同一性（t_88d455cb MANIFEST の実測値）。
CASCADE_SIM_SHA256 = "7370a7e4cdc78a2fd368344bc85a8007652881cd81d364c287eacd87ae4c10df"

# --- 凍結仕様の決定セル。値は cascade_sim のモジュール定数をそのまま使う（二重定義しない） ---
FROZEN_INTERVAL_MIN = 1                      # 前向き収集は1m足のみ（B2 の収集間隔）
FROZEN_DEPTH_PCT = 2.0                       # 決定セル: 深さ2%
FROZEN_DELAY_MIN = 30                        # 決定セル: 遅延30分
FROZEN_REST_MIN = cascade_sim.REST_MIN       # 30（測定時固定）
FROZEN_HOLD_MIN = cascade_sim.HOLD_MIN       # 60（測定時固定）
FROZEN_FEE_BPS_PER_SIDE = cascade_sim.FEE_BPS_PER_SIDE   # 4.5（往復 9.0bps）
FROZEN_NOTIONAL_USD = cascade_sim.NOTIONAL   # 1,000 USD / 試行
EPISODE_GAP_MIN = cascade_sim.EPISODE_GAP_MIN            # 60（独立局面の区切り）
EVENT_DROP_PCT = cascade_sim.EVENT_DROP_PCT              # 2.0
EVENT_VOL_MULT = cascade_sim.EVENT_VOL_MULT              # 3.0
ZERO_VOL_GATE = cascade_sim.MAX_ZERO_VOL_SHARE           # 0.25（銘柄単位の品質ゲート）
MIN_COIN_BARS = 100                                      # cascade_sim.main と同じ下限

# --- キル条件 ---
WORST_EPISODE_AT_MEASUREMENT_USD = -42.66
KILL_THRESHOLD_USD = -85.32
KILL_THRESHOLD_ORIGIN = (
    "測定時最悪局面 -42.657 USD（results_5m_primary.json 深さ2%/遅延30分/30独立局面）の2倍"
    "＝カード t_d4fdcfc4 で確定（-85.32）。再導出・再調整しない"
)

# --- 前方の起点（COO 裁定 2026-11-07・固定。変更禁止） ---
CUTOVER_UTC = "2026-11-08T00:00:00Z"
CUTOVER_DAY = "2026-11-08"

INDEPENDENT_TARGET = 20   # 20独立局面到達で前倒し判定できる（判定は下流の判定ページ）


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")


def _iso_ms(iso: str) -> int:
    return int(datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp() * 1000)


_CUTOVER_MS = _iso_ms(CUTOVER_UTC)


def _write_atomic(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1))
    os.replace(tmp, path)


def _read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def cascade_sim_identity() -> dict:
    """流用した cascade_sim.py が測定実行と同一バイトかどうか（同一でなくても記録は続ける）。"""
    p = Path(cascade_sim.__file__)
    digest = hashlib.sha256(p.read_bytes()).hexdigest()
    return {"path": str(p), "sha256": digest, "expected_sha256": CASCADE_SIM_SHA256,
            "identical_to_measurement": digest == CASCADE_SIM_SHA256}


# --------------------------------------------------------------------------- 損益・キル
def evaluate_kill(cluster_pnl_usd: float) -> dict:
    """1局面のクラスタ損益がキル閾値を超えて悪化したかを機械判定する（唯一の判定点）。"""
    pnl = float(cluster_pnl_usd)
    killed = pnl < KILL_THRESHOLD_USD          # 「-85.32 を超えたら」＝ -85.32 より悪い
    return {"threshold_usd": KILL_THRESHOLD_USD, "cluster_pnl_usd": round(pnl, 4),
            "triggered": killed,
            "reason": (f"クラスタ損益 {round(pnl, 4)} < {KILL_THRESHOLD_USD}"
                       if killed else f"クラスタ損益 {round(pnl, 4)} >= {KILL_THRESHOLD_USD}")}


def _unevaluated_kill(reason: str) -> dict:
    """確定していない局面のキル欄（未確定を確定扱いしないための明示）。"""
    return {"threshold_usd": KILL_THRESHOLD_USD, "cluster_pnl_usd": None,
            "triggered": None, "evaluated": False, "reason": reason}


# --------------------------------------------------------------------------- 入力
def load_candle_days(top: Path) -> dict[str, dict]:
    """前向き収集の 1m 足ファイル（読み取りのみ）。{day_key: {"coins": {coin: rows}, ...}}"""
    out: dict[str, dict] = {}
    for p in sorted((top / "candles_1m").glob("*.json")):
        try:
            d = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        coins = d.get("coins")
        if not isinstance(coins, dict):
            continue
        day = d.get("day_key") or p.stem
        out[day] = {
            "coins": coins,
            "updated_at_utc": d.get("updated_at_utc"),
            "day_completeness": d.get("day_completeness") or {},
            "file_mtime_utc": _utc(int(p.stat().st_mtime * 1000)) if p.exists() else None,
        }
    return out


def merge_coins(day_files: dict[str, dict]) -> tuple[dict[str, list], dict]:
    """銘柄ごとに日次ファイルを連結（t で重複排除・昇順）。測定時の merged ストアと同じ形にする。"""
    rows_by_coin: dict[str, list] = {}
    for day in sorted(day_files):
        for coin, rows in day_files[day]["coins"].items():
            if not isinstance(rows, list):
                continue
            rows_by_coin.setdefault(coin, []).extend(rows)
    meta = {}
    for coin, rows in list(rows_by_coin.items()):
        by_t = {r["t"]: r for r in rows if isinstance(r, dict) and "t" in r}
        rows_by_coin[coin] = [by_t[t] for t in sorted(by_t)]
        if not rows_by_coin[coin]:
            del rows_by_coin[coin]
            continue
        ts = [r["t"] for r in rows_by_coin[coin]]
        gaps = sum(1 for a, b in zip(ts, ts[1:]) if b - a != FROZEN_INTERVAL_MIN * 60_000)
        meta[coin] = {"rows": len(ts), "gaps": gaps,
                      "first_utc": _utc(ts[0]), "last_utc": _utc(ts[-1])}
    return rows_by_coin, meta


def split_segments(rows: list) -> list[list]:
    """1分足が連続している区間へ分割する（欠測を跨いだ bar_index 計算をしないため）。"""
    step = FROZEN_INTERVAL_MIN * 60_000
    segs: list[list] = []
    cur = [rows[0]]
    for prev, row in zip(rows, rows[1:]):
        if row["t"] - prev["t"] == step:
            cur.append(row)
        else:
            segs.append(cur)
            cur = [row]
    segs.append(cur)
    return segs


# --------------------------------------------------------------------------- 局面の数え方
def assign_episodes(events: list[dict]) -> int:
    """cascade_sim.py:269-275 と同一規則（直前の局面開始から EPISODE_GAP_MIN 超で新規局面）。

    測定実行では main() 内にインラインで書かれていたため関数として切り出せない。
    式はそのまま写し、原文の行番号をコメントに残す（判定規則は変えない）。
    """
    ep, ep_start = -1, None
    for e in events:
        if ep_start is None or (e["t_close_ms"] - ep_start) > EPISODE_GAP_MIN * 60_000:
            ep += 1
            ep_start = e["t_close_ms"]
        e["episode"] = ep
    return ep + 1


# --------------------------------------------------------------------------- cutover 判定
def classify_episode(start_ms: int, end_ms: int) -> str:
    """局面の前方/再生/境界の分類（COO 裁定 2026-11-07 で固定・変更しない）。

    start >= cutover                     -> "forward"          累積・期待値・局面数・キルに使う
    end   <= cutover                     -> "replay"           記録だけ残す（一切使わない）
    start <  cutover <= end              -> "boundary_excluded" 時刻を台帳に残し一切使わない
    """
    if start_ms >= _CUTOVER_MS:
        return "forward"
    if end_ms <= _CUTOVER_MS:
        return "replay"
    return "boundary_excluded"


# --------------------------------------------------------------------------- trial の確定時刻
def trial_determination(a: dict, ev: dict, tr: dict) -> dict:
    """1 trial の損益が確定する時刻を、simulate_event の戻り値から算術で求める。

    simulate_event 自体は無改変（写経しない）。戻り値の filled / fill_min_after_place と
    凍結定数から足indexを逆算するだけ。
      約定あり: 出口足 = 約定足 + HOLD_MIN。その足の終値で決済されるので確定時刻はその足の終値時刻。
      約定なし: 待ち窓の最終足を過ぎたら約定しないことが確定する（確定時刻はその足の終値時刻）。
    """
    ib = FROZEN_DELAY_MIN // FROZEN_INTERVAL_MIN
    wbars = FROZEN_REST_MIN // FROZEN_INTERVAL_MIN
    hbars = FROZEN_HOLD_MIN // FROZEN_INTERVAL_MIN
    ip = ev["bar_index"] + ib
    if tr.get("filled"):
        jx = ip + int(tr["fill_min_after_place"] or 0) // FROZEN_INTERVAL_MIN + hbars
        rule = "exit_bar_close"
    else:
        jx = min(len(a["t"]) - 1, ip + wbars)
        rule = "rest_window_end"
    return {"determined_ms": a["t"][jx] + FROZEN_INTERVAL_MIN * 60_000,
            "determined_rule": rule, "bar_index": jx}


# --------------------------------------------------------------------------- 本体
def build(data_dir: Path, *, top_dir_name: str = TOP_DIR_NAME, now_ms: int | None = None) -> dict:
    """前向き収集の全履歴を決定論的に再計算し、確定局面・除外台帳・累積を返す（書き込みなし）。"""
    top = Path(data_dir) / top_dir_name
    day_files = load_candle_days(top)
    if not day_files:
        raise SystemExit(f"candles_1m に読めるファイルが無い: {top / 'candles_1m'}")

    rows_by_coin, coin_meta = merge_coins(day_files)
    now = int(now_ms) if now_ms is not None else _now_ms()
    today = _utc_day(now)

    excluded_coins: dict[str, dict] = {}
    seg_rows: dict[str, list[list]] = {}
    arrays: dict[str, list[dict]] = {}
    for coin, rows in rows_by_coin.items():
        zs = cascade_sim.zero_vol_share(rows)
        if len(rows) < MIN_COIN_BARS or zs > ZERO_VOL_GATE:
            excluded_coins[coin] = {"zero_vol_share": round(zs, 4), "n": len(rows)}
            continue
        segs = split_segments(rows)
        seg_rows[coin] = segs
        # find_events / simulate_event は bar 配列前提なので、区間ごとに同じ関数へ渡す
        arrays[coin] = [cascade_sim.series_to_arrays(s) for s in segs]

    events: list[dict] = []
    for coin, segs in seg_rows.items():
        for si, seg in enumerate(segs):
            evs = cascade_sim.find_events(coin, seg, FROZEN_INTERVAL_MIN,
                                          EVENT_DROP_PCT, EVENT_VOL_MULT)
            for ev in evs:
                ev["_segment"] = si
                events.append(ev)
    events.sort(key=lambda e: (e["t_close_ms"], e["coin"]))
    n_episodes = assign_episodes(events)

    ep_start_ms: dict[int, int] = {}
    for ev in events:
        ep_start_ms.setdefault(ev["episode"], ev["t_close_ms"])

    def _episode_id(ms: int) -> str:
        return "ep-" + _utc(ms).replace(":", "")

    trials: list[dict] = []
    censored: list[dict] = []
    for ev in events:
        a = arrays[ev["coin"]][ev["_segment"]]
        tr = cascade_sim.simulate_event(a, ev, FROZEN_INTERVAL_MIN, FROZEN_DEPTH_PCT,
                                        FROZEN_DELAY_MIN, FROZEN_REST_MIN, FROZEN_HOLD_MIN)
        if tr is None:
            censored.append({"coin": ev["coin"], "t_close_ms": ev["t_close_ms"],
                             "episode": ev["episode"],
                             "episode_id": _episode_id(ep_start_ms[ev["episode"]]),
                             "day_key": _utc_day(ep_start_ms[ev["episode"]]),
                             "reason": "future_bars_unavailable"})
            continue
        det = trial_determination(a, ev, tr)
        tr["episode"] = ev["episode"]
        tr["event_id"] = f'{ev["coin"]}@{ev["t_close_ms"]}'
        tr["determined_ms"] = det["determined_ms"]
        tr["determined_rule"] = det["determined_rule"]
        tr["drop_pct"] = ev["drop_pct"]
        tr["vol_ratio"] = ev["vol_ratio"]
        trials.append(tr)

    # --- 局面へ集計（測定時の cells と同じ「局面=クラスタ単位の損益」） ---
    by_ep: dict[int, dict] = {}
    for ev in events:
        e = by_ep.setdefault(ev["episode"], {
            "episode": ev["episode"], "t_first_ms": ev["t_close_ms"], "t_last_ms": ev["t_close_ms"],
            "coins": [], "n_events": 0,
        })
        e["n_events"] += 1
        e["t_last_ms"] = max(e["t_last_ms"], ev["t_close_ms"])
        if ev["coin"] not in e["coins"]:
            e["coins"].append(ev["coin"])
    for tr in trials:
        e = by_ep[tr["episode"]]
        e.setdefault("n_trials", 0)
        e["n_trials"] += 1
        if tr["filled"]:
            e.setdefault("fills", []).append(tr)
    censored_by_ep: dict[int, list[dict]] = {}
    for c in censored:
        censored_by_ep.setdefault(c["episode"], []).append(c)

    episodes: list[dict] = []
    for ep in sorted(by_ep.values(), key=lambda x: x["t_first_ms"]):
        fills = ep.get("fills", [])
        n_events = ep["n_events"]
        n_trials = ep.get("n_trials", 0)
        un = censored_by_ep.get(ep["episode"], [])
        start_ms = ep["t_first_ms"]
        last_event_ms = ep["t_last_ms"]
        ep_trials = [t for t in trials if t["episode"] == ep["episode"]]
        # --- 確定判定: 全 trial が確定しているときだけ確定。1本でも欠測なら pending（確定扱いしない） ---
        if un:
            status = "pending"
            cluster_pnl = None
            closed_ms = None
            kill = _unevaluated_kill(
                f"未確定: {len(un)} trial が censored（未来足不足/欠測）のため確定扱いしない")
        else:
            status = "final"
            cluster_pnl = round(sum(t["pnl_usd"] for t in fills), 4)
            closed_ms = max(t["determined_ms"] for t in ep_trials) if ep_trials else last_event_ms
            kill = evaluate_kill(cluster_pnl)
            kill["evaluated"] = True
        cls_end_ms = closed_ms if closed_ms is not None else last_event_ms
        classification = classify_episode(start_ms, cls_end_ms)
        forward_final = (classification == "forward" and status == "final")
        rec = {
            "episode_id": _episode_id(start_ms),
            "episode_start_utc": _utc(start_ms),
            # 最後の事象時刻（v1 の episode_end_utc の意味をそのまま残す。出口時刻ではない）
            "episode_end_utc": _utc(last_event_ms),
            # 全 trial が確定した時刻 = この局面の損益が確定した時刻（v2 で追加）
            "episode_closed_utc": _utc(closed_ms) if closed_ms is not None else None,
            "episode_closed_ms": closed_ms,
            "day_key": _utc_day(start_ms),
            "status": status,
            "classification": classification,
            "classification_provisional": status != "final",
            "counts_for_forward": forward_final,
            "excluded_reason": (None if forward_final else
                                ("replay_pre_cutover" if classification == "replay" else
                                 "boundary_excluded" if classification == "boundary_excluded" else
                                 "pending_all_trials_not_confirmed")),
            "independent": bool(fills) and status == "final",
            "n_events": n_events,
            "n_trials": n_trials,
            "n_unresolved": len(un),
            "n_filled": len(fills),
            "coins": ep["coins"],
            "cluster_pnl_usd": cluster_pnl,
            "mean_pnl_per_fill_usd": (round(cluster_pnl / len(fills), 4)
                                      if (fills and cluster_pnl is not None) else None),
            "worst_fill_usd": round(min(t["pnl_usd"] for t in fills), 4) if fills else None,
            "kill": kill,
            "fills": [
                {"coin": t["coin"], "t_close_utc": _utc(t["t_close_ms"]), "limit": t["limit"],
                 "exit_px": t.get("exit_px"), "ret_net_pct": t["ret_net_pct"],
                 "pnl_usd": t["pnl_usd"], "fill_min_after_place": t["fill_min_after_place"],
                 "determined_utc": _utc(t["determined_ms"]), "determined_rule": t["determined_rule"],
                 "drop_pct": t["drop_pct"], "vol_ratio": t["vol_ratio"]}
                for t in sorted(fills, key=lambda x: x["t_close_ms"])
            ],
            "unresolved": [{"coin": c["coin"], "t_close_utc": _utc(c["t_close_ms"]),
                            "reason": c["reason"]} for c in sorted(un, key=lambda x: x["t_close_ms"])],
        }
        episodes.append(rec)

    # --- 除外台帳（件数と時刻。隠さない） ---
    replay = [e for e in episodes if e["classification"] == "replay"]
    boundary = [e for e in episodes if e["classification"] == "boundary_excluded"]
    pending = [e for e in episodes if e["status"] == "pending"]
    forward_final = [e for e in episodes if e["counts_for_forward"]]

    # --- 日次へ割り当て（局面の開始UTC日=その日の新局面。規則はスキーマに明記） ---
    # 書いてよいのは cutover 以降の閉じたUTC日だけ（それ以前は再生分なのでファイルを触らない）。
    days = sorted(d for d in day_files if d < today and d >= CUTOVER_DAY)
    per_day: dict[str, list[dict]] = {d: [] for d in days}
    for rec in episodes:
        if rec["day_key"] in per_day:
            per_day[rec["day_key"]].append(rec)

    cumulative_independent = 0
    reach_day = None
    kill_state = {"threshold_usd": KILL_THRESHOLD_USD, "origin": KILL_THRESHOLD_ORIGIN,
                  "triggered": False, "episode_id": None, "day_key": None,
                  "cluster_pnl_usd": None, "worst_episode_usd": None,
                  "episode_closed_utc": None}
    day_records = []
    for day in days:
        eps = per_day[day]
        indep = [e for e in eps if e["independent"]]
        cumulative_independent += len(indep)
        if reach_day is None and cumulative_independent >= INDEPENDENT_TARGET:
            reach_day = day
        final_eps = [e for e in eps if e["status"] == "final"]
        day_pnl = round(sum(e["cluster_pnl_usd"] for e in final_eps), 4)
        worst = min((e["cluster_pnl_usd"] for e in final_eps), default=None)
        for e in eps:
            if e["kill"]["triggered"] and not kill_state["triggered"]:
                kill_state.update({"triggered": True, "episode_id": e["episode_id"],
                                   "day_key": day, "cluster_pnl_usd": e["cluster_pnl_usd"],
                                   "episode_closed_utc": e["episode_closed_utc"]})
            if e["cluster_pnl_usd"] is None:
                continue
            if kill_state["worst_episode_usd"] is None or e["cluster_pnl_usd"] < kill_state["worst_episode_usd"]:
                kill_state["worst_episode_usd"] = e["cluster_pnl_usd"]
        dc = (day_files.get(day) or {}).get("day_completeness") or {}
        counts = {c: len(r) for c, r in (day_files.get(day) or {}).get("coins", {}).items()}
        day_records.append({
            "schema_version": SCHEMA_VERSION,
            "card": CARD,
            "day_key": day,
            "day_kind": "closed_utc_day",
            "lane": "forward",
            "cutover_utc": CUTOVER_UTC,
            "generated_at_utc": _now_utc(),
            "spec": {
                "interval_min": FROZEN_INTERVAL_MIN,
                "depth_pct": FROZEN_DEPTH_PCT,
                "delay_min": FROZEN_DELAY_MIN,
                "rest_min": FROZEN_REST_MIN,
                "hold_min": FROZEN_HOLD_MIN,
                "event_drop_pct": EVENT_DROP_PCT,
                "event_vol_mult": EVENT_VOL_MULT,
                "event_cluster_min": cascade_sim.EVENT_CLUSTER_MIN,
                "episode_gap_min": EPISODE_GAP_MIN,
                "fee_bps_per_side": FROZEN_FEE_BPS_PER_SIDE,
                "round_trip_bps": round(2 * FROZEN_FEE_BPS_PER_SIDE, 4),
                "notional_usd": FROZEN_NOTIONAL_USD,
                "fill_rule": "bar low < limit (strict penetration)",
                "exit_rule": "close of the bar HOLD_MIN after the fill bar",
                "zero_vol_gate": ZERO_VOL_GATE,
                "episode_day_rule": "episode_start_utc の UTC 日",
                "episode_finality_rule": "全 trial が確定したときだけ確定（censored を除外した部分集計をしない）",
                "episode_closed_rule": "全 trial の確定時刻の最大（約定あり=出口足終値時刻/約定なし=待ち窓最終足終値時刻）",
                "cutover_rule": "開始>=cutover は forward / 終了<=cutover は replay / 開始<cutover<=終了 は boundary_excluded",
                "reused_from": "cascade_sim.py（t_88d455cb の測定実行と同一バイト）の "
                               "find_events / simulate_event / series_to_arrays / zero_vol_share",
            },
            "cascade_sim": cascade_sim_identity(),
            "kill_threshold_usd": KILL_THRESHOLD_USD,
            "kill_threshold_origin": KILL_THRESHOLD_ORIGIN,
            "cutover_utc": CUTOVER_UTC,
            "data_quality": {
                "candle_days": days,
                "coin_universe": len(arrays) + len(excluded_coins),
                "coins_used": len(arrays),
                "excluded_coins": sorted(excluded_coins),
                "excluded_coin_details": "cumulative.json の data_quality に記載（日次ファイルでは名前のみ）",
                "day_min_coin_minutes": dc.get("min_coin_minutes"),
                "day_coins": len(counts),
                "day_minutes_min_max": [min(counts.values()), max(counts.values())] if counts else None,
            },
            "counts": {
                "n_events": sum(e["n_events"] for e in eps),
                "n_trials": sum(e["n_trials"] for e in eps),
                "n_censored": sum(1 for c in censored if c["day_key"] == day),
                "n_episodes": len(eps),
                "n_independent_episodes": len(indep),
                "n_final_episodes": len(final_eps),
                "n_pending_episodes": len(eps) - len(final_eps),
            },
            "pnl": {
                "day_cluster_pnl_usd": day_pnl,
                "worst_episode_pnl_usd": worst,
                "best_episode_pnl_usd": max((e["cluster_pnl_usd"] for e in final_eps), default=None),
                "mean_episode_pnl_usd": round(day_pnl / len(final_eps), 4) if final_eps else None,
                "basis": "final_episodes_only",
            },
            "cumulative": {
                "independent_episodes": cumulative_independent,
                "target": INDEPENDENT_TARGET,
                "reached_20": cumulative_independent >= INDEPENDENT_TARGET,
                "reach_day": reach_day,
            },
            "kill": dict(kill_state),
            "episodes": eps,
        })

    # --- キル台帳の材料（forward かつ確定した局面だけ。即時永続は update_kill_ledger が行う） ---
    closed_events = []
    for e in forward_final:
        closed_events.append({
            "schema_version": SCHEMA_VERSION,
            "type": "episode_closed",
            "card": CARD,
            "episode_id": e["episode_id"],
            "day_key": e["day_key"],
            "episode_start_utc": e["episode_start_utc"],
            "episode_end_utc": e["episode_end_utc"],
            "episode_closed_utc": e["episode_closed_utc"],
            "episode_closed_ms": e["episode_closed_ms"],
            "pnl_basis": "all_trials_resolved",
            "n_events": e["n_events"],
            "n_trials": e["n_trials"],
            "n_unresolved": 0,
            "n_filled": e["n_filled"],
            "independent": e["independent"],
            "coins": e["coins"],
            "cluster_pnl_usd": e["cluster_pnl_usd"],
            "kill": e["kill"],
            "trials": [{"coin": f["coin"], "t_close_utc": f["t_close_utc"],
                        "determined_utc": f["determined_utc"],
                        "determined_rule": f["determined_rule"],
                        "filled": True, "pnl_usd": f["pnl_usd"]} for f in e["fills"]],
        })

    return {
        "episodes": episodes,
        "day_records": day_records,
        "closed_events": closed_events,
        "exclusions": {
            "schema_version": SCHEMA_VERSION,
            "card": CARD,
            "generated_at_utc": _now_utc(),
            "cutover_utc": CUTOVER_UTC,
            "rule": ("開始>=cutover=forward（使用）/ 終了<=cutover=replay（記録のみ）/ "
                     "開始<cutover<=終了=boundary_excluded（記録のみ）"),
            "counts": {
                "episodes_total": len(episodes),
                "forward_final": len(forward_final),
                "replay": len(replay),
                "boundary_excluded": len(boundary),
                "pending": len(pending),
                "censored_trials": len(censored),
            },
            "one_line": (f"excluded: replay={len(replay)} boundary_excluded={len(boundary)} "
                         f"pending={len(pending)} (cutover={CUTOVER_UTC})"),
            "boundary_excluded_episodes": [
                {"episode_id": e["episode_id"], "episode_start_utc": e["episode_start_utc"],
                 "episode_end_utc": e["episode_end_utc"], "episode_closed_utc": e["episode_closed_utc"],
                 "cluster_pnl_usd": e["cluster_pnl_usd"],
                 "note": "境界跨ぎのため期待値・区間・局面数・キル判定に使わない"} for e in boundary
            ],
            "pending_episodes": [
                {"episode_id": e["episode_id"], "episode_start_utc": e["episode_start_utc"],
                 "n_unresolved": e["n_unresolved"],
                 "unresolved": e["unresolved"],
                 "note": "全trial未確定のため確定扱いしない"} for e in pending
            ],
            "replay_episodes": [
                {"episode_id": e["episode_id"], "episode_start_utc": e["episode_start_utc"],
                 "episode_end_utc": e["episode_end_utc"],
                 "cluster_pnl_usd": e["cluster_pnl_usd"], "note": "cutover前の再生分"} for e in replay
            ],
            "replay_day_files": {},   # write_outputs が sha256 を埋める（書き換えていないことの証跡）
        },
        "cumulative": {
            "schema_version": SCHEMA_VERSION,
            "card": CARD,
            "generated_at_utc": _now_utc(),
            "lane": "forward",
            "cutover_utc": CUTOVER_UTC,
            "kill_threshold_usd": KILL_THRESHOLD_USD,
            "kill_threshold_origin": KILL_THRESHOLD_ORIGIN,
            "spec": day_records[-1]["spec"] if day_records else None,
            "cascade_sim": cascade_sim_identity(),
            "data_quality": {
                "coin_universe": len(arrays) + len(excluded_coins),
                "coins_used": len(arrays),
                "excluded_coins": excluded_coins,
                "coin_rows": coin_meta,
                "coin_segments": {c: len(s) for c, s in seg_rows.items()},
                "candle_days": {d: (day_files[d].get("day_completeness") or {}) for d in sorted(day_files)},
                "coverage_note": ("前向き収集の取得宇宙は『当日の上位20 ∪ 前日の上位20』（B2 の仕様）で、"
                                  "測定時の固定宇宙（46〜49銘柄）と同一ではない。事象数が測定標本と一致しないのは"
                                  "この取得範囲の差であり、事象定義・局面規則・約定/出口/手数料は測定時と同一。"),
            },
            "days": [{"day_key": d["day_key"],
                      "lane": d["lane"],
                      "n_events": d["counts"]["n_events"],
                      "n_episodes": d["counts"]["n_episodes"],
                      "n_independent_episodes": d["counts"]["n_independent_episodes"],
                      "n_pending_episodes": d["counts"]["n_pending_episodes"],
                      "day_cluster_pnl_usd": d["pnl"]["day_cluster_pnl_usd"],
                      "worst_episode_pnl_usd": d["pnl"]["worst_episode_pnl_usd"],
                      "cumulative_independent_episodes": d["cumulative"]["independent_episodes"],
                      "kill_triggered": d["kill"]["triggered"]} for d in day_records],
            "exclusions": {
                "cutover_utc": CUTOVER_UTC,
                "counts": {
                    "forward_final": len(forward_final),
                    "replay": len(replay),
                    "boundary_excluded": len(boundary),
                    "pending": len(pending),
                },
                "one_line": (f"excluded: replay={len(replay)} "
                             f"boundary_excluded={len(boundary)} pending={len(pending)} "
                             f"(cutover={CUTOVER_UTC})"),
            },
            # 「確定台帳」の累積は forward のみ。v1 の同名キーは前方互換のため残す（意味は forward のみ）。
            "cumulative_independent_episodes": (day_records[-1]["cumulative"]["independent_episodes"]
                                                if day_records else 0),
            "replay_independent_episodes": len([e for e in replay if e["independent"]]),
            "forward_final_episodes": len(forward_final),
            "forward_independent_episodes": len([e for e in forward_final if e["independent"]]),
            "target": INDEPENDENT_TARGET,
            "reached_20": (day_records[-1]["cumulative"]["reached_20"] if day_records else False),
            "reach_day": (day_records[-1]["cumulative"]["reach_day"] if day_records else None),
            "worst_forward_episode_usd": min((e["cluster_pnl_usd"] for e in forward_final), default=None),
            "kill": (day_records[-1]["kill"] if day_records
                     else {"threshold_usd": KILL_THRESHOLD_USD, "triggered": False,
                           "worst_episode_usd": None, "episode_id": None,
                           "episode_closed_utc": None}),
        },
        "n_events_total": len(events),
        "n_episodes_total": n_episodes,
        "n_trials_total": len(trials),
        "n_censored_total": len(censored),
        "n_forward_final": len(forward_final),
        "n_replay": len(replay),
        "n_boundary_excluded": len(boundary),
        "n_pending": len(pending),
        "censored": censored,
        # 監査用（ファイルには書かない）: 事象と局面の対応
        "events_diag": [{"coin": ev["coin"], "t_close_ms": ev["t_close_ms"],
                         "t_close_utc": _utc(ev["t_close_ms"]), "drop_pct": ev["drop_pct"],
                         "vol_ratio": ev["vol_ratio"], "episode_id": _episode_id(ep_start_ms[ev["episode"]])}
                        for ev in events],
    }


# --------------------------------------------------------------------------- キル台帳・確定イベント
def update_kill_ledger(out: Path, built: dict, *, now_ms: int | None = None) -> dict:
    """確定局面のイベントを追記し、キル台帳を単調に更新して即時永続する。

    - closed_episodes.jsonl は追記のみ（既出 episode_id は再追記しない）。
    - kill_ledger.json は「読んだ KILLED」と「今回の再計算」の OR。KILLED を消さない。
    """
    now = int(now_ms) if now_ms is not None else _now_ms()
    recorded_at = _utc(now)
    path = Path(out) / "kill_ledger.json"
    prev = _read_json(path) or {}
    prev_entries = prev.get("entries") or []
    prev_status = prev.get("status") or "ARMED"
    prev_killed_at = prev.get("killed_at_utc")
    prev_trigger = prev.get("trigger")
    known = {e.get("episode_id") for e in prev_entries}

    new_entries = []
    for ev in built["closed_events"]:
        if ev["episode_id"] in known:
            continue
        determined_ms = ev["episode_closed_ms"]
        new_entries.append({
            "episode_id": ev["episode_id"],
            "day_key": ev["day_key"],
            "determined_utc": ev["episode_closed_utc"],
            "recorded_at_utc": recorded_at,
            # 確定時刻 -> 台帳に書いた時刻（既存経路の遅延を毎回実測して残す）
            "latency_s": max(0, round((now - determined_ms) / 1000, 1)),
            "cluster_pnl_usd": ev["cluster_pnl_usd"],
            "n_filled": ev["n_filled"],
            "independent": ev["independent"],
            "threshold_usd": KILL_THRESHOLD_USD,
            "triggered": bool(ev["kill"]["triggered"]),
        })
        known.add(ev["episode_id"])

    entries = prev_entries + new_entries
    triggered_entries = [e for e in entries if e["triggered"]]
    # 「今回の再計算で閾値を割った局面があるか」は保存済みエントリではなく今回の確定イベントで判定する
    recomputed_killed = any(bool(ev["kill"]["triggered"]) for ev in built["closed_events"])
    status = "KILLED" if (prev_status == "KILLED" or recomputed_killed) else "ARMED"

    if prev_status == "KILLED":
        killed_at = prev_killed_at
        trigger = prev_trigger
    else:
        killed_at = triggered_entries[0]["recorded_at_utc"] if recomputed_killed else None
        trigger = ({k: triggered_entries[0][k] for k in
                    ("episode_id", "day_key", "determined_utc", "cluster_pnl_usd", "n_filled")}
                   if recomputed_killed else None)

    recompute_mismatch = (prev_status == "KILLED" and not recomputed_killed)
    ledger = {
        "schema_version": SCHEMA_VERSION,
        "card": CARD,
        "threshold_usd": KILL_THRESHOLD_USD,
        "threshold_origin": KILL_THRESHOLD_ORIGIN,
        "cutover_utc": CUTOVER_UTC,
        "status": status,
        "killed_at_utc": killed_at,
        "trigger": trigger,
        "state_source": ("ledger_persisted" if (prev_status == "KILLED" and not recomputed_killed)
                         else "recomputed"),
        "recompute_mismatch": recompute_mismatch,
        "recompute_note": ("保存済み KILLED を維持した（再計算では閾値未満の局面が見つからない）。"
                           "台帳は単調で、確定済みの KILLED を取り消さない。" if recompute_mismatch else ""),
        "entries": entries,
        "counts": {"evaluated": len(entries), "triggered": len(triggered_entries)},
        "latency": {
            "definition": "局面損益の確定時刻(episode_closed_utc) → 台帳に書いた時刻(recorded_at_utc)",
            "max_s": max((e["latency_s"] for e in entries), default=None),
            "path": "既存の日次フック（s07_daily_run.py:203-214 → fwd_item5_pnl.py --daily-hook）",
        },
        "updated_at_utc": recorded_at,
    }
    _write_atomic(path, ledger)

    # --- 確定イベントは追記のみ（既出 episode_id は書かない） ---
    ev_path = Path(out) / "closed_episodes.jsonl"
    ev_path.parent.mkdir(parents=True, exist_ok=True)
    existing_ids: set[str] = set()
    if ev_path.exists():
        for line in ev_path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                existing_ids.add(json.loads(line).get("episode_id"))
            except json.JSONDecodeError:
                continue
    seq = len(existing_ids)
    appended = 0
    with ev_path.open("a", encoding="utf-8") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            pass
        try:
            for ev in built["closed_events"]:
                if ev["episode_id"] in existing_ids:
                    continue
                seq += 1
                row = dict(ev)
                row["seq"] = seq
                row["recorded_at_utc"] = recorded_at
                row["kill_ledger_status"] = status
                fh.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
                appended += 1
            fh.flush()
            os.fsync(fh.fileno())
        finally:
            try:
                fcntl.flock(fh, fcntl.LOCK_UN)
            except OSError:
                pass
    return {"ledger": ledger, "events_appended": appended,
            "excluded_line": built["cumulative"]["exclusions"]["one_line"]}


# --------------------------------------------------------------------------- 出力
def write_outputs(data_dir: Path, built: dict, *, top_dir_name=TOP_DIR_NAME,
                  out_dir_name=OUT_DIR_NAME) -> list[Path]:
    """cutover 以降の閉じたUTC日の day ファイル＋累積＋除外台帳を書く（再生分ファイルは触らない）。"""
    out = Path(data_dir) / top_dir_name / out_dir_name
    written = []
    for rec in built["day_records"]:
        if rec["day_key"] < CUTOVER_DAY:      # 念のため二重の歯止め（再生分は書き換えない）
            continue
        p = out / "episodes" / f'{rec["day_key"]}.json'
        _write_atomic(p, rec)
        written.append(p)
    # 再生分ファイルの sha256 を「触っていない」証跡として残す
    replay_files = {}
    for p in sorted((out / "episodes").glob("*.json")):
        if p.stem < CUTOVER_DAY:
            replay_files[p.stem] = _sha256(p)
    built["exclusions"]["replay_day_files"] = replay_files
    built["exclusions"]["replay_day_files_note"] = ("cutover 前の再生分 day ファイルは削除も書き換えもしない"
                                                    "（sha256 を記録。書き込み対象は day_key>=cutover のみ）")
    p = out / "exclusions.json"
    _write_atomic(p, built["exclusions"])
    written.append(p)
    p = out / "cumulative.json"
    _write_atomic(p, built["cumulative"])
    written.append(p)
    return written


def cmd_daily_hook(data_dir: Path, *, top_dir_name=TOP_DIR_NAME, out_dir_name=OUT_DIR_NAME,
                   now_ms: int | None = None) -> int:
    built = build(data_dir, top_dir_name=top_dir_name, now_ms=now_ms)
    out = Path(data_dir) / top_dir_name / out_dir_name
    # 1) キルは確定時点で即時に永続（day ファイルや累積より先に書く）
    led = update_kill_ledger(out, built, now_ms=now_ms)
    ledger = led["ledger"]
    # 2) 累積の kill 欄は台帳を正とする（単調・再起動で維持）
    cum = built["cumulative"]
    cum["kill_ledger"] = {"path": str(out / "kill_ledger.json"),
                          "status": ledger["status"], "state_source": ledger["state_source"],
                          "killed_at_utc": ledger["killed_at_utc"],
                          "trigger": ledger["trigger"],
                          "recompute_mismatch": ledger["recompute_mismatch"]}
    if ledger["status"] == "KILLED":
        cum["kill"] = {**cum["kill"], "triggered": True,
                       "episode_id": (ledger["trigger"] or {}).get("episode_id"),
                       "cluster_pnl_usd": (ledger["trigger"] or {}).get("cluster_pnl_usd"),
                       "episode_closed_utc": (ledger["trigger"] or {}).get("determined_utc"),
                       "source": "kill_ledger"}
    # 3) day ファイル・累積・除外台帳
    written = write_outputs(data_dir, built, top_dir_name=top_dir_name, out_dir_name=out_dir_name)

    ex = built["cumulative"]["exclusions"]
    print(ex["one_line"])                       # 除外件数は必ず1行で出す（隠さない）
    if ledger["status"] == "KILLED":
        trig = ledger["trigger"] or {}
        print(f"KILLED: episode={trig.get('episode_id')} "
              f"pnl={trig.get('cluster_pnl_usd')} determined={trig.get('determined_utc')} "
              f"recorded={ledger['killed_at_utc']}")
    print(json.dumps({
        "fwd_item5": "ok",
        "cutover_utc": CUTOVER_UTC,
        "days_written": len(built["day_records"]),
        "last_day": built["day_records"][-1]["day_key"] if built["day_records"] else None,
        "independent_episodes": cum["cumulative_independent_episodes"],
        "forward_final_episodes": built["n_forward_final"],
        "replay": built["n_replay"],
        "boundary_excluded": built["n_boundary_excluded"],
        "pending": built["n_pending"],
        # 既存ランナーは最終行しか cron ログへ出さないので、除外件数を最終行にも必ず含める
        "excluded": ex["one_line"],
        "worst_episode_usd": cum["kill"].get("worst_episode_usd"),
        "kill_status": ledger["status"],
        "kill_triggered": ledger["status"] == "KILLED",
        "events_appended": led["events_appended"],
        "files": len(written),
    }, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="前向き局面損益の記録（読み取りのみ・実弾0）")
    ap.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    ap.add_argument("--top-dir-name", default=TOP_DIR_NAME)
    ap.add_argument("--out-dir-name", default=OUT_DIR_NAME)
    ap.add_argument("--daily-hook", action="store_true", help="日次ランナーから呼ぶ1回実行（既定の動作と同じ）")
    ap.add_argument("--check-kill", type=float, default=None,
                    help="キル判定だけを機械確認（JSONを出して終了）")
    ap.add_argument("--json", action="store_true", help="要約JSONを出す")
    args = ap.parse_args(argv)
    if args.check_kill is not None:
        print(json.dumps(evaluate_kill(args.check_kill), ensure_ascii=False))
        return 0
    return cmd_daily_hook(Path(args.data_dir), top_dir_name=args.top_dir_name,
                          out_dir_name=args.out_dir_name)


if __name__ == "__main__":
    raise SystemExit(main())
