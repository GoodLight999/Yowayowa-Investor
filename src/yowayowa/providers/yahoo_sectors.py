from __future__ import annotations

from datetime import UTC, datetime

import yfinance as yf
from cachetools import TTLCache

from yowayowa.config import Settings
from yowayowa.domain import LicenseClass, MarketOverview, Provenance
from yowayowa.providers.base import ProviderDescriptor, enforce_provider_policy
from yowayowa.providers.yahoo import MarketInstrumentSpec, YahooMarketProvider

SECTOR_UNIVERSE: tuple[MarketInstrumentSpec, ...] = (
    MarketInstrumentSpec("XLK", "Technology", "US sector", "USD"),
    MarketInstrumentSpec("XLC", "Communication Services", "US sector", "USD"),
    MarketInstrumentSpec("XLY", "Consumer Discretionary", "US sector", "USD"),
    MarketInstrumentSpec("XLP", "Consumer Staples", "US sector", "USD"),
    MarketInstrumentSpec("XLE", "Energy", "US sector", "USD"),
    MarketInstrumentSpec("XLF", "Financials", "US sector", "USD"),
    MarketInstrumentSpec("XLV", "Health Care", "US sector", "USD"),
    MarketInstrumentSpec("XLI", "Industrials", "US sector", "USD"),
    MarketInstrumentSpec("XLB", "Materials", "US sector", "USD"),
    MarketInstrumentSpec("XLRE", "Real Estate", "US sector", "USD"),
    MarketInstrumentSpec("XLU", "Utilities", "US sector", "USD"),
)


class YahooSectorProvider:
    descriptor = ProviderDescriptor(
        name="yahoo-sectors",
        license_class=LicenseClass.PERSONAL_ONLY,
        redistributable=False,
        description="US sector ETF relative strength via Yahoo Finance / yfinance.",
    )

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        enforce_provider_policy(
            self.descriptor,
            mode=settings.mode,
            allow_personal_in_public=settings.allow_personal_provider_in_public,
        )
        ttl = max(30, settings.cache_ttl_seconds)
        self._cache: TTLCache[str, MarketOverview] = TTLCache(maxsize=2, ttl=ttl)

    def overview(self) -> MarketOverview:
        cached = self._cache.get("sectors")
        if cached is not None:
            return cached
        symbols = [item.symbol for item in SECTOR_UNIVERSE]
        frame = yf.download(
            tickers=symbols,
            period="1y",
            interval="1d",
            group_by="ticker",
            auto_adjust=False,
            threads=True,
            progress=False,
            ignore_tz=True,
            multi_level_index=True,
        )
        if frame is None or frame.empty:
            raise LookupError("No sector history returned")
        result = YahooMarketProvider._overview_from_frame(frame, SECTOR_UNIVERSE)
        now = datetime.now(UTC)
        result = result.model_copy(
            update={
                "provenance": Provenance(
                    provider="yahoo/yfinance",
                    source="Select Sector SPDR ETF market history",
                    source_url="https://finance.yahoo.com/markets/etfs/",
                    license_class=LicenseClass.PERSONAL_ONLY,
                    retrieved_at=now,
                    as_of=result.provenance.as_of,
                    notes=[
                        "Equal-area sector relative-strength view using the 11 Select "
                        "Sector SPDR ETFs.",
                        "Tile area is intentionally not presented as sector market-cap weight.",
                        "Personal-use provider. Public redistribution is blocked by "
                        "provider policy.",
                    ],
                )
            }
        )
        self._cache["sectors"] = result
        return result
