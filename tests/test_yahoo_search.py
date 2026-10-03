from __future__ import annotations

from typing import Any

import pytest

from yowayowa.config import Settings
from yowayowa.providers import yahoo_search
from yowayowa.providers.yahoo_search import YahooSearchProvider


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {"database_url": "sqlite:///:memory:"}
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def _instrument_row(**overrides: object) -> dict[str, Any]:
    row: dict[str, Any] = {
        "symbol": "aapl",
        "longname": "Apple Inc.",
        "quoteType": "EQUITY",
        "exchange": "NMS",
        "currency": "usd",
    }
    row.update(overrides)
    return row


def test_yahoo_search_builds_search_with_policy_enforced_settings() -> None:
    provider = YahooSearchProvider(_settings())

    assert provider.settings.request_timeout_seconds > 0


def test_yahoo_search_skips_non_dict_rows_and_enforces_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    raw_rows: list[Any] = [
        _instrument_row(symbol="AAPL", longname="Apple Inc."),
        _instrument_row(symbol="MSFT", longname="Microsoft", quoteType="INDEX"),
        _instrument_row(symbol="EXTRA", longname="Beyond limit"),
        "not-a-dict",
        42,
        None,
    ]

    class RecordingSearch:
        def __init__(self, query: str, **kwargs: Any) -> None:
            captured["query"] = query
            captured["kwargs"] = kwargs
            self.quotes = raw_rows

    monkeypatch.setattr(yahoo_search.yf, "Search", RecordingSearch)
    provider = YahooSearchProvider(_settings())

    results = provider.search("tech stocks", limit=2)
    over_limit = provider.search("tech stocks", limit=6)

    assert captured["query"] == "tech stocks"
    assert captured["kwargs"]["max_results"] == 6
    assert captured["kwargs"]["news_count"] == 0
    assert [instrument.symbol for instrument in results] == ["AAPL", "MSFT"]
    # Non-dict rows are skipped; rows beyond the limit are never inspected.
    assert [instrument.symbol for instrument in over_limit] == ["AAPL", "MSFT", "EXTRA"]


def test_yahoo_search_instrument_resolves_name_fallback_chain() -> None:
    chain = [
        {"symbol": "X", "longname": "Long", "shortname": "Short"},
        {"symbol": "X", "longName": "LongName", "shortName": "ShortName"},
        {"symbol": "X", "shortname": "ShortOnly"},
        {"symbol": "X", "shortName": "ShortCamel"},
        {"symbol": "X"},
    ]
    names = [YahooSearchProvider._instrument(row).name for row in chain]  # type: ignore[arg-type]
    assert names == ["Long", "LongName", "ShortOnly", "ShortCamel", "X"]


def test_yahoo_search_instrument_empty_symbol_returns_none() -> None:
    for symbol in ("", "   ", None, 0):
        row: dict[str, Any] = {"symbol": symbol, "longname": "Named"}
        assert YahooSearchProvider._instrument(row) is None


def test_yahoo_search_instrument_resolves_quote_type_exchange_currency() -> None:
    row = _instrument_row(
        symbol="7203.T",
        longname="Toyota",
        quoteType="EQURY",
        exchange=None,
        exchDisp="Tokyo",
        currency="jpy",
    )
    instrument = YahooSearchProvider._instrument(row)  # type: ignore[arg-type]
    assert instrument is not None
    assert instrument.symbol == "7203.T"
    assert instrument.name == "Toyota"
    assert instrument.instrument_type == "equry"
    assert instrument.exchange == "Tokyo"
    assert instrument.currency == "JPY"


def test_yahoo_search_instrument_defaults_quote_type_and_normalizes_symbol() -> None:
    row: dict[str, Any] = {"symbol": " msft ", "typeDisp": "Index"}
    instrument = YahooSearchProvider._instrument(row)

    assert instrument is not None
    assert instrument.symbol == "MSFT"
    assert instrument.instrument_type == "index"
    assert instrument.exchange is None
    assert instrument.currency is None
