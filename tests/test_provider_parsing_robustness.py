"""Broken-input robustness regression net for the Yahoo price/fundamentals parsers.

Scope: ``yowayowa.providers.yahoo`` and ``yowayowa.providers.yahoo_fundamentals``.
Everything here is driven by stubbed ``yfinance`` objects and in-memory
DataFrames -- no real HTTP, no saved payload that could drift from a live
response. The parser gaps this file once reported (F1-F4) are fixed in
production code, so the cases below are now plain assertions instead of
``xfail`` markers.

The three invariants this file pins down
----------------------------------------
I1  A missing/absent value is never turned into a number (no 0, no 1.0).
I2  A currency is never guessed, swapped between listings, or accepted without
    validation by accident; a missing currency stays ``None`` with a
    machine-readable reason.
I3  A reported zero stays a zero and a missing value stays ``None``/absent, so
    downstream arithmetic can tell them apart.

Input-case table (case -> observed behaviour -> pinned by)
---------------------------------------------------------
history()
  close column absent ................ bars == [] (no zero bar) ........... I1
  every close NaN .................... bars == [] + explicit as_of fallback  I1
  close 0.0 next to a NaN row ........ 0.0 kept, NaN row skipped ........... I3
  volume column absent / NaN ......... volume is None, never 0.0 ........... I1/I3
  volume 0 ........................... 0.0 kept ............................ I3
  numeric strings in OHLC ............ parsed by float() ................... -
  non-numeric close ("n/a") .......... ValueError, no fabricated bar ....... I1
  price column absent with close ok .. KeyError (fails closed) ............. I1
  indicators on a partial frame ...... KeyError (no fabricated series) ...... I1
  frame rows out of date order ....... re-sorted, newest bar is as_of ....... F3

quotes()
  empty request ...................... quotes {}, unavailable [] ........... -
  empty download ..................... every symbol unavailable, no 0 ...... I1
  close all NaN ...................... unavailable, no price 0.0 ........... I1
  single close ....................... previous_close None, not 0.0 ........ I1/I3
  mixed close types .................. garbage dropped, previous = prior .... I1/I3
  close 0.0 .......................... price 0.0 kept ...................... I3
  fast_info without currency ......... currency None + NOT_PROVIDED ....... I2
  metadata currency "" / None ........ currency None + NOT_PROVIDED ....... I2
  metadata currency " jpy " .......... "JPY" (normalized+stripped, cached) . I2
  metadata currency "XYZQ"/"12" ...... currency None + INVALID ............. I2
  fast_info raises ................... currency None + LOOKUP_FAILED ....... I2
  .T listing, no metadata ............ currency None (no JPY guess) ........ I2
  two listings, USD + JPY ............ each keeps its own currency ......... I2
  unavailable symbol ................. no currency lookup attempted ........ I2
  flat (non-MultiIndex) frame ........ first symbol priced, rest unavailable . F1
  flat frame, one symbol requested ... priced normally ...................... -

fx_quote() / fx_history()
  OHLC partially NaN ................. present fields kept, rest None ...... I1/I3
  every field NaN .................... LookupError ......................... I1
  close non-numeric / NaN ............ LookupError (no 0 rate) ............. I1
  single close ....................... previous_close None ................. I3
  close 0.0 / negative ............... LookupError (no 0 rate) ............. F2
  close 0.0 then 155.0 ............... 0.0 dropped, previous None .......... I1

company_facts() / _point()
  all statement frames empty ......... LookupError, no zero metrics ........ I1
  "" / "n/a" / None / pd.NA / bool ... no point for that period ........... I1
  "1,000" (thousands separator) ...... dropped (not parsed) ............... I1
  0 .................................. Decimal("0.0") point kept ........... I3
  bad period key ..................... skipped ............................ I1
  period key as int 20251231 ......... 2025-12-31 FY point ................. -
  duplicate normalized rows .......... one point .......................... -
  only income statement available .... income metrics only, no zero rows ... I1
  no net-income point ................ no EPS derivation (no 0 EPS) ....... I1
  metadata currency missing, no .T ... unit "" / "per share" (no USD) ...... I2
  metadata currency lowercase ........ upper-cased ........................ I2
  metadata currency overrides .T ..... metadata wins ....................... I2
  metadata currency " jpy " .......... unit "JPY" (stripped + validated) .... F4
  metadata currency "123" / "XYZQ" ... unit "" (never a statement unit) .... F4
  metadata not a Mapping ............. {} fallback, symbol as name ......... I2
  point.currency field ............... always None (never price currency) .. I2
  same period in yearly + quarterly .. both FY and Q points kept ........... -

Findings F1-F4 (reported by the review of t_68669f8d and fixed here):

  F1  ``_symbol_frame`` hands a flat (non-MultiIndex) frame to every caller, so
      one close series used to be stamped onto every requested symbol/spec and
      pair one listing's price with another listing's currency. A flat frame
      carries no symbol identity, so ``_quotes_from_frame`` / ``_overview_from_frame``
      now attribute it to the first requested symbol only; the rest are
      reported unavailable.
  F2  ``fx_quote`` accepted a 0.0 close as a real rate. Non-positive closes are
      now dropped before the snapshot is built, so a bad row raises
      ``LookupError`` instead of publishing a zero FX rate.
  F3  ``history`` derived ``provenance.as_of`` from the last provider row, so an
      out-of-order response published a stale as_of. The frame is now ordered
      chronologically before bars and indicators, which also orders the bars.
  F4  ``company_facts`` built the statement unit from metadata currency with
      only ``str(...).upper()``. It now validates with ``normalize_currency``
      (strip + ISO-style three letters) and degrades to an empty unit when the
      value is unusable -- the same contract the quote path already had.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pandas as pd
import pytest

from yowayowa.config import Settings
from yowayowa.providers import yahoo as yahoo_module
from yowayowa.providers import yahoo_fundamentals as yahoo_fundamentals_module
from yowayowa.providers.yahoo import MarketInstrumentSpec, YahooMarketProvider
from yowayowa.providers.yahoo_fundamentals import YahooFundamentalsProvider
from yowayowa.quote_currency import (
    CURRENCY_REASON_INVALID,
    CURRENCY_REASON_LOOKUP_FAILED,
    CURRENCY_REASON_NOT_PROVIDED,
    CURRENCY_SOURCE_FAST_INFO,
)


def _provider() -> YahooMarketProvider:
    return YahooMarketProvider(Settings(database_url="sqlite:///:memory:"))


def _fundamentals() -> YahooFundamentalsProvider:
    return YahooFundamentalsProvider(Settings(database_url="sqlite:///:memory:"))


def _index(*dates: str) -> pd.DatetimeIndex:
    return pd.DatetimeIndex([pd.Timestamp(value) for value in dates])


def _multi_frame(
    columns: dict[tuple[str, str], list[object]], *, index: pd.DatetimeIndex
) -> pd.DataFrame:
    """Build a yfinance-shaped ``(symbol, field)`` column frame."""

    frame = pd.DataFrame({key: values for key, values in columns.items()}, index=index)
    frame.columns = pd.MultiIndex.from_tuples(list(columns))
    return frame


def _close_frame(symbol: str, closes: list[object], *, index: pd.DatetimeIndex) -> pd.DataFrame:
    return _multi_frame({(symbol, "Close"): closes}, index=index)


# --------------------------------------------------------------------- history


def _history_ticker(frame: pd.DataFrame) -> type:
    class HistoryTicker:
        def __init__(self, symbol: str) -> None:
            self.symbol = symbol

        def history(self, **_kwargs: object) -> pd.DataFrame:
            return frame

    return HistoryTicker


def _stub_history(monkeypatch: pytest.MonkeyPatch, frame: pd.DataFrame) -> None:
    monkeypatch.setattr(yahoo_module.yf, "Ticker", _history_ticker(frame))


def test_history_missing_close_column_yields_no_bars_not_zero_bars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_history(
        monkeypatch,
        pd.DataFrame({"Open": [1.0], "High": [2.0], "Low": [0.5]}, index=_index("2026-09-24")),
    )

    assert _provider().history("RKLB").bars == []


def test_history_all_nan_close_yields_no_bars_and_explicit_as_of(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_history(
        monkeypatch,
        pd.DataFrame(
            {
                "Open": [1.0],
                "High": [2.0],
                "Low": [0.5],
                "Close": [float("nan")],
                "Volume": [10],
            },
            index=_index("2026-09-24"),
        ),
    )

    history = _provider().history("RKLB")

    assert history.bars == []
    # The empty result still gets a real timestamp, never the skipped row's date.
    assert history.provenance.as_of == history.provenance.retrieved_at


def test_history_keeps_reported_zero_close_but_skips_missing_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_history(
        monkeypatch,
        pd.DataFrame(
            {
                "Open": [0.0, 4.0],
                "High": [0.0, 4.0],
                "Low": [0.0, 4.0],
                "Close": [0.0, float("nan")],
            },
            index=_index("2026-09-23", "2026-09-24"),
        ),
    )

    bars = _provider().history("RKLB").bars

    assert [bar.close for bar in bars] == [0.0]
    assert [bar.timestamp.date() for bar in bars] == [date(2026, 9, 23)]


def test_history_missing_volume_is_none_not_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_history(
        monkeypatch,
        pd.DataFrame(
            {"Open": [1.0], "High": [2.0], "Low": [0.5], "Close": [1.5]},
            index=_index("2026-09-24"),
        ),
    )

    assert _provider().history("RKLB").bars[0].volume is None


def test_history_nan_volume_is_none_but_reported_zero_volume_stays_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_history(
        monkeypatch,
        pd.DataFrame(
            {
                "Open": [1.0, 1.0],
                "High": [2.0, 2.0],
                "Low": [0.5, 0.5],
                "Close": [1.5, 1.5],
                "Volume": [float("nan"), 0],
            },
            index=_index("2026-09-23", "2026-09-24"),
        ),
    )

    bars = _provider().history("RKLB").bars

    assert bars[0].volume is None
    assert bars[1].volume == 0.0


def test_history_parses_numeric_strings_from_partial_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_history(
        monkeypatch,
        pd.DataFrame(
            {
                "Open": ["1"],
                "High": ["2"],
                "Low": ["0.5"],
                "Close": ["1.5"],
                "Volume": [None],
            },
            index=_index("2026-09-24"),
        ),
    )

    bar = _provider().history("RKLB").bars[0]

    assert (bar.open, bar.high, bar.low, bar.close) == (1.0, 2.0, 0.5, 1.5)
    assert bar.volume is None


def test_history_non_numeric_close_fails_closed_instead_of_zero_filling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_history(
        monkeypatch,
        pd.DataFrame(
            {"Open": [1.0], "High": [2.0], "Low": [0.5], "Close": ["n/a"]},
            index=_index("2026-09-24"),
        ),
    )

    with pytest.raises(ValueError):
        _provider().history("RKLB")


def test_history_missing_price_column_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A close-only frame cannot produce a bar, so the parse aborts (F5 note)."""

    _stub_history(monkeypatch, pd.DataFrame({"Close": [11.0]}, index=_index("2026-09-24")))

    with pytest.raises(KeyError):
        _provider().history("RKLB")


