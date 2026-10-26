"""Regression net for ``paperloop_verify`` (the record verifier).

Council card t_71463646. Implementation is NOT changed here: this file only
pins what the verifier currently proves, and — just as important — what it
does not. Every test name states the breakage it injects.

Layout:
  * result-document contract + read-only guarantee
  * hash-chain / anchor reconciliation (exit 2)
  * per-check detection on a semantically broken but RE-CHAINED journal
    (exit 1), so each L-check is proven to fire on its own
  * R1/R2/R3 split semantics
  * missing data (no journal, no prereg, no market store, empty window)
  * CLI surface (exit code + --json artifact)
  * the real ``data/demo-loop`` copied to a temp dir (read-only source)

Known gaps (asserted as-is, reported to the reviewer, NOT fixed here):
  * R2 coverage collapses to zero if the market store disappears (exit stays 0)
  * a wrong --from/--to judges nothing and still exits 0
  * a recorded_at reversal inside one day is not detected
  * a duplicate decision row for the same (book, asset, day) is not detected
  * a rewrite that also re-anchors is indistinguishable (no external anchor)
  * L2's own failure branch is unreachable (the loader rejects first)
  * L1 exempts only PREREG lines, never journal rows
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

try:  # repo root on sys.path (`python -m pytest`, `uv run pytest`)
    from tests.test_paperloop import (
        _inject_bar,
        _now_of,
        _prepare,
        _stub_networks,
    )
except ModuleNotFoundError:  # only tests/ on sys.path (plain `pytest tests/...`)
    from test_paperloop import (
        _inject_bar,
        _now_of,
        _prepare,
        _stub_networks,
    )
from yowayowa import paperloop as pl
from yowayowa import paperloop_verify as pv

Rows = list[dict[str, Any]]
Mutator = Callable[[Rows], Rows]


# --- fixtures / helpers -------------------------------------------------------


def _healthy(tmp_path: Path, monkeypatch) -> tuple[Path, Path, date, date]:
    """A store where every check passes: decisions, fills, mtm, runs, anchors."""

    data_dir, root, now = _prepare(tmp_path, monkeypatch)
    pl.run_daily_decide(root, now)
    _inject_bar(root, "BTC", now + timedelta(hours=1), 101.0)
    _inject_bar(root, "ETH", now + timedelta(hours=1), 60.0)
    pl.resolve_fills(root, now + timedelta(hours=2))
    # explicit run bracket on the SAME day as the decisions (L4)
    for phase in ("start", "end"):
        pl._append_row(
            root,
            {
                "event": "run",
                "phase": phase,
                "job": "daily",
                "trigger": "cron",
                "run_id": "unit-run",
                "recorded_at": now.isoformat(),
                "host": "pytest",
                **({"rows_written": 0, "ok": True} if phase == "end" else {}),
            },
        )
    return data_dir, root, now.date() - timedelta(days=1), now.date()


def _rows(root: Path) -> Rows:
    return [
        json.loads(line)
        for line in pl.journal_path(root).read_text(encoding="utf-8").splitlines()
        if line
    ]


def _rechain(rows: Rows) -> Rows:
    """Recompute seq/prev_hash/row_hash so ONLY the semantic check can fire."""

    prev = ""
    out: Rows = []
    for seq, row in enumerate(rows, start=1):
        row = dict(row)
        row["seq"] = seq
        row["prev_hash"] = prev
        row.pop("row_hash", None)
        row["row_hash"] = pl._sha256(
            json.dumps(
                {k: v for k, v in row.items() if k != "row_hash"}, sort_keys=True, default=str
            )
        )
        prev = row["row_hash"]
        out.append(row)
    return out


def _write_journal(root: Path, rows: Rows, *, rechain: bool) -> Rows:
    if rechain:
        rows = _rechain(rows)
    body = "\n".join(json.dumps(row, ensure_ascii=False, default=str) for row in rows) + "\n"
    pl.journal_path(root).write_text(body, encoding="utf-8")
    return rows


def _reseat_anchors(root: Path, rows: Rows) -> None:
    """Point each existing anchor at the re-chained row of the same seq.

    Only used by ``_mutate``: it keeps anchors_ok True so that a semantic
    check is the ONLY thing the mutated store violates.
    """

    path = pl.anchors_path(root)
    anchors = pl._rows_from_jsonl(path)
    if not anchors:
        return
    by_seq = {int(row["seq"]): row["row_hash"] for row in rows}
    for anchor in anchors:
        anchor["row_hash"] = by_seq.get(int(anchor["seq"]), anchor["row_hash"])
    path.write_text(
        "\n".join(json.dumps(anchor, ensure_ascii=False, default=str) for anchor in anchors) + "\n",
        encoding="utf-8",
    )


def _mutate(root: Path, mutate: Mutator, *, rechain: bool = True) -> None:
    rows = _write_journal(root, mutate(_rows(root)), rechain=rechain)
    if rechain:
        _reseat_anchors(root, rows)


def _failures(result: dict[str, Any], check: str) -> list[dict[str, Any]]:
    return result["checks"][check]["failures"]


def _tree_hashes(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _first(rows: Rows, **match: Any) -> dict[str, Any]:
    for row in rows:
        if all(row.get(k) == v for k, v in match.items()):
            return row
    raise AssertionError(f"no row matching {match}")


# --- result document contract + read-only guarantee ---------------------------


def test_healthy_store_passes_and_reports_every_check(tmp_path: Path, monkeypatch) -> None:
    data_dir, root, start, end = _healthy(tmp_path, monkeypatch)
    result = pv.verify(data_dir, start, end)

    assert result["exit_code"] == 0, result["failed_checks"]
    assert result["chain_ok"] is True
    assert result["anchors_ok"] is True
    assert result["rows"] == len(_rows(root))
    assert result["failed_checks"] == []
    for name in pv.CHECKS:
        assert result["checks"][name] == {"ok": True, "failures": []}, name
    # reconciliation really ran (a verifier that judges nothing is not a pass)
    assert result["decisions_judged"] > 0
    assert result["R1_total"] == result["decisions_judged"]
    assert result["R1_match"] == result["R1_total"]
    assert result["R2_total"] == result["decisions_judged"]
    assert result["R2_match"] == result["R2_total"]
    assert result["R2_excluded_revised"] == 0
    assert result["R3_revised"] == 0
    assert result["commit_before_fill_violations"] == 0
    assert result["legacy_v11_findings"] == {}
    assert result["panel_hash"] and result["panel_rows"] > 0


def test_verify_never_writes_into_the_data_dir(tmp_path: Path, monkeypatch) -> None:
    data_dir, root, start, end = _healthy(tmp_path, monkeypatch)
    before = _tree_hashes(root)
    pv.verify(data_dir, start, end)
    assert _tree_hashes(root) == before


# --- hash chain / anchors (exit 2) -------------------------------------------


def test_tampered_row_hash_is_chain_error(tmp_path: Path, monkeypatch) -> None:
    data_dir, root, start, end = _healthy(tmp_path, monkeypatch)

    def tamper(rows: Rows) -> Rows:
        rows[-1]["row_hash"] = "0" * 64
        return rows

    _mutate(root, tamper, rechain=False)
    result = pv.verify(data_dir, start, end)

    assert result["exit_code"] == 2
    assert result["chain_ok"] is False
    assert "row_hash mismatch" in result["error"]


def test_tampered_payload_without_rehash_is_chain_error(tmp_path: Path, monkeypatch) -> None:
    data_dir, root, start, end = _healthy(tmp_path, monkeypatch)

    def tamper(rows: Rows) -> Rows:
        _first(rows, event="mtm", book=pl.S01_BOOK)["equity"] = 1e9
        return rows

    _mutate(root, tamper, rechain=False)
    result = pv.verify(data_dir, start, end)

    assert result["exit_code"] == 2
    assert result["chain_ok"] is False


def test_deleted_middle_row_is_chain_error(tmp_path: Path, monkeypatch) -> None:
    data_dir, root, start, end = _healthy(tmp_path, monkeypatch)

    def drop(rows: Rows) -> Rows:
        del rows[len(rows) // 2]
        return rows

    _mutate(root, drop, rechain=False)
    result = pv.verify(data_dir, start, end)

    assert result["exit_code"] == 2
    assert result["chain_ok"] is False
    assert "seq gap" in result["error"]


def test_reordered_rows_are_chain_error(tmp_path: Path, monkeypatch) -> None:
    data_dir, root, start, end = _healthy(tmp_path, monkeypatch)

    def swap(rows: Rows) -> Rows:
        rows[2], rows[3] = rows[3], rows[2]
        return rows

    _mutate(root, swap, rechain=False)
    result = pv.verify(data_dir, start, end)

    assert result["exit_code"] == 2
    assert result["chain_ok"] is False


def test_corrupt_json_line_is_chain_error(tmp_path: Path, monkeypatch) -> None:
    data_dir, root, start, end = _healthy(tmp_path, monkeypatch)
    path = pl.journal_path(root)
    lines = path.read_text(encoding="utf-8").splitlines()
    lines[1] = lines[1][: len(lines[1]) // 2]  # truncated JSON object
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = pv.verify(data_dir, start, end)

    assert result["exit_code"] == 2
    assert result["chain_ok"] is False
    assert "invalid JSON" in result["error"]


def test_tail_truncation_is_caught_by_the_anchor_not_the_chain(tmp_path: Path, monkeypatch) -> None:
    """Cutting the newest rows keeps a valid chain — the anchor is the net."""

    data_dir, root, start, end = _healthy(tmp_path, monkeypatch)
    anchor_seq = max(int(a["seq"]) for a in pl._rows_from_jsonl(pl.anchors_path(root)))
    keep = _rows(root)[: anchor_seq - 1]  # drop the anchored row itself
    _write_journal(root, keep, rechain=True)  # chain stays internally consistent

    result = pv.verify(data_dir, start, end)

    assert result["chain_ok"] is True, "the surviving prefix is a valid chain"
    assert result["anchors_ok"] is False, "the last anchor must point at a now-missing row"
    assert result["exit_code"] == 2


def test_anchor_pointing_at_unknown_hash_is_rejected(tmp_path: Path, monkeypatch) -> None:
    data_dir, root, start, end = _healthy(tmp_path, monkeypatch)
    anchors = pl.anchors_path(root)
    rows = [json.loads(line) for line in anchors.read_text(encoding="utf-8").splitlines() if line]
    rows.append(
        {
            "seq": 9999,
            "row_hash": "f" * 64,
            "anchor_date": "1999-01-01",
            "anchored_at": "1999-01-01T00:00:00+00:00",
        }
    )
    anchors.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    result = pv.verify(data_dir, start, end)

    assert result["anchors_ok"] is False
    assert result["exit_code"] == 2


def test_every_anchor_row_matches_a_journal_row_hash(tmp_path: Path, monkeypatch) -> None:
    _data_dir, root, _start, _end = _healthy(tmp_path, monkeypatch)
    journal_hashes = {row["row_hash"] for row in _rows(root)}
    anchors = pl._rows_from_jsonl(pl.anchors_path(root))
    assert anchors, "the healthy store anchors the day"
    assert all(str(a["row_hash"]) in journal_hashes for a in anchors)


def test_rewritten_and_reanchored_journal_is_indistinguishable(tmp_path: Path, monkeypatch) -> None:
    """There is no external anchor: rewrite + re-anchor passes every check.

    ``_mutate`` re-chains the rows AND moves the anchors file to match, which
    is exactly what an editor with write access would do. The verifier cannot
    tell, so tamper-evidence needs the anchors copied off this directory.
    """

    data_dir, root, start, end = _healthy(tmp_path, monkeypatch)

    def inflate(rows: Rows) -> Rows:
        _first(rows, event="mtm", book=pl.S01_BOOK)["equity"] = 1e9
        return rows

    _mutate(root, inflate)
    result = pv.verify(data_dir, start, end)

    assert result["chain_ok"] is True
    assert result["anchors_ok"] is True
    assert result["exit_code"] == 0, result["failed_checks"]


# --- per-check detection on a re-chained journal (exit 1) --------------------


def test_L1_naive_timestamp_on_a_live_row_fails(tmp_path: Path, monkeypatch) -> None:
    data_dir, root, start, end = _healthy(tmp_path, monkeypatch)

    def naive(rows: Rows) -> Rows:
        row = _first(rows, event="mtm", book=pl.S01_BOOK)
        row["recorded_at"] = row["recorded_at"].replace("+00:00", "")  # same instant, tz dropped
        return rows

    _mutate(root, naive)
    result = pv.verify(data_dir, start, end)

    assert result["exit_code"] == 1
    assert _failures(result, pv.L1)
    assert {f["field"] for f in _failures(result, pv.L1)} == {"recorded_at"}
    assert result["legacy_v11_findings"] == {}


def test_L2_is_redundant_with_the_loader_on_a_rechained_journal(
    tmp_path: Path, monkeypatch
) -> None:
    """L2 can only fire for a row set the loader already accepted."""

    data_dir, root, start, end = _healthy(tmp_path, monkeypatch)
    result = pv.verify(data_dir, start, end)
    assert [row["seq"] for row in _rows(root)] == list(range(1, result["rows"] + 1))
    assert _failures(result, pv.L2) == []


def test_L5_duplicate_fill_for_one_decision_fails(tmp_path: Path, monkeypatch) -> None:
    data_dir, root, start, end = _healthy(tmp_path, monkeypatch)

    def dup(rows: Rows) -> Rows:
        fill = _first(rows, event="fill", book=pl.S01_BOOK)
        rows.append(dict(fill, fill_lag_sec=99999))
        return rows

    _mutate(root, dup)
    result = pv.verify(data_dir, start, end)

    assert result["exit_code"] == 1
    assert _failures(result, pv.L5)
    assert _failures(result, pv.L5)[0]["events"] == ["fill", "fill"]


def test_L5_fill_plus_reject_for_one_decision_fails(tmp_path: Path, monkeypatch) -> None:
    data_dir, root, start, end = _healthy(tmp_path, monkeypatch)

    def fill_and_reject(rows: Rows) -> Rows:
        fill = _first(rows, event="fill", book=pl.S01_BOOK)
        rows.append(
            {
                "event": "fill_rejected",
                "decision_id": fill["decision_id"],
                "book": fill["book"],
                "asset": fill["asset"],
                "recorded_at": fill["recorded_at"],
                "reason": "injected",
            }
        )
        return rows

    _mutate(root, fill_and_reject)
    result = pv.verify(data_dir, start, end)

    assert _failures(result, pv.L5)
    assert sorted(_failures(result, pv.L5)[0]["events"]) == ["fill", "fill_rejected"]


def test_L3_duplicate_mtm_key_fails(tmp_path: Path, monkeypatch) -> None:
    data_dir, root, start, end = _healthy(tmp_path, monkeypatch)

    def dup(rows: Rows) -> Rows:
        rows.append(dict(_first(rows, event="mtm", book=pl.S01_BOOK)))
        return rows

    _mutate(root, dup)
    result = pv.verify(data_dir, start, end)

    assert _failures(result, pv.L3)
    assert _failures(result, pv.L3)[0]["book"] == pl.S01_BOOK


def test_L4_run_start_without_end_fails(tmp_path: Path, monkeypatch) -> None:
    data_dir, root, start, end = _healthy(tmp_path, monkeypatch)

    def drop_end(rows: Rows) -> Rows:
        return [r for r in rows if not (r.get("event") == "run" and r.get("phase") == "end")]

    _mutate(root, drop_end)
    result = pv.verify(data_dir, start, end)

    assert result["exit_code"] == 1
    problems = {f["problem"] for f in _failures(result, pv.L4)}
    assert "start_without_end" in problems
    assert "no_successful_daily_end" in problems


def test_L6_missing_price_bar_date_on_a_live_mtm_fails(tmp_path: Path, monkeypatch) -> None:
    data_dir, root, start, end = _healthy(tmp_path, monkeypatch)

    def strip(rows: Rows) -> Rows:
        row = _first(rows, event="mtm", book=pl.S01_BOOK)
        row.pop("price_bar_date", None)
        return rows

    _mutate(root, strip)
    result = pv.verify(data_dir, start, end)

    assert result["exit_code"] == 1
    assert {"seq", "problem"} <= set(_failures(result, pv.L6)[0])
    assert _failures(result, pv.L6)[0]["problem"] == "missing_price_bar_date"


def test_L7_fill_that_does_not_postdate_the_commit_fails(tmp_path: Path, monkeypatch) -> None:
    """commit-before-fill: the fill's bar must OPEN after the decision stamp."""

    data_dir, root, start, end = _healthy(tmp_path, monkeypatch)

    def rewind(rows: Rows) -> Rows:
        fill = _first(rows, event="fill", book=pl.S01_BOOK, side="buy")
        decision = _first(rows, event="decision", **{"decision_id": fill["decision_id"]})
        fill["paper_fill_bar_open_ts"] = decision["recorded_at"]
        return rows

    _mutate(root, rewind)
    result = pv.verify(data_dir, start, end)

    assert result["exit_code"] == 1
    assert result["commit_before_fill_violations"] >= 1
    assert _failures(result, pv.L7)


