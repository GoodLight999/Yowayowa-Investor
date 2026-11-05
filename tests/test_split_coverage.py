"""Unit tests for the split-calendar coverage monitor (scripts/check_split_coverage.py).

The monitor is the operational guard for the price-adjustment policy: prices are
stored raw and the backtest engine is the only place splits are applied, so a
calendar hole or an already-adjusted series must fail loudly here instead of
silently producing a fake -90% return or a double correction.
"""

from __future__ import annotations

import json
from pathlib import Path

from scripts.check_split_coverage import build_report, load_calendar, render

CALENDAR = {"AAA": [{"effective_date": "2025-01-13", "ratio": 2.0}]}


def _write_store(root: Path, symbol: str, rows: list[tuple[str, float, float]]) -> None:
    directory = root / "stock-ohlcv" / symbol
    directory.mkdir(parents=True, exist_ok=True)
    lines = []
    for as_of, opened, close in rows:
        lines.append(
            json.dumps(
                {
                    "symbol": symbol,
                    "interval": "1d",
                    "provider": "alpaca",
                    "as_of": f"{as_of}T05:00:00Z",
                    "open": opened,
                    "close": close,
                }
            )
        )
    (directory / "ohlcv.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_calendar(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _flat(days: int, price: float = 100.0, start_day: int = 1) -> list[tuple[str, float, float]]:
    return [(f"2025-01-{start_day + i:02d}", price, price) for i in range(days)]


def test_clean_store_with_corroborated_split_passes(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    calendar_path = data_dir / "fundamentals" / "pbr" / "split-calendar.json"
    _write_calendar(calendar_path, CALENDAR)
    _write_store(
        data_dir,
        "AAA",
        [
            ("2025-01-10", 200.0, 200.0),
            # 2:1 split: -50% on both open and close corroborates the calendar.
            ("2025-01-13", 100.0, 100.6),
            ("2025-01-14", 101.0, 101.5),
        ],
    )

    report = build_report(data_dir=data_dir, calendar_path=calendar_path)

    assert report.ok, render(report)
    assert report.symbols[0].symbol == "AAA"
    assert report.symbols[0].calendar_events == 1
    assert report.failures == []


def test_unregistered_split_is_reported_as_failure(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    calendar_path = data_dir / "fundamentals" / "pbr" / "split-calendar.json"
    _write_calendar(calendar_path, {})
    _write_store(
        data_dir,
        "AAA",
        [
            ("2025-01-10", 400.0, 400.0),
            # 2:1 split with no calendar entry: a fake -50% return if not caught.
            ("2025-01-13", 200.0, 200.0),
            ("2025-01-14", 201.0, 201.0),
        ],
    )

    report = build_report(data_dir=data_dir, calendar_path=calendar_path)

    assert not report.ok
    assert [item.kind for item in report.failures] == ["unexplained_jump"]
    assert report.failures[0].symbol == "AAA"
    assert report.failures[0].session == "2025-01-13"


def test_already_split_adjusted_series_is_flagged_as_mismatch(tmp_path: Path) -> None:
    """Double adjustment guard: adjusted data + a calendar entry must fail."""

    data_dir = tmp_path / "data"
    calendar_path = data_dir / "fundamentals" / "pbr" / "split-calendar.json"
    _write_calendar(calendar_path, CALENDAR)
    _write_store(
        data_dir,
        "AAA",
        [
            # History is already on the post-split basis, so the split date shows
            # a ~1.0 ratio where the calendar expects 0.5.
            ("2025-01-10", 100.0, 100.0),
            ("2025-01-13", 100.2, 100.4),
            ("2025-01-14", 100.5, 100.8),
        ],
    )

    report = build_report(data_dir=data_dir, calendar_path=calendar_path)

    assert not report.ok
    assert [item.kind for item in report.failures] == ["calendar_mismatch"]
    finding = report.failures[0]
    assert finding.expected == 0.5
    assert finding.observed is not None and finding.observed > 0.99


def test_calendar_symbol_without_persisted_series_is_a_coverage_hole(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    calendar_path = data_dir / "fundamentals" / "pbr" / "split-calendar.json"
    _write_calendar(calendar_path, CALENDAR)
    _write_store(data_dir, "BBB", _flat(3))

    report = build_report(data_dir=data_dir, calendar_path=calendar_path)

    assert not report.ok
    assert [item.kind for item in report.failures] == ["calendar_symbol_missing"]
    assert report.failures[0].symbol == "AAA"


def test_genuine_crash_below_jump_threshold_only_watches(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    calendar_path = data_dir / "fundamentals" / "pbr" / "split-calendar.json"
    _write_calendar(calendar_path, {})
    _write_store(
        data_dir,
        "AAA",
        [
            ("2025-01-10", 100.0, 100.0),
            ("2025-01-13", 74.0, 73.5),  # -26.5%: real earnings crash, not a split
            ("2025-01-14", 74.5, 75.0),
        ],
    )

    report = build_report(data_dir=data_dir, calendar_path=calendar_path)

    assert report.ok, render(report)
    kinds = [item.kind for item in report.symbols[0].findings]
    assert kinds == ["watch_jump"]


def test_calendar_entry_outside_the_stored_window_is_not_a_failure(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    calendar_path = data_dir / "fundamentals" / "pbr" / "split-calendar.json"
    _write_calendar(
        calendar_path,
        {"AAA": [{"effective_date": "2019-05-01", "ratio": 10.0}]},
    )
    _write_store(data_dir, "AAA", _flat(3))

    report = build_report(data_dir=data_dir, calendar_path=calendar_path)

    assert report.ok, render(report)
    assert report.symbols[0].calendar_events_outside_window == 1


def test_load_calendar_normalizes_both_shapes(tmp_path: Path) -> None:
    path = tmp_path / "split-calendar.json"
    _write_calendar(
        path,
        {
            "aaa": [{"effective_date": "2025-01-13", "ratio": 2}],
            "BBB": {"2024-06-10": 10},
            "CCC": [],
        },
    )

    calendar = load_calendar(path)

    assert calendar["AAA"] == {"2025-01-13": 2.0}
    assert calendar["BBB"] == {"2024-06-10": 10.0}
    assert calendar["CCC"] == {}


def test_symbol_filter_limits_the_scan(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    calendar_path = data_dir / "fundamentals" / "pbr" / "split-calendar.json"
    _write_calendar(calendar_path, {})
    _write_store(data_dir, "AAA", _flat(3))
    _write_store(data_dir, "BBB", _flat(3))

    report = build_report(
        data_dir=data_dir,
        calendar_path=calendar_path,
        symbols=["bbb"],
    )

    assert [entry.symbol for entry in report.symbols] == ["BBB"]


def test_missing_store_reports_cannot_run(tmp_path: Path) -> None:
    calendar_path = tmp_path / "split-calendar.json"
    _write_calendar(calendar_path, {})

    try:
        build_report(data_dir=tmp_path / "absent", calendar_path=calendar_path)
    except FileNotFoundError as exc:
        assert "stock OHLCV store not found" in str(exc)
    else:  # pragma: no cover - the guard must raise
        raise AssertionError("missing store must raise FileNotFoundError")
