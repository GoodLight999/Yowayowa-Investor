"""Order lifecycle follow-up for the C2 paper (lane15) rebalance orders.

Why this module exists (measured 2026-11-09)
--------------------------------------------
The 2026-11-03 rebalance orders were submitted 2026-11-02T10:16Z and then
canceled in bulk at 2026-11-02T11:02:08Z (all four `canceled_at` within 20 ms,
`expired_at=null`, `reject_reason=null`, `filled_qty=0`).  `orders.jsonl` kept the
submit-time snapshot (`status=pending_new`) forever, so the local ledger claimed
four live orders while the broker held none -- and `equity_15` stayed welded to
the 100,000 cash baseline.  Nothing in the daily batch ever read the broker order
book, and nothing ever re-sent a dead order.

What it does (daily post-close batch)
-------------------------------------
1. refresh        append the broker's *true* status of every ``c2_*`` order to
                  ``order_lifecycle.jsonl`` (append-only, never rewrite the ledger)
2. stale-open     a still-open order whose validity window has already closed
                  (``expires_at <= now``, or ``pending_new`` untouched for >24h) is
                  canceled, because it will never trade
3. retry          for a desired position that has no live order and no fill, submit
                  the remaining delta for the *next* session (idempotent)

TIF / submission-time contract
------------------------------
Alpaca docs (docs.alpaca.markets/reference/postorder, "time_in_force"):

* ``day``  "eligible for execution only on the day it is live ... valid during
  Regular Trading Hours ... If unfilled after the closing auction, it is
  automatically canceled. If submitted after the close, it is queued and
  submitted the following trading day."
* ``opg``  "eligible to execute only in the market opening auction. Any unfilled
  orders after the open will be cancelled ... submitted after 9:28am but before
  7:00pm ET will be rejected."
* ``extended_hours`` only works with ``limit`` + ``day``/``gtc``.

Decision: keep ``time_in_force='day'`` + ``extended_hours=False`` and *prove the
validity window*.  ``opg`` would give up after a single gap through the 50 bps
band with no second chance inside the week, whereas a day limit keeps the whole
session to fill.  A day order submitted pre-open (or after the previous close,
where it is queued for the next open) is live for exactly one session, so the
order's ``expires_at`` -- which Alpaca sets to that session's close -- must fall
on the session the order is meant to trade.  That is precisely the check the
2026-11-02 order would have failed: it carried
``expires_at=2026-11-02T21:00:00Z`` while the decision it implemented was
labelled ``2026-11-03``.  Consequence: even without the operator cancel it would
have died 16 hours before the session it was meant to trade.

Idempotency
-----------
``client_order_id = c2_{symbol}_{side}_{session}_a{n}`` where ``n`` is
``1 + number of broker orders already carrying that prefix``.  The broker order
book is the single source of truth, so a second run on the same day sees the
order it just placed (live or filled) and submits nothing.  Alpaca additionally
rejects duplicate ``client_order_id`` values, so a lost HTTP response cannot
double up either.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger("paper_c2.order_lifecycle")

ET = ZoneInfo("America/New_York")

# Statuses in which Alpaca may still execute the order (see the Order Lifecycle
# table in docs.alpaca.markets/reference/postorder).
OPEN_STATUSES = frozenset(
    {
        "new",
        "accepted",
        "pending_new",
        "accepted_for_bidding",
        "partially_filled",
        "pending_cancel",
        "pending_replace",
        "held",
        "stopped",
    }
)
# Terminal states that produced no (or only a partial) position.
TERMINAL_UNFILLED = frozenset({"expired", "canceled", "rejected", "suspended"})

CLIENT_ORDER_ID_PREFIX = "c2_"
_ATTEMPT_SUFFIX = re.compile(r"_a(\d+)$")
MAX_ATTEMPTS_PER_SESSION = 3
PENDING_NEW_STALE_AFTER = timedelta(hours=24)
SLIPPAGE_TOLERANCE_BPS = 50.0
LIFECYCLE_FILENAME = "order_lifecycle.jsonl"


class OrderLifecycleError(RuntimeError):
    """Raised when the broker state cannot be read well enough to act safely."""


# --------------------------------------------------------------------------- #
# time helpers
# --------------------------------------------------------------------------- #
def parse_ts(value: Any) -> datetime | None:
    """Parse an Alpaca RFC3339 timestamp (up to nanosecond fractions) as UTC."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    # Python < 3.11 rejects more than 6 fractional digits; trim them defensively.
    if "." in text:
        head, _, tail = text.partition(".")
        frac = ""
        rest = ""
        for i, ch in enumerate(tail):
            if ch.isdigit():
                frac += ch
            else:
                rest = tail[i:]
                break
        text = f"{head}.{frac[:6]}{rest}"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)


