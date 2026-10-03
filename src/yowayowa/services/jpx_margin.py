"""Persistence + read services for JPX daily issue-level margin balances (P4-A).

Ingestion contract (docs/JPX_DAILY_MARGIN.md):

- one row per (application_date, code); re-ingesting an application date
  replaces that date's rows atomically (delete+insert inside one transaction,
  committed once — a failed parse or write never leaves a half-replaced day);
- derived fields (daily change vs the previous application date, short/long
  ratio) are computed at read time from persisted balances only; when the
  previous application date does not exist the change is ``None`` (never
  interpolated), and ratios with a zero denominator are ``None`` (never
  infinity).
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from yowayowa.db import JpxMarginAuxRecord, JpxMarginBalanceRecord
from yowayowa.domain import LicenseClass, Provenance
from yowayowa.jpx_models import JpxMarginBalance, JpxMarginBalancePoint, JpxMarginSeries
from yowayowa.providers.jpx_margin import parse_jpx_margin_csv

__all__ = [
    "ingest_jpx_margin_csv",
    "latest_jpx_margin_date",
    "read_jpx_margin_by_code",
    "read_jpx_margin_by_date",
]


def ingest_jpx_margin_csv(
    session: Session,
    data: bytes,
    *,
    source_url: str,
    retrieved_at: datetime | None = None,
) -> list[JpxMarginBalance]:
    """Parse and persist a JPX margin CSV, replacing affected dates atomically.

    The whole file must parse and validate before anything is written; rows
    are then written per application date as one transaction (all dates in
    the file commit together). Returns the persisted balances.
    """

    balances = parse_jpx_margin_csv(
        data,
        source_url=source_url,
        retrieved_at=retrieved_at,
    )
    retrieved = retrieved_at or datetime.now(UTC)
    affected_dates = sorted({balance.application_date for balance in balances})
    try:
        for application_date in affected_dates:
            # Last write for an application date wins (JPX re-publishes
            # corrected files): replace that date's rows inside the same
            # transaction as the inserts, so readers never see a half-replaced
            # day and a failure rolls the whole ingest back.
            session.execute(
                delete(JpxMarginBalanceRecord).where(
                    JpxMarginBalanceRecord.application_date == application_date
                )
            )
            # If a paid/reference file replaces a day previously ingested from
            # the free public PDF, clear only the last-writer provenance marker.
            # Public-only detail rows remain source facts in their own right.
            session.execute(
                delete(JpxMarginAuxRecord).where(
                    JpxMarginAuxRecord.kind == "balance_source",
                    JpxMarginAuxRecord.as_of_date == application_date,
                )
            )
            for balance in balances:
                if balance.application_date != application_date:
                    continue
                session.add(
                    JpxMarginBalanceRecord(
                        application_date=balance.application_date,
                        code=balance.code,
                        company_name=balance.company_name,
                        isin=balance.isin,
                        market_code=balance.market_code,
                        margin_code=balance.margin_code,
                        short_total=balance.short_total,
                        long_total=balance.long_total,
                        short_negotiable=balance.short_negotiable,
                        short_standardized=balance.short_standardized,
                        long_negotiable=balance.long_negotiable,
                        long_standardized=balance.long_standardized,
                        short_total_value=balance.short_total_value,
                        long_total_value=balance.long_total_value,
                        short_negotiable_value=balance.short_negotiable_value,
                        short_standardized_value=balance.short_standardized_value,
                        long_negotiable_value=balance.long_negotiable_value,
                        long_standardized_value=balance.long_standardized_value,
                        source_url=source_url,
                        retrieved_at=retrieved,
                    )
                )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return balances


def _point_from_record(
    record: JpxMarginBalanceRecord,
    previous: JpxMarginBalanceRecord | None,
    *,
    provenance: Provenance | None = None,
) -> JpxMarginBalancePoint:
    short_change = record.short_total - previous.short_total if previous is not None else None
    long_change = record.long_total - previous.long_total if previous is not None else None
    short_long_ratio = record.short_total / record.long_total if record.long_total != 0 else None
    return JpxMarginBalancePoint(
        application_date=record.application_date,
        code=record.code,
        short_total=record.short_total,
        long_total=record.long_total,
        short_total_value=record.short_total_value,
        long_total_value=record.long_total_value,
        short_change=short_change,
        long_change=long_change,
        short_long_ratio=short_long_ratio,
        previous_application_date=previous.application_date if previous is not None else None,
        retrieved_at=record.retrieved_at,
        provenance=provenance or _provenance_from_record(record),
    )


def _provenance_from_record(record: JpxMarginBalanceRecord) -> Provenance:
    return Provenance(
        provider="jpx_reference",
        source="JPX総研 銘柄別信用取引残高（日次） reference service",
        source_url=record.source_url,
        license_class=LicenseClass.PERSONAL_ONLY,
        retrieved_at=record.retrieved_at,
        as_of=record.application_date,
        notes=[
            "Personal-only JPX reference data; not redistributable (docs/JPX_DAILY_MARGIN.md).",
        ],
    )


def _public_provenance_from_aux(row: JpxMarginAuxRecord) -> Provenance | None:
    raw = row.payload.get("provenance")
    if not isinstance(raw, dict):
        return None
    return Provenance.model_validate(raw)


def _balance_source_map(
    session: Session,
    *,
    code: str | None = None,
    dates: list[date] | None = None,
) -> dict[tuple[date, str], Provenance]:
    statement = select(JpxMarginAuxRecord).where(
        JpxMarginAuxRecord.kind == "balance_source"
    )
    if code is not None:
        statement = statement.where(JpxMarginAuxRecord.code == code)
    if dates:
        statement = statement.where(JpxMarginAuxRecord.as_of_date.in_(dates))
    result: dict[tuple[date, str], Provenance] = {}
    for row in session.scalars(statement):
        if row.code is None:
            continue
        source = _public_provenance_from_aux(row)
        if source is not None:
            result[(row.as_of_date, row.code)] = source
    return result


def read_jpx_margin_by_code(
    session: Session,
    code: str,
    *,
    limit: int = 30,
    date_from: date | None = None,
    date_to: date | None = None,
) -> JpxMarginSeries:
    """Time series for one code, oldest first, with true previous-date changes.

    The returned limit is applied after finding the immediately preceding
    persisted application date. Thus limit=1 still reports a real daily change
    when an earlier row exists; the previous row itself is not returned.
    """

    statement = select(JpxMarginBalanceRecord).where(JpxMarginBalanceRecord.code == code)
    if date_from is not None:
        statement = statement.where(JpxMarginBalanceRecord.application_date >= date_from)
    if date_to is not None:
        statement = statement.where(JpxMarginBalanceRecord.application_date <= date_to)
    statement = statement.order_by(
        JpxMarginBalanceRecord.application_date.desc(), JpxMarginBalanceRecord.id.desc()
    ).limit(limit)
    rows = list(session.scalars(statement))
    rows.reverse()
    if not rows:
        return JpxMarginSeries(code=code, points=[])

    previous = session.scalar(
        select(JpxMarginBalanceRecord)
        .where(
            JpxMarginBalanceRecord.code == code,
            JpxMarginBalanceRecord.application_date < rows[0].application_date,
        )
        .order_by(
            JpxMarginBalanceRecord.application_date.desc(),
            JpxMarginBalanceRecord.id.desc(),
        )
        .limit(1)
    )
    provenance_by_key = _balance_source_map(
        session,
        code=code,
        dates=[row.application_date for row in rows],
    )
    points: list[JpxMarginBalancePoint] = []
    for row in rows:
        points.append(
            _point_from_record(
                row,
                previous,
                provenance=provenance_by_key.get((row.application_date, row.code)),
            )
        )
        previous = row
    return JpxMarginSeries(code=code, points=points)


def read_jpx_margin_by_date(
    session: Session,
    application_date: date,
) -> list[JpxMarginBalancePoint]:
    """All persisted issue balances for one application date, code order."""

    rows = list(
        session.scalars(
            select(JpxMarginBalanceRecord)
            .where(JpxMarginBalanceRecord.application_date == application_date)
            .order_by(JpxMarginBalanceRecord.code)
        )
    )
    provenance_by_key = _balance_source_map(
        session,
        dates=[application_date],
    )
    return [
        _point_from_record(
            row,
            None,
            provenance=provenance_by_key.get((row.application_date, row.code)),
        )
        for row in rows
    ]


def latest_jpx_margin_date(session: Session) -> date | None:
    """Most recent persisted application date, or None when nothing is stored."""

    return session.scalar(
        select(JpxMarginBalanceRecord.application_date)
        .order_by(JpxMarginBalanceRecord.application_date.desc())
        .limit(1)
    )