def test_L8_missing_code_rev_on_a_live_row_fails(tmp_path: Path, monkeypatch) -> None:
    data_dir, root, start, end = _healthy(tmp_path, monkeypatch)

    def strip(rows: Rows) -> Rows:
        _first(rows, event="mtm", book=pl.S01_BOOK).pop("code_rev", None)
        return rows

    _mutate(root, strip)
    result = pv.verify(data_dir, start, end)

    assert result["exit_code"] == 1
    assert _failures(result, pv.L8)
    assert _failures(result, pv.L8)[0]["problem"] == "missing"


def test_L9_preregistered_after_the_first_decision_fails(tmp_path: Path, monkeypatch) -> None:
    data_dir, root, start, end = _healthy(tmp_path, monkeypatch)
    path = pl.prereg_path(root)
    preregs = pl._rows_from_jsonl(path, strict=True)
    for line in preregs:
        if line.get("book_id") != "__regime_spec__":
            line["registered_at"] = "2099-01-01T00:00:00+00:00"
    path.write_text("\n".join(json.dumps(p) for p in preregs) + "\n", encoding="utf-8")

    result = pv.verify(data_dir, start, end)

    assert result["exit_code"] == 1
    problems = {f.get("problem") for f in _failures(result, pv.L9)}
    assert "registered_after_first_decision" in problems


