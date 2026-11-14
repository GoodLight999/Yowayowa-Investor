"""Read-only API surface for JPX daily issue-level margin balances (P4-A).

Serves balances persisted by ``services/jpx_margin.ingest_jpx_margin_csv`` —
this router never fetches: live ingestion is a separate, later task. The data
is contracted JPX reference data (``LicenseClass.PERSONAL_ONLY``), so the
surface fails closed outside personal mode: every route refuses with HTTP 403
via :func:`yowayowa.providers.jpx_margin.enforce_jpx_margin_policy` before any
row is read.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query

from yowayowa.api.deps import require_api_token
from yowayowa.config import get_settings
from yowayowa.db import get_session
from yowayowa.jpx_models import JpxMarginSeries, normalize_jpx_code
from yowayowa.providers.jpx_margin import enforce_jpx_margin_policy
from yowayowa.services.jpx_margin import (
    latest_jpx_margin_date,
    read_jpx_margin_by_code,
    read_jpx_margin_by_date,
)

router = APIRouter(prefix="/v1/jpx", dependencies=[Depends(require_api_token)])

_DEFAULT_HISTORY_LIMIT = 30
_MAX_HISTORY_LIMIT = 250


def _require_personal_mode() -> None:
    settings = get_settings()
    try:
        enforce_jpx_margin_policy(mode=settings.mode)
    except Exception as exc:  # ProviderPolicyError -> fail closed for the surface
        raise HTTPException(
            status_code=403,
            detail="JPX margin balances are personal-only contracted data and are "
            "not served outside personal mode",
        ) from exc


@router.get("/margin/latest", response_model=list[JpxMarginSeries])
def get_jpx_margin_latest(
    limit: int = Query(default=_DEFAULT_HISTORY_LIMIT, ge=1, le=_MAX_HISTORY_LIMIT),
) -> list[JpxMarginSeries]:
    """All persisted issue balances for the most recent application date."""

    _require_personal_mode()
    session = get_session()
    try:
        application_date = latest_jpx_margin_date(session)
        if application_date is None:
            raise HTTPException(status_code=404, detail="No JPX margin balances are persisted")
        points = read_jpx_margin_by_date(session, application_date)
    finally:
        session.close()
    return [JpxMarginSeries(code=point.code, points=[point]) for point in points]


@router.get("/margin/{code}", response_model=JpxMarginSeries)
def get_jpx_margin_history(
    code: str,
    limit: int = Query(default=_DEFAULT_HISTORY_LIMIT, ge=1, le=_MAX_HISTORY_LIMIT),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
) -> JpxMarginSeries:
    """Balance history for one code, oldest first (derived fields read-time)."""

    _require_personal_mode()
    try:
        normalized = normalize_jpx_code(code)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if date_from is not None and date_to is not None and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from must be on or before date_to")
    session = get_session()
    try:
        return read_jpx_margin_by_code(
            session,
            normalized,
            limit=limit,
            date_from=date_from,
            date_to=date_to,
        )
    finally:
        session.close()


@router.get("/margin", response_model=list[JpxMarginSeries])
def list_jpx_margin_history(
    codes: str = Query(
        description="Comma-separated JPX local codes, e.g. 13010,72030",
    ),
    limit: int = Query(default=_DEFAULT_HISTORY_LIMIT, ge=1, le=_MAX_HISTORY_LIMIT),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
) -> list[JpxMarginSeries]:
    """Balance history for several codes (batch variant of the code route)."""

    _require_personal_mode()
    normalized: list[str] = []
    for raw in codes.split(","):
        raw = raw.strip()
        if not raw:
            continue
        try:
            normalized.append(normalize_jpx_code(raw))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not normalized:
        raise HTTPException(status_code=422, detail="codes must contain at least one JPX code")
    session = get_session()
    try:
        return [
            read_jpx_margin_by_code(
                session,
                code,
                limit=limit,
                date_from=date_from,
                date_to=date_to,
            )
            for code in normalized
        ]
    finally:
        session.close()


@router.get("/margin/date/{application_date}", response_model=list[JpxMarginSeries])
def get_jpx_margin_for_date(application_date: date) -> list[JpxMarginSeries]:
    """All persisted issue balances for one application date, code order."""

    _require_personal_mode()
    session = get_session()
    try:
        points = read_jpx_margin_by_date(session, application_date)
    finally:
        session.close()
    if not points:
        raise HTTPException(
            status_code=404,
            detail=f"No JPX margin balances persisted for {application_date.isoformat()}",
        )
    return [
        JpxMarginSeries(
            code=point.code,
            points=[point],
        )
        for point in points
    ]
