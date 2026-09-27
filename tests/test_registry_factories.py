from __future__ import annotations

from collections.abc import Iterator

import pytest

import yowayowa.providers.registry as registry
from yowayowa.config import Settings
from yowayowa.providers.bea import BeaClient
from yowayowa.providers.binance import BinanceKlinesProvider
from yowayowa.providers.bls import BlsClient
from yowayowa.providers.coingecko import CoinGeckoOhlcProvider
from yowayowa.providers.edinet import EdinetClient
from yowayowa.providers.frankfurter import FrankfurterFxProvider
from yowayowa.providers.fred import FredClient
from yowayowa.providers.registry import (
    ListingAwareSecClient,
    bea_client,
    binance_klines_provider,
    bls_client,
    coingecko_ohlc_provider,
    edinet_client,
    frankfurter_fx_provider,
    fred_client,
    sec_client,
    treasury_yield_curve_provider,
    yahoo_deep_research_provider,
    yahoo_market_provider,
    yahoo_risk_provider,
    yahoo_screener_provider,
    yahoo_sector_provider,
    yahoo_tracked_calendar_provider,
)
from yowayowa.providers.sec import SecClient
from yowayowa.providers.treasury import TreasuryYieldCurveProvider
from yowayowa.providers.yahoo import YahooMarketProvider
from yowayowa.providers.yahoo_deep_research import YahooDeepResearchProvider
from yowayowa.providers.yahoo_risk import YahooRiskProvider
from yowayowa.providers.yahoo_screener import YahooScreenerProvider
from yowayowa.providers.yahoo_sectors import YahooSectorProvider
from yowayowa.providers.yahoo_tracked_calendar import YahooTrackedCalendarProvider


@pytest.fixture(autouse=True)
def _clear_registry_caches() -> Iterator[None]:
    sec_client.cache_clear()
    yield
    sec_client.cache_clear()


def test_all_factories_return_real_instances(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(database_url="sqlite:///:memory:", mode="personal")
    monkeypatch.setattr(registry, "get_settings", lambda: settings)

    assert isinstance(sec_client(), ListingAwareSecClient)
    assert isinstance(coingecko_ohlc_provider(), CoinGeckoOhlcProvider)
    assert isinstance(binance_klines_provider(), BinanceKlinesProvider)
    assert isinstance(yahoo_market_provider(), YahooMarketProvider)
    assert isinstance(frankfurter_fx_provider(), FrankfurterFxProvider)
    assert isinstance(yahoo_deep_research_provider(), YahooDeepResearchProvider)
    assert isinstance(yahoo_risk_provider(), YahooRiskProvider)
    assert isinstance(yahoo_screener_provider(), YahooScreenerProvider)
    assert isinstance(yahoo_sector_provider(), YahooSectorProvider)
    assert isinstance(yahoo_tracked_calendar_provider(), YahooTrackedCalendarProvider)
    assert isinstance(treasury_yield_curve_provider(), TreasuryYieldCurveProvider)
    assert isinstance(bls_client(), BlsClient)
    assert isinstance(bea_client(), BeaClient)
    assert isinstance(fred_client(), FredClient)
    assert isinstance(edinet_client(), EdinetClient)


def test_factories_cache_singletons(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(database_url="sqlite:///:memory:", mode="personal")
    monkeypatch.setattr(registry, "get_settings", lambda: settings)

    assert sec_client() is sec_client()
    assert yahoo_market_provider() is yahoo_market_provider()
    assert fred_client() is fred_client()


def test_listing_aware_client_uses_sec_for_us_tickers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(database_url="sqlite:///:memory:", mode="personal")
    client = ListingAwareSecClient(settings)

    calls: list[str] = []

    def fake_sec_facts(self: SecClient, symbol: str) -> str:
        calls.append(symbol)
        return "sec-fundamentals"

    monkeypatch.setattr(SecClient, "company_facts", fake_sec_facts)

    assert client.company_facts("AAPL") == "sec-fundamentals"
    assert calls == ["AAPL"]


def test_listing_aware_client_routes_international_listings_to_yahoo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(database_url="sqlite:///:memory:", mode="personal")
    client = ListingAwareSecClient(settings)
    assert client._international is not None

    routed: list[str] = []

    class FakeYahoo:
        def company_facts(self, symbol: str) -> str:
            routed.append(symbol)
            return "yahoo-fundamentals"

    monkeypatch.setattr(client, "_international", FakeYahoo())

    assert client.company_facts("7203.T") == "yahoo-fundamentals"
    assert routed == ["7203.T"]


def test_listing_aware_client_skips_yahoo_in_public_mode() -> None:
    settings = Settings(database_url="sqlite:///:memory:", mode="public", api_token="test-token")

    client = ListingAwareSecClient(settings)

    assert client._international is None