def test_L9_and_L10_fail_cleanly_when_the_prereg_file_is_missing(
    tmp_path: Path, monkeypatch
) -> None:
    """Missing prereg is a reported violation, never an exception."""

    data_dir, root, start, end = _healthy(tmp_path, monkeypatch)
    pl.prereg_path(root).unlink()

    result = pv.verify(data_dir, start, end)

    assert result["exit_code"] == 1
    assert result["chain_ok"] is True
    assert {f.get("problem") for f in _failures(result, pv.L9)} == {"not_registered"}
    l10 = {f.get("problem") for f in _failures(result, pv.L10) if f.get("problem")}
    assert "regime_spec_not_preregistered" in l10
    assert all(f["prereg"] is None for f in _failures(result, pv.L10) if "decision_id" in f)


def test_L10_decision_spec_hash_drift_fails(tmp_path: Path, monkeypatch) -> None:
    data_dir, root, start, end = _healthy(tmp_path, monkeypatch)

    def drift(rows: Rows) -> Rows:
        decision = _first(rows, event="decision", book=pl.S01_BOOK)
        decision["spec_hash"] = "deadbeef" * 8
        return rows

    _mutate(root, drift)
    result = pv.verify(data_dir, start, end)

    assert result["exit_code"] == 1
    fails = _failures(result, pv.L10)
    assert any(f.get("row") == "deadbeef" * 8 for f in fails)
    assert all(f.get("prereg") for f in fails if "decision_id" in f)


