#!/usr/bin/env python3
"""【J1】円建て価格差観測（bitFlyer/bitbank vs Binance×USDJPY）— 読み取りのみ・実弾0・注文0。

カード t_aa251cce（2026-11-06・親 t_7dd6efbb／項目21 /root/brushup-round2-crypto-20261106.md §項目21）。
既存 s07_paper 観測基盤に相乗り（B2 t_7a53946d と同じ純追記型）:
s07_daily_run.py から best-effort で `--daily-hook` が呼ばれ、flock 単一の 15分ループを確保する。
ループは毎UTC日 00:52 に自己終了し、翌日の cron で再確保される（OIループと同一の生存様式）。

## 定義（凍結・カード受入条件からそのまま採番・結果を見る前に確定）
- premium_pct = (国内mid / (Binance BTC/USDT mid × USDJPY) − 1) × 100
  - 国内mid = (best_bid + best_ask)/2（欠け時は ltp/last に縮退し reduced フラグを付ける）
  - USDJPY = Yahoo Finance USDJPY=X の最新値（fallback: open.er-api.com の日次値。row の
    usdjpy.source に記録し、日次値を使ったサンプル数をレポートに明記）
- 判定（凍結）: |premium_pct| ≥ 1.0% のサンプルが連続する「局面」のうち、局面の長さ
  （最初のヒット→最後のヒット）が ≥ 2.0時間 のものを「数時間単位で持続した局面」と数える。
  ヒット間の欠測は1サンプル分（到達間隔33分以内）までは同一局面に繋ぐ。
  bitFlyer基準・bitbank基準のそれぞれで数え、**どちらかの基準で 2局面以上**なら
  「設計材料として上げる」・1局面以下なら「閉じる」（カード受入条件「複数回」の凍結解釈）。
  感度（参考値・判定は変わらない）: 3.0h / 4.0h の持続閾値でも数える。
- 価格は板の最良気配(mid)基準。手数料・スリッページ・USD調達コストは含まない
  （設計材料の前段の観測であり、実行可能取り分の見積りではない）。

## 出力（jpy_premium/ 配下・既存 S-07/B1/B2 のファイルは一切変更しない）
  snapshots/YYYY-MM-DD.jsonl   15分毎のスナップショット（UTC日キー・追記のみ・冪等性なし）
  health.json                  この観測専用の稼働記録（S-07 本体 health は触らない）
  collector.log / collector.pid / jpy_collect.lock   収集ループの稼働記録
  reports/REPORT_JPY_PREMIUM.md + analysis.json      --report で生成（日次では自動生成しない）

## 負荷（レート制限規律）
- 1サンプル = 公開API 4系統に各1リクエスト（bitFlyer・bitbank・Binance・Yahoo）。15分間隔。
- 既存規律（B2のOIスナップ15分・10〜30分枠内）と同一。失敗時は30秒バックオフして続行。
- 署名なし・キーなし・注文なし。GET のみ。

## 単独実行
  python3 jpy_premium_obs.py --sample-once --data-dir <dir>   # 1回だけ取得して追記
  python3 jpy_premium_obs.py --daily-hook  --data-dir <dir>   # 取得＋ループ確保（日次ジョブ用）
  python3 jpy_premium_obs.py --status      --data-dir <dir>   # 稼働状態
  python3 jpy_premium_obs.py --report      --data-dir <dir>   # 集計（凍結ルール）とレポート生成
"""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import math
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

CARD = "t_aa251cce"
TOP_DIR_NAME = "jpy_premium"
INTERVAL_SEC = int(os.environ.get("JPY_PREM_INTERVAL_SEC", "900"))   # 既定15分（B2と同一の10〜30分枠内）
DEADLINE_UTC = "00:52"          # 毎UTC日 00:52 に自己終了（10:00 JST cron との重複回避・OIループと同一）
HTTP_TIMEOUT_S = 15
BACKOFF_S = 30.0                # 失敗時の再試行前バックオフ（B2 の 429 バックオフ基準と同一）

