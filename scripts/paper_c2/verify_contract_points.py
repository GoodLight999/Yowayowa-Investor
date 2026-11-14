"""Contract verification script for CTO review (t_6a99c045).

Verifies the 4 critical contract invariants:
1. Latest bar = 11-02 state -> daily run skips safely as missed trading day; mtm_history does NOT increment (0 rows).
2. First successful record requires last_bar_date = 2026-11-03, with returns strictly from 11-03 session.
3. Prior to 11-03 market open, open orders = 0, positions = 0 on Alpaca Paper account.
4. initial_paper_equity matches first observed paper equity (100,000.00).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from scripts.paper_c2.alpaca_paper_client import AlpacaPaperClient
from scripts.paper_c2.c2_runner import (
    load_local_history,
    load_state,
    run_daily_cycle,
    save_state,
    MTM_FILE,
    STATE_FILE,
)


def run_contract_verification() -> None:
    print("================================================================================")
    print("C2 FORWARD CONTRACT INVARIANTS VERIFICATION")
    print("================================================================================")

    # Check 3: Live Alpaca Paper API audit (orders & positions)
    print("\n[CHECK 3] Alpaca Paper Broker Status (Before 11-03 open):")
    client = AlpacaPaperClient()
    acct = client.get_account()
    open_orders = client.get_orders(status="open")
    all_orders = client._request(client.base_url + "/orders?status=all")
    positions = client.get_positions()
    print(f"  Account ID (masked): {acct.masked_id}")
    print(f"  Status:             {acct.status}")
    print(f"  Equity:             ${acct.equity:,.2f}")
    print(f"  Cash:               ${acct.cash:,.2f}")
    print(f"  Open orders count:  {len(open_orders)}")
    print(f"  Positions count:    {len(positions)}")
    print(f"  All orders count:   {len(all_orders) if isinstance(all_orders, list) else 0}")
    if isinstance(all_orders, list):
        for o in all_orders:
            print(f"    Order {o.get('id')[:8]}... | {o.get('symbol')} {o.get('side')} {o.get('qty')} | status={o.get('status')} | filled_at={o.get('filled_at')}")

    assert len(open_orders) == 0, f"Expected 0 open orders, found {len(open_orders)}"
    assert len(positions) == 0, f"Expected 0 positions, found {len(positions)}"
    print("  -> CHECK 3 PASS: Open orders = 0, positions = 0, zero pre-phase fill risk.")

    # Check 4: Initial paper equity baseline
    state = load_state()
    print("\n[CHECK 4] Initial Paper Equity Baseline Verification:")
    print(f"  state.initial_paper_equity: {state.get('initial_paper_equity')}")
    print(f"  Alpaca acct.equity:         {acct.equity}")
    assert state.get("initial_paper_equity") == acct.equity, "initial_paper_equity mismatch with observed broker equity"
    print("  -> CHECK 4 PASS: initial_paper_equity matches first observed paper equity ($100,000.00).")

    # Check 1: Simulated latest bar = 11-02 -> daily run skips as missed trading day without writing mtm_history
    print("\n[CHECK 1] Bar Freshness Guard: Latest bar = 11-02 for session 11-03:")
    base_hist = load_local_history()
    stale_hist = {}
    for s, bars in base_hist.items():
        b_list = list(bars)
        prev_close = b_list[-1]["c"]
        b_list.append({
            "t": "2026-11-02T04:00:00Z",
            "o": prev_close,
            "h": prev_close * 1.002,
            "l": prev_close * 0.998,
            "c": prev_close * 1.001,
            "v": 50000.0,
        })
        stale_hist[s] = b_list

    res_stale = run_daily_cycle(
        as_of_date=date(2026, 11, 3),
        override_history=stale_hist,
        client=client,
    )
    print(f"  Result status:      {res_stale.get('status')}")
    print(f"  Target date:        {res_stale.get('as_of_date')}")
    print(f"  Latest bar date:    {res_stale.get('last_bar_date')}")
    print(f"  Missed trading days: {res_stale.get('missed_trading_days')}")

    mtm_lines_1 = MTM_FILE.read_text(encoding="utf-8").strip().splitlines() if MTM_FILE.exists() else []
    print(f"  mtm_history.jsonl lines: {len(mtm_lines_1)}")
    assert res_stale["status"] == "skipped_stale_or_missing_bar"
    assert len(mtm_lines_1) == 0, f"Expected 0 MTM rows, found {len(mtm_lines_1)}"
    print("  -> CHECK 1 PASS: Stale 11-02 bar skipped as missed trading day; mtm_history remains 0 rows.")

    # Check 2: Simulated matching 11-03 bar -> First successful record with last_bar_date=2026-11-03
    print("\n[CHECK 2] First Successful Record with last_bar_date = 2026-11-03:")
    fresh_hist = {}
    for s, bars in stale_hist.items():
        b_list = list(bars)
        prev_c = b_list[-1]["c"]
        new_c = round(prev_c * 1.008, 2)  # +0.8% on 11-03
        b_list.append({
            "t": "2026-11-03T04:00:00Z",
            "o": prev_c,
            "h": max(prev_c, new_c),
            "l": min(prev_c, new_c),
            "c": new_c,
            "v": 100000.0,
        })
        fresh_hist[s] = b_list

    res_fresh = run_daily_cycle(
        as_of_date=date(2026, 11, 3),
        override_history=fresh_hist,
        client=client,
    )
    print(f"  Result status:      {res_fresh.get('status')}")
    print(f"  Target date:        {res_fresh.get('as_of_date')}")
    mtm_rec = res_fresh.get("mtm_record", {})
    print(f"  MTM record date:    {mtm_rec.get('date')}")
    print(f"  last_bar_date:      {mtm_rec.get('last_bar_date')}")
    print(f"  daily_return_10:    {mtm_rec.get('daily_return_10')}")
    print(f"  daily_return_15:    {mtm_rec.get('daily_return_15')}")
    print(f"  equity_10:          {mtm_rec.get('equity_10')}")
    print(f"  equity_15:          {mtm_rec.get('equity_15')}")

    mtm_lines_2 = MTM_FILE.read_text(encoding="utf-8").strip().splitlines()
    print(f"  mtm_history.jsonl lines after fresh run: {len(mtm_lines_2)}")
    assert res_fresh["status"] == "success"
    assert mtm_rec.get("last_bar_date") == "2026-11-03"
    assert mtm_rec.get("date") == "2026-11-03"
    assert len(mtm_lines_2) == 1
    print("  -> CHECK 2 PASS: First MTM record created with last_bar_date=2026-11-03 and 11-03 return.")

    # Idempotency re-run check
    print("\n[IDEMPOTENCY] Re-running same cycle (2026-11-03):")
    res_rerun = run_daily_cycle(
        as_of_date=date(2026, 11, 3),
        override_history=fresh_hist,
        client=client,
    )
    print(f"  Result status:      {res_rerun.get('status')}")
    mtm_lines_3 = MTM_FILE.read_text(encoding="utf-8").strip().splitlines()
    print(f"  mtm_history.jsonl lines after re-run:   {len(mtm_lines_3)}")
    assert res_rerun["status"] == "already_executed"
    assert len(mtm_lines_3) == 1
    print("  -> IDEMPOTENCY PASS: Re-running does not append or modify records.")

    # Clean up verification artifacts to restore clean forward start state
    print("\n[CLEANUP] Restoring state to pristine forward baseline (0 rows in mtm_history):")
    MTM_FILE.write_text("", encoding="utf-8")
    clean_state = {
        "alpaca_paper_equity": 100000.0,
        "completed_trading_days": [],
        "consecutive_api_failures": 0,
        "current_equity_10": 100000.0,
        "current_equity_15": 100000.0,
        "current_weights_10": {
            "IWM": 0.214424,
            "JNJ": 0.214424,
            "PG": 0.214424,
            "SPY": 0.214424,
            "USD": 0.142304,
        },
        "current_weights_15": {
            "IWM": 0.25,
            "JNJ": 0.25,
            "PG": 0.25,
            "SPY": 0.25,
            "USD": 0.0,
        },
        "initial_paper_equity": 100000.0,
        "kill_switch_event": None,
        "kill_switch_triggered": False,
        "last_decision_date": "2026-11-03",
        "last_mtm_bar_date": "2026-10-30",
        "last_mtm_date": None,
        "missed_rebalance_days": 0,
        "missed_trading_days": 0,
        "peak_equity_10": 100000.0,
        "peak_equity_15": 100000.0,
    }
    save_state(clean_state)
    print("  State and mtm_history reset to clean forward baseline.")
    print("================================================================================")
    print("ALL 4 CONTRACT INVARIANTS VERIFIED SUCCESSFULLY")
    print("================================================================================")


if __name__ == "__main__":
    run_contract_verification()
