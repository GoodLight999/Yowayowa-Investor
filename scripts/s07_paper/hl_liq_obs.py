#!/usr/bin/env python3
"""【B2】HL 清算検知用データ追加 — 1m蜡烛・OI高頻度スナップショット・代理検知件数（読み取りのみ・実弾0・注文0）

カード t_7a53946d（2026-11-06・親 t_b6e4bec8 = CIOレビューの実測に基づく純追記）。
既存 S-07 本体ロジックは変更しない: hl_funding_top20.py の run() から best-effort で `enrich()`
が呼ばれて day ファイルへ B2 セクションを追記する（失敗しても B1 の記録を落とさない）。
単独実行（バックフィル・キャリブレーション・OIループ・状態表示）も可。

## 名称の約束（必ず「代理検知件数」と明記する）
- 本モジュールが数えるのは **代理検知件数**。清算の実値ではない。
  公式APIに市場全体の清算履歴は無い（type=liquidations は deserialization 失敗・t_b6e4bec8 実測）。
  第三者データ（Hydromancer 等・キー必要）の採用はマスター裁可待ちのため、ここでは取らない。
- 「OI低下」のOIは metaAndAssetCtxs の自前前向きスナップ（既定15分間隔・10〜30分枠内）。
  公的な OI 履歴 API は実測不在のため、これが唯一の OI 粒度。
- 代理検知の構成（t_b6e4bec8 の type定義を 1m 粒度へ置換）:
    typeB相当 = 前分終値比の下落 −3.0% 以下 かつ 出来高（1m）が出来高基準の 2.0倍 以上
                （親レビュー実測: 30日20銘柄1h粒度で95件/月の水準と同規則の連続計数）
    typeA相当 = 「長い下ヒレ」かつ前分比 −1.0% 以下 かつ 出来高 3.0倍 以上
                （1h粒度の60%脚は30日20銘柄0件 → 1mで置換）
    「長い下ヒレ」閾値 W は 12〜14日分の実データで **キャリブレーションしてから固定**:
      冻結ルール: W = clamp(p99.9[全銘柄×完全UTC日の1m lower_wick_ratio プール], 0.60, 0.98) を
      0.5%刻みで丸める（t_b6e4bec8指摘2）。
    OI低下 = 発生時刻を挟む前後スナップのOI変化率 ≤ −1.0%（暫定・OIスナップ欠測日は計上不能と明記）
- 4つの計数を毎日記録（すべて代理検知件数）:
    代理検知件数_typeA相当(1m)          … candle条件のみ（OI条件なしでの同時成立）
    代理検知件数_typeA相当(1m+OI低下)   … OI低下まで同時成立した分（本命）
    代理検知件数_typeB相当(1m)          … candle条件のみ（親レビューの95件/月比較用の継続計数）
    代理検知件数_typeB相当(1m+OI低下)   … OI低下まで同時成立した分（本命）

## 出力（funding_top20/ 配下・既存B1のファイルは変更しない）
  candles_1m/YYYY-MM-DD.json            1m蜡烛（UTC日キー・{coin: [candle...]}・冪等マージ）
  oi_snapshots/YYYY-MM-DD.jsonl         OIスナップショット（前向きのみ蓄積・flock単一ループ）
  oi_snapshots/collector.log            OI収集ループの稼働記録
  liquid_events.jsonl                   出来事台帳（検知イベント追記・event_id冪等・
                                        「保有中funding収支」欄を **予約** として含める: t_b6e4bec8指摘3）
  liquidation_thresholds.json           固定済み閾値（--calibrate で1回だけ書く・以後上書きしない）
  calibration_YYYY-MM-DD.json           キャリブレーションの計算物（監査用）

## 負荷
- 日次追記: 1m蜡烛 ≈ 20呼び ＋ funding観測（既存約40呼び）と OI スナップ 96/日（15分毎）。
  日次追記データ量は day ファイル `b2_liquidation_observation.vps_load` と `--status` で実測報告する。

## キャリブレーションは前向き蓄積後（重要な実測）
- 実測プローブ（2026-11-06・t_7a53946d作業中）: HL 1m蜡烛履歴は **約3.6日分しか存在しない**
  （startTime=14日前でも最早蜡烛は≈3.6日前。endTime単独は422）。母のレビュー「一回で約7日」の
  5,000点上限は座標点数上限であり履歴深度の保証ではない。
- よって「長い下ヒレ」閾値 W の12〜14日分実データによる固定は **前向き蓄積で日数を確保してから** 実施:
  `--calibrate` は complete_days < 12 の間は拒否する（provisional W=0.60 で走る期間は明記）。
  代理検知件数は凍結前は暫定閾値値として記録され、日ファイルに wick_status で明記される。
"""

from __future__ import annotations

import argparse
import bisect
import datetime as dt
import fcntl
import hashlib
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hl_client  # noqa: E402  既存の読み取り専用クライアントを再利用（新規基盤は作らない）

CARD = "t_7a53946d"
SCHEMA_VERSION = 1
TOP_DIR_NAME = "funding_top20"

PROXY_NAME = "代理検知件数（清算実値ではない）"
LEDGER_NAME = "出来事台帳（清算代理検知）"
RESERVED_FIELD = "holding_funding_pnl"
RESERVED_NOTE = "予約: 保有中funding収支欄（t_b6e4bec8指摘3・実装未着手・nullで予約）"

