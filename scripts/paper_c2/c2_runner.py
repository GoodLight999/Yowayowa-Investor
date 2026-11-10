# ruff: noqa: RUF001, E501
"""C2 Orchestrator and CLI runner.

Runs both:
- Weekly batch: Features -> Rule (10% & 15%) -> LLM loop -> Atomic JSONL ledger -> Alpaca paper execution
- Daily batch: Market data ingest -> MTM mark-to-market -> Kill switch evaluation -> Attribution matrix update
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import UTC, date, datetime, timedelta
from datetime import time as dt_time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .alpaca_paper_client import KILL_SWITCH_CANCEL_REASON, AlpacaPaperClient
from .c2_attribution import (
    compute_attribution_matrix,
    format_attribution_markdown,
    join_outcomes,
    load_decisions,
)
from .c2_features import (
    C2_UNIVERSE,
    compute_features,
)
from .c2_kill_switch import evaluate_kill_switches
from .c2_ledger import (
    DEFAULT_LEDGER_PATH,
    V6_PROVENANCE_SHA,
    append_decision_record,
)
from .c2_llm_loop import apply_decision_to_weights, call_llm_decider
from .c2_order_lifecycle import (
    LIFECYCLE_FILENAME,
    OPEN_STATUSES,
    cancel_and_confirm,
    next_client_order_id,
    resolve_session,
    run_order_lifecycle,
    window_mismatch,
)
from .c2_order_lifecycle import summarize as summarize_order_lifecycle
from .c2_strategy import compute_rule_weights

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("paper_c2.runner")

DATA_DIR = Path("/root/current-work/yowayowa-investor/data/paper_c2")
STATE_FILE = DATA_DIR / "state.json"
MTM_FILE = DATA_DIR / "mtm_history.jsonl"
ORDERS_FILE = DATA_DIR / "orders.jsonl"
ATTRIBUTION_JSON = DATA_DIR / "attribution_summary.json"
ATTRIBUTION_MD = DATA_DIR / "attribution_summary.md"

PHASE_START_DATE = date(2026, 11, 3)
SLIPPAGE_TOLERANCE_BPS = 50.0  # 0.50% institutional slippage limit

# Where the limit price's reference came from. Persisted per order so a later
# reader can tell a live-quote order from a stale-close one and from a
# market-fallback replacement.
REFERENCE_PRICE_SOURCE_STALE = "stale_close"
REFERENCE_PRICE_SOURCE_LIVE = "live_quote"
REFERENCE_PRICE_SOURCE_FALLBACK = "market_fallback"

# A live print further than this from the previous session close is treated as
# bad data rather than as a gap: the close is used and the reason recorded.
MAX_REFERENCE_DEVIATION_BPS = 2000.0  # 20%

# Fallback window: 10:00 ET (30 minutes after the open) up to the close.
MARKET_FALLBACK_CUTOFF_ET = dt_time(10, 0)
MARKET_CLOSE_ET = dt_time(16, 0)
EASTERN = ZoneInfo("America/New_York")
OPEN_ORDER_STATUSES = {"new", "accepted", "pending_new", "partially_filled", "open", "held"}


def load_state() -> dict[str, Any]:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "peak_equity_10": 100000.0,
        "peak_equity_15": 100000.0,
        "current_equity_10": 100000.0,
        "current_equity_15": 100000.0,
        "initial_paper_equity": 100000.0,
        "alpaca_paper_equity": 100000.0,
        "current_weights_10": {"USD": 1.0, "SPY": 0.0, "IWM": 0.0, "JNJ": 0.0, "PG": 0.0},
        "current_weights_15": {"USD": 1.0, "SPY": 0.0, "IWM": 0.0, "JNJ": 0.0, "PG": 0.0},
        "consecutive_api_failures": 0,
        "last_decision_date": None,
        "last_mtm_date": None,
        "last_mtm_bar_date": None,
        "kill_switch_triggered": False,
        "kill_switch_event": None,
        "missed_trading_days": 0,
        "missed_rebalance_days": 0,
        "completed_trading_days": [],
    }


def save_state(state: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def load_local_history() -> dict[str, list[dict[str, Any]]]:
    """Read existing persisted raw OHLCV for bootstrap / warm-up without modifying it."""
    history: dict[str, list[dict[str, Any]]] = {}
    base_stock_dir = Path("/root/current-work/yowayowa-investor/data/stock-ohlcv")
    for sym in C2_UNIVERSE:
        p = base_stock_dir / sym / "ohlcv.jsonl"
        bars = []
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        row = json.loads(line)
                        bars.append(
                            {
                                "t": row.get("as_of"),
                                "o": row.get("open"),
                                "h": row.get("high"),
                                "l": row.get("low"),
                                "c": row.get("close"),
                                "v": row.get("volume"),
                            }
                        )
                    except Exception:
                        continue
        history[sym] = bars
    return history


def record_order_event(order_record: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(ORDERS_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(order_record) + "\n")


def _run_order_lifecycle_step(
    client: AlpacaPaperClient,
    state: dict[str, Any],
    as_of_date: date,
    prices: dict[str, float],
) -> dict[str, Any] | None:
    """Daily order-lifecycle pass: refresh broker status, cancel dead orders, retry.

    Never raises: a broker hiccup must not stop the MTM record. Any failure lands
    in the returned ``errors`` and blocks submission for that run (fail-closed).
    """
    try:
        result = run_order_lifecycle(
            client,
            state=state,
            as_of_date=as_of_date,
            prices=prices,
            universe=C2_UNIVERSE,
            now=datetime.now(UTC),
            ledger_path=ORDERS_FILE.parent / LIFECYCLE_FILENAME,
            record_submit=record_order_event,
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("Order lifecycle step failed: %s", exc)
        return {"status": "error", "errors": [str(exc)]}
    logger.info("Order lifecycle: %s", summarize_order_lifecycle(result))
    return result
def resolve_reference_prices(
    client: AlpacaPaperClient,
    symbols: list[str],
    stale_closes: dict[str, float],
) -> dict[str, dict[str, Any]]:
    """Re-base limit-order references on the freshest price available now.

    Priority per symbol: latest trade -> latest quote mid -> previous session
    close. This is the fix for the 2026-11-02 batch, whose limit prices were
    computed off a reference one session old: SPY opened +49.4bps against a
    +50bps cap (0.66bps of headroom) and IWM never filled at the open at all.

    A live print more than ``MAX_REFERENCE_DEVIATION_BPS`` away from the close
    is treated as bad data: the close is kept and the reason is recorded, so a
    single bogus print cannot move an order ~50% away from the market.

    Returns ``{symbol: {price, source, as_of_utc, stale_close, detail}}`` where
    ``source`` is ``REFERENCE_PRICE_SOURCE_LIVE`` or ``..._STALE``.
    """
    resolved: dict[str, dict[str, Any]] = {}
    if not symbols:
        return resolved

    trades: dict[str, dict[str, Any]] = {}
    quotes: dict[str, dict[str, Any]] = {}
    trade_error = quote_error = None

    try:
        trades = client.get_latest_trades(symbols)
    except Exception as exc:
        trade_error = str(exc)

    missing = [s for s in symbols if s not in trades]
    if missing:
        try:
            quotes = client.get_latest_quotes(missing)
        except Exception as exc:
            quote_error = str(exc)

    for sym in symbols:
        stale_raw = stale_closes.get(sym)
        stale = float(stale_raw) if stale_raw else 0.0
        live = trades.get(sym)
        live_kind = "trade"
        if live is None and sym in quotes:
            live = quotes[sym]
            live_kind = "quote_mid"

        if live is None or stale <= 0:
            resolved[sym] = {
                "price": stale,
                "source": REFERENCE_PRICE_SOURCE_STALE,
                "as_of_utc": None,
                "stale_close": stale if stale else None,
                "detail": trade_error or quote_error or "no_live_print",
            }
            continue

        price = float(live["price"])
        deviation_bps = abs(price / stale - 1.0) * 10000.0
        if deviation_bps > MAX_REFERENCE_DEVIATION_BPS:
            logger.warning(
                "Ignoring %s %s print %.4f: %.0fbps from last close %.4f exceeds %.0fbps guard",
                sym,
                live_kind,
                price,
                deviation_bps,
                stale,
                MAX_REFERENCE_DEVIATION_BPS,
            )
            resolved[sym] = {
                "price": stale,
                "source": REFERENCE_PRICE_SOURCE_STALE,
                "as_of_utc": None,
                "stale_close": stale,
                "detail": f"live_{live_kind}_deviation_{deviation_bps:.0f}bps",
            }
            continue

        resolved[sym] = {
            "price": price,
            "source": REFERENCE_PRICE_SOURCE_LIVE,
            "as_of_utc": live.get("timestamp"),
            "stale_close": stale,
            "detail": f"live_{live_kind}",
        }
    return resolved


def _latest_trade_price(client: AlpacaPaperClient, symbol: str) -> tuple[float | None, str | None]:
    """Best-effort last price for one symbol (never raises)."""
    try:
        trades = client.get_latest_trades([symbol])
    except Exception:
        return None, None
    row = trades.get(symbol)
    if not row:
        return None, None
    return float(row["price"]), row.get("timestamp")


def _is_c2_order_for_session(order: dict[str, Any], seq_date: str) -> bool:
    """True for an open C2 parent order belonging to the given session.

    Fallback replacements are named ``..._<session>_fb`` and therefore never
    match, which keeps the step idempotent.
    """
    cid = str(order.get("client_order_id") or "")
    return cid.startswith("c2_") and cid.endswith(seq_date)


def _existing_fallback_symbols(seq_date: str) -> set[str]:
    """Symbols already replaced by a market fallback for this session."""
    found: set[str] = set()
    if not ORDERS_FILE.exists():
        return found
    for line in ORDERS_FILE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if (
            row.get("as_of_date") == seq_date
            and row.get("reference_price_source") == REFERENCE_PRICE_SOURCE_FALLBACK
            and row.get("symbol")
        ):
            found.add(str(row["symbol"]))
    return found


def run_market_fallback(
    as_of_date: date,
    dry_run: bool = False,
    client: AlpacaPaperClient | None = None,
) -> dict[str, Any]:
    """10:00 ET step: replace unfilled C2 day limits with market orders.

    Deliberately a standalone step, not part of the weekly batch: the batch has
    returned hours before 10:00 ET, and a thread/child process inside it would
    die with the cron job. Creating the cron entry for this step is a separate
    decision (COO approval) and is not part of this function.

    Scope is narrow and fail-closed:
    - only orders whose ``client_order_id`` is ``c2_<sym>_<side>_<session>``
    - only while the session is open and 10:00 ET <= now < 16:00 ET
    - cancel one order at a time via ``cancel_order`` (never ``cancel_all_orders``)
    - the market order is submitted only AFTER the open-order list no longer
      contains the canceled id, so a failed cancel cannot leave two live orders
    - TIF stays ``day`` (no GTC: pre-start contamination and double-position risk)
    """
    logger.info("Starting C2 market fallback step for session: %s", as_of_date)
    seq_date = as_of_date.isoformat()
    state = load_state()

    if state.get("kill_switch_triggered"):
        logger.critical(
            "TRADING HALTED: kill switch triggered (%s); market fallback does nothing.",
            state.get("kill_switch_event"),
        )
        return {
            "status": "halted_by_kill_switch",
            "as_of_date": seq_date,
            "kill_switch_event": state.get("kill_switch_event"),
        }

    if as_of_date < PHASE_START_DATE:
        return {
            "status": "skipped_before_phase_start",
            "as_of_date": seq_date,
            "phase_start_date": PHASE_START_DATE.isoformat(),
        }

    now_utc = datetime.now(UTC)
    now_et = now_utc.astimezone(EASTERN)
    if now_utc.date() != as_of_date:
        return {
            "status": "skipped_wrong_session",
            "as_of_date": seq_date,
            "now_utc": now_utc.isoformat(),
        }
    if now_et.time() < MARKET_FALLBACK_CUTOFF_ET:
        return {
            "status": "skipped_before_fallback_cutoff",
            "as_of_date": seq_date,
            "now_et": now_et.isoformat(),
            "cutoff_et": MARKET_FALLBACK_CUTOFF_ET.strftime("%H:%M"),
        }
    if now_et.time() >= MARKET_CLOSE_ET:
        # Day-TIF orders are already dead by the close; replacing them now would
        # create orders for the next session, which the phase rules forbid.
        return {
            "status": "skipped_after_market_close",
            "as_of_date": seq_date,
            "now_et": now_et.isoformat(),
        }
    if state.get("last_market_fallback_date") == seq_date and not dry_run:
        return {"status": "already_executed", "as_of_date": seq_date}

    if client is None:
        client = AlpacaPaperClient()

    open_orders = client.get_orders("open")
    if not isinstance(open_orders, list):
        open_orders = []
    targets = [
        o
        for o in open_orders
        if isinstance(o, dict)
        and str(o.get("status", "new")).lower() in OPEN_ORDER_STATUSES
        and _is_c2_order_for_session(o, seq_date)
    ]

    already_replaced = _existing_fallback_symbols(seq_date)
    replaced: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    submitted = 0

    if not targets:
        logger.info("No open C2 orders for %s; nothing to replace.", seq_date)
        return {"status": "no_open_orders", "as_of_date": seq_date, "replaced": [], "skipped": []}

    for order in targets:
        order_id = str(order.get("id") or "")
        cid = str(order.get("client_order_id") or "")
        sym = str(order.get("symbol") or "")
        side = str(order.get("side") or "").lower()
        try:
            remaining = float(order.get("qty") or 0.0) - float(order.get("filled_qty") or 0.0)
        except (TypeError, ValueError):
            remaining = 0.0

        if not order_id or sym not in C2_UNIVERSE or side not in ("buy", "sell") or remaining <= 0:
            skipped.append({"order_id": order_id, "symbol": sym, "reason": "unusable_order"})
            continue
        if sym in already_replaced:
            skipped.append(
                {"order_id": order_id, "symbol": sym, "reason": "already_replaced_this_session"}
            )
            continue

        qty: int | float = int(remaining) if remaining == int(remaining) else remaining
        new_cid = f"c2_{sym}_{side}_{seq_date}_fb"

        if dry_run:
            replaced.append(
                {
                    "symbol": sym,
                    "side": side,
                    "qty": qty,
                    "order_id": order_id,
                    "client_order_id": cid,
                    "dry_run": True,
                }
            )
            continue

        cancel_res = client.cancel_order(order_id)
        still_open = {
            str(o.get("id")) for o in (client.get_orders("open") or []) if isinstance(o, dict)
        }
        if order_id in still_open:
            logger.error(
                "Cancel not confirmed for %s (%s); refusing to submit a second live order.",
                order_id,
                sym,
            )
            skipped.append(
                {
                    "order_id": order_id,
                    "symbol": sym,
                    "reason": "cancel_not_confirmed",
                    "cancel_response": cancel_res,
                }
            )
            continue

        ref_price, ref_ts = _latest_trade_price(client, sym)
        order_res = client.submit_paper_order(
            symbol=sym,
            qty=qty,
            side=side,
            order_type="market",
            time_in_force="day",
            client_order_id=new_cid,
        )
        record = {
            "as_of_date": seq_date,
            "timestamp_utc": datetime.now(UTC).isoformat(),
            "symbol": sym,
            "side": side,
            "qty": qty,
            "order_type": "market",
            "time_in_force": "day",
            "reference_price": ref_price,
            "reference_price_source": REFERENCE_PRICE_SOURCE_FALLBACK,
            "reference_price_as_of_utc": ref_ts,
            "limit_price": None,
            "slippage_tolerance_bps": None,
            "replaces_order_id": order_id,
            "replaces_client_order_id": cid,
            "cancel_response": cancel_res,
            "client_order_id": new_cid,
            "alpaca_response": order_res,
        }
        record_order_event(record)
        replaced.append(record)
        submitted += 1
        logger.warning(
            "Replaced unfilled limit order %s (%s %s %s) with market order %s after 10:00 ET",
            order_id,
            side,
            qty,
            sym,
            new_cid,
        )

    if submitted:
        state["last_market_fallback_date"] = seq_date
        save_state(state)

    status = "success" if submitted else ("dry_run" if dry_run and replaced else "nothing_replaced")
    return {
        "status": status,
        "as_of_date": seq_date,
        "now_et": now_et.isoformat(),
        "replaced": replaced,
        "skipped": skipped,
        "replaced_count": submitted if not dry_run else len(replaced),
    }


def run_weekly_cycle(
    as_of_date: date,
    dry_run: bool = False,
    force: bool = False,
    force_rule_only: bool = False,
    ledger_path: Path = DEFAULT_LEDGER_PATH,
) -> dict[str, Any]:
    """Execute weekly decision step for C2 parallel run (10% and 15%)."""
    logger.info("Starting C2 weekly decision cycle for date: %s", as_of_date)
    state = load_state()

    # 0. Check Kill Switch Halt
    if state.get("kill_switch_triggered"):
        logger.critical(
            "TRADING HALTED: Kill switch previously triggered (%s). No further decisions or orders allowed.",
            state.get("kill_switch_event"),
        )
        return {
            "status": "halted_by_kill_switch",
            "as_of_date": as_of_date.isoformat(),
            "kill_switch_event": state.get("kill_switch_event"),
        }

    # 1. Phase Start Date Guard
    if as_of_date < PHASE_START_DATE:
        logger.warning(
            "Date %s is before forward phase start date %s. Skipping weekly decision cycle.",
            as_of_date,
            PHASE_START_DATE,
        )
        return {
            "status": "skipped_before_phase_start",
            "as_of_date": as_of_date.isoformat(),
            "phase_start_date": PHASE_START_DATE.isoformat(),
        }

    # 1b. Execution Timing Guard: Do not execute cycle prior to decision session date
    # Prevents writing decisions/state in a prior session, which would consume the decision
    # and trigger idempotency early-return on the actual decision date.
    current_utc_date = datetime.now(UTC).date()
    if current_utc_date < as_of_date:
        logger.info(
            "Current execution date %s is prior to decision session date %s. "
            "Skipping cycle without writing decisions or state to allow execution on decision date.",
            current_utc_date,
            as_of_date,
        )
        return {
            "status": "skipped_prior_to_decision_date",
            "current_utc_date": current_utc_date.isoformat(),
            "as_of_date": as_of_date.isoformat(),
        }

    # 2. Idempotency Check
    seq_date = as_of_date.isoformat()
    if state.get("last_decision_date") == seq_date and not force:
        logger.info(
            "Weekly decision already executed for %s; returning existing state (idempotent).",
            seq_date,
        )
        return {
            "status": "already_executed",
            "as_of_date": seq_date,
            "weights_10": state.get("current_weights_10"),
            "weights_15": state.get("current_weights_15"),
        }

    client = AlpacaPaperClient()

    # 3. Gather price history: combine local persisted history with recent Alpaca bars
    history = load_local_history()
    start_fetch = (as_of_date - timedelta(days=20)).isoformat()
    end_fetch = (as_of_date - timedelta(days=1)).isoformat()
    try:
        recent_bars = client.get_daily_bars(C2_UNIVERSE, start=start_fetch, end=end_fetch)
        for sym, b_list in recent_bars.items():
            if b_list:
                existing_dates = {str(b.get("t"))[:10] for b in history.get(sym, [])}
                for b in b_list:
                    d_b = str(b.get("t"))[:10]
                    if d_b not in existing_dates:
                        history[sym].append(b)
        state["consecutive_api_failures"] = 0
    except Exception as exc:
        logger.warning("Alpaca recent bars fetch failed (%s); using local history.", exc)
        state["consecutive_api_failures"] = state.get("consecutive_api_failures", 0) + 1

    # 4. Compute Market Features
    context = compute_features(history, as_of_date)
    logger.info(
        "Market Context computed: SPY=$%.2f (last_bar=%s), σ21_ann=%.2f%%, Regime=%s",
        context.spy_close,
        context.last_bar_date,
        context.spy_sigma21_annualized * 100,
        context.regime_quadrant,
    )

    # 5. Compute Quantitative Rules for 10% and 15%
    rule_10 = compute_rule_weights(0.10, context.spy_sigma21_daily)
    rule_15 = compute_rule_weights(0.15, context.spy_sigma21_daily)
    logger.info(
        "Rule Proposals: 10%% w_rule=%.4f, 15%% w_rule=%.4f", rule_10.w_rule, rule_15.w_rule
    )

    # 6. LLM Dynamic Decision Step (Unified LLM loop)
    llm_decision = call_llm_decider(
        context=context,
        rule_10=rule_10,
        rule_15=rule_15,
        force_rule_only=force_rule_only,
    )
    logger.info(
        "LLM Decision: intent=%s, scale=%.2f, attribution=%s, reasoning=%s",
        llm_decision.parsed_intent,
        llm_decision.weight_scale,
        llm_decision.attribution_label,
        llm_decision.reasoning[:80],
    )

    # 7. Apply LLM decision to final weights
    final_w_10 = apply_decision_to_weights(rule_10, llm_decision)
    final_w_15 = apply_decision_to_weights(rule_15, llm_decision)

    now_iso = datetime.now(UTC).isoformat()

    # 8. Record 1: 10% Vol Target
    rec_10 = {
        "decision_id": f"C2:10:{seq_date}:001",
        "timestamp_utc": now_iso,
        "candidate": "C2",
        "target_vol": 0.10,
        "inputs": context.to_inputs_dict(),
        "rule_output": rule_10.to_dict(),
        "llm_decision": llm_decision.to_record_dict(),
        "prompt_template_sha256": llm_decision.prompt_template_sha256,
        "model": llm_decision.model,
        "final_weights": final_w_10,
        "attribution_label": llm_decision.attribution_label,
        "provenance_source_sha256": V6_PROVENANCE_SHA,
    }
    h1 = append_decision_record(rec_10, ledger_path)
    logger.info("Appended Record 1 (10%%): %s (hash=%s...)", rec_10["decision_id"], h1[:12])

    # 9. Record 2: 15% Vol Target (prev_line_sha256 matches h1!)
    rec_15 = {
        "decision_id": f"C2:15:{seq_date}:002",
        "timestamp_utc": now_iso,
        "candidate": "C2",
        "target_vol": 0.15,
        "inputs": context.to_inputs_dict(),
        "rule_output": rule_15.to_dict(),
        "llm_decision": llm_decision.to_record_dict(),
        "prompt_template_sha256": llm_decision.prompt_template_sha256,
        "model": llm_decision.model,
        "final_weights": final_w_15,
        "attribution_label": llm_decision.attribution_label,
        "provenance_source_sha256": V6_PROVENANCE_SHA,
    }
    h2 = append_decision_record(rec_15, ledger_path)
    logger.info("Appended Record 2 (15%%): %s (hash=%s...)", rec_15["decision_id"], h2[:12])

    # 10. Alpaca Paper Order Execution (Primary 15% strategy with slippage guard and delta-rebalance)
    orders_placed = []
    orders_blocked: list[dict[str, Any]] = []
    current_utc_date = datetime.now(UTC).date()
    if current_utc_date < PHASE_START_DATE:
        logger.info(
            "Current date %s is prior to forward phase start date %s. "
            "Skipping broker order submission to Alpaca.",
            current_utc_date,
            PHASE_START_DATE,
        )
        dry_run = True

    if not dry_run:
        try:
            acct = client.get_account()
            logger.info(
                "Alpaca Paper Account verified: ID=%s, Equity=$%.2f, Cash=$%.2f",
                acct.masked_id,
                acct.equity,
                acct.cash,
            )
            # Anchor initial paper equity baseline if not yet locked
            if state.get("initial_paper_equity") is None or not state.get("completed_trading_days"):
                state["initial_paper_equity"] = acct.equity
                state["current_equity_15"] = acct.equity
                state["peak_equity_15"] = max(state.get("peak_equity_15", acct.equity), acct.equity)
                state["alpaca_paper_equity"] = acct.equity
            # Fetch existing positions for delta rebalancing. A failed read must
            # never look like a flat book: ``get_positions`` raises on HTTP /
            # connection failure, and sizing every symbol from an empty dict
            # would re-send the full target next to positions that already
            # exist (review finding 2026-11-09). Fail closed here instead.
            positions_gate: str | None = None
            try:
                positions_list = client.get_positions()
                if not isinstance(positions_list, list):
                    raise RuntimeError(
                        f"unexpected /positions payload: {type(positions_list).__name__}"
                    )
            except Exception as exc:
                positions_gate = f"positions_unreadable: {exc}"
                positions_list = []
                orders_blocked.append(
                    {
                        "as_of_date": seq_date,
                        "symbol": None,
                        "side": None,
                        "reason": "positions_unreadable",
                        "detail": str(exc),
                    }
                )
                logger.error(
                    "Alpaca submission skipped for %s: cannot read positions (%s)",
                    seq_date,
                    exc,
                )
            current_positions: dict[str, float] = {
                p["symbol"]: float(p.get("qty", 0.0)) for p in positions_list if "symbol" in p
            }

            # TIF/session alignment (see c2_order_lifecycle module docstring):
            # a `day` order only lives in the session it is submitted for, so the
            # session this submission would trade in must be the decision session.
            # Refusing here keeps a mislabelled order out of the book -- the exact
            # 2026-11-02 defect (order labelled 2026-11-03, expires_at 2026-11-02).
            session = resolve_session(client, datetime.now(UTC))
            session_gate: str | None = positions_gate
            if session_gate is None and session.session_date != as_of_date:
                session_gate = (
                    f"session_mismatch: order submitted now would live in "
                    f"{session.session_date} ({session.source}) but the decision session "
                    f"is {as_of_date}; not submitting (the daily order-lifecycle pass "
                    f"will reconcile the book on the next run)"
                )
                logger.warning("Alpaca submission skipped for %s: %s", seq_date, session_gate)

            # Orders already queued for this session belong to the *previous*
            # target book (typically a daily order-lifecycle retry sent before the
            # decision). Replace them: leaving both in the book would double the
            # exposure when the session opens. A failed book read is fail-closed
            # (no submission at all) because we cannot prove the book is empty.
            queued_for_session: dict[tuple[str, str], list[dict[str, Any]]] = {}
            strict_reader = getattr(client, "get_orders_strict", None)
            lenient_reader = getattr(client, "get_orders", None)
            existing_orders: list[dict[str, Any]] = []
            if strict_reader is not None or lenient_reader is not None:
                try:
                    reader = strict_reader if strict_reader is not None else lenient_reader
                    assert reader is not None
                    fetched = reader(status="all")
                    existing_orders = fetched if isinstance(fetched, list) else []
                except Exception as exc:
                    session_gate = f"order_book_unreadable: {exc}"
                    logger.error(
                        "Alpaca submission skipped for %s: cannot read the order book (%s)",
                        seq_date,
                        exc,
                    )
            for existing in existing_orders:
                cid = str(existing.get("client_order_id", ""))
                if existing.get("status") in OPEN_STATUSES and cid.startswith(
                    f"c2_{existing.get('symbol')}_{existing.get('side')}_"
                ):
                    queued_for_session.setdefault(
                        (str(existing.get("symbol")), str(existing.get("side"))), []
                    ).append(existing)
            # Re-base the limit reference on the price quoted now, not on the last
            # session close. On 2026-11-02 the stale-close reference left SPY only
            # 0.66bps under the +50bps cap and IWM never filled at the open.
            reference_prices = resolve_reference_prices(
                client, list(C2_UNIVERSE), context.universe_closes
            )

            # Submit rebalance orders targeting final_w_15 with limit slippage guard
            for sym in C2_UNIVERSE:
                if session_gate is not None:
                    break
                target_w = final_w_15.get(sym, 0.0)
                target_notional = acct.equity * target_w
                ref = reference_prices.get(sym) or {}
                sym_price = float(ref.get("price") or context.universe_closes.get(sym, 0.0) or 0.0)
                if sym_price <= 0:
                    logger.warning("No usable reference price for %s; skipping order.", sym)
                    continue
                target_shares = int(target_notional / sym_price) if sym_price > 0 else 0
                curr_shares = int(current_positions.get(sym, 0.0))
                delta_shares = target_shares - curr_shares

                if delta_shares == 0:
                    continue

                replaced: list[str] = []
                blocked_orders: list[dict[str, Any]] = []
                for stale_order in queued_for_session.get(
                    (sym, "buy" if delta_shares > 0 else "sell"), []
                ):
                    order_id = str(stale_order.get("id"))
                    # A DELETE is only a request. Queue the replacement only after the
                    # broker read-back reports a terminal status; a refused cancel, a
                    # communication failure or ``pending_cancel`` leaves the old order
                    # fillable, and sending a new client_order_id next to it would
                    # double the exposure (review finding 2026-11-09).
                    confirmation = cancel_and_confirm(client, order_id)
                    if not confirmation.confirmed:
                        blocked_orders.append(
                            {
                                "id": order_id,
                                "client_order_id": stale_order.get("client_order_id"),
                                "broker_status": confirmation.status,
                                "detail": confirmation.detail,
                            }
                        )
                        continue
                    replaced.append(str(stale_order.get("client_order_id")))

                if blocked_orders:
                    orders_blocked.append(
                        {
                            "as_of_date": seq_date,
                            "symbol": sym,
                            "side": "buy" if delta_shares > 0 else "sell",
                            "reason": "cancel_unconfirmed",
                            "orders": blocked_orders,
                        }
                    )
                    logger.error(
                        "Not submitting %s: %d superseded order(s) not confirmed canceled: %s",
                        sym,
                        len(blocked_orders),
                        [b["id"] for b in blocked_orders],
                    )
                    continue

                if replaced:
                    logger.info(
                        "Replaced %d order(s) already queued for %s: %s",
                        len(replaced),
                        sym,
                        replaced,
                    )
                    # A fill can race the cancel. Re-read positions so only the
                    # residual target delta is sent, never the original size.
                    try:
                        latest_positions = client.get_positions()
                        if not isinstance(latest_positions, list):
                            raise RuntimeError(
                                f"unexpected /positions payload: {type(latest_positions).__name__}"
                            )
                        current_positions = {
                            p["symbol"]: float(p.get("qty", 0.0))
                            for p in latest_positions
                            if isinstance(p, dict) and "symbol" in p
                        }
                    except Exception as exc:
                        orders_blocked.append(
                            {
                                "as_of_date": seq_date,
                                "symbol": sym,
                                "side": "buy" if delta_shares > 0 else "sell",
                                "reason": "positions_unreadable_after_cancel",
                                "detail": str(exc),
                            }
                        )
                        logger.error(
                            "Not submitting %s: cannot re-read positions after cancel (%s)",
                            sym,
                            exc,
                        )
                        continue
                    curr_shares = int(current_positions.get(sym, 0.0))
                    delta_shares = target_shares - curr_shares
                    if delta_shares == 0:
                        logger.info("Residual for %s is zero after cancel; nothing to send", sym)
                        continue

                if delta_shares > 0:
                    side = "buy"
                    qty = delta_shares
                    # Slippage guard: buy limit price capped at +50 bps above reference
                    limit_price = round(sym_price * (1.0 + SLIPPAGE_TOLERANCE_BPS / 10000.0), 2)
                else:
                    side = "sell"
                    qty = abs(delta_shares)
                    # Slippage guard: sell limit price floored at -50 bps below reference
                    limit_price = round(sym_price * (1.0 - SLIPPAGE_TOLERANCE_BPS / 10000.0), 2)

                # Alpaca rejects a reused client_order_id forever, so a re-run that
                # replaced an order already carrying the bare id must version the
                # replacement instead of colliding with the dead one.
                client_order_id = f"c2_{sym}_{side}_{seq_date}"
                if client_order_id in {str(o.get("client_order_id", "")) for o in existing_orders}:
                    client_order_id = next_client_order_id(existing_orders, sym, side, as_of_date)
                order_res = client.submit_paper_order(
                    symbol=sym,
                    qty=qty,
                    side=side,
                    order_type="limit",
                    time_in_force="day",
                    limit_price=limit_price,
                    client_order_id=client_order_id,
                )
                # Validity-window assertion: the broker's expires_at must cover the
                # session this order is meant to trade (Alpaca sets it to that
                # session's close for a `day` order). A mismatch means the order
                # would die before/after the intended session -> cancel it instead
                # of leaving a mislabelled order in the book.
                mismatch, window_note = window_mismatch(
                    order_res if isinstance(order_res, dict) else {}, session.session_date
                )
                window_ok = not mismatch
                window_cancel_ok: bool | None = None
                if mismatch:
                    logger.critical(
                        "Order %s %s carries a validity window that does not cover session %s: %s",
                        side,
                        sym,
                        session.session_date,
                        window_note,
                    )
                    order_id = (order_res or {}).get("id") if isinstance(order_res, dict) else None
                    if order_id and hasattr(client, "cancel_order"):
                        confirmation = cancel_and_confirm(client, str(order_id))
                        window_cancel_ok = confirmation.confirmed
                        if not confirmation.confirmed:
                            logger.error(
                                "Mislabelled order %s could not be confirmed canceled: %s",
                                order_id,
                                confirmation.detail,
                            )
                order_entry = {
                    "as_of_date": seq_date,
                    "timestamp_utc": now_iso,
                    "symbol": sym,
                    "side": side,
                    "qty": qty,
                    "order_type": "limit",
                    "time_in_force": "day",
                    "reference_price": sym_price,
                    "reference_price_source": ref.get("source", REFERENCE_PRICE_SOURCE_STALE),
                    "reference_price_as_of_utc": ref.get("as_of_utc"),
                    "reference_detail": ref.get("detail"),
                    "stale_close_price": ref.get("stale_close"),
                    "limit_price": limit_price,
                    "slippage_tolerance_bps": SLIPPAGE_TOLERANCE_BPS,
                    "client_order_id": client_order_id,
                    "session_date": session.session_date.isoformat(),
                    "session_source": session.source,
                    "window_ok": window_ok,
                    "window_note": window_note,
                    "window_cancel_confirmed": window_cancel_ok,
                    "replaced_client_order_ids": replaced,
                    "alpaca_response": order_res,
                }
                record_order_event(order_entry)
                orders_placed.append(order_entry)
                logger.info(
                    "Submitted paper order: %s %d %s @ limit $%.2f (delta from %d to %d)",
                    side,
                    qty,
                    sym,
                    limit_price,
                    curr_shares,
                    target_shares,
                )

        except Exception as exc:
            logger.warning("Paper order submission encountered: %s", exc)
            state["missed_rebalance_days"] = state.get("missed_rebalance_days", 0) + 1

    # 11. Update state
    state["last_decision_date"] = seq_date
    state["current_weights_10"] = final_w_10
    state["current_weights_15"] = final_w_15
    save_state(state)

    return {
        "status": "success",
        "as_of_date": seq_date,
        "record_10": rec_10,
        "record_15": rec_15,
        "h1": h1,
        "h2": h2,
        "orders_placed": orders_placed,
        "orders_blocked": orders_blocked,
    }


def run_daily_cycle(
    as_of_date: date,
    ledger_path: Path = DEFAULT_LEDGER_PATH,
    override_history: dict[str, list[dict[str, Any]]] | None = None,
    client: AlpacaPaperClient | None = None,
) -> dict[str, Any]:
    """Execute daily post-close batch: MTM update, attribution join, kill switch evaluation."""
    logger.info("Starting C2 daily post-close cycle for date: %s", as_of_date)
    state = load_state()

    # 0. Check Kill Switch Halt
    if state.get("kill_switch_triggered"):
        logger.critical(
            "TRADING HALTED: Kill switch previously triggered (%s). No further daily MTM processing.",
            state.get("kill_switch_event"),
        )
        return {
            "status": "halted_by_kill_switch",
            "as_of_date": as_of_date.isoformat(),
            "kill_switch_event": state.get("kill_switch_event"),
        }

    # 1. Phase Start Date Guard
    if as_of_date < PHASE_START_DATE:
        logger.warning(
            "Date %s is before forward phase start date %s. Skipping daily cycle.",
            as_of_date,
            PHASE_START_DATE,
        )
        return {
            "status": "skipped_before_phase_start",
            "as_of_date": as_of_date.isoformat(),
            "phase_start_date": PHASE_START_DATE.isoformat(),
        }

    if client is None:
        client = AlpacaPaperClient()

    # 2. Query Paper Account
    paper_equity = state.get("alpaca_paper_equity", 100000.0)
    try:
        acct = client.get_account()
        paper_equity = acct.equity
        # Anchor initial paper equity baseline if not yet locked
        if state.get("initial_paper_equity") is None:
            state["initial_paper_equity"] = acct.equity
        state["consecutive_api_failures"] = 0
    except Exception as exc:
        logger.warning("Failed to query Alpaca paper account in daily cycle: %s", exc)
        state["consecutive_api_failures"] = state.get("consecutive_api_failures", 0) + 1
        state["missed_trading_days"] = state.get("missed_trading_days", 0) + 1

    # 3. Market Features and Bar Date (post-close includes session date bar)
    history = override_history if override_history is not None else load_local_history()
    context = compute_features(history, as_of_date, include_target_date=True)

    # 4. Bar Freshness & Strict Date Match Check
    # Invariant: The bar date must exactly equal the session date being recorded (last_bar == seq_date).
    # Invariant: No record may ever be created with last_bar_date < PHASE_START_DATE (2026-11-03).
    # Stale or missing bars are treated as missed trading days and skipped safely without MTM entry.
    seq_date = as_of_date.isoformat()
    last_bar = context.last_bar_date
    phase_start_str = PHASE_START_DATE.isoformat()

    if last_bar is None or last_bar != seq_date or last_bar < phase_start_str:
        logger.warning(
            "Daily cycle skipped for session %s: bar date mismatch (last_bar=%s, required=%s, phase_start=%s). "
            "Recording as missed trading day.",
            seq_date,
            last_bar,
            seq_date,
            phase_start_str,
        )
        state["missed_trading_days"] = state.get("missed_trading_days", 0) + 1
        save_state(state)
        return {
            "status": "skipped_stale_or_missing_bar",
            "as_of_date": seq_date,
            "last_bar_date": last_bar,
            "missed_trading_days": state["missed_trading_days"],
        }

    # 4b. Idempotency Check: if this date/bar has already been applied, do not duplicate!
    if state.get("last_mtm_date") == seq_date or state.get("last_mtm_bar_date") == last_bar:
        logger.info(
            "Daily cycle already processed for date %s (bar %s). Returning existing MTM (idempotent).",
            seq_date,
            last_bar,
        )
        # The MTM row is idempotent, but the broker order book is not part of that
        # row: still refresh order status (and retry a dead book) on a re-run.
        lifecycle = _run_order_lifecycle_step(client, state, as_of_date, context.universe_closes)
        return {
            "status": "already_executed",
            "as_of_date": seq_date,
            "last_bar_date": last_bar,
            "equity_10": state["current_equity_10"],
            "equity_15": state["current_equity_15"],
            "alpaca_paper_equity": state.get("alpaca_paper_equity", paper_equity),
            "order_lifecycle": lifecycle,
        }

    # 5. Daily MTM tracking for 10% and 15%
    # 15% series uses Alpaca Paper broker equity as primary benchmark
    # Baseline is observed paper equity at start of phase (or previous day)
    prev_eq_15 = state.get("current_equity_15", state.get("initial_paper_equity", 100000.0))
    new_eq_15 = paper_equity
    ret_day_15 = (new_eq_15 / prev_eq_15 - 1.0) if prev_eq_15 > 0 else 0.0

    # 10% series tracks theoretical parallel execution with Fred DGS1 cash interest
    ret_universe_10 = sum(
        state["current_weights_10"].get(sym, 0.0) * context.universe_daily_returns.get(sym, 0.0)
        for sym in C2_UNIVERSE
    )
    cash_yield_10 = state["current_weights_10"].get("USD", 0.0) * (context.dgs1_rate / 252.0)
    ret_day_10 = ret_universe_10 + cash_yield_10

    state["current_equity_10"] = state["current_equity_10"] * (1.0 + ret_day_10)
    state["current_equity_15"] = new_eq_15
    state["alpaca_paper_equity"] = paper_equity

    state["peak_equity_10"] = max(state["peak_equity_10"], state["current_equity_10"])
    state["peak_equity_15"] = max(state["peak_equity_15"], state["current_equity_15"])

    dd_10 = state["current_equity_10"] / state["peak_equity_10"] - 1.0
    dd_15 = state["current_equity_15"] / state["peak_equity_15"] - 1.0

    now_iso = datetime.now(UTC).isoformat()
    mtm_record = {
        "date": seq_date,
        "last_bar_date": last_bar,
        "timestamp_utc": now_iso,
        "equity_10": round(state["current_equity_10"], 2),
        "equity_15": round(state["current_equity_15"], 2),
        "alpaca_paper_equity": round(paper_equity, 2),
        "daily_return_10": round(ret_day_10, 6),
        "daily_return_15": round(ret_day_15, 6),
        "drawdown_10": round(dd_10, 6),
        "drawdown_15": round(dd_15, 6),
    }

    MTM_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(MTM_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(mtm_record) + "\n")

    # 6. Kill Switch Evaluation (on primary 15% series)
    kill_eval = evaluate_kill_switches(
        current_equity=state["current_equity_15"],
        peak_equity=state["peak_equity_15"],
        daily_return=ret_day_15,
        consecutive_api_failures=state["consecutive_api_failures"],
    )
    if kill_eval.triggered:
        logger.critical(
            "EMERGENCY KILL SWITCH TRIGGERED: %s (%s). Executing full liquidation and halting.",
            kill_eval.trigger_type,
            kill_eval.details,
        )
        state["kill_switch_triggered"] = True
        state["kill_switch_event"] = {
            "trigger_type": kill_eval.trigger_type,
            "details": kill_eval.details,
            "action": kill_eval.action,
            "verdict": kill_eval.verdict,
            "date": seq_date,
            "timestamp_utc": now_iso,
        }
        try:
            client.cancel_all_orders(reason=KILL_SWITCH_CANCEL_REASON)
            liq_res = client.close_all_positions(cancel_orders=True)
            logger.warning("Emergency liquidation completed: %s", liq_res)
        except Exception as exc:
            logger.error("Failed to execute emergency liquidation on Alpaca: %s", exc)

    # 6b. Order lifecycle follow-up (paper only): refresh the broker's true order
    # status, cancel orders whose validity window already closed, and re-send the
    # part of the target book that never filled. Runs after the kill-switch
    # evaluation so a halt day never opens new exposure.
    order_lifecycle = _run_order_lifecycle_step(client, state, as_of_date, context.universe_closes)
    if order_lifecycle is not None:
        state["last_order_lifecycle"] = {
            "as_of_date": order_lifecycle.get("as_of_date"),
            "checked_at": order_lifecycle.get("checked_at"),
            "session_date": order_lifecycle.get("session_date"),
            "session_source": order_lifecycle.get("session_source"),
            "status": order_lifecycle.get("status"),
            "refreshed": len(order_lifecycle.get("refreshed", [])),
            "stale_canceled": len(order_lifecycle.get("stale_canceled", [])),
            "cancel_unconfirmed": len(order_lifecycle.get("cancel_unconfirmed", [])),
            "submitted": len(order_lifecycle.get("submitted", [])),
            "errors": order_lifecycle.get("errors", []),
        }

    # 7. Attribution Join & Matrix Update
    decisions = load_decisions(ledger_path)
    price_hist: dict[str, dict[date, float]] = {s: {} for s in C2_UNIVERSE}
    trading_dates_set = set()
    for s in C2_UNIVERSE:
        for b in history.get(s, []):
            d_val = b.get("t")
            if d_val:
                try:
                    dt = date.fromisoformat(str(d_val)[:10])
                    price_hist[s][dt] = float(b["c"])
                    trading_dates_set.add(dt)
                except Exception:
                    continue

    joined = join_outcomes(decisions, price_hist, list(trading_dates_set))
    attribution_matrix = compute_attribution_matrix(joined)

    ATTRIBUTION_JSON.parent.mkdir(parents=True, exist_ok=True)
    ATTRIBUTION_JSON.write_text(json.dumps(attribution_matrix, indent=2), encoding="utf-8")
    ATTRIBUTION_MD.write_text(format_attribution_markdown(attribution_matrix), encoding="utf-8")
    logger.info("Updated Attribution reports: %s and %s", ATTRIBUTION_JSON, ATTRIBUTION_MD)

    state["last_mtm_date"] = seq_date
    state["last_mtm_bar_date"] = last_bar
    if seq_date not in state.get("completed_trading_days", []):
        state.setdefault("completed_trading_days", []).append(seq_date)
    save_state(state)

    return {
        "status": "success",
        "as_of_date": seq_date,
        "mtm_record": mtm_record,
        "kill_switch_eval": kill_eval,
        "attribution_matrix": attribution_matrix,
        "order_lifecycle": order_lifecycle,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="C2 (vol_target 10/15 parallel) paper runner")
    parser.add_argument("--date", type=str, default="2026-11-03", help="Date in YYYY-MM-DD")
    parser.add_argument(
        "--action", choices=["weekly", "daily", "full", "fallback"], default="weekly"
    )
    parser.add_argument("--dry-run", action="store_true", help="Do not submit broker orders")
    parser.add_argument(
        "--force", action="store_true", help="Force re-run even if already executed"
    )
    parser.add_argument(
        "--force-rule-only", action="store_true", help="Skip LLM and use rule weights"
    )
    parser.add_argument(
        "--ledger", type=Path, default=DEFAULT_LEDGER_PATH, help="Path to decisions.jsonl"
    )
    args = parser.parse_args()

    target_date = date.fromisoformat(args.date)

    if args.action in ("weekly", "full"):
        res = run_weekly_cycle(
            as_of_date=target_date,
            dry_run=args.dry_run,
            force=args.force,
            force_rule_only=args.force_rule_only,
            ledger_path=args.ledger,
        )
        print("=== WEEKLY CYCLE RESULT ===")
        print(f"Status: {res.get('status')}")
        print(f"Date: {res.get('as_of_date')}")
        if "record_10" in res:
            print(f"Record 1 (10%): {res['record_10']['decision_id']}")
            print(f"Record 2 (15%): {res['record_15']['decision_id']}")
            print(f"Hash 1: {res.get('h1')}")
            print(f"Hash 2: {res.get('h2')}")
        if "orders_placed" in res:
            print(f"Orders placed: {len(res['orders_placed'])}")
        if res.get("orders_blocked"):
            print(f"Orders blocked: {len(res['orders_blocked'])}")

    if args.action in ("daily", "full"):
        d_res = run_daily_cycle(as_of_date=target_date, ledger_path=args.ledger)
        print("=== DAILY CYCLE RESULT ===")
        print(f"Status: {d_res.get('status')}")
        if "mtm_record" in d_res:
            print(f"MTM Record: {d_res['mtm_record']}")
        if "kill_switch_eval" in d_res:
            ks = d_res["kill_switch_eval"]
            print(f"Kill Switch: {ks.action} ({ks.verdict})")

    if args.action == "fallback":
        f_res = run_market_fallback(as_of_date=target_date, dry_run=args.dry_run)
        print("=== MARKET FALLBACK RESULT ===")
        print(f"Status: {f_res.get('status')}")
        print(f"Replaced: {f_res.get('replaced_count', 0)}")
        for row in f_res.get("replaced", []):
            print(
                f"  {row.get('symbol')} {row.get('side')} {row.get('qty')} -> {row.get('status', '')}"
            )
        for row in f_res.get("skipped", []):
            print(f"  skipped {row.get('symbol')} {row.get('reason')}")


if __name__ == "__main__":
    main()
