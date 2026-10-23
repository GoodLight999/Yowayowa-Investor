"""P5 R4: price-currency contract for quotes and valuation.

Measured dead-end (``docs/P5_DEADEND_AUDIT_20261023.md`` §3 R4): every
``/v1/markets/quotes`` currency was ``null`` and ``/v1/valuation/{symbol}`` had
no currency field at all, so a price could not be carried into sizing.

Contract under test:

- ``currency`` is an ISO-4217 code copied from the provider, or ``None``.
- ``currency_source`` names the provider field it came from when resolved.
- ``currency_reason`` is one of the ``yowayowa.quote_currency`` codes whenever
  ``currency`` is ``None`` — a missing currency is never a silent ``null``.
- ``/v1/valuation/{symbol}`` copies the quote's currency fields verbatim, so the
  two surfaces cannot disagree.

No network access: ``yf.download``/``yf.Ticker`` are stubbed.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pandas as pd
import pytest

from yowayowa.config import Settings
from yowayowa.domain import (
    Fundamentals,
    LicenseClass,
    MarketQuote,
    MarketQuoteBatch,
    MetricPoint,
    MetricSeries,
    Provenance,
)
from yowayowa.providers import yahoo as yahoo_module
from yowayowa.providers.yahoo import YahooMarketProvider
from yowayowa.quote_currency import (
    CURRENCY_REASON_INVALID,
    CURRENCY_REASON_LOOKUP_FAILED,
    CURRENCY_REASON_NOT_PROVIDED,
    CURRENCY_SOURCE_FAST_INFO,
)
from yowayowa.services.valuation import valuation_snapshot


def _provider() -> YahooMarketProvider:
    return YahooMarketProvider(Settings(database_url="sqlite:///:memory:"))


def _quote_frame(rows: int = 2) -> pd.DataFrame:
    """Two-symbol batch frame as ``yf.download(group_by="ticker")`` returns it."""

    day_one = pd.Timestamp("2026-10-22")
    day_two = pd.Timestamp("2026-10-23")
    index = pd.DatetimeIndex([day_one, day_two][:rows])
    columns = pd.MultiIndex.from_tuples(
        [
            ("7203.T", "Close"),
            ("7203.T", "Open"),
            ("AAPL", "Close"),
            ("AAPL", "Open"),
        ]
    )
    values = [
        [3103.0, 3080.0, 348.22, 344.0],
        [3090.0, 3084.0, 343.99, 345.0],
    ][:rows]
    return pd.DataFrame(values, index=index, columns=columns)


class _FastInfo(Mapping[str, Any]):
    """Mapping like yfinance's ``_FastInfo`` (not a ``dict``)."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def __getitem__(self, key: str) -> Any:
        return self._payload[key]

    def __iter__(self):  # type: ignore[no-untyped-def]
        return iter(self._payload)

    def __len__(self) -> int:
        return len(self._payload)


_MISSING = object()


def _currency_ticker(currency_by_symbol: dict[str, object]):  # type: ignore[no-untyped-def]
    """Stub ``yf.Ticker`` whose ``fast_info`` answers from ``currency_by_symbol``."""

    class FakeTicker:
        def __init__(self, symbol: str) -> None:
            self.symbol = symbol

        @property
        def fast_info(self) -> _FastInfo:
            value = currency_by_symbol[self.symbol]
            if isinstance(value, Exception):
                raise value
            return _FastInfo({} if value is _MISSING else {"currency": value})

    return FakeTicker


def _stub_download(monkeypatch: pytest.MonkeyPatch, frame: pd.DataFrame | None) -> None:
    monkeypatch.setattr(yahoo_module.yf, "download", lambda **_: frame)


# ------------------------------------------------------------ provider: quotes


def test_quote_carries_jpy_currency_from_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_download(monkeypatch, _quote_frame())
    monkeypatch.setattr(
        yahoo_module.yf,
        "Ticker",
        _currency_ticker({"7203.T": "JPY", "AAPL": "USD"}),
    )

    batch = _provider().quotes(["7203.T", "AAPL"])

    jpy = batch.quotes["7203.T"]
    assert jpy.price == 3090.0
    assert jpy.currency == "JPY"
    assert jpy.currency_source == CURRENCY_SOURCE_FAST_INFO
    assert jpy.currency_reason is None