def _next_weekday(day: date) -> date:
    cursor = day
    while cursor.weekday() >= 5:
        cursor += timedelta(days=1)
    return cursor


@dataclass(frozen=True)
class SessionWindow:
    """The ET trading session an order submitted now would actually live in."""

    session_date: date
    source: str  # "clock" (Alpaca /v2/clock) or "local" (derived from the ET wall clock)

    def as_dict(self) -> dict[str, str]:
        return {"session_date": self.session_date.isoformat(), "session_source": self.source}


def resolve_session(client: Any, now: datetime) -> SessionWindow:
    """Return the session a ``time_in_force=day`` order submitted at ``now`` trades in.

    Uses ``GET /v2/clock`` when the client exposes it (the broker knows the holiday
    calendar).  Falls back to the ET wall clock -- if the market has already closed
    the order is queued for the next trading day, per the ``day`` TIF contract.
    """
    clock: Any = None
    get_clock = getattr(client, "get_clock", None)
    if callable(get_clock):
        try:
            clock = get_clock()
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Alpaca clock unavailable (%s); deriving session locally", exc)
            clock = None
    if isinstance(clock, dict) and not clock.get("error") and not clock.get("http_code"):
        anchor = clock.get("next_close") if clock.get("is_open") else clock.get("next_open")
        parsed = parse_ts(anchor)
        if parsed is not None:
            return SessionWindow(parsed.astimezone(ET).date(), "clock")

    et_now = now.astimezone(ET)
    session_day = et_now.date()
    if et_now.time() >= time(16, 0) or session_day.weekday() >= 5:
        session_day = _next_weekday(session_day + timedelta(days=1))
    return SessionWindow(session_day, "local")


def window_mismatch(order: dict[str, Any], session_date: date) -> tuple[bool, str]:
    """Does the order's validity window cover ``session_date``?

    Returns ``(mismatch, reason)``.  A missing ``expires_at`` is reported as
    unverified-but-not-fatal (reason ``expires_at_missing``) so callers can record
    it without inventing a failure the broker did not produce.
    """
    expires = parse_ts(order.get("expires_at"))
    if expires is None:
        return False, "expires_at_missing"
    expires_et = expires.astimezone(ET)
    if expires_et.date() != session_date:
        return (
            True,
            f"expires_at {expires_et.isoformat()} (ET date {expires_et.date()}) "
            f"does not cover session {session_date}",
        )
    if expires_et.time() < time(12, 0):
        return (
            True,
            f"expires_at {expires_et.isoformat()} (ET) is not a market close for {session_date}",
        )
    return False, ""


# --------------------------------------------------------------------------- #
# book helpers
# --------------------------------------------------------------------------- #
def _is_mine(order: dict[str, Any]) -> bool:
    return str(order.get("client_order_id", "")).startswith(CLIENT_ORDER_ID_PREFIX)


def _is_stale_open(order: dict[str, Any], now: datetime) -> tuple[bool, str]:
    """A still-open order that will never trade (its window has already closed)."""
    expires = parse_ts(order.get("expires_at"))
    if expires is not None and expires <= now:
        return True, f"expires_at {expires.isoformat()} already passed"
    if order.get("status") == "pending_new":
        updated = parse_ts(order.get("updated_at")) or parse_ts(order.get("submitted_at"))
        if updated is not None and now - updated > PENDING_NEW_STALE_AFTER:
            return (
                True,
                f"pending_new for >{PENDING_NEW_STALE_AFTER} (updated_at {updated.isoformat()})",
            )
    return False, ""