def test_history_partial_frame_with_indicators_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_history(
        monkeypatch,
        pd.DataFrame(
            {"Close": [1.0, 2.0, 3.0]},
            index=_index("2026-09-22", "2026-09-23", "2026-09-24"),
        ),
    )

    with pytest.raises(KeyError):
        _provider().history("RKLB", indicators=["sma:2"])


def test_history_as_of_is_the_newest_bar_even_when_rows_arrive_unordered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_history(
        monkeypatch,
        pd.DataFrame(
            {
                "Open": [1.0, 2.0],
                "High": [1.0, 2.0],
                "Low": [1.0, 2.0],
                "Close": [1.0, 2.0],
            },
            index=_index("2026-09-24", "2026-09-23"),
        ),
    )

    history = _provider().history("RKLB")

    assert history.provenance.as_of == datetime(2026, 9, 24, tzinfo=UTC)
    assert [bar.timestamp.date() for bar in history.bars] == [
        date(2026, 9, 23),
        date(2026, 9, 24),
    ]


# ---------------------------------------------------------------------- quotes


def test_quotes_with_no_symbols_returns_no_quotes() -> None:
    batch = _provider().quotes([])

    assert batch.quotes == {}
    assert batch.unavailable_symbols == []


def test_quotes_empty_download_marks_every_symbol_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(yahoo_module.yf, "download", lambda **_kwargs: pd.DataFrame())

    batch = _provider().quotes(["RKLB", "7203.T"])

    assert batch.quotes == {}
    assert batch.unavailable_symbols == ["7203.T", "RKLB"]


