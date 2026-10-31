"""US equity trading-session calendar and daily-bar finality helpers.

Two callers need the same calendar and must never disagree about it:

- ``ohlcv_coverage`` audits persisted coverage against the regular NYSE
  session calendar (the holiday table lives here, so there is exactly one
  copy of it);
- stock acquisition (``providers/alpaca.py``, ``stock_acquisition.py``) has to
  answer "is this daily bar final yet?" and "how many sessions behind is the
  newest persisted bar?".

Conventions
-----------
A daily bar is identified by its bar-start instant: 04:00Z in summer and
05:00Z in winter, i.e. midnight in US/Eastern on the session date. A bar is
*final* once that session's regular close (16:00 ET) has passed; anything
newer is an in-progress session and must not be persisted as a daily close.

Only regular full-day holidays are modelled. Emergency and one-off closures
are intentionally not guessed (same policy as ``ohlcv_coverage``), so a
one-off closure can show up as a one-session lag until an operator reviews it.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

#: Exchange timezone for US equities (DST-aware).
US_EASTERN = ZoneInfo("America/New_York")

#: Regular-session close in exchange local time.
REGULAR_CLOSE_ET = time(16, 0)

#: Upper bound on how far back :func:`last_finalized_session` will walk.
_MAX_LOOKBACK_DAYS = 20


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

    return set(_holidays(year))


@lru_cache(maxsize=32)
def _holidays(year: int) -> frozenset[date]:
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
    return frozenset(holidays)


def is_session_day(day: date) -> bool:
    """True for a regular NYSE session date (weekday, not a regular holiday)."""

    return day.weekday() < 5 and day not in _holidays(day.year)


def session_close_utc(bar_start: datetime) -> datetime:
    """UTC instant at which the session that ``bar_start`` belongs to closes."""

    local_day = bar_start.astimezone(US_EASTERN).date()
    close = datetime.combine(local_day, REGULAR_CLOSE_ET, tzinfo=US_EASTERN)
    return close.astimezone(UTC)


def is_finalized_bar(bar_start: datetime, *, now: datetime) -> bool:
    """True when the daily bar starting at ``bar_start`` is a completed session."""

    return session_close_utc(bar_start) <= now


def last_finalized_session(now: datetime) -> date:
    """Most recent session date whose regular close has already passed.

    The returned date may be a holiday-free weekday that had no trading for a
    reason the calendar does not model; callers treat it as the *expected*
    session, not as proof that a session happened.
    """

    day = now.astimezone(US_EASTERN).date()
    for _ in range(_MAX_LOOKBACK_DAYS):
        close = datetime.combine(day, REGULAR_CLOSE_ET, tzinfo=US_EASTERN).astimezone(UTC)
        if close <= now and is_session_day(day):
            return day
        day -= timedelta(days=1)
    raise ValueError(f"no finalized session within {_MAX_LOOKBACK_DAYS} days of {now!r}")


def session_lag(bar_date: date, *, now: datetime) -> int:
    """Count of expected sessions after ``bar_date`` up to the last finalized one.

    ``0`` means the newest persisted bar is the most recent finalized session;
    ``1`` means one session is missing (the "one business day behind" case the
    demo loop must be able to see). Holidays are excluded, so a market holiday
    does not register as a lag.
    """

    expected = last_finalized_session(now)
    if bar_date >= expected:
        return 0
    lag = 0
    day = bar_date + timedelta(days=1)
    while day <= expected:
        if is_session_day(day):
            lag += 1
        day += timedelta(days=1)
    return lag
