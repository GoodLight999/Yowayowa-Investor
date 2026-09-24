"""Persistence + read services for weekly credit margin balances (P4-C).

Ingestion contract (docs/CREDIT_MARGIN.md):

- independent of the JPX daily service: weekly scraped data has no 一般/制度
  breakdown, so a separate table and model are used (never zero-fill the
  daily identity);
- one row per (as_of_date, code). Re-persisting a week whose parsed values
  are unchanged is a no-op; when values changed the row is updated (values,
  retrieved_at, source_url) and a ``再確認(値変化)`` note is appended;
  different weeks insert normally. A daily job picking up pages whenever
  they run converges the weekly datapoints;
- derived fields (week-over-week change, short/long ratio) are computed at
  read time from persisted rows only; a missing previous week is ``None``
  (never interpolated), a zero long balance gives ratio ``None`` (never
  infinity).
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from yowayowa.credit_margin_models import (
    CreditMarginWeekly,
    CreditMarginWeeklyPoint,
    CreditMarginWeeklySeries,
)
from yowayowa.db import CreditMarginWeeklyRecord
from yowayowa.domain import LicenseClass, Provenance

__all__ = [
    "latest_credit_margin_week",
    "persist_credit_margin_weekly",
    "read_credit_margin_by_code",
    "read_credit_margin_by_date",
]

_RECHECK_NOTE = "再確認(値変化)"


def _row_key_equal(record: CreditMarginWeeklyRecord, weekly: CreditMarginWeekly) -> bool:
    return record.short_total == weekly.short_total and record.long_total == weekly.long_total


def _apply_update(
    record: CreditMarginWeeklyRecord,
    weekly: CreditMarginWeekly,
    *,
    notes: list[str],
) -> None:
    record.short_total = weekly.short_total
    record.long_total = weekly.long_total
    record.source_url = weekly.provenance.source_url
    record.provider = weekly.provenance.provider
    record.retrieved_at = weekly.provenance.retrieved_at
    record.notes = notes


def persist_credit_margin_weekly(
    session: Session,
    weeklies: list[CreditMarginWeekly],
) -> dict[str, int]:
    """Upsert weekly rows: value-identical re-fetch is a no-op, changed
    values UPDATE (value/retrieved_at/source_url + ``再確認(値変化)`` note),
    other as_of weeks INSERT. All writes commit atomically.

    Returns ``{"inserted": n, "updated": n, "unchanged": n}``.
    """

    inserted = updated = unchanged = 0
    try:
        for weekly in weeklies:
            record = session.scalar(
                select(CreditMarginWeeklyRecord).where(
                    CreditMarginWeeklyRecord.as_of_date == weekly.as_of_date,
                    CreditMarginWeeklyRecord.code == weekly.code,
                )
            )
            base_notes = list(weekly.provenance.notes or [])
            if record is None:
                session.add(
                    CreditMarginWeeklyRecord(
                        as_of_date=weekly.as_of_date,
                        code=weekly.code,
                        short_total=weekly.short_total,
                        long_total=weekly.long_total,
                        source_url=weekly.provenance.source_url,
                        provider=weekly.provenance.provider,
                        retrieved_at=weekly.provenance.retrieved_at,
                        notes=base_notes,
                    )
                )
                inserted += 1
            elif _row_key_equal(record, weekly):
                unchanged += 1
            else:
                _apply_update(record, weekly, notes=[*base_notes, _RECHECK_NOTE])
                updated += 1
        session.commit()
    except Exception:
        session.rollback()
        raise
    return {"inserted": inserted, "updated": updated, "unchanged": unchanged}


def _provenance_from_record(record: CreditMarginWeeklyRecord) -> Provenance:
    notes = list(record.notes or [])
    if _RECHECK_NOTE in notes:
        notes = [note for note in notes if note != _RECHECK_NOTE]
    return Provenance(
        provider=record.provider,
        source=(
            "Yahoo!ファイナンス 信用残 weekly history (quote page)"
            if record.provider == "yahoo_finance_margin"
            else "株探 信用取引残高 weekly table (stock top page)"
        ),
        source_url=record.source_url,
        license_class=LicenseClass.PERSONAL_ONLY,
        retrieved_at=record.retrieved_at,
        as_of=record.as_of_date,
        notes=notes,
    )


def _point_from_record(
    record: CreditMarginWeeklyRecord,
    previous: CreditMarginWeeklyRecord | None,
) -> CreditMarginWeeklyPoint:
    short_change = record.short_total - previous.short_total if previous is not None else None
    long_change = record.long_total - previous.long_total if previous is not None else None
    short_long_ratio = record.short_total / record.long_total if record.long_total != 0 else None
    return CreditMarginWeeklyPoint(
        as_of_date=record.as_of_date,
        code=record.code,
        short_total=record.short_total,
        long_total=record.long_total,
        short_change=short_change,
        long_change=long_change,
        short_long_ratio=short_long_ratio,
        previous_as_of_date=previous.as_of_date if previous is not None else None,
        retrieved_at=record.retrieved_at,
        provenance=_provenance_from_record(record),
    )


def read_credit_margin_by_code(
    session: Session,
    code: str,
    *,
    limit: int = 30,
    date_from: date | None = None,
    date_to: date | None = None,
) -> CreditMarginWeeklySeries:
    """Time series for one code, oldest first.

    Default window is the most recent ``limit`` as-of weeks; explicit
    ``date_from``/``date_to`` bound the query instead (limit still applies).
    """

    statement = select(CreditMarginWeeklyRecord).where(CreditMarginWeeklyRecord.code == code)
    if date_from is not None:
        statement = statement.where(CreditMarginWeeklyRecord.as_of_date >= date_from)
    if date_to is not None:
        statement = statement.where(CreditMarginWeeklyRecord.as_of_date <= date_to)
    statement = statement.order_by(
        CreditMarginWeeklyRecord.as_of_date.desc(), CreditMarginWeeklyRecord.id.desc()
    ).limit(limit)
    rows = list(session.scalars(statement))
    rows.reverse()
    points: list[CreditMarginWeeklyPoint] = []
    previous: CreditMarginWeeklyRecord | None = None
    for row in rows:
        points.append(_point_from_record(row, previous))
        previous = row
    return CreditMarginWeeklySeries(code=code, points=points)


def read_credit_margin_by_date(
    session: Session,
    as_of_date: date,
) -> list[CreditMarginWeeklyPoint]:
    """All persisted weekly rows for one as-of date, code order."""

    rows = list(
        session.scalars(
            select(CreditMarginWeeklyRecord)
            .where(CreditMarginWeeklyRecord.as_of_date == as_of_date)
            .order_by(CreditMarginWeeklyRecord.code)
        )
    )
    return [_point_from_record(row, None) for row in rows]


def latest_credit_margin_week(session: Session) -> date | None:
    """Most recent persisted as-of week, or None when nothing is stored."""

    return session.scalar(
        select(CreditMarginWeeklyRecord.as_of_date)
        .order_by(CreditMarginWeeklyRecord.as_of_date.desc())
        .limit(1)
    )
