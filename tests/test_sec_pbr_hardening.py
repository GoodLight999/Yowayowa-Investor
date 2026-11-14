"""Offline adversarial fixtures; never used as live financial data."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from test_sec_pbr import bar, fact, payload, seal_supply

from yowayowa.sec_pbr import build_pbr, join_pbr, split_calendar, verify_supply


def test_multiple_ciks_filed_boundary_and_source_provenance() -> None:
    old = payload(
        [fact(1000, end="2026-03-31", filed="2026-05-04", accn="0000034088-26-000067")],
        [fact(200, end="2026-04-30", filed="2026-05-04", accn="0000034088-26-000067")],
    )
    new = payload(
        [fact(2000, end="2026-06-30", filed="2026-08-03", accn="0000034088-26-000093")],
        [fact(300, end="2026-07-31", filed="2026-08-03", accn="0000034088-26-000093")],
    )
    old["cik"], new["cik"] = 34088, 2115436
    rows, _ = build_pbr(
        "XOM", [bar("2026-07-31"), bar("2026-08-03"), bar("2026-08-04")], [old, new]
    )
    assert [(r["cik"], r["pbr"]) for r in rows] == [
        ("0000034088", 2),
        ("0002115436", 1.5),
        ("0002115436", 1.5),
    ]
    assert rows[1]["accession"] == "0000034088-26-000093"
    assert rows[1]["source_url"].endswith("CIK0002115436.json")
    assert rows[1]["disclosure_date"] == "2026-08-03"
    assert rows[1]["reported_shares"] == 300


def test_latest_filed_wins_even_when_period_is_older() -> None:
    data = payload(
        [fact(1000), fact(2000, end="2023-12-31", filed="2024-06-01", accn="b")],
        [fact(200), fact(300, end="2023-12-31", filed="2024-06-01", accn="b")],
    )
    rows, _ = build_pbr("AAPL", [bar("2024-06-03")], data)
    assert rows[0]["accession"] == "b"
    assert rows[0]["pbr"] == 1.5


def test_multiple_ciks_never_mix_shares_with_equity() -> None:
    a = payload([fact(1000)], [])
    b = payload([], [fact(200)])
    b["cik"] = 2
    rows, _ = build_pbr("AAPL", [bar("2024-05-06")], [a, b])
    assert rows[0]["pbr"] is None


@pytest.mark.parametrize("ratio", [0.1, 0.25, 0.5, 1.5, 2.5, 6.5])
def test_reverse_and_fractional_splits_use_sec_ratio(ratio: float) -> None:
    data = payload(
        [fact(1000)], [fact(100)], splits=[fact(ratio, end="2024-06-10", filed="2024-08-01")]
    )
    rows, events = build_pbr("AAPL", [bar("2024-06-07", 10 * ratio), bar("2024-06-10", 10)], data)
    assert len(events) == 1
    assert events[0]["ratio"] == ratio
    assert rows[-1]["adjusted_shares"] == pytest.approx(100 * ratio)
    assert rows[-1]["pbr"] == pytest.approx(rows[0]["pbr"])
    assert events[0]["sec_is_retrospective_eligibility_gate"] is True


@pytest.mark.parametrize("after", [45, 50, 58, 170, 200, 230])
def test_large_nonsplit_price_moves_do_not_create_actions(after: float) -> None:
    assert split_calendar([bar("2024-06-07", 100), bar("2024-06-10", after)], payload([], [])) == []


@pytest.mark.parametrize("end,filed", [("2020-06-10", "2024-08-01"), ("2024-06-10", "2025-08-01")])
def test_distant_sec_tags_cannot_confirm_price_shocks(end: str, filed: str) -> None:
    assert (
        split_calendar(
            [bar("2024-06-07", 100), bar("2024-06-10", 50)],
            payload([], [], splits=[fact(2, end=end, filed=filed)]),
        )
        == []
    )


@pytest.mark.parametrize("field", ["close", "open", "previous_close"])
@pytest.mark.parametrize("value", [None, 0, -1, float("nan"), float("inf"), "missing"])
def test_invalid_prices_are_skipped_not_crashes(field: str, value: Any) -> None:
    bars = [bar("2024-06-07", 100), bar("2024-06-10", 50)]
    if field == "previous_close":
        bars[0]["close"] = value
    else:
        bars[1][field] = value
    data = payload(
        [fact(1000)], [fact(100)], splits=[fact(2, end="2024-06-10", filed="2024-08-01")]
    )
    assert split_calendar(bars, data) == []
    rows, _ = build_pbr("AAPL", bars, data)
    if field == "close":
        assert rows[-1]["pbr"] is None


@pytest.mark.parametrize(
    "artifact", ["AAPL.jsonl", "split-calendar.json", "raw.json", "ticker-map.json"]
)
@pytest.mark.parametrize("action", ["modify", "delete"])
def test_manifest_artifact_tampering_fails_closed(
    tmp_path: Path, artifact: str, action: str
) -> None:
    (tmp_path / "AAPL.jsonl").write_text("")
    seal_supply(tmp_path)
    assert verify_supply(tmp_path)["schema_version"] == 2
    path = tmp_path / artifact
    if action == "delete":
        path.unlink()
    else:
        path.write_text(path.read_text() + " ")
    with pytest.raises(ValueError, match=r"SHA256 mismatch|Missing artifact"):
        join_pbr({"AAPL": [bar("2024-05-06")]}, tmp_path)


def test_manifest_missing_is_not_silent_fallback(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Missing supply manifest"):
        join_pbr({"AAPL": []}, tmp_path)


@pytest.mark.parametrize("relative", ["/root/raw.json", "../../../../raw.json"])
def test_manifest_rejects_absolute_and_escaping_raw_paths(tmp_path: Path, relative: str) -> None:
    (tmp_path / "AAPL.jsonl").write_text("")
    seal_supply(tmp_path)
    path = tmp_path / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["symbols"]["AAPL"]["raw_snapshots"][0]["raw_snapshot"] = relative
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="Nonportable or escaping"):
        verify_supply(tmp_path)


def test_verified_store_relocates_without_original_absolute_path(tmp_path: Path) -> None:
    source = tmp_path / "original"
    source.mkdir()
    (source / "AAPL.jsonl").write_text("")
    seal_supply(source)
    moved = tmp_path / "different-host-layout" / "pbr"
    shutil.copytree(source, moved)
    shutil.rmtree(source)
    assert join_pbr({"AAPL": [bar("2024-05-06")]}, moved)["AAPL"][0]["pbr"] is None


def test_split_tags_parsed_outside_bar_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    import yowayowa.sec_pbr as module

    original = module.fact_rows
    calls = []

    def counted(data: Any, namespace: str, tag: str, unit: str) -> Any:
        if tag == module.SPLIT_TAG:
            calls.append(tag)
        return original(data, namespace, tag, unit)

    monkeypatch.setattr(module, "fact_rows", counted)
    build_pbr(
        "AAPL",
        [bar(f"2024-05-{day:02d}") for day in range(6, 30)],
        payload([fact(1000)], [fact(200)]),
    )
    assert len(calls) == 2  # Calendar once, presentation-basis once, not per bar.
