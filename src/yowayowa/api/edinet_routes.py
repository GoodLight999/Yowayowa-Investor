from __future__ import annotations

from datetime import date

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from yowayowa.api.deps import db_session, request_data_source_settings, require_api_token
from yowayowa.config import get_settings
from yowayowa.edinet_models import (
    EdinetDocumentList,
    EdinetFactSearchResult,
    EdinetFilingHistory,
    EdinetFinancials,
    EdinetIndexSyncResult,
)
from yowayowa.providers.base import ProviderPolicyError
from yowayowa.providers.edinet import EdinetClient
from yowayowa.providers.registry import edinet_client
from yowayowa.services.edinet import document_list, financials, search_facts
from yowayowa.services.edinet_index import filing_history, sync_filing_index

router = APIRouter(prefix="/v1/filings/edinet", dependencies=[Depends(require_api_token)])


def _translate_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ProviderPolicyError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, RuntimeError):
        return HTTPException(status_code=424, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, LookupError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        if status_code in {400, 404}:
            return HTTPException(
                status_code=status_code, detail="EDINET document was not available"
            )
        if status_code in {401, 403}:
            return HTTPException(status_code=424, detail="EDINET API key was rejected")
        if status_code == 429:
            return HTTPException(status_code=429, detail="EDINET API rate limit was reached")
    return HTTPException(status_code=502, detail=str(exc))


def _request_client(request: Request) -> EdinetClient:
    settings = request_data_source_settings(request, get_settings(), "edinet")
    if settings is get_settings():
        return edinet_client()
    return EdinetClient(settings)


@router.get("/documents", response_model=EdinetDocumentList)
def edinet_document_list(
    request: Request,
    filing_date: date,
    security_code: str | None = Query(default=None, max_length=5),
    edinet_code: str | None = Query(default=None, max_length=16),
    doc_type_code: list[str] | None = Query(default=None),
    csv_only: bool = False,
    downloadable_only: bool = True,
    limit: int = Query(default=500, ge=1, le=1000),
) -> EdinetDocumentList:
    try:
        return document_list(
            _request_client(request),
            filing_date,
            security_code=security_code,
            edinet_code=edinet_code,
            doc_type_codes=set(doc_type_code or []),
            csv_only=csv_only,
            downloadable_only=downloadable_only,
            limit=limit,
        )
    except Exception as exc:
        raise _translate_error(exc) from exc


@router.get("/index/history", response_model=EdinetFilingHistory)
def edinet_filing_history(
    start_date: date,
    end_date: date,
    security_code: str | None = Query(default=None, max_length=5),
    edinet_code: str | None = Query(default=None, max_length=16),
    doc_type_code: list[str] | None = Query(default=None),
    csv_only: bool = False,
    limit: int = Query(default=500, ge=1, le=1000),
    session: Session = Depends(db_session),
) -> EdinetFilingHistory:
    try:
        return filing_history(
            session,
            start_date,
            end_date,
            security_code=security_code,
            edinet_code=edinet_code,
            doc_type_codes=set(doc_type_code or []),
            csv_only=csv_only,
            limit=limit,
        )
    except Exception as exc:
        raise _translate_error(exc) from exc


@router.post("/index/sync", response_model=EdinetIndexSyncResult)
def edinet_index_sync(
    request: Request,
    start_date: date,
    end_date: date,
    session: Session = Depends(db_session),
) -> EdinetIndexSyncResult:
    try:
        return sync_filing_index(session, _request_client(request), start_date, end_date)
    except Exception as exc:
        raise _translate_error(exc) from exc


@router.get("/{doc_id}/financials", response_model=EdinetFinancials)
def edinet_financials(request: Request, doc_id: str) -> EdinetFinancials:
    try:
        return financials(_request_client(request), doc_id)
    except Exception as exc:
        raise _translate_error(exc) from exc


@router.get("/{doc_id}/facts", response_model=EdinetFactSearchResult)
def edinet_fact_search(
    request: Request,
    doc_id: str,
    q: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=200, ge=1, le=500),
) -> EdinetFactSearchResult:
    try:
        return search_facts(_request_client(request), doc_id, query=q, limit=limit)
    except Exception as exc:
        raise _translate_error(exc) from exc
