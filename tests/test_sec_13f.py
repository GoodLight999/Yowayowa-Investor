from datetime import date

import pytest

from yowayowa.config import Settings
from yowayowa.domain import LicenseClass
from yowayowa.institutional_models import InstitutionalHolding
from yowayowa.providers.sec_13f import FilingReference, Sec13FProvider
from yowayowa.services.institutional import _compare_holdings


def _settings() -> Settings:
    return Settings(database_url="sqlite:///:memory:")


XML = """<?xml version="1.0" encoding="UTF-8"?>
<informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">
  <infoTable>
    <nameOfIssuer>ALPHA INC</nameOfIssuer>
    <titleOfClass>COM</titleOfClass>
    <cusip>000000001</cusip>
    <value>1250</value>
    <shrsOrPrnAmt>
      <sshPrnamt>50000</sshPrnamt>
      <sshPrnamtType>SH</sshPrnamtType>
    </shrsOrPrnAmt>
    <investmentDiscretion>SOLE</investmentDiscretion>
    <votingAuthority>
      <Sole>50000</Sole><Shared>0</Shared><None>0</None>
    </votingAuthority>
  </infoTable>
  <infoTable>
    <nameOfIssuer>BETA CORP</nameOfIssuer>
    <titleOfClass>COM</titleOfClass>
    <cusip>000000002</cusip>
    <value>750</value>
    <putCall>Call</putCall>
    <shrsOrPrnAmt>
      <sshPrnamt>10000</sshPrnamt>
      <sshPrnamtType>SH</sshPrnamtType>
    </shrsOrPrnAmt>
    <investmentDiscretion>DFND</investmentDiscretion>
    <votingAuthority>
      <Sole>0</Sole><Shared>0</Shared><None>10000</None>
    </votingAuthority>
  </infoTable>
</informationTable>
"""


def holding(
    cusip: str,
    issuer: str,
    shares: float,
    value: int,
) -> InstitutionalHolding:
    return InstitutionalHolding(
        issuer=issuer,
        title_of_class="COM",
        cusip=cusip,
        reported_value_thousands=value // 1000,
        value_usd=value,
        shares_or_principal=shares,
        amount_type="SH",
    )


def test_13f_information_table_parser_converts_thousands_and_weights() -> None:
    holdings = Sec13FProvider.parse_information_table(XML)

    assert [item.issuer for item in holdings] == ["ALPHA INC", "BETA CORP"]
    alpha, beta = holdings
    assert alpha.value_usd == 1_250_000
    assert alpha.reported_value_thousands == 1250
    assert alpha.shares_or_principal == 50_000
    assert alpha.voting_sole == 50_000
    assert alpha.weight == pytest.approx(0.625)
    assert beta.value_usd == 750_000
    assert beta.put_call == "Call"
    assert beta.weight == pytest.approx(0.375)


def test_13f_parser_ignores_incomplete_rows() -> None:
    xml = """<informationTable>
    <infoTable><nameOfIssuer>NO CUSIP</nameOfIssuer></infoTable>
    </informationTable>"""
    assert Sec13FProvider.parse_information_table(xml) == []


def test_13f_comparison_classifies_position_changes() -> None:
    previous = [
        holding("A", "Alpha", 100, 100_000),
        holding("B", "Beta", 200, 200_000),
        holding("C", "Gamma", 300, 300_000),
    ]
    current = [
        holding("A", "Alpha", 150, 180_000),
        holding("B", "Beta", 120, 150_000),
        holding("D", "Delta", 50, 80_000),
    ]

    changes = _compare_holdings(current, previous)
    by_cusip = {item.cusip: item for item in changes}

    assert by_cusip["A"].status == "increased"
    assert by_cusip["A"].share_change == 50
    assert by_cusip["A"].share_change_fraction == pytest.approx(0.5)
    assert by_cusip["B"].status == "decreased"
    assert by_cusip["C"].status == "exited"
    assert by_cusip["C"].current_shares == 0
    assert by_cusip["D"].status == "new"
    assert by_cusip["D"].previous_shares == 0


def test_13f_recent_reference_prefers_latest_amendment() -> None:
    payload = {
        "filings": {
            "recent": {
                "form": ["13F-HR", "13F-HR/A", "10-K"],
                "accessionNumber": [
                    "0001-26-000001",
                    "0001-26-000002",
                    "0001-26-000003",
                ],
                "filingDate": ["2026-05-10", "2026-05-20", "2026-05-21"],
                "reportDate": ["2026-03-31", "2026-03-31", "2025-12-31"],
                "primaryDocument": ["primary.xml", "primary_a.xml", "annual.htm"],
            }
        }
    }

    refs = Sec13FProvider._recent_references(payload)

    assert len(refs) == 1
    assert refs[0].form == "13F-HR/A"
    assert refs[0].filing_date == date(2026, 5, 20)


