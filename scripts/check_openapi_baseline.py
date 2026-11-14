"""Fail when the generated OpenAPI document drifts from the committed baseline.

Usage:
    python scripts/check_openapi_baseline.py            # gate (CI + make verify)
    python scripts/check_openapi_baseline.py --write    # refresh after an
                                                       # intentional contract change
    python scripts/check_openapi_baseline.py --baseline /tmp/copy.json

Exit codes: 0 = identical, 1 = drift, 2 = baseline missing.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from yowayowa.openapi_baseline import (
    UPDATE_PROCEDURE_DOC,
    count_operations,
    current_spec,
    default_baseline_path,
    describe_differences,
    load_baseline,
    write_baseline,
)

WRITE_HINT = (
    "If the change is intentional, run "
    "`python scripts/check_openapi_baseline.py --write` and commit the refreshed "
    f"baseline together with the API change. Procedure: {UPDATE_PROCEDURE_DOC}"
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--baseline",
        type=Path,
        default=None,
        help="baseline file to compare against (default: docs/openapi_baseline.json)",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="rewrite the baseline from the current application instead of checking",
    )
    parser.add_argument(
        "--max-details",
        type=int,
        default=40,
        help="maximum number of value differences to print (default: 40)",
    )
    args = parser.parse_args(argv)

    baseline_path = args.baseline if args.baseline is not None else default_baseline_path()
    generated = current_spec()
    operations = count_operations(generated)

    if args.write:
        previous = load_baseline(baseline_path) if baseline_path.is_file() else None
        write_baseline(baseline_path, generated)
        if previous is None:
            print(f"Created {baseline_path} ({operations} operations).")
        else:
            accepted = describe_differences(previous, generated, max_details=0)
            print(
                f"Rewrote {baseline_path} ({operations} operations); "
                f"the previous baseline differed (report: {len(accepted)} line(s))."
            )
        return 0

    if not baseline_path.is_file():
        print(f"OpenAPI baseline not found: {baseline_path}", file=sys.stderr)
        print(
            "Create it with `python scripts/check_openapi_baseline.py --write` "
            f"(procedure: {UPDATE_PROCEDURE_DOC}).",
            file=sys.stderr,
        )
        return 2

    baseline = load_baseline(baseline_path)
    report = describe_differences(baseline, generated, max_details=args.max_details)
    if not report:
        print(
            f"OpenAPI baseline matches the generated document "
            f"({operations} operations, {baseline_path})."
        )
        return 0

    print(
        f"OpenAPI baseline drift detected: the generated document differs from {baseline_path}.",
        file=sys.stderr,
    )
    for line in report:
        print(line, file=sys.stderr)
    print(WRITE_HINT, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
