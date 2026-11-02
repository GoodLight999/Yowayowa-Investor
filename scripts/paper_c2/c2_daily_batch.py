"""Cron entry point for C2 daily post-close batch.

Runs market data ingest, MTM calculation, kill switch evaluation, and attribution matrix update.
"""

from __future__ import annotations

import sys
from datetime import UTC, date, datetime
from pathlib import Path

# Add repo to pythonpath
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

from scripts.paper_c2.c2_runner import PHASE_START_DATE, run_daily_cycle  # noqa: E402


def main() -> None:
    today = datetime.now(UTC).date()
    now_str = datetime.now(UTC).isoformat()
    if today < PHASE_START_DATE:
        print(
            f"[{now_str}] C2 Daily Batch: Today ({today}) is before forward phase start "
            f"date ({PHASE_START_DATE}). Skipping execution safely (no chain pollution)."
        )
        return

    print(f"[{now_str}] C2 Daily Batch Starting for date: {today}")
    res = run_daily_cycle(as_of_date=today)
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