PROVISIONAL = {
    "wick_threshold": 0.60,          # 1h粒度の60%脚を暫定流用（1mではキャリブレーションで置換）
    "minute_drop_pct_typeA": -1.0,   # typeA: drop1%（t_b6e4bec8 の typeA と同一）
    "minute_drop_pct_typeB": -3.0,   # typeB: drop3%（t_b6e4bec8 の typeB と同一）
    "volume_mult_typeA": 3.0,        # typeA: vol3x
    "volume_mult_typeB": 2.0,        # typeB: vol2x
    "oi_drop_min_pct": 1.0,          # OI低下: 前後スナップで −1.0% 以下（暫定値）
}
WICK_RULE_TEXT = (
    "長い下ヒレ閾値 W = clamp( p99.9[ 全銘柄×完全UTC日の1m lower_wick_ratio プール ], 0.60, 0.98 ) "
    "を0.5%刻みで丸める（12〜14日分の実データでキャリブレーションしてから固定: t_b6e4bec8指摘2）"
)

OI_INTERVAL_SEC = int(os.environ.get("HL_OI_INTERVAL_SEC", "900"))   # 既定15分（10〜30分枠内）
OI_DEADLINE_UTC = "00:52"   # 毎UTC日 00:52 に自己終了（10:00 JST cron との重複回避）
OI_429_BACKOFF_BASE = 30.0
PAGE_WINDOW_MS = 4_800 * 60_000   # 4800分（≈3.35日）/呼び（実測上限≈5000点未満の安全弁）


