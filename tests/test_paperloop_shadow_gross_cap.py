"""Unit tests for the v1.10 shadow book A_HALF (gross cap 50%, t_52c4aa20).

The card asks for three things to be proven, not assumed:
  1. the shadow book's target weight is ``0.5 / len(active)`` (0.25/0.25 when
     both crypto assets pass the SMA200 gate),
  2. book A is UNCHANGED — same spec_hash, same 0.5/0.5 targets,
  3. recompute stays consistent for both books (``mark.recompute_match`` and
     the verifier's R2 / L12 on the shadow book).

No network, no orders: the loop surface is exercised on a tmp data dir.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from tests.test_paperloop import _inject_bar, _prepare
from yowayowa import paperloop as pl
from yowayowa import paperloop_verify as pv

REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_LOOP = REPO_ROOT / "data" / "demo-loop"
_real_loop = pytest.mark.skipif(
    not (REAL_LOOP / "journal.jsonl").exists(),
    reason="data/demo-loop is not present (data/ is git-ignored)",
)

# Book A's frozen registration (data/demo-loop/prereg.jsonl:1). If this ever
# changes, the shadow comparison is no longer against the same book.
BOOK_A_SPEC_HASH = "b71b70fc2e9df13c68d934fdb5cf66020c7b51c52b31ab3bc99cd865b9eadf17"


def _rows(prices: dict[str, list[float]], last_day: date = date(2026, 1, 1)) -> dict[str, Any]:
    """``symbol -> {date: {"close": float}}`` ending at ``last_day`` (inclusive)."""
    out: dict[str, Any] = {}
    for symbol, series in prices.items():
        start = last_day - timedelta(days=len(series) - 1)
        out[symbol] = {
            start + timedelta(days=index): {"close": value} for index, value in enumerate(series)
        }
    return out


def _rising(n: int = 201, start: float = 100.0, step: float = 1.0) -> list[float]:
    return [start + step * index for index in range(n)]


def _decision_day(rows: dict[str, Any]) -> date:
    return max(next(iter(rows.values())))


# --- 1. the shadow weight is 0.5/len(active) ---------------------------------


def test_A_half_targets_are_half_of_A_when_both_assets_are_active() -> None:
    rows = _rows({"BTC": _rising(), "ETH": _rising(start=50.0)})
    day = _decision_day(rows)

    target_a = pl._engine_target(pl.S01_BOOK, rows, day)
    target_half = pl._engine_target(pl.BOOK_A_HALF, rows, day)

    assert target_a == {"BTC": 0.5, "ETH": 0.5}
    assert target_half == {"BTC": 0.25, "ETH": 0.25}
    # half the exposure, half the book left in cash
    assert sum(target_a.values()) == pytest.approx(1.0)
    assert sum(target_half.values()) == pytest.approx(pl.SHADOW_GROSS_CAP)


def test_A_half_is_0_5_over_len_active_with_one_asset_active() -> None:
    # ETH below its 200d mean -> its gate fails; only BTC is active.
    rows = _rows({"BTC": _rising(), "ETH": list(reversed(_rising(start=50.0)))})
    day = _decision_day(rows)

    target_a = pl._engine_target(pl.S01_BOOK, rows, day)
    target_half = pl._engine_target(pl.BOOK_A_HALF, rows, day)

    assert target_a == {"BTC": 1.0, "ETH": 0.0}
    assert target_half == {"BTC": 0.5, "ETH": 0.0}


def test_A_half_liquidates_with_A_when_every_gate_fails() -> None:
    rows = _rows(
        {
            "BTC": list(reversed(_rising())),
            "ETH": list(reversed(_rising(start=50.0))),
        }
    )
    day = _decision_day(rows)

    assert pl._engine_target(pl.S01_BOOK, rows, day) == {"BTC": 0.0, "ETH": 0.0}
    assert pl._engine_target(pl.BOOK_A_HALF, rows, day) == {"BTC": 0.0, "ETH": 0.0}


# --- 2. book A is untouched ---------------------------------------------------


def test_book_A_spec_and_registration_are_unchanged() -> None:
    assert pl.S01_BOOK == "A"
    assert pl.BOOK_SPEC_HASHES[pl.S01_BOOK] == (
        "primary:s01:crypto_trend_dual_ma:BTCETH:200:daily:0.0015"
    )
    assert pl._sha256(pl.BOOK_SPEC_HASHES[pl.S01_BOOK]) == BOOK_A_SPEC_HASH
    # the shadow book can never collide with A in prereg / L10
    assert pl.BOOK_SPEC_HASHES[pl.BOOK_A_HALF] != pl.BOOK_SPEC_HASHES[pl.S01_BOOK]
    assert pl._sha256(pl.BOOK_SPEC_HASHES[pl.BOOK_A_HALF]) != BOOK_A_SPEC_HASH
    # enabled, but only as an ADDITION (A stays first, nothing was removed)
    assert pl.BOOKS_ENABLED[:4] == (
        pl.S01_BOOK,
        pl.BENCH_CRYPTO_BOOK,
        pl.BOOK_C_BOOK,
        pl.BENCH_EQUITY_BOOK,
    )
    assert pl.BOOK_A_HALF in pl.BOOKS_ENABLED
    assert pl.BOOK_A_HALF in pl.STRATEGY_BOOKS
    assert pl.BOOK_A_HALF in pl.CRYPTO_BOOKS  # crypto universe/calendar, not equity
    assert pl.BOOK_BENCHMARK[pl.BOOK_A_HALF] == pl.BENCH_CRYPTO_BOOK


# --- 3. end-to-end: half-size fills, recompute, verifier ----------------------


def _prepare_running_loop(tmp_path: Path, monkeypatch):
    data_dir, root, now = _prepare(tmp_path, monkeypatch)
    pl.run_daily_decide(root, now)
    _inject_bar(root, "BTC", now + timedelta(hours=1), 101.0)
    _inject_bar(root, "ETH", now + timedelta(hours=1), 60.0)
    pl.resolve_fills(root, now + timedelta(hours=2))
    # the execution day's daily bar lands (mark_up needs it)
    for symbol, open_price in (("BTC", 100.5), ("ETH", 59.5)):
        path = data_dir / "crypto-ohlcv" / symbol / "ohlcv.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "symbol": symbol,
                        "interval": "1d",
                        "currency": "USDT",
                        "provider": "binance",
                        "source_url": "u",
                        "license_class": "personal_only",
                        "retrieved_at": "2026-01-01T00:00:00+00:00",
                        "as_of": now.date().isoformat() + "T00:00:00Z",
                        "open": open_price,
                        "close": open_price * 1.01,
                    }
                )
                + "\n"
            )
    pl.mark_up(root, now + timedelta(hours=25))
    return data_dir, root, now


def _rows_of(root: Path, event: str, book: str) -> list[dict[str, Any]]:
    return [
        row
        for row in pl._load_journal(root)
        if row.get("event") == event and row.get("book") == book
    ]


def test_A_half_trades_half_of_A_in_the_same_run(tmp_path: Path, monkeypatch) -> None:
    _data_dir, root, now = _prepare_running_loop(tmp_path, monkeypatch)

    decisions_a = _rows_of(root, "decision", pl.S01_BOOK)
    decisions_half = _rows_of(root, "decision", pl.BOOK_A_HALF)
    assert len(decisions_a) == len(decisions_half) == 2
    assert {row["decision_date"] for row in decisions_half} == {now.date().isoformat()}
    assert [row["target_weight"] for row in decisions_a] == [0.5, 0.5]
    assert [row["target_weight"] for row in decisions_half] == [0.25, 0.25]
    # the counterfactual column is capped the same way (no HALT/guard involved)
    assert [row["raw_target_weight"] for row in decisions_half] == [0.25, 0.25]
    # the guard is applied to strategy books — the shadow book is one
    assert all(row["guard_applied"] is True for row in decisions_half)

    fills_a = _rows_of(root, "fill", pl.S01_BOOK)
    fills_half = _rows_of(root, "fill", pl.BOOK_A_HALF)
    assert len(fills_a) == len(fills_half) == 2
    notional_a = sum(float(row["traded_notional"]) for row in fills_a)
    notional_half = sum(float(row["traded_notional"]) for row in fills_half)
    assert notional_half == pytest.approx(notional_a * pl.SHADOW_GROSS_CAP, rel=1e-9)
    assert [row["weight_after"] for row in fills_half] == pytest.approx([0.25, 0.25])
    # the other half of the book is still cash
    cash = pl.rebuild_state(root, pl.BOOK_A_HALF)["cash"]
    assert cash == pytest.approx(1.0 - sum(row["traded_notional"] for row in fills_half), abs=1e-12)


def test_A_half_marks_recompute_and_verify_pass(tmp_path: Path, monkeypatch) -> None:
    data_dir, root, now = _prepare_running_loop(tmp_path, monkeypatch)

    marks_half = _rows_of(root, "mark", pl.BOOK_A_HALF)
    assert marks_half, "the shadow book must be marked like every other book"
    assert all(row.get("recompute_match") is True for row in marks_half), marks_half

    # exactly one mtm per (book, day), shadow book included
    mtms_half = _rows_of(root, "mtm", pl.BOOK_A_HALF)
    assert len(mtms_half) == 1
    assert len(_rows_of(root, "mtm", pl.S01_BOOK)) == 1

    start = now.date() - timedelta(days=1)
    result = pv.verify(data_dir, start, now.date())
    assert result["exit_code"] == 0, json.dumps(result.get("failed_checks"))
    for check in pv.CHECKS:
        assert result["checks"][check]["ok"] is True, (
            check,
            result["checks"][check]["failures"],
        )
    # R2 (engine reconciliation) actually covered the shadow book's decisions
    assert result.get("R2_total", 0) >= 2
    assert result.get("R2_match") == result.get("R2_total")
    assert result.get("R1_match") == result.get("R1_total")


def test_A_half_is_preregistered_once_behind_A(tmp_path: Path, monkeypatch) -> None:
    _data_dir, root, _now = _prepare_running_loop(tmp_path, monkeypatch)

    rows = pl._rows_from_jsonl(pl.prereg_path(root), strict=True)
    by_book = {str(row["book_id"]): row for row in rows}
    assert set(by_book) == {
        pl.S01_BOOK,
        pl.BENCH_CRYPTO_BOOK,
        pl.BOOK_C_BOOK,
        pl.BENCH_EQUITY_BOOK,
        pl.BOOK_A_HALF,
        "__regime_spec__",
    }
    assert by_book[pl.S01_BOOK]["spec_hash"] == BOOK_A_SPEC_HASH  # A untouched
    half = by_book[pl.BOOK_A_HALF]
    assert half["spec_hash"] == pl._sha256(pl.BOOK_SPEC_HASHES[pl.BOOK_A_HALF])
    assert "50% gross cap" in str(half["hypothesis"])
    assert "20営業日連続" in str(half["judgment_rule"])
    # hash chain still links (prev_hash -> row_hash of the previous row)
    previous = ""
    for row in rows:
        assert row["prev_hash"] == previous
        assert row["row_hash"] == pl._sha256(
            json.dumps(
                {k: v for k, v in row.items() if k != "row_hash"}, sort_keys=True, default=str
            )
        )
        previous = str(row["row_hash"])
    # idempotent: a second decide does not register it twice
    pl.run_daily_decide(
        root, datetime.combine(_now.date(), datetime.min.time(), tzinfo=UTC) + timedelta(days=1)
    )
    again = [
        row
        for row in pl._rows_from_jsonl(pl.prereg_path(root), strict=True)
        if str(row["book_id"]) == pl.BOOK_A_HALF
    ]
    assert len(again) == 1


# --- 4. the REAL journal, copied (never written in place) ---------------------


@_real_loop
def test_real_journal_copy_next_decide_sizes_the_shadow_book_half(
    tmp_path: Path, monkeypatch
) -> None:
    """The next calendar day's decide on a COPY of the live loop: A and A_HALF.

    data/demo-loop is git-ignored live evidence, so it is copied, never touched:
    the source journal's sha256 is asserted identical afterwards. The market
    stores are symlinked read-only (they are only read by the loop).

    t_a9b4d5f4 / t_3059fb88: the day is ``last journal day + 1``, NOT the next
    Monday — a Monday far ahead of the stores trips the crypto freshness bound
    (HALT_FRESHNESS_CRYPTO, paperloop.py) and the run is then decided by data
    staleness instead of the journal.
    """

    source = REAL_LOOP
    journal_sha_before = hashlib.sha256((source / "journal.jsonl").read_bytes()).hexdigest()

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    for store in ("crypto-ohlcv", "stock-ohlcv"):
        target = REPO_ROOT / "data" / store
        if target.exists():
            (data_dir / store).symlink_to(target)
    root = pl.loop_root(data_dir)
    root.mkdir(parents=True)
    for name in ("journal.jsonl", "prereg.jsonl", "anchors.jsonl"):
        shutil.copy2(source / name, root / name)
    shutil.copytree(source / "inputs", root / "inputs")
    if (source / "tape").exists():
        shutil.copytree(source / "tape", root / "tape")

    monkeypatch.setattr(pl, "_forming_candle_open", lambda symbol: None)
    monkeypatch.setattr(pl, "_fetch_crypto_1h", lambda symbol, limit=48: [])

    decisions = [row for row in pl._load_journal(root) if row.get("event") == "decision"]
    last_day = max(date.fromisoformat(str(row["decision_date"])) for row in decisions)
    next_day = last_day + timedelta(days=1)
    now = datetime.combine(next_day, datetime.min.time(), tzinfo=UTC) + timedelta(minutes=20)

    pl.run_daily_decide(root, now)

    new_decisions = [
        row
        for row in pl._load_journal(root)
        if row.get("event") == "decision" and row["decision_date"] == next_day.isoformat()
    ]
    by_book: dict[str, dict[str, dict[str, Any]]] = {}
    for row in new_decisions:
        by_book.setdefault(str(row["book"]), {})[str(row["asset"])] = row

    # t_a9b4d5f4: if the live store is stale against the journal the loop HALTs
    # (target 0 for A and A_HALF alike, so the half-size check below would pass
    # vacuously as 0 == 0.5 * 0). Say so explicitly instead of passing empty.
    halted_new = [
        row
        for row in new_decisions
        if str(row.get("book")) in (pl.S01_BOOK, pl.BOOK_A_HALF) and row.get("halt_flag")
    ]
    if halted_new:
        pytest.skip("live store stale vs journal; t_a9b4d5f4")

    # D7 (paperloop.py:25-26): the benchmark buys 1/N on its first decision and
    # never trades again — L14 counts TRADES, not hold rows
    # (paperloop_verify.py:499-500, t_08eb947a). Same filter as L14 itself: a
    # hold fill row is a no-trade. The copied journal already carries the
    # buy-in, so this run must add no bench trade and every new benchmark
    # decision row is a hold.
    bench_fills = [
        row
        for row in pl._load_journal(root)
        if row.get("event") == "fill"
        and str(row.get("book")) == pl.BENCH_CRYPTO_BOOK
        and str(row.get("side")) != "hold"
    ]
    assert len(bench_fills) == len(pl.CRYPTO_UNIVERSE)
    new_bench_decisions = [
        row for row in new_decisions if str(row.get("book")) == pl.BENCH_CRYPTO_BOOK
    ]
    assert new_bench_decisions
    assert all(str(row.get("side")) == "hold" for row in new_bench_decisions)

    assert set(by_book[pl.BOOK_A_HALF]) == set(by_book[pl.S01_BOOK]) == set(pl.CRYPTO_UNIVERSE)
    for asset in pl.CRYPTO_UNIVERSE:
        a_target = float(by_book[pl.S01_BOOK][asset]["target_weight"])
        half_target = float(by_book[pl.BOOK_A_HALF][asset]["target_weight"])
        # the ONLY difference: half the exposure (0.0 stays 0.0)
        assert half_target == pytest.approx(a_target * pl.SHADOW_GROSS_CAP, abs=1e-12)
        # book A's decision logic is untouched: 1/len(active) or 0
        assert a_target in (0.0, 0.5, 1.0)
        assert (
            by_book[pl.BOOK_A_HALF][asset]["spec_hash"] != by_book[pl.S01_BOOK][asset]["spec_hash"]
        )

    # the shadow book is registered by the SAME run, once, behind A's row
    prereg_rows = pl._rows_from_jsonl(pl.prereg_path(root), strict=True)
    half_rows = [row for row in prereg_rows if str(row["book_id"]) == pl.BOOK_A_HALF]
    assert len(half_rows) == 1
    assert half_rows[0]["prev_hash"] == prereg_rows[-2]["row_hash"]
    assert [str(row["book_id"]) for row in prereg_rows].count(pl.S01_BOOK) == 1
    assert (
        next(row for row in prereg_rows if str(row["book_id"]) == pl.S01_BOOK)["spec_hash"]
        == BOOK_A_SPEC_HASH
    )

    # recompute agreement + the verifier on the copied journal (R1/R2/L12 included)
    assert pl.mark_up(root, now + timedelta(hours=30)) >= 0
    result = pv.verify(data_dir, last_day, next_day)
    assert result["exit_code"] == 0, json.dumps(result.get("failed_checks"))
    # L14 (paperloop_verify.py:497-521): the benchmark trades exactly once
    # (1/N on first decision) and never again — verify-level assertion mirroring
    # the journal-level invariant above. Does not depend on the number of
    # decision days (audit t_a9b4d5f4 recommendation #3).
    assert result["bench_fill_count"] == len(pl.CRYPTO_UNIVERSE), result["bench_fill_count"]
    assert result.get("R2_match") == result.get("R2_total")
    for check in pv.CHECKS:
        assert result["checks"][check]["ok"] is True, (
            check,
            result["checks"][check]["failures"],
        )

    # the live evidence was read-only throughout
    assert hashlib.sha256((source / "journal.jsonl").read_bytes()).hexdigest() == journal_sha_before
