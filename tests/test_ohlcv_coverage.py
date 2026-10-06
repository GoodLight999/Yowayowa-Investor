from __future__ import annotations

import json
from datetime import date

from yowayowa.ohlcv_coverage import audit_ohlcv, nyse_regular_holidays


def test_nyse_regular_holidays_cover_good_friday_and_juneteenth() -> None:
    holidays = nyse_regular_holidays(2024)
    assert date(2024, 3, 29) in holidays
    assert date(2024, 6, 19) in holidays
    assert date(2024, 7, 4) in holidays
    assert date(2024, 3, 28) not in holidays
    # 2021 Christmas Eve was closed, but year-end 2021 (Dec 31) was open.
    assert date(2021, 12, 24) in nyse_regular_holidays(2021)
    assert date(2021, 12, 31) not in nyse_regular_holidays(2021)
    assert date(2022, 12, 26) in nyse_regular_holidays(2022)
    assert date(2025, 1, 9) in nyse_regular_holidays(2025)


def test_audit_measures_crypto_requested_window_duplicates_and_corruption(tmp_path) -> None:
    path = tmp_path / "crypto-ohlcv" / "BTC" / "ohlcv.jsonl"
    path.parent.mkdir(parents=True)
    row = {
        "provider": "binance",
        "currency": "USDT",
        "as_of": "2023-01-02T00:00:00+00:00",
    }
    path.write_text(
        json.dumps(row) + "\n" + json.dumps(row) + "\n" + "not-json\n",
        encoding="utf-8",
    )
    report = audit_ohlcv(
        tmp_path / "crypto-ohlcv",
        requested_start=date(2023, 1, 1),
        requested_end=date(2023, 1, 3),
        expected_series=[("BTC", "binance", "USDT"), ("ETH", "binance", "USDT")],
    )
    btc, eth = report["series"]
    assert report["file_count"] == 1
    assert report["bytes"] == path.stat().st_size
    assert (btc["bars"], btc["unique_dates"], btc["duplicate_dates"]) == (2, 1, 1)
    assert btc["bars_in_requested_window"] == 1
    assert btc["missing_expected_dates"] == 2
    assert btc["coverage_start"] == "2023-01-02"
    assert btc["coverage_end"] == "2023-01-02"
    assert btc["invalid_rows"] == 1
    assert eth["coverage_start"] is None
    assert eth["missing_expected_dates"] == 3


def test_stock_audit_excludes_weekends_and_good_friday(tmp_path) -> None:
    path = tmp_path / "stock-ohlcv" / "AAPL" / "ohlcv.jsonl"
    path.parent.mkdir(parents=True)
    rows = [
        {"provider": "alpaca", "currency": "USD", "as_of": "2024-03-28T04:00:00+00:00"},
        {"provider": "alpaca", "currency": "USD", "as_of": "2024-04-01T04:00:00+00:00"},
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    report = audit_ohlcv(
        tmp_path / "stock-ohlcv",
        requested_start=date(2024, 3, 28),
        requested_end=date(2024, 4, 1),
    )
    series = report["series"][0]
    assert series["expected_dates"] == 2
    assert series["missing_expected_dates"] == 0


def test_stock_audit_flags_holiday_bar_as_non_session(tmp_path) -> None:
    path = tmp_path / "stock-ohlcv" / "AAPL" / "ohlcv.jsonl"
    path.parent.mkdir(parents=True)
    rows = [
        {"provider": "alpaca", "currency": "USD", "as_of": "2021-12-23T05:00:00+00:00"},
        {"provider": "alpaca", "currency": "USD", "as_of": "2021-12-24T05:00:00+00:00"},
        {"provider": "alpaca", "currency": "USD", "as_of": "2021-12-27T05:00:00+00:00"},
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    report = audit_ohlcv(
        tmp_path / "stock-ohlcv",
        requested_start=date(2021, 12, 23),
        requested_end=date(2021, 12, 27),
    )
    series = report["series"][0]
    assert series["missing_expected_dates"] == 0
    assert series["non_session_dates"] == ["2021-12-24"]
