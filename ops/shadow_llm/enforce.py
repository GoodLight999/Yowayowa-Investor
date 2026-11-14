"""Mechanical enforcement: the seven accident classes M1-M7 (設計V2 §4-2).

Only accidents are enforced.  Nothing in here judges whether a decision is
*sensible* - an LLM that wants to sit out, cut, or size down keeps its answer
untouched.  Every override is returned with the class, the before value and the
after value, and the caller must write all of them to the journal (a silent
override is a bug, not a feature).
"""

from __future__ import annotations

import math
from typing import Any

FREEZE_DAILY_LOSS = -0.05   # M2: 憲法 L250-251 (1 日 5%)
FREEZE_WEEKLY_LOSS = -0.10  # M2: 1 週 10%
MAX_RISK_FRACTION = 0.05    # M3: 資金の 5%
EPS = 1e-9
# A held weight drifts by a few bps of mtm; a "target" that is above the held
# weight by less than this is not an add, so it must not be treated as opening
# new exposure (M1/M1b/M3 would otherwise fire on rounding).
INCREASE_EPS = 2e-3


def _series(ctx: dict[str, Any], asset: str) -> dict[str, list[float]]:
    return ctx.get("series", {}).get(asset, {})


def resolve_stop(basis: str, asset: str, ctx: dict[str, Any]) -> tuple[float | None, str]:
    """Turn ``stop_basis`` into a price.  The LLM never writes the number."""
    series = _series(ctx, asset)
    closes = series.get("close") or []
    highs = series.get("high") or []
    lows = series.get("low") or []
    if not closes:
        return None, "no_bars"

    if basis == "wick_low":
        if len(lows) < 20:
            return None, "needs_20_bars"
        stop = min(lows[-20:])
    elif basis == "sma20":
        if len(closes) < 20:
            return None, "needs_20_bars"
        stop = sum(closes[-20:]) / 20.0
    elif basis == "atr15":
        atr = ctx.get("atr14", {}).get(asset)
        if not atr:
            return None, "no_atr14"
        stop = closes[-1] - 1.5 * float(atr)
    elif basis == "fib":
        if len(highs) < 20 or len(lows) < 20:
            return None, "needs_20_bars"
        span = max(highs[-20:]) - min(lows[-20:])
        stop = closes[-1] - 0.618 * span
    else:
        return None, f"unknown_basis:{basis}"

    if not isinstance(stop, (int, float)) or not math.isfinite(stop) or stop <= 0:
        return None, "nonfinite_stop"
    if stop >= closes[-1]:
        return None, "stop_above_price"
    return float(stop), "ok"


