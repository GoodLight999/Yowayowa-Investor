from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from yowayowa.domain import LicenseClass, MarketHistory, MarketOverview, MarketQuoteBatch
from yowayowa.services.licensing import public_api_allowed


class ProviderPolicyError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ProviderDescriptor:
    name: str
    license_class: LicenseClass
    redistributable: bool
    description: str


class MarketDataProvider(Protocol):
    descriptor: ProviderDescriptor

    def history(
        self, symbol: str, period: str, interval: str, indicators: list[str]
    ) -> MarketHistory: ...

    def quotes(self, symbols: list[str]) -> MarketQuoteBatch: ...

    def overview(self) -> MarketOverview: ...


def enforce_source_policy(provider: str, *, mode: str) -> None:
    if mode == "public" and not public_api_allowed(provider):
        raise ProviderPolicyError(
            f"Source {provider!r} is not approved for commercial public API redistribution"
        )


def enforce_provider_policy(
    descriptor: ProviderDescriptor, *, mode: str, allow_personal_in_public: bool = False
) -> None:
    del allow_personal_in_public
    if mode != "public":
        return
    if not descriptor.redistributable or descriptor.license_class == LicenseClass.PERSONAL_ONLY:
        raise ProviderPolicyError(
            f"Provider {descriptor.name!r} is not licensed for public redistribution"
        )
    enforce_source_policy(descriptor.name, mode=mode)
