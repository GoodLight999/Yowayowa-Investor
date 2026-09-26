from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import httpx

from yowayowa.config import Settings
from yowayowa.domain import LicenseClass, Provenance
from yowayowa.providers.base import enforce_source_policy


class FredClient:
    base_url = "https://api.stlouisfed.org/fred"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        enforce_source_policy("fred", mode=settings.mode)
        self.client = httpx.Client(timeout=settings.request_timeout_seconds)

    def _key(self) -> str:
        if not self.settings.fred_api_key:
            raise RuntimeError("FRED requires YOWAYOWA_FRED_API_KEY")
        return self.settings.fred_api_key

    def _get(self, path: str, **params: object) -> dict[str, Any]:
        query: dict[str, str | int | float] = {
            "api_key": self._key(),
            "file_type": "json",
        }
        for key, value in params.items():
            if value is None:
                continue
            if isinstance(value, bool):
                query[key] = "true" if value else "false"
            elif isinstance(value, (str, int, float)):
                query[key] = value
            else:
                query[key] = str(value)
        response = self.client.get(
            f"{self.base_url}/{path.lstrip('/')}",
            params=query,
        )
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    def search(self, query: str, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        payload = self._get(
            "series/search",
            search_text=query,
            search_type="full_text",
            order_by="search_rank",
            sort_order="asc",
            limit=limit,
            offset=offset,
        )
        now = datetime.now(UTC)
        return {
            "query": query,
            "series": payload.get("seriess", []),
            "count": payload.get("count"),
            "offset": payload.get("offset", offset),
            "limit": payload.get("limit", limit),
            "provenance": self._provenance(now).model_dump(mode="json"),
        }

    def series_info(self, series_id: str) -> dict[str, Any]:
        payload = self._get("series", series_id=series_id)
        rows = payload.get("seriess", [])
        now = datetime.now(UTC)
        return {
            "series_id": series_id,
            "metadata": rows[0] if isinstance(rows, list) and rows else None,
            "provenance": self._provenance(now, series_id).model_dump(mode="json"),
        }

    def series(
        self,
        series_id: str,
        limit: int = 5000,
        observation_start: date | None = None,
        observation_end: date | None = None,
        units: str | None = None,
        frequency: str | None = None,
        aggregation_method: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, object] = {"series_id": series_id, "limit": limit}
        if observation_start:
            params["observation_start"] = observation_start.isoformat()
        if observation_end:
            params["observation_end"] = observation_end.isoformat()
        if units:
            params["units"] = units
        if frequency:
            params["frequency"] = frequency
        if aggregation_method:
            params["aggregation_method"] = aggregation_method
        observations = self._get("series/observations", **params)
        metadata = self._get("series", series_id=series_id)
        now = datetime.now(UTC)
        return {
            "series_id": series_id,
            "metadata": (metadata.get("seriess") or [None])[0],
            "observations": observations.get("observations", []),
            "provenance": self._provenance(now, series_id).model_dump(mode="json"),
        }

    def release_dates(self, limit: int = 100, offset: int = 0) -> dict[str, Any]:
        payload = self._get(
            "releases/dates",
            limit=limit,
            offset=offset,
            order_by="release_date",
            sort_order="desc",
            include_release_dates_with_no_data="false",
        )
        now = datetime.now(UTC)
        return {
            "release_dates": payload.get("release_dates", []),
            "count": payload.get("count"),
            "offset": payload.get("offset", offset),
            "limit": payload.get("limit", limit),
            "provenance": self._provenance(now).model_dump(mode="json"),
        }

    @staticmethod
    def _provenance(now: datetime, series_id: str | None = None) -> Provenance:
        source_url = (
            f"https://fred.stlouisfed.org/series/{series_id}"
            if series_id
            else "https://fred.stlouisfed.org/"
        )
        return Provenance(
            provider="fred",
            source="Federal Reserve Economic Data",
            source_url=source_url,
            license_class=LicenseClass.USER_KEY,
            retrieved_at=now,
            as_of=now,
            notes=[
                "Personal-mode BYOK source. FRED series may be owned by third parties and carry "
                "series-specific copyright restrictions; generic public redistribution is blocked."
            ],
        )