def test_13f_normalize_cik_strips_prefix_and_zero_pads() -> None:
    assert Sec13FProvider.normalize_cik("CIK0001234567") == "0001234567"
    assert Sec13FProvider.normalize_cik("123") == "0000000123"
    assert Sec13FProvider.normalize_cik(1234567) == "0001234567"


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "CIK", "CIK12345678901", "12x4", "12345678901", "abc"],
)
def test_13f_normalize_cik_rejects_invalid_input(raw: str) -> None:
    with pytest.raises(ValueError, match="at most 10 digits"):
        Sec13FProvider.normalize_cik(raw)


def test_13f_submissions_raises_lookup_error_on_404(httpx_mock) -> None:  # type: ignore[no-untyped-def]
    httpx_mock.add_response(status_code=404)

    provider = Sec13FProvider(_settings())

    with pytest.raises(LookupError, match="0001234567"):
        provider.submissions("1234567")

    request = httpx_mock.get_requests()[0]
    assert request.url.path == "/submissions/CIK0001234567.json"


def test_13f_submissions_raises_lookup_error_on_non_dict_payload(httpx_mock) -> None:  # type: ignore[no-untyped-def]
    httpx_mock.add_response(json=["unexpected"])

    with pytest.raises(LookupError, match="Unexpected SEC submissions payload"):
        Sec13FProvider(_settings()).submissions("1234567")


def test_13f_submissions_caches_payload_per_cik(httpx_mock) -> None:  # type: ignore[no-untyped-def]
    httpx_mock.add_response(json={"name": "BRIDGE", "filings": {"recent": {}}})

    provider = Sec13FProvider(_settings())

    first = provider.submissions("1234567")
    second = provider.submissions("CIK1234567")

    assert first is second
    assert len(httpx_mock.get_requests()) == 1


def test_13f_recent_references_returns_empty_for_malformed_payload() -> None:
    for payload in (
        {},
        {"filings": {}},
        {"filings": {"recent": {}}},
        {"filings": {"recent": {"form": "13F-HR"}}},
        {"filings": {"recent": {"form": [], "accessionNumber": "x"}}},
        {"filings": {"recent": {"form": [], "accessionNumber": [], "filingDate": "x"}}},
    ):
        assert Sec13FProvider._recent_references(payload) == []


def test_13f_recent_references_skips_bad_dates_and_dedupes_report_date() -> None:
    payload = {
        "filings": {
            "recent": {
                "form": ["13F-HR", "13F-HR/A", "10-K", "13F-HR", "13F-HR"],
                "accessionNumber": [
                    "0001-26-000001",
                    "0001-26-000002",
                    "0001-26-000003",
                    "0001-26-000004",
                    "0001-26-000005",
                ],
                "filingDate": [
                    "2026-05-10",
                    "2026-05-20",
                    "2026-05-21",
                    "2026-05-01",
                    "not-a-date",
                ],
                "reportDate": [
                    "2026-03-31",
                    "2026-03-31",
                    "2025-12-31",
                    "2025-12-31",
                    "2026-03-31",
                ],
                "primaryDocument": [
                    "primary.xml",
                    "primary_a.xml",
                    "annual.htm",
                    "older.xml",
                    "broken.xml",
                ],
            }
        }
    }

    refs = Sec13FProvider._recent_references(payload)

    assert [(ref.accession_number, ref.form) for ref in refs] == [
        ("0001-26-000002", "13F-HR/A"),
        ("0001-26-000004", "13F-HR"),
    ]


def test_13f_recent_references_enforces_limit_bounds(httpx_mock) -> None:  # type: ignore[no-untyped-def]
    provider = Sec13FProvider(_settings())

    for bad_limit in (0, 9):
        with pytest.raises(ValueError, match="between 1 and 8"):
            provider.recent_references("1234567", limit=bad_limit)
    assert httpx_mock.get_requests() == []


def test_13f_recent_references_formats_name_and_refs(httpx_mock) -> None:  # type: ignore[no-untyped-def]
    httpx_mock.add_response(
        json={
            "name": "BRIDGE",
            "filings": {
                "recent": {
                    "form": ["13F-HR", "10-K"],
                    "accessionNumber": ["0001-26-000001", "0001-26-000002"],
                    "filingDate": ["2026-05-10", "2026-05-21"],
                    "reportDate": ["2026-03-31", "2025-12-31"],
                    "primaryDocument": ["primary.xml", "annual.htm"],
                }
            },
        }
    )

    provider = Sec13FProvider(_settings())
    name, refs = provider.recent_references("1234567", limit=1)

    assert name == "BRIDGE"
    assert len(refs) == 1
    assert refs[0].accession_number == "0001-26-000001"
    assert refs[0].report_date == date(2026, 3, 31)


def test_13f_recent_references_name_falls_back_to_cik(httpx_mock) -> None:  # type: ignore[no-untyped-def]
    httpx_mock.add_response(
        json={
            "filings": {
                "recent": {
                    "form": ["13F-HR"],
                    "accessionNumber": ["0001-26-000001"],
                    "filingDate": ["2026-05-10"],
                    "reportDate": ["2026-03-31"],
                    "primaryDocument": ["primary.xml"],
                }
            }
        }
    )

    name, refs = Sec13FProvider(_settings()).recent_references("1234567")

    assert name == "CIK 0001234567"
    assert len(refs) == 1


