"""Entry points for the demo-loop v1.2 no_agent cron jobs (paper only).

- ``python -m yowayowa.loop_cli decide``: decide (+ D3 mtm) for one day.
- ``python -m yowayowa.loop_cli fill``: resolve pending fills (+ mtm).
- ``python -m yowayowa.loop_cli mark``: bt fill prices + signed shortfall.
- ``python -m yowayowa.loop_cli report``: append one weekly summary.
- ``python -m yowayowa.loop_cli halt-check``: the daily halt_check rows.

v1.2: each job brackets its work with run start/end rows (a start without an
end is a crashed run, L4) and carries ``trigger`` so manual runs can be
excluded from the eval denominators (design §4).

Journal reads raise ChainError on corrupt lines; the CLI maps them to
exit code 2 (chain broken) per spec §8-2.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from yowayowa.paperloop import (
    ChainError,
    loop_root,
    mark_up,
    resolve_fills,
    run_daily_decide,
    weekly_summary,
    _write_daily_halt_checks,
    _new_run_id,
)


def _now() -> datetime:
    return datetime.now(UTC)


def decide(trigger: str = "cron") -> int:
    root = loop_root(Path("./data"))
    run_id = _new_run_id()
    rows = run_daily_decide(root, _now(), run_id=run_id, trigger=trigger)
    print(json.dumps({"rows_written": rows, "run_id": run_id}, ensure_ascii=False))
    return 0


def fill(trigger: str = "cron") -> int:
    root = loop_root(Path("./data"))
    run_id = _new_run_id()
    resolved = resolve_fills(root, _now(), run_id=run_id, trigger=trigger)
    print(json.dumps({"fills_resolved": resolved, "run_id": run_id}, ensure_ascii=False))
    return 0


def mark() -> int:
    root = loop_root(Path("./data"))
    marked = mark_up(root, _now())
    print(json.dumps({"rows_marked": marked}, ensure_ascii=False))
    return 0


def report() -> int:
    summary = weekly_summary(Path("./data"), _now().date())
    print(json.dumps(summary, ensure_ascii=False, default=str))
    return 0


def halt_check() -> int:
    root = loop_root(Path("./data"))
    written = _write_daily_halt_checks(root, _now())
    print(json.dumps({"halt_checks": written}, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode",
        choices=("decide", "fill", "mark", "report", "halt-check"),
    )
    parser.add_argument(
        "--trigger",
        default="cron",
        choices=("cron", "manual"),
        help="how this run was started (manual rows are excluded from eval)",
    )
    args = parser.parse_args(argv)
    try:
        if args.mode == "decide":
            return decide(args.trigger)
        if args.mode == "fill":
            return fill(args.trigger)
        if args.mode == "mark":
            return mark()
        if args.mode == "report":
            return report()
        return halt_check()
    except ChainError as exc:
        print(json.dumps({"ok": False, "error": f"ChainError: {exc}"}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