# ---- 凍結定数（カード受入条件の採番・結果を見る前に確定、以後変更しない） ----
THRESHOLD_PCT = 1.0             # |premium_pct| の局面判定閾値（カード文言どおり）
MIN_DURATION_H = 2.0            # 「数時間単位」の下限を 2.0時間 と凍結
GAP_TOL_SAMPLES = 1             # 欠測1サンプルまでは同一局面に繋ぐ（毎日09:52-10:00 JSTの継ぎ目対策）
MAX_BRIDGE_MS = int((GAP_TOL_SAMPLES + 1) * INTERVAL_SEC * 1.1 * 1000)  # ヒット間の最大到達間隔（=33分）
SENSITIVITY_DURATION_H = (3.0, 4.0)   # 感度参照（判定には使わない）
VERDICT_MIN_EPISODES = 2        # 「複数回」= 2局面以上（どちらかの国内基準で）→ 上げる
PREMIUM_FORMULA = (
    "premium_pct = (国内mid / (Binance BTC/USDT mid × USDJPY) − 1) × 100。"
    "国内mid=(best_bid+best_ask)/2（欠け時はltp/lastへ縮退）。USDJPYはYahoo USDJPY=Xの最新値"
    "（fallback: open.er-api.com 日次値・sourceで明記）。判定: |premium_pct|≥1.0% が局面の長さ≥2.0h で持続した"
    "局面の回数（欠測1サンプルまで同一局面）。bitFlyer基準/bitbank基準のどちらかで2局面以上→設計材料として上げる、"
    "1局面以下→閉じる。価格は最良気配mid基準・手数料等は含まない。"
)

BITFLYER_TICKER_URL = "https://api.bitflyer.com/v1/ticker?product_code=BTC_JPY"
BITBANK_TICKER_URL = "https://public.bitbank.cc/btc_jpy/ticker"
BINANCE_BOOKTICKER_URL = "https://api.binance.com/api/v3/ticker/bookTicker?symbol=BTCUSDT"
YAHOO_USDJPY_URL = "https://query1.finance.yahoo.com/v8/finance/chart/USDJPY=X?interval=1m&range=1d"
YAHOO_UA = "Mozilla/5.0 (compatible; jpy_premium_obs/1.0; observation only)"
ERAPI_USD_URL = "https://open.er-api.com/v6/latest/USD"


# --------------------------------------------------------------------------
# 基礎（時刻・書込・HTTP）
# --------------------------------------------------------------------------

def _utc_str(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _jst_str(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone(dt.timedelta(hours=9))).strftime("%Y-%m-%dT%H:%M:%S%z")


def _jst_hour(ms: int) -> int:
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone(dt.timedelta(hours=9))).hour


def _ms_day(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%d")


def top_dir(data_dir: Path) -> Path:
    return Path(data_dir) / TOP_DIR_NAME


def _write_atomic(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=False))
    os.replace(tmp, path)


def _append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        f.flush()
        os.fsync(f.fileno())


def _get_json(url: str, *, headers: dict | None = None, tries: int = 2):
    """公開APIへの読み取りGET（署名なし・キーなし）。失敗時は BACKOFF_S 待って1回だけ再試行。"""
    last_exc: Exception | None = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=headers or {"User-Agent": YAHOO_UA}, method="GET")
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
                return json.loads(resp.read().decode())
        except Exception as exc:  # noqa: BLE001 - ネットワーク系は全部ここで数えて再試行
            last_exc = exc
            if i + 1 < tries:
                time.sleep(BACKOFF_S)
    raise last_exc if last_exc else RuntimeError("get_json failed")


# --------------------------------------------------------------------------
# 4系統の取得（各1リクエスト・読み取りのみ）
# --------------------------------------------------------------------------

def fetch_bitflyer() -> dict:
    d = _get_json(BITFLYER_TICKER_URL, headers={"User-Agent": YAHOO_UA})
    bid, ask, ltp = d.get("best_bid"), d.get("best_ask"), d.get("ltp")
    reduced = bid is None or ask is None
    mid = ((bid + ask) / 2.0) if not reduced else ltp
    if mid is None:
        raise ValueError("bitflyer: no bid/ask/ltp")
    return {"bid": bid, "ask": ask, "ltp": ltp, "mid": round(float(mid), 1),
            "reduced_mid": reduced, "api_ts": d.get("timestamp")}


