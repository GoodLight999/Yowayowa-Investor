from datetime import date

import pandas as pd
import pytest

from yowayowa.config import Settings
from yowayowa.domain import Fundamentals, LicenseClass, Provenance
from yowayowa.providers import yahoo_fundamentals
from yowayowa.providers.fundamentals import (
    ListingAwareFundamentalsProvider,
    SecFirstFundamentalsProvider,
    is_non_us_exchange_listing,
)
from yowayowa.providers.yahoo_fundamentals import YahooFundamentalsProvider


class FakeTicker:
    def __init__(self, symbol: str) -> None:
        self.symbol = symbol

    def get_income_stmt(self, **_: object) -> pd.DataFrame:
        return pd.DataFrame(
            {
                pd.Timestamp("2025-12-31"): [1000, 400, 100, 50, 2.5, 20],
                pd.Timestamp("2024-12-31"): [800, 300, 80, 40, 2.0, 20],
            },
            index=[
                "Total Revenue",
                "Gross Profit",
                "Operating Income",
                "Net Income",
                "Diluted EPS",
                "Diluted Average Shares",
            ],
        )

    def get_balance_sheet(self, **_: object) -> pd.DataFrame:
        return pd.DataFrame(
            {
                pd.Timestamp("2025-12-31"): [2000, 900, 1100, 250],
                pd.Timestamp("2024-12-31"): [1800, 850, 950, 200],
            },
            index=[
                "Total Assets",
                "Total Liabilities Net Minority Interest",
                "Stockholders Equity",
                "Cash Cash Equivalents And Short Term Investments",
            ],
        )

    def get_cash_flow(self, **_: object) -> pd.DataFrame:
        return pd.DataFrame(
            {
                pd.Timestamp("2025-12-31"): [120, -30],
                pd.Timestamp("2024-12-31"): [100, -20],
            },
            index=["Operating Cash Flow", "Capital Expenditure"],
        )

    def get_history_metadata(self) -> dict[str, object]:
        return {
            "longName": "Example Japan Corp",
            "currency": "JPY",
            "symbol": self.symbol,
        }

    def get_info(self) -> dict[str, object]:
        raise AssertionError("company_facts must not depend on quoteSummary get_info")


