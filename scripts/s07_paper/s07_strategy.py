"""S-07 `hl_perp_btc_trend_funding` のシグナル計算（設計書 §4.3 準拠）。

入力は日足配列（昇順）と funding 履歴。出力は「当日終値時点の判断」だけを返し、
約定や損益は持たない（それは runner の責務）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

MS_PER_DAY = 86_400_000
MS_PER_HOUR = 3_600_000

DEFAULTS = {
    "sma_window": 50,
    "ema_fast": 10,
    "ema_slow": 20,
    "breakout_window": 10,
    "atr_window": 14,
    "funding_window_h": 24,
    "funding_block_ann_pct": 30.0,  # 年率 ±30% 超でエントリー遮断
    "stop_atr_mult": 1.8,
    "risk_per_trade": 0.010,        # 口座Equityの1.0%
    "max_leverage": 3.0,
    "min_notional_usdc": 10.0,
}


def sma(values: list[float], window: int, idx: int) -> float | None:
    if idx + 1 < window:
        return None
    return sum(values[idx + 1 - window : idx + 1]) / window


def ema_series(values: list[float], window: int) -> list[float | None]:
    """SMAを初期値にした標準的なEMA。先頭 window-1 個は None。"""
    out: list[float | None] = [None] * len(values)
    if len(values) < window:
        return out
    seed = sum(values[:window]) / window
    out[window - 1] = seed
    k = 2.0 / (window + 1.0)
    prev = seed
    for i in range(window, len(values)):
        prev = values[i] * k + prev * (1.0 - k)
        out[i] = prev
    return out


def atr_series(candles: list[dict[str, Any]], window: int) -> list[float | None]:
    """Wilder の ATR。"""
    n = len(candles)
    out: list[float | None] = [None] * n
    if n < 2:
        return out
    trs: list[float] = [0.0] * n
    for i in range(1, n):
        h, l, pc = candles[i]["h"], candles[i]["l"], candles[i - 1]["c"]
        trs[i] = max(h - l, abs(h - pc), abs(l - pc))
    if n <= window:
        return out
    first = sum(trs[1 : window + 1]) / window
    out[window] = first
    prev = first
    for i in range(window + 1, n):
        prev = (prev * (window - 1) + trs[i]) / window
        out[i] = prev
    return out


def funding_annualized_pct(
    funding: list[dict[str, Any]], day_end_ms: int, window_h: int = 24
) -> float | None:
    """day_end_ms 直前 window_h 時間の平均funding率を年率%に直す。"""
    lo = day_end_ms - window_h * MS_PER_HOUR
    rates: list[float] = [f["rate"] for f in funding if lo <= f["time"] < day_end_ms]
    if len(rates) < window_h // 2:  # 半分も揃わなければ判定不能
        return None
    mean_hourly = sum(rates) / len(rates)
    return mean_hourly * 24.0 * 365.0 * 100.0


@dataclass
class DayDecision:
    day: str
    idx: int
    close: float
    sma200: float | None
    ema20: float | None
    ema50: float | None
    atr14: float | None
    prev20_high: float | None
    prev20_low: float | None
    funding_ann_pct: float | None
    long_signal: bool
    short_signal: bool
    exit_long: bool
    exit_short: bool
    block_long: bool
    block_short: bool
    warm: bool
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "sma200": self.sma200,
            "ema20": self.ema20,
            "ema50": self.ema50,
            "atr14": self.atr14,
            "prev20_high": self.prev20_high,
            "prev20_low": self.prev20_low,
            "funding_ann_pct": self.funding_ann_pct,
            "long_signal": self.long_signal,
            "short_signal": self.short_signal,
            "exit_long": self.exit_long,
            "exit_short": self.exit_short,
            "block_long": self.block_long,
            "block_short": self.block_short,
        }


class Strategy:
    """日足配列に対する S-07 の判定器。指標は生成時に一度だけ計算する。"""

    def __init__(
        self,
        candles: list[dict[str, Any]],
        funding: list[dict[str, Any]],
        cfg: dict[str, Any] | None = None,
    ):
        self.candles = candles
        self.funding = funding
        self.cfg = {**DEFAULTS, **(cfg or {})}
        closes = [c["c"] for c in candles]
        self._sma = [sma(closes, self.cfg["sma_window"], i) for i in range(len(closes))]
        self._ema_fast = ema_series(closes, self.cfg["ema_fast"])
        self._ema_slow = ema_series(closes, self.cfg["ema_slow"])
        self._atr = atr_series(candles, self.cfg["atr_window"])

    def decide(self, idx: int) -> DayDecision:
        cfg = self.cfg
        c = self.candles[idx]
        w = cfg["breakout_window"]
        prev_high = prev_low = None
        if idx >= w:
            window = self.candles[idx - w : idx]
            prev_high = max(x["h"] for x in window)
            prev_low = min(x["l"] for x in window)

        day_end = c["t"] + MS_PER_DAY
        day = _day_str(c["t"])
        s200 = self._sma[idx]
        e20 = self._ema_fast[idx]
        e50 = self._ema_slow[idx]
        atr = self._atr[idx]
        fann = funding_annualized_pct(self.funding, day_end, cfg["funding_window_h"])

        warm = all(v is not None for v in (s200, e20, e50, atr, prev_high, prev_low))
        if not warm:
            return DayDecision(
                day, idx, c["c"], s200, e20, e50, atr, prev_high, prev_low, fann,
                False, False, False, False, False, False, False, "warmup",
            )
        # warm 判定の結果を型にも写す（mypy は all(...) の内包を絞り込まない）。
        assert s200 is not None and e20 is not None and e50 is not None
        assert atr is not None and prev_high is not None and prev_low is not None

        long_sig = c["c"] > s200 and e20 > e50 and c["h"] >= prev_high
        short_sig = c["c"] < s200 and e20 < e50 and c["l"] <= prev_low
        exit_long = c["c"] < e50 or c["c"] < prev_low
        exit_short = c["c"] > e50 or c["c"] > prev_high

        block_long = fann is not None and fann > cfg["funding_block_ann_pct"]
        block_short = fann is not None and fann < -cfg["funding_block_ann_pct"]

        return DayDecision(
            day, idx, c["c"], s200, e20, e50, atr, prev_high, prev_low, fann,
            long_sig, short_sig, exit_long, exit_short, block_long, block_short, True,
        )

    def size_position(self, equity: float, price: float, atr: float) -> dict[str, Any]:
        """設計書 §4.3 のロットサイジング。返り値 notional<=0 なら見送り。"""
        cfg = self.cfg
        stop_dist = cfg["stop_atr_mult"] * atr
        if stop_dist <= 0 or price <= 0:
            return {"ok": False, "reason": "atr_invalid", "notional": 0.0}
        stop_pct = stop_dist / price
        notional = equity * cfg["risk_per_trade"] / stop_pct
        capped = False
        if notional > equity * cfg["max_leverage"]:
            notional = equity * cfg["max_leverage"]
            capped = True
        if notional < cfg["min_notional_usdc"]:
            return {
                "ok": False,
                "reason": "capacity_min_notional",
                "notional": notional,
                "stop_pct": stop_pct,
            }
        # HL の szDecimals=5 に切り捨て（数量誤差で容量例外を出さない）
        size = int(notional / price * 10**5) / 10**5
        if size <= 0:
            return {"ok": False, "reason": "capacity_size_rounding", "notional": notional}
        return {
            "ok": True,
            "notional": size * price,
            "size": size,
            "stop_dist": stop_dist,
            "stop_pct": stop_pct,
            "leverage": size * price / equity,
            "capped_by_leverage": capped,
        }


def _day_str(ms: int) -> str:
    import datetime as dt

    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%d")
