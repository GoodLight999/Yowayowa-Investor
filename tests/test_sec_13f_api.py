from datetime import UTC, date, datetime

from starlette.testclient import TestClient

from yowayowa.domain import LicenseClass, Provenance
from yowayowa.institutional_models import (
    InstitutionalHolding,
    ThirteenFFiling,
    ThirteenFManagerReport,
)


def test_sec_13f_endpoint_is_mounted_and_returns_delayed_report(monkeypatch) -> None:
    from yowayowa.api import institutional_routes
    from yowayowa.api.app import app

    holding = InstitutionalHolding(
        issuer="ALPHA INC",
        title_of_class="COM",
        cusip="000000001",
        reported_value_thousands=1000,
        value_usd=1_000_000,
        shares_or_principal=50_000,
        amount_type="SH",
        weight=1.0,
    )
    filing = ThirteenFFiling(
        accession_number="0000000001-26-000001",
        form="13F-HR",
        filing_date=date(2026, 5, 15),
        report_date=date(2026, 3, 31),
        primary_document="primary.xml",
        source_url="https://www.sec.gov/fixture/infotable.xml",
        holdings=[holding],
        total_value_usd=1_000_000,
        provenance=Provenance(
            provider="sec-edgar",
            source="SEC EDGAR Form 13F information table",
            source_url="https://www.sec.gov/fixture/infotable.xml",
            license_class=LicenseClass.OFFICIAL_PUBLIC,
            retrieved_at=datetime(2026, 8, 16, tzinfo=UTC),
            as_of=date(2026, 3, 31),
        ),
    )
    report = ThirteenFManagerReport(
        cik="0000000001",
        manager_name="Fixture Manager",
        filings=[filing],
        retrieved_at=datetime(2026, 8, 16, tzinfo=UTC),
        notes=["13F is a delayed quarterly disclosure."],
    )

    monkeypatch.setattr(
        institutional_routes,
        "manager_report",
        lambda provider, cik, quarters=2: report,
    )

    with TestClient(app) as client:
        response = client.get("/v1/institutional/13f/1?quarters=2")

    assert response.status_code == 200
    payload = response.json()
    assert payload["manager_name"] == "Fixture Manager"
    assert payload["filings"][0]["report_date"] == "2026-03-31"
    assert payload["filings"][0]["holdings"][0]["value_usd"] == 1_000_000
