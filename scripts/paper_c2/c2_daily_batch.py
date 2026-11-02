"""Cron entry point for C2 daily post-close batch.

Runs market data ingest, MTM calculation, kill switch evaluation, and attribution matrix update.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

# Add repo to pythonpath
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

from scripts.paper_c2.c2_runner import run_daily_cycle  # noqa: E402


def main() -> None:
    today = datetime.now(UTC).date()
    print(f"[{datetime.now(UTC).isoformat()}] C2 Daily Batch Starting for date: {today}")
    res = run_daily_cycle(as_of_date=today)
    mtm = res.get("mtm_record", {})
    kill = res.get("kill_switch_eval")
    print(
        f"C2_DAILY_OK date={res.get('as_of_date')} "
        f"eq10={mtm.get('equity_10')} eq15={mtm.get('equity_15')} "
        f"kill_verdict={kill.verdict if kill else 'N/A'}"
    )


if __name__ == "__main__":
    main()
