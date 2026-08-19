from __future__ import annotations

from functools import lru_cache

from yowayowa.config import Settings, get_settings
from yowayowa.domain import Fundamentals
from yowayowa.providers.bea import BeaClient
from yowayowa.providers.bls import BlsClient
from yowayowa.providers.edinet import EdinetClient
from yowayowa.providers.fred import FredClient
from yowayowa.providers.sec import SecClient
from yowayowa.providers.treasury import TreasuryYieldCurveProvider
from yowayowa.providers.yahoo import YahooMarketProvider
from yowayowa.providers.yahoo_deep_research import YahooDeepResearchProvider
from yowayowa.providers.yahoo_fundamentals import YahooFundamentalsProvider
from yowayowa.providers.yahoo_risk import YahooRiskProvider
from yowayowa.providers.yahoo_screener import YahooScreenerProvider
from yowayowa.providers.yahoo_sectors import YahooSectorProvider
from yowayowa.providers.yahoo_tracked_calendar import YahooTrackedCalendarProvider


class SecFirstClient(SecClient):
    """Use official SEC first; Yahoo fallback exists only in personal mode."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._fallback = (
            YahooFundamentalsProvider(settings) if settings.mode == "personal" else None
        )

    def company_facts(self, symbol: str) -> Fundamentals:
        try:
            return super().company_facts(symbol)
        except LookupError:
            if self._fallback is None:
                raise
            return self._fallback.company_facts(symbol)


@lru_cache(maxsize=1)
def sec_client() -> SecClient:
    return SecFirstClient(get_settings())


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