def attempt_ids(
    orders: Iterable[dict[str, Any]], symbol: str, side: str, session: date
) -> list[str]:
    """Existing *retry* client_order_ids for ``symbol/side`` in ``session``.

    The weekly decision order uses the bare id ``c2_{sym}_{side}_{session}``; only
    the ``_a{n}`` retries created here count against the retry cap (a weekly
    replace must not eat the day's retry budget).
    """
    prefix = f"{CLIENT_ORDER_ID_PREFIX}{symbol}_{side}_{session.isoformat()}"
    return sorted(
        str(o.get("client_order_id"))
        for o in orders
        if str(o.get("client_order_id", "")).startswith(prefix)
        and _ATTEMPT_SUFFIX.search(str(o.get("client_order_id", "")))
    )


def attempt_numbers(
    orders: Iterable[dict[str, Any]], symbol: str, side: str, session: date
) -> list[int]:
    numbers: list[int] = []
    for client_order_id in attempt_ids(orders, symbol, side, session):
        match = _ATTEMPT_SUFFIX.search(client_order_id)
        if match is not None:
            numbers.append(int(match.group(1)))
    return numbers


def next_client_order_id(
    orders: Iterable[dict[str, Any]], symbol: str, side: str, session: date
) -> str:
    numbers = attempt_numbers(orders, symbol, side, session)
    next_attempt = max(numbers, default=0) + 1
    return f"{CLIENT_ORDER_ID_PREFIX}{symbol}_{side}_{session.isoformat()}_a{next_attempt}"


def desired_deltas(
    weights: dict[str, Any],
    equity: float,
    positions: dict[str, float],
    prices: dict[str, float],
    universe: Iterable[str],
) -> list[dict[str, Any]]:
    """Share deltas between the persisted target book and the actual positions."""
    deltas: list[dict[str, Any]] = []
    for symbol in universe:
        price = float(prices.get(symbol) or 0.0)
        if price <= 0:
            continue
        weight = float(weights.get(symbol, 0.0) or 0.0)
        target_shares = int(equity * weight / price)
        current_shares = int(positions.get(symbol, 0.0) or 0.0)
        delta = target_shares - current_shares
        if delta == 0:
            continue
        deltas.append(
            {
                "symbol": symbol,
                "side": "buy" if delta > 0 else "sell",
                "qty": abs(delta),
                "target_shares": target_shares,
                "current_shares": current_shares,
                "reference_price": price,
                "limit_price": round(
                    price * (1.0 + SLIPPAGE_TOLERANCE_BPS / 10000.0)
                    if delta > 0
                    else price * (1.0 - SLIPPAGE_TOLERANCE_BPS / 10000.0),
                    2,
                ),
            }
        )
    return deltas


