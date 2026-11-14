#!/usr/bin/env python3
"""S-07 データ取得器 — Hyperliquid 公開APIから日足とfunding履歴を取る。

読み取り専用。キー不要。既存データがあれば差分だけ取り、日付キーで冪等にマージする。

使い方:
    python3 fetch_s07_data.py                 # 差分取得（初回は全履歴）
    python3 fetch_s07_data.py --full          # 全履歴を取り直す
    python3 fetch_s07_data.py --json          # 結果を1行JSONで標準出力（cron用）

注意: fundingHistory は1回500点までしか返さないためページングする。
      日足も念のため2000本ずつ区切って取る（API上限に当たらないように）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hl_client  # noqa: E402

MS_PER_DAY = 86_400_000
MS_PER_HOUR = 3_600_000
FULL_HISTORY_START_MS = 1_520_000_000_000  # 2018-03 ごろ。HLの最古より前でよい
FUNDING_PAGE = 500
FUNDING_LOOKBACK_DAYS_DEFAULT = 220
DEFAULT_DATA_DIR = Path("/root/current-work/yowayowa-investor/data/s07_paper")


def _load(path: Path, key: str) -> dict:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}
    return raw.get(key, raw if isinstance(raw, dict) else {})


def _write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, separators=(",", ":"), sort_keys=True))
    os.replace(tmp, path)


def _fetch_candles_paged(start_ms: int, end_ms: int) -> list[dict]:
    out: list[dict] = []
    cursor = start_ms
    for _ in range(40):  # 安全弁
        batch = hl_client.fetch_candles("BTC", "1d", cursor, end_ms)
        if not batch:
            break
        out.extend(batch)
        last = batch[-1]["t"]
        if last >= end_ms - MS_PER_DAY:
            break
        cursor = last + MS_PER_DAY
        time.sleep(0.2)
    return out


def _fetch_funding_paged(start_ms: int, end_ms: int, max_calls: int = 60) -> list[dict]:
    out: list[dict] = []
    cursor = start_ms
    for _ in range(max_calls):
        batch = hl_client.fetch_funding("BTC", cursor, end_ms)
        if not batch:
            break
        out.extend(batch)
        last = batch[-1]["time"]
        if last >= end_ms - MS_PER_HOUR:
            break
        cursor = last + 1
        if len(batch) < FUNDING_PAGE:  # 端まで来た
            if last >= end_ms - 2 * MS_PER_HOUR:
                break
        time.sleep(0.2)
    return out


def fetch(data_dir: Path, *, full: bool = False, funding_days: int = FUNDING_LOOKBACK_DAYS_DEFAULT) -> dict:
    candles_path = data_dir / "candles_1d.json"
    funding_path = data_dir / "funding_1h.json"

    candles = {} if full else _load(candles_path, "candles")
    funding = {} if full else _load(funding_path, "funding")

    now_ms = int(time.time() * 1000)

    # --- 日足（全履歴。差分時は3日ぶん重ねる） ---
    if candles:
        last_t = max(int(k) for k in candles)
        start = max(FULL_HISTORY_START_MS, last_t - 3 * MS_PER_DAY)
    else:
        start = FULL_HISTORY_START_MS
    new_candles = _fetch_candles_paged(start, now_ms)
    for c in new_candles:
        candles[str(c["t"])] = c

    # --- funding（直近 funding_days 日ぶんを維持。1回500点なのでページング） ---
    floor_ms = now_ms - funding_days * MS_PER_DAY
    if funding:
        last_f = max(int(k) for k in funding)
        existing_min = min(int(k) for k in funding)
        floor_ms = max(floor_ms, existing_min)
        fstart = max(floor_ms, last_f - 6 * MS_PER_HOUR)
    else:
        fstart = floor_ms
    new_funding = _fetch_funding_paged(fstart, now_ms)
    for f in new_funding:
        funding[str(f["time"])] = f

    _write_atomic(
        candles_path,
        {
            "coin": "BTC",
            "interval": "1d",
            "source": "POST https://api.hyperliquid.xyz/info type=candleSnapshot",
            "fetched_at": now_ms,
            "candles": candles,
        },
    )
    _write_atomic(
        funding_path,
        {
            "coin": "BTC",
            "interval": "1h",
            "source": "POST https://api.hyperliquid.xyz/info type=fundingHistory",
            "fetched_at": now_ms,
            "funding": funding,
        },
    )

    try:
        meta = hl_client.fetch_asset_meta("BTC")
    except Exception as exc:  # noqa: BLE001 - meta は補助情報なので失敗しても続行
        meta = {"error": str(exc)}

    c_times = sorted(int(k) for k in candles)
    f_times = sorted(int(k) for k in funding)
    return {
        "candles_total": len(candles),
        "candles_new": len(new_candles),
        "candles_range": [
            _iso(c_times[0]) if c_times else None,
            _iso(c_times[-1]) if c_times else None,
        ],
        "funding_total": len(funding),
        "funding_new": len(new_funding),
        "funding_range": [
            _iso(f_times[0]) if f_times else None,
            _iso(f_times[-1]) if f_times else None,
        ],
        "meta": meta,
        "data_dir": str(data_dir),
        "bytes": {
            "candles": candles_path.stat().st_size,
            "funding": funding_path.stat().st_size,
        },
    }


def _iso(ms: int) -> str:
    import datetime as dt

    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="S-07 HL public data fetcher (read-only)")
    ap.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    ap.add_argument("--full", action="store_true", help="既存を捨てて全履歴を取り直す")
    ap.add_argument("--funding-days", type=int, default=FUNDING_LOOKBACK_DAYS_DEFAULT)
    ap.add_argument("--json", action="store_true", help="結果を1行JSONで出力")
    args = ap.parse_args(argv)

    try:
        summary = fetch(Path(args.data_dir), full=args.full, funding_days=args.funding_days)
    except Exception as exc:  # noqa: BLE001
        print(f"FETCH_FAILED: {exc}", file=sys.stderr)
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1

    summary["ok"] = True
    if args.json:
        print(json.dumps(summary, ensure_ascii=False))
    else:
        print(f"日足: {summary['candles_total']}本 (新規{summary['candles_new']}) "
              f"{summary['candles_range'][0]} 〜 {summary['candles_range'][1]}")
        print(f"funding: {summary['funding_total']}点 (新規{summary['funding_new']}) "
              f"{summary['funding_range'][0]} 〜 {summary['funding_range'][1]}")
        print(f"meta: {summary['meta']}")
        print(f"data_dir: {summary['data_dir']} "
              f"(candles {summary['bytes']['candles']}B / funding {summary['bytes']['funding']}B)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
