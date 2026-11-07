"""Cron entry point for C2 daily post-close batch.

Runs market data ingest, MTM calculation, kill switch evaluation, and attribution matrix update.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

# Add repo to pythonpath
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

from scripts.paper_c2.c2_runner import (  # noqa: E402
    PHASE_START_DATE,
    load_local_history,
    run_daily_cycle,
)


def get_default_session_date() -> date:
    """Determine the US market trading session to evaluate for post-close MTM.

    At morning run (e.g. 09:00 JST = 00:00 UTC), the session that just closed
    is yesterday's session date in ET. Check latest available bar date from local store.
    """
    hist = load_local_history()
    spy_bars = hist.get("SPY", [])
    if spy_bars:
        last_t = str(spy_bars[-1].get("t", ""))[:10]
        try:
            bar_date = date.fromisoformat(last_t)
            if bar_date >= PHASE_START_DATE:
                return bar_date
        except Exception:
            pass
    # Fallback: if run in early morning (before 12:00 UTC), target previous day
    now_utc = datetime.now(UTC)
    if now_utc.hour < 12:
        return (now_utc - timedelta(days=1)).date()
    return now_utc.date()


def main() -> None:
    parser = argparse.ArgumentParser(description="C2 daily post-close batch")
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="Trading session date in YYYY-MM-DD (defaults to latest closed session)",
    )
    args = parser.parse_args()

    session_date = date.fromisoformat(args.date) if args.date else get_default_session_date()
    now_str = datetime.now(UTC).isoformat()

    if session_date < PHASE_START_DATE:
        print(
            f"[{now_str}] C2 Daily Batch: Target session date ({session_date}) is before forward phase start "
            f"date ({PHASE_START_DATE}). Skipping execution safely (no chain pollution)."
        )
        return

    print(f"[{now_str}] C2 Daily Batch Starting for session date: {session_date}")
    res = run_daily_cycle(as_of_date=session_date)
    status = res.get("status")
    mtm = res.get("mtm_record", {})
    kill = res.get("kill_switch_eval")
    print(
        f"C2_DAILY_OK status={status} date={res.get('as_of_date')} "
        f"eq10={mtm.get('equity_10')} eq15={mtm.get('equity_15')} "
        f"kill_verdict={kill.verdict if kill else 'N/A'}"
    )


if __name__ == "__main__":
    main()
