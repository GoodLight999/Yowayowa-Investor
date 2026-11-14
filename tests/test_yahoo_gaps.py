"""Batch 2 gap coverage for the Yahoo market provider.

Covers paths measured missing: empty/trimmed history & quote results, FX
quote/history resolution and payload edge shapes, the overview HTTP entry,
and the ``_symbol_frame``/``_close_series`` empty / missing-column /
multi-index fallbacks. yfinance boundaries are stubbed via monkeypatch; no
network access occurs.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pytest

from yowayowa.config import Settings
from yowayowa.providers import yahoo as yahoo_module
from yowayowa.providers.yahoo import (
    DEFAULT_MARKET_UNIVERSE,
    MarketInstrumentSpec,
    YahooMarketProvider,
)


def _provider() -> YahooMarketProvider:
    return YahooMarketProvider(Settings(database_url="sqlite:///:memory:"))


def _frame(values: dict[str, object], index: pd.DatetimeIndex) -> pd.DataFrame:
    return pd.DataFrame(values, index=index)


# --------------------------------------------------------------- history


def test_history_raises_on_empty_download(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    class EmptyTicker:
        def __init__(self, symbol: str) -> None:
            self.symbol = symbol

        def history(self, **_kwargs: object) -> pd.DataFrame:
            return pd.DataFrame()

    monkeypatch.setattr(yahoo_module.yf, "Ticker", EmptyTicker)

    with pytest.raises(LookupError, match="No market history returned for RKLB"):
        _provider().history("RKLB")


def test_history_reuses_cache_for_identical_request(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    index = pd.DatetimeIndex([datetime(2026, 9, 24, tzinfo=UTC)], name="Date")
    payload = _frame(
        {
            "Open": [10.0],
            "High": [12.0],
            "Low": [9.0],
            "Close": [11.0],
            "Volume": [1000],
        },
        index,
    )

    class HistoryTicker:
        def __init__(self, symbol: str) -> None:
            self.symbol = symbol

        def history(self, **_kwargs: object) -> pd.DataFrame:
            return payload

    monkeypatch.setattr(yahoo_module.yf, "Ticker", HistoryTicker)
    provider = _provider()

    first = provider.history("RKLB", indicators=[])
    second = provider.history("rklb ", indicators=[])

    assert first is second
    assert first.symbol == "RKLB"
    assert first.bars[0].close == 11.0


def test_history_maps_us_class_share_to_dash_symbol(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    seen: list[str] = []

    class RecordingTicker:
        def __init__(self, symbol: str) -> None:
            seen.append(symbol)

        def history(self, **_kwargs: object) -> pd.DataFrame:
            return pd.DataFrame()

    monkeypatch.setattr(yahoo_module.yf, "Ticker", RecordingTicker)

    with pytest.raises(LookupError):
        _provider().history("BRK.B")
    with pytest.raises(LookupError):
        _provider().history("6758.T")

    assert seen == ["BRK-B", "6758.T"]


# ---------------------------------------------------------------- quotes


def test_quotes_empty_symbol_list_short_circuits(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def boom(**_kwargs: object) -> pd.DataFrame:
        raise AssertionError("download must not be called without symbols")

    monkeypatch.setattr(yahoo_module.yf, "download", boom)

    result = _provider().quotes([])

    assert result.quotes == {}
    assert result.unavailable_symbols == []


def test_quotes_whitelist_deduplicates_and_sorts_symbols(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    downloaded: list[list[str]] = []

    def download(**kwargs: object) -> pd.DataFrame:
        downloaded.append(list(kwargs["tickers"]))
        return pd.DataFrame()

    monkeypatch.setattr(yahoo_module.yf, "download", download)

    result = _provider().quotes(["rklb", " RKLB", "asts.t "])

    assert result.quotes == {}
    assert sorted(result.unavailable_symbols) == ["ASTS.T", "RKLB"]
    assert downloaded == [["ASTS.T", "RKLB"]]


def test_quotes_download_none_payload_marks_unavailable(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(yahoo_module.yf, "download", lambda **_: None)

    result = _provider().quotes(["RKLB"])

    assert result.unavailable_symbols == ["RKLB"]


# ----------------------------------------------------------------- fx


def test_fx_quote_raises_when_close_series_is_empty(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    class EmptyTicker:
        def __init__(self, symbol: str) -> None:
            self.symbol = symbol

        def history(self, **_kwargs: object) -> pd.DataFrame:
            return pd.DataFrame()

    monkeypatch.setattr(yahoo_module.yf, "Ticker", EmptyTicker)

    with pytest.raises(LookupError, match="No FX rate returned for USDJPY"):
        _provider().fx_quote("USDJPY")


def test_fx_quote_single_close_has_no_previous_close(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    index = pd.DatetimeIndex([datetime(2026, 9, 25, tzinfo=UTC)], name="Date")
    frame = _frame({"Close": [150.0]}, index)

    class SingleRowTicker:
        def __init__(self, symbol: str) -> None:
            assert symbol == "JPY=X"

        def history(self, **_kwargs: object) -> pd.DataFrame:
            return frame

    monkeypatch.setattr(yahoo_module.yf, "Ticker", SingleRowTicker)

    result = _provider().fx_quote("USDJPY")

    assert result.rate == 150.0
    assert result.previous_close is None
    assert result.as_of == datetime(2026, 9, 25, tzinfo=UTC)


def test_fx_quote_uses_previous_close_when_two_rows(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    index = pd.DatetimeIndex(
        [datetime(2026, 9, 24, tzinfo=UTC), datetime(2026, 9, 25, tzinfo=UTC)], name="Date"
    )
    frame = _frame({"Close": [149.0, 150.0]}, index)

    class TwoRowTicker:
        def __init__(self, symbol: str) -> None:
            self.symbol = symbol

        def history(self, **_kwargs: object) -> pd.DataFrame:
            return frame

    monkeypatch.setattr(yahoo_module.yf, "Ticker", TwoRowTicker)

    result = _provider().fx_quote("USDJPY")

    assert result.rate == 150.0
    assert result.previous_close == 149.0
    # second call resolves from the TTL cache
    again = _provider().fx_quote("USDJPY")
    assert again.rate == 150.0


def test_fx_history_raises_when_no_points_survive(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    class EmptyTicker:
        def __init__(self, symbol: str) -> None:
            self.symbol = symbol

        def history(self, **_kwargs: object) -> pd.DataFrame:
            return pd.DataFrame()

    monkeypatch.setattr(yahoo_module.yf, "Ticker", EmptyTicker)

    with pytest.raises(LookupError, match="No FX history returned for USDJPY"):
        _provider().fx_history("USDJPY")


def test_fx_history_skips_fully_absent_rows(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    index = pd.DatetimeIndex(
        [
            datetime(2026, 9, 24, tzinfo=UTC),
            datetime(2026, 9, 25, tzinfo=UTC),
        ],
        name="Date",
    )
    frame = pd.DataFrame(
        {
            "Open": [float("nan"), 149.5],
            "High": [float("nan"), 150.5],
            "Low": [float("nan"), 149.2],
            "Close": [float("nan"), 150.0],
            "Volume": [float("nan"), float("nan")],
        },
        index=index,
    )

    class PartialTicker:
        def __init__(self, symbol: str) -> None:
            self.symbol = symbol

        def history(self, **_kwargs: object) -> pd.DataFrame:
            return frame

    monkeypatch.setattr(yahoo_module.yf, "Ticker", PartialTicker)

    result = _provider().fx_history("USDJPY")

    assert len(result.points) == 1
    assert result.points[0].close == 150.0
    assert result.points[0].volume is None


# ------------------------------------------------------------ overview


def test_overview_raises_when_download_returns_empty(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(yahoo_module.yf, "download", lambda **_: pd.DataFrame())

    with pytest.raises(LookupError, match="No market overview data returned"):
        _provider().overview()


def test_overview_caches_between_calls(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    index = pd.date_range("2026-01-02", periods=3, freq="B", tz="UTC")
    calls: list[list[str]] = []

    def download(**kwargs: object) -> pd.DataFrame:
        calls.append(list(kwargs["tickers"]))
        return pd.DataFrame(
            {(symbol, "Close"): [1.0, 2.0, 3.0] for symbol in kwargs["tickers"]},
            index=index,
        )

    monkeypatch.setattr(yahoo_module.yf, "download", download)
    provider = _provider()

    first = provider.overview()
    second = provider.overview()

    assert first is second
    assert len(calls) == 1
    assert set(calls[0]) == {spec.symbol for spec in DEFAULT_MARKET_UNIVERSE}


def test_overview_none_payload_raises(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(yahoo_module.yf, "download", lambda **_: None)

    with pytest.raises(LookupError, match="No market overview data returned"):
        _provider().overview()


# ------------------------------------------------- frame / close-series edges


def test_symbol_frame_empty_frame_returns_none() -> None:
    assert YahooMarketProvider._symbol_frame(pd.DataFrame(), "RKLB") is None


def test_symbol_frame_level_one_multiindex_lookup() -> None:
    """A level-1 hit is cross-sectioned: the matching level-0 label survives."""
    index = pd.DatetimeIndex([datetime(2026, 9, 25, tzinfo=UTC)])
    frame = pd.DataFrame(
        {("OTHER", "Close"): [1.0], ("OTHER", "Open"): [0.5]},
        index=index,
    )
    frame.columns = pd.MultiIndex.from_tuples(frame.columns)

    result = YahooMarketProvider._symbol_frame(frame, "Close")

    assert result is not None
    assert result.iloc[0, 0] == 1.0
    assert str(result.columns[0]) == "OTHER"


def test_symbol_frame_returns_none_when_symbol_absent() -> None:
    index = pd.DatetimeIndex([datetime(2026, 9, 25, tzinfo=UTC)])
    frame = pd.DataFrame({("AAA", "Close"): [1.0]}, index=index)
    frame.columns = pd.MultiIndex.from_tuples(frame.columns)

    assert YahooMarketProvider._symbol_frame(frame, "ZZZ") is None


def test_symbol_frame_single_level_returns_frame_as_is() -> None:
    index = pd.DatetimeIndex([datetime(2026, 9, 25, tzinfo=UTC)])
    frame = _frame({"Close": [1.0]}, index)

    assert YahooMarketProvider._symbol_frame(frame, "RKLB") is frame


def test_symbol_frame_primary_non_dataframe_column_is_wrapped() -> None:
    index = pd.DatetimeIndex([datetime(2026, 9, 25, tzinfo=UTC)])
    # A MultiIndex whose sub-column resolves to a Series is wrapped into a frame.
    frame = pd.DataFrame(
        {("RKLB", "Close"): [1.0], ("ZZZ", "Close"): [2.0]},
        index=index,
    )
    frame.columns = pd.MultiIndex.from_tuples(frame.columns)

    primary = YahooMarketProvider._symbol_frame(frame, "RKLB")

    assert isinstance(primary, pd.DataFrame)
    assert list(primary.columns) == ["Close"]


def test_close_series_returns_none_without_close_column() -> None:
    index = pd.DatetimeIndex([datetime(2026, 9, 25, tzinfo=UTC)])
    frame = _frame({"Open": [1.0]}, index)

    assert YahooMarketProvider._close_series(frame) is None
    assert YahooMarketProvider._close_series(None) is None


def test_close_series_narrowed_from_dataframe_column() -> None:
    """A duplicated-column frame slice is narrowed to its first column."""
    index = pd.DatetimeIndex([datetime(2026, 9, 24, tzinfo=UTC), datetime(2026, 9, 25, tzinfo=UTC)])
    wide = pd.DataFrame({"Close": [10.0, 11.0]}, index=index)
    duplicated = pd.concat([wide, wide], axis=1)  # two identical "Close" columns
    duplicated.columns = ["Close", "Close_dup"]

    close = YahooMarketProvider._close_series(duplicated)
    assert close is not None
    assert list(close.index) == [index[0], index[1]]
    assert close.iloc[-1] == 11.0


def test_close_series_drops_nan_rows_and_sorts() -> None:
    index = pd.DatetimeIndex(
        [
            datetime(2026, 9, 25, tzinfo=UTC),
            datetime(2026, 9, 24, tzinfo=UTC),
        ]
    )
    frame = _frame({"Close": [float("nan"), 10.0]}, index)

    close = YahooMarketProvider._close_series(frame)

    assert close is not None
    assert list(close.index) == [pd.Timestamp(datetime(2026, 9, 24, tzinfo=UTC))]
    assert close.iloc[-1] == 10.0


def test_spec_label_is_preserved_in_universe() -> None:
    first = DEFAULT_MARKET_UNIVERSE[0]
    assert isinstance(first, MarketInstrumentSpec)
    assert first.symbol == "^GSPC"


def test_quotes_cache_hit_skips_download(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    downloaded: list[int] = []

    def download(**_kwargs: object) -> pd.DataFrame:
        downloaded.append(1)
        return pd.DataFrame()

    monkeypatch.setattr(yahoo_module.yf, "download", download)
    provider = _provider()

    first = provider.quotes(["RKLB"])
    second = provider.quotes(["RKLB"])

    assert first is second
    assert len(downloaded) == 1


def test_fx_quote_non_empty_frame_with_extra_columns(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    index = pd.DatetimeIndex(
        [datetime(2026, 9, 24, tzinfo=UTC), datetime(2026, 9, 25, tzinfo=UTC)], name="Date"
    )
    frame = _frame(
        {"Open": [148.0, 149.0], "Close": [149.0, 150.0], "Volume": [10, 20]},
        index,
    )

    class WideTicker:
        def __init__(self, symbol: str) -> None:
            self.symbol = symbol

        def history(self, **_kwargs: object) -> pd.DataFrame:
            return frame

    monkeypatch.setattr(yahoo_module.yf, "Ticker", WideTicker)

    result = _provider().fx_quote("USDJPY")

    assert result.rate == 150.0
    assert result.previous_close == 149.0


def test_fx_history_cache_hit_skips_download(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A successful fetch is cached; a failed one is refetched (no negative cache)."""
    downloaded: list[int] = []

    class StubTicker:
        def __init__(self, symbol: str) -> None:
            self.symbol = symbol

        def history(self, **_kwargs: object) -> pd.DataFrame:
            downloaded.append(1)
            if len(downloaded) == 1:
                return pd.DataFrame()
            index = pd.DatetimeIndex([datetime(2026, 9, 25, tzinfo=UTC)], name="Date")
            return _frame({"Close": [150.0]}, index)

    monkeypatch.setattr(yahoo_module.yf, "Ticker", StubTicker)
    provider = _provider()

    with pytest.raises(LookupError):
        provider.fx_history("USDJPY")
    result = provider.fx_history("USDJPY")

    assert len(downloaded) == 2
    assert result.points[0].close == 150.0

    # The successful result is now cached.
    again = provider.fx_history("USDJPY")
    assert again is result
    assert len(downloaded) == 2


