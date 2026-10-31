"""Real-data probe for kanban task t_e2c98d2b (NOT a repo test).

Purpose: connect one open link of the P3 loop (recorded edge-lifetime verdict
-> next decision) and measure it on the operator's real demo-loop data, without
writing a single byte into the production data directory.

Method
------
1. copy the production demo-loop + ohlcv stores into a scratch work dir;
2. rewind the copied journal to the state just before the 2026-10-23 09:20 JST
   daily run (drop the rows written that day; the remaining prefix still has a
   valid seq/hash chain because only the tail is removed);
3. run the real `run_daily_decide` on that copy twice:
     A) with the production monitor log (2026-W42 verdict: continue),
     B) with one synthetic PAUSE row appended to the copy's monitor log;
4. compare A against the decisions production actually wrote for 2026-10-23,
   and A against B.

Env: P3_REPO (repo root), P3_OUT (output dir). Nothing is written outside P3_OUT.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from yowayowa import paperloop as pl

REPO = Path(os.environ["P3_REPO"])
OUT = Path(os.environ["P3_OUT"])
REWIND_BEFORE = "2026-10-23"  # keep rows strictly before this date
DECISION_NOW = datetime(2026, 10, 23, 0, 20, tzinfo=UTC)  # 09:20 JST daily run


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _build_base(dest: Path) -> None:
    """Copy only the stores the demo loop reads, then rewind the journal."""

    data = dest / "data"
    for name in ("demo-loop", "crypto-ohlcv", "stock-ohlcv"):
        shutil.copytree(REPO / "data" / name, data / name)
    root = pl.loop_root(data)
    rows = _jsonl(pl.journal_path(root))
    kept = [row for row in rows if str(row["recorded_at"])[:10] < REWIND_BEFORE]
    assert kept, "rewind kept no rows"
    with pl.journal_path(root).open("w", encoding="utf-8") as handle:
        for row in kept:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    # anchors point at rows that were just dropped; the probe does not verify
    (root / "anchors.jsonl").unlink()
    # a same-day weekly summary row for 2026-10-23 must not pre-exist in the copy
    assert pl._load_journal(root)[-1]["seq"] == kept[-1]["seq"]


def _append_synthetic_pause(root: Path) -> dict:
    """Append a PAUSE verdict chained exactly like cusum_watch.run_weekly."""

    path = root / pl.CUSUM_LOG_NAME
    prior = _jsonl(path) if path.exists() else []
    row = {
        "schema": "cusum-watch-v1",
        "book_id": "A",
        "computed_at": "2026-10-19T00:50:53.558848+00:00",
        "prereg_row_hash": prior[-1]["prereg_row_hash"] if prior else "0" * 64,
        "baseline_ref_sha256": "1" * 64,
        "baseline_sha_match": True,
        "constants": {
            "kappa0": 0.5,
            "k": 0.5,
            "mean": 0.004933,
            "sigma_hat": 0.048186,
            "h_star": 4.8,
        },
        "week": "2026-W43",
        "weeks_observed": 3,
        "z": -1.0,
        "S": 5.13,
        "alarm": True,
        "action": "PAUSE",
        "note": "SYNTHETIC probe row (t_e2c98d2b) — not a monitor computation",
    }
    row["prev_hash"] = str(prior[-1]["row_hash"]) if prior else ""
    row["row_hash"] = pl._sha256(json.dumps(row, sort_keys=True, default=str))
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    return row


def _run(data: Path) -> dict:
    root = pl.loop_root(data)
    pl._forming_candle_open = lambda symbol: None  # no network in the probe
    pl._fetch_crypto_1h = lambda symbol, limit=48: []
    written = pl.run_daily_decide(root, DECISION_NOW, run_id="probe-t_e2c98d2b", trigger="manual")
    day = DECISION_NOW.date().isoformat()
    decisions = [
        {
            key: row.get(key)
            for key in (
                "book",
                "asset",
                "decision_id",
                "decision_date",
                "regime_state",
                "halt_flag",
                "halt_reason",
                "target_weight",
                "side",
                "delta_weight",
                "fill_status",
                "input_sha256",
                "close",
                "sma200",
                "regime_tag",
            )
        }
        for row in pl._load_journal(root)
        if row.get("event") == "decision" and row.get("decision_date") == day
    ]
    checks = [
        {
            key: row.get(key)
            for key in (
                "book",
                "check_date",
                "action",
                "halted_before",
                "halted_after",
                "drawdown",
                "edge_lifetime",
                "margin_min",
            )
        }
        | {"tripped": [e["name"] for e in row.get("triggers", []) if e.get("tripped")]}
        for row in pl._load_journal(root)
        if row.get("event") == "halt_check" and row.get("check_date") == day
    ]
    return {
        "rows_written": written,
        "journal_rows": len(pl._load_journal(root)),
        "journal_sha256": _sha(pl.journal_path(root)),
        "decisions": decisions,
        "halt_checks": checks,
    }


def _production_decisions() -> list[dict]:
    root = pl.loop_root(REPO / "data")
    day = DECISION_NOW.date().isoformat()
    return [
        row
        for row in pl._load_journal(root)
        if row.get("event") == "decision" and row.get("decision_date") == day
    ]


def test_p3_edge_lifetime_gate_on_real_data() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    prod_journal = REPO / "data/demo-loop/journal.jsonl"
    prod_cusum = REPO / "data/demo-loop/cusum_A.jsonl"
    before = {name: _sha(path) for name, path in
              (("journal", prod_journal), ("cusum", prod_cusum))}

    base = OUT / "base"
    if base.exists():
        shutil.rmtree(base)
    _build_base(base)
    prefix_rows = len(_jsonl(pl.journal_path(pl.loop_root(base / "data"))))
    run_a_dir, run_b_dir = OUT / "run_a", OUT / "run_b"
    for path in (run_a_dir, run_b_dir):
        if path.exists():
            shutil.rmtree(path)
        shutil.copytree(base, path)

    verdict_a = pl.cusum_edge_verdict(pl.loop_root(run_a_dir / "data"))
    synthetic = _append_synthetic_pause(pl.loop_root(run_b_dir / "data"))
    verdict_b = pl.cusum_edge_verdict(pl.loop_root(run_b_dir / "data"))

    a = _run(run_a_dir / "data")
    b = _run(run_b_dir / "data")

    # --- A must reproduce what production actually wrote for 2026-10-23 ------
    compared_keys = (
        "book",
        "asset",
        "regime_state",
        "halt_flag",
        "halt_reason",
        "target_weight",
        "side",
        "delta_weight",
        "fill_status",
        "input_sha256",
        "close",
        "sma200",
        "regime_tag",
    )
    prod = {
        row["decision_id"]: row for row in _production_decisions()
    }  # 4 decisions (A + CRYPTO_1N; equity books are Monday-gated)
    mine = {row["decision_id"]: row for row in a["decisions"]}
    prod_mismatch = [
        (decision_id, key, prod[decision_id].get(key), mine.get(decision_id, {}).get(key))
        for decision_id in sorted(set(prod) | set(mine))
        for key in compared_keys
        if decision_id in prod and decision_id in mine
        and prod[decision_id].get(key) != mine[decision_id].get(key)
    ]

    # --- A vs B: the only difference must be the monitored book --------------
    a_by_id = {row["decision_id"]: row for row in a["decisions"]}
    b_by_id = {row["decision_id"]: row for row in b["decisions"]}
    diff = [
        (decision_id, key, a_by_id[decision_id].get(key), b_by_id[decision_id].get(key))
        for decision_id in sorted(set(a_by_id) & set(b_by_id))
        for key in compared_keys
        if a_by_id[decision_id].get(key) != b_by_id[decision_id].get(key)
    ]
    changed_books = sorted({row[0].split(":")[0] for row in diff})

    summary = {
        "card": "t_e2c98d2b",
        "generated_at": datetime.now(UTC).isoformat(),
        "code_sha256": {
            "src/yowayowa/paperloop.py": _sha(REPO / "src/yowayowa/paperloop.py"),
            "tests/test_paperloop.py": _sha(REPO / "tests/test_paperloop.py"),
            "probe": _sha(Path(__file__)),
        },
        "inputs": {
            "production_journal_sha256_before": before["journal"],
            "production_cusum_sha256_before": before["cusum"],
            "production_journal_rows": len(_jsonl(prod_journal)),
            "rewound_prefix_rows": prefix_rows,
            "rewind_before": REWIND_BEFORE,
            "decision_now": DECISION_NOW.isoformat(),
            "production_2026_10_23_decisions": len(prod),
        },
        "verdict_read": {"run_a": verdict_a, "run_b": verdict_b, "synthetic_row": synthetic},
        "run_a_gate_open": a,
        "run_b_gate_closed": b,
        "reproduction": {"compared": len(mine), "mismatches": prod_mismatch},
        "a_vs_b": {"changed_decision_fields": diff, "changed_books": changed_books},
        "production_untouched": {
            "journal_sha256_after": _sha(prod_journal),
            "cusum_sha256_after": _sha(prod_cusum),
        },
    }
    (OUT / "p3_edge_gate_real_data.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # --- assertions that make the run a proof, not a log ---------------------
    assert verdict_a["status"] == "continue", verdict_a
    assert verdict_a["week"] == "2026-W42", verdict_a
    assert verdict_b["status"] == "pause", verdict_b
    assert len(mine) == len(prod) == 4, (mine.keys(), prod.keys())
    assert not prod_mismatch, prod_mismatch
    assert a["rows_written"] == 4 and b["rows_written"] == 4
    assert changed_books == ["A"], changed_books
    # every changed row of book A flipped to the halted/skipped shape
    assert all(key in ("regime_state", "halt_flag", "halt_reason", "target_weight",
                       "side", "delta_weight", "fill_status") for _d, key, _x, _y in diff)
    paused_a = [row for row in b["decisions"] if row["book"] == "A"]
    assert paused_a and all(row["halt_flag"] and row["fill_status"] == "skipped"
                            for row in paused_a)
    assert all("cusum_pause" in row["halt_reason"] for row in paused_a)
    untouched = [row for row in b["decisions"] if row["book"] == pl.BENCH_CRYPTO_BOOK]
    assert untouched and all(not row["halt_flag"] for row in untouched)
    check_a = [row for row in a["halt_checks"] if row["book"] == "A"][0]
    check_b = [row for row in b["halt_checks"] if row["book"] == "A"][0]
    assert check_a["action"] == "none" and check_b["action"] == "halt"
    assert check_a["edge_lifetime"]["S"] == pytest.approx(0.1307875935134487)
    assert check_b["edge_lifetime"]["S"] == pytest.approx(5.13)
    assert check_b["tripped"] == ["cusum_pause"]
    # the production data dir was never written to
    assert summary["production_untouched"]["journal_sha256_after"] == before["journal"]
    assert summary["production_untouched"]["cusum_sha256_after"] == before["cusum"]
