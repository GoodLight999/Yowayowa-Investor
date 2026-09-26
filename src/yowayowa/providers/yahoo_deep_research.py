from __future__ import annotations

import math
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

import pandas as pd
import yfinance as yf

from yowayowa.config import Settings
from yowayowa.domain import LicenseClass, Provenance
from yowayowa.providers.base import ProviderDescriptor, enforce_provider_policy
from yowayowa.research_models import CompanyResearch, OptionChainSnapshot, ResearchSection
from yowayowa.symbols import normalize_symbol


class YahooDeepResearchProvider:
    """High-density personal research data exposed by yfinance's public API.

    Every subsection is isolated so one missing Yahoo module does not erase the
    rest of the research surface. The provider remains PERSONAL_ONLY by design.
    """

    descriptor = ProviderDescriptor(
        name="yahoo-deep-research",
        license_class=LicenseClass.PERSONAL_ONLY,
        redistributable=False,
        description=(
            "Yahoo Finance analyst, ownership, insider, ESG, corporate-action, option, and fund "
            "data via yfinance; personal use only."
        ),
    )

    PROFILE_KEYS = (
        "longName",
        "shortName",
        "quoteType",
        "exchange",
        "fullExchangeName",
        "currency",
        "financialCurrency",
        "country",
        "city",
        "website",
        "sector",
        "industry",
        "industryKey",
        "sectorKey",
        "fullTimeEmployees",
        "longBusinessSummary",
        "marketCap",
        "enterpriseValue",
        "sharesOutstanding",
        "floatShares",
        "impliedSharesOutstanding",
        "beta",
        "trailingPE",
        "forwardPE",
        "pegRatio",
        "priceToBook",
        "enterpriseToRevenue",
        "enterpriseToEbitda",
        "profitMargins",
        "grossMargins",
        "operatingMargins",
        "ebitdaMargins",
        "returnOnAssets",
        "returnOnEquity",
        "revenueGrowth",
        "earningsGrowth",
        "earningsQuarterlyGrowth",
        "totalRevenue",
        "grossProfits",
        "ebitda",
        "netIncomeToCommon",
        "totalCash",
        "totalCashPerShare",
        "totalDebt",
        "debtToEquity",
        "currentRatio",
        "quickRatio",
        "freeCashflow",
        "operatingCashflow",
        "dividendRate",
        "dividendYield",
        "payoutRatio",
        "fiveYearAvgDividendYield",
        "exDividendDate",
        "lastDividendValue",
        "lastDividendDate",
        "shortRatio",
        "shortPercentOfFloat",
        "sharesShort",
        "sharesShortPriorMonth",
        "sharesPercentSharesOut",
        "heldPercentInsiders",
        "heldPercentInstitutions",
        "averageVolume",
        "averageVolume10days",
        "fiftyTwoWeekHigh",
        "fiftyTwoWeekLow",
        "fiftyDayAverage",
        "twoHundredDayAverage",
        "targetHighPrice",
        "targetLowPrice",
        "targetMeanPrice",
        "targetMedianPrice",
        "recommendationMean",
        "recommendationKey",
        "numberOfAnalystOpinions",
    )

    def __init__(self, settings: Settings) -> None:
        enforce_provider_policy(
            self.descriptor,
            mode=settings.mode,
            allow_personal_in_public=settings.allow_personal_provider_in_public,
        )
        self.settings = settings

    def research(
        self,
        symbol: str,
        sections: list[ResearchSection] | None = None,
    ) -> CompanyResearch:
        normalized = normalize_symbol(symbol)
        ticker = yf.Ticker(normalized)
        requested = sections or list(ResearchSection)
        loaders: dict[ResearchSection, Callable[[yf.Ticker], Any]] = {
            ResearchSection.PROFILE: self._profile,
            ResearchSection.ANALYST: self._analyst,
            ResearchSection.OWNERSHIP: self._ownership,
            ResearchSection.INSIDERS: self._insiders,
            ResearchSection.ESG: self._esg,
            ResearchSection.ACTIONS: self._actions,
            ResearchSection.FILINGS: self._filings,
            ResearchSection.FUND: self._fund,
        }
        payload: dict[str, Any] = {}
        errors: dict[str, str] = {}
        for section in requested:
            try:
                payload[section.value] = self._jsonable(loaders[section](ticker))
            except Exception as exc:
                errors[section.value] = f"{type(exc).__name__}: {exc}"
        if not payload and errors:
            raise LookupError(f"No Yahoo research data available for {normalized}")
        now = datetime.now(UTC)
        return CompanyResearch(
            symbol=normalized,
            sections=payload,
            errors=errors,
            provenance=Provenance(
                provider="yahoo/yfinance",
                source="Yahoo Finance research modules",
                source_url=f"https://finance.yahoo.com/quote/{normalized}/",
                license_class=LicenseClass.PERSONAL_ONLY,
                retrieved_at=now,
                as_of=now,
                notes=[
                    "Research modules are exposed for personal research only.",
                    "Each section is isolated; a provider failure may leave other sections usable.",
                ],
            ),
        )

    def option_chain(self, symbol: str, expiration: str | None = None) -> OptionChainSnapshot:
        normalized = normalize_symbol(symbol)
        ticker = yf.Ticker(normalized)
        expirations = list(ticker.options)
        calls: list[dict[str, Any]] = []
        puts: list[dict[str, Any]] = []
        underlying: dict[str, Any] = {}
        if expiration:
            if expiration not in expirations:
                choices = ", ".join(expirations)
                raise ValueError(
                    f"Expiration {expiration!r} is unavailable; choose one of {choices}"
                )
            chain = ticker.option_chain(expiration)
            calls = self._records(chain.calls)
            puts = self._records(chain.puts)
            underlying_raw = getattr(chain, "underlying", {})
            if isinstance(underlying_raw, dict):
                underlying = self._jsonable(underlying_raw)
        now = datetime.now(UTC)
        return OptionChainSnapshot(
            symbol=normalized,
            expirations=expirations,
            expiration=expiration,
            underlying=underlying,
            calls=calls,
            puts=puts,
            provenance=Provenance(
                provider="yahoo/yfinance",
                source="Yahoo Finance options",
                source_url=f"https://finance.yahoo.com/quote/{normalized}/options/",
                license_class=LicenseClass.PERSONAL_ONLY,
                retrieved_at=now,
                as_of=now,
                notes=["US option-chain coverage and field availability depend on Yahoo Finance."],
            ),
        )

    def _profile(self, ticker: yf.Ticker) -> dict[str, Any]:
        info = ticker.get_info()
        if not isinstance(info, dict):
            return {}
        return {key: info[key] for key in self.PROFILE_KEYS if info.get(key) is not None}

    @staticmethod
    def _analyst(ticker: yf.Ticker) -> dict[str, Any]:
        return {
            "price_targets": ticker.get_analyst_price_targets(),
            "recommendations": ticker.get_recommendations(),
            "recommendations_summary": ticker.get_recommendations_summary(),
            "upgrades_downgrades": ticker.get_upgrades_downgrades(),
            "earnings_estimate": ticker.get_earnings_estimate(),
            "revenue_estimate": ticker.get_revenue_estimate(),
            "earnings_history": ticker.get_earnings_history(),
            "eps_trend": ticker.get_eps_trend(),
            "eps_revisions": ticker.get_eps_revisions(),
            "growth_estimates": ticker.get_growth_estimates(),
        }

    @staticmethod
    def _ownership(ticker: yf.Ticker) -> dict[str, Any]:
        return {
            "major_holders": ticker.get_major_holders(),
            "institutional_holders": ticker.get_institutional_holders(),
            "mutual_fund_holders": ticker.get_mutualfund_holders(),
            "shares": ticker.get_shares(),
        }

    @staticmethod
    def _insiders(ticker: yf.Ticker) -> dict[str, Any]:
        return {
            "purchases": ticker.get_insider_purchases(),
            "transactions": ticker.get_insider_transactions(),
            "roster": ticker.get_insider_roster_holders(),
        }

    @staticmethod
    def _esg(ticker: yf.Ticker) -> Any:
        return ticker.get_sustainability()

    @staticmethod
    def _actions(ticker: yf.Ticker) -> dict[str, Any]:
        return {
            "dividends": ticker.get_dividends(period="10y"),
            "splits": ticker.get_splits(period="10y"),
            "capital_gains": ticker.get_capital_gains(period="10y"),
        }

    @staticmethod
    def _filings(ticker: yf.Ticker) -> Any:
        return ticker.get_sec_filings()

    @staticmethod
    def _fund(ticker: yf.Ticker) -> dict[str, Any]:
        fund = ticker.get_funds_data()
        if fund is None:
            return {}
        result: dict[str, Any] = {}
        for name in (
            "description",
            "fund_overview",
            "fund_operations",
            "asset_classes",
            "top_holdings",
            "equity_holdings",
            "bond_holdings",
            "bond_ratings",
            "sector_weightings",
        ):
            try:
                value = getattr(fund, name)
            except Exception:
                continue
            if value is not None:
                result[name] = value
        return result

    @classmethod
    def _records(cls, frame: pd.DataFrame | None) -> list[dict[str, Any]]:
        value = cls._jsonable(frame)
        return value if isinstance(value, list) else []

    @classmethod
    def _jsonable(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, pd.DataFrame):
            frame = value.reset_index()
            rows: list[dict[str, Any]] = []
            for raw in frame.to_dict(orient="records"):
                rows.append({str(key): cls._jsonable(item) for key, item in raw.items()})
            return rows
        if isinstance(value, pd.Series):
            return [
                {"index": cls._jsonable(index), "value": cls._jsonable(item)}
                for index, item in value.items()
            ]
        if isinstance(value, dict):
            return {str(key): cls._jsonable(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [cls._jsonable(item) for item in value]
        if isinstance(value, (pd.Timestamp, datetime, date)):
            return value.isoformat()
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return None
        if hasattr(value, "item"):
            try:
                return cls._jsonable(value.item())
            except (ValueError, AttributeError):
                pass
        if isinstance(value, (str, int, float, bool)):
            return value
        return str(value)
