"""Read-only OHLCV JSONL coverage audit for point-in-time backtest inputs."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any


def _observed(day: date) -> date:
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def _easter_sunday(year: int) -> date:
    """Gregorian Easter computus; NYSE is closed on the preceding Friday."""

    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    weekday_adjustment = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * weekday_adjustment) // 451
    month, day = divmod(h + weekday_adjustment - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    last = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def nyse_regular_holidays(year: int) -> set[date]:
    """Regular NYSE full-day holidays, including observed federal dates.

    Emergency and one-off closures are intentionally not guessed; reported
    missing sessions on those dates require an operator/calendar review.
    """

    new_year = date(year, 1, 1)
    if new_year.weekday() == 6:
        new_year += timedelta(days=1)
    holidays = {
        new_year,
        _nth_weekday(year, 1, 0, 3),  # Martin Luther King Jr. Day
        _nth_weekday(year, 2, 0, 3),  # Washington's Birthday
        _easter_sunday(year) - timedelta(days=2),  # Good Friday
        _last_weekday(year, 5, 0),  # Memorial Day
        _observed(date(year, 7, 4)),
        _nth_weekday(year, 9, 0, 1),  # Labor Day
        _nth_weekday(year, 11, 3, 4),  # Thanksgiving
        _observed(date(year, 12, 25)),  # Christmas
    }
    if year >= 2022:
        holidays.add(_observed(date(year, 6, 19)))  # Juneteenth
    # The exchanges closed for President Jimmy Carter's National Day of Mourning.
    if year == 2025:
        holidays.add(date(2025, 1, 9))
    return holidays


def _expected_dates(start: date, end: date, *, market: str) -> set[date]:
    expected: set[date] = set()
    holidays = {
        holiday
        for year in range(start.year, end.year + 1)
        for holiday in nyse_regular_holidays(year)
    }
    current = start
    while current <= end:
        if market == "crypto_calendar_day" or (current.weekday() < 5 and current not in holidays):
            expected.add(current)
        current += timedelta(days=1)
    return expected


def audit_ohlcv(
    root: Path,
    *,
    requested_start: date | None = None,
    requested_end: date | None = None,
    expected_series: list[tuple[str, str, str]] | None = None,
) -> dict[str, Any]:
    """Measure persisted coverage per symbol/provider/currency without repair."""

    grouped: dict[tuple[str, str, str], list[date]] = defaultdict(list)
    symbol_bytes: dict[str, int] = defaultdict(int)
    invalid_rows: dict[str, int] = defaultdict(int)
    files = sorted(root.glob("*/ohlcv.jsonl")) if root.exists() else []
    for path in files:
        symbol = path.parent.name.upper()
        size = path.stat().st_size
        symbol_bytes[symbol] += size
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                    provider = str(row["provider"])
                    currency = str(row["currency"])
                    as_of = datetime.fromisoformat(str(row["as_of"]).replace("Z", "+00:00"))
                    if as_of.tzinfo is None:
                        raise ValueError("as_of must include a timezone")
                    day = as_of.date()
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    invalid_rows[symbol] += 1
                    continue
                grouped[(symbol, provider, currency)].append(day)

    market = "crypto_calendar_day" if root.name == "crypto-ohlcv" else "NYSE_regular_sessions"
    for symbol, provider, currency in expected_series or []:
        grouped.setdefault((symbol.upper(), provider, currency), [])
    series: list[dict[str, Any]] = []
    coverage_start = requested_start
    coverage_end = requested_end
    for (symbol, provider, currency), raw_days in sorted(grouped.items()):
        days = sorted(set(raw_days))
        series_start = requested_start or (days[0] if days else None)
        series_end = requested_end or (days[-1] if days else None)
        if series_start is None or series_end is None:
            continue
        if series_start > series_end:
            raise ValueError("requested_start must be on or before requested_end")
        if days:
            coverage_start = min(coverage_start or days[0], days[0])
            coverage_end = max(coverage_end or days[-1], days[-1])
        expected = _expected_dates(series_start, series_end, market=market)
        observed = {day for day in days if series_start <= day <= series_end}
        missing = sorted(expected - observed)
        non_session = sorted(observed - expected)
        series.append(
            {
                "symbol": symbol,
                "provider": provider,
                "currency": currency,
                "market_calendar": market,
                "requested_start": series_start.isoformat(),
                "requested_end": series_end.isoformat(),
                "coverage_start": None if not observed else min(observed).isoformat(),
                "coverage_end": None if not observed else max(observed).isoformat(),
                "store_start": None if not days else min(days).isoformat(),
                "store_end": None if not days else max(days).isoformat(),
                "bars_in_requested_window": len(observed),
                "bars": len(raw_days),
                "unique_dates": len(days),
                "duplicate_dates": len(raw_days) - len(days),
                "expected_dates": len(expected),
                "missing_expected_dates": len(missing),
                "non_session_dates": [day.isoformat() for day in non_session[:20]],
                "non_session_dates_truncated": len(non_session) > 20,
                "missing_date_examples": [day.isoformat() for day in missing[:20]],
                "missing_date_examples_truncated": len(missing) > 20,
                "invalid_rows": invalid_rows[symbol],
            }
        )
    return {
        "store": str(root),
        "requested_start": None if requested_start is None else requested_start.isoformat(),
        "requested_end": None if requested_end is None else requested_end.isoformat(),
        "coverage_start": None if coverage_start is None else coverage_start.isoformat(),
        "coverage_end": None if coverage_end is None else coverage_end.isoformat(),
        "file_count": len(files),
        "bytes": sum(symbol_bytes.values()),
        "series_count": len(series),
        "series": series,
        "invalid_rows_by_symbol": dict(sorted(invalid_rows.items())),
        "bytes_by_symbol": dict(sorted(symbol_bytes.items())),
        "calendar_notes": [
            "NYSE gaps use regular full-day holidays; exceptional closures require manual review.",
            "A gap is measured only inside each source's observed first/last dates; "
            "no data is synthesized.",
        ],
    }