def fetch_bitbank() -> dict:
    d = _get_json(BITBANK_TICKER_URL)
    if not (isinstance(d, dict) and d.get("success") == 1 and isinstance(d.get("data"), dict)):
        raise ValueError("bitbank: unexpected payload")
    t = d["data"]
    sell, buy, last = (float(t["sell"]), float(t["buy"]), float(t.get("last") or 0) or None)
    reduced = not (sell > 0 and buy > 0)
    mid = (sell + buy) / 2.0 if not reduced else last
    if not mid:
        raise ValueError("bitbank: no bid/ask/last")
    return {"bid": buy, "ask": sell, "last": last, "mid": round(float(mid), 1),
            "reduced_mid": reduced, "api_ts_ms": t.get("timestamp")}


def fetch_binance() -> dict:
    d = _get_json(BINANCE_BOOKTICKER_URL)
    if d.get("symbol") != "BTCUSDT":
        raise ValueError("binance: unexpected symbol")
    bid, ask = float(d["bidPrice"]), float(d["askPrice"])
    return {"bid": bid, "ask": ask, "mid": round((bid + ask) / 2.0, 2)}


def fetch_usdjpy() -> dict:
    """Yahoo USDJPY=X の最新値。失敗時は open.er-api.com の日次値へ縮退（sourceで明記）。"""
    try:
        d = _get_json(YAHOO_USDJPY_URL)
        meta = (d.get("chart") or {}).get("result", [{}])[0].get("meta", {})
        val = meta.get("regularMarketPrice")
        if not val:
            raise ValueError("yahoo: no regularMarketPrice")
        mt_ms = int(meta.get("regularMarketTime") or 0) * 1000
        return {"value": round(float(val), 4), "source": "yahoo_1m",
                "market_time_utc": _utc_str(mt_ms) if mt_ms else None}
    except Exception:  # noqa: BLE001 - fallback へ縮退（sourceに記録）
        d = _get_json(ERAPI_USD_URL, headers={"User-Agent": YAHOO_UA})
        if d.get("result") != "success":
            raise ValueError("er-api: result != success")
        val = (d.get("rates") or {}).get("JPY")
        if not val:
            raise ValueError("er-api: no JPY rate")
        return {"value": round(float(val), 4), "source": "er_api_daily",
                "market_time_utc": None}


def premium_pct(venue_mid: float, binance_mid: float, usdjpy: float) -> float:
    """凍結定義の計算（純関数・テスト対象）。"""
    return (venue_mid / (binance_mid * usdjpy) - 1.0) * 100.0


def sample_once(now_ms: int | None = None) -> dict:
    """1サンプル取得（系統ごとに個別 try・落ちた系統は None。premium は構成が揃った系統のみ）。"""
    now_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    row: dict = {
        "at_utc": _utc_str(now_ms), "jst": _jst_str(now_ms), "card": CARD,
        "interval_note": f"15分間隔設計（設定 {INTERVAL_SEC}s）・公開API読取のみ・注文0",
    }
    for name, fn in (("bitflyer", fetch_bitflyer), ("bitbank", fetch_bitbank),
                     ("binance", fetch_binance), ("usdjpy", fetch_usdjpy)):
        try:
            row[name] = fn()
        except Exception as exc:  # noqa: BLE001 - 1系統の失敗でサンプル全体を落とさない
            row[name] = {"error": f"{type(exc).__name__}: {exc}"[:180]}
    row["premium_pct"] = {"bitflyer": None, "bitbank": None}
    bn = row.get("binance") or {}
    fx = row.get("usdjpy") or {}
    if isinstance(bn.get("mid"), (int, float)) and isinstance(fx.get("value"), (int, float)):
        for venue in ("bitflyer", "bitbank"):
            v = row.get(venue) or {}
            if isinstance(v.get("mid"), (int, float)):
                row["premium_pct"][venue] = round(premium_pct(v["mid"], bn["mid"], fx["value"]), 4)
    return row


# --------------------------------------------------------------------------
# 収集ループ（B2のOIループと同一様式: flock 単一・00:52UTC自己終了・日次cronで再確保）
# --------------------------------------------------------------------------