def test_L10_regime_spec_drift_fails(tmp_path: Path, monkeypatch) -> None:
    data_dir, root, start, end = _healthy(tmp_path, monkeypatch)
    path = pl.prereg_path(root)
    preregs = pl._rows_from_jsonl(path, strict=True)
    for line in preregs:
        if line.get("book_id") == "__regime_spec__":
            line["spec_hash"] = "0" * 64
    path.write_text("\n".join(json.dumps(p) for p in preregs) + "\n", encoding="utf-8")

    result = pv.verify(data_dir, start, end)

    assert result["exit_code"] == 1
    problems = {f.get("problem") for f in _failures(result, pv.L10)}
    assert "regime_spec_hash_drift" in problems


def test_L11_duplicate_halt_check_fails(tmp_path: Path, monkeypatch) -> None:
    data_dir, root, start, end = _healthy(tmp_path, monkeypatch)

    def dup(rows: Rows) -> Rows:
        rows.append(dict(_first(rows, event="halt_check", book=pl.S01_BOOK)))
        return rows

    _mutate(root, dup)
    result = pv.verify(data_dir, start, end)

    assert result["exit_code"] == 1
    fails = _failures(result, pv.L11)
    assert any(f.get("problem") == "duplicate" for f in fails)


def test_L11_missing_halt_check_for_a_traded_day_fails(tmp_path: Path, monkeypatch) -> None:
    data_dir, root, start, end = _healthy(tmp_path, monkeypatch)

    def drop(rows: Rows) -> Rows:
        return [
            r for r in rows if not (r.get("event") == "halt_check" and r.get("book") == pl.S01_BOOK)
        ]

    _mutate(root, drop)
    result = pv.verify(data_dir, start, end)

    assert result["exit_code"] == 1
    fails = _failures(result, pv.L11)
    assert any(
        f.get("problem") == "missing_halt_check" and f.get("book") == pl.S01_BOOK for f in fails
    )


