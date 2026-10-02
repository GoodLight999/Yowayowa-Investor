from __future__ import annotations

import importlib.util
import json
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest

from yowayowa.config import Settings
from yowayowa.providers.sec import SecClient
from yowayowa.sec_pbr import build_pbr, filing_inputs, join_pbr, split_calendar
from yowayowa.services.backtest import _garp_point_in_time_pbr, _target_weights


def fact(
    val: float, end: str = "2024-03-31", filed: str = "2024-05-04", accn: str = "a", **extra: Any
) -> dict[str, Any]:
    return {"val": val, "end": end, "filed": filed, "accn": accn, "form": "10-Q", **extra}


def payload(
    equity: list[dict[str, Any]],
    shares: list[dict[str, Any]],
    *,
    gaap: bool = False,
    weighted: bool = False,
    splits: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    tag = (
        "WeightedAverageNumberOfSharesOutstandingBasic"
        if weighted
        else "CommonStockSharesOutstanding"
    )
    return {
        "cik": 1,
        "facts": {
            "us-gaap": {
                "StockholdersEquity": {"units": {"USD": equity}},
                **({tag: {"units": {"shares": shares}}} if gaap or weighted else {}),
                "StockholdersEquityNoteStockSplitConversionRatio1": {
                    "units": {"pure": splits or []}
                },
            },
            "dei": {}
            if gaap or weighted
            else {"EntityCommonStockSharesOutstanding": {"units": {"shares": shares}}},
        },
    }


def bar(day: str, close: float = 10, opening: float | None = None) -> dict[str, Any]:
    return {
        "symbol": "AAPL",
        "as_of": day,
        "close": close,
        "open": opening or close,
        "provider": "alpaca",
        "pbr": None,
        "disclosure_date": None,
    }


def test_weekend_filed_and_strict_decision_boundary() -> None:
    data = payload([fact(1000)], [fact(200)])
    rows, _ = build_pbr("AAPL", [bar("2024-05-03"), bar("2024-05-06")], data)
    assert len(rows) == 1
    assert rows[0]["disclosure_date"] == "2024-05-04"  # Saturday, not period end
    assert rows[0]["pbr"] == 2
    mapped = {date(2024, 5, 6): rows[0]}
    assert _garp_point_in_time_pbr(mapped, date(2024, 5, 6)) is None
    assert _garp_point_in_time_pbr(mapped, date(2024, 5, 7)) == 2
    row = {**rows[0], "disclosure_date": "2024-05-07"}
    assert _garp_point_in_time_pbr({date(2024, 5, 6): row}, date(2024, 5, 7)) is None
    assert _garp_point_in_time_pbr({date(2024, 5, 6): row}, date(2024, 5, 8)) == 2


def test_original_filing_not_future_restatement() -> None:
    data = payload(
        [fact(1000), fact(2000, filed="2024-06-01", accn="b")],
        [fact(200), fact(300, filed="2024-06-01", accn="b")],
    )
    rows, _ = build_pbr("AAPL", [bar("2024-05-31"), bar("2024-06-03")], data)
    assert [(r["pbr"], r["accession"]) for r in rows] == [(2, "a"), (1.5, "b")]


def test_no_cross_accession_shares_fill() -> None:
    data = payload(
        [fact(1000), fact(2000, end="2024-06-30", filed="2024-08-01", accn="b")], [fact(200)]
    )
    rows, _ = build_pbr("AAPL", [bar("2024-08-02")], data)
    assert rows[0]["pbr"] is None
    assert rows[0]["reported_shares"] is None
    assert rows[0]["accession"] == "b"


def test_latest_eligible_row_missing_remains_missing(tmp_path: Path) -> None:
    original = {"AAPL": [bar("2024-05-06"), bar("2024-05-07")]}
    (tmp_path / "AAPL.jsonl").write_text(
        json.dumps(
            {
                "symbol": "AAPL",
                "as_of": "2024-05-06",
                "disclosure_date": "2024-05-04",
                "pbr": 2,
                "per": 5,
            }
        )
        + "\n"
        + json.dumps(
            {"symbol": "AAPL", "as_of": "2024-05-07", "disclosure_date": "2024-05-04", "pbr": None}
        )
        + "\n"
    )
    joined = join_pbr(original, tmp_path)
    mapped = {date.fromisoformat(r["as_of"]): r for r in joined["AAPL"]}
    assert _garp_point_in_time_pbr(mapped, date(2024, 5, 8)) is None
    assert joined["AAPL"][1]["per"] is None
    assert original["AAPL"][0]["pbr"] is None


@pytest.mark.parametrize("ratio", [2, 3, 4, 5, 7, 10, 20])
def test_split_effective_on_price_date_not_sec_filed(ratio: int) -> None:
    data = payload(
        [fact(1000)], [fact(100)], splits=[fact(ratio, end="2024-06-10", filed="2024-08-01")]
    )
    rows, events = build_pbr("AAPL", [bar("2024-06-07", 10 * ratio), bar("2024-06-10", 10)], data)
    assert events[0]["effective_date"] == "2024-06-10"
    assert events[0]["sec_first_filed"] == "2024-08-01"
    assert rows[0]["split_factor"] == 1
    assert rows[1]["split_factor"] == ratio
    assert rows[1]["pbr"] == rows[0]["pbr"]
    assert rows[1]["disclosure_date"] == "2024-05-04"


def test_split_new_disclosure_not_double_adjusted() -> None:
    data = payload(
        [fact(1000), fact(1100, end="2024-06-30", filed="2024-08-01", accn="b")],
        [fact(100), fact(1000, end="2024-07-20", filed="2024-08-01", accn="b")],
    )
    rows, _ = build_pbr(
        "AAPL", [bar("2024-06-07", 100), bar("2024-06-10", 10), bar("2024-08-02", 11)], data
    )
    assert rows[-1]["adjusted_shares"] == 1000
    assert rows[-1]["split_factor"] == 1
    assert rows[-1]["pbr"] == 10


def test_gaap_shares_already_retroactively_presented_post_period_split() -> None:
    data = payload(
        [fact(1000), fact(1100, end="2024-06-30", filed="2024-07-27", accn="b")],
        [fact(100), fact(2000, end="2024-06-30", filed="2024-07-27", accn="b")],
        gaap=True,
        splits=[fact(20, end="2024-07-18", filed="2024-07-27", accn="b")],
    )
    rows, _ = build_pbr(
        "GOOGL",
        [
            bar("2024-07-15", 200),
            bar("2024-07-18", 10),
            bar("2024-07-26", 10),
            bar("2024-07-27", 11),
        ],
        data,
    )
    assert rows[-2]["adjusted_shares"] == 2000
    assert rows[-1]["adjusted_shares"] == 2000
    assert rows[-1]["shares_already_presented_splits"] == ["2024-07-18"]
    assert rows[-1]["split_factor"] == 1


def test_googl_20_point_5_is_not_40() -> None:
    events = split_calendar(
        [bar("2022-07-15", 2235.55), bar("2022-07-18", 109.03, 112.64)],
        payload([], [], splits=[fact(2), fact(20)]),
    )
    assert events[0]["ratio"] == 20
    assert events[0]["observed_close_ratio"] == pytest.approx(20.5039897)


def test_tsla_intraday_move_does_not_change_5_to_4() -> None:
    events = split_calendar(
        [bar("2020-08-28", 2213.4), bar("2020-08-31", 498.32, 444.61)], payload([], [])
    )
    assert events[0]["ratio"] == 5


def test_future_split_tags_do_not_control_multiplier() -> None:
    bars = [bar("2024-06-07", 100), bar("2024-06-10", 10)]
    a, _ = build_pbr("AAPL", bars, payload([fact(1000)], [fact(100)]))
    b, _ = build_pbr(
        "AAPL", bars, payload([fact(1000)], [fact(100)], splits=[fact(3, filed="2025-08-01")])
    )
    assert a == b


def test_no_split_detected_in_normal_prices() -> None:
    assert split_calendar([bar("2024-06-07", 100), bar("2024-06-10", 95)], payload([], [])) == []


def test_meta_period_average_is_explicit_and_shortest_period() -> None:
    data = payload(
        [fact(1000)], [fact(300, start="2024-01-01"), fact(200, start="2024-03-01")], weighted=True
    )
    rows, _ = build_pbr("META", [bar("2024-05-06")], data)
    assert rows[0]["shares_basis"] == "period_average"
    assert rows[0]["reported_shares"] == 200
    assert rows[0]["shares_tag"] == "us-gaap:WeightedAverageNumberOfSharesOutstandingBasic"


@pytest.mark.parametrize("equity", [0, -10])
def test_nonpositive_equity_is_missing(equity: int) -> None:
    rows, _ = build_pbr("AAPL", [bar("2024-05-06")], payload([fact(equity)], [fact(200)]))
    assert rows[0]["pbr"] is None


@pytest.mark.parametrize(
    "field,value", [("form", "8-K"), ("end", "2025-01-01"), ("val", float("nan"))]
)
def test_invalid_equity_is_not_used(field: str, value: Any) -> None:
    assert filing_inputs(payload([{**fact(1000), field: value}], [fact(200)])) == []


@pytest.mark.parametrize(
    "row",
    [
        {"symbol": "AAPL", "as_of": "2024-05-06", "disclosure_date": "2024-05-07"},
        {"symbol": "MSFT", "as_of": "2024-05-06", "disclosure_date": "2024-05-04"},
    ],
)
def test_loader_rejects_future_or_wrong_symbol(tmp_path: Path, row: dict[str, Any]) -> None:
    (tmp_path / "AAPL.jsonl").write_text(json.dumps(row) + "\n")
    with pytest.raises(ValueError):
        join_pbr({"AAPL": [bar("2024-05-06")]}, tmp_path)


def test_no_sidecar_keeps_momentum_fallback(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location(
        "s03", Path(__file__).parents[1] / "run_backtests_garp_momentum_hybrid.py"
    )
    assert spec and spec.loader
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    # 254 ordered synthetic bars ONLY for offline unit testing, never live evaluation.
    from datetime import timedelta

    histories = {
        s: [
            bar((date(2020, 1, 1) + timedelta(days=i)).isoformat(), 10 + i / 100)
            for i in range(254)
        ]
        for s in runner.UNIVERSE
    }
    joined = join_pbr(histories, tmp_path)
    rows = runner.map_histories(joined, date(2021, 1, 1))
    garp, momentum, _ = runner.definitions()
    g = _target_weights(garp, rows, date(2021, 1, 1))
    m = _target_weights(momentum, rows, date(2021, 1, 1))
    assert g == {s: m.get(s, 0) for s in runner.UNIVERSE}
    assert g["SPY"] == 0


def test_raw_public_endpoint_preserves_unabridged(httpx_mock: Any) -> None:
    original = payload([fact(1000), fact(2000, filed="2025-01-01", accn="b")], [fact(200)])
    httpx_mock.add_response(
        url="https://data.sec.gov/api/xbrl/companyfacts/CIK0000000001.json", json=original
    )
    client = SecClient(Settings())
    try:
        assert client.raw_company_facts("0000000001") == original
    finally:
        client.client.close()


@pytest.mark.parametrize("cik", ["1", "CIK0000000001", "000000000\uff11", "000000000x"])
def test_raw_cik_validation(cik: str) -> None:
    client = SecClient(Settings())
    try:
        with pytest.raises(ValueError):
            client.raw_company_facts(cik)
    finally:
        client.client.close()


def test_raw_403_not_bypassed(httpx_mock: Any) -> None:
    httpx_mock.add_response(
        url="https://data.sec.gov/api/xbrl/companyfacts/CIK0000000001.json", status_code=403
    )
    client = SecClient(Settings())
    try:
        with pytest.raises(httpx.HTTPStatusError):
            client.raw_company_facts("0000000001")
    finally:
        client.client.close()