def collector_alive(data_dir: Path) -> bool:
    lock_path = top_dir(data_dir) / "jpy_collect.lock"
    if not lock_path.exists():
        return False
    try:
        fh = open(lock_path, "w")
    except OSError:
        return True
    alive = False
    try:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fh, fcntl.LOCK_UN)
        except BlockingIOError:
            alive = True
    finally:
        fh.close()
    return alive


def ensure_collector(data_dir: Path, *, interval_sec: int = INTERVAL_SEC) -> dict:
    """日次ジョブ内から収集ループを確実に走らせる。flock で二重起動なし。読み取りのみ。"""
    if collector_alive(data_dir):
        return {"status": "already_running", "spawned": False, "interval_sec": interval_sec}
    log_path = top_dir(data_dir) / "collector.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logfh = open(log_path, "a", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--loop",
         "--data-dir", str(data_dir), "--interval", str(interval_sec)],
        stdout=logfh, stderr=logfh, stdin=subprocess.DEVNULL,
        start_new_session=True, cwd=str(Path(__file__).resolve().parent),
    )
    pid = proc.pid
    logfh.close()
    _write_atomic(top_dir(data_dir) / "collector.pid",
                  {"pid": pid, "started_at_utc": _utc_str(int(time.time() * 1000)),
                   "interval_sec": interval_sec, "card": CARD})
    return {"status": "spawned", "spawned": True, "pid": pid, "interval_sec": interval_sec}