def test_L12_hold_fill_that_changes_weight_fails(tmp_path: Path, monkeypatch) -> None:
    """A hold row carries no trade, so its weight must stay unchanged."""

    data_dir, root, start, end = _healthy(tmp_path, monkeypatch)

    def bump(rows: Rows) -> Rows:
        fill = _first(rows, event="fill", book=pl.S01_BOOK, side="buy")
        fill["side"] = "hold"
        fill["weight_before"] = 0.5
        fill["weight_after"] = 0.9  # a hold must not move the weight
        return rows

    _mutate(root, bump)
    result = pv.verify(data_dir, start, end)

    assert result["exit_code"] == 1
    fails = _failures(result, pv.L12)
    assert any(f.get("problem") == "hold_fill_changed_weight" for f in fails)


def test_L12_hold_fill_with_unchanged_weight_passes(tmp_path: Path, monkeypatch) -> None:
    data_dir, root, start, end = _healthy(tmp_path, monkeypatch)

    def hold(rows: Rows) -> Rows:
        fill = _first(rows, event="fill", book=pl.S01_BOOK, side="buy")
        fill["side"] = "hold"
        fill["weight_before"] = 0.5
        fill["weight_after"] = 0.5
        return rows

    _mutate(root, hold)
    result = pv.verify(data_dir, start, end)

    assert _failures(result, pv.L12) == []
    assert result["exit_code"] == 0, result["failed_checks"]


def test_L14_bench_fill_beyond_the_universe_fails(tmp_path: Path, monkeypatch) -> None:
    data_dir, root, start, end = _healthy(tmp_path, monkeypatch)

    def extra(rows: Rows) -> Rows:
        fill = dict(_first(rows, event="fill", book=pl.BENCH_CRYPTO_BOOK))
        fill["decision_id"] = "injected-bench-extra"
        rows.append(fill)
        return rows

    _mutate(root, extra)
    result = pv.verify(data_dir, start, end)

    assert result["exit_code"] == 1
    assert result["bench_fill_count"] > len(pl.CRYPTO_UNIVERSE)
    fails = _failures(result, pv.L14)
    assert any(f.get("problem") == "benchmark_traded_more_than_once" for f in fails)


def test_L14_bench_decision_with_a_regime_state_fails(tmp_path: Path, monkeypatch) -> None:
    data_dir, root, start, end = _healthy(tmp_path, monkeypatch)

    def tag(rows: Rows) -> Rows:
        decision = _first(rows, event="decision", book=pl.BENCH_CRYPTO_BOOK)
        decision["regime_state"] = "trend_on"
        return rows

    _mutate(root, tag)
    result = pv.verify(data_dir, start, end)

    assert result["exit_code"] == 1
    fails = _failures(result, pv.L14)
    assert any(f.get("regime_state") == "trend_on" for f in fails)


def test_L14_passes_when_the_benchmark_day_halts(tmp_path: Path, monkeypatch) -> None:
    """t_f1aa50e5: the FIRST stale-data day must not turn verify red forever.

    Production shape (audit t_a9b4d5f4): with the price store frozen for days,
    the freshness trigger halts every book; the HALT branch used to stamp
    regime_state="HALT" onto the benchmark books too, which L14's no-regime
    clause fails — and the appended rows are never rewritten, so every later
    weekly run would stay exit 1. The HALT branch must apply the same rule as
    the normal path ("benchmark books carry no strategy state").
    """

    data_dir, root, start, end = _healthy(tmp_path, monkeypatch)
    stale_now = _now_of(end + timedelta(days=8))  # crypto lag ~8d (>26h), equity ~9d (>2d)
    assert pl.run_daily_decide(root, stale_now) > 0

    def halt_rows(book: str) -> Rows:
        return [
            row
            for row in _rows(root)
            if row.get("event") == "decision"
            and row.get("book") == book
            and row.get("decision_date") == stale_now.date().isoformat()
            and row.get("halt_flag")
        ]

    bench = halt_rows(pl.BENCH_CRYPTO_BOOK)
    assert len(bench) == len(pl.CRYPTO_UNIVERSE)  # the synthetic day really halted
    for row in bench:
        assert row["regime_state"] == "not_applicable"
        assert row["halt_flag"] is True
        assert row["halt_reason"]
        assert row["fill_status"] == "skipped"
    strategy = halt_rows(pl.S01_BOOK)
    assert strategy  # strategy books keep their own HALT semantics unchanged
    for row in strategy:
        assert row["regime_state"] == "HALT"

    result = pv.verify(data_dir, start, end)

    assert _failures(result, pv.L14) == []
    assert result["exit_code"] == 0, result["failed_checks"]
    assert result["bench_fill_count"] == len(pl.CRYPTO_UNIVERSE)