def apply(ctx: dict[str, Any]) -> dict[str, Any]:
    """Apply M1-M7 to ``ctx['targets']`` and report what changed."""
    candidates: tuple[str, ...] = tuple(ctx["candidates"])
    held: dict[str, float] = {a: float(ctx["held"].get(a) or 0.0) for a in candidates}
    targets: dict[str, float] = {a: float(ctx["targets"].get(a) or 0.0) for a in candidates}
    overrides: list[dict[str, Any]] = []
    halt = False
    halt_reason = ""

    def override(cls: str, asset: str | None, before: float | None,
                 after: float | None, reason: str) -> None:
        overrides.append({
            "class": cls,
            "asset": asset,
            "before": before,
            "after": after,
            "reason": reason,
        })

    def zero_all(cls: str, reason: str) -> None:
        nonlocal halt, halt_reason
        for asset in candidates:
            if targets[asset] != 0.0:
                override(cls, asset, targets[asset], 0.0, reason)
                targets[asset] = 0.0
            elif not any(o["class"] == cls and o["asset"] == asset for o in overrides):
                override(cls, asset, targets[asset], 0.0, reason)
        halt = True
        halt_reason = halt_reason or reason

    # ---- M7: equity that the record cannot explain -----------------------
    gap = ctx.get("reconcile_gap_usd")
    equity = float(ctx.get("equity") or 0.0)
    tolerance = max(1e-6, abs(equity) * 1e-6)
    if gap is not None and abs(float(gap)) > tolerance:
        zero_all("M7", f"reconcile_gap_usd={float(gap):.9f} > tol={tolerance:.9f}")

    # ---- M5: missing / stale data ---------------------------------------
    if ctx.get("halt_reasons"):
        zero_all("M5", "; ".join(str(r) for r in ctx["halt_reasons"])[:180])

    # ---- M6: deadline / contract ----------------------------------------
    if ctx.get("deadline_exceeded"):
        for asset in candidates:
            if targets[asset] != held[asset]:
                override("M6", asset, targets[asset], held[asset], "deadline exceeded -> hold previous")
                targets[asset] = held[asset]
        halt = True
        halt_reason = halt_reason or "deadline_exceeded"
    elif ctx.get("contract_ok") is False:
        for asset in candidates:
            if targets[asset] != held[asset]:
                override("M6", asset, targets[asset], held[asset], "contract rejected -> hold previous")
                targets[asset] = held[asset]
        halt = True
        halt_reason = halt_reason or "contract_rejected"

    # ---- M2: daily / weekly loss ceiling --------------------------------
    loss_today = float(ctx.get("loss_today_pct") or 0.0)
    loss_week = float(ctx.get("loss_week_pct") or 0.0)
    if loss_today <= FREEZE_DAILY_LOSS:
        for asset in candidates:
            if targets[asset] > held[asset]:
                override("M2", asset, targets[asset], held[asset],
                         f"loss_today={loss_today:.4f} <= {FREEZE_DAILY_LOSS}")
                targets[asset] = held[asset]
        halt = True
        halt_reason = halt_reason or "daily_loss_ceiling"
    if loss_week <= FREEZE_WEEKLY_LOSS:
        for asset in candidates:
            if targets[asset] > held[asset]:
                override("M2", asset, targets[asset], held[asset],
                         f"loss_week={loss_week:.4f} <= {FREEZE_WEEKLY_LOSS}")
                targets[asset] = held[asset]
        halt = True
        halt_reason = halt_reason or "weekly_loss_ceiling"

    # ---- M1 / M1b / M3: stop, stop movement, position risk --------------
    basis = str(ctx.get("stop_basis") or "")
    prices = ctx.get("prices") or {}
    for asset in candidates:
        if targets[asset] <= held[asset] + INCREASE_EPS:
            continue  # nothing is being opened, nothing to protect
        stop, why = resolve_stop(basis, asset, ctx)
        before = targets[asset]
        if stop is None:
            override("M1", asset, before, 0.0, f"no usable stop ({why})")
            targets[asset] = 0.0
            continue
        prev = (ctx.get("prev_stop") or {}).get(asset)
        # only a strictly worse stop is the M1b accident: the same bars give the
        # same stop, and re-selecting a looser stop_basis is what must be stopped
        if prev is not None and stop > float(prev) + EPS:
            override("M1b", asset, before, held[asset],
                     f"stop moved adversely ({stop:.6f} > {float(prev):.6f})")
            targets[asset] = held[asset]
            continue
        price = float(prices.get(asset) or 0.0)
        if price <= 0 or equity <= 0:
            override("M3", asset, before, 0.0, "no price/equity to size the risk")
            targets[asset] = 0.0
            continue
        risk_usd = (price - stop) / price * targets[asset] * equity
        if risk_usd > equity * MAX_RISK_FRACTION + EPS:
            override("M3", asset, before, 0.0,
                     f"max_loss={risk_usd:.4f} > {MAX_RISK_FRACTION:.0%} of equity={equity:.4f}")
            targets[asset] = 0.0

    # ---- M4: leverage ceiling (defence in depth; the schema also checks) --
    # Only a real decision can be over-leveraged; a fallback "hold previous"
    # mirrors the book's actual weights (which carry a few bps of drift) and
    # must not be zeroed by this check.
    total = sum(targets.values())
    if ctx.get("contract_ok", True) and total > 1.0 + 1e-3:
        for asset in candidates:
            if targets[asset] != 0.0:
                override("M4", asset, targets[asset], 0.0, f"sum={total:.4f} > 1.0")
                targets[asset] = 0.0

    return {
        "targets": {a: round(targets[a], 10) for a in candidates},
        "overrides": overrides,
        "fired": bool(overrides),
        "classes": sorted({o["class"] for o in overrides}),
        "halt": halt,
        "halt_reason": halt_reason,
    }