def test_13f_optional_float_handles_none_and_garbage() -> None:
    assert Sec13FProvider._optional_float(None) is None
    assert Sec13FProvider._optional_float("12.5") == 12.5
    assert Sec13FProvider._optional_float("abc") is None


def test_13f_information_table_xml_prefers_infotable_candidates(httpx_mock) -> None:  # type: ignore[no-untyped-def]
    xml = (
        "<informationTable><infoTable>"
        "<nameOfIssuer>ALPHA</nameOfIssuer><titleOfClass>COM</titleOfClass>"
        "<cusip>000000001</cusip><value>100</value>"
        "<shrsOrPrnAmt><sshPrnamt>10</sshPrnamt>"
        "<sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt>"
        "</infoTable></informationTable>"
    )
    httpx_mock.add_response(
        json={
            "directory": {
                "item": [
                    {"name": "cover.htm"},
                    {"name": "Infotable.xml"},
                    {"name": "other.xml"},
                ]
            }
        }
    )
    httpx_mock.add_response(text=xml)

    provider = Sec13FProvider(_settings())
    ref = FilingReference(
        accession_number="0001234567-26-000001",
        form="13F-HR",
        filing_date=date(2026, 5, 10),
        report_date=date(2026, 3, 31),
        primary_document="Infotable.xml",
    )

    text, source_url = provider._information_table_xml("1234567", ref)

    assert text == xml
    assert source_url == (
        "https://www.sec.gov/Archives/edgar/data/1234567/000123456726000001/Infotable.xml"
    )
    first_xml_request = httpx_mock.get_requests()[1]
    assert first_xml_request.url.path.endswith("/Infotable.xml")


def test_13f_information_table_xml_falls_back_to_primary_document(httpx_mock) -> None:  # type: ignore[no-untyped-def]
    xml = (
        "<informationTable><infoTable>"
        "<nameOfIssuer>ALPHA</nameOfIssuer><titleOfClass>COM</titleOfClass>"
        "<cusip>000000001</cusip><value>100</value>"
        "<shrsOrPrnAmt><sshPrnamt>10</sshPrnamt>"
        "<sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt>"
        "</infoTable></informationTable>"
    )
    httpx_mock.add_response(json={"directory": {"item": [{"name": "cover.htm"}]}})
    httpx_mock.add_response(text=xml)

    provider = Sec13FProvider(_settings())
    ref = FilingReference(
        accession_number="0001234567-26-000001",
        form="13F-HR",
        filing_date=date(2026, 5, 10),
        report_date=date(2026, 3, 31),
        primary_document="primary_doc.xml",
    )

    text, source_url = provider._information_table_xml("1234567", ref)

    assert text == xml
    assert source_url.endswith("/000123456726000001/primary_doc.xml")


def test_13f_information_table_xml_raises_when_no_candidate_parses(httpx_mock) -> None:  # type: ignore[no-untyped-def]
    httpx_mock.add_response(json={"directory": {"item": [{"name": "cover.htm"}]}})
    httpx_mock.add_response(status_code=404)

    provider = Sec13FProvider(_settings())
    ref = FilingReference(
        accession_number="0001234567-26-000001",
        form="13F-HR",
        filing_date=date(2026, 5, 10),
        report_date=date(2026, 3, 31),
        primary_document="missing.xml",
    )

    with pytest.raises(LookupError, match="0001234567-26-000001"):
        provider._information_table_xml("1234567", ref)


def test_13f_filing_builds_model_and_caches_by_accession(httpx_mock) -> None:  # type: ignore[no-untyped-def]
    xml = (
        "<informationTable><infoTable>"
        "<nameOfIssuer>ALPHA</nameOfIssuer><titleOfClass>COM</titleOfClass>"
        "<cusip>000000001</cusip><value>100</value>"
        "<shrsOrPrnAmt><sshPrnamt>10</sshPrnamt>"
        "<sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt>"
        "</infoTable></informationTable>"
    )
    httpx_mock.add_response(json={"directory": {"item": [{"name": "infotable.xml"}]}})
    httpx_mock.add_response(text=xml)

    provider = Sec13FProvider(_settings())
    ref = FilingReference(
        accession_number="0001234567-26-000001",
        form="13F-HR",
        filing_date=date(2026, 5, 10),
        report_date=date(2026, 3, 31),
        primary_document="infotable.xml",
    )

    filing = provider.filing("1234567", ref)
    again = provider.filing(1234567, ref)

    assert filing.accession_number == "0001234567-26-000001"
    assert filing.total_value_usd == 100_000
    assert filing.holdings[0].value_usd == 100_000
    assert filing.holdings[0].shares_or_principal == 10
    assert filing.provenance.license_class == LicenseClass.OFFICIAL_PUBLIC
    assert filing.provenance.as_of == date(2026, 3, 31)
    assert filing.source_url.endswith("/infotable.xml")
    assert again is filing
    assert len(httpx_mock.get_requests()) == 2
