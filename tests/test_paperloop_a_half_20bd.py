"""t_264e0692: A_HALF's pre-registered 20-business-day gate (「20営業日連続」).

The prereg row (book_id="A_HALF", row_hash 5cd8e914…) froze the judgment rule
「design §4 12週 + 20営業日連続: 費用控除後 equity>=1.0 / 最大DD<=3.0% /
DD比<=0.6」. These tests pin the card's six measured cases (a)-(f) and the
A/C-block regression: the gate rides the weekly milestone collection as an
extra "20bd" slot on A_HALF ONLY, with the same record-only discipline
(no early judgment, one record ever, missing facts defer).

Synthetic journals are chain-appended through ``pl._append_row`` (the only
journal writer) and read back through the real judgment code path
``pl._collect_milestone_judgments`` / ``pl._a_half_20bd_judgment``. No real
journal row is touched; no order, config or cron path is exercised.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import yowayowa.paperloop as pl

START = date(2026, 10, 26)  # Monday — the window starts here either way
WINDOW = pl._a_half_20bd_business_days(START)
STAMP_BASE = datetime(2026, 10, 1, 1, 0, tzinfo=UTC)


def _stamp(index: int) -> str:
    return (STAMP_BASE + timedelta(days=index)).isoformat()


def _add_mtm(root: Path, book: str, day: date, equity: float, index: int) -> None:
    pl._append_row(
        root,
        {
            "event": "mtm",
            "book": book,
            "mtm_date": day.isoformat(),
            "equity": equity,
            "recorded_at": _stamp(index),
        },
    )


def _build_book(
    root: Path,
    a_by_day: dict[date, float],
    bench_by_day: dict[date, float],
    *,
    start: date = START,
) -> list[dict[str, object]]:
    """One non-backfill A_HALF decision row + the two mtm series."""

    pl._append_row(
        root,
        {
            "event": "decision",
            "book": pl.BOOK_A_HALF,
            "decision_date": start.isoformat(),
            "recorded_at": _stamp(0),
        },
    )
    index = 1
    for day in sorted(set(a_by_day) | set(bench_by_day)):
        if day in a_by_day:
            _add_mtm(root, pl.BOOK_A_HALF, day, a_by_day[day], index)
            index += 1
        if day in bench_by_day:
            _add_mtm(root, pl.BENCH_CRYPTO_BOOK, day, bench_by_day[day], index)
            index += 1
    return pl._load_journal(root)


def _book_path(peak: float, dip: float, dip_offset: int) -> dict[date, float]:
    """A book series that peaks at ``peak`` just before dipping to ``dip``.

    The dip depth is therefore ``dip / peak - 1``, measurable exactly, and
    later days rise again without changing the recorded window max DD.
    """

    values: dict[date, float] = {day: 1.0 for day in WINDOW}
    for offset in range(1, dip_offset):
        values[WINDOW[offset]] = round(1.0 + (peak - 1.0) * offset / (dip_offset - 1), 10)
    values[WINDOW[dip_offset - 1]] = round(peak, 10)
    values[WINDOW[dip_offset]] = round(dip, 10)
    floor = max(1.0, dip)
    for offset in range(dip_offset + 1, len(WINDOW)):
        values[WINDOW[offset]] = round(floor * 1.004 ** (offset - dip_offset), 10)
    return values


def _bench_path(depth: float, dip_offset: int = 10) -> dict[date, float]:
    """Benchmark series flat at 1.0 until its dip: the window DD == ``depth``."""

    values: dict[date, float] = {day: 1.0 for day in WINDOW}
    values[WINDOW[dip_offset]] = round(1.0 - depth, 10)
    for offset in range(dip_offset + 1, len(WINDOW)):
        values[WINDOW[offset]] = round((1.0 - depth) * 1.003 ** (offset - dip_offset), 10)
    return values


def test_case_a_pass(tmp_path: Path) -> None:
    """(a) 20 weekdays all equity>=1.0, max DD 2.0%, DD ratio 0.5 → PASS."""

    a_values = _book_path(1.03, 1.0094, 6)  # 1.0094/1.03 - 1 = -2.000%
    bench_values = _bench_path(0.04)
    _build_book(tmp_path, a_values, bench_values)
    judgment = pl._a_half_20bd_judgment(
        tmp_path, pl._load_journal(tmp_path), pl.BOOK_A_HALF, WINDOW[-1]
    )
    assert judgment is not None
    assert judgment["verdict"] == "PASS"
    assert judgment["failed_criteria"] == []
    equity = judgment["criteria"]["equity_net_min"]
    dd = judgment["criteria"]["max_drawdown_max"]
    ratio = judgment["criteria"]["dd_vs_benchmark"]
    assert equity["pass"] is True
    assert equity["failing_days"] == []
    assert abs(equity["value"] - min(a_values.values())) < 1e-12
    assert dd["pass"] is True
    assert abs(dd["value"] - (-0.02)) < 1e-9, dd["value"]
    assert ratio["pass"] is True
    assert abs(ratio["value"] - 0.5) < 1e-6, ratio["value"]
    assert judgment["business_days_required"] == 20
    assert judgment["business_days_start"] == "2026-10-26"
    assert judgment["business_days_end"] == "2026-11-20"


def test_case_b_single_dip_fails_and_names_the_day(tmp_path: Path) -> None:
    """(b) one weekday at equity 0.995 → FAIL; the failing day is recorded."""

    a_values = _book_path(1.02, 0.995, 8)  # window DD 2.45%: inside 3%
    bench_values = _bench_path(0.05)
    _build_book(tmp_path, a_values, bench_values)
    judgment = pl._a_half_20bd_judgment(
        tmp_path, pl._load_journal(tmp_path), pl.BOOK_A_HALF, WINDOW[-1]
    )
    assert judgment["verdict"] == "FAIL"
    assert judgment["failed_criteria"] == ["equity_net_min"]
    equity = judgment["criteria"]["equity_net_min"]
    assert equity["pass"] is False
    assert equity["failing_days"] == [WINDOW[8].isoformat()]
    assert judgment["criteria"]["max_drawdown_max"]["pass"] is True, "DD alone was inside 3%"
    assert judgment["criteria"]["dd_vs_benchmark"]["pass"] is True


def test_case_c_max_drawdown_3_5pct_fails(tmp_path: Path) -> None:
    """(c) window max DD 3.5% (principal still respected every day) → FAIL."""

    a_values = _book_path(1.05, 1.01325, 10)  # 1.01325/1.05 - 1 = -3.500%
    bench_values = _bench_path(0.07)
    _build_book(tmp_path, a_values, bench_values)
    judgment = pl._a_half_20bd_judgment(
        tmp_path, pl._load_journal(tmp_path), pl.BOOK_A_HALF, WINDOW[-1]
    )
    assert judgment["verdict"] == "FAIL"
    assert judgment["failed_criteria"] == ["max_drawdown_max"]
    dd = judgment["criteria"]["max_drawdown_max"]
    assert dd["pass"] is False
    assert abs(dd["value"] - (-0.035)) < 1e-9, dd["value"]
    equity = judgment["criteria"]["equity_net_min"]
    assert equity["pass"] is True, "every closing level stayed >= 1.0"
    assert equity["failing_days"] == []
    ratio = judgment["criteria"]["dd_vs_benchmark"]
    assert ratio["pass"] is True, "the DD ratio gate alone would have passed"
    assert abs(ratio["value"] - 0.5) < 1e-6


def test_case_d_dd_ratio_0_7_fails(tmp_path: Path) -> None:
    """(d) window DD ratio 0.70 → FAIL (the own max-DD gate passes)."""

    a_values = _book_path(1.03, 1.0094, 6)  # book window DD -2.000%
    bench_values = _bench_path(0.028571)  # bench window DD -2.857% → ratio ≈ 0.70
    _build_book(tmp_path, a_values, bench_values)
    judgment = pl._a_half_20bd_judgment(
        tmp_path, pl._load_journal(tmp_path), pl.BOOK_A_HALF, WINDOW[-1]
    )
    assert judgment["verdict"] == "FAIL"
    assert judgment["failed_criteria"] == ["dd_vs_benchmark"]
    ratio = judgment["criteria"]["dd_vs_benchmark"]
    assert ratio["pass"] is False
    assert abs(ratio["value"] - 0.70) < 0.005, ratio["value"]
    assert judgment["criteria"]["max_drawdown_max"]["pass"] is True
    assert judgment["criteria"]["equity_net_min"]["pass"] is True


def test_case_e_nineteen_days_defers(tmp_path: Path) -> None:
    """(e) 19 weekdays only → nothing judged (保留), never a PASS."""

    a_values = _book_path(1.03, 1.0094, 6)
    bench_values = _bench_path(0.04)
    del a_values[WINDOW[15]]
    del bench_values[WINDOW[15]]
    _build_book(tmp_path, a_values, bench_values)
    assert (
        pl._a_half_20bd_judgment(tmp_path, pl._load_journal(tmp_path), pl.BOOK_A_HALF, WINDOW[-1])
        is None
    )
    collected = pl._collect_milestone_judgments(tmp_path, WINDOW[-1])
    slot = collected.get(pl.BOOK_A_HALF)
    assert slot is None or "20bd" not in slot

    # early-judgment variant: complete data but week_end < window_end
    early_root = tmp_path / "early"
    early_root.mkdir()
    _build_book(early_root, _book_path(1.03, 1.0094, 6), _bench_path(0.04))
    assert (
        pl._a_half_20bd_judgment(
            early_root,
            pl._load_journal(early_root),
            pl.BOOK_A_HALF,
            WINDOW[-1] - timedelta(days=1),
        )
        is None
    )


def test_case_f_run_twice_records_once(tmp_path: Path) -> None:
    """(f) two runs over the same data → the record is written exactly once."""

    _build_book(tmp_path, _book_path(1.03, 1.0094, 6), _bench_path(0.04))
    first = pl._collect_milestone_judgments(tmp_path, WINDOW[-1])
    recorded = first[pl.BOOK_A_HALF]["20bd"]
    assert recorded["verdict"] == "PASS"

    summary_file = pl.summary_path(tmp_path)
    summary_file.parent.mkdir(parents=True, exist_ok=True)
    with summary_file.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "schema": "demo-loop-summary-v1.10",
                    "week_end": WINDOW[-1].isoformat(),
                    "milestone": {pl.BOOK_A_HALF: {"20bd": recorded}},
                }
            )
            + "\n"
        )
    second = pl._collect_milestone_judgments(tmp_path, WINDOW[-1])
    assert "20bd" not in second.get(pl.BOOK_A_HALF, {}), "written once, ever"
    summary_rows = [json.loads(line) for line in summary_file.read_text().splitlines()]
    carriers = [
        row
        for row in summary_rows
        if isinstance(row.get("milestone"), dict)
        and "20bd" in row["milestone"].get(pl.BOOK_A_HALF, {})
    ]
    assert len(carriers) == 1


def test_a_and_c_blocks_keep_their_shape(tmp_path: Path) -> None:
    """Regression: A and C never gain the A_HALF extra slot from this gate."""

    _build_book(tmp_path, _book_path(1.03, 1.0094, 6), _bench_path(0.04))
    for book in (pl.S01_BOOK, pl.BOOK_C_BOOK):
        pl._append_row(
            tmp_path,
            {
                "event": "decision",
                "book": book,
                "decision_date": START.isoformat(),
                "recorded_at": _stamp(0),
            },
        )
    collected = pl._collect_milestone_judgments(tmp_path, WINDOW[-1])
    for book in (pl.S01_BOOK, pl.BOOK_C_BOOK):
        if book in collected:
            assert set(collected[book]) == {"weeks"}, (book, collected[book].keys())


def test_gate_thresholds_match_prereg_text() -> None:
    """Frozen thresholds are 1:1 with the pre-registered wording."""

    assert pl.A_HALF_20BD_BUSINESS_DAYS == 20  # 20営業日連続
    assert pl.A_HALF_20BD_RULES == {
        "equity_net_min": 1.0,  # 費用控除後 equity >= 1.0
        "max_drawdown_max": 0.03,  # 最大DD <= 3.0%
        "dd_vs_benchmark": 0.6,  # DD比 <= 0.6
    }
    prereg = [
        row
        for row in pl._rows_from_jsonl(pl.prereg_path(pl.loop_root(Path("./data"))))
        if row.get("book_id") == pl.BOOK_A_HALF
    ]
    assert len(prereg) == 1
    rule = prereg[0]["judgment_rule"]
    assert "20営業日連続" in rule
    assert "equity>=1.0" in rule
    assert "最大DD<=3.0%" in rule
    assert "DD比<=0.6" in rule


def test_window_end_is_preregistered_2026_11_20() -> None:
    """The weekday count reproduces the pre-registered plan (11-20)."""

    days = pl._a_half_20bd_business_days(START)
    assert len(days) == 20
    assert all(day.weekday() < 5 for day in days)
    assert days[-1].isoformat() == "2026-11-20"
    assert date(2026, 10, 25) not in days  # the Sunday enabling day is not a 営業日
