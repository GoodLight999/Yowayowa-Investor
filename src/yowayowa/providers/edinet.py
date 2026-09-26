from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from threading import Lock
from typing import Any
from zipfile import BadZipFile, ZipFile

import httpx
from cachetools import TTLCache

from yowayowa.config import Settings
from yowayowa.domain import LicenseClass, Provenance
from yowayowa.edinet_models import EdinetFact
from yowayowa.providers.base import enforce_source_policy

_DOC_ID = re.compile(r"^[A-Z0-9]{8}$")
_MAX_CSV_FILES = 64
_MAX_CSV_FILE_BYTES = 32 * 1024 * 1024
_MAX_TOTAL_CSV_BYTES = 96 * 1024 * 1024


@dataclass(frozen=True)
class EdinetCsvPayload:
    facts: list[EdinetFact]
    source_files: list[str]
    parse_warnings: list[str]


class EdinetClient:
    base_url = "https://api.edinet-fsa.go.jp/api/v2"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        enforce_source_policy("edinet-v2", mode=settings.mode)
        self.client = httpx.Client(timeout=settings.request_timeout_seconds)
        self._csv_cache: TTLCache[str, EdinetCsvPayload] = TTLCache(
            maxsize=24,
            ttl=settings.cache_ttl_seconds,
        )
        self._cache_lock = Lock()

    def _api_key(self) -> str:
        if not self.settings.edinet_api_key:
            raise RuntimeError("EDINET requires YOWAYOWA_EDINET_API_KEY")
        return self.settings.edinet_api_key

    @staticmethod
    def normalize_doc_id(doc_id: str) -> str:
        normalized = doc_id.strip().upper()
        if not _DOC_ID.fullmatch(normalized):
            raise ValueError("EDINET document ID must be eight ASCII letters/digits")
        return normalized

    @staticmethod
    def _provenance(*, as_of: datetime | date | None, source_url: str) -> Provenance:
        return Provenance(
            provider="edinet-v2",
            source="EDINET API Version 2",
            source_url=source_url,
            license_class=LicenseClass.OFFICIAL_PUBLIC,
            retrieved_at=datetime.now(UTC),
            as_of=as_of,
            notes=[
                "EDINET API Version 2 requires a registered API key for access.",
                "EDINET content is used under Public Data License 1.0; Yowayowa-derived outputs "
                "retain source attribution and identify processing.",
                "The official type=5 XBRL-to-CSV conversion is UTF-16LE tab-separated data. "
                "Yowayowa normalizes those rows rather than reproducing EDINET taxonomy assets.",
                "EDINET's CSV conversion truncates very long narrative instance values; use the "
                "original XBRL package when full narrative text is required.",
            ],
        )

    def documents(self, filing_date: date) -> dict[str, Any]:
        response = self.client.get(
            f"{self.base_url}/documents.json",
            params={
                "date": filing_date.isoformat(),
                "type": 2,
                "Subscription-Key": self._api_key(),
            },
        )
        response.raise_for_status()
        payload = response.json()
        return {
            "metadata": payload.get("metadata", {}),
            "results": payload.get("results", []),
            "provenance": self._provenance(
                as_of=filing_date,
                source_url="https://disclosure2.edinet-fsa.go.jp/",
            ).model_dump(mode="json"),
        }

    def document_csv_archive(self, doc_id: str) -> bytes:
        normalized = self.normalize_doc_id(doc_id)
        response = self.client.get(
            f"{self.base_url}/documents/{normalized}",
            params={"type": 5, "Subscription-Key": self._api_key()},
        )
        response.raise_for_status()
        return response.content

    def csv_facts(self, doc_id: str) -> EdinetCsvPayload:
        normalized = self.normalize_doc_id(doc_id)
        with self._cache_lock:
            cached = self._csv_cache.get(normalized)
        if cached is not None:
            return cached

        payload = self._parse_csv_archive(self.document_csv_archive(normalized))
        with self._cache_lock:
            self._csv_cache[normalized] = payload
        return payload

    @staticmethod
    def _parse_csv_archive(archive: bytes) -> EdinetCsvPayload:
        try:
            zipped = ZipFile(io.BytesIO(archive))
        except BadZipFile as exc:
            raise ValueError("EDINET returned an invalid type=5 CSV archive") from exc

        with zipped:
            infos = [
                info
                for info in zipped.infolist()
                if not info.is_dir()
                and info.filename.replace("\\", "/").lower().endswith(".csv")
                and "xbrl_to_csv/" in info.filename.replace("\\", "/").lower()
            ]
            if not infos:
                raise LookupError("The EDINET document has no XBRL-to-CSV files")
            if len(infos) > _MAX_CSV_FILES:
                raise ValueError("EDINET CSV archive contains an unexpected number of files")
            if any(info.file_size > _MAX_CSV_FILE_BYTES for info in infos):
                raise ValueError("EDINET CSV archive contains an unexpectedly large file")
            if sum(info.file_size for info in infos) > _MAX_TOTAL_CSV_BYTES:
                raise ValueError("EDINET CSV archive is unexpectedly large")

            facts: list[EdinetFact] = []
            warnings: list[str] = []
            source_files: list[str] = []
            malformed_rows = 0
            for info in sorted(infos, key=lambda item: item.filename):
                source_name = info.filename.replace("\\", "/")
                source_files.append(source_name)
                raw = zipped.read(info)
                text = raw.decode("utf-16-le").lstrip("\ufeff")
                reader = csv.reader(io.StringIO(text, newline=""), delimiter="\t", quotechar='"')
                header = next(reader, None)
                if header is None or len(header) < 9:
                    warnings.append(f"{source_name}: missing the nine-column EDINET CSV header")
                    continue
                for row in reader:
                    if not row or not any(cell.strip() for cell in row):
                        continue
                    if len(row) < 9:
                        malformed_rows += 1
                        continue
                    facts.append(
                        EdinetFact(
                            source_file=source_name,
                            element_id=row[0].strip(),
                            label=row[1].strip(),
                            context_id=row[2].strip(),
                            relative_year=row[3].strip() or None,
                            consolidation=row[4].strip() or None,
                            period_type=row[5].strip() or None,
                            unit_id=row[6].strip() or None,
                            unit=row[7].strip() or None,
                            value=row[8],
                        )
                    )
            if malformed_rows:
                warnings.append(f"Skipped {malformed_rows} malformed EDINET CSV row(s).")
            if not facts:
                raise LookupError("The EDINET CSV archive contained no parseable XBRL facts")
            return EdinetCsvPayload(
                facts=facts,
                source_files=source_files,
                parse_warnings=warnings,
            )

    def provenance_for_document(self, doc_id: str) -> Provenance:
        self.normalize_doc_id(doc_id)
        return self._provenance(
            as_of=datetime.now(UTC),
            source_url="https://disclosure2.edinet-fsa.go.jp/",
        )
