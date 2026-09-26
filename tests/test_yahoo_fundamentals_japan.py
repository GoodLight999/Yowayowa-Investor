from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd

from yowayowa.config import Settings
from yowayowa.domain import (
    Fundamentals,
    LicenseClass,
    MetricPoint,
    MetricSeries,
    Provenance,
)
from yowayowa.providers import yahoo_fundamentals
from yowayowa.providers.yahoo_fundamentals import YahooFundamentalsProvider
from yowayowa.services.screening import annual_points


class JapaneseTickerFixture:
    def __init__(self, symbol: str) -> None:
        self.symbol = symbol

    @staticmethod
    def _annual_income() -> pd.DataFrame:
        return pd.DataFrame(
            {
                pd.Timestamp("2026-03-31"): [48_000, 9_000, 4_800, 3_600, 320, 1_100],
                pd.Timestamp("2025-03-31"): [45_000, 8_200, 4_100, 3_100, 275, 1_100],
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

    @staticmethod
    def _quarter_income() -> pd.DataFrame:
        return pd.DataFrame(
            {pd.Timestamp("2026-06-30"): [12_400, 2_350, 1_180, 850, 77, 1_100]},
            index=[
                "Total Revenue",
                "Gross Profit",
                "Operating Income",
                "Net Income",
                "Diluted EPS",
                "Diluted Average Shares",
            ],
        )

    @staticmethod
    def _annual_balance() -> pd.DataFrame:
        return pd.DataFrame(
            {
                pd.Timestamp("2026-03-31"): [82_000, 34_000, 18_000, 7_500, 45_000, 37_000],
                pd.Timestamp("2025-03-31"): [78_000, 32_000, 17_000, 6_900, 43_000, 35_000],
            },
            index=[
                "Total Assets",
                "Current Assets",
                "Current Liabilities",
                "Cash And Cash Equivalents",
                "Total Liabilities Net Minority Interest",
                "Stockholders Equity",
            ],
        )

    @staticmethod
    def _quarter_balance() -> pd.DataFrame:
        return pd.DataFrame(
            {pd.Timestamp("2026-06-30"): [84_000, 35_500, 18_300, 8_100, 46_000, 38_000]},
            index=[
                "Total Assets",
                "Current Assets",
                "Current Liabilities",
                "Cash And Cash Equivalents",
                "Total Liabilities Net Minority Interest",
                "Stockholders Equity",
            ],
        )

    @staticmethod
    def _annual_cash() -> pd.DataFrame:
        return pd.DataFrame(
            {
                pd.Timestamp("2026-03-31"): [6_000, -2_100],
                pd.Timestamp("2025-03-31"): [5_400, -1_900],
            },
            index=["Operating Cash Flow", "Capital Expenditure"],
        )

    @staticmethod
    def _quarter_cash() -> pd.DataFrame:
        return pd.DataFrame(
            {pd.Timestamp("2026-06-30"): [1_500, -520]},
            index=["Operating Cash Flow", "Capital Expenditure"],
        )

    def get_income_stmt(
        self,
        *,
        freq: str | None = None,
        frequency: str | None = None,
        **_: object,
    ) -> pd.DataFrame:
        if (freq or frequency) == "quarterly":
            return self._quarter_income()
        return self._annual_income()

    def get_balance_sheet(
        self,
        *,
        freq: str | None = None,
        frequency: str | None = None,
        **_: object,
    ) -> pd.DataFrame:
        if (freq or frequency) == "quarterly":
            return self._quarter_balance()
        return self._annual_balance()

    def get_cash_flow(
        self,
        *,
        freq: str | None = None,
        frequency: str | None = None,
        **_: object,
    ) -> pd.DataFrame:
        if (freq or frequency) == "quarterly":
            return self._quarter_cash()
        return self._annual_cash()

    def get_history_metadata(self) -> dict[str, object]:
        return {
            "longName": "トヨタ自動車株式会社",
            "currency": "JPY",
            "symbol": self.symbol,
        }


def test_japanese_fundamentals_include_annual_quarterly_and_liquidity_rows(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(yahoo_fundamentals.yf, "Ticker", JapaneseTickerFixture)
    provider = YahooFundamentalsProvider(Settings(database_url="sqlite:///:memory:"))

    result = provider.company_facts("7203.T")

    assert result.symbol == "7203.T"
    assert result.company_name == "トヨタ自動車株式会社"
    assert result.provenance.license_class == LicenseClass.PERSONAL_ONLY
    assert result.provenance.as_of == date(2026, 6, 30)
    assert result.metrics["current_assets"].points[-1].value == 35_500
    assert result.metrics["current_liabilities"].points[-1].value == 18_300
    assert {point.fiscal_period for point in result.metrics["revenue"].points} == {"FY", "Q"}
    assert result.metrics["capex"].points[-1].value == 520
    assert result.metrics["eps_diluted"].points[-1].unit == "JPY/share"
    assert any("approximate" in note for note in result.provenance.notes)


def _japanese_fundamentals() -> Fundamentals:
    provider = YahooFundamentalsProvider(Settings(database_url="sqlite:///:memory:"))
    return provider.company_facts("7203.T")


def test_fiscal_year_derived_for_all_points(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(yahoo_fundamentals.yf, "Ticker", JapaneseTickerFixture)

    result = _japanese_fundamentals()

    for series in result.metrics.values():
        for point in series.points:
            assert point.fiscal_year is not None

    revenue = result.metrics["revenue"].points
    assert {point.fiscal_period for point in revenue} == {"FY", "Q"}
    fy_2026 = next(
        point
        for point in revenue
        if point.period_end == date(2026, 3, 31) and point.fiscal_period == "FY"
    )
    fy_2025 = next(
        point
        for point in revenue
        if point.period_end == date(2025, 3, 31) and point.fiscal_period == "FY"
    )
    q_2026 = next(
        point
        for point in revenue
        if point.period_end == date(2026, 6, 30) and point.fiscal_period == "Q"
    )
    assert fy_2026.fiscal_year == 2026
    assert fy_2025.fiscal_year == 2025
    assert q_2026.fiscal_year == 2027


def test_annual_points_extracts_consecutive_fy(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(yahoo_fundamentals.yf, "Ticker", JapaneseTickerFixture)

    result = _japanese_fundamentals()

    annual = annual_points(result, "revenue")
    assert [(point.period_end, point.fiscal_period) for point in annual] == [
        (date(2025, 3, 31), "FY"),
        (date(2026, 3, 31), "FY"),
    ]

    interleaved = Fundamentals(
        symbol="SYN",
        cik="",
        company_name="Synthetic",
        metrics={
            "revenue": MetricSeries(
                key="revenue",
                label="revenue",
                points=[
                    MetricPoint(
                        period_end=date(2022, 3, 31),
                        fiscal_year=2022,
                        fiscal_period="FY",
                        value=Decimal("10"),
                        unit="JPY",
                    ),
                    MetricPoint(
                        period_end=date(2022, 9, 30),
                        fiscal_year=2023,
                        fiscal_period="Q",
                        value=Decimal("3"),
                        unit="JPY",
                    ),
                    MetricPoint(
                        period_end=date(2023, 3, 31),
                        fiscal_year=2023,
                        fiscal_period="FY",
                        value=Decimal("11"),
                        unit="JPY",
                    ),
                    MetricPoint(
                        period_end=date(2023, 9, 30),
                        fiscal_year=2024,
                        fiscal_period="Q",
                        value=Decimal("4"),
                        unit="JPY",
                    ),
                    MetricPoint(
                        period_end=date(2024, 3, 31),
                        fiscal_year=2024,
                        fiscal_period="FY",
                        value=Decimal("12"),
                        unit="JPY",
                    ),
                ],
            )
        },
        provenance=Provenance(
            provider="test",
            source="fixture",
            license_class=LicenseClass.OFFICIAL_PUBLIC,
            retrieved_at="2026-09-26T00:00:00Z",
        ),
    )

    synthetic_annual = annual_points(interleaved, "revenue")
    assert [point.period_end for point in synthetic_annual] == [
        date(2022, 3, 31),
        date(2023, 3, 31),
        date(2024, 3, 31),
    ]
    assert all(point.fiscal_period == "FY" for point in synthetic_annual)


def test_fiscal_year_derived_for_june_year_end_company(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    class JuneYearEndTicker:
        def __init__(self, symbol: str) -> None:
            self.symbol = symbol

        def get_income_stmt(
            self,
            *,
            freq: str | None = None,
            frequency: str | None = None,
            **_: object,
        ) -> pd.DataFrame:
            if (freq or frequency) == "quarterly":
                return pd.DataFrame(
                    {pd.Timestamp("2024-12-31"): [600, 200, 20]},
                    index=["Total Revenue", "Net Income", "Diluted Average Shares"],
                )
            return pd.DataFrame(
                {
                    pd.Timestamp("2024-06-30"): [1000, 400, 20],
                    pd.Timestamp("2023-06-30"): [900, 350, 20],
                },
                index=["Total Revenue", "Net Income", "Diluted Average Shares"],
            )

        def get_balance_sheet(self, **_: object) -> pd.DataFrame:
            return pd.DataFrame()

        def get_cash_flow(self, **_: object) -> pd.DataFrame:
            return pd.DataFrame()

        def get_history_metadata(self) -> dict[str, object]:
            return {"longName": "June Year End Corp", "currency": "JPY"}

    monkeypatch.setattr(yahoo_fundamentals.yf, "Ticker", JuneYearEndTicker)
    provider = YahooFundamentalsProvider(Settings(database_url="sqlite:///:memory:"))

    result = provider.company_facts("9999.T")

    revenue = result.metrics["revenue"].points
    fy_2024 = next(
        point
        for point in revenue
        if point.period_end == date(2024, 6, 30) and point.fiscal_period == "FY"
    )
    fy_2023 = next(
        point
        for point in revenue
        if point.period_end == date(2023, 6, 30) and point.fiscal_period == "FY"
    )
    q_dec = next(
        point
        for point in revenue
        if point.period_end == date(2024, 12, 31) and point.fiscal_period == "Q"
    )
    assert fy_2024.fiscal_year == 2024
    assert fy_2023.fiscal_year == 2023
    assert q_dec.fiscal_year == 2025
