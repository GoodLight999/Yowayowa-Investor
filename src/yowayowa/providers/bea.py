from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

import httpx
from cachetools import TTLCache

from yowayowa.bea_models import (
    BeaNipaCatalog,
    BeaNipaCatalogItem,
    BeaNipaRow,
    BeaNipaTable,
)
from yowayowa.config import Settings
from yowayowa.domain import LicenseClass, Provenance
from yowayowa.providers.base import enforce_source_policy

BEA_NIPA_CATALOG: tuple[BeaNipaCatalogItem, ...] = (
    BeaNipaCatalogItem(
        table_name="T10101",
        title="Real GDP — percent change from preceding period",
        category="Output",
        default_frequency="Q",
        default_line_number=1,
    ),
    BeaNipaCatalogItem(
        table_name="T20305",
        title="Personal consumption expenditures by major type of product",
        category="Consumption",
        default_frequency="Q",
        default_line_number=1,
    ),
)

_TABLE_PATTERN = re.compile(r"^T\d{5}$")


class BeaClient:
    base_url = "https://apps.bea.gov/api/data/"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        enforce_source_policy("bea", mode=settings.mode)
        self.client = httpx.Client(timeout=settings.request_timeout_seconds)
        self._cache: TTLCache[tuple[str, str, str, int | None], BeaNipaTable] = TTLCache(
            maxsize=128,
            ttl=max(3600, settings.cache_ttl_seconds),
        )

    def _key(self) -> str:
        if not self.settings.bea_api_key:
            raise RuntimeError("BEA requires YOWAYOWA_BEA_API_KEY")
        return self.settings.bea_api_key

    @staticmethod
    def _provenance(now: datetime, table_name: str | None = None) -> Provenance:
        source_url = (
            f"https://apps.bea.gov/iTable/?ReqID=19&step=3&isuri=1&nipa_table_list={table_name}"
            if table_name
            else "https://apps.bea.gov/api/"
        )
        return Provenance(
            provider="bea",
            source="U.S. Bureau of Economic Analysis Data API",
            source_url=source_url,
            license_class=LicenseClass.OFFICIAL_PUBLIC,
            retrieved_at=now,
            as_of=now,
            notes=[
                "Source: U.S. Bureau of Economic Analysis.",
                "BEA-published data are treated as public domain except material identified as "
                "third-party copyrighted content. Yowayowa transformations are its own "
                "analysis.",
            ],
        )

    def catalog(self) -> BeaNipaCatalog:
        return BeaNipaCatalog(
            tables=list(BEA_NIPA_CATALOG),
            provenance=self._provenance(datetime.now(UTC)),
        )

    @staticmethod
    def _normalize_years(years: list[int] | None) -> tuple[list[str], str]:
        if not years:
            current = datetime.now(UTC).year
            resolved = [str(year) for year in range(max(1947, current - 9), current + 1)]
            return resolved, ",".join(resolved)
        unique = sorted(set(years))
        current = datetime.now(UTC).year
        if len(unique) > 50 or any(year < 1929 or year > current for year in unique):
            raise ValueError(
                "BEA years must contain at most 50 values between 1929 and the current year"
            )
        resolved = [str(year) for year in unique]
        return resolved, ",".join(resolved)

    @staticmethod
    def _number(value: object) -> float | None:
        if value is None:
            return None
        text = str(value).strip().replace(",", "")
        if not text or text in {"---", "(NA)", "NA"}:
            return None
        try:
            return float(text)
        except ValueError:
            return None

    @staticmethod
    def _integer(value: object) -> int | None:
        try:
            return int(str(value))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _results(payload: dict[str, Any]) -> dict[str, Any]:
        root = payload.get("BEAAPI")
        if not isinstance(root, dict):
            raise LookupError("Unexpected BEA response")
        results = root.get("Results")
        if not isinstance(results, dict):
            raise LookupError("Unexpected BEA results")
        error = results.get("Error")
        if error:
            if isinstance(error, dict):
                detail = error.get("APIErrorDescription") or error.get("APIErrorCode") or error
            else:
                detail = error
            raise LookupError(f"BEA API error: {detail}")
        return results

    def nipa(
        self,
        table_name: str,
        *,
        frequency: str = "Q",
        years: list[int] | None = None,
        line_number: int | None = None,
    ) -> BeaNipaTable:
        normalized_table = table_name.strip().upper()
        if not _TABLE_PATTERN.fullmatch(normalized_table):
            raise ValueError("BEA NIPA table name must look like T10101")
        normalized_frequency = frequency.strip().upper()
        if normalized_frequency not in {"A", "Q", "M"}:
            raise ValueError("BEA NIPA frequency must be A, Q, or M")
        if line_number is not None and line_number < 1:
            raise ValueError("BEA line_number must be positive")
        resolved_years, years_param = self._normalize_years(years)
        cache_key = (normalized_table, normalized_frequency, years_param, line_number)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        response = self.client.get(
            self.base_url,
            params={
                "UserID": self._key(),
                "method": "GetData",
                "DataSetName": "NIPA",
                "TableName": normalized_table,
                "Frequency": normalized_frequency,
                "Year": years_param,
                "ResultFormat": "JSON",
            },
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise LookupError("Unexpected BEA response")
        results = self._results(payload)
        raw_rows = results.get("Data", [])
        rows: list[BeaNipaRow] = []
        if isinstance(raw_rows, list):
            for item in raw_rows:
                if not isinstance(item, dict):
                    continue
                parsed_line = self._integer(item.get("LineNumber"))
                if line_number is not None and parsed_line != line_number:
                    continue
                description = str(item.get("LineDescription") or "").strip()
                time_period = str(item.get("TimePeriod") or "").strip()
                if not description or not time_period:
                    continue
                rows.append(
                    BeaNipaRow(
                        table_name=str(item.get("TableName") or normalized_table),
                        series_code=(str(item["SeriesCode"]) if item.get("SeriesCode") else None),
                        line_number=parsed_line,
                        line_description=description,
                        time_period=time_period,
                        metric_name=(str(item["METRIC_NAME"]) if item.get("METRIC_NAME") else None),
                        unit=str(item["CL_UNIT"]) if item.get("CL_UNIT") else None,
                        unit_mult=self._integer(item.get("UNIT_MULT")),
                        value=self._number(item.get("DataValue")),
                        note_ref=str(item["NoteRef"]) if item.get("NoteRef") else None,
                    )
                )
        if not rows:
            raise LookupError(f"No BEA NIPA data returned for {normalized_table}")

        notes: list[str] = []
        raw_notes = results.get("Notes", [])
        if isinstance(raw_notes, list):
            for note in raw_notes:
                if isinstance(note, dict) and note.get("NoteText"):
                    notes.append(str(note["NoteText"]))

        result = BeaNipaTable(
            table_name=normalized_table,
            frequency=normalized_frequency,
            years=resolved_years,
            rows=rows,
            notes=notes,
            provenance=self._provenance(datetime.now(UTC), normalized_table),
        )
        self._cache[cache_key] = result
        return result
