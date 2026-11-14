from datetime import date

import pandas as pd

from yowayowa.config import Settings
from yowayowa.providers import yahoo_research
from yowayowa.providers.yahoo_research import YahooResearchProvider


class FakeSearch:
    def __init__(self, *_: object, **__: object) -> None:
        self.news = [
            {
                "id": "n1",
                "content": {
                    "title": "Rocket Lab update",
                    "provider": {"displayName": "Example Wire"},
                    "pubDate": "2026-08-12T12:00:00Z",
                    "clickThroughUrl": {"url": "https://example.test/rklb"},
                    "summary": "A concise update.",
                },
            }
        ]


class FakeCalendars:
    def __init__(self, *_: object, **__: object) -> None:
        pass

    def get_earnings_calendar(self, **_: object) -> pd.DataFrame:
        return pd.DataFrame(
            [{"Symbol": "RKLB", "Company": "Rocket Lab", "Earnings Date": "2026-08-14"}]
        )

    def get_economic_events_calendar(self, **_: object) -> pd.DataFrame:
        return pd.DataFrame([{"Country": "United States", "Event": "CPI", "Date": "2026-08-15"}])

    def get_ipo_info_calendar(self, **_: object) -> pd.DataFrame:
        return pd.DataFrame()

    def get_splits_calendar(self, **_: object) -> pd.DataFrame:
        return pd.DataFrame()


class FakeTicker:
    def __init__(self, _: str) -> None:
        self.calendar = {"Ex-Dividend Date": "2026-08-16"}


def test_news_normalization(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(yahoo_research.yf, "Search", FakeSearch)
    provider = YahooResearchProvider(Settings(database_url="sqlite:///:memory:"))
    result = provider.news("RKLB")
    assert result.items[0].title == "Rocket Lab update"
    assert result.items[0].publisher == "Example Wire"
    assert result.items[0].symbol == "RKLB"
    assert result.items[0].url == "https://example.test/rklb"


def test_calendar_normalizes_global_and_ticker_events(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(yahoo_research.yf, "Calendars", FakeCalendars)
    monkeypatch.setattr(yahoo_research.yf, "Ticker", FakeTicker)
    provider = YahooResearchProvider(Settings(database_url="sqlite:///:memory:"))
    result = provider.calendar(
        date(2026, 8, 13),
        date(2026, 8, 20),
        event_types=["earnings", "economic"],
        symbol="RKLB",
    )
    assert [event.event_type for event in result.events] == ["earnings", "economic", "ticker"]
    assert result.events[0].symbol == "RKLB"
    assert result.events[2].title == "Ex-Dividend Date"
