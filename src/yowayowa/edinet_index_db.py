from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from yowayowa.db import Base


class EdinetFilingRecord(Base):
    __tablename__ = "edinet_filings"

    doc_id: Mapped[str] = mapped_column(String(8), primary_key=True)
    filing_date: Mapped[date] = mapped_column(Date, index=True)
    edinet_code: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    security_code: Mapped[str | None] = mapped_column(String(5), nullable=True, index=True)
    filer_name: Mapped[str] = mapped_column(String(500), index=True)
    fund_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ordinance_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    form_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    doc_type_code: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    description: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    xbrl_available: Mapped[bool] = mapped_column(Boolean, default=False)
    csv_available: Mapped[bool] = mapped_column(Boolean, default=False)
    legal_status: Mapped[str | None] = mapped_column(String(8), nullable=True)
    indexed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class EdinetIndexDayRecord(Base):
    __tablename__ = "edinet_index_days"

    filing_date: Mapped[date] = mapped_column(Date, primary_key=True)
    source_total_count: Mapped[int] = mapped_column(Integer)
    indexed_count: Mapped[int] = mapped_column(Integer)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
