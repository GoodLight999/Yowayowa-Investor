from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from yowayowa.db import Base
from yowayowa.domain import LicenseClass, Provenance
from yowayowa.edinet_index_db import EdinetFilingRecord
from yowayowa.services.edinet_index import sync_filing_day


class EmptyEdinetClient:
    def documents(self, filing_date: date) -> dict[str, Any]:
        provenance = Provenance(
            provider="edinet-v2",
            source="EDINET API Version 2",
            source_url="https://disclosure2.edinet-fsa.go.jp/",
            license_class=LicenseClass.OFFICIAL_PUBLIC,
            retrieved_at=datetime.now(UTC),
            as_of=filing_date,
        )
        return {
            "metadata": {"status": "200"},
            "results": [],
            "provenance": provenance.model_dump(mode="json"),
        }


def test_edinet_resync_removes_filing_no_longer_present_in_official_day() -> None:
    filing_date = date(2026, 6, 20)
    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            session.add(
                EdinetFilingRecord(
                    doc_id="S100TEST",
                    filing_date=filing_date,
                    edinet_code="E00001",
                    security_code="72030",
                    filer_name="株式会社テスト",
                    xbrl_available=True,
                    csv_available=True,
                    indexed_at=datetime.now(UTC),
                )
            )
            session.commit()

            synced = sync_filing_day(  # type: ignore[arg-type]
                session,
                EmptyEdinetClient(),
                filing_date,
            )
            rows = list(session.scalars(select(EdinetFilingRecord)).all())

        assert synced == 0
        assert rows == []
    finally:
        engine.dispose()
