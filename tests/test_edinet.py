from __future__ import annotations

import csv
import io
from datetime import date
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from yowayowa.config import Settings
from yowayowa.domain import LicenseClass
from yowayowa.providers.edinet import EdinetClient
from yowayowa.services.edinet import document_list, financials, search_facts


class FakeResponse:
    def __init__(self, *, payload: dict[str, Any] | None = None, content: bytes = b"") -> None:
        self._payload = payload
        self.content = content

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        assert self._payload is not None
        return self._payload


class FakeHttpClient:
    def __init__(self, documents_payload: dict[str, Any], archive: bytes) -> None:
        self.documents_payload = documents_payload
        self.archive = archive
        self.calls: list[tuple[str, dict[str, object]]] = []

    def get(self, url: str, params: dict[str, object]) -> FakeResponse:
        self.calls.append((url, params))
        if url.endswith("/documents.json"):
            return FakeResponse(payload=self.documents_payload)
        return FakeResponse(content=self.archive)


def _archive(rows: list[list[str]]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, delimiter="\t", quotechar='"', lineterminator="\r\n")
    writer.writerow(
        [
            "要素ID",
            "項目名",
            "コンテキストID",
            "相対年度",
            "連結・個別",
            "期間・時点",
            "ユニットID",
            "単位",
            "値",
        ]
    )
    writer.writerows(rows)
    encoded = b"\xff\xfe" + stream.getvalue().encode("utf-16-le")
    output = io.BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as zipped:
        zipped.writestr("XBRL_TO_CSV/jpcrp030000-asr.csv", encoded)
    return output.getvalue()


def _rows() -> list[list[str]]:
    return [
        [
            "jpdei_cor:FilerNameInJapaneseDEI",
            "提出者名",
            "FilingDateInstant",
            "当期",
            "",
            "時点",
            "",
            "",
            "株式会社テスト",
        ],
        [
            "jpdei_cor:EDINETCodeDEI",
            "EDINETコード",
            "FilingDateInstant",
            "当期",
            "",
            "時点",
            "",
            "",
            "E00001",
        ],
        [
            "jpdei_cor:SecurityCodeDEI",
            "証券コード",
            "FilingDateInstant",
            "当期",
            "",
            "時点",
            "",
            "",
            "72030",
        ],
        [
            "jpdei_cor:AccountingStandardsDEI",
            "会計基準",
            "FilingDateInstant",
            "当期",
            "",
            "時点",
            "",
            "",
            "Japan GAAP",
        ],
        [
            "jpdei_cor:DocumentTypeDEI",
            "書類種別",
            "FilingDateInstant",
            "当期",
            "",
            "時点",
            "",
            "",
            "有価証券報告書",
        ],
        [
            "jpdei_cor:CurrentPeriodStartDateDEI",
            "当期首",
            "FilingDateInstant",
            "当期",
            "",
            "時点",
            "",
            "",
            "2025-04-01",
        ],
        [
            "jpdei_cor:CurrentPeriodEndDateDEI",
            "当期末",
            "FilingDateInstant",
            "当期",
            "",
            "時点",
            "",
            "",
            "2026-03-31",
        ],
        [
            "jppfs_cor:NetSales",
            "売上高",
            "Prior1YearDuration",
            "前期",
            "連結",
            "期間",
            "JPY",
            "円",
            "900000000000",
        ],
        [
            "jppfs_cor:NetSales",
            "売上高",
            "CurrentYearDuration",
            "当期",
            "連結",
            "期間",
            "JPY",
            "円",
            "1000000000000",
        ],
        [
            "jppfs_cor:NetSales",
            "売上高",
            "CurrentYearDuration_NonConsolidatedMember",
            "当期",
            "個別",
            "期間",
            "JPY",
            "円",
            "700000000000",
        ],
        [
            "jppfs_cor:OperatingIncome",
            "営業利益",
            "CurrentYearDuration",
            "当期",
            "連結",
            "期間",
            "JPY",
            "円",
            "123000000000",
        ],
        [
            "jppfs_cor:Assets",
            "資産合計",
            "CurrentYearInstant",
            "当期",
            "連結",
            "時点",
            "JPY",
            "円",
            "2500000000000",
        ],
        [
            "jppfs_cor:NetCashProvidedByUsedInOperatingActivities",
            "営業活動によるキャッシュ・フロー",
            "CurrentYearDuration",
            "当期",
            "連結",
            "期間",
            "JPY",
            "円",
            "150000000000",
        ],
        [
            "jppfs_cor:DilutedEarningsPerShare",
            "潜在株式調整後1株当たり当期純利益",
            "CurrentYearDuration",
            "当期",
            "連結",
            "期間",
            "JPYPerShare",
            "円",
            "123.45",
        ],
        [
            "jpcrp_cor:BusinessPolicyTextBlock",
            "事業方針",
            "FilingDateInstant",
            "当期",
            "",
            "時点",
            "",
            "",
            "長いテキスト",
        ],
    ]


