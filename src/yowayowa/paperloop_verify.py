"""paperloop_verify: R1/R2/chain/anchors/commit-before-fill checks (spec §8).

Usage:
  python -m yowayowa.paperloop_verify --data-dir ./data \
      --from 2026-10-12 --to <last day> --json data/demo-loop/verify/<ts>.json

Exit codes: 0 all pass, 1 mismatches, 2 chain broken."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

from yowayowa.paperloop import (
    CRYPTO_UNIVERSE,
    STRATEGY_BOOKS,
    ChainError,
    _anchors_ok,
    _daily_map,
    _engine_target,
    _load_journal,
    _parse_ts,
    loop_root,
)


def verify(data_dir: Path, start: date, end: date) -> dict[str, Any]:
    result: dict[str, Any] = {
        "chain_ok": False,
        "anchors_ok": False,
        "rows": 0,
        "decisions_judged": 0,
        "R1_match": 0,
        "R1_total": 0,
        "R2_match": 0,
        "R2_total": 0,
        "R2_mismatches": [],
        "R3_revised": 0,
        "commit_before_fill_violations": 0,
        "exit_code": 0,
    }
    try:
        root = loop_root(data_dir)
        journal = _load_journal(root)
    except ChainError as exc:
        result["exit_code"] = 2
        result["error"] = str(exc)
        return result
    result["chain_ok"] = True
    result["anchors_ok"] = _anchors_ok(root)
    result["rows"] = len(journal)
    decisions = [row for row in journal if row.get("event") == "decision"]
    fills_by_decision: dict[str, dict[str, Any]] = {}
    marks = {str(row.get("decision_id")) for row in journal if row.get("event") == "mark"}
    for row in journal:
        if row.get("event") == "fill":
            fills_by_decision.setdefault(str(row.get("decision_id")), row)
    for row in decisions:
        decision_date = str(row.get("decision_date", ""))
        if not (start <= date.fromisoformat(decision_date or "1970-01-01") <= end):
            continue
        if row.get("backfill") or row.get("halt_flag"):
            continue
        book = str(row.get("book"))
        if book not in STRATEGY_BOOKS:
            continue
        result["decisions_judged"] += 1
        asset = str(row.get("asset"))
        # R1: frozen-input recompute
        target_now = _engine_target(
            book,
            {other: _daily_map(root, other) for other in CRYPTO_UNIVERSE},
            date.fromisoformat(decision_date),
        )
        match = abs(target_now.get(asset, 0.0) - float(row.get("target_weight", 0.0))) <= 1e-12
        result["R1_total"] += 1
        result["R1_match"] += int(match)
        # R2: same engine comparison (identical here; the D7 scope keeps it)
        result["R2_total"] += 1
        result["R2_match"] += int(match)
        if not match:
            result["R2_mismatches"].append(
                {
                    "decision_id": row.get("decision_id"),
                    "journal_target": row.get("target_weight"),
                    "engine_target": target_now.get(asset, 0.0),
                }
            )
        # C2/C3: fill strict-after decision, fill row later than bar open
        decision_recorded = _parse_ts(row.get("recorded_at"))
        fill_row = fills_by_decision.get(str(row.get("decision_id")))
        if fill_row is None:
            continue
        bar_open = _parse_ts(fill_row.get("paper_fill_bar_open_ts", "1970-01-01T00:00:00+00:00"))
        if not (
            bar_open > decision_recorded and _parse_ts(fill_row.get("recorded_at")) >= bar_open
        ):
            result["commit_before_fill_violations"] += 1
        if str(row.get("decision_id")) not in marks:
            result.setdefault("unmarked_filled", []).append(str(row.get("decision_id")))
    if result["R1_match"] < result["R1_total"] or result["commit_before_fill_violations"]:
        result["exit_code"] = 1
    if not result["anchors_ok"]:
        result["exit_code"] = 2
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="demo loop v1.1 verifier")
    parser.add_argument("--data-dir", type=Path, default=Path("./data"))
    parser.add_argument("--from", dest="date_from", type=date.fromisoformat, required=True)
    parser.add_argument("--to", dest="date_to", type=date.fromisoformat, required=True)
    parser.add_argument("--json", dest="json_path", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        result = verify(args.data_dir, args.date_from, args.date_to)
    except ChainError as exc:
        result = {"chain_ok": False, "exit_code": 2, "error": str(exc)}
    output = json.dumps(result, ensure_ascii=False, default=str)
    if args.json_path is not None:
        args.json_path.parent.mkdir(parents=True, exist_ok=True)
        args.json_path.write_text(output, encoding="utf-8")
    print(output)
    return int(result["exit_code"])


if __name__ == "__main__":
    sys.exit(main())
