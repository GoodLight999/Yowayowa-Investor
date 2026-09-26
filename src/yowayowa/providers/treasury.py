from __future__ import annotations

from datetime import UTC, date, datetime
from xml.etree import ElementTree

import httpx
from cachetools import TTLCache

from yowayowa.config import Settings
from yowayowa.domain import LicenseClass, Provenance
from yowayowa.rate_models import (
    TreasuryYieldCurve,
    YieldCurvePoint,
    YieldCurveSnapshot,
)

_MATURITIES: tuple[tuple[str, str, float], ...] = (
    ("BC_1MONTH", "1M", 1 / 12),
    ("BC_1_5MONTH", "1.5M", 1.5 / 12),
    ("BC_2MONTH", "2M", 2 / 12),
    ("BC_3MONTH", "3M", 3 / 12),
    ("BC_4MONTH", "4M", 4 / 12),
    ("BC_6MONTH", "6M", 6 / 12),
    ("BC_1YEAR", "1Y", 1),
    ("BC_2YEAR", "2Y", 2),
    ("BC_3YEAR", "3Y", 3),
    ("BC_5YEAR", "5Y", 5),
    ("BC_7YEAR", "7Y", 7),
    ("BC_10YEAR", "10Y", 10),
    ("BC_20YEAR", "20Y", 20),
    ("BC_30YEAR", "30Y", 30),
)


class TreasuryYieldCurveProvider:
    base_url = (
        "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml"
    )

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = httpx.Client(
            timeout=settings.request_timeout_seconds,
            headers={"User-Agent": ("Yowayowa-Investor/1.0 (+personal investment research)")},
        )
        ttl = max(300, settings.cache_ttl_seconds)
        self._cache: TTLCache[int, TreasuryYieldCurve] = TTLCache(
            maxsize=4,
            ttl=ttl,
        )

    @staticmethod
    def _local_name(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    @classmethod
    def _parse_xml(
        cls,
        xml: str,
        *,
        retrieved_at: datetime,
    ) -> TreasuryYieldCurve:
        root = ElementTree.fromstring(xml)
        snapshots: list[YieldCurveSnapshot] = []
        for properties in root.iter():
            if cls._local_name(properties.tag).casefold() != "properties":
                continue
            values = {
                cls._local_name(child.tag): (child.text or "").strip() for child in list(properties)
            }
            raw_date = values.get("NEW_DATE") or values.get("QUOTE_DATE")
            if not raw_date:
                continue
            try:
                curve_date = date.fromisoformat(raw_date[:10])
            except ValueError:
                continue
            points: list[YieldCurvePoint] = []
            rates: dict[str, float] = {}
            for field, maturity, years in _MATURITIES:
                raw = values.get(field)
                if not raw:
                    continue
                try:
                    rate = float(raw)
                except ValueError:
                    continue
                rates[maturity] = rate
                points.append(
                    YieldCurvePoint(
                        maturity=maturity,
                        years=years,
                        yield_percent=rate,
                    )
                )
            if not points:
                continue
            snapshots.append(
                YieldCurveSnapshot(
                    date=curve_date,
                    points=points,
                    spread_10y_2y=(
                        rates["10Y"] - rates["2Y"] if "10Y" in rates and "2Y" in rates else None
                    ),
                    spread_10y_3m=(
                        rates["10Y"] - rates["3M"] if "10Y" in rates and "3M" in rates else None
                    ),
                )
            )
        snapshots.sort(key=lambda item: item.date)
        if not snapshots:
            raise LookupError("No Treasury par yield curve observations returned")
        latest = snapshots[-1]
        return TreasuryYieldCurve(
            latest=latest,
            history=snapshots,
            provenance=Provenance(
                provider="us-treasury",
                source=("U.S. Department of the Treasury Daily Treasury Par Yield Curve Rates"),
                source_url=cls.base_url,
                license_class=LicenseClass.OFFICIAL_PUBLIC,
                retrieved_at=retrieved_at,
                as_of=latest.date,
                notes=[
                    "Official Treasury XML feed; missing maturities are omitted by the "
                    "source and remain absent.",
                    "Yield values are percentage points, not decimal fractions.",
                    "10Y-2Y and 10Y-3M spreads are simple percentage-point differences "
                    "from published par yields.",
                ],
            ),
        )

    def curve(self, year: int | None = None) -> TreasuryYieldCurve:
        current_year = datetime.now(UTC).year
        resolved_year = year or current_year
        if not 1990 <= resolved_year <= current_year:
            raise ValueError(
                "Treasury par yield curve year must be between 1990 and the current year"
            )
        cached = self._cache.get(resolved_year)
        if cached is not None:
            return cached
        response = self.client.get(
            self.base_url,
            params={
                "data": "daily_treasury_yield_curve",
                "field_tdr_date_value": str(resolved_year),
            },
        )
        response.raise_for_status()
        result = self._parse_xml(
            response.text,
            retrieved_at=datetime.now(UTC),
        )
        self._cache[resolved_year] = result
        return result
