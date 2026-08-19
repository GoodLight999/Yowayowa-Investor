from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, ClassVar

import pandas as pd
import yfinance as yf

from yowayowa.config import Settings
from yowayowa.domain import Fundamentals, LicenseClass, MetricPoint, MetricSeries, Provenance
from yowayowa.providers.base import ProviderDescriptor, enforce_provider_policy
from yowayowa.symbols import normalize_symbol


class YahooFundamentalsProvider:
    """Personal-mode financial-statement fallback for non-SEC issuers.

    This path is intentionally isolated from public mode. Yahoo provides statement
    period ends and frequency rather than SEC tagged contexts, so approximate period
    starts are used only for ratio/grouping math and disclosed in provenance.
    """

    descriptor = ProviderDescriptor(
        name="yahoo-fundamentals",
        license_class=LicenseClass.PERSONAL_ONLY,
        redistributable=False,
        description=(
            "Yahoo Finance normalized financial statements via yfinance; personal use only."
        ),
    )

    ROW_ALIASES: ClassVar[dict[str, tuple[str, ...]]] = {
        "revenue": ("Total Revenue", "Operating Revenue"),
        "gross_profit": ("Gross Profit",),
        "operating_income": ("Operating Income",),
        "net_income": ("Net Income Common Stockholders", "Net Income"),
        "eps_diluted": ("Diluted EPS",),
        "shares_diluted": ("Diluted Average Shares",),
        "cash": (
            "Cash Cash Equivalents And Short Term Investments",
            "Cash And Cash Equivalents",
            "Cash Financial",
        ),
        "current_assets": ("Current Assets",),
        "current_liabilities": ("Current Liabilities",),
        "assets": ("Total Assets",),
        "liabilities": (
            "Total Liabilities Net Minority Interest",
            "Total Liabilities",
        ),
        "equity": (
            "Stockholders Equity",
            "Total Equity Gross Minority Interest",
            "Common Stock Equity",
        ),
        "operating_cash_flow": (
            "Operating Cash Flow",
            "Total Cash From Operating Activities",
        ),
        "capex": ("Capital Expenditure", "Capital Expenditures"),
    }

    LABELS: ClassVar[dict[str, str]] = {
        "revenue": "Revenue",
        "gross_profit": "Gross profit",
        "operating_income": "Operating income",
        "net_income": "Net income",
        "eps_diluted": "Diluted EPS",
        "shares_diluted": "Diluted shares",
        "cash": "Cash and cash equivalents",
        "current_assets": "Current assets",
        "current_liabilities": "Current liabilities",
        "assets": "Total assets",
        "liabilities": "Total liabilities",
        "equity": "Equity",
        "operating_cash_flow": "Operating cash flow",
        "capex": "Capital expenditure",
    }

    def __init__(self, settings: Settings) -> None:
        enforce_provider_policy(
            self.descriptor,
            mode=settings.mode,
            allow_personal_in_public=settings.allow_personal_provider_in_public,
        )
        self.settings = settings

    def company_facts(self, symbol: str) -> Fundamentals:
        normalized = normalize_symbol(symbol)
        ticker = yf.Ticker(normalized)
        info = self._safe_info(ticker)
        currency = str(info.get("financialCurrency") or info.get("currency") or "").upper()
        company_name = str(info.get("longName") or info.get("shortName") or normalized)

        metrics: dict[str, MetricSeries] = {}
        frames = self._statement_frames(ticker)
        for frequency, statements in frames.items():
            for frame in statements:
                self._merge_frame(metrics, frame, frequency, currency)

        for series in metrics.values():
            dedup: dict[tuple[date, str | None, Decimal], MetricPoint] = {}
            for point in series.points:
                dedup[(point.period_end, point.fiscal_period, point.value)] = point
            series.points = sorted(dedup.values(), key=lambda item: item.period_end)

        if not metrics:
            raise LookupError(f"No Yahoo financial statements available for {normalized}")

        now = datetime.now(UTC)
        return Fundamentals(
            symbol=normalized,
            cik="",
            company_name=company_name,
            metrics=metrics,
            provenance=Provenance(
                provider="yahoo/yfinance",
                source="Yahoo Finance financial statements",
                source_url=f"https://finance.yahoo.com/quote/{normalized}/financials/",
                license_class=LicenseClass.PERSONAL_ONLY,
                retrieved_at=now,
                as_of=max(
                    (point.period_end for series in metrics.values() for point in series.points),
                    default=None,
                ),
                notes=[
                    (
                        "Personal-mode fallback for issuers without SEC company facts, "
                        "including Japanese listings."
                    ),
                    (
                        "Statement period ends and annual/quarterly frequency come from Yahoo; "
                        "approximate starts are inferred only for normalized ratio/grouping math."
                    ),
                    (
                        "Capital expenditure is normalized to a positive cash outflow before "
                        "FCF calculations."
                    ),
                    "Do not redistribute this normalized Yahoo dataset from public mode.",
                ],
            ),
        )

    @staticmethod
    def _safe_info(ticker: yf.Ticker) -> dict[str, Any]:
        try:
            value = ticker.get_info()
        except Exception:
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _safe_frame(loader: Any, frequency: str) -> pd.DataFrame:
        try:
            frame = loader(freq=frequency)
        except (TypeError, ValueError):
            try:
                frame = loader(frequency=frequency)
            except Exception:
                return pd.DataFrame()
        except Exception:
            return pd.DataFrame()
        return frame if isinstance(frame, pd.DataFrame) else pd.DataFrame()

    def _statement_frames(self, ticker: yf.Ticker) -> dict[str, list[pd.DataFrame]]:
        result: dict[str, list[pd.DataFrame]] = {"yearly": [], "quarterly": []}
        loaders = (ticker.get_income_stmt, ticker.get_balance_sheet, ticker.get_cash_flow)
        for frequency in result:
            for loader in loaders:
                frame = self._safe_frame(loader, frequency)
                if not frame.empty:
                    result[frequency].append(frame)
        return result

    @classmethod
    def _merge_frame(
        cls,
        metrics: dict[str, MetricSeries],
        frame: pd.DataFrame,
        frequency: str,
        currency: str,
    ) -> None:
        if frame.empty:
            return
        normalized_index = {cls._normalize_row_name(item): item for item in frame.index}
        for key, aliases in cls.ROW_ALIASES.items():
            source_row = next(
                (
                    normalized_index[cls._normalize_row_name(alias)]
                    for alias in aliases
                    if cls._normalize_row_name(alias) in normalized_index
                ),
                None,
            )
            if source_row is None:
                continue
            row = frame.loc[source_row]
            if isinstance(row, pd.DataFrame):
                row = row.iloc[0]
            for raw_period, raw_value in row.items():
                point = cls._point(key, raw_period, raw_value, frequency, currency)
                if point is None:
                    continue
                series = metrics.setdefault(
                    key,
                    MetricSeries(key=key, label=cls.LABELS.get(key, key), points=[]),
                )
                series.points.append(point)

    @staticmethod
    def _normalize_row_name(value: object) -> str:
        return "".join(ch.lower() for ch in str(value) if ch.isalnum())

    @classmethod
    def _point(
        cls,
        key: str,
        raw_period: object,
        raw_value: object,
        frequency: str,
        currency: str,
    ) -> MetricPoint | None:
        try:
            number = float(str(raw_value))
        except ValueError:
            return None
        if not math.isfinite(number):
            return None
        if key == "capex":
            number = abs(number)
        try:
            timestamp = pd.Timestamp(str(raw_period))
        except (TypeError, ValueError, OverflowError):
            return None
        if pd.isna(timestamp):
            return None
        period = timestamp.date()

        annual = frequency == "yearly"
        period_start = period - timedelta(days=364 if annual else 89)
        fiscal_period = "FY" if annual else "Q"
        unit = "shares" if key == "shares_diluted" else currency
        if key == "eps_diluted":
            unit = f"{currency}/share" if currency else "per share"
        return MetricPoint(
            period_start=period_start,
            period_end=period,
            fiscal_year=None,
            fiscal_period=fiscal_period,
            value=Decimal(str(number)),
            unit=unit,
            accession=None,
            filed=None,
            form="Yahoo normalized statement",
        )
