from __future__ import annotations

from typing import Protocol

from yowayowa.domain import Fundamentals


class FundamentalsSource(Protocol):
    def company_facts(self, symbol: str) -> Fundamentals: ...


# Yahoo-style exchange suffixes used for listings outside the SEC issuer universe.
# This is deliberately explicit: provider failure must never decide data provenance.
NON_US_EXCHANGE_SUFFIXES = (
    ".T",
    ".HK",
    ".L",
    ".TO",
    ".V",
    ".AX",
    ".NZ",
    ".SI",
    ".KS",
    ".KQ",
    ".TW",
    ".TWO",
    ".NS",
    ".BO",
    ".SA",
    ".MX",
    ".DE",
    ".F",
    ".PA",
    ".AS",
    ".BR",
    ".MI",
    ".MC",
    ".SW",
    ".ST",
    ".OL",
    ".CO",
    ".HE",
    ".IR",
    ".VI",
    ".PR",
    ".WA",
    ".AT",
    ".JK",
    ".BK",
)


def is_non_us_exchange_listing(symbol: str) -> bool:
    normalized = symbol.strip().upper()
    return normalized.endswith(NON_US_EXCHANGE_SUFFIXES)


class ListingAwareFundamentalsProvider:
    """Choose financial-statement provenance from the listing, never from an outage.

    SEC remains authoritative for US issuers. Personal-mode international listings
    can use a separately supplied personal-only source. A failed SEC request is not
    a signal to switch providers.
    """

    def __init__(
        self,
        sec: FundamentalsSource,
        international: FundamentalsSource | None = None,
    ) -> None:
        self.sec = sec
        self.international = international

    def company_facts(self, symbol: str) -> Fundamentals:
        if self.international is not None and is_non_us_exchange_listing(symbol):
            return self.international.company_facts(symbol)
        return self.sec.company_facts(symbol)


class SecFirstFundamentalsProvider:
    """Legacy compatibility wrapper; do not use for new routing.

    Prefer :class:`ListingAwareFundamentalsProvider`, which selects provenance from
    the symbol before any network request. This class remains for compatibility with
    callers that explicitly need the historic LookupError fallback contract.
    """

    def __init__(self, primary: FundamentalsSource, fallback: FundamentalsSource) -> None:
        self.primary = primary
        self.fallback = fallback

    def company_facts(self, symbol: str) -> Fundamentals:
        try:
            return self.primary.company_facts(symbol)
        except LookupError:
            return self.fallback.company_facts(symbol)
