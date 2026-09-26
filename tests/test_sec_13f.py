from datetime import date

import pytest

from yowayowa.institutional_models import InstitutionalHolding
from yowayowa.providers.sec_13f import Sec13FProvider
from yowayowa.services.institutional import _compare_holdings

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