def collect_loop(data_dir: Path, *, interval_sec: int = INTERVAL_SEC) -> int:
    """収集ループ（デタッチ子プロセス用）。flock で単一・00:52UTC に自己終了。"""
    top_dir(data_dir).mkdir(parents=True, exist_ok=True)
    lock_path = top_dir(data_dir) / "jpy_collect.lock"
    fh = open(lock_path, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("jpy premium collector: already running（既存ループがいるので何もしない）")
        return 0
    now = dt.datetime.now(dt.timezone.utc)
    deadline = dt.datetime.combine(now.date() + dt.timedelta(days=1),
                                   dt.time.fromisoformat(DEADLINE_UTC), tzinfo=dt.timezone.utc)
    log_path = top_dir(data_dir) / "collector.log"

    def log(msg: str) -> None:
        line = f"{_utc_str(int(time.time() * 1000))} {msg}"
        print(line, flush=True)
        try:
            with log_path.open("a", encoding="utf-8") as lf:
                lf.write(line + "\n")
        except OSError:
            pass

    log(f"start pid={os.getpid()} interval={interval_sec}s deadline={deadline.isoformat()}")
    snap_count = 0
    while True:
        try:
            row = sample_once()
            path = top_dir(data_dir) / "snapshots" / f"{_ms_day(int(time.time() * 1000))}.jsonl"
            _append_jsonl(path, row)
            snap_count += 1
        except Exception as exc:  # noqa: BLE001 - 1回の失敗でループは止めない（公開APIの読取）
            log(f"snapshot_fail {type(exc).__name__}: {str(exc)[:200]}")
            time.sleep(BACKOFF_S)
            continue
        if time.time() + interval_sec >= deadline.timestamp():
            time.sleep(max(1.0, deadline.timestamp() - time.time()))
            log(f"deadline reached total_snapshots={snap_count}")
            break
        time.sleep(interval_sec)
    log("exit ok")
    try:
        fcntl.flock(fh, fcntl.LOCK_UN)
    except OSError:
        pass
    fh.close()
    return 0


# --------------------------------------------------------------------------
# 日次フック・health・status
# --------------------------------------------------------------------------

def _update_health(data_dir: Path, *, ok: bool, detail: dict | str) -> None:
    hp = top_dir(data_dir) / "health.json"
    try:
        health = json.loads(hp.read_text()) if hp.exists() else {}
    except (json.JSONDecodeError, OSError):
        health = {}
    now_ms = int(time.time() * 1000)
    event = {"at_utc": _utc_str(now_ms), "event": "ok" if ok else "fail"}
    if isinstance(detail, str):
        event["error"] = detail[:300]
    else:
        event.update(detail)
    health.setdefault("history", []).append(event)
    health["history"] = health["history"][-100:]
    if ok:
        health["last_ok_utc"] = _utc_str(now_ms)
        health["last_error"] = None
    else:
        health["last_error"] = f"{_utc_str(now_ms)} {str(detail)[:300]}"
    health["card"] = CARD
    _write_atomic(hp, health)


def daily_hook(data_dir: Path) -> dict:
    """日次ジョブからの入口: 1サンプル取得＋収集ループ確保＋health更新。"""
    data_dir = Path(data_dir)
    summary: dict = {"card": CARD}
    try:
        row = sample_once()
        path = top_dir(data_dir) / "snapshots" / f"{_ms_day(int(time.time() * 1000))}.jsonl"
        _append_jsonl(path, row)
        summary.update({
            "status": "ok", "sample_at_utc": row["at_utc"],
            "premium_pct": row["premium_pct"],
            "usdjpy_source": (row.get("usdjpy") or {}).get("source"),
        })
        _update_health(data_dir, ok=True, detail={"sample_at_utc": row["at_utc"]})
    except Exception as exc:  # noqa: BLE001 - hook自体は best-effort（S-07本体を落とさない）
        summary.update({"status": "failed", "error": f"{type(exc).__name__}: {exc}"[:300],
                        "note": "この観測のみ欠落。翌日cronで自動再試行（S-07本体・B1・B2は無影響）"})
        _update_health(data_dir, ok=False, detail=str(exc))
        return summary
    summary["collector"] = ensure_collector(data_dir)
    try:
        summary["total_samples"] = count_samples(data_dir)
    except OSError:
        summary["total_samples"] = None
    return summary


def count_samples(data_dir: Path) -> int:
    n = 0
    for p in sorted(top_dir(data_dir).glob("snapshots/*.jsonl")):
        with p.open(encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    n += 1
    return n


def load_rows(data_dir: Path) -> tuple[list[dict], int]:
    """全スナップショットを読む（時刻昇順・壊れた行はスキップして件数を返す）。"""
    rows: list[dict] = []
    bad = 0
    for p in sorted(top_dir(data_dir).glob("snapshots/*.jsonl")):
        with p.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    bad += 1
    rows.sort(key=lambda r: r.get("at_utc") or "")
    return rows, bad


def last_sample(data_dir: Path) -> dict | None:
    rows, _ = load_rows(data_dir)
    return rows[-1] if rows else None


def cmd_status(data_dir: Path) -> int:
    alive = collector_alive(data_dir)
    n = count_samples(data_dir)
    last = last_sample(data_dir)
    age_min = None
    if last:
        t = dt.datetime.strptime(last["at_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
        age_min = round((time.time() * 1000 - int(t.timestamp() * 1000)) / 60000, 1)
    print(json.dumps({
        "card": CARD, "collector_alive": alive, "total_samples": n,
        "last_sample_at_utc": (last or {}).get("at_utc"), "age_min": age_min,
        "last_premium_pct": (last or {}).get("premium_pct"),
    }, ensure_ascii=False))
    return 0


# --------------------------------------------------------------------------
# 集計（凍結ルール・決定的・乱数なし）
# --------------------------------------------------------------------------

def detect_episodes(points: list[tuple[int, float]], *, threshold_pct: float = THRESHOLD_PCT,
                    min_duration_h: float = MIN_DURATION_H,
                    max_bridge_ms: int = MAX_BRIDGE_MS) -> tuple[list[dict], int]:
    """|premium|≥閾値の「局面」を抽出（欠測橋渡し付き・純関数・決定的）。

    points: (ms, premium_pct) 昇順。ヒット間が max_bridge_ms 以内なら同一局面に繋ぐ。
    戻り値: (全局面リスト[duration問わず], 持続閾値を満たした局面数)
    """
    episodes: list[dict] = []
    cur: list[tuple[int, float]] = []
    for ms, v in points:
        if abs(v) >= threshold_pct:
            if cur and ms - cur[-1][0] > max_bridge_ms:
                episodes.append(cur)
                cur = []
            cur.append((ms, v))
        # 非ヒットは局面を切らない（橋渡しはヒット間隔で判定）
    if cur:
        episodes.append(cur)
    out = []
    for ep in episodes:
        start_ms, end_ms = ep[0][0], ep[-1][0]
        vals = [v for _, v in ep]
        peak = max(vals, key=abs)
        out.append({
            "start_utc": _utc_str(start_ms), "end_utc": _utc_str(end_ms),
            "jst_start": _jst_str(start_ms), "jst_start_hour": _jst_hour(start_ms),
            "duration_h": round((end_ms - start_ms) / 3_600_000, 2),
            "samples": len(ep),
            "sign": "+" if peak > 0 else "-",
            "max_abs_pct": round(abs(peak), 4), "peak_pct": round(peak, 4),
            "meets_min_duration": (end_ms - start_ms) >= min_duration_h * 3_600_000,
        })
    n_meet = sum(1 for e in out if e["meets_min_duration"])
    return out, n_meet


def _pct_rank(sorted_vals: list[float], q: float) -> float | None:
    if not sorted_vals:
        return None
    idx = min(len(sorted_vals) - 1, max(0, int(math.ceil(q / 100.0 * len(sorted_vals))) - 1))
    return round(sorted_vals[idx], 4)


def venue_analysis(points: list[tuple[int, float]]) -> dict:
    vals = sorted(v for _, v in points)
    n = len(vals)
    stats = {
        "samples": n,
        "mean_pct": round(sum(vals) / n, 4) if n else None,
        "median_pct": _pct_rank(vals, 50), "p01_pct": _pct_rank(vals, 1),
        "p90_pct": _pct_rank(vals, 90), "p99_pct": _pct_rank(vals, 99),
        "min_pct": round(vals[0], 4) if n else None, "max_pct": round(vals[-1], 4) if n else None,
        "pct_time_abs_ge_1.0": round(100.0 * sum(1 for v in vals if abs(v) >= 1.0) / n, 3) if n else None,
        "pct_time_abs_ge_0.5": round(100.0 * sum(1 for v in vals if abs(v) >= 0.5) / n, 3) if n else None,
    }
    episodes, n_meet = detect_episodes(points)
    meet = [e for e in episodes if e["meets_min_duration"]]
    for dur_h in SENSITIVITY_DURATION_H:
        stats[f"episodes_ge_{str(dur_h).replace('.', '_')}h"] = sum(
            1 for e in episodes if e["duration_h"] >= dur_h)
    stats["episodes_ge_2_0h"] = n_meet
    stats["episodes_ge_2_0h_positive"] = sum(1 for e in meet if e["sign"] == "+")
    stats["episodes_ge_2_0h_negative"] = sum(1 for e in meet if e["sign"] == "-")
    stats["episodes_list_ge_2_0h"] = meet
    stats["episodes_all_short"] = [
        e for e in episodes if not e["meets_min_duration"]][:20]
    return stats


def jst_hour_profile(points: list[tuple[int, float]]) -> dict:
    by_hour: dict[int, list[float]] = {}
    for ms, v in points:
        by_hour.setdefault(_jst_hour(ms), []).append(v)
    return {str(h): round(sum(v) / len(v), 4) for h, v in sorted(by_hour.items())}


def verdict_for(n_bitflyer: int, n_bitbank: int) -> str:
    """凍結判定規則（純関数・テスト対象）。"""
    if max(n_bitflyer, n_bitbank) >= VERDICT_MIN_EPISODES:
        return ("上げる（設計材料）: 1%以上の差が2.0時間以上持続した局面を複数回観測"
                f"（bitFlyer基準 {n_bitflyer} 回・bitbank基準 {n_bitbank} 回）")
    return (f"閉じる: 1%以上の差が2.0時間以上持続した局面は複数回ではない"
            f"（bitFlyer基準 {n_bitflyer} 回・bitbank基準 {n_bitbank} 回）")


def analyze(data_dir: Path) -> dict:
    rows, bad_lines = load_rows(data_dir)
    points_bf: list[tuple[int, float]] = []
    points_bb: list[tuple[int, float]] = []
    fx_fallback = 0
    usable_days: set[str] = set()
    for r in rows:
        t = r.get("at_utc")
        if not t:
            continue
        ms = int(dt.datetime.strptime(t, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
        fxv = (r.get("usdjpy") or {})
        if fxv.get("source") == "er_api_daily":
            fx_fallback += 1
        prems = r.get("premium_pct") or {}
        if isinstance(prems.get("bitflyer"), (int, float)):
            points_bf.append((ms, prems["bitflyer"]))
            usable_days.add(_ms_day(ms))
        if isinstance(prems.get("bitbank"), (int, float)):
            points_bb.append((ms, prems["bitbank"]))
            usable_days.add(_ms_day(ms))

    def cov(points):
        if not points:
            return {"span_days": 0.0, "days_with_samples": 0}
        span = (points[-1][0] - points[0][0]) / 86_400_000
        return {"span_days": round(span, 2), "days_with_samples": len(usable_days),
                "first_utc": _utc_str(points[0][0]), "last_utc": _utc_str(points[-1][0])}

    bf = venue_analysis(points_bf)
    bb = venue_analysis(points_bb)
    out = {
        "card": CARD, "generated_at_utc": _utc_str(int(time.time() * 1000)),
        "frozen_rules": {
            "formula": PREMIUM_FORMULA, "threshold_pct": THRESHOLD_PCT,
            "min_duration_h": MIN_DURATION_H, "gap_tol_samples": GAP_TOL_SAMPLES,
            "max_bridge_ms": MAX_BRIDGE_MS, "verdict_min_episodes": VERDICT_MIN_EPISODES,
            "note": "凍結はカード受入条件からの採番（観測開始前に確定・以後変更しない）",
        },
        "coverage": {"rows_total": len(rows), "rows_unparsable": bad_lines,
                     "fx_fallback_samples": fx_fallback,
                     "bitflyer": cov(points_bf), "bitbank": cov(points_bb)},
        "bitflyer_base": bf, "bitbank_base": bb,
        "jst_hour_mean_premium": {"bitflyer": jst_hour_profile(points_bf),
                                  "bitbank": jst_hour_profile(points_bb)},
        "verdict": verdict_for(bf["episodes_ge_2_0h"], bb["episodes_ge_2_0h"]),
        "limitations": [
            "価格は最良気配mid基準。手数料・スリッページ・USD調達・円転のコストは含まない（実行可能取り分ではない）。",
            "USDJPYのfallback（日次値）を使ったサンプルは fx_fallback_samples の件数。FXが大きく動いた時間帯のpremiumは誤差を含む。",
            "サンプルは15分間隔のスナップショット。局面の長さは15分粒度（欠測1サンプルまでは橋渡し）。",
            "観測は前向き蓄積。ループ死亡時は次の10:00 JST cronまで欠測（collector.logに記録）。",
        ],
    }
    return out


def write_report(path: Path, analysis: dict) -> None:
    bf = analysis["bitflyer_base"]
    bb = analysis["bitbank_base"]
    cov = analysis["coverage"]

    def rows_of(stats):
        out_rows = []
        for e in stats["episodes_list_ge_2_0h"]:
            out_rows.append(
                f"| {e['jst_start']} | {e['duration_h']:.2f}h | {e['sign']} | {e['peak_pct']:+.2f}% | {e['samples']} |")
        return "\n".join(out_rows) or "（なし）"

    md = f"""# 円建て価格差観測レポート（bitFlyer/bitbank vs Binance×USDJPY）【J1・{CARD}】

生成: {analysis['generated_at_utc']}（--report の都度再生成・決定的）

## 凍結ルール（観測開始前に確定・以後変更しない）
- {analysis['frozen_rules']['formula']}

## カバレッジ
- スナップ行: {cov['rows_total']} 行（壊れた行 {cov['rows_unparsable']}・USDJPY fallback {cov['fx_fallback_samples']} 行）
- bitFlyer基準: {cov['bitflyer']}
- bitbank基準: {cov['bitbank']}

## 分布（全サンプル・premium_pct）
| 指標 | bitFlyer基準 | bitbank基準 |
|---|---|---|
| サンプル数 | {bf['samples']} | {bb['samples']} |
| 平均 | {bf['mean_pct']}% | {bb['mean_pct']}% |
| 中央値 | {bf['median_pct']}% | {bb['median_pct']}% |
| p01 / p90 / p99 | {bf['p01_pct']} / {bf['p90_pct']} / {bf['p99_pct']} | {bb['p01_pct']} / {bb['p90_pct']} / {bb['p99_pct']} |
| 最小 / 最大 | {bf['min_pct']} / {bf['max_pct']} | {bb['min_pct']} / {bb['max_pct']} |
| \\|差\\|≥1.0% の時間 share | {bf['pct_time_abs_ge_1.0']}% | {bb['pct_time_abs_ge_1.0']}% |
| \\|差\\|≥0.5% の時間 share | {bf['pct_time_abs_ge_0.5']}% | {bb['pct_time_abs_ge_0.5']}% |

## 1%以上が2.0時間以上持続した局面（受入判定の本数）
- bitFlyer基準: **{bf['episodes_ge_2_0h']} 局面**（正 {bf['episodes_ge_2_0h_positive']} / 負 {bf['episodes_ge_2_0h_negative']}）
- bitbank基準: **{bb['episodes_ge_2_0h']} 局面**（正 {bb['episodes_ge_2_0h_positive']} / 負 {bb['episodes_ge_2_0h_negative']}）
- 感度（参考・判定不変）: ≥3.0h bitFlyer {bf['episodes_ge_3_0h']} / bitbank {bb['episodes_ge_3_0h']} 〜 ≥4.0h bitFlyer {bf['episodes_ge_4_0h']} / bitbank {bb['episodes_ge_4_0h']}

### bitFlyer基準の局面一覧（JST開始・持続・向き・ピーク・サンプル数）
| JST開始 | 持続 | 向き | ピーク | サンプル |
|---|---|---|---|---|
{rows_of(bf)}

### bitbank基準の局面一覧
| JST開始 | 持続 | 向き | ピーク | サンプル |
|---|---|---|---|---|
{rows_of(bb)}

## 時間帯別（JST時刻別の平均 premium_pct）
- bitFlyer基準: {analysis['jst_hour_mean_premium']['bitflyer']}
- bitbank基準: {analysis['jst_hour_mean_premium']['bitbank']}

## 判定（凍結規則の適用）
- **{analysis['verdict']}**

## 限界（判定文に必ず添える）
"""
    md += "\n".join(f"- {x}" for x in analysis["limitations"]) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(md, encoding="utf-8")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _parse(argv):
    ap = argparse.ArgumentParser(description="J1 円建て価格差観測（読み取りのみ・注文0）")
    ap.add_argument("--data-dir", default="/root/current-work/yowayowa-investor/data/s07_paper")
    ap.add_argument("--interval", type=int, default=INTERVAL_SEC)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--sample-once", action="store_true", help="1回だけ取得して追記")
    g.add_argument("--daily-hook", action="store_true", help="取得＋ループ確保（日次ジョブ用）")
    g.add_argument("--loop", action="store_true", help="収集ループ（内部用・flock単一・00:52UTCで自己終了）")
    g.add_argument("--status", action="store_true")
    g.add_argument("--report", action="store_true", help="凍結ルールで集計しレポート生成")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    data_dir = Path(args.data_dir)
    if args.sample_once:
        row = sample_once()
        path = top_dir(data_dir) / "snapshots" / f"{_ms_day(int(time.time() * 1000))}.jsonl"
        _append_jsonl(path, row)
        print(json.dumps({"status": "ok", "at_utc": row["at_utc"], "premium_pct": row["premium_pct"]},
                         ensure_ascii=False))
        return 0
    if args.daily_hook:
        print(json.dumps(daily_hook(data_dir), ensure_ascii=False))
        return 0
    if args.loop:
        return collect_loop(data_dir, interval_sec=args.interval)
    if args.status:
        return cmd_status(data_dir)
    if args.report:
        analysis = analyze(data_dir)
        _write_atomic(top_dir(data_dir) / "analysis.json", analysis)
        write_report(top_dir(data_dir) / "reports" / "REPORT_JPY_PREMIUM.md", analysis)
        print(json.dumps({"status": "ok", "verdict": analysis["verdict"],
                          "episodes_ge_2_0h": {"bitflyer": analysis["bitflyer_base"]["episodes_ge_2_0h"],
                                               "bitbank": analysis["bitbank_base"]["episodes_ge_2_0h"]},
                          "rows_total": analysis["coverage"]["rows_total"]}, ensure_ascii=False))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
