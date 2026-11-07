# ruff: noqa: E501, RUF001
"""C2 Feature generation and market regime summary engine.

Calculates:
- 21-day annualized sample volatility of SPY (sigma21_ann = sigma21_daily * sqrt(252))
- 200-day Simple Moving Average (SMA200) and deviation
- 14-day Average True Range (ATR14) of SPY
- DGS1 1-year Treasury rate
- 4-quadrant market regime: (BULL/LOW_VOL, BULL/HIGH_VOL, BEAR/HIGH_VOL, BEAR/LOW_VOL)
- Universe closes and daily returns for [SPY, IWM, JNJ, PG]
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from statistics import fmean, stdev
from typing import Any

C2_UNIVERSE = ["SPY", "IWM", "JNJ", "PG"]
VOL_SYMBOL = "SPY"
LOOKBACK_VOL = 21
LOOKBACK_SMA = 200
LOOKBACK_ATR = 14
ANNUALIZATION_FACTOR = math.sqrt(252)


@dataclass(frozen=True)
class C2MarketContext:
    as_of_date: str
    last_bar_date: str
    spy_close: float
    spy_sma200: float
    spy_sma200_dev: float
    spy_sigma21_annualized: float
    spy_sigma21_daily: float
    spy_atr14: float
    dgs1_rate: float
    regime_trend: str  # BULL or BEAR
    regime_vol: str  # LOW_VOL or HIGH_VOL
    regime_quadrant: str  # e.g. BULL/LOW_VOL
    universe_closes: dict[str, float]
    universe_daily_returns: dict[str, float]

    def to_inputs_dict(self) -> dict[str, Any]:
        return {
            "as_of_date": self.as_of_date,
            "last_bar_date": self.last_bar_date,
            "spy_close": round(self.spy_close, 4),
            "spy_sma200": round(self.spy_sma200, 4),
            "spy_sma200_dev_pct": round(self.spy_sma200_dev * 100, 2),
            "spy_sigma21_annualized_pct": round(self.spy_sigma21_annualized * 100, 2),
            "spy_atr14": round(self.spy_atr14, 4),
            "dgs1_rate_pct": round(self.dgs1_rate * 100, 2),
            "regime_quadrant": self.regime_quadrant,
            "universe_closes": {s: round(v, 4) for s, v in self.universe_closes.items()},
            "universe_daily_returns": {
                s: round(v, 6) for s, v in self.universe_daily_returns.items()
            },
        }

    def to_summary_text(self) -> str:
        closes_str = ", ".join(f"{s}: ${v:.2f}" for s, v in self.universe_closes.items())
        rets_str = ", ".join(
            f"{s}: {v * 100:+.2f}%" for s, v in self.universe_daily_returns.items()
        )
        return (
            f"As-of Date: {self.as_of_date} (Last Bar: {self.last_bar_date})\n"
            f"SPY Close: ${self.spy_close:.2f} (SMA200: ${self.spy_sma200:.2f}, Dev: {self.spy_sma200_dev * 100:+.2f}%)\n"
            f"SPY 21-Day Vol (Annualized): {self.spy_sigma21_annualized * 100:.2f}% (Daily σ: {self.spy_sigma21_daily * 100:.3f}%)\n"
            f"SPY ATR14: ${self.spy_atr14:.2f}\n"
            f"US 1Y Treasury Rate (DGS1): {self.dgs1_rate * 100:.2f}%\n"
            f"Regime: {self.regime_quadrant} (Trend: {self.regime_trend}, Volatility: {self.regime_vol})\n"
            f"Universe Closes: {closes_str}\n"
            f"Universe 1d Returns: {rets_str}"
        )


def compute_features(
    history_by_symbol: dict[str, list[dict[str, Any]]],
    as_of_date: date | str,
    dgs1_override: float | None = None,
    include_target_date: bool = False,
) -> C2MarketContext:
    """Compute all C2 indicators as of given decision date using prior closed bars.

    If include_target_date is True (e.g. for post-close daily MTM evaluation),
    bars with date <= target_date are included.
    If False (e.g. for weekly pre-market decisions), bars with date < target_date are included.
    """
    target_date = date.fromisoformat(str(as_of_date)[:10])

    # Filter bars prior to target_date (or up to latest completed bar)
    filtered: dict[str, list[dict[str, Any]]] = {}
    for sym in C2_UNIVERSE:
        bars = history_by_symbol.get(sym, [])
        valid = []
        for b in bars:
            d = _extract_date(b)
            if d is not None:
                if include_target_date and d <= target_date:
                    valid.append(b)
                elif not include_target_date and d < target_date:
                    valid.append(b)
        valid.sort(key=lambda x: _extract_date(x) or date.min)
        filtered[sym] = valid

    spy_bars = filtered.get(VOL_SYMBOL, [])
    if len(spy_bars) < LOOKBACK_VOL + 1:
        raise ValueError(
            f"Insufficient SPY history ({len(spy_bars)} bars) for lookback {LOOKBACK_VOL}"
        )

    spy_closes = [float(b["c"]) for b in spy_bars]
    spy_close = spy_closes[-1]
    last_bar_d = _extract_date(spy_bars[-1])
    last_bar_date_str = last_bar_d.isoformat() if last_bar_d else target_date.isoformat()

    # 1. 21-day volatility
    returns = []
    for i in range(len(spy_closes) - LOOKBACK_VOL, len(spy_closes)):
        prev = spy_closes[i - 1]
        curr = spy_closes[i]
        returns.append(curr / prev - 1.0)
    sigma21_daily = stdev(returns) if len(returns) == LOOKBACK_VOL else 0.01
    sigma21_ann = sigma21_daily * ANNUALIZATION_FACTOR

    # 2. 200-day SMA
    if len(spy_closes) >= LOOKBACK_SMA:
        sma200 = fmean(spy_closes[-LOOKBACK_SMA:])
    else:
        sma200 = fmean(spy_closes)
    sma200_dev = spy_close / sma200 - 1.0 if sma200 > 0 else 0.0

    # 3. ATR14
    atr_window = min(len(spy_bars) - 1, LOOKBACK_ATR)
    true_ranges = []
    for i in range(len(spy_bars) - atr_window, len(spy_bars)):
        high = float(spy_bars[i].get("h", spy_bars[i]["c"]))
        low = float(spy_bars[i].get("l", spy_bars[i]["c"]))
        prev_c = float(spy_bars[i - 1]["c"])
        tr = max(high - low, abs(high - prev_c), abs(low - prev_c))
        true_ranges.append(tr)
    atr14 = fmean(true_ranges) if true_ranges else 0.0

    # 4. DGS1
    dgs1_val = dgs1_override if dgs1_override is not None else _load_latest_dgs1(target_date)

    # 5. 4-quadrant regime
    trend = "BULL" if spy_close >= sma200 else "BEAR"
    vol = "LOW_VOL" if sigma21_ann <= 0.1500 else "HIGH_VOL"
    quadrant = f"{trend}/{vol}"

    # 6. Universe closes and 1-day returns
    univ_closes: dict[str, float] = {}
    univ_returns: dict[str, float] = {}
    for sym in C2_UNIVERSE:
        bars = filtered.get(sym, [])
        if bars:
            c_curr = float(bars[-1]["c"])
            univ_closes[sym] = c_curr
            if len(bars) >= 2:
                c_prev = float(bars[-2]["c"])
                univ_returns[sym] = c_curr / c_prev - 1.0 if c_prev > 0 else 0.0
            else:
                univ_returns[sym] = 0.0
        else:
            univ_closes[sym] = 0.0
            univ_returns[sym] = 0.0

    return C2MarketContext(
        as_of_date=target_date.isoformat(),
        last_bar_date=last_bar_date_str,
        spy_close=spy_close,
        spy_sma200=sma200,
        spy_sma200_dev=sma200_dev,
        spy_sigma21_annualized=sigma21_ann,
        spy_sigma21_daily=sigma21_daily,
        spy_atr14=atr14,
        dgs1_rate=dgs1_val,
        regime_trend=trend,
        regime_vol=vol,
        regime_quadrant=quadrant,
        universe_closes=univ_closes,
        universe_daily_returns=univ_returns,
    )


def _extract_date(bar: dict[str, Any]) -> date | None:
    t_val = bar.get("t") or bar.get("as_of")
    if not t_val:
        return None
    try:
        return date.fromisoformat(str(t_val)[:10])
    except Exception:
        return None


def _load_latest_dgs1(as_of: date) -> float:
    """Read DGS1 rate from local files or default to 0.0412 (4.12%)."""
    search_paths = [
        Path("/root/current-work/yowayowa-investor/data/macro-observations/fred.jsonl"),
        Path("/root/current-work/yowayowa-investor/data/macro-observations/treasury.jsonl"),
        Path("/root/current-work/yowayowa-investor/data/gates/dgs1.jsonl"),
    ]
    for p in search_paths:
        if p.exists():
            try:
                for line in reversed(p.read_text().splitlines()):
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    if "DGS1" in row:
                        val = float(row["DGS1"])
                        return val / 100.0 if val > 1.0 else val
                    if row.get("series") == "DGS1" and "value" in row:
                        val = float(row["value"])
                        return val / 100.0 if val > 1.0 else val
            except Exception:
                continue
    return 0.0412  # Standard 4.12% Treasury rate benchmark
