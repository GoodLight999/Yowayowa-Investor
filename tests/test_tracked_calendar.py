from datetime import date
from typing import ClassVar

from yowayowa.config import Settings
from yowayowa.providers import yahoo_tracked_calendar
from yowayowa.providers.yahoo_tracked_calendar import YahooTrackedCalendarProvider


class FakeTicker:
    calls: ClassVar[dict[str, int]] = {}

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        self.calls[symbol] = self.calls.get(symbol, 0) + 1

    @property
    def calendar(self) -> dict[str, object]:
        if self.symbol == "ASTS":
            raise RuntimeError("fixture unavailable")
        return {
            "Earnings Date": ["2026-08-14", "2026-08-15"],
            "Ex-Dividend Date": "2026-08-16",
            "Dividend Date": "2026-08-20",
            "Earnings High": 0.12,
        }


def test_tracked_calendar_normalizes_dates_and_isolates_failures(monkeypatch) -> None:
    FakeTicker.calls = {}
    monkeypatch.setattr(yahoo_tracked_calendar.yf, "Ticker", FakeTicker)
    provider = YahooTrackedCalendarProvider(Settings(database_url="sqlite:///:memory:"))

    events, unavailable, provenance = provider.events(
        ["rklb", "asts"],
        date(2026, 8, 13),
        date(2026, 8, 20),
        ["earnings", "dividend"],
    )

    assert unavailable == ["ASTS"]
    assert [(event.event_type, event.subtype) for event in events] == [
        ("earnings", "earnings"),
        ("dividend", "ex_dividend"),
        ("dividend", "dividend_payment"),
    ]
    assert events[0].symbol == "RKLB"
    assert events[0].starts_at.isoformat() == "2026-08-14T00:00:00+00:00"
    assert events[0].ends_at is not None
    assert events[0].ends_at.isoformat() == "2026-08-15T00:00:00+00:00"
    assert provenance.provider == "yahoo/yfinance"
    assert provenance.license_class == "personal_only"


def test_tracked_calendar_reuses_ticker_calendar_cache(monkeypatch) -> None:
    FakeTicker.calls = {}
    monkeypatch.setattr(yahoo_tracked_calendar.yf, "Ticker", FakeTicker)
    provider = YahooTrackedCalendarProvider(Settings(database_url="sqlite:///:memory:"))

    for _ in range(2):
        events, unavailable, _ = provider.events(
            ["RKLB"],
            date(2026, 8, 13),
            date(2026, 8, 17),
            ["earnings", "dividend"],
        )
        assert len(events) == 2
        assert unavailable == []

    assert FakeTicker.calls == {"RKLB": 1}


def test_tracked_calendar_filters_events_outside_requested_range(monkeypatch) -> None:
    FakeTicker.calls = {}
    monkeypatch.setattr(yahoo_tracked_calendar.yf, "Ticker", FakeTicker)
    provider = YahooTrackedCalendarProvider(Settings(database_url="sqlite:///:memory:"))

    events, unavailable, _ = provider.events(
        ["RKLB"],
        date(2026, 8, 18),
        date(2026, 8, 19),
        ["earnings", "dividend"],
    )

    assert events == []
    assert unavailable == []