def test_L14_bench_HALT_stays_a_failure_even_when_halted(tmp_path: Path, monkeypatch) -> None:
    """t_f1aa50e5 (verifier stays strict): halt_flag=true does NOT excuse
    regime_state="HALT" on a benchmark decision row — the writer must emit
    not_applicable (relaxing L14 instead would break this pin).
    """

    data_dir, root, start, end = _healthy(tmp_path, monkeypatch)

    def tag(rows: Rows) -> Rows:
        decision = _first(rows, event="decision", book=pl.BENCH_CRYPTO_BOOK)
        decision["regime_state"] = "HALT"
        decision["halt_flag"] = True
        decision["halt_reason"] = "stale_hours"
        decision["fill_status"] = "skipped"
        return rows

    _mutate(root, tag)
    result = pv.verify(data_dir, start, end)

    assert result["exit_code"] == 1
    fails = _failures(result, pv.L14)
    assert any(f.get("regime_state") == "HALT" for f in fails)


# --- R1 / R2 / R3 -------------------------------------------------------------


def _judged_input_file(root: Path, decision_date: str, book: str) -> Path:
    return pl.inputs_dir(root) / decision_date / f"{book}.json"


def test_R1_edited_frozen_input_fails_the_run(tmp_path: Path, monkeypatch) -> None:
    data_dir, root, start, end = _healthy(tmp_path, monkeypatch)
    decision = _first(_rows(root), event="decision", book=pl.S01_BOOK)
    frozen = _judged_input_file(root, str(decision["decision_date"]), pl.S01_BOOK)
    assert frozen.exists()
    frozen.write_text(frozen.read_text(encoding="utf-8") + " ", encoding="utf-8")

    result = pv.verify(data_dir, start, end)

    assert result["exit_code"] == 1
    assert result["R1_match"] < result["R1_total"]
    assert any(m["kind"] == "frozen_file_hash" for m in result["R1_mismatches"])
    # R1 reads the frozen FILE; the STORE is untouched, so nothing is revised
    assert result["R3_revised"] == 0
    assert result["R2_match"] == result["R2_total"]


def test_R2_tampered_target_weight_fails_the_run(tmp_path: Path, monkeypatch) -> None:
    data_dir, root, start, end = _healthy(tmp_path, monkeypatch)

    def shift(rows: Rows) -> Rows:
        decision = _first(rows, event="decision", book=pl.S01_BOOK)
        decision["target_weight"] = float(decision.get("target_weight") or 0.0) + 0.25
        return rows

    _mutate(root, shift)
    result = pv.verify(data_dir, start, end)

    assert result["exit_code"] == 1
    assert result["R2_match"] < result["R2_total"]
    mismatch = result["R2_mismatches"][0]
    assert {"decision_id", "journal_target", "engine_target"} <= set(mismatch)


def test_R3_revision_is_counted_not_failed(tmp_path: Path, monkeypatch) -> None:
    """Editing the OHLCV store after the freeze is a data_revised fact."""

    data_dir, _root, start, end = _healthy(tmp_path, monkeypatch)
    ohlcv = data_dir / "crypto-ohlcv" / "BTC" / "ohlcv.jsonl"
    lines = ohlcv.read_text(encoding="utf-8").splitlines()
    # revise the LAST bar (inside the SMA window the freeze payload carries)
    newest = json.loads(lines[-1])
    newest["close"] = float(newest["close"]) * 1.01
    lines[-1] = json.dumps(newest)
    ohlcv.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = pv.verify(data_dir, start, end)

    assert result["R3_revised"] >= 1
    assert result["R3_revised_ids"]
    assert result["R2_excluded_revised"] == result["R3_revised"]
    assert result["R2_total"] == result["decisions_judged"] - result["R3_revised"]
    assert result["exit_code"] == 0, result["failed_checks"]
    assert result["R1_match"] == result["R1_total"]


def test_missing_market_store_degrades_R2_to_zero_coverage(tmp_path: Path, monkeypatch) -> None:
    """No store => every decision reads as data_revised => R2 judges nothing.

    Fail-open on the R2 side: exit stays 0 while coverage collapses. Asserted
    as-is (documented limitation, not silently "fixed" here).
    """

    data_dir, _root, start, end = _healthy(tmp_path, monkeypatch)
    shutil.rmtree(data_dir / "crypto-ohlcv")
    shutil.rmtree(data_dir / "stock-ohlcv", ignore_errors=True)

    result = pv.verify(data_dir, start, end)

    assert result["decisions_judged"] > 0
    assert result["R3_revised"] == result["decisions_judged"]
    assert result["R2_total"] == 0
    assert result["R2_excluded_revised"] == result["decisions_judged"]
    assert result["exit_code"] == 0


def test_window_outside_the_decisions_judges_nothing(tmp_path: Path, monkeypatch) -> None:
    """A wrong --from/--to silently judges zero decisions instead of erroring."""

    data_dir, _root, _start, _end = _healthy(tmp_path, monkeypatch)
    result = pv.verify(data_dir, date(2030, 1, 1), date(2030, 1, 2))

    assert result["rows"] > 0
    assert result["decisions_judged"] == 0
    assert result["R1_total"] == 0
    assert result["R2_total"] == 0
    assert result["exit_code"] == 0


