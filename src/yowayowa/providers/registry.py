from __future__ import annotations

from functools import lru_cache

from yowayowa.config import Settings, get_settings
from yowayowa.domain import Fundamentals
from yowayowa.providers.bea import BeaClient
from yowayowa.providers.bls import BlsClient
from yowayowa.providers.edinet import EdinetClient
from yowayowa.providers.fred import FredClient
from yowayowa.providers.fundamentals import is_non_us_exchange_listing
from yowayowa.providers.sec import SecClient
from yowayowa.providers.treasury import TreasuryYieldCurveProvider
from yowayowa.providers.yahoo import YahooMarketProvider
from yowayowa.providers.yahoo_deep_research import YahooDeepResearchProvider
from yowayowa.providers.yahoo_fundamentals import YahooFundamentalsProvider
from yowayowa.providers.yahoo_risk import YahooRiskProvider
from yowayowa.providers.yahoo_screener import YahooScreenerProvider
from yowayowa.providers.yahoo_sectors import YahooSectorProvider
from yowayowa.providers.yahoo_tracked_calendar import YahooTrackedCalendarProvider


class ListingAwareSecClient(SecClient):
    """Keep SEC capabilities while routing known international listings before I/O.

    This is intentionally not an error fallback. A US/SEC request that fails stays a
    SEC failure; only symbols with an explicit non-US exchange suffix use Yahoo, and
    only in personal mode.
    """

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._international = (
            YahooFundamentalsProvider(settings) if settings.mode == "personal" else None
        )

    def company_facts(self, symbol: str) -> Fundamentals:
        if self._international is not None and is_non_us_exchange_listing(symbol):
            return self._international.company_facts(symbol)
        return super().company_facts(symbol)


@lru_cache(maxsize=1)
def sec_client() -> SecClient:
    return ListingAwareSecClient(get_settings())


@lru_cache(maxsize=1)
def fundamentals_provider() -> SecClient:
    return sec_client()


@lru_cache(maxsize=1)
def yahoo_market_provider() -> YahooMarketProvider:
    return YahooMarketProvider(get_settings())


@lru_cache(maxsize=1)
def yahoo_deep_research_provider() -> YahooDeepResearchProvider:
    return YahooDeepResearchProvider(get_settings())


@lru_cache(maxsize=1)
def yahoo_risk_provider() -> YahooRiskProvider:
    return YahooRiskProvider(get_settings())


@lru_cache(maxsize=1)
def yahoo_screener_provider() -> YahooScreenerProvider:
    return YahooScreenerProvider(get_settings())


@lru_cache(maxsize=1)
def yahoo_sector_provider() -> YahooSectorProvider:
    return YahooSectorProvider(get_settings())


@lru_cache(maxsize=1)
def yahoo_tracked_calendar_provider() -> YahooTrackedCalendarProvider:
    return YahooTrackedCalendarProvider(get_settings())


@lru_cache(maxsize=1)
def treasury_yield_curve_provider() -> TreasuryYieldCurveProvider:
    return TreasuryYieldCurveProvider(get_settings())


@lru_cache(maxsize=1)
def bls_client() -> BlsClient:
    return BlsClient(get_settings())


@lru_cache(maxsize=1)
def bea_client() -> BeaClient:
    return BeaClient(get_settings())


@lru_cache(maxsize=1)
def fred_client() -> FredClient:
    return FredClient(get_settings())


@lru_cache(maxsize=1)
def edinet_client() -> EdinetClient:
    return EdinetClient(get_settings())
