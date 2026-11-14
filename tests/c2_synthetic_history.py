"""Frozen synthetic C2 bootstrap history shared by the C2 unit tests.

Why this module exists
----------------------
`run_daily_cycle(..., override_history=...)` only avoids the live store when the caller
supplies history itself. Tests that built their fixture with
`c2_runner.load_local_history()` read `data/stock-ohlcv/<SYM>/ohlcv.jsonl` directly, so:

* they opened production market data on every run (unacceptable for a unit test), and
* they silently inherited the store's bar count, i.e. the "SPY has >= 22 bars" premise
  held only while the retention window happened to be long enough. A truncated store
  turns the suite red for a reason unrelated to the code under test.

The series below is deterministic and ends on a fixed session (2026-10-30), matching
`tests/test_c2_daily_cycle.py::_synthetic_base_history`. Keep the two in sync if the
fixture ever has to change.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

C2_SYMBOLS = ("SPY", "IWM", "JNJ", "PG")
SYNTHETIC_BASE_END = date(2026, 10, 30)


def synthetic_base_history(
    end_date: date = SYNTHETIC_BASE_END, n_bars: int = 260
) -> dict[str, list[dict[str, Any]]]:
    """Deterministic OHLCV series whose last bar is always `end_date`.

    Never derive bootstrap history from the live store (data/stock-ohlcv/*/ohlcv.jsonl):
    it advances every trading day, so any "latest bar is X" premise would decay and the
    >= LOOKBACK_VOL+1 bar requirement would depend on the store's retention window.
    """
    sessions: list[date] = []
    cursor = end_date
    while len(sessions) < n_bars:
        if cursor.weekday() < 5:  # weekdays only
            sessions.append(cursor)
        cursor -= timedelta(days=1)
    sessions.reverse()

    starts = {"SPY": 500.0, "IWM": 200.0, "JNJ": 150.0, "PG": 160.0}
    drifts = {"SPY": 0.8, "IWM": 0.5, "JNJ": 0.25, "PG": 0.3}

    def _close(sym: str, i: int) -> float:
        return round(starts[sym] + drifts[sym] * i + 0.4 * ((i % 5) - 2), 2)

    history: dict[str, list[dict[str, Any]]] = {}
    for sym in C2_SYMBOLS:
        bars: list[dict[str, Any]] = []
        for i, session in enumerate(sessions):
            close = _close(sym, i)
            prev = _close(sym, i - 1) if i else close
            bars.append(
                {
                    "t": f"{session.isoformat()}T04:00:00Z",
                    "o": prev,
                    "h": round(max(prev, close) * 1.002, 2),
                    "l": round(min(prev, close) * 0.998, 2),
                    "c": close,
                    "v": 1_000_000.0,
                }
            )
        history[sym] = bars
    return history


def detached_copy(
    history: dict[str, list[dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    """Shallow-copy every bar so callers can append without mutating the fixture."""
    return {sym: [dict(bar) for bar in bars] for sym, bars in history.items()}
