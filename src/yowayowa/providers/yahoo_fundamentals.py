from __future__ import annotations

import math
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, ClassVar

import pandas as pd
import yfinance as yf

from yowayowa.config import Settings
from yowayowa.domain import Fundamentals, LicenseClass, MetricPoint, MetricSeries, Provenance
from yowayowa.providers.base import ProviderDescriptor, enforce_provider_policy
from yowayowa.symbols import normalize_symbol


class YahooFundamentalsProvider:
    """Personal-mode financial statements for non-SEC issuers.

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

    CURRENCY_SUFFIX_HINTS: ClassVar[dict[str, str]] = {
        ".T": "JPY",
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
        metadata = self._safe_history_metadata(ticker)
        currency = str(metadata.get("currency") or self._currency_hint(normalized)).upper()
        company_name = str(
            metadata.get("longName")
            or metadata.get("shortName")
            or metadata.get("symbol")
            or normalized
        )

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

        self._fill_fiscal_years(metrics, self._yearly_period_ends(frames))
        self._derive_eps_diluted(metrics, currency)

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
                        "Personal-mode financial statements for issuers outside the SEC "
                        "company-facts universe, including Japanese listings."
                    ),
                    (
                        "Statement period ends and annual/quarterly frequency come from Yahoo; "
                        "approximate starts are inferred only for normalized ratio/grouping math."
                    ),
                    (
                        "Company identity and currency use Yahoo price-history metadata when "
                        "available; metadata failure does not invalidate otherwise usable "
                        "statements."
                    ),
                    (
                        "Capital expenditure is normalized to a positive cash outflow before "
                        "FCF calculations."
                    ),
                    (
                        "Diluted EPS is derived as net income / diluted shares for periods "
                        "where Yahoo reports no usable EPS row."
                    ),
                    "Do not redistribute this normalized Yahoo dataset from public mode.",
                ],
            ),
        )

    @classmethod
    def _currency_hint(cls, symbol: str) -> str:
        return next(
            (
                currency
                for suffix, currency in cls.CURRENCY_SUFFIX_HINTS.items()
                if symbol.endswith(suffix)
            ),
            "",
        )

    @staticmethod
    def _safe_history_metadata(ticker: yf.Ticker) -> dict[str, Any]:
        try:
            value = ticker.get_history_metadata()
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
    def _yearly_period_ends(frames: dict[str, list[pd.DataFrame]]) -> list[date]:
        """Collect every period end across all yearly-frequency statement frames."""

        ends: list[date] = []
        for frame in frames.get("yearly", []):
            for column in frame.columns:
                try:
                    timestamp = pd.Timestamp(str(column))
                except (TypeError, ValueError, OverflowError):
                    continue
                if pd.isna(timestamp):
                    continue
                ends.append(timestamp.date())
        return ends

    @classmethod
    def _fill_fiscal_years(
        cls,
        metrics: dict[str, MetricSeries],
        yearly_period_ends: list[date],
    ) -> None:
        """Assign fiscal years to every point from the inferred fiscal year-end month.

        The fiscal year-end month is the most common month among yearly statement
        period ends (needs at least two). A period ending in the fiscal-end month
        closes that fiscal year; a period ending in a later month belongs to the
        next fiscal year; a period ending before it belongs to the current one.
        Without enough yearly frames, only FY points can be safely labeled with
        their period-end year and quarterly points stay unlabeled.
        """

        fiscal_month: int | None = None
        if len(yearly_period_ends) >= 2:
            month_counts = Counter(period.month for period in yearly_period_ends)
            fiscal_month = max(month_counts, key=lambda month: (month_counts[month], -month))
        for series in metrics.values():
            for point in series.points:
                point.fiscal_year = cls._fiscal_year_for(point, fiscal_month)

    @staticmethod
    def _fiscal_year_for(point: MetricPoint, fiscal_month: int | None) -> int | None:
        if fiscal_month is None:
            return point.period_end.year if point.fiscal_period == "FY" else None
        month = point.period_end.month
        if month == fiscal_month:
            return point.period_end.year
        if month > fiscal_month:
            return point.period_end.year + 1
        return point.period_end.year

    @classmethod
    def _derive_eps_diluted(cls, metrics: dict[str, MetricSeries], currency: str) -> None:
        """Derive diluted EPS as net income / diluted shares when Yahoo has no usable EPS.

        Fails closed per period: division is refused unless both inputs exist and
        diluted shares are non-zero. A non-zero Yahoo-provided EPS value is never
        overwritten; a zero-valued one is replaced.
        """

        net_income = metrics.get("net_income")
        shares = metrics.get("shares_diluted")
        if net_income is None or shares is None:
            return
        net_by_period: dict[tuple[date, str | None], MetricPoint] = {
            (point.period_end, point.fiscal_period): point for point in net_income.points
        }
        shares_by_period: dict[tuple[date, str | None], MetricPoint] = {
            (point.period_end, point.fiscal_period): point for point in shares.points
        }
        eps_series = metrics.get("eps_diluted")
        eps_by_period: dict[tuple[date, str | None], MetricPoint] = {
            (point.period_end, point.fiscal_period): point
            for point in (eps_series.points if eps_series is not None else [])
        }
        unit = f"{currency}/share" if currency else "per share"
        derived: list[MetricPoint] = []
        for period_key, net_point in net_by_period.items():
            share_point = shares_by_period.get(period_key)
            if share_point is None:
                continue
            shares_value = share_point.value
            if not shares_value.is_finite() or shares_value == 0:
                continue
            if not net_point.value.is_finite():
                continue
            existing = eps_by_period.get(period_key)
            if existing is not None and existing.value != Decimal("0"):
                continue
            value = (net_point.value / shares_value).quantize(
                Decimal("0.000001"),
                rounding=ROUND_HALF_UP,
            )
            derived.append(
                MetricPoint(
                    period_start=net_point.period_start,
                    period_end=net_point.period_end,
                    fiscal_year=net_point.fiscal_year,
                    fiscal_period=net_point.fiscal_period,
                    value=value,
                    unit=unit,
                    accession=net_point.accession,
                    filed=net_point.filed,
                    form="Yahoo normalized statement",
                )
            )
        if not derived:
            return
        series = metrics.setdefault(
            "eps_diluted",
            MetricSeries(
                key="eps_diluted",
                label=cls.LABELS.get("eps_diluted", "eps_diluted"),
                points=[],
            ),
        )
        derived_keys = {(point.period_end, point.fiscal_period) for point in derived}
        series.points = [
            point
            for point in series.points
            if point.value != Decimal("0")
            or (point.period_end, point.fiscal_period) not in derived_keys
        ]
        series.points.extend(derived)
        series.points.sort(key=lambda point: point.period_end)

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
