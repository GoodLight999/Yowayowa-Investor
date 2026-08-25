from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from yowayowa.db import Base
from yowayowa.domain import LicenseClass, Provenance
from yowayowa.edinet_index_db import EdinetFilingRecord
from yowayowa.edinet_models import EdinetFact
from yowayowa.providers.edinet import EdinetCsvPayload
from yowayowa.services.strategy_edinet import (
    balance_sheet_supplement,
    latest_indexed_annual_report,
    tokyo_security_code,
)


class FakeEdinetClient:
    def normalize_doc_id(self, doc_id: str) -> str:
        return doc_id.strip().upper()

    def csv_facts(self, doc_id: str) -> EdinetCsvPayload:
        assert doc_id == "S100TEST"
        facts = [
            EdinetFact(
                source_file="XBRL_TO_CSV/jppfs.csv",
                element_id="jppfs_cor:CurrentAssets",
                label="流動資産",
                context_id="CurrentYearInstant",
                relative_year="当期",
                consolidation="連結",
                period_type="時点",
                unit_id="JPY",
                unit="円",
                value="120",
            ),
            EdinetFact(
                source_file="XBRL_TO_CSV/jppfs.csv",
                element_id="jppfs_cor:Liabilities",
                label="負債合計",
                context_id="CurrentYearInstant",
                relative_year="当期",
                consolidation="連結",
                period_type="時点",
                unit_id="JPY",
                unit="円",
                value="40",
            ),
            EdinetFact(
                source_file="XBRL_TO_CSV/jppfs.csv",
                element_id="jppfs_cor:InvestmentSecurities",
                label="投資有価証券",
                context_id="CurrentYearInstant",
                relative_year="当期",
                consolidation="連結",
                period_type="時点",
                unit_id="JPY",
                unit="円",
                value="30",
            ),
            EdinetFact(
                source_file="XBRL_TO_CSV/jpdei.csv",
                element_id="jpdei_cor:CurrentPeriodEndDateDEI",
                label="当期末",
                context_id="FilingDateInstant",
                relative_year="当期",
                consolidation=None,
                period_type="時点",
                unit_id=None,
                unit=None,
                value="2026-03-31",
            ),
        ]
        return EdinetCsvPayload(
            facts=facts,
            source_files=["XBRL_TO_CSV/jppfs.csv"],
            parse_warnings=[],
        )

    def provenance_for_document(self, doc_id: str) -> Provenance:
        return Provenance(
            provider="edinet-v2",
            source="EDINET API Version 2",
            source_url="https://disclosure2.edinet-fsa.go.jp/",
            license_class=LicenseClass.OFFICIAL_PUBLIC,
            retrieved_at=datetime(2026, 8, 25, tzinfo=UTC),
            as_of=date(2026, 3, 31),
        )


@contextmanager
def _session() -> Iterator[Session]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            session.add(
                EdinetFilingRecord(
                    doc_id="S100TEST",
                    filing_date=date(2026, 6, 20),
                    edinet_code="E00001",
                    security_code="72030",
                    filer_name="株式会社テスト",
                    doc_type_code="120",
                    description="有価証券報告書",
                    period_start=date(2025, 4, 1),
                    period_end=date(2026, 3, 31),
                    submitted_at=datetime(2026, 6, 20, 10, 30, tzinfo=UTC),
                    xbrl_available=True,
                    csv_available=True,
                    legal_status="1",
                    indexed_at=datetime(2026, 8, 25, tzinfo=UTC),
                )
            )
            session.commit()
            yield session
    finally:
        engine.dispose()


def test_tokyo_symbol_maps_to_edinet_security_code() -> None:
    assert tokyo_security_code("7203.T") == "72030"
    assert tokyo_security_code("7203") is None
    assert tokyo_security_code("AAPL") is None


def test_latest_indexed_report_requires_annual_csv_filing() -> None:
    with _session() as session:
        filing = latest_indexed_annual_report(session, "7203.T")

    assert filing is not None
    assert filing.doc_id == "S100TEST"
    assert filing.doc_type_code == "120"


def test_edinet_strategy_supplement_uses_three_metrics_from_same_filing() -> None:
    with _session() as session:
        supplement = balance_sheet_supplement(  # type: ignore[arg-type]
            session,
            FakeEdinetClient(),
            "7203.T",
        )

    assert supplement is not None
    assert supplement.current_assets == 120
    assert supplement.liabilities == 40
    assert supplement.investment_securities == 30
    assert supplement.provenance.provider == "edinet-v2"
    assert any("S100TEST" in note for note in supplement.provenance.notes)
