from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from yowayowa.api.edinet_ux_routes import search_indexed_issuers
from yowayowa.db import Base
from yowayowa.edinet_index_db import EdinetFilingRecord


def _record(
    doc_id: str,
    security_code: str,
    filer_name: str,
    filing_date: date,
) -> EdinetFilingRecord:
    return EdinetFilingRecord(
        doc_id=doc_id,
        filing_date=filing_date,
        edinet_code="E00001",
        security_code=security_code,
        filer_name=filer_name,
        fund_code=None,
        ordinance_code=None,
        form_code=None,
        doc_type_code="120",
        description="有価証券報告書",
        period_start=date(2025, 4, 1),
        period_end=date(2026, 3, 31),
        submitted_at=datetime(2026, 6, 20, 1, 0, tzinfo=UTC),
        xbrl_available=True,
        csv_available=True,
        legal_status="1",
        indexed_at=datetime(2026, 6, 20, 1, 10, tzinfo=UTC),
    )


def test_edinet_issuer_search_accepts_company_name_and_security_code() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            [
                _record("S1000001", "72030", "トヨタ自動車株式会社", date(2026, 6, 20)),
                _record("S1000002", "72030", "トヨタ自動車株式会社", date(2026, 5, 20)),
                _record("S1000003", "67580", "ソニーグループ株式会社", date(2026, 6, 21)),
            ]
        )
        session.commit()

        by_name = search_indexed_issuers(session, "トヨタ", 20)
        by_code = search_indexed_issuers(session, "7203", 20)

    assert by_name == by_code
    assert len(by_name) == 1
    assert by_name[0]["security_code"] == "72030"
    assert by_name[0]["filer_name"] == "トヨタ自動車株式会社"
    assert by_name[0]["latest_filing_date"] == date(2026, 6, 20)