def test_quotes_from_frame_single_close_quote() -> None:
    index = pd.DatetimeIndex([datetime(2026, 9, 25, tzinfo=UTC)])
    frame = pd.DataFrame({("RKLB", "Close"): [20.0]}, index=index)
    frame.columns = pd.MultiIndex.from_tuples(frame.columns)

    result = YahooMarketProvider._quotes_from_frame(frame, ("RKLB",))

    assert result.quotes["RKLB"].price == 20.0
    assert result.quotes["RKLB"].previous_close is None


def test_overview_from_frame_handles_snapshot_only_symbol() -> None:
    index = pd.DatetimeIndex([datetime(2026, 9, 25, tzinfo=UTC)])
    frame = pd.DataFrame({("AAA", "Close"): [5.0]}, index=index)
    frame.columns = pd.MultiIndex.from_tuples(frame.columns)
    specs = (
        MarketInstrumentSpec("AAA", "Alpha", "Equities", "points"),
        MarketInstrumentSpec("BBB", "Missing", "FX", "rate"),
    )

    result = YahooMarketProvider._overview_from_frame(frame, specs)

    assert [item.symbol for item in result.items] == ["AAA"]
    assert result.items[0].change_1d is None  # single close has no baseline
    assert result.unavailable_symbols == ["BBB"]


