"""Unit tests for the split-calendar coverage monitor (scripts/check_split_coverage.py).

The monitor is the operational guard for the price-adjustment policy: prices are
stored raw and the backtest engine is the only place splits are applied, so a
calendar hole or an already-adjusted series must fail loudly here instead of
silently producing a fake -90% return or a double correction.
"""

from __future__ import annotations

import json
from pathlib import Path

from scripts.check_split_coverage import (
    CorporateAction,
    build_report,
    compare_corporate_actions,
    load_calendar,
    normalize_corporate_actions,
    render,
)

CALENDAR = {"AAA": [{"effective_date": "2025-01-13", "ratio": 2.0}]}

# The recorded Alpaca corporate-actions snapshot (t_2fc44a51 audit,
# fetched 2026-11-05): every split Alpaca reported for the 14 stored symbols.
SNAPSHOT_FIXTURE = Path(__file__).parent / "fixtures" / "corporate_actions_snapshot_20261105.json"

FULL_CALENDAR = {
    "AAPL": [{"effective_date": "2020-08-31", "ratio": 4.0}],
    "AMZN": [{"effective_date": "2022-06-06", "ratio": 20.0}],
    "GOOGL": [{"effective_date": "2022-07-18", "ratio": 20.0}],
    "NVDA": [
        {"effective_date": "2021-07-20", "ratio": 4.0},
        {"effective_date": "2024-06-10", "ratio": 10.0},
    ],
    "TSLA": [
        {"effective_date": "2020-08-31", "ratio": 5.0},
        {"effective_date": "2022-08-25", "ratio": 3.0},
    ],
}


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


# --- corporate-actions cross-check (audit criterion t_abaea291: missing = fail) ---


def _load_snapshot() -> list[CorporateAction]:
    payload = json.loads(SNAPSHOT_FIXTURE.read_text(encoding="utf-8"))
    return normalize_corporate_actions(payload)


def test_normalize_accepts_snapshot_api_and_flat_shapes() -> None:
    snapshot = normalize_corporate_actions(
        {"AAA": [["2025-01-13", 1, 2]], "_provenance": {"source": "x"}}
    )
    api = normalize_corporate_actions(
        {
            "corporate_actions": {
                "forward_splits": [
                    {
                        "symbol": "BBB",
                        "ex_date": "2025-02-03",
                        "old_rate": 1,
                        "new_rate": 10,
                    }
                ]
            }
        }
    )
    flat = normalize_corporate_actions(
        [{"symbol": "CCC", "ex_date": "2025-03-03", "old_rate": 1, "new_rate": 3}]
    )

    assert snapshot == [CorporateAction(symbol="AAA", ex_date="2025-01-13", ratio=2.0)]
    assert api == [CorporateAction(symbol="BBB", ex_date="2025-02-03", ratio=10.0)]
    assert flat == [CorporateAction(symbol="CCC", ex_date="2025-03-03", ratio=3.0)]


def test_recorded_snapshot_fully_covered_by_calendar() -> None:
    """The 2026-11-05 snapshot: 7 Alpaca events, all 7 present with equal ratios."""

    actions = _load_snapshot()
    assert len(actions) == 7

    failures, notices = compare_corporate_actions(actions, FULL_CALENDAR)
    assert failures == []
    assert notices == []


def test_snapshot_calendar_shape_also_loads_cleanly() -> None:
    """load_calendar skips the snapshot's _provenance metadata block; the
    [ex_date, old, new] triple shape is corporate-actions-only and yields
    empty event maps there (that is normalize_corporate_actions' job)."""

    calendar = load_calendar(SNAPSHOT_FIXTURE)
    assert "_PROVENANCE" not in calendar
    assert set(calendar) == {
        "AAPL",
        "AMZN",
        "GOOGL",
        "IWM",
        "JNJ",
        "JPM",
        "META",
        "MSFT",
        "NVDA",
        "PG",
        "QQQ",
        "SPY",
        "TSLA",
        "XOM",
    }
    assert all(events == {} for events in calendar.values())


def test_snapshot_also_matches_the_live_calendar_file() -> None:
    """Same cross-check against the repo's real split-calendar.json (no store)."""

    repo_calendar = (
        Path(__file__).parent.parent / "data" / "fundamentals" / "pbr" / "split-calendar.json"
    )
    if not repo_calendar.is_file():  # gitignored store absent (fresh clone)
        return
    failures, _ = compare_corporate_actions(_load_snapshot(), load_calendar(repo_calendar))
    assert failures == []


def test_missing_corporate_action_fails() -> None:
    calendar = {
        "NVDA": [
            {"effective_date": "2021-07-20", "ratio": 4.0},
            # 2024-06-10 10:1 deliberately absent
        ]
    }

    failures, _ = compare_corporate_actions(_load_snapshot(), calendar)

    missing = [item for item in failures if item.kind == "corporate_action_missing_from_calendar"]
    assert len(missing) == 6  # everything except NVDA 2021-07-20
    nvda_2024 = next(
        item for item in missing if item.symbol == "NVDA" and item.session == "2024-06-10"
    )
    assert nvda_2024.observed == 10.0


def test_ratio_mismatch_fails() -> None:
    calendar = {"NVDA": [{"effective_date": "2024-06-10", "ratio": 5.0}]}

    failures, _ = compare_corporate_actions(_load_snapshot(), calendar)

    mismatches = [item for item in failures if item.kind == "corporate_action_ratio_mismatch"]
    assert len(mismatches) == 1
    assert mismatches[0].symbol == "NVDA"
    assert mismatches[0].expected == 5.0
    assert mismatches[0].observed == 10.0


def test_calendar_only_event_is_notice_not_failure() -> None:
    actions = [CorporateAction(symbol="AAA", ex_date="2025-01-13", ratio=2.0)]
    calendar = {
        "AAA": [{"effective_date": "2025-01-13", "ratio": 2.0}],
        "BBB": [{"effective_date": "2019-05-01", "ratio": 10.0}],
    }

    failures, notices = compare_corporate_actions(actions, calendar)

    assert failures == []
    assert len(notices) == 1
    assert "BBB 2019-05-01" in notices[0]


def test_build_report_wires_corporate_action_failures(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    calendar_path = data_dir / "fundamentals" / "pbr" / "split-calendar.json"
    _write_calendar(calendar_path, {})
    _write_store(data_dir, "AAA", _flat(3))

    report = build_report(
        data_dir=data_dir,
        calendar_path=calendar_path,
        corporate_actions=[CorporateAction(symbol="AAA", ex_date="2025-01-13", ratio=2.0)],
    )

    assert not report.ok
    assert report.corporate_actions_checked == 1
    assert [item.kind for item in report.failures] == ["corporate_action_missing_from_calendar"]
    assert "corporate_action_missing_from_calendar AAA 2025-01-13" in render(report)
