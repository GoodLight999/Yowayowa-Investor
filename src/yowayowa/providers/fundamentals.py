from __future__ import annotations

from typing import Protocol

from yowayowa.domain import Fundamentals


class FundamentalsSource(Protocol):
    def company_facts(self, symbol: str) -> Fundamentals: ...


class SecFirstFundamentalsProvider:
    """Prefer official SEC facts and fall back only for non-SEC issuers.

    The fallback is deliberately triggered only by LookupError. Network/provider
    failures from SEC are not silently replaced with a different data source.
    """

    def __init__(self, primary: FundamentalsSource, fallback: FundamentalsSource) -> None:
        self.primary = primary
        self.fallback = fallback

    def company_facts(self, symbol: str) -> Fundamentals:
        try:
            return self.primary.company_facts(symbol)
        except LookupError:
            return self.fallback.company_facts(symbol)