def _documents_payload() -> dict[str, Any]:
    return {
        "metadata": {"status": "200"},
        "results": [
            {
                "docID": "S100TEST",
                "edinetCode": "E00001",
                "secCode": "72030",
                "filerName": "株式会社テスト",
                "docTypeCode": "120",
                "docDescription": "有価証券報告書",
                "periodStart": "2025-04-01",
                "periodEnd": "2026-03-31",
                "submitDateTime": "2026-06-20 10:30",
                "xbrlFlag": "1",
                "csvFlag": "1",
                "withdrawalStatus": "0",
                "legalStatus": "1",
            },
            {
                "docID": "S100OTHR",
                "edinetCode": "E99999",
                "secCode": "99990",
                "filerName": "別会社",
                "docTypeCode": "130",
                "submitDateTime": "2026-06-20 09:00",
                "xbrlFlag": "1",
                "csvFlag": "0",
                "withdrawalStatus": "0",
                "legalStatus": "1",
            },
        ],
    }


def _client() -> tuple[EdinetClient, FakeHttpClient]:
    client = EdinetClient(Settings(mode="personal", edinet_api_key="test-key"))
    fake = FakeHttpClient(_documents_payload(), _archive(_rows()))
    client.client = fake  # type: ignore[assignment]
    return client, fake


def test_edinet_document_list_filters_security_code_and_csv() -> None:
    client, fake = _client()

    result = document_list(
        client,
        date(2026, 6, 20),
        security_code="7203",
        csv_only=True,
    )

    assert result.total_count == 2
    assert result.matched_count == 1
    assert result.documents[0].doc_id == "S100TEST"
    assert result.documents[0].security_code == "72030"
    assert result.documents[0].csv_available is True
    assert result.provenance.license_class == LicenseClass.OFFICIAL_PUBLIC
    assert fake.calls[0][1]["Subscription-Key"] == "test-key"


def test_edinet_financials_normalize_official_csv_and_prioritize_current_consolidated() -> None:
    client, fake = _client()

    result = financials(client, "s100test")

    assert result.doc_id == "S100TEST"
    assert result.company_name == "株式会社テスト"
    assert result.edinet_code == "E00001"
    assert result.security_code == "72030"
    assert result.accounting_standard == "Japan GAAP"
    assert result.period_start == date(2025, 4, 1)
    assert result.period_end == date(2026, 3, 31)
    assert result.metrics["revenue"][0].value == "1000000000000"
    assert result.metrics["revenue"][0].context_id == "CurrentYearDuration"
    assert result.metrics["revenue"][0].numeric_value == 1_000_000_000_000
    assert result.metrics["operating_income"][0].numeric_value == 123_000_000_000
    assert result.metrics["assets"][0].numeric_value == 2_500_000_000_000
    assert float(result.metrics["eps_diluted"][0].numeric_value or 0) == pytest.approx(123.45)
    assert "liabilities" in result.unavailable_metrics
    assert result.fact_count == len(_rows())
    assert result.provenance.provider == "edinet-v2"
    assert fake.calls[-1][1]["type"] == 5


def test_edinet_csv_payload_is_cached_and_raw_fact_search_is_bounded() -> None:
    client, fake = _client()

    first = search_facts(client, "S100TEST", query="売上高", limit=2)
    second = search_facts(client, "S100TEST", query="OperatingIncome", limit=10)

    archive_calls = [call for call in fake.calls if "/documents/" in call[0]]
    assert len(archive_calls) == 1
    assert first.matched_count == 3
    assert len(first.facts) == 2
    assert second.matched_count == 1
    assert second.facts[0].element_id == "jppfs_cor:OperatingIncome"


def test_edinet_rejects_invalid_document_id_and_non_zip_csv_response() -> None:
    client, _ = _client()
    with pytest.raises(ValueError, match="document ID"):
        client.document_csv_archive("../bad")

    with pytest.raises(ValueError, match="invalid type=5"):
        client._parse_csv_archive(b"not-a-zip")
