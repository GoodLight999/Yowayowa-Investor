"""Cron entry point for C2 weekly pre-open batch.

Generates market features, quantitative rules (10% and 15%), calls LLM dynamic decider,
appends atomic hash-chained decision records, and executes Alpaca paper orders.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

# Add repo to pythonpath
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

from scripts.paper_c2.c2_runner import run_weekly_cycle  # noqa: E402


def main() -> None:
    today = datetime.now(UTC).date()
    print(f"[{datetime.now(UTC).isoformat()}] C2 Weekly Batch Starting for date: {today}")
    res = run_weekly_cycle(as_of_date=today, dry_run=False)
    rec10 = res.get("record_10", {})
    rec15 = res.get("record_15", {})
    print(
        f"C2_WEEKLY_OK date={res.get('as_of_date')} "
        f"rec10={rec10.get('decision_id')} rec15={rec15.get('decision_id')} "
        f"attrib={rec10.get('attribution_label')}"
    )


if __name__ == "__main__":
    main()
