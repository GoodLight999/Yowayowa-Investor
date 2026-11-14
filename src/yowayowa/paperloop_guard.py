"""Anti-scalp guard (frozen thresholds, design §4 / spec §4 of t_fcd37354).

Pure function: reads no clock, no files. Acceptance evidence = synthetic
violation tests G1-G9 (NOT runtime "0 violations" counting)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

# frozen thresholds (crypto/equity; FX values are recorded for future scope)
MIN_HOLD_HOURS: dict[str, float] = {"crypto": 4.0, "equity": 4.0, "fx": 24.0}
MIN_FILL_INTERVAL: dict[str, timedelta] = {
    "crypto": timedelta(hours=1),
    "equity": timedelta(hours=1),
    "fx": timedelta(hours=4),
}
MAX_FILLS_PER_DAY: dict[str, int] = {"crypto": 2, "equity": 2, "fx": 1}
MIN_BAR_SECONDS: dict[str, int] = {"crypto": 3600, "equity": 3600, "fx": 14400}


@dataclass(frozen=True)
class GuardInput:
    asset_class: Literal["crypto", "equity", "fx"]
    side: Literal["buy", "sell"]
    now: datetime  # the fill bar's open time (the fill moment)
    last_fill_ts: datetime | None  # same asset's most recent fill
    last_fill_side: str | None
    position_opened_ts: datetime | None  # when the position went 0 -> nonzero
    fills_today: int  # same asset's UTC-day fill count (excluding this fill)
    decision_bar_seconds: int  # length of the bar the decision used (daily = 86400)


def check_trade(g: GuardInput) -> tuple[bool, str]:
    """(allowed, reject_reason-or-""). First-hit rejection order (§4-2)."""

    if g.decision_bar_seconds < MIN_BAR_SECONDS[g.asset_class]:
        return False, "bar_too_fine"
    if g.fills_today >= MAX_FILLS_PER_DAY[g.asset_class]:
        return False, "max_fills_per_day"
    if g.last_fill_ts is not None and (g.now - g.last_fill_ts) < MIN_FILL_INTERVAL[g.asset_class]:
        return False, "min_fill_interval"
    if g.position_opened_ts is not None and (g.now - g.position_opened_ts) < timedelta(
        hours=MIN_HOLD_HOURS[g.asset_class]
    ):
        return False, "min_hold"
    if (
        g.last_fill_side is not None
        and g.last_fill_ts is not None
        and g.last_fill_side != g.side
        and g.last_fill_ts.date() == g.now.date()
    ):
        return False, "same_day_reversal"
    return True, ""
