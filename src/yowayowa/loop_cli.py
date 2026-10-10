"""Entry points for the demo-loop v1.1 no_agent cron jobs (paper only).

- ``python -m yowayowa.loop_cli decide``: decide (+ D3 mtm) for one day.
- ``python -m yowayowa.loop_cli fill``: resolve pending fills (+ mtm).
- ``python -m yowayowa.loop_cli mark``: bt fill prices + signed shortfall.
- ``python -m yowayowa.loop_cli report``: append one weekly summary.

Journal reads raise ChainError on corrupt lines; the CLI maps them to
exit code 2 (chain broken) per spec §8-2."""

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
)


def _now() -> datetime:
    return datetime.now(UTC)


def decide() -> int:
    root = loop_root(Path("./data"))
    rows = run_daily_decide(root, _now())
    print(json.dumps({"book_rows": rows}, ensure_ascii=False))
    return 0


def fill() -> int:
    root = loop_root(Path("./data"))
    resolved = resolve_fills(root, _now())
    print(json.dumps({"fills_resolved": resolved}, ensure_ascii=False))
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("decide", "fill", "mark", "report"))
    args = parser.parse_args(argv)
    try:
        if args.mode == "decide":
            return decide()
        if args.mode == "fill":
            return fill()
        if args.mode == "mark":
            return mark()
        return report()
    except ChainError as exc:
        print(json.dumps({"ok": False, "error": f"ChainError: {exc}"}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
