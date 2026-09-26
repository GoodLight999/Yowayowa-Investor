"""Read-only API surface for weekly credit margin balances (P4-C).

Serves weekly credit balances persisted by
``services/credit_margin.persist_credit_margin_weekly`` — this router never
fetches. The data is personal-only scraped data
(``LicenseClass.PERSONAL_ONLY``), so the surface fails closed outside
personal mode: every route refuses with HTTP 403 via the provider policy
(mirroring ``jpx_routes._require_personal_mode``) before any row is read.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query

from yowayowa.api.deps import require_api_token
from yowayowa.config import get_settings
from yowayowa.credit_margin_models import (
    CreditMarginWeeklySeries,
    normalize_credit_margin_code,
)
from yowayowa.db import get_session
from yowayowa.providers.base import ProviderPolicyError
from yowayowa.providers.credit_margin_kabutan import enforce_credit_margin_kabutan_policy
from yowayowa.providers.credit_margin_yahoo import enforce_credit_margin_yahoo_policy
from yowayowa.services.credit_margin import (
    latest_credit_margin_week,
    read_credit_margin_by_code,
    read_credit_margin_by_date,
)

router = APIRouter(prefix="/v1/credit", dependencies=[Depends(require_api_token)])

_DEFAULT_HISTORY_LIMIT = 20
_MAX_HISTORY_LIMIT = 250


def _require_personal_mode() -> None:
    settings = get_settings()
    try:
        enforce_credit_margin_yahoo_policy(mode=settings.mode)
        enforce_credit_margin_kabutan_policy(mode=settings.mode)
    except ProviderPolicyError as exc:
        raise HTTPException(
            status_code=403,
            detail="Credit margin balances are personal-only scraped data and are "
            "not served outside personal mode",
        ) from exc


@router.get("/margin/latest", response_model=list[CreditMarginWeeklySeries])
def get_credit_margin_latest(
    limit: int = Query(default=_DEFAULT_HISTORY_LIMIT, ge=1, le=_MAX_HISTORY_LIMIT),
) -> list[CreditMarginWeeklySeries]:
    """All persisted weekly balances for the most recent as-of week."""

    _require_personal_mode()
    session = get_session()
    try:
        as_of = latest_credit_margin_week(session)
        if as_of is None:
            raise HTTPException(status_code=404, detail="No credit margin weeks are persisted")
        points = read_credit_margin_by_date(session, as_of)
    finally:
        session.close()
    return [CreditMarginWeeklySeries(code=point.code, points=[point]) for point in points]


@router.get("/margin/{code}", response_model=CreditMarginWeeklySeries)
def get_credit_margin_history(
    code: str,
    limit: int = Query(default=_DEFAULT_HISTORY_LIMIT, ge=1, le=_MAX_HISTORY_LIMIT),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
) -> CreditMarginWeeklySeries:
    """Weekly balance history for one code, oldest first (derived read-time)."""

    _require_personal_mode()
    try:
        normalized = normalize_credit_margin_code(code)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if date_from is not None and date_to is not None and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from must be on or before date_to")
    session = get_session()
    try:
        return read_credit_margin_by_code(
            session,
            normalized,
            limit=limit,
            date_from=date_from,
            date_to=date_to,
        )
    finally:
        session.close()


@router.get("/margin/date/{as_of_date}", response_model=list[CreditMarginWeeklySeries])
def get_credit_margin_for_date(as_of_date: date) -> list[CreditMarginWeeklySeries]:
    """All persisted weekly balances for one as-of week, code order."""

    _require_personal_mode()
    session = get_session()
    try:
        points = read_credit_margin_by_date(session, as_of_date)
    finally:
        session.close()
    if not points:
        raise HTTPException(
            status_code=404,
            detail=f"No credit margin balances persisted for {as_of_date.isoformat()}",
        )
    return [
        CreditMarginWeeklySeries(
            code=point.code,
            points=[point],
        )
        for point in points
    ]
