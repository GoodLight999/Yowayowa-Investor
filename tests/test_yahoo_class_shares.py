from __future__ import annotations

import pandas as pd

from yowayowa.config import Settings
from yowayowa.providers import yahoo as yahoo_module
from yowayowa.providers.yahoo import YahooMarketProvider


def test_yahoo_provider_symbol_translates_us_class_shares_only() -> None:
    assert YahooMarketProvider._provider_symbol("BRK.B") == "BRK-B"
    assert YahooMarketProvider._provider_symbol("bf.b") == "BF-B"
    assert YahooMarketProvider._provider_symbol("7203.T") == "7203.T"
    assert YahooMarketProvider._provider_symbol("VOD.L") == "VOD.L"
    assert YahooMarketProvider._provider_symbol("BMW.DE") == "BMW.DE"


def test_quotes_use_yahoo_class_share_symbol_but_preserve_public_symbol(monkeypatch) -> None:
    captured: dict[str, object] = {}
    index = pd.DatetimeIndex(["2026-08-18", "2026-08-19"])
    frame = pd.DataFrame(
        [[500.0], [510.0]],
        index=index,
        columns=pd.MultiIndex.from_tuples([("BRK-B", "Close")]),
    )

    def fake_download(*, tickers, **kwargs):  # type: ignore[no-untyped-def]
        captured["tickers"] = tickers
        captured["kwargs"] = kwargs
        return frame

    monkeypatch.setattr(yahoo_module.yf, "download", fake_download)
    provider = YahooMarketProvider(Settings(database_url="sqlite:///:memory:"))

    result = provider.quotes(["BRK.B"])

    assert captured["tickers"] == ["BRK-B"]
    assert result.unavailable_symbols == []
    assert result.quotes["BRK.B"].symbol == "BRK.B"
    assert result.quotes["BRK.B"].price == 510.0
    assert result.quotes["BRK.B"].previous_close == 500.0


def test_history_uses_yahoo_class_share_symbol_but_preserves_public_symbol(monkeypatch) -> None:
    captured: dict[str, str] = {}
    index = pd.DatetimeIndex(["2026-08-18", "2026-08-19"])
    frame = pd.DataFrame(
        {
            "Open": [500.0, 505.0],
            "High": [512.0, 515.0],
            "Low": [498.0, 503.0],
            "Close": [510.0, 511.0],
            "Volume": [1000.0, 1200.0],
        },
        index=index,
    )

    class FakeTicker:
        def __init__(self, symbol: str) -> None:
            captured["symbol"] = symbol

        def history(self, **_kwargs):  # type: ignore[no-untyped-def]
            return frame

    monkeypatch.setattr(yahoo_module.yf, "Ticker", FakeTicker)
    provider = YahooMarketProvider(Settings(database_url="sqlite:///:memory:"))

    result = provider.history("BRK.B")

    assert captured["symbol"] == "BRK-B"
    assert result.symbol == "BRK.B"
    assert result.provenance.source_url.endswith("/BRK-B")