def test_quotes_all_nan_close_is_unavailable_not_zero_price(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        yahoo_module.yf,
        "download",
        lambda **_kwargs: _close_frame("RKLB", [float("nan")], index=_index("2026-09-24")),
    )

    batch = _provider().quotes(["RKLB"])

    assert batch.quotes == {}
    assert batch.unavailable_symbols == ["RKLB"]


def test_quotes_single_close_has_no_previous_close(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        yahoo_module.yf,
        "download",
        lambda **_kwargs: _close_frame("RKLB", [10.0], index=_index("2026-09-24")),
    )

    quote = _provider().quotes(["RKLB"]).quotes["RKLB"]

    assert quote.price == 10.0
    assert quote.previous_close is None


def test_quotes_mixed_types_drop_garbage_and_use_previous_valid_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        yahoo_module.yf,
        "download",
        lambda **_kwargs: _close_frame(
            "RKLB",
            ["1.5", "abc", None, 2.5],
            index=_index("2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24"),
        ),
    )

    quote = _provider().quotes(["RKLB"]).quotes["RKLB"]

    assert quote.price == 2.5
    assert quote.previous_close == 1.5


def test_quotes_reported_zero_close_is_a_price_not_a_missing_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        yahoo_module.yf,
        "download",
        lambda **_kwargs: _close_frame(
            "RKLB", [0.0, 0.0], index=_index("2026-09-23", "2026-09-24")
        ),
    )

    quote = _provider().quotes(["RKLB"]).quotes["RKLB"]

    assert quote.price == 0.0
    assert quote.previous_close == 0.0


