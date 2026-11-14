from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from yowayowa.api.deps import db_session, require_api_token
from yowayowa.edinet_index_db import EdinetFilingRecord

router = APIRouter(prefix="/v1/filings/edinet/index", dependencies=[Depends(require_api_token)])


def search_indexed_issuers(
    session: Session,
    query: str,
    limit: int = 20,
) -> list[dict[str, object]]:
    needle = query.strip()
    like = f"%{needle}%"
    statement = (
        select(
            EdinetFilingRecord.security_code,
            EdinetFilingRecord.filer_name,
            EdinetFilingRecord.edinet_code,
            func.max(EdinetFilingRecord.filing_date).label("latest_filing_date"),
        )
        .where(
            EdinetFilingRecord.security_code.is_not(None),
            or_(
                EdinetFilingRecord.security_code.like(f"{needle}%"),
                EdinetFilingRecord.filer_name.ilike(like),
            ),
        )
        .group_by(
            EdinetFilingRecord.security_code,
            EdinetFilingRecord.filer_name,
            EdinetFilingRecord.edinet_code,
        )
        .order_by(func.max(EdinetFilingRecord.filing_date).desc())
        .limit(limit * 3)
    )
    rows = session.execute(statement).all()
    seen: set[str] = set()
    issuers: list[dict[str, object]] = []
    for security_code, filer_name, edinet_code, latest_filing_date in rows:
        if not security_code or security_code in seen:
            continue
        seen.add(security_code)
        issuers.append(
            {
                "security_code": security_code,
                "filer_name": filer_name,
                "edinet_code": edinet_code,
                "latest_filing_date": latest_filing_date,
            }
        )
        if len(issuers) >= limit:
            break
    return issuers


@router.get("/issuers")
def issuer_search(
    q: str = Query(min_length=1, max_length=100),
    limit: int = Query(default=20, ge=1, le=100),
    session: Session = Depends(db_session),
) -> dict[str, object]:
    needle = q.strip()
    return {
        "query": needle,
        "issuers": search_indexed_issuers(session, needle, limit),
    }
