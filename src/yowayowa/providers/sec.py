from __future__ import annotations

import threading
import time
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from cachetools import TTLCache
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

from yowayowa.config import Settings
from yowayowa.domain import (
    Fundamentals,
    Instrument,
    LicenseClass,
    MetricPoint,
    MetricSeries,
    Provenance,
)

_PERIODIC_FORMS = frozenset(
    {"10-K", "10-K/A", "10-Q", "10-Q/A", "20-F", "20-F/A", "40-F", "40-F/A"}
)


class _RateLimiter:
    def __init__(self, rate: float) -> None:
        self._interval = 1.0 / rate
        self._lock = threading.Lock()
        self._next_at = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delay = max(0.0, self._next_at - now)
            if delay:
                time.sleep(delay)
            self._next_at = max(now, self._next_at) + self._interval


CONCEPTS: dict[str, tuple[str, tuple[str, ...]]] = {
    "revenue": (
        "Revenue",
        (
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "SalesRevenueNet",
            "Revenues",
        ),
    ),
    "gross_profit": ("Gross profit", ("GrossProfit",)),
    "operating_income": ("Operating income", ("OperatingIncomeLoss",)),
    "net_income": ("Net income", ("NetIncomeLoss", "ProfitLoss")),
    "eps_diluted": ("Diluted EPS", ("EarningsPerShareDiluted",)),
    "assets": ("Total assets", ("Assets",)),
    "current_assets": ("Current assets", ("AssetsCurrent",)),
    "liabilities": ("Total liabilities", ("Liabilities",)),
    "current_liabilities": ("Current liabilities", ("LiabilitiesCurrent",)),
    "equity": (
        "Stockholders' equity",
        (
            "StockholdersEquity",
            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        ),
    ),
    "cash": (
        "Cash and equivalents",
        (
            "CashAndCashEquivalentsAtCarryingValue",
            "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        ),
    ),
    "operating_cash_flow": (
        "Operating cash flow",
        ("NetCashProvidedByUsedInOperatingActivities",),
    ),
    "capex": (
        "Capital expenditure",
        ("PaymentsToAcquirePropertyPlantAndEquipment",),
    ),
    "shares_diluted": (
        "Diluted shares",
        ("WeightedAverageNumberOfDilutedSharesOutstanding",),
    ),
}


def _candidate_score(priority: int, point: MetricPoint) -> tuple[int, int, int]:
    """Rank duplicate SEC facts without mixing quarter and YTD durations."""
    duration_score = 0
    if point.period_start is not None:
        duration_days = (point.period_end - point.period_start).days
        if point.fiscal_period in {"Q1", "Q2", "Q3"}:
            duration_score = -duration_days
        elif point.fiscal_period == "FY":
            duration_score = duration_days
    filed_score = (point.filed or date.min).toordinal()
    return duration_score, -priority, filed_score


class SecClient:
    base_url = "https://data.sec.gov"
    tickers_url = "https://www.sec.gov/files/company_tickers.json"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = httpx.Client(
            timeout=settings.request_timeout_seconds,
            headers={
                "User-Agent": settings.sec_user_agent,
                "Accept-Encoding": "gzip, deflate",
                "Accept": "application/json",
            },
        )
        self.limiter = _RateLimiter(settings.sec_requests_per_second)
        self._ticker_cache: tuple[datetime, dict[str, Instrument]] | None = None
        self._facts_cache: TTLCache[str, Fundamentals] = TTLCache(
            maxsize=512, ttl=max(60, settings.cache_ttl_seconds)
        )

    @retry(
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential_jitter(initial=0.25, max=3),
        reraise=True,
    )
    def _get_json(self, url: str) -> Any:
        self.limiter.wait()
        response = self.client.get(url)
        response.raise_for_status()
        return response.json()

    def ticker_map(self) -> dict[str, Instrument]:
        now = datetime.now(UTC)
        if self._ticker_cache and (now - self._ticker_cache[0]).total_seconds() < 86_400:
            return self._ticker_cache[1]
        payload = self._get_json(self.tickers_url)
        result: dict[str, Instrument] = {}
        for row in payload.values():
            symbol = str(row["ticker"]).upper()
            result[symbol] = Instrument(
                symbol=symbol,
                name=str(row["title"]),
                cik=f"{int(row['cik_str']):010d}",
                instrument_type="equity",
            )
        self._ticker_cache = (now, result)
        return result

    def search(self, query: str, limit: int = 20) -> list[Instrument]:
        q = query.casefold().strip()
        if not q:
            return []
        instruments = self.ticker_map().values()
        exact: list[Instrument] = []
        prefix: list[Instrument] = []
        contains: list[Instrument] = []
        for instrument in instruments:
            symbol = instrument.symbol.casefold()
            name = instrument.name.casefold()
            if symbol == q:
                exact.append(instrument)
            elif symbol.startswith(q) or name.startswith(q):
                prefix.append(instrument)
            elif q in symbol or q in name:
                contains.append(instrument)
        return (exact + prefix + contains)[:limit]

    def company_facts(self, symbol: str) -> Fundamentals:
        normalized_symbol = symbol.upper()
        cached = self._facts_cache.get(normalized_symbol)
        if cached is not None:
            return cached
        instrument = self.ticker_map().get(normalized_symbol)
        if not instrument or not instrument.cik:
            raise LookupError(f"SEC ticker mapping not found for {symbol.upper()}")
        payload = self._get_json(f"{self.base_url}/api/xbrl/companyfacts/CIK{instrument.cik}.json")
        metrics: dict[str, MetricSeries] = {}
        us_gaap = payload.get("facts", {}).get("us-gaap", {})
        for key, (label, concepts) in CONCEPTS.items():
            series = self._extract_metric(us_gaap, key, label, concepts)
            if series.points:
                metrics[key] = series
        retrieved = datetime.now(UTC)
        result = Fundamentals(
            symbol=instrument.symbol,
            cik=instrument.cik,
            company_name=str(payload.get("entityName") or instrument.name),
            metrics=metrics,
            provenance=Provenance(
                provider="sec-edgar",
                source="SEC EDGAR Company Facts",
                source_url=(f"{self.base_url}/api/xbrl/companyfacts/CIK{instrument.cik}.json"),
                license_class=LicenseClass.OFFICIAL_PUBLIC,
                retrieved_at=retrieved,
                as_of=retrieved,
                notes=[
                    "Merged prioritized standard us-gaap aliases across periods; custom taxonomy "
                    "extensions are not merged."
                ],
            ),
        )
        self._facts_cache[normalized_symbol] = result
        return result

    @staticmethod
    def _extract_metric(
        us_gaap: dict[str, Any], key: str, label: str, concepts: tuple[str, ...]
    ) -> MetricSeries:
        candidates: list[tuple[int, MetricPoint]] = []
        for priority, concept in enumerate(concepts):
            fact = us_gaap.get(concept)
            if not fact:
                continue
            for unit, rows in fact.get("units", {}).items():
                for row in rows:
                    form = row.get("form")
                    if form not in _PERIODIC_FORMS:
                        continue
                    end = row.get("end")
                    value = row.get("val")
                    if end is None or value is None:
                        continue
                    try:
                        point = MetricPoint(
                            period_start=(
                                date.fromisoformat(row["start"]) if row.get("start") else None
                            ),
                            period_end=date.fromisoformat(end),
                            fiscal_year=row.get("fy"),
                            fiscal_period=row.get("fp"),
                            value=Decimal(str(value)),
                            unit=unit,
                            accession=row.get("accn"),
                            filed=(date.fromisoformat(row["filed"]) if row.get("filed") else None),
                            form=form,
                        )
                    except (ValueError, InvalidOperation):
                        continue
                    candidates.append((priority, point))

        dedup: dict[tuple[date, str, str | None], tuple[int, MetricPoint]] = {}
        for priority, point in candidates:
            key_tuple = (point.period_end, point.unit, point.fiscal_period)
            current = dedup.get(key_tuple)
            if current is None or _candidate_score(priority, point) > _candidate_score(*current):
                dedup[key_tuple] = (priority, point)

        points = sorted(
            (candidate[1] for candidate in dedup.values()),
            key=lambda point: (point.period_end, point.filed or date.min),
        )
        return MetricSeries(key=key, label=label, points=points[-24:])