# ------------------------------------------------------------ quote currencies


def _quote_ticker(behaviours: dict[str, object]) -> tuple[type, list[str]]:
    """Ticker stub whose ``fast_info`` is controlled per provider symbol."""

    calls: list[str] = []

    class QuoteTicker:
        def __init__(self, symbol: str) -> None:
            self.symbol = symbol
            calls.append(symbol)
            behaviour = behaviours.get(symbol, {})
            if isinstance(behaviour, Exception):
                raise behaviour
            self.fast_info = behaviour

        def history(self, **_kwargs: object) -> pd.DataFrame:
            return pd.DataFrame()

    return QuoteTicker, calls


def _stub_quotes(
    monkeypatch: pytest.MonkeyPatch, frame: pd.DataFrame, behaviours: dict[str, object]
) -> list[str]:
    ticker, calls = _quote_ticker(behaviours)
    monkeypatch.setattr(yahoo_module.yf, "Ticker", ticker)
    monkeypatch.setattr(yahoo_module.yf, "download", lambda **_kwargs: frame)
    return calls


def test_quote_currency_missing_metadata_is_none_with_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame = _close_frame("7203.T", [2500.0], index=_index("2026-09-24"))
    _stub_quotes(monkeypatch, frame, {"7203.T": {}})

    quote = _provider().quotes(["7203.T"]).quotes["7203.T"]

    # The ".T" suffix is not evidence: no JPY guess is allowed on this path.
    assert quote.currency is None
    assert quote.currency_source is None
    assert quote.currency_reason == CURRENCY_REASON_NOT_PROVIDED


@pytest.mark.parametrize("fast_info", [{"currency": ""}, {"currency": None}, {}])
def test_quote_currency_blank_metadata_is_not_provided(
    monkeypatch: pytest.MonkeyPatch, fast_info: dict[str, object]
) -> None:
    frame = _close_frame("RKLB", [10.0], index=_index("2026-09-24"))
    _stub_quotes(monkeypatch, frame, {"RKLB": fast_info})

    quote = _provider().quotes(["RKLB"]).quotes["RKLB"]

    assert quote.currency is None
    assert quote.currency_reason == CURRENCY_REASON_NOT_PROVIDED


def test_quote_currency_is_normalized_and_sourced(monkeypatch: pytest.MonkeyPatch) -> None:
    frame = _close_frame("7203.T", [2500.0], index=_index("2026-09-24"))
    _stub_quotes(monkeypatch, frame, {"7203.T": {"currency": " jpy "}})

    quote = _provider().quotes(["7203.T"]).quotes["7203.T"]

    assert quote.currency == "JPY"
    assert quote.currency_source == CURRENCY_SOURCE_FAST_INFO
    assert quote.currency_reason is None


