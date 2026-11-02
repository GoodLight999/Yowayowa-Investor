"""Standalone CLI tool to verify the C2 JSONL hash chain."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .c2_ledger import DEFAULT_LEDGER_PATH, verify_hash_chain


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify C2 decisions.jsonl hash chain")
    parser.add_argument(
        "--ledger", type=Path, default=DEFAULT_LEDGER_PATH, help="Path to decisions.jsonl"
    )
    args = parser.parse_args()

    is_valid, issues = verify_hash_chain(args.ledger)
    if is_valid:
        print(f"PASS: Hash chain verified intact across all records in {args.ledger}")
        sys.exit(0)
    else:
        print(f"FAIL: Hash chain verification failed with {len(issues)} issues:")
        for iss in issues:
            print(f"  - {iss}")
        sys.exit(1)


if __name__ == "__main__":
    main()
