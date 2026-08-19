from __future__ import annotations

from datetime import date

import pandas as pd

from yowayowa.config import Settings
from yowayowa.domain import LicenseClass
from yowayowa.providers import yahoo_fundamentals
from yowayowa.providers.yahoo_fundamentals import YahooFundamentalsProvider


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

    def get_info(self) -> dict[str, object]:
        return {
            "longName": "トヨタ自動車株式会社",
            "currency": "JPY",
            "financialCurrency": "JPY",
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
