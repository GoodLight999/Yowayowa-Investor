from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from yowayowa.domain import LicenseClass, Provenance
from yowayowa.edinet_index_db import EdinetFilingRecord, EdinetIndexDayRecord
from yowayowa.edinet_models import (
    EdinetDocumentSummary,
    EdinetFilingHistory,
    EdinetIndexFailure,
    EdinetIndexSyncResult,
)
from yowayowa.providers.edinet import EdinetClient
from yowayowa.services.edinet import document_list, normalize_security_code

_MAX_SYNC_DAYS = 31
_MAX_SEARCH_DAYS = 3660
_JST = ZoneInfo("Asia/Tokyo")


def _date_span(start_date: date, end_date: date, *, max_days: int) -> list[date]:
    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")
    days = (end_date - start_date).days + 1
    if days > max_days:
        raise ValueError(f"date range cannot exceed {max_days} days")
    return [start_date + timedelta(days=offset) for offset in range(days)]


def _record_from_summary(
    filing_date: date,
    summary: EdinetDocumentSummary,
    *,
    indexed_at: datetime,
) -> EdinetFilingRecord:
    return EdinetFilingRecord(
        doc_id=summary.doc_id,
        filing_date=filing_date,
        edinet_code=summary.edinet_code,
        security_code=summary.security_code,
        filer_name=summary.filer_name,
        fund_code=summary.fund_code,
        ordinance_code=summary.ordinance_code,
        form_code=summary.form_code,
        doc_type_code=summary.doc_type_code,
        description=summary.description,
        period_start=summary.period_start,
        period_end=summary.period_end,
        submitted_at=summary.submitted_at,
        xbrl_available=summary.xbrl_available,
        csv_available=summary.csv_available,
        legal_status=summary.legal_status,
        indexed_at=indexed_at,
    )


def _apply_summary(
    row: EdinetFilingRecord,
    filing_date: date,
    summary: EdinetDocumentSummary,
    *,
    indexed_at: datetime,
) -> None:
    row.filing_date = filing_date
    row.edinet_code = summary.edinet_code
    row.security_code = summary.security_code
    row.filer_name = summary.filer_name
    row.fund_code = summary.fund_code
    row.ordinance_code = summary.ordinance_code
    row.form_code = summary.form_code
    row.doc_type_code = summary.doc_type_code
    row.description = summary.description
    row.period_start = summary.period_start
    row.period_end = summary.period_end
    row.submitted_at = summary.submitted_at
    row.xbrl_available = summary.xbrl_available
    row.csv_available = summary.csv_available
    row.legal_status = summary.legal_status
    row.indexed_at = indexed_at


def sync_filing_day(session: Session, client: EdinetClient, filing_date: date) -> int:
    listing = document_list(
        client,
        filing_date,
        downloadable_only=True,
        limit=100_000,
    )
    indexed_at = datetime.now(UTC)
    source_doc_ids = {summary.doc_id for summary in listing.documents}
    stale_statement = delete(EdinetFilingRecord).where(
        EdinetFilingRecord.filing_date == filing_date
    )
    if source_doc_ids:
        stale_statement = stale_statement.where(EdinetFilingRecord.doc_id.not_in(source_doc_ids))
    session.execute(stale_statement)

    upserted = 0
    for summary in listing.documents:
        row = session.get(EdinetFilingRecord, summary.doc_id)
        if row is None:
            session.add(_record_from_summary(filing_date, summary, indexed_at=indexed_at))
        else:
            _apply_summary(row, filing_date, summary, indexed_at=indexed_at)
        upserted += 1

    day = session.get(EdinetIndexDayRecord, filing_date)
    if day is None:
        day = EdinetIndexDayRecord(
            filing_date=filing_date,
            source_total_count=listing.total_count,
            indexed_count=len(listing.documents),
            fetched_at=indexed_at,
        )
        session.add(day)
    else:
        day.source_total_count = listing.total_count
        day.indexed_count = len(listing.documents)
        day.fetched_at = indexed_at
    session.commit()
    return upserted


def sync_filing_index(
    session: Session,
    client: EdinetClient,
    start_date: date,
    end_date: date,
) -> EdinetIndexSyncResult:
    days = _date_span(start_date, end_date, max_days=_MAX_SYNC_DAYS)
    if end_date >= datetime.now(_JST).date():
        raise ValueError("EDINET filing index can only synchronize completed Japan calendar days")

    failures: list[EdinetIndexFailure] = []
    documents_upserted = 0
    days_synced = 0
    for filing_date in days:
        try:
            documents_upserted += sync_filing_day(session, client, filing_date)
            days_synced += 1
        except Exception as exc:
            session.rollback()
            failures.append(
                EdinetIndexFailure(
                    filing_date=filing_date,
                    error=type(exc).__name__,
                )
            )
    return EdinetIndexSyncResult(
        start_date=start_date,
        end_date=end_date,
        days_requested=len(days),
        days_synced=days_synced,
        documents_upserted=documents_upserted,
        failures=failures,
    )