def test_quote_carries_usd_currency_from_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_download(monkeypatch, _quote_frame())
    monkeypatch.setattr(
        yahoo_module.yf,
        "Ticker",
        _currency_ticker({"7203.T": "JPY", "AAPL": "USD"}),
    )

    batch = _provider().quotes(["7203.T", "AAPL"])

    usd = batch.quotes["AAPL"]
    assert usd.currency == "USD"
    assert usd.currency_source == CURRENCY_SOURCE_FAST_INFO


def test_quote_without_provider_currency_is_null_with_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No currency in the provider payload -> null, but never silent."""

    _stub_download(monkeypatch, _quote_frame())
    monkeypatch.setattr(
        yahoo_module.yf,
        "Ticker",
        _currency_ticker({"7203.T": _MISSING, "AAPL": ""}),
    )

    batch = _provider().quotes(["7203.T", "AAPL"])

    for symbol in ("7203.T", "AAPL"):
        quote = batch.quotes[symbol]
        assert quote.currency is None
        assert quote.currency_source is None
        assert quote.currency_reason == CURRENCY_REASON_NOT_PROVIDED


def test_quote_currency_lookup_failure_is_reported_not_guessed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A provider error must not fall back to a ticker-suffix guess."""

    _stub_download(monkeypatch, _quote_frame())
    monkeypatch.setattr(
        yahoo_module.yf,
        "Ticker",
        _currency_ticker({"7203.T": RuntimeError("boom"), "AAPL": "USD"}),
    )

    batch = _provider().quotes(["7203.T", "AAPL"])

    failed = batch.quotes["7203.T"]
    assert failed.currency is None
    assert failed.currency_reason == CURRENCY_REASON_LOOKUP_FAILED
    # The price itself is still served: a currency gap is not a quote gap.
    assert failed.price == 3090.0
    assert batch.quotes["AAPL"].currency == "USD"


def test_quote_currency_rejects_malformed_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """A value that is not an ISO-shaped code is refused, not passed through."""

    _stub_download(monkeypatch, _quote_frame())
    monkeypatch.setattr(
        yahoo_module.yf,
        "Ticker",
        _currency_ticker({"7203.T": "US D", "AAPL": "JPYY"}),
    )

    batch = _provider().quotes(["7203.T", "AAPL"])

    assert batch.quotes["7203.T"].currency is None
    assert batch.quotes["7203.T"].currency_reason == CURRENCY_REASON_INVALID
    assert batch.quotes["7203.T"].price == 3090.0