def test_an_R1_break_inside_the_window_is_invisible_outside_it(tmp_path: Path, monkeypatch) -> None:
    """Same run, same tampered freeze file: in-window fails, out-of-window passes."""

    data_dir, root, start, end = _healthy(tmp_path, monkeypatch)
    decision = _first(_rows(root), event="decision", book=pl.S01_BOOK)
    frozen = _judged_input_file(root, str(decision["decision_date"]), pl.S01_BOOK)
    frozen.write_text(frozen.read_text(encoding="utf-8") + " ", encoding="utf-8")

    inside = pv.verify(data_dir, start, end)
    outside = pv.verify(data_dir, date(2030, 1, 1), date(2030, 1, 2))

    assert inside["exit_code"] == 1
    assert outside["exit_code"] == 0


# --- missing data / empty store ----------------------------------------------


def test_no_store_at_all_is_not_a_pass(tmp_path: Path, monkeypatch) -> None:
    """An empty data dir must fail closed (regime spec is not preregistered)."""

    _stub_networks(monkeypatch)
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    result = pv.verify(data_dir, date(2026, 10, 1), date(2026, 10, 2))

    assert result["chain_ok"] is True  # no journal -> nothing to chain
    assert result["rows"] == 0
    assert result["decisions_judged"] == 0
    assert result["exit_code"] == 1
    assert {f.get("problem") for f in _failures(result, pv.L10)} == {
        "regime_spec_not_preregistered"
    }


def test_journal_without_any_live_row_judges_nothing_but_keeps_the_chain(
    tmp_path: Path, monkeypatch
) -> None:
    _stub_networks(monkeypatch)
    data_dir = tmp_path / "data"
    root = pl.loop_root(data_dir)
    root.mkdir(parents=True)
    pl._append_row_v11(
        root,
        {
            "event": "decision",
            "book": pl.S01_BOOK,
            "asset": "BTC",
            "decision_date": "2026-10-12",
            "recorded_at": "2026-10-12T00:00:00",
        },
    )

    result = pv.verify(data_dir, date(2026, 10, 12), date(2026, 10, 12))

    assert result["chain_ok"] is True
    assert result["rows"] == 1
    assert result["live_days"] == 0  # no v1.2/v1.3 row at all
    # a v1.1 JOURNAL row gets no legacy exemption: a tz-less stamp fails L1 here
    # (only PREREG lines written before the rollout are listed as legacy)
    assert {f["field"] for f in _failures(result, pv.L1)} == {"recorded_at"}
    assert result["legacy_v11_findings"] == {}
    # R1/R2 do not filter on schema: an unjudgeable decision fails closed
    assert result["decisions_judged"] == 1
    assert result["R1_mismatches"][0]["kind"] == "frozen_file_hash"
    assert result["exit_code"] == 1


def test_legacy_prereg_row_before_the_rollout_is_listed_not_failed(
    tmp_path: Path, monkeypatch
) -> None:
    """The one documented exemption: a tz-less PREREG line older than v1.2."""

    data_dir, root, start, end = _healthy(tmp_path, monkeypatch)
    path = pl.prereg_path(root)
    preregs = pl._rows_from_jsonl(path, strict=True)
    book_rows = [p for p in preregs if p.get("book_id") == pl.S01_BOOK]
    assert book_rows, "book A must be preregistered"
    legacy = dict(book_rows[-1])
    legacy["registered_at"] = "2020-01-01T00:00:00"  # tz-less, before the rollout
    preregs.append(legacy)
    path.write_text("\n".join(json.dumps(p) for p in preregs) + "\n", encoding="utf-8")

    result = pv.verify(data_dir, start, end)

    assert result["checks"][pv.L1]["failures"] == []
    listed = result["legacy_v11_findings"][pv.L1]
    assert listed[0]["registered_at"] == "2020-01-01T00:00:00"
    assert listed[0]["reason"] == "frozen_v1.1_row_written_before_v1.2_rollout"
    assert result["exit_code"] == 0, result["failed_checks"]


def test_decision_without_its_frozen_input_fails_closed(tmp_path: Path, monkeypatch) -> None:
    """Missing data is not a pass: R1 fails when the freeze file is gone."""

    data_dir, root, start, end = _healthy(tmp_path, monkeypatch)
    decision = _first(_rows(root), event="decision", book=pl.S01_BOOK)
    _judged_input_file(root, str(decision["decision_date"]), pl.S01_BOOK).unlink()
    # one freeze file serves every asset of that (book, day)
    affected = {
        row["decision_id"]
        for row in _rows(root)
        if row.get("event") == "decision"
        and row.get("book") == pl.S01_BOOK
        and str(row.get("decision_date")) == str(decision["decision_date"])
    }

    result = pv.verify(data_dir, start, end)

    assert result["exit_code"] == 1
    assert result["R1_match"] == result["R1_total"] - len(affected)
    assert {m["decision_id"] for m in result["R1_mismatches"]} == affected


# --- CLI surface --------------------------------------------------------------


def test_main_returns_the_json_exit_code_and_writes_it(tmp_path: Path, monkeypatch, capsys) -> None:
    data_dir, _root, start, end = _healthy(tmp_path, monkeypatch)
    out = tmp_path / "verify" / "run.json"

    code = pv.main(
        [
            "--data-dir",
            str(data_dir),
            "--from",
            start.isoformat(),
            "--to",
            end.isoformat(),
            "--json",
            str(out),
        ]
    )

    printed = json.loads(capsys.readouterr().out)
    assert code == 0
    assert out.exists()
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["exit_code"] == code
    assert written["chain_ok"] is True
    assert printed["exit_code"] == code