@pytest.mark.parametrize("raw", ["XYZQ", "12", "US D", "1234"])
def test_quote_currency_invalid_metadata_is_rejected(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    frame = _close_frame("RKLB", [10.0], index=_index("2026-09-24"))
    _stub_quotes(monkeypatch, frame, {"RKLB": {"currency": raw}})

    quote = _provider().quotes(["RKLB"]).quotes["RKLB"]

    assert quote.currency is None
    assert quote.currency_reason == CURRENCY_REASON_INVALID


def test_quote_currency_lookup_failure_is_reported_and_not_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame = _close_frame("RKLB", [10.0], index=_index("2026-09-24"))
    behaviours: dict[str, object] = {"RKLB": RuntimeError("fast_info exploded")}
    calls = _stub_quotes(monkeypatch, frame, behaviours)
    provider = _provider()

    first = provider.quotes(["RKLB"]).quotes["RKLB"]
    assert first.currency is None
    assert first.currency_reason == CURRENCY_REASON_LOOKUP_FAILED

    # A transient failure must be retried, not frozen into the cache.
    behaviours["RKLB"] = {"currency": "USD"}
    provider._quote_cache.clear()
    second = provider.quotes(["RKLB"]).quotes["RKLB"]

    assert second.currency == "USD"
    assert calls.count("RKLB") >= 2


def test_quote_currency_success_is_cached_across_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    frame = _close_frame("RKLB", [10.0], index=_index("2026-09-24"))
    calls = _stub_quotes(monkeypatch, frame, {"RKLB": {"currency": "usd"}})
    provider = _provider()

    provider.quotes(["RKLB"])
    provider._quote_cache.clear()
    quote = provider.quotes(["RKLB"]).quotes["RKLB"]

    assert quote.currency == "USD"
    assert calls.count("RKLB") == 1


def test_quote_currencies_do_not_bleed_between_listings(monkeypatch: pytest.MonkeyPatch) -> None:
    frame = _multi_frame(
        {("RKLB", "Close"): [10.0, 11.0], ("7203.T", "Close"): [2500.0, 2510.0]},
        index=_index("2026-09-23", "2026-09-24"),
    )
    calls = _stub_quotes(
        monkeypatch,
        frame,
        {
            "RKLB": {"currency": "USD"},
            "7203.T": {"currency": "JPY"},
            "MISSING": {"currency": "EUR"},
        },
    )

    batch = _provider().quotes(["RKLB", "7203.T", "MISSING"])

    assert batch.quotes["RKLB"].currency == "USD"
    assert batch.quotes["7203.T"].currency == "JPY"
    assert batch.quotes["RKLB"].price == 11.0
    assert batch.quotes["7203.T"].price == 2510.0
    # An unpriced listing has nothing to denominate, so it is never looked up.
    assert batch.unavailable_symbols == ["MISSING"]
    assert "MISSING" not in calls


def test_quote_currency_contract_none_always_has_a_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    frame = _multi_frame(
        {("RKLB", "Close"): [10.0], ("7203.T", "Close"): [2500.0]},
        index=_index("2026-09-24"),
    )
    _stub_quotes(monkeypatch, frame, {"RKLB": {"currency": "USD"}, "7203.T": {}})

    batch = _provider().quotes(["RKLB", "7203.T"])

    for quote in batch.quotes.values():
        if quote.currency is None:
            assert quote.currency_reason is not None
            assert quote.currency_source is None
        else:
            assert quote.currency_reason is None
            assert quote.currency_source == CURRENCY_SOURCE_FAST_INFO


def test_quotes_flat_frame_must_not_apply_one_series_to_every_symbol() -> None:
    flat = pd.DataFrame({"Close": [10.0, 11.0]}, index=_index("2026-09-23", "2026-09-24"))

    batch = YahooMarketProvider._quotes_from_frame(flat, ("RKLB", "7203.T"))

    assert "RKLB" in batch.quotes
    assert "7203.T" not in batch.quotes
    assert "7203.T" in batch.unavailable_symbols
    assert batch.unavailable_symbols == ["7203.T"]
    # The series is used once, not copied: the priced quote is the first symbol.
    assert batch.quotes["RKLB"].price == 11.0


def test_quotes_flat_frame_single_symbol_still_prices_the_listing() -> None:
    flat = pd.DataFrame({"Close": [10.0, 11.0]}, index=_index("2026-09-23", "2026-09-24"))

    batch = YahooMarketProvider._quotes_from_frame(flat, ("RKLB",))

    assert list(batch.quotes) == ["RKLB"]
    assert batch.quotes["RKLB"].price == 11.0
    assert batch.quotes["RKLB"].previous_close == 10.0
    assert batch.unavailable_symbols == []


def test_overview_flat_frame_must_not_duplicate_one_series_onto_every_spec() -> None:
    flat = pd.DataFrame({"Close": [10.0, 11.0]}, index=_index("2026-09-23", "2026-09-24"))
    specs = (
        MarketInstrumentSpec("^GSPC", "S&P 500", "US equities", "points"),
        MarketInstrumentSpec("^VIX", "VIX", "Volatility", "index"),
    )

    overview = YahooMarketProvider._overview_from_frame(flat, specs)

    assert len(overview.items) == 1
    assert [item.symbol for item in overview.items] == ["^GSPC"]
    assert overview.unavailable_symbols == ["^VIX"]


def test_overview_zero_baseline_yields_none_change_not_a_number() -> None:
    frame = _close_frame(
        "^GSPC", [0.0, 0.0, 5.0], index=_index("2026-09-22", "2026-09-23", "2026-09-24")
    )

    item = YahooMarketProvider._overview_from_frame(
        frame,
        (MarketInstrumentSpec("^GSPC", "S&P 500", "US equities", "points"),),
    ).items[0]

    assert item.value == 5.0
    assert item.change_1d is None
    assert item.change_1m is None
    assert item.change_1y is None


def test_overview_skips_nan_closes_instead_of_zero_filling() -> None:
    frame = _close_frame("^GSPC", [float("nan"), 5.0], index=_index("2026-09-23", "2026-09-24"))

    item = YahooMarketProvider._overview_from_frame(
        frame,
        (MarketInstrumentSpec("^GSPC", "S&P 500", "US equities", "points"),),
    ).items[0]

    assert item.value == 5.0
    assert item.change_1d is None
    assert len(item.sparkline) == 1


# -------------------------------------------------------------------------- fx


def test_fx_history_partial_fields_stay_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_history(
        monkeypatch,
        pd.DataFrame(
            {
                "Open": [float("nan")],
                "High": [155.2],
                "Low": [154.0],
                "Close": [float("nan")],
                "Volume": [0],
            },
            index=_index("2026-09-24"),
        ),
    )

    point = _provider().fx_history("USDJPY").points[0]

    assert point.open is None
    assert point.close is None
    assert (point.high, point.low) == (155.2, 154.0)
    # A reported zero volume is a real zero, not a missing field.
    assert point.volume == 0.0


def test_fx_history_all_nan_rows_raise_instead_of_zero_points(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_history(
        monkeypatch,
        pd.DataFrame(
            {
                "Open": [float("nan")],
                "High": [float("nan")],
                "Low": [float("nan")],
                "Close": [float("nan")],
            },
            index=_index("2026-09-24"),
        ),
    )

    with pytest.raises(LookupError):
        _provider().fx_history("USDJPY")


@pytest.mark.parametrize("closes", [["n/a"], [float("nan")]])
def test_fx_quote_unusable_close_raises_instead_of_zero_rate(
    monkeypatch: pytest.MonkeyPatch, closes: list[object]
) -> None:
    _stub_history(monkeypatch, pd.DataFrame({"Close": closes}, index=_index("2026-09-24")))

    with pytest.raises(LookupError):
        _provider().fx_quote("USDJPY")


def test_fx_quote_single_close_has_no_previous_rate(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_history(monkeypatch, pd.DataFrame({"Close": [155.0]}, index=_index("2026-09-24")))

    snapshot = _provider().fx_quote("USDJPY")

    assert snapshot.rate == 155.0
    assert snapshot.previous_close is None


def test_fx_quote_zero_close_must_be_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_history(monkeypatch, pd.DataFrame({"Close": [0.0]}, index=_index("2026-09-24")))

    with pytest.raises(LookupError):
        _provider().fx_quote("USDJPY")


def test_fx_quote_negative_close_must_be_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_history(
        monkeypatch,
        pd.DataFrame({"Close": [-155.0, -154.0]}, index=_index("2026-09-23", "2026-09-24")),
    )

    with pytest.raises(LookupError):
        _provider().fx_quote("USDJPY")


def test_fx_quote_zero_previous_close_is_skipped_not_published(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_history(
        monkeypatch,
        pd.DataFrame({"Close": [0.0, 155.0]}, index=_index("2026-09-23", "2026-09-24")),
    )

    snapshot = _provider().fx_quote("USDJPY")

    assert snapshot.rate == 155.0
    assert snapshot.previous_close is None


# ----------------------------------------------------------------- fundamentals


def _statement_ticker(
    *,
    income: pd.DataFrame | None = None,
    balance: pd.DataFrame | None = None,
    cash: pd.DataFrame | None = None,
    metadata: object = None,
    both_frequencies: bool = False,
) -> type:
    income_frame = income if income is not None else pd.DataFrame()
    balance_frame = balance if balance is not None else pd.DataFrame()
    cash_frame = cash if cash is not None else pd.DataFrame()
    metadata_payload = {} if metadata is None else metadata

    class StatementTicker:
        def __init__(self, symbol: str) -> None:
            self.symbol = symbol

        @staticmethod
        def _frequency(kwargs: dict[str, object]) -> object:
            return kwargs.get("freq") or kwargs.get("frequency")

        def _pick(self, frame: pd.DataFrame, kwargs: dict[str, object]) -> pd.DataFrame:
            frequency = self._frequency(kwargs)
            if frequency == "yearly":
                return frame
            if both_frequencies and frequency == "quarterly":
                return frame
            return pd.DataFrame()

        def get_income_stmt(self, **kwargs: object) -> pd.DataFrame:
            return self._pick(income_frame, kwargs)

        def get_balance_sheet(self, **kwargs: object) -> pd.DataFrame:
            return self._pick(balance_frame, kwargs)

        def get_cash_flow(self, **kwargs: object) -> pd.DataFrame:
            return self._pick(cash_frame, kwargs)

        def get_history_metadata(self) -> object:
            return metadata_payload

    return StatementTicker


def _stub_statements(monkeypatch: pytest.MonkeyPatch, ticker: type) -> None:
    monkeypatch.setattr(yahoo_fundamentals_module.yf, "Ticker", ticker)


def _income(columns: dict[str, list[object]], index: list[str]) -> pd.DataFrame:
    return pd.DataFrame({pd.Timestamp(column): row for column, row in columns.items()}, index=index)


def test_company_facts_without_any_statement_raises_instead_of_empty_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_statements(monkeypatch, _statement_ticker())

    with pytest.raises(LookupError):
        _fundamentals().company_facts("7203.T")


def test_company_facts_partial_statements_expose_only_sourced_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_statements(
        monkeypatch,
        _statement_ticker(
            income=_income({"2025-12-31": [1000.0], "2024-12-31": [800.0]}, ["Total Revenue"]),
            metadata={"currency": "JPY", "longName": "Partial Corp"},
        ),
    )

    result = _fundamentals().company_facts("7203.T")

    assert set(result.metrics) == {"revenue"}
    # Balance-sheet metrics are absent, never present as zeros.
    for key in ("assets", "liabilities", "equity", "current_assets"):
        assert key not in result.metrics


@pytest.mark.parametrize("broken", ["", "n/a", None, pd.NA, True, "inf", "1,000", "-"])
def test_point_drops_unparseable_values_instead_of_defaulting(broken: object) -> None:
    assert YahooFundamentalsProvider._point("assets", "2025-12-31", broken, "yearly", "JPY") is None


def test_point_keeps_reported_zero_and_rejects_missing_distinctly() -> None:
    build = YahooFundamentalsProvider._point

    zero = build("assets", "2025-12-31", 0.0, "yearly", "JPY")

    assert zero is not None
    assert zero.value == Decimal("0.0")
    assert build("assets", "2025-12-31", None, "yearly", "JPY") is None
    assert build("assets", "2025-12-31", "", "yearly", "JPY") is None


def test_point_skips_unparseable_period_keys() -> None:
    build = YahooFundamentalsProvider._point

    assert build("assets", "not-a-date", "100", "yearly", "JPY") is None
    assert build("assets", pd.NaT, "100", "yearly", "JPY") is None
    assert build("assets", None, "100", "yearly", "JPY") is None


def test_point_accepts_compact_integer_period_key() -> None:
    point = YahooFundamentalsProvider._point("assets", 20251231, "100", "yearly", "JPY")

    assert point is not None
    assert point.period_end == date(2025, 12, 31)
    assert point.fiscal_period == "FY"


def test_company_facts_drops_broken_cells_and_keeps_clean_periods(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_statements(
        monkeypatch,
        _statement_ticker(
            income=_income(
                {
                    "2025-12-31": ["", "n/a", None, "1,000"],
                    "2024-12-31": [1000.0, 400.0, 40.0, 20.0],
                },
                ["Total Revenue", "Net Income", "Diluted EPS", "Diluted Average Shares"],
            ),
            metadata={"currency": "JPY"},
        ),
    )

    result = _fundamentals().company_facts("7203.T")

    for series in result.metrics.values():
        assert [point.period_end for point in series.points] == [date(2024, 12, 31)]
        assert all(point.value != 0 for point in series.points)


def test_company_facts_reported_zero_is_kept_while_missing_period_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_statements(
        monkeypatch,
        _statement_ticker(
            income=_income(
                {
                    "2025-12-31": [0.0, 0.0, 20.0],
                    "2024-12-31": [float("nan"), 400.0, 20.0],
                },
                ["Total Revenue", "Net Income", "Diluted Average Shares"],
            ),
            metadata={"currency": "JPY"},
        ),
    )

    result = _fundamentals().company_facts("7203.T")

    revenue = {point.period_end: point.value for point in result.metrics["revenue"].points}
    assert revenue == {date(2025, 12, 31): Decimal("0.0")}
    assert date(2024, 12, 31) not in revenue

    earnings = {point.period_end: point.value for point in result.metrics["net_income"].points}
    assert earnings[date(2024, 12, 31)] == Decimal("400.0")
    assert earnings[date(2025, 12, 31)] == Decimal("0.0")


def test_company_facts_does_not_derive_eps_without_a_net_income_point(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_statements(
        monkeypatch,
        _statement_ticker(
            income=_income(
                {"2025-12-31": [float("nan"), 20.0]},
                ["Net Income", "Diluted Average Shares"],
            ),
            metadata={"currency": "JPY"},
        ),
    )

    result = _fundamentals().company_facts("7203.T")

    assert "net_income" not in result.metrics
    assert "eps_diluted" not in result.metrics


def test_company_facts_missing_currency_is_never_assumed_to_be_usd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_statements(
        monkeypatch,
        _statement_ticker(
            income=_income({"2025-12-31": [1000.0, 2.5]}, ["Total Revenue", "Diluted EPS"]),
            metadata={"longName": "Some US Corp"},
        ),
    )

    result = _fundamentals().company_facts("RKLB")

    assert result.metrics["revenue"].points[0].unit == ""
    assert result.metrics["eps_diluted"].points[0].unit == "per share"


def test_company_facts_suffix_hint_only_applies_to_the_listing_suffix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame = _income({"2025-12-31": [1000.0]}, ["Total Revenue"])

    _stub_statements(monkeypatch, _statement_ticker(income=frame))
    assert _fundamentals().company_facts("7203.T").metrics["revenue"].points[0].unit == "JPY"

    _stub_statements(monkeypatch, _statement_ticker(income=frame))
    assert _fundamentals().company_facts("RKLB").metrics["revenue"].points[0].unit == ""


def test_company_facts_metadata_currency_overrides_suffix_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_statements(
        monkeypatch,
        _statement_ticker(
            income=_income({"2025-12-31": [1000.0]}, ["Total Revenue"]),
            metadata={"currency": "usd"},
        ),
    )

    result = _fundamentals().company_facts("7203.T")

    assert result.metrics["revenue"].points[0].unit == "USD"


def test_company_facts_non_mapping_metadata_falls_back_without_guessing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_statements(
        monkeypatch,
        _statement_ticker(
            income=_income({"2025-12-31": [1000.0]}, ["Total Revenue"]),
            metadata=["currency", "USD"],
        ),
    )

    result = _fundamentals().company_facts("RKLB")

    assert result.company_name == "RKLB"
    assert result.metrics["revenue"].points[0].unit == ""


def test_company_facts_never_stamps_price_currency_on_metric_points(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_statements(
        monkeypatch,
        _statement_ticker(
            income=_income(
                {"2025-12-31": [1000.0, 400.0, 20.0]},
                ["Total Revenue", "Net Income", "Diluted Average Shares"],
            ),
            balance=_income({"2025-12-31": [2000.0]}, ["Total Assets"]),
            metadata={"currency": "JPY"},
        ),
    )

    result = _fundamentals().company_facts("7203.T")

    assert {point.currency for series in result.metrics.values() for point in series.points} == {
        None
    }
    assert result.metrics["eps_diluted"].points[0].currency is None


def test_company_facts_duplicate_index_rows_yield_one_point(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_statements(
        monkeypatch,
        _statement_ticker(
            income=pd.DataFrame(
                {pd.Timestamp("2025-12-31"): [1000.0, 2000.0]},
                index=["Total Revenue", "total revenue"],
            ),
            metadata={"currency": "JPY"},
        ),
    )

    result = _fundamentals().company_facts("7203.T")

    points = result.metrics["revenue"].points
    assert len(points) == 1
    assert points[0].value in {Decimal("1000.0"), Decimal("2000.0")}


def test_company_facts_keeps_yearly_and_quarterly_points_labelled_apart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_statements(
        monkeypatch,
        _statement_ticker(
            income=_income({"2025-12-31": [10.0]}, ["Total Revenue"]),
            metadata={"currency": "JPY"},
            both_frequencies=True,
        ),
    )

    result = _fundamentals().company_facts("7203.T")

    labels = {(point.period_end, point.fiscal_period) for point in result.metrics["revenue"].points}
    # The same period appears twice, but with distinct labels so a consumer can
    # pick the annual figure instead of summing both.
    assert labels == {(date(2025, 12, 31), "FY"), (date(2025, 12, 31), "Q")}


def test_company_facts_currency_metadata_is_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_statements(
        monkeypatch,
        _statement_ticker(
            income=_income({"2025-12-31": [1000.0]}, ["Total Revenue"]),
            metadata={"currency": " jpy "},
        ),
    )

    result = _fundamentals().company_facts("7203.T")

    assert result.metrics["revenue"].points[0].unit == "JPY"


def test_company_facts_non_iso_currency_is_not_used_as_a_unit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_statements(
        monkeypatch,
        _statement_ticker(
            income=_income({"2025-12-31": [1000.0]}, ["Total Revenue"]),
            metadata={"currency": "123"},
        ),
    )

    result = _fundamentals().company_facts("7203.T")

    assert result.metrics["revenue"].points[0].unit == ""


def test_company_facts_provenance_as_of_is_the_latest_sourced_period(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_statements(
        monkeypatch,
        _statement_ticker(
            income=_income({"2025-12-31": [1000.0], "2024-12-31": [800.0]}, ["Total Revenue"]),
            metadata={"currency": "JPY"},
        ),
    )

    result = _fundamentals().company_facts("7203.T")

    assert result.provenance.as_of == date(2025, 12, 31)
    assert result.provenance.retrieved_at.tzinfo is not None