def _utc_str(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _day_ms(day: str) -> int:
    return int(dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def _ms_day(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%d")


def top_dir(data_dir: Path) -> Path:
    return Path(data_dir) / TOP_DIR_NAME


def _write_atomic(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=False))
    os.replace(tmp, path)


# --------------------------------------------------------------------------
# 1m蜡烛（hl_client.fetch_candles を再利用・429時のみ自前バックオフ）
# --------------------------------------------------------------------------

def _fetch_candles_backoff(coin: str, start_ms: int, end_ms: int, *, max_tries: int = 4):
    last = None
    for attempt in range(1, max_tries + 1):
        try:
            return hl_client.fetch_candles(coin, "1m", start_ms=start_ms, end_ms=end_ms)
        except hl_client.HLError as exc:  # 429はここでバックオフ（hl_clientは4xx即raise）
            last = exc
            if "429" in str(exc):
                wait = OI_429_BACKOFF_BASE * attempt
                print(f"[B2] 429 backoff {wait:.0f}s (coin={coin}, attempt={attempt})", file=sys.stderr)
                time.sleep(wait)
                continue
            raise
    raise last


def candles_path(data_dir: Path, day: str) -> Path:
    return top_dir(data_dir) / "candles_1m" / f"{day}.json"


def load_candles_file(data_dir: Path, day: str) -> dict | None:
    p = candles_path(data_dir, day)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def collect_candles(data_dir: Path, coins: list[str], start_ms: int, end_ms: int,
                    *, page_sleep: float = 0.25, coin_sleep: float = 3.0, log=None) -> dict:
    """[start_ms, end_ms) の1m蜡烛を取得し UTC日ごと candles_1m/YYYY-MM-DD.json へ冪等マージ。
    実測プローブ（2026-11-06）: candleSnapshot は startTime 単独では無効で、開始時刻を過ぎた
    窓の**後側最大5000点程度**を返す（=窓幅>上限では最新側で打ち切り）＋ endTime 単独は 422。
    → 1呼びが上限内に収まる固定幅窓（PAGE_WINDOW_MS）で前進ページングする。読み取りのみ・注文0。"""
    ok, fail = 0, []
    span = max(1, end_ms - start_ms)
    max_pages = span // PAGE_WINDOW_MS + 20
    for coin in coins:
        cur = start_ms
        pages = 0
        while cur < end_ms and pages < max_pages:
            pages += 1
            page_end = min(cur + PAGE_WINDOW_MS, end_ms)
            try:
                batch = _fetch_candles_backoff(coin, cur, page_end)
            except Exception as exc:  # noqa: BLE001 - 欠測は明記して続行（1銘柄で全体を殺さない）
                fail.append({"coin": coin, "cursor_utc": _utc_str(cur),
                             "error": f"{type(exc).__name__}: {exc}"[:200]})
                cur = page_end
                continue
            if batch:
                by_day: dict[str, list[dict]] = {}
                for c in batch:
                    t = int(c["t"])
                    by_day.setdefault(_ms_day(t), []).append(
                        {"t": t, "o": float(c["o"]), "h": float(c["h"]), "l": float(c["l"]),
                         "c": float(c["c"]), "v": float(c["v"])}
                    )
                for day, rows in by_day.items():
                    rows.sort(key=lambda r: r["t"])
                    _merge_candles(coin, day, rows, data_dir)
            cur = page_end
            time.sleep(page_sleep)
        ok += 1
        time.sleep(coin_sleep)
    return {"coins_ok": ok, "coins_failed": fail}


def _merge_candles(coin: str, day: str, rows: list[dict], data_dir: Path) -> int:
    path = candles_path(data_dir, day)
    rec = load_candles_file(data_dir, day) or {
        "schema_version": SCHEMA_VERSION, "card": CARD, "day_key": day, "interval": "1m",
        "source": "POST https://api.hyperliquid.xyz/info type=candleSnapshot 読み取りのみ",
        "coins": {},
    }
    coins_map = rec.setdefault("coins", {})
    existing: dict[int, dict] = {int(r["t"]): r for r in coins_map.get(coin, [])}
    for r in rows:
        existing[int(r["t"])] = r
    coins_map[coin] = [existing[t] for t in sorted(existing)]
    rec["updated_at_utc"] = _utc_str(int(time.time() * 1000))
    rec["coin_minute_counts"] = {c: len(v) for c, v in coins_map.items()}
    rec["day_completeness"] = {"expected_minutes": 1440,
                               "min_coin_minutes": min((len(v) for v in coins_map.values()), default=0)}
    _write_atomic(path, rec)
    return len(coins_map[coin])


def day_median_minutes(rec: dict) -> float:
    counts = rec.get("coin_minute_counts") or {}
    if not counts:
        return 0.0
    return statistics.median(counts.values())


def latest_complete_day(data_dir: Path, *, min_minutes: int = 1300) -> str | None:
    """candles_1m 配下の最も新しい「ほぼ完全」な UTC 日を返す（当日の部分日は飛ばして旧日へ）。"""
    pdir = top_dir(data_dir) / "candles_1m"
    if not pdir.exists():
        return None
    for p in sorted(pdir.glob("*.json"), reverse=True):
        rec = load_candles_file(data_dir, p.stem)
        if rec is None:
            continue
        if day_median_minutes(rec) >= min_minutes:
            return p.stem
    return None


# --------------------------------------------------------------------------
# OI高頻度スナップショット（前向き・15分毎・flockで単一ループ）
# --------------------------------------------------------------------------

def latest_day_picks(data_dir: Path) -> list[str]:
    days_dir = top_dir(data_dir) / "days"
    if not days_dir.exists():
        return []
    for p in sorted(days_dir.glob("*.json"), reverse=True):
        try:
            rec = json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        names = [c.get("coin") for c in (rec.get("coins") or []) if c.get("coin")]
        if names:
            return names
    return []


def complete_day_picks(data_dir: Path, complete_day: str | None) -> list[str]:
    """代理検知の集計宇宙: 対象の「その日の上位20銘柄」（dayファイルから）。無ければ空列（全銘柄扱い）。"""
    if not complete_day:
        return []
    p = top_dir(data_dir) / "days" / f"{complete_day}.json"
    if not p.exists():
        return []
    try:
        rec = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    return [c.get("coin") for c in (rec.get("coins") or []) if c.get("coin")]


def oi_snapshot_once(coins: list[str]) -> dict:
    """type=metaAndAssetCtxs を1回呼び、上位20銘柄分のOI等を1行返す（読み取りのみ）。"""
    raw = hl_client._post({"type": "metaAndAssetCtxs"})
    if not (isinstance(raw, list) and len(raw) == 2):
        raise hl_client.HLError(f"unexpected metaAndAssetCtxs payload: {type(raw)}")
    ctx_by_coin = {a["name"]: c for a, c in zip(raw[0]["universe"], raw[1]) if a.get("name")}
    now_ms = int(time.time() * 1000)
    row = {"at_utc": _utc_str(now_ms), "card": CARD,
           "interval_note": f"10〜30分間隔設計（設定 {OI_INTERVAL_SEC}s）", "coins": {}}
    for coin in coins:
        ctx = ctx_by_coin.get(coin)
        if not ctx:
            row["coins"][coin] = None
            continue
        try:
            mark = float(ctx.get("markPx") or 0.0)
            oi = float(ctx.get("openInterest") or 0.0)
            vlm = float(ctx.get("dayNtlVlm") or 0.0)
            rate = float(ctx.get("funding"))
        except (KeyError, TypeError, ValueError):
            row["coins"][coin] = None
            continue
        row["coins"][coin] = {
            "mark_px": mark,
            "open_interest_usd": round(oi * mark, 2),
            "day_ntl_vlm_usd": round(vlm, 2),
            "funding_rate_hourly": rate,
        }
    return row


def _append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        f.flush()
        os.fsync(f.fileno())


def collect_oi_loop(data_dir: Path, *, interval_sec: int = OI_INTERVAL_SEC) -> int:
    """OIスナップ収集ループ（デタッチ子プロセス用）。flock で単一・00:52UTCに自己終了。"""
    top_dir(data_dir).mkdir(parents=True, exist_ok=True)
    lock_path = top_dir(data_dir) / "oi_collect.lock"
    fh = open(lock_path, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("OI collector: already running（既存ループがいるので何もしない）")
        return 0
    now = dt.datetime.now(dt.timezone.utc)
    deadline = dt.datetime.combine(now.date() + dt.timedelta(days=1),
                                   dt.time.fromisoformat(OI_DEADLINE_UTC), tzinfo=dt.timezone.utc)
    log_path = top_dir(data_dir) / "oi_snapshots" / "collector.log"

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
            coins = latest_day_picks(data_dir)
            if not coins:
                log("no day file yet: 1周期待機")
            else:
                row = oi_snapshot_once(coins)
                path = top_dir(data_dir) / "oi_snapshots" / f"{_ms_day(int(time.time() * 1000))}.jsonl"
                _append_jsonl(path, row)
                snap_count += 1
        except Exception as exc:  # noqa: BLE001 - 1回の失敗でループは止めない（HL公開APIの読取）
            log(f"snapshot_fail {type(exc).__name__}: {str(exc)[:200]}")
            time.sleep(OI_429_BACKOFF_BASE)
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


def collector_alive(data_dir: Path) -> bool:
    lock_path = top_dir(data_dir) / "oi_collect.lock"
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


def ensure_oi_collector(data_dir: Path, *, interval_sec: int = OI_INTERVAL_SEC) -> dict:
    """日次ジョブ内から OI 収集ループを確実に走らせる。自前 flock で二重起動なし。読み取りのみ。"""
    if collector_alive(data_dir):
        return {"status": "already_running", "spawned": False, "interval_sec": interval_sec}
    log_path = top_dir(data_dir) / "oi_snapshots" / "collector.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logfh = open(log_path, "a", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--oi-loop",
         "--data-dir", str(data_dir), "--oi-interval", str(interval_sec)],
        stdout=logfh, stderr=logfh, stdin=subprocess.DEVNULL,
        start_new_session=True, cwd=str(Path(__file__).resolve().parent),
    )
    pid = proc.pid
    logfh.close()
    _write_atomic(top_dir(data_dir) / "oi_snapshots" / "collector.pid",
                  {"pid": pid, "started_at_utc": _utc_str(int(time.time() * 1000)),
                   "interval_sec": interval_sec, "card": CARD})
    return {"status": "spawned", "spawned": True, "pid": pid, "interval_sec": interval_sec}


# --------------------------------------------------------------------------
# OI の読み出しと OI低下判定
# --------------------------------------------------------------------------

def load_oi_day(data_dir: Path, day: str) -> list[dict]:
    p = top_dir(data_dir) / "oi_snapshots" / f"{day}.jsonl"
    if not p.exists():
        return []
    rows: list[dict] = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    rows.sort(key=lambda r: r.get("at_utc", ""))
    return rows


def oi_snapshot_row_ms(row: dict) -> int | None:
    try:
        return int(dt.datetime.fromisoformat(row["at_utc"].replace("Z", "+00:00")).timestamp() * 1000)
    except (KeyError, ValueError):
        return None


def oi_span_daily(rows: list[dict], *, day: str | None = None) -> dict:
    if not rows:
        return {"count": 0, "day": day,
                "note": "OIスナップショットなし（自前前向き蓄積の初日以前=欠測・OI低下条件は計上不能）"}
    ts = [m for m in (oi_snapshot_row_ms(r) for r in rows) if m is not None]
    diffs = [b - a for a, b in zip(ts, ts[1:]) if b > a]
    out = {"count": len(rows),
           "first_utc": _utc_str(ts[0]) if ts else None,
           "last_utc": _utc_str(ts[-1]) if ts else None,
           "median_interval_seconds": round(statistics.median(diffs) / 1000, 1) if diffs else None,
           "note": "公的OI履歴APIは実測不在のため自前前向き蓄積（唯一のOI粒度）"}
    if day:
        out["day"] = day
    return out


def _oi_series_for_coin(rows: list[dict], coin: str) -> list[tuple[int, float]]:
    out: list[tuple[int, float]] = []
    for r in rows:
        c = (r.get("coins") or {}).get(coin)
        if isinstance(c, dict) and c.get("open_interest_usd") is not None:
            m = oi_snapshot_row_ms(r)
            if m is not None:
                out.append((m, float(c["open_interest_usd"])))
    out.sort(key=lambda x: x[0])
    return out


def oi_drop_for(series: list[tuple[int, float]], t_ms: int, *, window_ms: int = 120 * 60_000) -> dict:
    """時点 t_ms を挟む「直前スナップ vs 直後スナップ」のOI変化率（%）。欠測は欠測と明記。"""
    if not series:
        return {"status": "欠測", "change_pct": None, "note": "OIスナップが無い（前向き蓄積の初日以前）"}
    ts = [s[0] for s in series]
    i = bisect.bisect_right(ts, t_ms)
    pre = ts[i - 1] if i > 0 else None
    post = ts[i] if i < len(ts) else None
    # 直近であっても「発生に隣接する」窓内のみ採用（120分前・120分後を既定とする）
    if pre is not None and pre < t_ms - window_ms:
        pre = None
    if post is not None and post > t_ms + window_ms:
        post = None
    if pre is None or post is None:
        return {"status": "欠測", "change_pct": None, "note": "窓内の前後スナップが揃わない"}
    oi_pre, oi_post = series[i - 1][1], series[i][1]
    if oi_pre <= 0:
        return {"status": "欠測", "change_pct": None, "note": "前スナップOIが0/負"}
    chg = (oi_post - oi_pre) / oi_pre * 100.0
    return {"status": "ok", "change_pct": round(chg, 3),
            "pre_utc": _utc_str(pre), "post_utc": _utc_str(post)}


# --------------------------------------------------------------------------
# 代理検知の集計（清算実値ではない）
# --------------------------------------------------------------------------

def lower_wick_ratio(open_, high, low, close) -> float:
    rng = high - low
    if rng <= 0:
        return 0.0
    return (min(open_, close) - low) / rng


def thresholds_block(data_dir: Path) -> dict:
    p = top_dir(data_dir) / "liquidation_thresholds.json"
    frozen = None
    if p.exists():
        try:
            frozen = json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            frozen = None
    if frozen and frozen.get("frozen"):
        return {
            "source": "frozen: liquidation_thresholds.json（以後上書きしない）",
            "wick_threshold": frozen.get("wick_threshold"),
            "wick_status": frozen.get("wick_status"),
            "wick_rule": frozen.get("wick_rule", WICK_RULE_TEXT),
            "minute_drop_pct_typeA": frozen.get("minute_drop_pct_typeA", PROVISIONAL["minute_drop_pct_typeA"]),
            "minute_drop_pct_typeB": frozen.get("minute_drop_pct_typeB", PROVISIONAL["minute_drop_pct_typeB"]),
            "volume_mult_typeA": frozen.get("volume_mult_typeA", PROVISIONAL["volume_mult_typeA"]),
            "volume_mult_typeB": frozen.get("volume_mult_typeB", PROVISIONAL["volume_mult_typeB"]),
            "oi_drop_min_pct": frozen.get("oi_drop_min_pct", PROVISIONAL["oi_drop_min_pct"]),
            "oi_drop_status": frozen.get("oi_drop_status", "provisional"),
            "volume_baseline": "同日・同銘柄の1m出来高の中央値（決定的）",
        }
    out = dict(PROVISIONAL)
    out["source"] = "provisional（--calibrate で12〜14日分の実データから1回だけ凍結する）"
    out["wick_rule"] = WICK_RULE_TEXT
    out["volume_baseline"] = "同日・同銘柄の1m出来高の中央値（決定的）"
    return out


def build_events_for_day(candles_by_coin: dict, oi_rows: list[dict], thr: dict) -> list[dict]:
    """完全UTC日の1m蜡烛＋同日のOIスナップから「出来高急増＋長い下ヒレ＋OI低下」の同時発生を集計。
    決定的（乱数なし・同一入力は同一出力）。戻り値は kind ごとに独立したイベント列。"""
    dropA = float(thr["minute_drop_pct_typeA"])
    dropB = float(thr["minute_drop_pct_typeB"])
    vmA = float(thr["volume_mult_typeA"])
    vmB = float(thr["volume_mult_typeB"])
    wick_thr = float(thr["wick_threshold"])
    oi_thr = float(thr["oi_drop_min_pct"])
    oi_series_cache: dict[str, list[tuple[int, float]]] = {}
    events: list[dict] = []
    for coin in sorted(candles_by_coin):
        candles = candles_by_coin[coin]
        if not candles:
            continue
        base_v = statistics.median([float(c["v"]) for c in candles if float(c["v"]) > 0] or [0.0])
        if base_v <= 0:
            continue
        if coin not in oi_series_cache:
            oi_series_cache[coin] = _oi_series_for_coin(oi_rows, coin)
        series = oi_series_cache[coin]
        for i in range(1, len(candles)):
            cur, prev = candles[i], candles[i - 1]
            prev_c = float(prev["c"])
            if prev_c <= 0:
                continue
            cur_c = float(cur["c"])
            minute_drop = (cur_c / prev_c - 1) * 100.0
            v = float(cur["v"])
            vol_ratio = v / base_v
            lw = lower_wick_ratio(float(cur["o"]), float(cur["h"]), float(cur["l"]), cur_c)
            t_ms = int(cur["t"])
            matches = []
            if minute_drop <= dropB and v >= vmB * base_v:
                matches.append("typeB_eq")
            if lw >= wick_thr and minute_drop <= dropA and v >= vmA * base_v:
                matches.append("typeA_eq")
            if not matches:
                continue
            oi = oi_drop_for(series, t_ms)
            oi_cond = isinstance(oi.get("change_pct"), (int, float)) and oi["change_pct"] <= -oi_thr
            for kind in matches:
                events.append({
                    "event_id": hashlib.sha256(f"{kind}|{coin}|{t_ms}|{CARD}".encode()).hexdigest()[:16],
                    "kind": kind,
                    "coin": coin,
                    "t_utc": _utc_str(t_ms),
                    "day_utc": _ms_day(t_ms),
                    "o": cur["o"], "h": cur["h"], "l": cur["l"], "c": cur["c"], "v": v,
                    "volume_ratio": round(vol_ratio, 4),
                    "minute_drop_pct": round(minute_drop, 4),
                    "lower_wick_ratio": round(lw, 4),
                    "oi": oi,
                    "oi_drop_condition_met": bool(oi_cond),
                    "proxy_name": PROXY_NAME,
                    "holding_funding_pnl": None,
                    "holding_funding_pnl_note": RESERVED_NOTE,
                    "card": CARD,
                })
    return events


def summarize_counts(events: list[dict]) -> dict:
    """代理検欠件数（清算実値ではない）: OI条件あり/なしの4計数。"""
    eA = [e for e in events if e["kind"] == "typeA_eq"]
    eB = [e for e in events if e["kind"] == "typeB_eq"]
    return {
        "name": PROXY_NAME,
        "代理検知件数_typeA相当_1m": len(eA),
        "代理検知件数_typeA相当_1m_OI低下含む": sum(1 for e in eA if e["oi_drop_condition_met"]),
        "代理検知件数_typeB相当_1m": len(eB),
        "代理検知件数_typeB相当_1m_OI低下含む": sum(1 for e in eB if e["oi_drop_condition_met"]),
        "typeA_note": "typeA相当（下ヒレ脚。1h粒度の60%脚は30日20銘柄0件・t_b6e4bec8実測。1mでは閾値キャリブレーション置換）",
        "typeB_note": "typeB相当（親レビュー実測95件/月水準と同規則・drop3%+vol2xを1mへ置換）",
        "oi_note": "OI低下は15分間隔スナップで評価（OIスナップが無い日は「計上不能」= なし扱いではない。欠測明記はoi配下）",
    }


def ledger_path(data_dir: Path) -> Path:
    return top_dir(data_dir) / "liquid_events.jsonl"


def _ledger_ids(path: Path) -> set[str]:
    ids: set[str] = set()
    if not path.exists():
        return ids
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            j = json.loads(line)
        except json.JSONDecodeError:
            continue
        if j.get("event_id"):
            ids.add(j["event_id"])
    return ids


def append_ledger(events: list[dict], data_dir: Path, *, max_append: int = 2000) -> dict:
    """出来事台帳（liquid_events.jsonl）へ追記。event_id冪等。「保有中funding収支」欄を予約で含める。"""
    path = ledger_path(data_dir)
    known = _ledger_ids(path)
    new = sorted([e for e in events if e["event_id"] not in known], key=lambda x: x["t_utc"])
    written = 0
    for e in new:
        if written >= max_append:
            break
        _append_jsonl(path, e)
        written += 1
    total = len(_ledger_ids(path))
    return {"name": LEDGER_NAME,
            "path": "funding_top20/liquid_events.jsonl",
            "appended_this_run": written,
            "total_events": total,
            "total_events_note": "全台帳行数（重複event_id排除後）",
            "reserved_column": {RESERVED_FIELD: None, "note": RESERVED_NOTE},
            "funding_pnl_status": "予約: 保有中のfunding収支欄（t_b6e4bec8指摘3・実装未着手）"}


# --------------------------------------------------------------------------
# 日次ランナーからの追記入口（hl_funding_top20.run() から呼ぶ）
# --------------------------------------------------------------------------

def vps_load(data_dir: Path) -> dict:
    """VPS負荷推計（1日追加データ量の実測）。日次 cron から毎日記録される。"""
    cs = top_dir(data_dir) / "candles_1m"
    candle_files = sorted(cs.glob("*.json")) if cs.exists() else []
    sizes = [p.stat().st_size for p in candle_files]
    per_day_kb = round(statistics.mean(sizes) / 1024, 1) if sizes else 0.0
    total_candle_mb = round(sum(sizes) / (1024 * 1024), 1)
    oi_day = _ms_day(int(time.time() * 1000))
    oi_rows = load_oi_day(data_dir, oi_day)
    oi_kb = round(sum(len(json.dumps(r, ensure_ascii=False, separators=(",", ":")))
                      for r in oi_rows) / 1024, 1)
    oi_per_snap_kb = round(oi_kb / len(oi_rows), 2) if oi_rows else None
    # 1日=96スナップ（15分毎）相当へ外挿
    oi_daily_kb = round((oi_per_snap_kb or 1.3) * 96, 1)
    return {
        "candles_files": len(candle_files),
        "candles_mean_file_kb": per_day_kb,
        "candles_total_mb": total_candle_mb,
        "oi_snapshots_today": len(oi_rows),
        "oi_kb_today": oi_kb,
        "oi_per_snapshot_kb": oi_per_snap_kb,
        "daily_increment_estimate_kb": round(per_day_kb + oi_daily_kb, 1),
        "estimate_note": ("1m: 当日∪前日の上位20（≤40銘柄）×1440点/日（1日1ファイル実測 candles_mean_file_kb）"
                          f" ＋ OIスナップ（{96}/日・外挿 {oi_daily_kb}KB/日）＋dayファイルへの軽量セクション追記。"
                          "日次追加API呼び出し≈136回（1m蜡烛≤40＋OI 96以外は既存B1）。28日で約0.2GB未満の見立て。"),
        "ultra_note": "heavy なし・LLM呼び出し0・TZ=UTC・冪等マージのため再実行でも増えない。",
    }


def enrich(data_dir: Path, record: dict, picks: list[dict], now_ms: int) -> dict:
    """dayファイルへの B2 セクションを組み立てる（冪等・決定的）。
    ①1m蜡烛取得マージ（前日+当日部分）②OIスナップ読み出し ③代理検知件数と出来事台帳
    ④OI収集ループ確保 ⑤VPS負荷実測を記録。"""
    data_dir = Path(data_dir)
    day_key = record.get("day_key") or _ms_day(now_ms)
    coins = [c["coin"] for c in picks]
    # 取得宇宙 = 当日の上位20 ∪ 前日の上位20（cardsの代理検知は「対象日のその日の上位20」が宇宙。
    # cron 実行は当日01:00 UTCで前日を完成させて確定させるため、前日の選出分も取得しないと
    # 前日の集計宇宙が欠ける（2026-11-06 実測で宇宙=8/20だったのを修正）。
    prev_day = (dt.date.fromisoformat(day_key) - dt.timedelta(days=1)).isoformat()
    prev_picks = [c for c in complete_day_picks(data_dir, prev_day) if c and c not in coins]
    coins_all = coins + prev_picks

    # ① 1m蜡烛: 前日00:00UTC〜現在まで取得・日ごとマージ（= 前日の完全1440点 + 当日の部分）
    start_ms = _day_ms(day_key) - 24 * 3_600_000
    candles_stat = collect_candles(data_dir, coins_all, start_ms, now_ms - 1)

    # ② OIスナップ: 当日 + 代理検知対象の完全UTC日
    complete_day = latest_complete_day(data_dir)
    oi_today = oi_span_daily(load_oi_day(data_dir, day_key), day=day_key)
    oi_src: dict
    if complete_day and complete_day != day_key:
        oi_src = oi_span_daily(load_oi_day(data_dir, complete_day), day=complete_day)
        oi_src["file"] = f"oi_snapshots/{complete_day}.jsonl"
    else:
        oi_src = oi_span_daily(load_oi_day(data_dir, day_key), day=day_key)
        oi_src["file"] = f"oi_snapshots/{day_key}.jsonl"

    # ③ 代理検知（清算実値ではない）: 完全UTC日に対して計算（当日はまだ未完成）
    thr = thresholds_block(data_dir)
    ledger_info: dict = {"name": LEDGER_NAME, "path": "funding_top20/liquid_events.jsonl",
                         "appended_this_run": 0,
                         "reserved_column": {RESERVED_FIELD: None, "note": RESERVED_NOTE}}
    complete_rec = load_candles_file(data_dir, complete_day) if complete_day else None
    if complete_rec:
        # 対象宇宙の限定: 完全UTC日の「その日の上位20銘柄」（dayファイルがあれば）に限る。
        # candle ファイルには宇宙の交代分が1日ずつ混在する（冪等マージ）ため、代理検知の集計宇宙を明示。
        universe = complete_day_picks(data_dir, complete_day)
        coins_sub = {c: v for c, v in (complete_rec.get("coins") or {}).items() if c in universe} if universe \
            else dict(complete_rec.get("coins") or {})
        oi_rows_src = load_oi_day(data_dir, complete_day) if complete_day != day_key \
            else load_oi_day(data_dir, day_key)
        events_daily = build_events_for_day(coins_sub, oi_rows_src, thr)
        counts = summarize_counts(events_daily)
        ledger_info.update(append_ledger(events_daily, data_dir))
        proxy = {
            "proxy_source_day": complete_day,
            "universe": f"その日（{complete_day}）の上位20銘柄（集計宇宙={len(coins_sub)}銘柄・dayファイル由来）",
            "proxy_note": "清算実値ではない（公式APIに市場全体の清算履歴なし: type=liquidations deserialization失敗・t_b6e4bec8実測）",
            "代理検知件数": counts,
            "events_head": [
                {k: e[k] for k in ("event_id", "kind", "coin", "t_utc", "minute_drop_pct",
                                   "volume_ratio", "lower_wick_ratio", "oi_drop_condition_met")}
                for e in sorted(events_daily, key=lambda x: x["t_utc"])[-8:]
            ],
            "formulas_b2": {
                "構成": "代理検知 = 出来高急増（基準=同日・同銘柄の1m出来高中央値）＋長い下ヒレ＋OI低下（前後スナップ比較）の同時発生・代理検知件数として記録",
                "typeA_eq": (f"lower_wick_ratio ≥ {float(thr['wick_threshold']):.2f} かつ minute_drop ≤ "
                             f"{float(thr['minute_drop_pct_typeA']):.1f}% かつ volume_ratio ≥ "
                             f"{float(thr['volume_mult_typeA']):.1f}x"),
                "typeB_eq": (f"minute_drop ≤ {float(thr['minute_drop_pct_typeB']):.1f}% かつ "
                             f"volume_ratio ≥ {float(thr['volume_mult_typeB']):.1f}x"),
                "oi_drop": f"oi_change_pct ≤ −{float(thr['oi_drop_min_pct']):.1f}%（{thr.get('oi_drop_status', 'provisional')}）",
                "wick_rule": thr.get("wick_rule", WICK_RULE_TEXT),
            },
        }
    else:
        proxy = {"proxy_source_day": None,
                 "proxy_note": "完全UTC日が1日分も未蓄積（翌日cron以降に自動で完成する）",
                 "代理検知件数": summarize_counts([])}

    # ④ 収集ループ確保（デタッチ子プロセス・flock二重起動なし）
    collector = ensure_oi_collector(data_dir)

    return {
        "card": CARD,
        "status": "ok",
        "collected_at_utc": _utc_str(now_ms),
        "candles_1m": {
            "interval": "1m",
            "daily_target": "1440点/銘柄（完全UTC日）・同日再実行は冪等マージ",
            "fetch_universe": f"当日の上位20 ∪ 前日の上位20 = {len(coins_all)}銘柄"
                              f"（代理検知の集計宇宙=対象日のその日の上位20）",
            "collection": candles_stat,
            "latest_complete_day": complete_day,
            "file_for_complete_day": f"candles_1m/{complete_day}.json" if complete_day else None,
            "coin_minute_counts_head": {k: v for k, v in sorted(
                (complete_rec or {}).get("coin_minute_counts", {}).items())[:5]} if complete_rec else {},
        },
        "oi_snapshots": {"today": oi_today, "source_day": oi_src},
        "liquidation_proxy": proxy,
        "events_ledger": ledger_info,
        "thresholds": thr,
        "collector": collector,
        "vps_load": vps_load(data_dir),
    }


# --------------------------------------------------------------------------
# 単独実行: --backfill / --calibrate / --status / --oi-loop
# --------------------------------------------------------------------------

def cmd_backfill(data_dir: Path, days: int) -> int:
    now_ms = int(time.time() * 1000)
    coins = latest_day_picks(data_dir)
    if not coins:
        print("B2 backfill: day file が無い（hl_funding_top20.py を先に実行して上位20を確定すること）")
        return 2
    start_ms = _day_ms(_ms_day(now_ms)) - days * 24 * 3_600_000
    print(f"B2 backfill: {len(coins)} coins × {days}d from {_utc_str(start_ms)}")
    res = collect_candles(data_dir, coins, start_ms, now_ms - 1)
    print(f"result: coins_ok={res['coins_ok']} failed={len(res['coins_failed'])}")
    if res["coins_failed"]:
        print(json.dumps(res["coins_failed"], ensure_ascii=False)[:1500])
    print(f"latest_complete_day={latest_complete_day(data_dir)}")
    return 0


def build_events_candle_only(complete_days: dict, thr: dict) -> list[dict]:
    """キャリブレーション感度表用: OI欠測（空列）で代理検知条件のcandle部分だけを数える（決定的）。"""
    events: list[dict] = []
    for day, rec in sorted(complete_days.items()):
        for e in build_events_for_day(rec.get("coins") or {}, [], thr):
            # 決定的に日を区別できるように day だけ接頭辞で落とす（集計用途・台帳には使わない）
            e["day_utc"] = day
            e["event_id"] = f"{day}:{e['event_id']}"
            events.append(e)
    return events


def cmd_calibrate(data_dir: Path) -> int:
    """凍結ルール: W = clamp(p99.9, 0.60, 0.98)（0.5%刻み丸め）。「長い下ヒレ」を固定する。
    完全UTC日の1m蜡烛のみ使用（部分日は除外・決定的・乱数なし）。1回だけ書き・以後上書きしない。"""
    tp = top_dir(data_dir) / "liquidation_thresholds.json"
    if tp.exists():
        try:
            existing = json.loads(tp.read_text())
        except (json.JSONDecodeError, OSError):
            existing = None
        if existing and existing.get("frozen"):
            print("B2 calibrate: 既に凍結済み（liquidation_thresholds.json・上書きしない）")
            print(json.dumps(existing, ensure_ascii=False, indent=1))
            return 0
    cs = top_dir(data_dir) / "candles_1m"
    files = sorted(cs.glob("*.json")) if cs.exists() else []
    complete: dict[str, dict] = {}
    pooled: list[float] = []
    per_day: dict[str, dict] = {}
    for p in files:
        rec = load_candles_file(data_dir, p.stem)
        if rec is None:
            continue
        if day_median_minutes(rec) < 1300:
            continue
        per_day[p.stem] = {"coins": len(rec.get("coin_minute_counts") or {}),
                           "median_minutes": round(day_median_minutes(rec), 1)}
        complete[p.stem] = rec
        for coin, cand in (rec.get("coins") or {}).items():
            for c in cand:
                pooled.append(lower_wick_ratio(c["o"], c["h"], c["l"], c["c"]))
    if not pooled or not complete:
        print("B2 calibrate: 完全な1m蜡烛が足りない（先に --backfill を実行すること）")
        return 1
    # カード規律: 「12〜14日分の実データでキャリブレーションしてから固定」（t_b6e4bec8指摘2）。
    # 実測（2026-11-06プローブ）: HL 1m蜡烛履歴は約3.6日分しかAPIに存在しない
    # （開始時刻を過去に置いても最早蜡烛は≈3.6日前 → 14日バックフィルは不可）。
    # よって12日以上は未来への日次蓄積で確保し、それ未満での凍結は拒否する。
    if len(complete) < 12:
        print(f"B2 calibrate: complete_days={len(complete)} < 12（カード規律のため凍結しない。"
              "1m蜡烛は前向き蓄積のみ可能なため、12〜14日後に再度実行すること）")
        return 2
    pooled.sort()

    def pct(q: float) -> float:
        i = min(len(pooled) - 1, max(0, int(round(q * (len(pooled) - 1)))))
        return pooled[i]

    w_raw = pct(0.999)
    W = max(0.60, min(0.98, round(w_raw * 200) / 200))
    # 感度承諾表: W の候補ごとの typeA 相当構成の件数（candle 条件のみ・OI欠測扱い）
    sens: dict[str, int] = {}
    for wq in sorted({0.60, 0.70, 0.80, 0.90, 0.95, round(W, 2)}):
        t = {**PROVISIONAL, "wick_threshold": wq}
        n = sum(1 for e in build_events_candle_only(complete, t) if e["kind"] == "typeA_eq")
        sens[str(wq)] = n
    stat = {
        "day_count_complete": len(complete),
        "per_day": per_day,
        "n_candles_pooled": len(pooled),
        "wick_ratio_p955": round(pct(0.955), 4),
        "p99": round(pct(0.990), 4),
        "p995": round(pct(0.995), 4),
        "p999": round(pct(0.999), 4),
        "p9999": round(pct(0.9999), 4),
        "p100": round(pooled[-1], 4),
        "wick_chosen": W,
        "wick_freeze_rule": WICK_RULE_TEXT,
        "sens_typeA_counts_by_wick": sens,
        "rule": "完全UTC日の1m蜡烛のみ・部分日除外・決定的計算（乱数なし）・凍結値は以後固定",
    }
    stamp = _ms_day(int(time.time() * 1000))
    _write_atomic(top_dir(data_dir) / f"calibration_{stamp}.json",
                  {"schema_version": SCHEMA_VERSION, "card": CARD, "day_key": stamp,
                   "generated_at_utc": _utc_str(int(time.time() * 1000)), **stat})
    thresholds = {
        "schema_version": SCHEMA_VERSION,
        "card": CARD,
        "frozen": True,
        "frozen_at_utc": _utc_str(int(time.time() * 1000)),
        "wick_threshold": W,
        "wick_status": "calibrated（12〜14日分の実データから --calibrate で一度固定・以後不変）",
        "wick_rule": WICK_RULE_TEXT,
        "minute_drop_pct_typeA": PROVISIONAL["minute_drop_pct_typeA"],
        "minute_drop_pct_typeB": PROVISIONAL["minute_drop_pct_typeB"],
        "volume_mult_typeA": PROVISIONAL["volume_mult_typeA"],
        "volume_mult_typeB": PROVISIONAL["volume_mult_typeB"],
        "oi_drop_min_pct": PROVISIONAL["oi_drop_min_pct"],
        "oi_drop_status": "provisional（1%は暫定・OIスナップ蓄積後に別カードで固定の可能性を明示）",
        "volume_baseline": "同日・同銘柄の1m出来高の中央値（決定的）",
        "day_count_used_calibration": len(complete),
    }
    _write_atomic(tp, thresholds)
    print(json.dumps({"calibrated": True, "wick_threshold": W, "complete_days": len(complete),
                      "n_candles": len(pooled)}, ensure_ascii=False))
    return 0


def cmd_status(data_dir: Path) -> int:
    today = _ms_day(int(time.time() * 1000))
    ledger_file = ledger_path(data_dir)
    rec = {
        "card": CARD,
        "now_utc": _utc_str(int(time.time() * 1000)),
        "candles_1m_days": [p.stem for p in sorted((top_dir(data_dir) / "candles_1m").glob("*.json"))][-40:]
        if (top_dir(data_dir) / "candles_1m").exists() else [],
        "oi_today": oi_span_daily(load_oi_day(data_dir, today), day=today),
        "oi_days": [p.stem for p in sorted((top_dir(data_dir) / "oi_snapshots").glob("*.jsonl"))]
        if (top_dir(data_dir) / "oi_snapshots").exists() else [],
        "thresholds": thresholds_block(data_dir),
        "collector_alive": collector_alive(data_dir),
        "ledger": {"exists": ledger_file.exists(),
                   "lines": sum(1 for _ in ledger_file.open("r")) if ledger_file.exists() else 0},
        "vps_load": vps_load(data_dir),
    }
    print(json.dumps(rec, ensure_ascii=False, indent=1, default=str))
    return 0


def _parse(argv):
    ap = argparse.ArgumentParser(description="【B2】清算検知用データ追加（代理検知・読み取りのみ・注文0）")
    ap.add_argument("--data-dir", default="/root/current-work/yowayowa-investor/data/s07_paper")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--backfill", type=int, default=None, metavar="DAYS",
                    help="直近DAYS日分の1m蜡烛を取得して candles_1m/ へ冪等マージ（12〜14推奨）")
    ap.add_argument("--calibrate", action="store_true",
                    help="完全UTC日の実データで「長い下ヒレ」閾値を凍結（1回だけ・以後不変）")
    ap.add_argument("--oi-loop", action="store_true",
                    help="OIスナップ収集ループ（内部用・flock単一・00:52UTCで自己終了）")
    ap.add_argument("--oi-interval", type=int, default=OI_INTERVAL_SEC)
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    data_dir = Path(args.data_dir)
    if args.oi_loop:
        return collect_oi_loop(data_dir, interval_sec=args.oi_interval)
    if args.status:
        return cmd_status(data_dir)
    if args.calibrate:
        return cmd_calibrate(data_dir)
    if args.backfill is not None:
        return cmd_backfill(data_dir, args.backfill)
    print("nothing to do: --status / --backfill N / --calibrate（日次追記は hl_funding_top20.py 経由の enrich()）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
