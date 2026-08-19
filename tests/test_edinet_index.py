from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from yowayowa.db import Base
from yowayowa.domain import LicenseClass, Provenance
from yowayowa.services.edinet_index import filing_history, sync_filing_index


class FakeEdinetClient:
    def __init__(self, *, fail_on: date | None = None) -> None:
        self.fail_on = fail_on
        self.calls: list[date] = []

    def documents(self, filing_date: date) -> dict[str, Any]:
        self.calls.append(filing_date)
        if filing_date == self.fail_on:
            raise RuntimeError("simulated EDINET outage")
        suffix = f"{filing_date.day:02d}"
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
            "results": [
                {
                    "docID": f"S10000{suffix}",
                    "edinetCode": "E00001",
                    "secCode": "72030",
                    "filerName": "株式会社テスト",
                    "docTypeCode": "120",
                    "docDescription": "有価証券報告書",
                    "periodStart": "2025-04-01",
                    "periodEnd": "2026-03-31",
                    "submitDateTime": f"{filing_date.isoformat()} 10:30",
                    "xbrlFlag": "1",
                    "csvFlag": "1",
                    "withdrawalStatus": "0",
                    "legalStatus": "1",
                },
                {
                    "docID": f"S20000{suffix}",
                    "edinetCode": "E99999",
                    "secCode": "99990",
                    "filerName": "別会社",
                    "docTypeCode": "130",
                    "submitDateTime": f"{filing_date.isoformat()} 09:00",
                    "xbrlFlag": "1",
                    "csvFlag": "0",
                    "withdrawalStatus": "0",
                    "legalStatus": "1",
                },
            ],
            "provenance": provenance.model_dump(mode="json"),
        }


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_edinet_index_sync_and_company_history_are_idempotent_and_coverage_aware() -> None:
    start = date(2026, 6, 20)
    end = date(2026, 6, 21)
    client = FakeEdinetClient()
    with _session() as session:
        first = sync_filing_index(session, client, start, end)  # type: ignore[arg-type]
        second = sync_filing_index(session, client, start, end)  # type: ignore[arg-type]
        result = filing_history(
            session,
            start,
            end,
            security_code="7203",
            csv_only=True,
        )

    assert first.days_synced == 2
    assert first.documents_upserted == 4
    assert not first.failures
    assert second.days_synced == 2
    assert result.security_code == "72030"
    assert result.matched_count == 2
    assert len(result.documents) == 2
    assert result.documents[0].doc_id == "S1000021"
    assert result.documents[1].doc_id == "S1000020"
    assert result.indexed_days == 2
    assert result.expected_days == 2
    assert result.coverage_complete is True
    assert result.index_start == start
    assert result.index_end == end
    assert result.provenance.provider == "edinet-v2-index"


def test_edinet_index_reports_partial_sync_and_does_not_hide_missing_days() -> None:
    start = date(2026, 6, 20)
    failed = start + timedelta(days=1)
    end = start + timedelta(days=2)
    client = FakeEdinetClient(fail_on=failed)
    with _session() as session:
        synced = sync_filing_index(session, client, start, end)  # type: ignore[arg-type]
        result = filing_history(session, start, end, edinet_code="E00001")

    assert synced.days_requested == 3
    assert synced.days_synced == 2
    assert synced.failures[0].filing_date == failed
    assert synced.failures[0].error == "RuntimeError"
    assert result.matched_count == 2
    assert result.indexed_days == 2
    assert result.expected_days == 3
    assert result.coverage_complete is False


def test_edinet_index_bounds_network_sync_and_requires_company_identifier() -> None:
    start = date(2026, 1, 1)
    client = FakeEdinetClient()
    with _session() as session:
        with pytest.raises(ValueError, match="31 days"):
            sync_filing_index(  # type: ignore[arg-type]
                session,
                client,
                start,
                start + timedelta(days=31),
            )
        with pytest.raises(ValueError, match="security_code or edinet_code"):
            filing_history(session, start, start, csv_only=True)


def test_edinet_index_rejects_current_japan_calendar_day() -> None:
    today_jst = datetime.now(ZoneInfo("Asia/Tokyo")).date()
    client = FakeEdinetClient()
    with (
        _session() as session,
        pytest.raises(ValueError, match="completed Japan calendar days"),
    ):
        sync_filing_index(session, client, today_jst, today_jst)  # type: ignore[arg-type]

    assert client.calls == []
