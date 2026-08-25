from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from xml.etree import ElementTree

import httpx
from cachetools import TTLCache

from yowayowa.config import Settings
from yowayowa.domain import LicenseClass, Provenance
from yowayowa.institutional_models import InstitutionalHolding, ThirteenFFiling


@dataclass(frozen=True, slots=True)
class FilingReference:
    accession_number: str
    form: str
    filing_date: date
    report_date: date
    primary_document: str


class Sec13FProvider:
    submissions_base = "https://data.sec.gov/submissions"
    archives_base = "https://www.sec.gov/Archives/edgar/data"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        user_agent = str(
            getattr(
                settings,
                "sec_user_agent",
                "Yowayowa-Investor personal research contact@example.invalid",
            )
        )
        self.client = httpx.Client(
            timeout=settings.request_timeout_seconds,
            headers={
                "User-Agent": user_agent,
                "Accept-Encoding": "gzip, deflate",
            },
        )
        ttl = max(300, settings.cache_ttl_seconds)
        self._submissions_cache: TTLCache[str, dict[str, Any]] = TTLCache(
            maxsize=128,
            ttl=ttl,
        )
        self._filing_cache: TTLCache[str, ThirteenFFiling] = TTLCache(
            maxsize=256,
            ttl=ttl,
        )

    @staticmethod
    def normalize_cik(cik: str | int) -> str:
        raw = str(cik).strip().removeprefix("CIK").strip()
        if not raw.isdigit() or len(raw) > 10:
            raise ValueError("SEC CIK must contain at most 10 digits")
        return raw.zfill(10)

    def submissions(self, cik: str | int) -> dict[str, Any]:
        normalized = self.normalize_cik(cik)
        cached = self._submissions_cache.get(normalized)
        if cached is not None:
            return cached
        response = self.client.get(f"{self.submissions_base}/CIK{normalized}.json")
        if response.status_code == 404:
            raise LookupError(f"SEC registrant CIK {normalized} was not found")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise LookupError(f"Unexpected SEC submissions payload for CIK {normalized}")
        self._submissions_cache[normalized] = payload
        return payload

    @staticmethod
    def _recent_references(payload: dict[str, Any]) -> list[FilingReference]:
        filings = payload.get("filings")
        if not isinstance(filings, dict):
            return []
        recent = filings.get("recent")
        if not isinstance(recent, dict):
            return []
        forms = recent.get("form")
        accessions = recent.get("accessionNumber")
        filing_dates = recent.get("filingDate")
        report_dates = recent.get("reportDate")
        primary_documents = recent.get("primaryDocument")
        if not isinstance(forms, list):
            return []
        if not isinstance(accessions, list):
            return []
        if not isinstance(filing_dates, list):
            return []
        if not isinstance(report_dates, list):
            return []
        if not isinstance(primary_documents, list):
            return []
        refs: list[FilingReference] = []
        for form, accession, filing_date, report_date, primary_document in zip(
            forms,
            accessions,
            filing_dates,
            report_dates,
            primary_documents,
            strict=False,
        ):
            if str(form) not in {"13F-HR", "13F-HR/A"}:
                continue
            try:
                refs.append(
                    FilingReference(
                        accession_number=str(accession),
                        form=str(form),
                        filing_date=date.fromisoformat(str(filing_date)),
                        report_date=date.fromisoformat(str(report_date)),
                        primary_document=str(primary_document),
                    )
                )
            except ValueError:
                continue
        by_report_date: dict[date, FilingReference] = {}
        for ref in refs:
            previous = by_report_date.get(ref.report_date)
            if previous is None or (ref.filing_date, ref.form.endswith("/A")) > (
                previous.filing_date,
                previous.form.endswith("/A"),
            ):
                by_report_date[ref.report_date] = ref
        return sorted(
            by_report_date.values(),
            key=lambda item: (item.report_date, item.filing_date),
            reverse=True,
        )

    def recent_references(
        self,
        cik: str | int,
        limit: int = 2,
    ) -> tuple[str, list[FilingReference]]:
        if not 1 <= limit <= 8:
            raise ValueError("13F filing limit must be between 1 and 8")
        payload = self.submissions(cik)
        name = str(payload.get("name") or f"CIK {self.normalize_cik(cik)}")
        refs = self._recent_references(payload)
        if not refs:
            raise LookupError(f"No recent 13F-HR filings found for {name}")
        return name, refs[:limit]

    @staticmethod
    def _local_name(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    @classmethod
    def _descendant_text(
        cls,
        element: ElementTree.Element,
        name: str,
    ) -> str | None:
        for child in element.iter():
            if cls._local_name(child.tag).casefold() == name.casefold():
                text = (child.text or "").strip()
                return text or None
        return None

    @classmethod
    def parse_information_table(cls, xml: str) -> list[InstitutionalHolding]:
        root = ElementTree.fromstring(xml)
        holdings: list[InstitutionalHolding] = []
        for element in root.iter():
            if cls._local_name(element.tag).casefold() != "infotable":
                continue
            issuer = cls._descendant_text(element, "nameOfIssuer")
            cusip = cls._descendant_text(element, "cusip")
            raw_value = cls._descendant_text(element, "value")
            raw_amount = cls._descendant_text(element, "sshPrnamt")
            if not issuer or not cusip or raw_value is None or raw_amount is None:
                continue
            try:
                value_thousands = int(float(raw_value))
                amount = float(raw_amount)
            except ValueError:
                continue
            holdings.append(
                InstitutionalHolding(
                    issuer=issuer,
                    title_of_class=cls._descendant_text(element, "titleOfClass"),
                    cusip=cusip.upper(),
                    reported_value_thousands=value_thousands,
                    value_usd=value_thousands * 1000,
                    shares_or_principal=amount,
                    amount_type=cls._descendant_text(element, "sshPrnamtType"),
                    put_call=cls._descendant_text(element, "putCall"),
                    investment_discretion=cls._descendant_text(
                        element,
                        "investmentDiscretion",
                    ),
                    voting_sole=cls._optional_float(cls._descendant_text(element, "Sole")),
                    voting_shared=cls._optional_float(cls._descendant_text(element, "Shared")),
                    voting_none=cls._optional_float(cls._descendant_text(element, "None")),
                )
            )
        total = sum(item.value_usd for item in holdings)
        if total > 0:
            holdings = [
                item.model_copy(update={"weight": item.value_usd / total}) for item in holdings
            ]
        return sorted(holdings, key=lambda item: item.value_usd, reverse=True)

    @staticmethod
    def _optional_float(value: str | None) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except ValueError:
            return None

    def _filing_directory(self, cik: str, accession_number: str) -> str:
        accession = accession_number.replace("-", "")
        return f"{self.archives_base}/{int(cik)}/{accession}"

    def _information_table_xml(
        self,
        cik: str,
        ref: FilingReference,
    ) -> tuple[str, str]:
        directory = self._filing_directory(cik, ref.accession_number)
        index_response = self.client.get(f"{directory}/index.json")
        index_response.raise_for_status()
        index_payload = index_response.json()
        directory_payload = (
            index_payload.get("directory") if isinstance(index_payload, dict) else None
        )
        items = directory_payload.get("item") if isinstance(directory_payload, dict) else None
        names = [str(item.get("name")) for item in items or [] if isinstance(item, dict)]
        xml_names = [name for name in names if name.lower().endswith(".xml")]
        primary_lower = ref.primary_document.lower()
        candidates = sorted(
            xml_names,
            key=lambda name: (
                "infotable" not in name.lower() and "informationtable" not in name.lower(),
                name.lower() == primary_lower,
                name,
            ),
        )
        if ref.primary_document and ref.primary_document not in candidates:
            candidates.append(ref.primary_document)
        for name in candidates[:6]:
            response = self.client.get(f"{directory}/{name}")
            if response.status_code >= 400:
                continue
            try:
                holdings = self.parse_information_table(response.text)
            except ElementTree.ParseError:
                continue
            if holdings:
                return response.text, f"{directory}/{name}"
        raise LookupError(f"13F information table XML not found for {ref.accession_number}")

    def filing(self, cik: str | int, ref: FilingReference) -> ThirteenFFiling:
        normalized = self.normalize_cik(cik)
        cached = self._filing_cache.get(ref.accession_number)
        if cached is not None:
            return cached
        xml, source_url = self._information_table_xml(normalized, ref)
        holdings = self.parse_information_table(xml)
        total = sum(item.value_usd for item in holdings)
        retrieved_at = datetime.now(UTC)
        filing = ThirteenFFiling(
            accession_number=ref.accession_number,
            form=ref.form,
            filing_date=ref.filing_date,
            report_date=ref.report_date,
            primary_document=ref.primary_document,
            source_url=source_url,
            holdings=holdings,
            total_value_usd=total,
            provenance=Provenance(
                provider="sec-edgar",
                source="SEC EDGAR Form 13F information table",
                source_url=source_url,
                license_class=LicenseClass.OFFICIAL_PUBLIC,
                retrieved_at=retrieved_at,
                as_of=ref.report_date,
                notes=[
                    "Form 13F positions are reported as of the quarter-end report date, "
                    "not as current holdings.",
                    "The information-table value field is reported in thousands of U.S. "
                    "dollars and is converted to dollars here.",
                ],
            ),
        )
        self._filing_cache[ref.accession_number] = filing
        return filing
