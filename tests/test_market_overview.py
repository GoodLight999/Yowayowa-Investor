import pandas as pd
import pytest

from yowayowa.config import Settings
from yowayowa.providers.yahoo import MarketInstrumentSpec, YahooMarketProvider
from yowayowa.providers.yahoo_sectors import SECTOR_UNIVERSE, YahooSectorProvider


def test_market_overview_normalizes_multiindex_batch_and_partial_failures() -> None:
    index = pd.date_range("2025-01-02", periods=260, freq="B", tz="UTC")
    frame = pd.DataFrame(
        {
            ("AAA", "Close"): [100 + day for day in range(260)],
            ("BBB", "Close"): [300 - day * 0.5 for day in range(260)],
        },
        index=index,
    )
    frame.columns = pd.MultiIndex.from_tuples(frame.columns)
    specs = (
        MarketInstrumentSpec("AAA", "Alpha", "Equities", "points"),
        MarketInstrumentSpec("BBB", "Beta", "Commodities", "USD"),
        MarketInstrumentSpec("CCC", "Missing", "FX", "rate"),
    )

    result = YahooMarketProvider._overview_from_frame(frame, specs)

    assert [item.symbol for item in result.items] == ["AAA", "BBB"]
    assert result.unavailable_symbols == ["CCC"]
    alpha, beta = result.items
    assert alpha.value == 359.0
    assert beta.value == 170.5
    assert alpha.change_1d is not None and alpha.change_1d > 0
    assert beta.change_1d is not None and beta.change_1d < 0
    assert alpha.change_1m is not None and alpha.change_1m > 0
    assert alpha.change_3m is not None and alpha.change_3m > 0
    assert alpha.change_1y is not None and alpha.change_1y > 0
    assert len(alpha.sparkline) == 30
    assert result.provenance.provider == "yahoo/yfinance"


def test_latest_quotes_use_last_two_valid_closes() -> None:
    index = pd.date_range("2026-08-06", periods=4, freq="B", tz="UTC")
    frame = pd.DataFrame(
        {
            ("AAA", "Close"): [10.0, 11.0, float("nan"), 12.5],
            ("BBB", "Close"): [20.0, 19.0, 18.5, 18.0],
        },
        index=index,
    )
    frame.columns = pd.MultiIndex.from_tuples(frame.columns)

    result = YahooMarketProvider._quotes_from_frame(frame, ("AAA", "BBB", "CCC"))

    assert result.quotes["AAA"].price == 12.5
    assert result.quotes["AAA"].previous_close == 11.0
    assert result.quotes["BBB"].price == 18.0
    assert result.quotes["BBB"].previous_close == 18.5
    assert result.unavailable_symbols == ["CCC"]


def test_market_change_rejects_zero_and_missing_baselines() -> None:
    assert YahooMarketProvider._change(10.0, 0) is None
    assert YahooMarketProvider._change(10.0, None) is None
    assert YahooMarketProvider._change(12.0, 10.0) == pytest.approx(0.2)


def test_sector_overview_batches_all_etfs_and_caches(monkeypatch) -> None:
    index = pd.date_range("2025-08-15", periods=260, freq="B", tz="UTC")
    data = {
        (spec.symbol, "Close"): [
            100.0 + position * 3 + day * (0.03 + position * 0.002) for day in range(260)
        ]
        for position, spec in enumerate(SECTOR_UNIVERSE)
    }
    frame = pd.DataFrame(data, index=index)
    frame.columns = pd.MultiIndex.from_tuples(frame.columns)
    calls = 0

    def download(**kwargs):
        nonlocal calls
        calls += 1
        assert set(kwargs["tickers"]) == {spec.symbol for spec in SECTOR_UNIVERSE}
        assert kwargs["period"] == "1y"
        return frame

    monkeypatch.setattr("yowayowa.providers.yahoo_sectors.yf.download", download)
    provider = YahooSectorProvider(Settings(database_url="sqlite:///:memory:", mode="personal"))

    first = provider.overview()
    second = provider.overview()

    assert len(first.items) == 11
    assert first.unavailable_symbols == []
    assert first.model_dump() == second.model_dump()
    assert calls == 1
    assert all(item.change_1d is not None for item in first.items)
    assert all(item.change_1m is not None for item in first.items)
    assert "Equal-area sector relative-strength view" in first.provenance.notes[0]
    assert "not presented as sector market-cap weight" in first.provenance.notes[1]
