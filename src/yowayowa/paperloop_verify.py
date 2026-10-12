"""paperloop_verify: R1/R2/chain/anchors/commit-before-fill + L1-L14 checks.

Design t_bcb87ad5 §6. One integration check that fails → exit code 1; a broken
hash chain → exit code 2 (unchanged from v1.1).

Usage:
  python -m yowayowa.paperloop_verify --data-dir ./data \
      --from 2026-10-12 --to <last day> --json data/demo-loop/verify/<ts>.json

Exit codes: 0 all pass, 1 mismatches, 2 chain broken.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

from yowayowa.paperloop import (
    BOOKS_ENABLED,
    CRYPTO_UNIVERSE,
    S01_BOOK,
    SCHEMA_V12,
    STRATEGY_BOOKS,
    ChainError,
    _anchors_ok,
    _daily_map,
    _engine_target,
    _load_journal,
    _parse_ts,
    _parse_ts_assume_utc,
    _rows_from_jsonl,
    _utc_now_text,
    loop_root,
    prereg_path,
    regime_spec_hash,
)
from yowayowa.paperloop_panel import build_panel

L1 = "L1_timestamps_are_tz_aware_utc"
L2 = "L2_seq_and_hash_chain"
L3 = "L3_one_mtm_per_book_day"
L4 = "L4_run_start_has_end"
L5 = "L5_decision_finished_at_most_once"
L6 = "L6_mtm_key_unique_and_price_bar_before_recorded"
L7 = "L7_commit_before_fill"
L8 = "L8_code_rev_on_every_row"
L9 = "L9_prereg_before_first_decision"
L10 = "L10_decision_spec_hash_matches_prereg"
L11 = "L11_one_halt_check_per_book_day_no_fills_when_halted"
L12 = "L12_fill_weight_delta_matches_decision"
L13 = "L13_panel_is_reproducible"
L14 = "L14_benchmark_trades_once_and_no_regime"

CHECKS = (L1, L2, L3, L4, L5, L6, L7, L8, L9, L10, L11, L12, L13, L14)


def _tz_ok(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _note_legacy(result: dict[str, Any], check: str, entry: dict[str, Any]) -> None:
    """List a frozen v1.1 row's violation without failing the check (§8-1)."""

    result["legacy_v11_findings"].setdefault(check, []).append(entry)


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
        "schema_live": SCHEMA_V12,
        "checks": {name: {"ok": True, "failures": []} for name in CHECKS},
        # design §8-1: the pre-v1.2 rows are frozen (never rewritten), so their
        # violations are LISTED here instead of failing L1-L14. Every entry
        # carries the reason; an empty dict means the journal has none.
        "legacy_v11_findings": {},
        "legacy_note": (
            "v1.1 rows are frozen pre-calibration rows (design section 8-1); "
            "their violations are listed under legacy_v11_findings and do not "
            "fail L1-L14. Recurrence guards (L1/L3/L5/L12) apply to v1.2 rows."
        ),
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
    v12_decisions = [row for row in decisions if row.get("schema") == SCHEMA_V12]
    result["v12_rows"] = sum(1 for row in journal if row.get("schema") == SCHEMA_V12)
    result["v12_days"] = len(
        {str(row.get("decision_date")) for row in v12_decisions}
    )
    result["v12_window"] = (
        [min((str(r.get("decision_date")) for r in v12_decisions), default=None),
         max((str(r.get("decision_date")) for r in v12_decisions), default=None)]
    )

    fills_by_decision: dict[str, dict[str, Any]] = {}
    marks = {str(row.get("decision_id")) for row in journal if row.get("event") == "mark"}
    for row in journal:
        if row.get("event") == "fill":
            fills_by_decision.setdefault(str(row.get("decision_id")), row)

    # ---- L1: every timestamp is tz-aware UTC -------------------------------
    # Frozen v1.1 prereg rows (design §8-1: existing rows are never rewritten)
    # carry a tz-less registered_at written by the pre-B4 code. They are
    # hash-chained, so correcting them in place would break L2; they are
    # REPORTED under legacy_v11_findings instead of failing L1. The exemption
    # is anchored to the first v1.2 journal row: any registration written at
    # or after the v1.2 rollout that lacks a tz still fails L1.
    first_v12_recorded: datetime | None = None
    for row in journal:
        if row.get("schema") != SCHEMA_V12:
            continue
        try:
            stamp = _parse_ts_assume_utc(row.get("recorded_at"))
        except (TypeError, ValueError, OverflowError):
            continue
        if first_v12_recorded is None or stamp < first_v12_recorded:
            first_v12_recorded = stamp
    legacy_prereg: list[dict[str, Any]] = []
    for index, row in enumerate(journal, start=1):
        for field in ("recorded_at", "paper_fill_bar_open_ts", "decision_recorded_at"):
            if field in row and not _tz_ok(row.get(field)):
                result["checks"][L1]["failures"].append(
                    {"line": index, "seq": row.get("seq"), "field": field, "value": row.get(field)}
                )
    for field_path in (prereg_path(root),):
        for line in _rows_from_jsonl(field_path):
            if _tz_ok(line.get("registered_at")):
                continue
            try:
                registered = _parse_ts_assume_utc(line.get("registered_at"))
            except (TypeError, ValueError, OverflowError):
                result["checks"][L1]["failures"].append(
                    {"file": field_path.name, "value": line.get("registered_at")}
                )
                continue
            if first_v12_recorded is not None and registered < first_v12_recorded:
                legacy_prereg.append(
                    {
                        "file": field_path.name,
                        "book_id": line.get("book_id"),
                        "registered_at": line.get("registered_at"),
                        "reason": "frozen_v1.1_row_written_before_v1.2_rollout",
                    }
                )
                continue
            result["checks"][L1]["failures"].append(
                {"file": field_path.name, "value": line.get("registered_at")}
            )
    if legacy_prereg:
        result["legacy_v11_findings"][L1] = legacy_prereg

    # ---- L2: seq 1..N and the hash chain (already enforced by the loader) --
    seqs = [int(row.get("seq", -1)) for row in journal]
    if seqs != list(range(1, len(journal) + 1)):
        result["checks"][L2]["failures"].append({"expected": f"1..{len(journal)}", "got": seqs[:20]})

    # ---- L3: exactly one mtm per (book, day) -------------------------------
    # A group is checked as soon as ONE of its rows is v1.2; a group made of
    # frozen v1.1 rows only (the known B2 duplicate) is listed as legacy.
    mtm_keys: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in journal:
        if row.get("event") == "mtm":
            mtm_keys.setdefault((str(row.get("book")), str(row.get("mtm_date"))), []).append(row)
    for (book, day), group in sorted(mtm_keys.items()):
        if len(group) < 2:
            continue
        seq_list = [int(row.get("seq") or 0) for row in group]
        entry = {"book": book, "mtm_date": day, "seqs": seq_list}
        if any(row.get("schema") == SCHEMA_V12 for row in group):
            result["checks"][L3]["failures"].append(entry)
        else:
            _note_legacy(result, L3, dict(entry, reason="frozen_v1.1_rows"))

    # ---- L4: every run start has an end; daily end ok per calendar day -----
    starts = {
        str(row.get("run_id"))
        for row in journal
        if row.get("event") == "run" and row.get("phase") == "start"
    }
    ends = {
        str(row.get("run_id"))
        for row in journal
        if row.get("event") == "run" and row.get("phase") == "end"
    }
    for run_id in sorted(starts - ends):
        result["checks"][L4]["failures"].append({"run_id": run_id, "problem": "start_without_end"})
    for run_id in sorted(ends - starts):
        result["checks"][L4]["failures"].append({"run_id": run_id, "problem": "end_without_start"})
    if starts:
        # only days that actually ran through the v1.2 run bracket need an end
        daily_ends = [
            row
            for row in journal
            if row.get("event") == "run"
            and row.get("phase") == "end"
            and row.get("job") == "daily"
            and row.get("ok")
        ]
        daily_days = {str(row.get("recorded_at"))[:10] for row in daily_ends}
        start_days = {
            str(row.get("recorded_at"))[:10]
            for row in journal
            if row.get("event") == "run" and row.get("phase") == "start"
        }
        for day in sorted(start_days - daily_days):
            result["checks"][L4]["failures"].append(
                {"day": day, "problem": "no_successful_daily_end"}
            )

    # ---- L5: at most one fill/fill_rejected per decision_id (B1) ----------
    # In scope as soon as the decision OR any of its events is v1.2: a v1.1
    # decision re-processed by v1.2 code (the original B1 bug) must still be
    # caught. Groups made entirely of frozen v1.1 rows are listed as legacy.
    per_decision: dict[str, list[dict[str, Any]]] = {}
    for row in journal:
        if row.get("event") in ("fill", "fill_rejected") and row.get("decision_id"):
            per_decision.setdefault(str(row["decision_id"]), []).append(row)
    decision_schema = {str(row.get("decision_id")): row.get("schema") for row in decisions}
    for decision_id, group in sorted(per_decision.items()):
        if len(group) < 2:
            continue
        events = [str(row.get("event")) for row in group]
        in_scope = decision_schema.get(decision_id) == SCHEMA_V12 or any(
            row.get("schema") == SCHEMA_V12 for row in group
        )
        if in_scope:
            result["checks"][L5]["failures"].append({"decision_id": decision_id, "events": events})
        else:
            _note_legacy(
                result,
                L5,
                {"decision_id": decision_id, "events": events, "reason": "frozen_v1.1_rows"},
            )

    # ---- L6: (book, mtm_date) unique (mirrors L3) and price bar < recorded -
    for row in journal:
        if row.get("event") != "mtm":
            continue
        price_bar = row.get("price_bar_date")
        if price_bar is None and row.get("schema") == SCHEMA_V12:
            result["checks"][L6]["failures"].append(
                {"seq": row.get("seq"), "problem": "missing_price_bar_date"}
            )
        elif price_bar is not None and str(price_bar) >= str(row.get("recorded_at"))[:10]:
            result["checks"][L6]["failures"].append(
                {"seq": row.get("seq"), "problem": "price_bar_after_recorded", "bar": price_bar}
            )

    # ---- L7: commit-before-fill (C2/C3) — unchanged semantics -------------
    for row in decisions:
        if row.get("backfill") or row.get("halt_flag"):
            continue
        book = str(row.get("book"))
        if book not in STRATEGY_BOOKS:
            continue
        decision_recorded = _parse_ts(row.get("recorded_at"))
        fill_row = fills_by_decision.get(str(row.get("decision_id")))
        if fill_row is None:
            continue
        bar_open = _parse_ts(fill_row.get("paper_fill_bar_open_ts", "1970-01-01T00:00:00+00:00"))
        if not (bar_open > decision_recorded and _parse_ts(fill_row.get("recorded_at")) >= bar_open):
            result["commit_before_fill_violations"] += 1
            result["checks"][L7]["failures"].append(
                {
                    "decision_id": row.get("decision_id"),
                    "decision_recorded_at": row.get("recorded_at"),
                    "bar_open": fill_row.get("paper_fill_bar_open_ts"),
                    "fill_recorded_at": fill_row.get("recorded_at"),
                }
            )

    # ---- L8: code_rev on every row; report the days it changed -------------
    revisions: dict[str, list[str]] = {}
    for row in journal:
        if row.get("schema") != SCHEMA_V12:
            continue
        rev = row.get("code_rev")
        if not rev:
            result["checks"][L8]["failures"].append({"seq": row.get("seq"), "problem": "missing"})
            continue
        stamp = str(row.get("recorded_at"))[:10]
        revisions.setdefault(str(rev), []).append(stamp)
    result["code_rev_days"] = {rev: sorted(set(days)) for rev, days in revisions.items()}

    # ---- L9: prereg registered BEFORE the book's first decision -----------
    # Both sides are normalised to aware UTC (_parse_ts_assume_utc) because
    # frozen v1.1 prereg rows carry a tz-less stamp; a stamp that cannot be
    # parsed at all is RECORDED as an L9 failure instead of raising, so the
    # verifier always returns JSON (audit-argus Round 1).
    preregs = _rows_from_jsonl(prereg_path(root), strict=True)
    prereg_at: dict[str, datetime] = {}
    for line in preregs:
        book_id = str(line.get("book_id"))
        try:
            prereg_at[book_id] = _parse_ts_assume_utc(line.get("registered_at"))
        except (TypeError, ValueError, OverflowError):
            result["checks"][L9]["failures"].append(
                {"book_id": book_id, "problem": "registered_at_not_parseable"}
            )
    first_decision: dict[str, datetime] = {}
    for row in decisions:
        if row.get("schema") != SCHEMA_V12:
            continue
        book = str(row.get("book"))
        try:
            stamp = _parse_ts_assume_utc(row.get("recorded_at"))
        except (TypeError, ValueError, OverflowError):
            result["checks"][L9]["failures"].append(
                {"book": book, "problem": "recorded_at_not_parseable"}
            )
            continue
        if book not in first_decision or stamp < first_decision[book]:
            first_decision[book] = stamp
    for book, stamp in sorted(first_decision.items()):
        registered = prereg_at.get(book)
        if registered is None:
            result["checks"][L9]["failures"].append({"book": book, "problem": "not_registered"})
        elif registered > stamp:
            result["checks"][L9]["failures"].append(
                {
                    "book": book,
                    "problem": "registered_after_first_decision",
                    "registered_at": registered.isoformat(),
                    "first_decision": stamp.isoformat(),
                }
            )

    # ---- L10: decision spec_hash matches the latest prereg ----------------
    latest_spec: dict[str, str] = {}
    for line in preregs:
        latest_spec[str(line.get("book_id"))] = str(line.get("spec_hash"))
    for row in v12_decisions:
        want = latest_spec.get(str(row.get("book")))
        got = row.get("spec_hash")
        if want is None or got != want:
            result["checks"][L10]["failures"].append(
                {
                    "decision_id": row.get("decision_id"),
                    "prereg": want,
                    "row": got,
                }
            )
    if latest_spec.get("__regime_spec__") is None:
        result["checks"][L10]["failures"].append({"problem": "regime_spec_not_preregistered"})
    elif latest_spec.get("__regime_spec__") != regime_spec_hash():
        result["checks"][L10]["failures"].append(
            {"problem": "regime_spec_hash_drift", "want": regime_spec_hash()}
        )

    # ---- L11: one halt_check per (book, day); halted days have no fills ----
    halt_keys: dict[tuple[str, str], list[int]] = {}
    halted_days: set[tuple[str, str]] = set()
    for row in journal:
        if row.get("event") != "halt_check":
            continue
        key = (str(row.get("book")), str(row.get("check_date")))
        halt_keys.setdefault(key, []).append(int(row.get("seq")))
        if row.get("halted_after"):
            halted_days.add(key)
    for (book, day), seq_list in sorted(halt_keys.items()):
        if len(seq_list) > 1:
            result["checks"][L11]["failures"].append(
                {"book": book, "check_date": day, "seqs": seq_list, "problem": "duplicate"}
            )
    expected_halt = {
        (str(row.get("book")), str(row.get("check_date")))
        for row in journal
        if row.get("event") == "halt_check"
    }
    for book in BOOKS_ENABLED:
        for day in sorted({str(r.get("decision_date")) for r in v12_decisions}):
            if (book, day) not in expected_halt:
                result["checks"][L11]["failures"].append(
                    {"book": book, "check_date": day, "problem": "missing_halt_check"}
                )
    for row in journal:
        if row.get("event") != "fill":
            continue
        day = str(row.get("paper_fill_bar_open_ts"))[:10]
        if (str(row.get("book")), day) in halted_days:
            result["checks"][L11]["failures"].append(
                {
                    "book": row.get("book"),
                    "day": day,
                    "decision_id": row.get("decision_id"),
                    "problem": "fill_on_halted_day",
                }
            )

    # ---- L12: fill weight_after - weight_before == decision delta_weight --
    # D2 recurrence guard: in scope as soon as the fill OR its decision is
    # v1.2; a mismatch between two frozen v1.1 rows is listed as legacy.
    decision_by_id = {str(row.get("decision_id")): row for row in decisions}

    def _l12_record(
        entry: dict[str, Any], row: dict[str, Any], decision: dict[str, Any]
    ) -> None:
        if row.get("schema") == SCHEMA_V12 or decision.get("schema") == SCHEMA_V12:
            result["checks"][L12]["failures"].append(entry)
        else:
            _note_legacy(result, L12, dict(entry, reason="frozen_v1.1_rows"))

    for row in journal:
        if row.get("event") != "fill":
            continue
        decision = decision_by_id.get(str(row.get("decision_id")))
        if decision is None:
            continue
        delta = float(decision.get("delta_weight") or 0.0)
        got = float(row.get("weight_after") or 0.0) - float(row.get("weight_before") or 0.0)
        # hold fills (|delta| < 1e-6) carry no trade: their weight must be
        # UNCHANGED rather than equal to a sub-threshold delta (design §3-2:
        # L12 verifies the delta-notional arithmetic on real fills)
        if str(row.get("side")) == "hold":
            if abs(got) > 1e-9:
                _l12_record(
                    {
                        "decision_id": row.get("decision_id"),
                        "decision_delta_weight": delta,
                        "fill_weight_delta": got,
                        "problem": "hold_fill_changed_weight",
                    },
                    row,
                    decision,
                )
            continue
        if abs(got - delta) > 1e-9:
            _l12_record(
                {
                    "decision_id": row.get("decision_id"),
                    "decision_delta_weight": delta,
                    "fill_weight_delta": got,
                },
                row,
                decision,
            )

    # ---- L13: the panel is built the same way twice ------------------------
    first_panel = build_panel(data_dir)
    second_panel = build_panel(data_dir)
    if first_panel["hash"] != second_panel["hash"]:
        result["checks"][L13]["failures"].append(
            {"first": first_panel["hash"], "second": second_panel["hash"]}
        )
    result["panel_hash"] = first_panel["hash"]
    result["panel_rows"] = len(first_panel["rows"])

    # ---- L14: the benchmark trades once and keeps no regime state ---------
    bench_book = "CRYPTO_1N"
    # t_08eb947a: a hold row is a no-trade (v1.1 D7 test requires hold-only
    # fill rows to exist), so L14 counts TRADES, not every bench row.
    bench_fills = [
        row
        for row in journal
        if row.get("event") == "fill"
        and str(row.get("book")) == bench_book
        and str(row.get("side")) != "hold"
    ]
    result["bench_fill_count"] = len(bench_fills)
    if len(bench_fills) > len(CRYPTO_UNIVERSE):
        result["checks"][L14]["failures"].append(
            {"problem": "benchmark_traded_more_than_once", "fills": len(bench_fills)}
        )
    for row in decisions:
        if str(row.get("book")) != bench_book:
            continue
        if row.get("regime_state") == "not_applicable":
            continue
        if row.get("schema") == SCHEMA_V12:
            result["checks"][L14]["failures"].append(
                {"decision_id": row.get("decision_id"), "regime_state": row.get("regime_state")}
            )

    # ---- R1/R2 recompute (unchanged scope: strategy books) -----------------
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
        target_now = _engine_target(
            book,
            {other: _daily_map(root, other) for other in CRYPTO_UNIVERSE},
            date.fromisoformat(decision_date),
        )
        match = abs(target_now.get(asset, 0.0) - float(row.get("target_weight", 0.0))) <= 1e-12
        result["R1_total"] += 1
        result["R1_match"] += int(match)
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
        if str(row.get("decision_id")) not in marks:
            result.setdefault("unmarked_filled", []).append(str(row.get("decision_id")))

    failing = [name for name in CHECKS if result["checks"][name]["failures"]]
    result["failed_checks"] = failing
    for name in CHECKS:
        result["checks"][name]["ok"] = not result["checks"][name]["failures"]
    if (
        failing
        or result["R1_match"] < result["R1_total"]
        or result["commit_before_fill_violations"]
    ):
        result["exit_code"] = 1
    if not result["anchors_ok"]:
        result["exit_code"] = 2
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="demo loop v1.2 verifier")
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
