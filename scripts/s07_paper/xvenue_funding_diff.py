#!/usr/bin/env python3
"""【項目20】場をまたぐファンディング差 — 公開APIの同時刻差を計測する観測（読み取り専用・注文0）。

カード: t_7dd6efbb（一次資料 /root/brushup-round2-crypto-20261106.md §項目20）
既存の「ファンディング影観測」（scripts/s07_paper/hl_funding_top20.py と data/s07_paper/funding_top20/）に
**追記するだけ**の独立ファイル。既存ファイル・既存設定・既存cron は一切変更しない。

測るもの（1行）:
  同じ8時間窓 [T, T+8h) に各場が実際に徴収/支払したファンディングの**合計**を取り、
  場どうしの差 diff = (HLの窓合計) − (CEXの窓合計) を銘柄別・時間帯別・週別に集計する。
  窓合計で揃えるのは、HL が1時間精算・Binance/Bybit が8時間精算と粒度が違うため
  （レートを年率換算して比べると粒度の違いが乗る。実際に徴収された額で揃えるのが正しい）。

API（すべて公開・キー不要・読み取りのみ）:
  POST https://api.hyperliquid.xyz/info  {"type":"fundingHistory","coin":..}  … 1時間精算・500点/呼
  GET  https://fapi.binance.com/fapi/v1/fundingRate?symbol=..USDT            … 8時間精算
  GET  https://api.bybit.com/v5/market/funding/history?category=linear        … 8時間精算（startTime+endTime 両方必須）
  POST https://api.hyperliquid.xyz/info  {"type":"candleSnapshot"}            … ベーシス（価格差）用

間隔の規律:
  既存 hl_funding_top20.py は HL 呼び出し間に 0.25 秒（PAGE_SLEEP_SEC）、HL 1銘柄あたり最大6呼び。
  本スクリプトは REQ_SLEEP=0.60 秒（既存より遅い＝退歩でない）、HL 1銘柄あたり最大8呼び。
  リトライは既存 hl_client と同じ 1.5秒×(試行+1) の線形バックオフ。

実弾0・口座0: 署名なし。POST /exchange は呼ばない。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

# ---------------------------------------------------------------- 定数
COINS = ["BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "AVAX", "LINK"]
WINDOW_HOURS = 8
DAYS_BACK = 56                     # 4週間以上（8週間）
REQ_SLEEP = 0.60                   # 既存観測 0.25 秒より遅い
HL_MAX_PAGES = 8                   # 既存 6。1時間足500点×8=4000点=166日ぶん
TIMEOUT = 30.0
RETRIES = 3

HL_INFO = "https://api.hyperliquid.xyz/info"
BN_FUNDING = "https://fapi.binance.com/fapi/v1/fundingRate"
BY_FUNDING = "https://api.bybit.com/v5/market/funding/history"
UA = "yowayowa-xvenue-funding/1.0 (read-only, no-orders)"

# 手数料（推測ではなく公表値。tier は最下位＝最も不利な側で置く）
FEE_BPS = {
    "hyperliquid_taker": 4.5,      # HL perp taker（base tier）
    "binance_taker": 5.0,          # Binance USDⓈ-M futures taker VIP0（BNB割引なし）
    "bybit_taker": 5.5,            # Bybit linear taker VIP0
}
# 証拠金の分断（推測。前提を変えたら分析側も変わる）
LEVERAGE_PER_VENUE = 3.0           # 各場で3倍（保守側）
MARGIN_BUFFER = 0.20               # 各場に20%の余剰を積む（片側の急変で即清算させない）
TRANSFER_COST_USD_PER_MONTH = 2.0  # 月1回の資金移動（USDT出金≈1 + HL出金ガス≈1）
DAYS_PER_YEAR = 365.0

DEFAULT_OUT = Path("/root/current-work/yowayowa-investor/data/s07_paper/xvenue_funding")
MS_H = 3_600_000
_CALLS = 0                         # 実際に投げたHTTPリクエスト数（レート制限の証跡用）


# ---------------------------------------------------------------- HTTP
def _req(url: str, payload: dict | None = None) -> object:
    global _CALLS
    _CALLS += 1
    last: Exception | None = None
    for attempt in range(RETRIES):
        try:
            if payload is None:
                req = urllib.request.Request(url, headers={"User-Agent": UA})
            else:
                req = urllib.request.Request(
                    url,
                    data=json.dumps(payload).encode(),
                    headers={"User-Agent": UA, "Content-Type": "application/json"},
                    method="POST",
                )
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            body = exc.read()[:160].decode("utf-8", "replace")
            last = RuntimeError(f"HTTP {exc.code} {url.split('?')[0]}: {body}")
            if 400 <= exc.code < 500 and exc.code != 429:
                raise last from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last = exc
        time.sleep(1.5 * (attempt + 1))          # 既存 hl_client と同じ線形バックオフ
    raise RuntimeError(f"request failed after {RETRIES} tries: {last}")


def _iso(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _floor_hour(ms: int) -> int:
    return int(ms) // MS_H * MS_H


# ---------------------------------------------------------------- 収集
def fetch_hl_funding(coin: str, start_ms: int, end_ms: int) -> dict[int, float]:
    """HL fundingHistory をページングして {hour_ms: rate} を返す（1時間精算）。"""
    out: dict[int, float] = {}
    cursor = start_ms
    for _ in range(HL_MAX_PAGES):
        pts = _req(HL_INFO, {"type": "fundingHistory", "coin": coin, "startTime": cursor})
        if not isinstance(pts, list) or not pts:
            break
        for p in pts:
            out[_floor_hour(int(p["time"]))] = float(p["fundingRate"])
        last = int(pts[-1]["time"])
        if last >= end_ms - MS_H:
            break
        cursor = last + 1
        time.sleep(REQ_SLEEP)
    return out


def fetch_binance_funding(symbol: str, start_ms: int, end_ms: int,
                          marks_out: dict[int, float] | None = None) -> dict[int, float]:
    """Binance USDⓈ-M fundingRate。markPrice が同梱されるので同時に拾う（追加呼び出し0）。"""
    out: dict[int, float] = {}
    cursor = start_ms
    for _ in range(4):
        url = f"{BN_FUNDING}?symbol={symbol}&startTime={cursor}&endTime={end_ms}&limit=1000"
        rows = _req(url)
        if not isinstance(rows, list) or not rows:
            break
        for r in rows:
            ts = _floor_hour(int(r["fundingTime"]))
            out[ts] = float(r["fundingRate"])
            if marks_out is not None and r.get("markPrice"):
                marks_out[ts] = float(r["markPrice"])
        last = int(rows[-1]["fundingTime"])
        if last >= end_ms - MS_H:
            break
        cursor = last + 1
        time.sleep(REQ_SLEEP)
    return out


def fetch_bybit_funding(symbol: str, start_ms: int, end_ms: int) -> dict[int, float]:
    """Bybit v5 は startTime と endTime の両方が必須（片方だけだと retCode=10001）。"""
    out: dict[int, float] = {}
    cursor = start_ms
    for _ in range(4):
        url = (f"{BY_FUNDING}?category=linear&symbol={symbol}"
               f"&startTime={cursor}&endTime={end_ms}&limit=200")
        data = _req(url)
        if not isinstance(data, dict) or data.get("retCode") != 0:
            raise RuntimeError(f"bybit retCode={data.get('retCode') if isinstance(data, dict) else data}")
        rows = data["result"]["list"]
        if not rows:
            break
        for r in rows:
            out[_floor_hour(int(r["fundingRateTimestamp"]))] = float(r["fundingRate"])
        last = max(int(r["fundingRateTimestamp"]) for r in rows)
        if last >= end_ms - MS_H:
            break
        cursor = last + 1
        time.sleep(REQ_SLEEP)
    return out


def fetch_hl_marks(coin: str, start_ms: int, end_ms: int) -> dict[int, float]:
    """1時間足の終値 {hour_ms: close}（ベーシス計算用・価格そのものは売買に使わない）。"""
    raw = _req(HL_INFO, {"type": "candleSnapshot",
                         "req": {"coin": coin, "interval": "1h",
                                 "startTime": start_ms, "endTime": end_ms}})
    out: dict[int, float] = {}
    if isinstance(raw, list):
        for c in raw:
            out[_floor_hour(int(c["t"]))] = float(c["c"])
    return out


def fetch_binance_close_1h(symbol: str, start_ms: int, end_ms: int) -> dict[int, float]:
    """Binance USDⓈ-M の1時間足終値 {hour_ms: close}。

    ベーシスは「同じ種類の価格」で測る必要がある。HL の candleSnapshot は約定価格なので、
    Binance 側も markPrice（公正価格）ではなく約定系の終値を使う（mark との混在は系統誤差になる）。
    """
    out: dict[int, float] = {}
    cursor = start_ms
    for _ in range(4):
        url = (f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=1h"
               f"&startTime={cursor}&endTime={end_ms}&limit=1500")
        rows = _req(url)
        if not isinstance(rows, list) or not rows:
            break
        for r in rows:
            out[_floor_hour(int(r[0]))] = float(r[4])
        last = int(rows[-1][0])
        if last >= end_ms - MS_H:
            break
        cursor = last + 1
        time.sleep(REQ_SLEEP)
    return out


# ---------------------------------------------------------------- 窓合計
def window_sum(series: dict[int, float], t0: int, hours: int = WINDOW_HOURS) -> tuple[float | None, int]:
    """[t0, t0+hours*1h) に入る精算の合計と、観測できた点数。"""
    total = 0.0
    n = 0
    for i in range(hours):
        v = series.get(t0 + i * MS_H)
        if v is not None:
            total += v
            n += 1
    return (total if n else None), n


# ---------------------------------------------------------------- 統計ヘルパ
def pct(vals: list[float], q: float) -> float | None:
    if not vals:
        return None
    s = sorted(vals)
    if len(s) == 1:
        return s[0]
    idx = (len(s) - 1) * q
    lo = math.floor(idx)
    hi = math.ceil(idx)
    if lo == hi:
        return s[int(idx)]
    return s[lo] + (s[hi] - s[lo]) * (idx - lo)


def bps(x: float) -> float:
    """率 → bps（1bp = 0.0001 = 0.01%）。"""
    return x * 10_000.0


def run_lengths(signs: list[int]) -> list[int]:
    if not signs:
        return []
    runs = []
    cur = 1
    for a, b in zip(signs, signs[1:]):
        if a == b:
            cur += 1
        else:
            runs.append(cur)
            cur = 1
    runs.append(cur)
    return runs


# ---------------------------------------------------------------- メイン収集
def collect(out_dir: Path, days_back: int = DAYS_BACK) -> dict:
    now_ms = int(time.time() * 1000)
    # 窓は必ず Binance/Bybit の精算グリッド（00/08/16 UTC）に合わせる。
    # ずらすと Binance の markPrice と突き合わせられず、時間帯別の意味も濁る。
    step = WINDOW_HOURS * MS_H
    end_ms = now_ms // step * step
    start_ms = end_ms - days_back * 24 * MS_H
    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    venues: dict[str, dict[str, dict[int, float]]] = {"hl": {}, "bn": {}, "by": {}}
    bn_marks: dict[str, dict[int, float]] = {}
    hl_marks: dict[str, dict[int, float]] = {}
    bn_close: dict[str, dict[int, float]] = {}
    errors: list[dict] = []
    calls = 0

    for coin in COINS:
        sym = f"{coin}USDT"
        for key, fn in (("hl", lambda: fetch_hl_funding(coin, start_ms, end_ms)),
                        ("bn", lambda: fetch_binance_funding(sym, start_ms, end_ms, bn_marks.setdefault(coin, {}))),
                        ("by", lambda: fetch_bybit_funding(sym, start_ms, end_ms))):
            try:
                venues[key][coin] = fn()
            except Exception as exc:                       # noqa: BLE001 - 欠測は明記して続行
                venues[key][coin] = {}
                errors.append({"coin": coin, "venue": key, "error": f"{type(exc).__name__}: {exc}"[:200]})
            time.sleep(REQ_SLEEP)
        try:
            hl_marks[coin] = fetch_hl_marks(coin, start_ms, end_ms)
            time.sleep(REQ_SLEEP)
            bn_close[coin] = fetch_binance_close_1h(sym, start_ms, end_ms)
        except Exception as exc:                           # noqa: BLE001
            hl_marks.setdefault(coin, {})
            bn_close.setdefault(coin, {})
            errors.append({"coin": coin, "venue": "hl_marks", "error": f"{type(exc).__name__}: {exc}"[:200]})
        time.sleep(REQ_SLEEP)

    # 生データをそのまま保存（監査用。後から再集計できる）
    for key in ("hl", "bn", "by"):
        for coin, series in venues[key].items():
            (raw_dir / f"{key}_{coin}.json").write_text(
                json.dumps({_iso(t): r for t, r in sorted(series.items())}, ensure_ascii=False)
            )
    for name, src in (("marks_hl", hl_marks), ("marks_bn", bn_marks), ("close_bn", bn_close)):
        (raw_dir / f"{name}.json").write_text(
            json.dumps({c: {_iso(t): v for t, v in sorted(s.items())} for c, s in src.items()},
                       ensure_ascii=False)
        )

    # 8時間窓で整列
    windows: list[dict] = []
    t = start_ms
    while t + WINDOW_HOURS * MS_H <= end_ms:
        for coin in COINS:
            s_hl, n_hl = window_sum(venues["hl"].get(coin, {}), t)
            s_bn, n_bn = window_sum(venues["bn"].get(coin, {}), t)
            s_by, n_by = window_sum(venues["by"].get(coin, {}), t)
            row = {
                "t_ms": t,
                "t_utc": _iso(t),
                "coin": coin,
                "hour_utc": dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc).hour,
                "n_hl": n_hl, "n_bn": n_bn, "n_by": n_by,
                "hl": s_hl, "bn": s_bn, "by": s_by,
            }
            if s_hl is not None and s_bn is not None:
                row["diff_bn"] = s_hl - s_bn
            if s_hl is not None and s_by is not None:
                row["diff_by"] = s_hl - s_by
            if s_bn is not None and s_by is not None:
                row["diff_bn_by"] = s_bn - s_by
            # ベーシス: 約定終値どうし（HL 1h close vs Binance 1h close）を主、mark 基準を従
            hp = hl_marks.get(coin, {}).get(t)
            bp = bn_close.get(coin, {}).get(t)
            if hp and bp:
                row["basis_bps"] = (hp - bp) / bp * 10_000.0
            bp_mark = bn_marks.get(coin, {}).get(t)
            if hp and bp_mark:
                row["basis_mark_bps"] = (hp - bp_mark) / bp_mark * 10_000.0
            windows.append(row)
        t += WINDOW_HOURS * MS_H

    meta = {
        "schema_version": 1,
        "card": "t_7dd6efbb",
        "source_doc": "/root/brushup-round2-crypto-20261106.md §項目20",
        "collected_at_utc": _iso(now_ms),
        "window_hours": WINDOW_HOURS,
        "days_back": days_back,
        "period_from_utc": _iso(start_ms),
        "period_to_utc": _iso(end_ms),
        "coins": COINS,
        "definition": (
            "窓合計 = [T, T+8h) に各場が実際に徴収/支払したファンディングレートの合計。"
            "diff_bn = HL窓合計 − Binance窓合計（正なら HL の long が多く払う＝HL short/CEX long が受け取る）。"
        ),
        "rate_limit": {"req_sleep_sec": REQ_SLEEP,
                       "existing_observation_sleep_sec": 0.25,
                       "note": "既存より遅い（退歩でない）。HLは1銘柄 最大8ページ"},
        "fees_bps": FEE_BPS,
        "margin_assumptions": {
            "leverage_per_venue": LEVERAGE_PER_VENUE,
            "margin_buffer": MARGIN_BUFFER,
            "transfer_cost_usd_per_month": TRANSFER_COST_USD_PER_MONTH,
            "note": "推測。公表手数料と違い、レバレッジ・余剰・移動費は仮定値",
        },
        "errors": errors,
        "http_requests": _CALLS,
        "n_rows": len(windows),
        "n_windows": len(windows) // max(1, len(COINS)),
        "n_coins": len(COINS),
    }

    (out_dir / "windows.json").write_text(
        json.dumps({"meta": meta, "windows": windows}, ensure_ascii=False), encoding="utf-8"
    )
    return {"meta": meta, "windows": windows}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="項目20 場をまたぐファンディング差の観測（読み取りのみ）")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--days", type=int, default=DAYS_BACK)
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    rec = collect(out, args.days)
    m = rec["meta"]
    cov = Counter()
    for w in rec["windows"]:
        if w.get("diff_bn") is not None:
            cov["bn_ok"] += 1
        if w.get("diff_by") is not None:
            cov["by_ok"] += 1
    print(json.dumps({
        "ok": True,
        "period": [m["period_from_utc"], m["period_to_utc"]],
        "n_windows": m["n_windows"],
        "n_rows": m["n_rows"],
        "diff_bn_available": cov["bn_ok"],
        "diff_by_available": cov["by_ok"],
        "errors": len(m["errors"]),
        "elapsed_s": round(time.time() - t0, 1),
        "out": str(out),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