def test_close_series_first_column_narrowing() -> None:
    """_close_series narrows a frame slice back to a Series via .iloc[:, 0]."""
    index = pd.DatetimeIndex([datetime(2026, 9, 25, tzinfo=UTC)])
    wide = pd.DataFrame({"Close": [10.0, 11.0][:1]}, index=index)
    tripled = pd.concat([wide] * 3, axis=1)
    tripled.columns = ["Close", "Close2", "Close3"]

    close = YahooMarketProvider._close_series(tripled)
    assert close is not None
    assert close.iloc[0] == 10.0


def test_change_and_utc_timestamp_helpers() -> None:
    naive = "2026-09-27 10:00:00"

    stamp = YahooMarketProvider._utc_timestamp(naive)

    assert stamp.tzinfo is UTC
    assert stamp.hour == 10

    aware = "2026-09-27T09:00:00+00:00"
    assert YahooMarketProvider._utc_timestamp(aware) == datetime(2026, 9, 27, 9, 0, tzinfo=UTC)


def test_fx_quote_cache_hit_skips_download(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Second fx_quote resolves from the TTL cache even with a failing ticker."""
    index = pd.DatetimeIndex(
        [datetime(2026, 9, 24, tzinfo=UTC), datetime(2026, 9, 25, tzinfo=UTC)], name="Date"
    )
    frame = _frame({"Close": [149.0, 150.0]}, index)

    class OnceTicker:
        def __init__(self, symbol: str) -> None:
            self.symbol = symbol

        def history(self, **_kwargs: object) -> pd.DataFrame:
            return frame

    monkeypatch.setattr(yahoo_module.yf, "Ticker", OnceTicker)
    provider = _provider()

    first = provider.fx_quote("USDJPY")

    class FailingTicker:
        def __init__(self, symbol: str) -> None:
            self.symbol = symbol

    monkeypatch.setattr(yahoo_module.yf, "Ticker", FailingTicker)

    again = provider.fx_quote("USDJPY")

    assert again is first
    assert again.rate == 150.0


def test_change_zero_baseline_returns_none() -> None:
    baseline = 0.0
    assert YahooMarketProvider._change(10.0, baseline) is None


def test_close_series_duplicated_close_labels_hit_frame_branch() -> None:
    """Two identical Close columns make .loc return a frame; helper narrows it."""
    index = pd.DatetimeIndex([datetime(2026, 9, 25, tzinfo=UTC)])
    frame = pd.DataFrame([[10.0, 10.0]], columns=["Close", "Close"], index=index)

    close = YahooMarketProvider._close_series(frame)

    assert close is not None
    assert close.iloc[0] == 10.0
