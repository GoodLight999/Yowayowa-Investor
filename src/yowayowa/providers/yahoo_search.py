from __future__ import annotations

from typing import Any

import yfinance as yf

from yowayowa.config import Settings
from yowayowa.domain import Instrument, LicenseClass
from yowayowa.providers.base import ProviderDescriptor, enforce_provider_policy


class YahooSearchProvider:
    descriptor = ProviderDescriptor(
        name="yahoo-search",
        license_class=LicenseClass.PERSONAL_ONLY,
        redistributable=False,
        description="Yahoo Finance search via yfinance; personal use only.",
    )

    def __init__(self, settings: Settings) -> None:
        enforce_provider_policy(
            self.descriptor,
            mode=settings.mode,
            allow_personal_in_public=settings.allow_personal_provider_in_public,
        )
        self.settings = settings

    def search(self, query: str, limit: int = 20) -> list[Instrument]:
        raw = yf.Search(
            query,
            max_results=limit,
            news_count=0,
            lists_count=0,
            include_cb=False,
            include_nav_links=False,
            include_research=False,
            include_cultural_assets=False,
            timeout=self.settings.request_timeout_seconds,
        ).quotes
        results: list[Instrument] = []
        for item in raw[:limit]:
            if not isinstance(item, dict):
                continue
            instrument = self._instrument(item)
            if instrument is not None:
                results.append(instrument)
        return results

    @staticmethod
    def _instrument(item: dict[str, Any]) -> Instrument | None:
        symbol = str(item.get("symbol") or "").strip().upper()
        if not symbol:
            return None
        name = str(
            item.get("longname")
            or item.get("longName")
            or item.get("shortname")
            or item.get("shortName")
            or symbol
        ).strip()
        quote_type = str(item.get("quoteType") or item.get("typeDisp") or "equity").lower()
        exchange = item.get("exchange") or item.get("exchDisp") or item.get("exchangeDisp")
        currency = item.get("currency")
        return Instrument(
            symbol=symbol,
            name=name,
            exchange=str(exchange) if exchange else None,
            instrument_type=quote_type,
            currency=str(currency).upper() if currency else None,
        )
