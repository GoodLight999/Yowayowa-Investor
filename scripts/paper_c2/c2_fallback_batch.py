"""Cron entry point for the C2 10:00 ET market fallback step.

Replaces C2 day limit orders that are still unfilled 30 minutes after the US
open with market orders, and records ``reference_price_source="market_fallback"``
in ``orders.jsonl``. Standalone step by design: the weekly batch has long
returned by 10:00 ET, so this cannot live inside it as a thread/child process.

Registering this as a cron entry is a separate decision (COO approval); until
then it is run by hand:

    .venv/bin/python -m scripts.paper_c2.c2_fallback_batch --date 2026-11-10 --dry-run
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# Add repo to pythonpath
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

from scripts.paper_c2.c2_runner import PHASE_START_DATE, run_market_fallback  # noqa: E402

EASTERN = ZoneInfo("America/New_York")


def main() -> None:
    parser = argparse.ArgumentParser(description="C2 10:00 ET market fallback step")
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="US session date in YYYY-MM-DD (defaults to today in America/New_York)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report which orders would be replaced; cancel/submit nothing",
    )
    args = parser.parse_args()

    now_utc = datetime.now(UTC)
    session_date = (
        datetime.fromisoformat(args.date).date()
        if args.date
        else now_utc.astimezone(EASTERN).date()
    )
    print(
        f"[{now_utc.isoformat()}] C2 Market Fallback starting "
        f"(session={session_date}, now_et={now_utc.astimezone(EASTERN).isoformat()}, "
        f"dry_run={args.dry_run})"
    )

    if session_date < PHASE_START_DATE:
        print(
            f"C2_FALLBACK_OK status=skipped_before_phase_start session={session_date} "
            f"phase_start={PHASE_START_DATE}"
        )
        return

    res = run_market_fallback(as_of_date=session_date, dry_run=args.dry_run)
    print(
        f"C2_FALLBACK_OK status={res.get('status')} session={res.get('as_of_date')} "
        f"replaced={res.get('replaced_count', 0)} skipped={len(res.get('skipped', []))}"
    )
    for row in res.get("replaced", []):
        print(
            f"  replaced {row.get('symbol')} {row.get('side')} qty={row.get('qty')} "
            f"old_order={row.get('replaces_order_id')} new_order={row.get('client_order_id')}"
        )
    for row in res.get("skipped", []):
        print(f"  skipped {row.get('symbol')} reason={row.get('reason')}")


if __name__ == "__main__":
    main()