def test_main_returns_2_on_a_broken_chain(tmp_path: Path, monkeypatch, capsys) -> None:
    data_dir, root, start, end = _healthy(tmp_path, monkeypatch)
    _mutate(root, lambda rows: [dict(rows[0], row_hash="1" * 64), *rows[1:]], rechain=False)

    code = pv.main(
        ["--data-dir", str(data_dir), "--from", start.isoformat(), "--to", end.isoformat()]
    )

    printed = json.loads(capsys.readouterr().out)
    assert code == 2
    assert printed["chain_ok"] is False
    assert "row_hash mismatch" in printed["error"]


# --- time ordering ------------------------------------------------------------


def test_recorded_at_reversal_inside_a_day_is_not_detected(tmp_path: Path, monkeypatch) -> None:
    """No check enforces monotone recorded_at: an in-day reversal passes.

    Asserted as-is (documented gap): L2 covers seq order, not wall-clock order.
    """

    data_dir, root, start, end = _healthy(tmp_path, monkeypatch)

    def reverse(rows: Rows) -> Rows:
        mtms = [r for r in rows if r.get("event") == "mtm"]
        assert len(mtms) >= 2
        first, second = mtms[0], mtms[1]
        assert str(first["recorded_at"])[:10] == str(second["recorded_at"])[:10]
        first["recorded_at"], second["recorded_at"] = second["recorded_at"], first["recorded_at"]
        return rows

    _mutate(root, reverse)
    result = pv.verify(data_dir, start, end)

    assert result["exit_code"] == 0, result["failed_checks"]


def test_duplicate_decision_row_is_not_detected(tmp_path: Path, monkeypatch) -> None:
    """Two decisions for the same (book, asset, day) both pass every check."""

    data_dir, root, start, end = _healthy(tmp_path, monkeypatch)
    baseline = pv.verify(data_dir, start, end)["decisions_judged"]

    def dup(rows: Rows) -> Rows:
        original = _first(rows, event="decision", book=pl.S01_BOOK)
        clone = dict(original)
        clone["decision_id"] = f"{original['decision_id']}-dup"
        rows.append(clone)
        return rows

    _mutate(root, dup)
    result = pv.verify(data_dir, start, end)

    assert result["decisions_judged"] == baseline + 1  # the duplicate is counted too
    assert result["exit_code"] == 0, result["failed_checks"]


def test_mtm_priced_from_a_future_bar_fails(tmp_path: Path, monkeypatch) -> None:
    data_dir, root, start, end = _healthy(tmp_path, monkeypatch)

    def future(rows: Rows) -> Rows:
        for row in rows:
            if row.get("event") == "mtm":
                row["price_bar_date"] = "2999-12-31"
        return rows

    _mutate(root, future)
    result = pv.verify(data_dir, start, end)

    assert result["exit_code"] == 1
    fails = _failures(result, pv.L6)
    assert fails and all(f.get("problem") == "price_bar_after_recorded" for f in fails)


# --- the real data/demo-loop, copied (never touched in place) ----------------

REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_DATA = REPO_ROOT / "data"
_real_loop = pytest.mark.skipif(
    not (REAL_DATA / "demo-loop" / "journal.jsonl").exists(),
    reason="data/demo-loop is not present (data/ is git-ignored)",
)


def _copy_real_store(tmp_path: Path) -> tuple[Path, date, date]:
    """Copy the live demo loop + market stores into tmp_path (source is read-only)."""

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    for name in ("demo-loop", "crypto-ohlcv", "stock-ohlcv"):
        source = REAL_DATA / name
        if source.exists():
            shutil.copytree(source, data_dir / name)
    rows = _rows(data_dir / "demo-loop")
    days = sorted(
        {
            str(row.get("decision_date"))
            for row in rows
            if row.get("event") == "decision" and row.get("decision_date")
        }
    )
    assert days, "the real journal must contain decisions"
    return data_dir, date.fromisoformat(days[0]) - timedelta(days=1), date.fromisoformat(days[-1])


@_real_loop
def test_real_demo_loop_copy_verifies_clean_and_source_is_untouched(tmp_path: Path) -> None:
    before = _tree_hashes(REAL_DATA / "demo-loop")
    data_dir, start, end = _copy_real_store(tmp_path)

    result = pv.verify(data_dir, start, end)

    assert _tree_hashes(REAL_DATA / "demo-loop") == before, "the real data must only ever be read"
    assert result["chain_ok"] is True
    assert result["anchors_ok"] is True
    assert result["decisions_judged"] > 0
    assert result["R1_match"] == result["R1_total"]
    assert result["failed_checks"] == [], result["failed_checks"]
    assert result["exit_code"] == 0


@_real_loop
def test_real_journal_keeps_frozen_v11_rows_out_of_the_failing_set(tmp_path: Path) -> None:
    """The v1.1 rows stay frozen: violations are listed, they never fail L1-L14."""

    data_dir, start, end = _copy_real_store(tmp_path)
    result = pv.verify(data_dir, start, end)

    assert result["legacy_v11_findings"], "the real journal carries frozen v1.1 findings"
    for check in result["legacy_v11_findings"]:
        assert result["checks"][check]["ok"] is True, check