# --------------------------------------------------------------------------- #
# main entry point
# --------------------------------------------------------------------------- #
def run_order_lifecycle(
    client: Any,
    *,
    state: dict[str, Any],
    as_of_date: date,
    prices: dict[str, float],
    universe: Iterable[str],
    now: datetime | None = None,
    ledger_path: Path | None = None,
    record_submit: Callable[[dict[str, Any]], None] | None = None,
    max_attempts: int = MAX_ATTEMPTS_PER_SESSION,
    slippage_bps: float = SLIPPAGE_TOLERANCE_BPS,
) -> dict[str, Any]:
    """Refresh, cancel dead orders, and re-send the missing book (paper only).

    Never raises for broker trouble: the daily MTM pass must still record.  Any
    failure is reported in ``errors`` and prevents *submission* (fail-closed).
    """
    now = now or datetime.now(UTC)
    result: dict[str, Any] = {
        "status": "ok",
        "as_of_date": as_of_date.isoformat(),
        "checked_at": now.isoformat(),
        "session_date": None,
        "session_source": None,
        "refreshed": [],
        "stale_canceled": [],
        "submitted": [],
        "skipped": [],
        "errors": [],
    }

    try:
        session = resolve_session(client, now)
    except Exception as exc:
        result["status"] = "error"
        result["errors"].append(f"session_resolution_failed: {exc}")
        return result
    result.update(session.as_dict())

    # ---- 1. read the broker book (read-only) ----
    try:
        # Prefer the strict reader: a failed read must never look like an empty
        # book, or the retry below would double up on live orders.
        reader = getattr(client, "get_orders_strict", None) or client.get_orders
        orders = reader(status="all")
        if not isinstance(orders, list):
            raise OrderLifecycleError(f"unexpected /orders payload: {type(orders).__name__}")
    except Exception as exc:
        result["status"] = "error"
        result["errors"].append(f"orders_read_failed: {exc}")
        return result

    mine = [o for o in orders if _is_mine(o) and o.get("status")]
    result["refreshed"] = [
        {
            "client_order_id": o.get("client_order_id"),
            "id": o.get("id"),
            "symbol": o.get("symbol"),
            "side": o.get("side"),
            "qty": o.get("qty"),
            "filled_qty": o.get("filled_qty"),
            "status": o.get("status"),
            "time_in_force": o.get("time_in_force"),
            "expires_at": o.get("expires_at"),
            "expired_at": o.get("expired_at"),
            "canceled_at": o.get("canceled_at"),
            "failed_at": o.get("failed_at"),
            "reject_reason": o.get("reject_reason"),
            "limit_price": o.get("limit_price"),
        }
        for o in mine
    ]

    # ---- 2. cancel orders whose window already closed ----
    live: dict[tuple[str, str], dict[str, Any]] = {}
    for order in mine:
        if order.get("status") not in OPEN_STATUSES:
            continue
        stale, why = _is_stale_open(order, now)
        if not stale:
            live.setdefault((str(order.get("symbol")), str(order.get("side"))), order)
            continue
        order_id = order.get("id")
        try:
            cancel_res = client.cancel_order(str(order_id))
            if isinstance(cancel_res, dict) and (
                cancel_res.get("http_code") or cancel_res.get("error")
            ):
                # e.g. already expired between the read and the cancel: it cannot
                # fill anyway, so keep going and let the retry below re-send.
                result["errors"].append(f"cancel_rejected:{order_id}: {cancel_res}")
            result["stale_canceled"].append(
                {"client_order_id": order.get("client_order_id"), "id": order_id, "reason": why}
            )
        except Exception as exc:
            result["errors"].append(f"cancel_failed:{order_id}: {exc}")
            # still treat it as not-live: it cannot fill anyway (window closed)

    # ---- 3. re-send the missing book ----
    halt = state.get("kill_switch_triggered") or state.get("order_retry_paused")
    if halt:
        result["status"] = "skipped_halted"
        result["skipped"].append(
            {
                "reason": "kill_switch_triggered"
                if state.get("kill_switch_triggered")
                else "order_retry_paused"
            }
        )
        _persist(result, ledger_path)
        return result

    try:
        account = client.get_account()
        positions_payload = client.get_positions()
        if not isinstance(positions_payload, list):
            raise OrderLifecycleError(
                f"unexpected /positions payload: {type(positions_payload).__name__}"
            )
    except Exception as exc:
        result["status"] = "error"
        result["errors"].append(f"account_or_positions_read_failed: {exc}")
        _persist(result, ledger_path)
        return result

    # A zero/negative equity read would size every target to 0 shares and turn a
    # retry into a full liquidation; refuse instead.
    if float(account.equity) <= 0:
        result["status"] = "error"
        result["errors"].append(f"non_positive_equity: {account.equity}")
        _persist(result, ledger_path)
        return result

    positions = {
        str(p.get("symbol")): float(p.get("qty", 0.0) or 0.0)
        for p in positions_payload
        if isinstance(p, dict) and p.get("symbol")
    }

    deltas = desired_deltas(
        state.get("current_weights_15", {}) or {},
        float(account.equity),
        positions,
        prices,
        universe,
    )

    for delta in deltas:
        symbol = delta["symbol"]
        side = delta["side"]
        if (symbol, side) in live:
            result["skipped"].append(
                {
                    "symbol": symbol,
                    "side": side,
                    "reason": "live_order",
                    "client_order_id": live[(symbol, side)].get("client_order_id"),
                }
            )
            continue
        opposite = live.get((symbol, "sell" if side == "buy" else "buy"))
        if opposite is not None:
            result["skipped"].append(
                {
                    "symbol": symbol,
                    "side": side,
                    "reason": "conflicting_live_order",
                    "client_order_id": opposite.get("client_order_id"),
                }
            )
            continue

        existing_attempts = attempt_numbers(mine, symbol, side, session.session_date)
        if existing_attempts and max(existing_attempts) >= max_attempts:
            result["skipped"].append(
                {
                    "symbol": symbol,
                    "side": side,
                    "reason": "retry_cap_reached",
                    "attempts": existing_attempts,
                }
            )
            continue

        client_order_id = next_client_order_id(mine, symbol, side, session.session_date)
        limit_price = delta["limit_price"]
        if slippage_bps != SLIPPAGE_TOLERANCE_BPS:
            factor = 1.0 + slippage_bps / 10000.0 if side == "buy" else 1.0 - slippage_bps / 10000.0
            limit_price = round(delta["reference_price"] * factor, 2)

        try:
            response = client.submit_paper_order(
                symbol=symbol,
                qty=delta["qty"],
                side=side,
                order_type="limit",
                time_in_force="day",
                limit_price=limit_price,
                client_order_id=client_order_id,
            )
        except Exception as exc:
            result["errors"].append(f"submit_failed:{symbol}:{side}: {exc}")
            continue

        mismatch, reason = window_mismatch(
            response if isinstance(response, dict) else {}, session.session_date
        )
        entry = {
            "symbol": symbol,
            "side": side,
            "qty": delta["qty"],
            "limit_price": limit_price,
            "client_order_id": client_order_id,
            "session_date": session.session_date.isoformat(),
            "response_status": (response or {}).get("status")
            if isinstance(response, dict)
            else None,
            "expires_at": (response or {}).get("expires_at")
            if isinstance(response, dict)
            else None,
            "window_ok": not mismatch,
            "window_note": reason,
        }
        if isinstance(response, dict) and (response.get("http_code") or response.get("error")):
            entry["error"] = response
            result["errors"].append(f"submit_rejected:{symbol}:{side}: {response}")
        if mismatch:
            # The order cannot trade the intended session.  Cancel it instead of
            # letting a mislabelled order sit in the book (the 2026-11-02 defect).
            order_id = (response or {}).get("id") if isinstance(response, dict) else None
            if order_id:
                try:
                    client.cancel_order(str(order_id))
                    entry["canceled_for_window"] = True
                except Exception as exc:
                    result["errors"].append(f"window_cancel_failed:{order_id}: {exc}")
            result["errors"].append(f"session_window_mismatch:{symbol}:{side}: {reason}")
        result["submitted"].append(entry)

        if record_submit is not None:
            try:
                record_submit(
                    {
                        "as_of_date": as_of_date.isoformat(),
                        "timestamp_utc": now.isoformat(),
                        "origin": "order_lifecycle_retry",
                        "symbol": symbol,
                        "side": side,
                        "qty": delta["qty"],
                        "order_type": "limit",
                        "time_in_force": "day",
                        "reference_price": delta["reference_price"],
                        "limit_price": limit_price,
                        "slippage_tolerance_bps": slippage_bps,
                        "client_order_id": client_order_id,
                        "session_date": session.session_date.isoformat(),
                        "window_ok": not mismatch,
                        "alpaca_response": response,
                    }
                )
            except Exception as exc:  # pragma: no cover - defensive
                result["errors"].append(f"record_submit_failed: {exc}")

        mine = [
            *mine,
            {"client_order_id": client_order_id, "status": entry["response_status"]},
        ]

    _persist(result, ledger_path)
    return result


def _persist(result: dict[str, Any], ledger_path: Path | None) -> None:
    """Append one lifecycle snapshot line (append-only; never rewrites the ledger)."""
    if ledger_path is None:
        return
    try:
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with open(ledger_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(result, sort_keys=True) + "\n")
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Could not append order lifecycle record to %s: %s", ledger_path, exc)


def summarize(result: dict[str, Any]) -> str:
    """One-line summary for the cron output."""
    return (
        f"session={result.get('session_date')}({result.get('session_source')}) "
        f"refresh={len(result.get('refreshed', []))} "
        f"stale_canceled={len(result.get('stale_canceled', []))} "
        f"submitted={len(result.get('submitted', []))} "
        f"skipped={len(result.get('skipped', []))} "
        f"errors={len(result.get('errors', []))}"
    )
