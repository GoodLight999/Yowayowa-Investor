"""C-01 regression (t_cad2ff49): the 1/N benchmark books never self-liquidate.

`docs/PAPERLOOP_MONEY_REVIEW_20261023.md` C-01: after the first buy-in the
benchmark's target_map is empty ("hold only"), but only CRYPTO_1N was exempted
from the resulting ``target 0% - held weight`` delta. EQUITY_EW therefore wrote
four ``sell`` decisions on the second Monday and ``resolve_fills`` liquidated the
whole 1/N position, freezing the benchmark in cash and making every later
``vs_benchmark`` comparison meaningless.

This test runs the equity benchmark over three Mondays on a synthetic journal and
asserts the position survives: no sells, cash stays invested, and the mtm legs
keep marking the position (instead of collapsing to a flat cash equity).
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
import test_paperloop as tp

from yowayowa import paperloop as pl


def _append_flat_bars(data_dir: Path, start: date, days: int) -> None:
    """Daily bars covering the weeks after the synthetic set (flat, +0.5%)."""

    series = [((start + timedelta(days=i)).isoformat(), 100.0, 100.5) for i in range(days)]
    for symbol in pl.CRYPTO_UNIVERSE:
        tp._write_crypto_days(data_dir, symbol, series)
    for symbol in pl.EQUITY_UNIVERSE:
        tp._write_stock_days(data_dir, symbol, series)


def _session_close(day: datetime) -> datetime:
    """23:00 UTC of the decision day: after the 13:30 UTC equity session open."""

    return datetime.combine(day.date(), datetime.min.time(), tzinfo=UTC) + timedelta(hours=23)


def _equity_ew_rows(root: Path, event: str, decision_date: date | None = None) -> list[dict]:
    rows = [
        row
        for row in pl._load_journal(root)
        if row.get("event") == event and row.get("book") == pl.BENCH_EQUITY_BOOK
    ]
    if decision_date is not None:
        rows = [
            row
            for row in rows
            if str(row.get("decision_id", "")).endswith(f":{decision_date.isoformat()}")
        ]
    return rows


def test_equity_ew_benchmark_holds_its_position_after_the_first_buy_in(
    tmp_path: Path, monkeypatch
) -> None:
    data_dir, root, first_monday = tp._prepare(tmp_path, monkeypatch)
    assert first_monday.weekday() == 0  # the equity books decide on Mondays
    _append_flat_bars(data_dir, first_monday.date(), 21)  # three weeks of bars
    second_monday = first_monday + timedelta(days=7)
    third_monday = first_monday + timedelta(days=14)

    # --- first Monday: 1/N buy-in, then the fills land ---------------------
    pl.run_daily_decide(root, first_monday)
    first_decisions = _equity_ew_rows(root, "decision", first_monday.date())
    assert len(first_decisions) == len(pl.EQUITY_UNIVERSE)
    assert {row["side"] for row in first_decisions} == {"buy"}
    assert all(abs(float(row["target_weight"]) - 0.25) < 1e-9 for row in first_decisions)
    assert pl.resolve_fills(root, _session_close(first_monday)) > 0
    buy_fills = _equity_ew_rows(root, "fill", first_monday.date())
    assert len(buy_fills) == len(pl.EQUITY_UNIVERSE)
    assert {row["side"] for row in buy_fills} == {"buy"}
    state_after_buy_in = pl.rebuild_state(root, pl.BENCH_EQUITY_BOOK)
    held_units = {
        symbol: float(units)
        for symbol, units in state_after_buy_in["units"].items()
        if float(units) > 0
    }
    assert held_units, "the first Monday must leave the benchmark invested"
    assert abs(float(state_after_buy_in["cash"])) < 1e-9

    # --- second Monday: the broken code sold everything here --------------
    pl.run_daily_decide(root, second_monday)
    pl.resolve_fills(root, _session_close(second_monday))
    second_decisions = _equity_ew_rows(root, "decision", second_monday.date())
    assert len(second_decisions) == len(pl.EQUITY_UNIVERSE)
    assert [row["side"] for row in second_decisions] == ["hold"] * len(pl.EQUITY_UNIVERSE)
    assert all(abs(float(row["delta_weight"])) < 1e-9 for row in second_decisions)
    assert all(row["fill_status"] == "skipped" for row in second_decisions)
    assert _equity_ew_rows(root, "fill", second_monday.date()), "hold rows still fill as hold"
    assert not [row for row in _equity_ew_rows(root, "fill") if row["side"] == "sell"], (
        "EQUITY_EW must never sell after buy-in (D7)"
    )

    # --- third Monday: still invested, still marked ----------------------
    pl.run_daily_decide(root, third_monday)
    pl.resolve_fills(root, _session_close(third_monday))
    state_third = pl.rebuild_state(root, pl.BENCH_EQUITY_BOOK)
    assert {
        symbol: float(units) for symbol, units in state_third["units"].items() if float(units) > 0
    } == pytest.approx(held_units)
    assert abs(float(state_third["cash"])) < 1e-9, "the benchmark must stay fully invested"

    mtm = [
        (row["mtm_date"], float(row["equity"]), float(row["cash"]))
        for row in pl._load_journal(root)
        if row.get("event") == "mtm" and row.get("book") == pl.BENCH_EQUITY_BOOK
    ]
    assert [day for day, _equity, _cash in mtm] == [
        first_monday.date().isoformat(),
        second_monday.date().isoformat(),
        third_monday.date().isoformat(),
    ]
    # the first leg is the pre-fill inception mark (decide marks before fills);
    # the later legs must mark an INVESTED book, not a liquidated one
    assert mtm[0][2] == pytest.approx(1.0)
    assert all(cash == 0.0 for _day, _equity, cash in mtm[1:])
    assert mtm[-1][1] == pytest.approx(mtm[-2][1]), "cash frozen would hide the flat week"

    # the crypto benchmark is untouched by this fix: same hold-only behaviour
    crypto_sells = [
        row
        for row in pl._load_journal(root)
        if row.get("event") == "fill"
        and row.get("book") == pl.BENCH_CRYPTO_BOOK
        and row["side"] == "sell"
    ]
    assert not crypto_sells


def test_equity_ew_benchmark_decision_rows_stay_in_the_journal(tmp_path: Path, monkeypatch) -> None:
    """The fix must not silence the benchmark: decisions are still recorded."""

    data_dir, root, monday = tp._prepare(tmp_path, monkeypatch)
    _append_flat_bars(data_dir, monday.date(), 7)
    pl.run_daily_decide(root, monday)
    pl.resolve_fills(root, _session_close(monday))
    next_monday = monday + timedelta(days=7)
    pl.run_daily_decide(root, next_monday)
    rows = _equity_ew_rows(root, "decision", next_monday.date())
    assert len(rows) == len(pl.EQUITY_UNIVERSE)
    # the journal is append-only: sequence numbers stay gapless and increasing
    journal = pl._load_journal(root)
    seqs = [int(row["seq"]) for row in journal if "seq" in row]
    assert seqs == list(range(1, len(seqs) + 1))
    with pl.journal_path(root).open(encoding="utf-8") as handle:
        assert all(json.loads(line)["event"] for line in handle if line.strip())
