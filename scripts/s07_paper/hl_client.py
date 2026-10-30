"""Hyperliquid 公開API（読み取り専用）クライアント — S-07 ペーパー用。

設計上の約束:
  * 認証なし。秘密鍵・APIウォレット・署名は一切使わない。
  * 使うのは `POST https://api.hyperliquid.xyz/info` のみ（公開情報エンドポイント）。
  * 注文系エンドポイント（/exchange）は呼ばない＝実弾0・注文0。

既存 yowayowa コードには一切依存しない新規ファイル。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Iterable

INFO_URL = "https://api.hyperliquid.xyz/info"
USER_AGENT = "yowayowa-s07-paper/1.0 (read-only)"


class HLError(RuntimeError):
    """公開APIが想定外の応答を返したときに投げる。"""


def _post(payload: dict[str, Any], *, timeout: float = 30.0, retries: int = 3) -> Any:
    body = json.dumps(payload).encode("utf-8")
    last: Exception | None = None
    for attempt in range(retries):
        req = urllib.request.Request(
            INFO_URL,
            data=body,
            headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                return json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as exc:  # 422 などは即座に失敗させる
            detail = exc.read()[:200].decode("utf-8", "replace")
            last = HLError(f"HTTP {exc.code}: {detail}")
            if 400 <= exc.code < 500:
                raise last from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last = exc
        time.sleep(1.5 * (attempt + 1))
    raise HLError(f"Hyperliquid info request failed after {retries} tries: {last}")


def fetch_candles(
    coin: str = "BTC",
    interval: str = "1d",
    start_ms: int | None = None,
    end_ms: int | None = None,
) -> list[dict[str, float | int]]:
    """日足などのOHLCVを昇順で返す（正規化済み）。"""
    req: dict[str, Any] = {"coin": coin, "interval": interval}
    if start_ms is not None:
        req["startTime"] = int(start_ms)
    if end_ms is not None:
        req["endTime"] = int(end_ms)
    raw = _post({"type": "candleSnapshot", "req": req})
    if not isinstance(raw, list):
        raise HLError(f"unexpected candle payload: {type(raw)}")
    out: list[dict[str, float | int]] = []
    for c in raw:
        out.append(
            {
                "t": int(c["t"]),
                "T": int(c["T"]),
                "o": float(c["o"]),
                "h": float(c["h"]),
                "l": float(c["l"]),
                "c": float(c["c"]),
                "v": float(c["v"]),
                "n": int(c.get("n", 0)),
            }
        )
    out.sort(key=lambda x: x["t"])
    return out


def fetch_funding(
    coin: str = "BTC",
    start_ms: int | None = None,
    end_ms: int | None = None,
) -> list[dict[str, float | int]]:
    """資金調達率（funding）の履歴を時刻昇順で返す。HLは1時間ごと。"""
    payload: dict[str, Any] = {"type": "fundingHistory", "coin": coin}
    if start_ms is not None:
        payload["startTime"] = int(start_ms)
    if end_ms is not None:
        payload["endTime"] = int(end_ms)
    raw = _post(payload)
    if not isinstance(raw, list):
        raise HLError(f"unexpected funding payload: {type(raw)}")
    out = [
        {"time": int(r["time"]), "rate": float(r["fundingRate"])}
        for r in raw
        if r.get("fundingRate") is not None
    ]
    out.sort(key=lambda x: x["time"])
    return out


def fetch_l2_book(coin: str = "BTC") -> dict[str, float]:
    """最良気配とスプレッド(bps)。約定時スリッページ実測に使う。"""
    raw = _post({"type": "l2Book", "coin": coin})
    levels = raw.get("levels") or [[], []]
    bids, asks = levels[0], levels[1]
    if not bids or not asks:
        raise HLError("empty L2 book")
    bid = float(bids[0]["px"])
    ask = float(asks[0]["px"])
    mid = (bid + ask) / 2.0
    return {
        "bid": bid,
        "ask": ask,
        "mid": mid,
        "spread_bps": (ask - bid) / mid * 10_000.0,
        "half_spread_bps": (ask - bid) / 2.0 / mid * 10_000.0,
        "ts": int(time.time() * 1000),
    }


def fetch_asset_meta(coin: str = "BTC") -> dict[str, Any]:
    """szDecimals と maxLeverage（容量チェック用・発注はしない）。"""
    raw = _post({"type": "meta"})
    for asset in raw.get("universe", []):
        if asset.get("name") == coin:
            return {
                "coin": coin,
                "szDecimals": int(asset["szDecimals"]),
                "maxLeverage": int(asset["maxLeverage"]),
                "onlyIsolated": bool(asset.get("onlyIsolated", False)),
            }
    raise HLError(f"coin {coin} not found in meta universe")


def chunked(rng: Iterable[int], size: int) -> list[list[int]]:
    """API範囲分割が必要になったとき用の小道具。"""
    out: list[list[int]] = []
    buf: list[int] = []
    for v in rng:
        buf.append(v)
        if len(buf) >= size:
            out.append(buf)
            buf = []
    if buf:
        out.append(buf)
    return out