def _summary_from_record(row: EdinetFilingRecord) -> EdinetDocumentSummary:
    return EdinetDocumentSummary(
        doc_id=row.doc_id,
        edinet_code=row.edinet_code,
        security_code=row.security_code,
        filer_name=row.filer_name,
        fund_code=row.fund_code,
        ordinance_code=row.ordinance_code,
        form_code=row.form_code,
        doc_type_code=row.doc_type_code,
        description=row.description,
        period_start=row.period_start,
        period_end=row.period_end,
        submitted_at=row.submitted_at,
        xbrl_available=row.xbrl_available,
        csv_available=row.csv_available,
        legal_status=row.legal_status,
    )


def _index_provenance(as_of: date | None) -> Provenance:
    return Provenance(
        provider="edinet-v2-index",
        source="EDINET API Version 2 / local filing index",
        source_url="https://disclosure2.edinet-fsa.go.jp/",
        license_class=LicenseClass.OFFICIAL_PUBLIC,
        retrieved_at=datetime.now(UTC),
        as_of=as_of,
        notes=[
            (
                "This filing index is a Yowayowa cache derived from official EDINET "
                "daily document lists."
            ),
            (
                "Coverage is explicit: coverage_complete is false whenever any requested "
                "calendar day has not been synchronized."
            ),
            (
                "Financial facts are still loaded from the original EDINET document on demand; "
                "the index stores filing metadata only."
            ),
        ],
    )


def filing_history(
    session: Session,
    start_date: date,
    end_date: date,
    *,
    security_code: str | None = None,
    edinet_code: str | None = None,
    doc_type_codes: set[str] | None = None,
    csv_only: bool = False,
    limit: int = 500,
) -> EdinetFilingHistory:
    days = _date_span(start_date, end_date, max_days=_MAX_SEARCH_DAYS)
    normalized_security_code = (
        normalize_security_code(security_code) if security_code is not None else None
    )
    normalized_edinet_code = edinet_code.strip().upper() if edinet_code else None
    if normalized_security_code is None and normalized_edinet_code is None:
        raise ValueError("security_code or edinet_code is required for filing history search")

    statement = select(EdinetFilingRecord).where(
        EdinetFilingRecord.filing_date >= start_date,
        EdinetFilingRecord.filing_date <= end_date,
    )
    if normalized_security_code:
        statement = statement.where(EdinetFilingRecord.security_code == normalized_security_code)
    if normalized_edinet_code:
        statement = statement.where(EdinetFilingRecord.edinet_code == normalized_edinet_code)
    normalized_doc_types = {value.strip() for value in (doc_type_codes or set()) if value.strip()}
    if normalized_doc_types:
        statement = statement.where(EdinetFilingRecord.doc_type_code.in_(normalized_doc_types))
    if csv_only:
        statement = statement.where(EdinetFilingRecord.csv_available.is_(True))
    statement = statement.order_by(
        EdinetFilingRecord.submitted_at.desc(),
        EdinetFilingRecord.doc_id.desc(),
    )

    rows = list(session.scalars(statement).all())
    matched_count = len(rows)
    indexed_days = session.scalar(
        select(func.count())
        .select_from(EdinetIndexDayRecord)
        .where(
            EdinetIndexDayRecord.filing_date >= start_date,
            EdinetIndexDayRecord.filing_date <= end_date,
        )
    )
    indexed_days = int(indexed_days or 0)
    index_start = session.scalar(select(func.min(EdinetIndexDayRecord.filing_date)))
    index_end = session.scalar(select(func.max(EdinetIndexDayRecord.filing_date)))
    return EdinetFilingHistory(
        start_date=start_date,
        end_date=end_date,
        security_code=normalized_security_code,
        edinet_code=normalized_edinet_code,
        documents=[_summary_from_record(row) for row in rows[:limit]],
        matched_count=matched_count,
        indexed_days=indexed_days,
        expected_days=len(days),
        coverage_complete=indexed_days == len(days),
        index_start=index_start,
        index_end=index_end,
        provenance=_index_provenance(index_end),
    )
