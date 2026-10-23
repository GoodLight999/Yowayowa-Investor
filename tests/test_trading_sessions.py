"""Trading-session calendar and daily-bar finality (t_bcc12803)."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from yowayowa.trading_sessions import (
    is_finalized_bar,
    is_session_day,
    last_finalized_session,
    nyse_regular_holidays,
    session_close_utc,
    session_lag,
)


def test_session_close_utc_follows_us_dst() -> None:
    # 2026-10-22 is still EDT (DST ends 2026-11-01): 16:00 ET == 20:00Z.
    assert session_close_utc(datetime(2026, 10, 22, 4, 0, tzinfo=UTC)) == datetime(
        2026, 10, 22, 20, 0, tzinfo=UTC
    )
    # 2026-01-05 is EST: 16:00 ET == 21:00Z.
    assert session_close_utc(datetime(2026, 1, 5, 5, 0, tzinfo=UTC)) == datetime(
        2026, 1, 5, 21, 0, tzinfo=UTC
    )


def test_bar_is_final_only_after_the_session_close() -> None:
    bar = datetime(2026, 10, 22, 4, 0, tzinfo=UTC)
    assert not is_finalized_bar(bar, now=datetime(2026, 10, 22, 18, 0, tzinfo=UTC))
    assert is_finalized_bar(bar, now=datetime(2026, 10, 22, 20, 0, tzinfo=UTC))
    assert is_finalized_bar(bar, now=datetime(2026, 10, 23, 0, 20, tzinfo=UTC))


def test_last_finalized_session_at_the_daily_run_time() -> None:
    # The daily job runs at 00:20Z: the previous session closed at 20:00Z.
    assert last_finalized_session(datetime(2026, 10, 23, 0, 20, tzinfo=UTC)) == date(2026, 10, 22)
    # Saturday 00:20Z: Friday's session closed the evening before.
    assert last_finalized_session(datetime(2026, 10, 24, 0, 20, tzinfo=UTC)) == date(2026, 10, 23)
    # Monday 00:20Z (before the US open): still Friday.
    assert last_finalized_session(datetime(2026, 10, 26, 0, 20, tzinfo=UTC)) == date(2026, 10, 23)


def test_last_finalized_session_skips_a_holiday() -> None:
    # 2026-11-26 is Thanksgiving; the day after, the last session is Wednesday.
    assert last_finalized_session(datetime(2026, 11, 27, 0, 20, tzinfo=UTC)) == date(2026, 11, 25)


def test_session_lag_counts_expected_sessions_only() -> None:
    now = datetime(2026, 10, 23, 0, 20, tzinfo=UTC)
    assert session_lag(date(2026, 10, 22), now=now) == 0
    assert session_lag(date(2026, 10, 21), now=now) == 1
    assert session_lag(date(2026, 10, 16), now=now) == 4  # Fri -> Mon..Thu
    # A holiday is not a missing session: 11/26 closed, 11/25 is current.
    holiday_now = datetime(2026, 11, 27, 0, 20, tzinfo=UTC)
    assert session_lag(date(2026, 11, 25), now=holiday_now) == 0
    assert session_lag(date(2026, 11, 24), now=holiday_now) == 1


def test_is_session_day_excludes_weekends_and_observed_holidays() -> None:
    assert not is_session_day(date(2026, 10, 24))  # Saturday
    assert not is_session_day(date(2026, 11, 26))  # Thanksgiving
    assert not is_session_day(date(2026, 7, 3))  # Independence Day observed (Sat 7/4)
    assert is_session_day(date(2026, 7, 2))


def test_nyse_regular_holidays_include_observed_dates() -> None:
    holidays = nyse_regular_holidays(2026)
    assert date(2026, 7, 3) in holidays
    assert date(2026, 11, 26) in holidays
    assert date(2026, 1, 1) in holidays


def test_last_finalized_session_fails_closed_without_a_session(monkeypatch) -> None:
    """No session found inside the lookback is a loud error, never a silent guess."""

    from yowayowa import trading_sessions

    monkeypatch.setattr(trading_sessions, "is_session_day", lambda _day: False)
    with pytest.raises(ValueError):
        trading_sessions.last_finalized_session(datetime(2026, 10, 23, 0, 20, tzinfo=UTC))
