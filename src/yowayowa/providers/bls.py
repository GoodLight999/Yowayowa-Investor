from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import httpx
from cachetools import TTLCache

from yowayowa.bls_models import (
    BlsCatalog,
    BlsCatalogItem,
    BlsObservation,
    BlsSeries,
)
from yowayowa.config import Settings
from yowayowa.domain import LicenseClass, Provenance
from yowayowa.providers.base import enforce_source_policy

BLS_CATALOG: tuple[BlsCatalogItem, ...] = (
    BlsCatalogItem(
        series_id="CUSR0000SA0",
        title="Consumer Price Index — All urban consumers, all items",
        unit="Index 1982-84=100",
        seasonal_adjustment="Seasonally adjusted",
        category="Inflation",
    ),
    BlsCatalogItem(
        series_id="CUSR0000SA0L1E",
        title="Consumer Price Index — All items less food and energy",
        unit="Index 1982-84=100",
        seasonal_adjustment="Seasonally adjusted",
        category="Inflation",
    ),
    BlsCatalogItem(
        series_id="LNS14000000",
        title="Unemployment rate",
        unit="Percent",
        seasonal_adjustment="Seasonally adjusted",
        category="Labor",
    ),
    BlsCatalogItem(
        series_id="LNS11300000",
        title="Labor force participation rate",
        unit="Percent",
        seasonal_adjustment="Seasonally adjusted",
        category="Labor",
    ),
    BlsCatalogItem(
        series_id="CES0000000001",
        title="Total nonfarm payroll employment",
        unit="Thousands of persons",
        seasonal_adjustment="Seasonally adjusted",
        category="Labor",
    ),
    BlsCatalogItem(
        series_id="CES0500000003",
        title="Average hourly earnings — Total private",
        unit="U.S. dollars per hour",
        seasonal_adjustment="Seasonally adjusted",
        category="Labor",
    ),
)

_CATALOG_BY_ID = {item.series_id: item for item in BLS_CATALOG}


class BlsClient:
    v1_url = "https://api.bls.gov/publicAPI/v1/timeseries/data/"
    v2_url = "https://api.bls.gov/publicAPI/v2/timeseries/data/"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        enforce_source_policy("bls", mode=settings.mode)
        self.client = httpx.Client(timeout=settings.request_timeout_seconds)
        self._cache: TTLCache[tuple[str, int, int], BlsSeries] = TTLCache(
            maxsize=256,
            ttl=max(3600, settings.cache_ttl_seconds),
        )

    @staticmethod
    def _provenance(now: datetime, series_id: str | None = None) -> Provenance:
        source_url = (
            f"https://data.bls.gov/timeseries/{series_id}"
            if series_id
            else "https://www.bls.gov/developers/"
        )
        return Provenance(
            provider="bls",
            source="U.S. Bureau of Labor Statistics Public Data API",
            source_url=source_url,
            license_class=LicenseClass.OFFICIAL_PUBLIC,
            retrieved_at=now,
            as_of=now,
            notes=[
                "BLS published material is public domain except identified third-party assets.",
                "Source: U.S. Bureau of Labor Statistics. Yowayowa transformations are not "
                "endorsed by BLS, and BLS cannot vouch for analyses after retrieval.",
            ],
        )

    def catalog(self) -> BlsCatalog:
        return BlsCatalog(
            series=list(BLS_CATALOG),
            provenance=self._provenance(datetime.now(UTC)),
        )

    @staticmethod
    def _observation_date(year: int, period: str) -> date | None:
        if len(period) == 3 and period.startswith("M") and period[1:].isdigit():
            month = int(period[1:])
            if 1 <= month <= 12:
                return date(year, month, 1)
        return None

    @staticmethod
    def _series_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
        results = payload.get("Results", {})
        if isinstance(results, dict):
            rows = results.get("series", [])
            return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
        if isinstance(results, list):
            for item in results:
                if not isinstance(item, dict):
                    continue
                rows = item.get("series", [])
                if isinstance(rows, list):
                    return [row for row in rows if isinstance(row, dict)]
        return []

    def series(
        self,
        series_id: str,
        *,
        start_year: int | None = None,
        end_year: int | None = None,
    ) -> BlsSeries:
        normalized = series_id.strip().upper()
        if (
            not normalized
            or len(normalized) > 64
            or not all(char.isupper() or char.isdigit() or char in "_-#" for char in normalized)
        ):
            raise ValueError("Invalid BLS series id")

        current_year = datetime.now(UTC).year
        resolved_end = end_year or current_year
        max_years = 20 if self.settings.bls_api_key else 10
        resolved_start = start_year or max(1900, resolved_end - max_years + 1)
        if not 1900 <= resolved_start <= resolved_end <= current_year:
            raise ValueError("Invalid BLS year range")
        if resolved_end - resolved_start + 1 > max_years:
            raise ValueError(
                f"BLS request spans at most {max_years} years with the configured access level"
            )

        cache_key = (normalized, resolved_start, resolved_end)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        request: dict[str, object] = {
            "seriesid": [normalized],
            "startyear": str(resolved_start),
            "endyear": str(resolved_end),
        }
        endpoint = self.v1_url
        if self.settings.bls_api_key:
            endpoint = self.v2_url
            request["registrationkey"] = self.settings.bls_api_key
            request["catalog"] = True
        response = self.client.post(endpoint, json=request)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise LookupError("Unexpected BLS response")
        if payload.get("status") != "REQUEST_SUCCEEDED":
            messages = payload.get("message") or []
            detail = "; ".join(str(message) for message in messages) or "BLS request failed"
            raise LookupError(detail)

        rows = self._series_rows(payload)
        if not rows:
            raise LookupError(f"No BLS series data returned for {normalized}")
        row = rows[0]
        observations: list[BlsObservation] = []
        raw_data = row.get("data", [])
        if isinstance(raw_data, list):
            for item in raw_data:
                if not isinstance(item, dict):
                    continue
                try:
                    year = int(str(item["year"]))
                    period = str(item["period"])
                    value = float(str(item["value"]))
                except (KeyError, TypeError, ValueError):
                    continue
                footnotes = [
                    str(footnote.get("text"))
                    for footnote in item.get("footnotes", [])
                    if isinstance(footnote, dict) and footnote.get("text")
                ]
                observations.append(
                    BlsObservation(
                        year=year,
                        period=period,
                        period_name=str(item.get("periodName") or period),
                        date=self._observation_date(year, period),
                        value=value,
                        footnotes=footnotes,
                    )
                )
        observations.sort(key=lambda item: (item.date or date(item.year, 12, 31), item.period))
        if not observations:
            raise LookupError(f"No numeric BLS observations returned for {normalized}")

        catalog_item = _CATALOG_BY_ID.get(normalized)
        raw_catalog = row.get("catalog")
        catalog: dict[str, Any] = raw_catalog if isinstance(raw_catalog, dict) else {}
        title = (
            catalog_item.title
            if catalog_item
            else str(catalog.get("series_title") or catalog.get("seriesTitle") or normalized)
        )
        unit = catalog_item.unit if catalog_item else None
        seasonal = catalog_item.seasonal_adjustment if catalog_item else None
        result = BlsSeries(
            series_id=normalized,
            title=title,
            unit=unit,
            seasonal_adjustment=seasonal,
            observations=observations,
            provenance=self._provenance(datetime.now(UTC), normalized),
        )
        self._cache[cache_key] = result
        return result