def test_resolved_currency_is_cached_across_batches(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class CountingTicker:
        def __init__(self, symbol: str) -> None:
            calls.append(symbol)

        @property
        def fast_info(self) -> _FastInfo:
            return _FastInfo({"currency": "JPY"})

    _stub_download(monkeypatch, _quote_frame())
    monkeypatch.setattr(yahoo_module.yf, "Ticker", CountingTicker)
    provider = _provider()

    provider.quotes(["7203.T"])
    result = provider.quotes(["7203.T", "AAPL"])
    provider.quotes(["7203.T"])

    # One lookup per listing, not one per request.
    assert calls == ["7203.T", "AAPL"]
    assert result.quotes["7203.T"].currency == "JPY"


def test_currency_lookup_failure_is_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts: list[str] = []

    class FlakyTicker:
        def __init__(self, symbol: str) -> None:
            attempts.append(symbol)

        @property
        def fast_info(self) -> _FastInfo:
            if len(attempts) == 1:
                raise RuntimeError("transient")
            return _FastInfo({"currency": "JPY"})

    _stub_download(monkeypatch, _quote_frame())
    monkeypatch.setattr(yahoo_module.yf, "Ticker", FlakyTicker)
    provider = _provider()

    first = provider.quotes(["7203.T"])
    second = provider.quotes(["7203.T", "AAPL"])

    assert first.quotes["7203.T"].currency is None
    assert second.quotes["7203.T"].currency == "JPY"


def test_unavailable_symbol_skips_currency_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    """No price means no currency lookup at all (nothing to denominate)."""

    called: list[str] = []

    class WatchTicker:
        def __init__(self, symbol: str) -> None:
            called.append(symbol)

        @property
        def fast_info(self) -> _FastInfo:
            return _FastInfo({"currency": "JPY"})

    monkeypatch.setattr(
        yahoo_module.yf,
        "download",
        lambda **_: pd.DataFrame(),
    )
    monkeypatch.setattr(yahoo_module.yf, "Ticker", WatchTicker)

    batch = _provider().quotes(["9999.T"])

    assert batch.unavailable_symbols == ["9999.T"]
    assert called == []


# ------------------------------------------------------------- provenance text


def test_quote_provenance_states_currency_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_download(monkeypatch, _quote_frame())
    monkeypatch.setattr(
        yahoo_module.yf,
        "Ticker",
        _currency_ticker({"7203.T": "JPY", "AAPL": "USD"}),
    )

    notes = _provider().quotes(["7203.T"]).provenance.notes

    assert any("currency" in note and "currency_reason" in note for note in notes)


# ------------------------------------------------------- MS2 provider (no field)


def test_ms2_quote_reports_missing_currency_field(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from yowayowa.providers.ms2_rss_file import Ms2RssFileProvider

    store = tmp_path / "data" / "ms2" / "quotes"
    store.mkdir(parents=True)
    (store / "ms2_quotes_20261023.jsonl").write_text(
        '{"symbol": "7203", "as_of": "2026-10-23T15:00:00+09:00", '
        '"retrieved_at": "2026-10-23T06:00:00+00:00", "quotes": {"last": 3103.0}}\n',
        encoding="utf-8",
    )
    provider = Ms2RssFileProvider(Settings(database_url="sqlite:///:memory:"), quotes_dir=store)

    quote = provider.quotes(["7203"]).quotes["7203"]

    assert quote.price == 3103.0
    assert quote.currency is None
    assert quote.currency_reason == CURRENCY_REASON_NOT_PROVIDED


# -------------------------------------------------------- valuation single source


def _facts(symbol: str = "7203.T", currency: str | None = "JPY") -> Fundamentals:
    return Fundamentals(
        symbol=symbol,
        cik="",
        company_name="Toyota Motor Corporation",
        metrics={
            "net_income": MetricSeries(
                key="net_income",
                label="Net income",
                points=[
                    MetricPoint(
                        period_start=date(2025, 4, 1),
                        period_end=date(2026, 3, 31),
                        fiscal_year=2026,
                        fiscal_period="FY",
                        value=Decimal("1000"),
                        unit="JPY",
                        currency=currency,
                    )
                ],
            ),
            "shares_diluted": MetricSeries(
                key="shares_diluted",
                label="Diluted shares",
                points=[
                    MetricPoint(
                        period_start=date(2025, 4, 1),
                        period_end=date(2026, 3, 31),
                        fiscal_year=2026,
                        fiscal_period="FY",
                        value=Decimal("10"),
                        unit="shares",
                    )
                ],
            ),
        },
        provenance=Provenance(
            provider="yahoo/yfinance",
            source="Yahoo Finance financial statements",
            license_class=LicenseClass.PERSONAL_ONLY,
            retrieved_at="2026-10-23T00:00:00Z",
        ),
    )


def _batch(
    symbol: str = "7203.T",
    *,
    currency: str | None = "JPY",
    source: str | None = CURRENCY_SOURCE_FAST_INFO,
    reason: str | None = None,
) -> MarketQuoteBatch:
    return MarketQuoteBatch(
        quotes={
            symbol: MarketQuote(
                symbol=symbol,
                price=3103.0,
                previous_close=3084.0,
                currency=currency,
                currency_source=source,
                currency_reason=reason,
                as_of=datetime(2026, 10, 23, tzinfo=UTC),
            )
        },
        provenance=Provenance(
            provider="yahoo/yfinance",
            source="Yahoo Finance",
            license_class=LicenseClass.PERSONAL_ONLY,
            retrieved_at="2026-10-23T00:00:00Z",
        ),
    )


def test_valuation_copies_quote_currency_and_source() -> None:
    snapshot = valuation_snapshot(_facts(), _batch())

    assert snapshot.currency == "JPY"
    assert snapshot.currency_source == CURRENCY_SOURCE_FAST_INFO
    assert snapshot.currency_reason is None
    assert snapshot.metrics["price_to_earnings"] == 31.03


def test_valuation_reports_unknown_currency_reason() -> None:
    snapshot = valuation_snapshot(
        _facts(currency=None),
        _batch(currency=None, source=None, reason=CURRENCY_REASON_NOT_PROVIDED),
    )

    assert snapshot.currency is None
    assert snapshot.currency_source is None
    assert snapshot.currency_reason == CURRENCY_REASON_NOT_PROVIDED
    note = snapshot.provenance[1].notes[-1]
    assert CURRENCY_REASON_NOT_PROVIDED in note
    assert "currency-unverified" in note


def test_valuation_refuses_multiple_when_quote_and_statement_currencies_differ() -> None:
    """USD quote against JPY statements: the P/E is not comparable."""

    snapshot = valuation_snapshot(_facts(currency="JPY"), _batch(currency="USD"))

    assert snapshot.currency == "USD"
    assert snapshot.metrics["price_to_earnings"] is None
    assert snapshot.metrics["earnings_yield"] is None


# ------------------------------------------------------------------ live routes


def _api_fixtures(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("YOWAYOWA_MODE", "personal")
    monkeypatch.setenv("YOWAYOWA_DATABASE_URL", f"sqlite:///{tmp_path / 'r4.db'}")
    monkeypatch.delenv("YOWAYOWA_API_TOKEN", raising=False)
    from yowayowa.config import get_settings

    get_settings.cache_clear()


def test_valuation_is_served_by_the_fundamentals_handler(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """R4 §2: two ``/v1/valuation/{symbol}`` handlers exist; only one answers.

    ``app.py`` includes ``fundamentals_router`` before ``router``, so requests
    resolve to ``fundamentals_routes.valuation_anywhere``. Measured here by
    disabling that handler's providers and making the SEC-only handler in
    ``routes.py`` (which must stay untouched in this card) unusable: a 200 can
    only come from the fundamentals handler.
    """

    _api_fixtures(tmp_path, monkeypatch)

    from starlette.testclient import TestClient

    from yowayowa.api import fundamentals_routes, routes
    from yowayowa.api.app import app

    def dead_sec(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("routes.py SEC-only valuation handler must not answer")

    monkeypatch.setattr(routes, "_sec", dead_sec)
    monkeypatch.setattr(fundamentals_routes, "_fundamentals", lambda symbol: _facts(symbol))
    monkeypatch.setattr(fundamentals_routes, "yahoo_market_provider", lambda: _StubMarketProvider())

    try:
        with TestClient(app) as client:
            response = client.get("/v1/valuation/7203.T")
            assert response.status_code == 200
            payload = response.json()
            assert payload["currency"] == "JPY"
            assert payload["price"] == 3103.0
    finally:
        from yowayowa.config import get_settings

        get_settings.cache_clear()


def test_valuation_api_exposes_quote_currency(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _api_fixtures(tmp_path, monkeypatch)

    from starlette.testclient import TestClient

    from yowayowa.api import fundamentals_routes
    from yowayowa.api.app import app

    monkeypatch.setattr(fundamentals_routes, "_fundamentals", lambda symbol: _facts(symbol))
    monkeypatch.setattr(fundamentals_routes, "yahoo_market_provider", lambda: _StubMarketProvider())

    try:
        with TestClient(app) as client:
            response = client.get("/v1/valuation/7203.T")
            assert response.status_code == 200
            payload = response.json()
            assert payload["currency"] == "JPY"
            assert payload["currency_source"] == CURRENCY_SOURCE_FAST_INFO
            assert payload["currency_reason"] is None
            assert payload["company_name"] == "Toyota Motor Corporation"
            # Existing response keys are untouched.
            for key in ("symbol", "price", "shares_diluted", "market_cap", "metrics"):
                assert key in payload
    finally:
        from yowayowa.config import get_settings

        get_settings.cache_clear()


def test_quotes_api_exposes_currency_fields(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _api_fixtures(tmp_path, monkeypatch)

    from starlette.testclient import TestClient

    from yowayowa.api import routes
    from yowayowa.api.app import app

    class StubProvider:
        def quotes(self, symbols: list[str]) -> MarketQuoteBatch:
            return MarketQuoteBatch(
                quotes={
                    symbol: MarketQuote(
                        symbol=symbol,
                        price=3103.0,
                        currency="JPY",
                        currency_source=CURRENCY_SOURCE_FAST_INFO,
                        as_of=datetime(2026, 10, 23, tzinfo=UTC),
                    )
                    for symbol in symbols
                },
                provenance=Provenance(
                    provider="stub",
                    source="Stub",
                    license_class=LicenseClass.PERSONAL_ONLY,
                    retrieved_at="2026-10-23T00:00:00Z",
                ),
            )

    monkeypatch.setattr(routes, "yahoo_market_provider", lambda: StubProvider())

    try:
        with TestClient(app) as client:
            response = client.get("/v1/markets/quotes", params={"symbols": "7203.T"})
            assert response.status_code == 200
            quote = response.json()["quotes"]["7203.T"]
            assert quote["currency"] == "JPY"
            assert quote["currency_source"] == CURRENCY_SOURCE_FAST_INFO
            assert quote["currency_reason"] is None
    finally:
        from yowayowa.config import get_settings

        get_settings.cache_clear()


class _StubMarketProvider:
    def quotes(self, symbols: list[str]) -> MarketQuoteBatch:
        return _batch(symbols[0])


# ------------------------------------------- fundamentals metadata root cause


def test_yahoo_fundamentals_accepts_mapping_metadata(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """yfinance 1.7 returns ``HistoryMetadata`` (a Mapping, not a dict).

    The old ``isinstance(value, dict)`` check threw that payload away, which is
    why ``company_name`` fell back to the symbol and the statement currency fell
    back to a suffix guess. Feed a non-dict Mapping and require both from the
    provider.
    """

    from yowayowa.providers import yahoo_fundamentals

    class MetadataTicker:
        def __init__(self, symbol: str) -> None:
            self.symbol = symbol

        def get_history_metadata(self) -> _FastInfo:
            return _FastInfo(
                {"longName": "Toyota Motor Corporation", "currency": "JPY", "symbol": "7203.T"}
            )

        def get_income_stmt(self, **_: object) -> pd.DataFrame:
            return pd.DataFrame(
                {pd.Timestamp("2026-03-31"): [1000.0]},
                index=["Total Revenue"],
            )

        def get_balance_sheet(self, **_: object) -> pd.DataFrame:
            return pd.DataFrame({pd.Timestamp("2026-03-31"): [2000.0]}, index=["Total Assets"])

        def get_cash_flow(self, **_: object) -> pd.DataFrame:
            return pd.DataFrame(
                {pd.Timestamp("2026-03-31"): [120.0]},
                index=["Operating Cash Flow"],
            )

    monkeypatch.setattr(yahoo_fundamentals.yf, "Ticker", MetadataTicker)
    provider = yahoo_fundamentals.YahooFundamentalsProvider(
        Settings(database_url="sqlite:///:memory:")
    )

    metadata = yahoo_fundamentals.YahooFundamentalsProvider._safe_history_metadata(
        MetadataTicker("7203.T")
    )
    facts = provider.company_facts("7203.T")

    assert metadata["longName"] == "Toyota Motor Corporation"
    assert metadata["currency"] == "JPY"
    assert facts.company_name == "Toyota Motor Corporation"
    assert facts.metrics["revenue"].points[-1].unit == "JPY"