def test_yahoo_annual_fundamentals_are_normalized(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(yahoo_fundamentals.yf, "Ticker", FakeTicker)
    provider = YahooFundamentalsProvider(Settings(database_url="sqlite:///:memory:"))

    result = provider.company_facts("7203.T")

    assert result.symbol == "7203.T"
    assert result.company_name == "Example Japan Corp"
    assert result.cik == ""
    assert result.provenance.license_class == LicenseClass.PERSONAL_ONLY
    assert result.provenance.as_of == date(2025, 12, 31)
    assert result.metrics["revenue"].points[-1].value == 1000
    assert result.metrics["revenue"].points[-1].unit == "JPY"
    assert result.metrics["capex"].points[-1].value == 30
    assert result.metrics["shares_diluted"].points[-1].unit == "shares"


def test_yahoo_metadata_failure_does_not_discard_japanese_statements(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    class MetadataFailureTicker(FakeTicker):
        def get_history_metadata(self) -> dict[str, object]:
            raise RuntimeError("metadata unavailable")

    monkeypatch.setattr(yahoo_fundamentals.yf, "Ticker", MetadataFailureTicker)
    provider = YahooFundamentalsProvider(Settings(database_url="sqlite:///:memory:"))

    result = provider.company_facts("7203.T")

    assert result.company_name == "7203.T"
    assert result.metrics["revenue"].points[-1].unit == "JPY"
    assert result.metrics["eps_diluted"].points[-1].unit == "JPY/share"


class _EpsTicker(FakeTicker):
    """Frequency-aware FakeTicker variant whose income rows are per-test."""

    def get_income_stmt(
        self,
        *,
        freq: str | None = None,
        frequency: str | None = None,
        **_: object,
    ) -> pd.DataFrame:
        if (freq or frequency) == "quarterly":
            return pd.DataFrame()
        return self._income()

    def _income(self) -> pd.DataFrame:
        raise NotImplementedError


def _income_frame(eps: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            pd.Timestamp("2025-12-31"): [1000, 400, eps[0], 20],
            pd.Timestamp("2024-12-31"): [800, 40, eps[1], 20],
        },
        index=[
            "Total Revenue",
            "Net Income",
            "Diluted EPS",
            "Diluted Average Shares",
        ],
    )


def test_eps_diluted_derived_when_row_missing(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    class NoEpsRowTicker(_EpsTicker):
        def _income(self) -> pd.DataFrame:
            return pd.DataFrame(
                {
                    pd.Timestamp("2025-12-31"): [1000, 400, 20],
                    pd.Timestamp("2024-12-31"): [800, 40, 20],
                },
                index=["Total Revenue", "Net Income", "Diluted Average Shares"],
            )

    monkeypatch.setattr(yahoo_fundamentals.yf, "Ticker", NoEpsRowTicker)
    provider = YahooFundamentalsProvider(Settings(database_url="sqlite:///:memory:"))

    result = provider.company_facts("7203.T")

    points = result.metrics["eps_diluted"].points
    assert len(points) == 2
    assert float(points[-1].value) == pytest.approx(20.0, rel=0.01)
    assert float(points[-2].value) == pytest.approx(2.0, rel=0.01)
    assert points[-1].unit == "JPY/share"
    assert points[-1].period_end == date(2025, 12, 31)
    assert points[-1].fiscal_year == 2025


def test_eps_diluted_zero_is_replaced_by_derivation(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    class ZeroEpsTicker(_EpsTicker):
        def _income(self) -> pd.DataFrame:
            return _income_frame([0.0, 0.0])

    monkeypatch.setattr(yahoo_fundamentals.yf, "Ticker", ZeroEpsTicker)
    provider = YahooFundamentalsProvider(Settings(database_url="sqlite:///:memory:"))

    result = provider.company_facts("7203.T")

    points = result.metrics["eps_diluted"].points
    assert len(points) == 2
    assert float(points[-1].value) == pytest.approx(20.0, rel=0.01)
    assert float(points[-2].value) == pytest.approx(2.0, rel=0.01)
    assert all(point.value != 0 for point in points)


def test_eps_diluted_yahoo_value_not_overwritten(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    class YahooEpsTicker(_EpsTicker):
        def _income(self) -> pd.DataFrame:
            return _income_frame([2.5, 2.0])

    monkeypatch.setattr(yahoo_fundamentals.yf, "Ticker", YahooEpsTicker)
    provider = YahooFundamentalsProvider(Settings(database_url="sqlite:///:memory:"))

    result = provider.company_facts("7203.T")

    points = result.metrics["eps_diluted"].points
    assert len(points) == 2
    assert float(points[-1].value) == pytest.approx(2.5, rel=0.01)
    assert float(points[-2].value) == pytest.approx(2.0, rel=0.01)


class PrimaryMissing:
    def company_facts(self, symbol: str) -> Fundamentals:
        raise LookupError(symbol)


class PrimaryBroken:
    def company_facts(self, symbol: str) -> Fundamentals:
        raise RuntimeError(symbol)


class Fallback:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def company_facts(self, symbol: str) -> Fundamentals:
        self.calls.append(symbol)
        return Fundamentals(
            symbol=symbol,
            cik="",
            company_name="Fallback",
            metrics={},
            provenance=Provenance(
                provider="fallback",
                source="fixture",
                license_class=LicenseClass.PERSONAL_ONLY,
                retrieved_at="2026-08-13T00:00:00Z",
            ),
        )


def test_listing_aware_provider_routes_before_network_failure() -> None:
    international = Fallback()
    provider = ListingAwareFundamentalsProvider(PrimaryBroken(), international)

    result = provider.company_facts("7203.T")

    assert result.company_name == "Fallback"
    assert international.calls == ["7203.T"]


def test_listing_aware_provider_does_not_treat_us_class_share_as_international() -> None:
    international = Fallback()
    provider = ListingAwareFundamentalsProvider(PrimaryMissing(), international)

    with pytest.raises(LookupError):
        provider.company_facts("BRK.B")

    assert international.calls == []
    assert is_non_us_exchange_listing("7203.T") is True
    assert is_non_us_exchange_listing("BRK.B") is False


def test_sec_first_provider_falls_back_only_when_issuer_is_not_in_sec() -> None:
    fallback = Fallback()
    provider = SecFirstFundamentalsProvider(PrimaryMissing(), fallback)
    result = provider.company_facts("7203.T")
    assert result.company_name == "Fallback"
    assert fallback.calls == ["7203.T"]


def test_sec_first_provider_does_not_hide_primary_outage() -> None:
    fallback = Fallback()
    provider = SecFirstFundamentalsProvider(PrimaryBroken(), fallback)
    with pytest.raises(RuntimeError):
        provider.company_facts("RKLB")
    assert fallback.calls == []
