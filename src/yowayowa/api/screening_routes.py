"""Read/Delete API surface for machine screening (P4-D).

Exposes the screening pipeline run and the persisted candidates:

- ``POST /v1/screening/run`` executes the full pipeline once and persists the
  result (idempotent per run date). Runs regardless of EDINET/credit-margin
  coverage; the market screener is personal-only (Yahoo custom screener), so
  the whole POST fails closed outside personal mode with HTTP 403 —
  mirroring ``jpx_routes``/``credit_routes``.
- ``GET /v1/screening/candidates`` reads persisted candidates with their
  read-provenance; same personal-mode gate.

Every route refuses with HTTP 403 outside personal mode before any row is
read or any provider is touched.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from yowayowa.api.deps import require_api_token
from yowayowa.config import get_settings
from yowayowa.db import get_session
from yowayowa.providers.yahoo_screener import YahooScreenerProvider
from yowayowa.screening_models import ScreeningRunResult
from yowayowa.services.screening_pipeline import (
    persist_screening_run,
    read_screening_candidates,
    run_screening_pipeline,
)

router = APIRouter(prefix="/v1/screening", dependencies=[Depends(require_api_token)])

_DEFAULT_CANDIDATES_LIMIT = 50
_MAX_CANDIDATES_LIMIT = 250


class ScreeningRunResponse(BaseModel):
    """One pipeline run plus the persistence counts."""

    result: ScreeningRunResult
    persisted: dict[str, int]


def _require_personal_mode() -> None:
    settings = get_settings()
    try:
        # The screener is the only provider policy surface in the pipeline;
        # its descriptor (personal-only) is the enforcement point, exactly
        # like the provider itself at construction time.
        YahooScreenerProvider(settings)
    except Exception as exc:  # ProviderPolicyError -> fail closed for the surface
        raise HTTPException(
            status_code=403,
            detail="Machine screening includes personal-only scraped data and is "
            "not served outside personal mode",
        ) from exc


@router.post("/run", response_model=ScreeningRunResponse)
def post_screening_run() -> ScreeningRunResponse:
    """Run the machine screening pipeline once and persist the result."""

    _require_personal_mode()
    session = get_session()
    try:
        result = run_screening_pipeline(session, market_size=50)
        persisted = persist_screening_run(session, result)
    finally:
        session.close()
    return ScreeningRunResponse(result=result, persisted=persisted)


@router.get("/candidates")
def get_screening_candidates(
    run_date: date | None = Query(default=None),
    source: str | None = Query(default=None, max_length=32),
    signal: str | None = Query(default=None, max_length=64),
    limit: int = Query(
        default=_DEFAULT_CANDIDATES_LIMIT,
        ge=1,
        le=_MAX_CANDIDATES_LIMIT,
    ),
) -> list[dict[str, object]]:
    """Persisted screening candidates (read-provenance included)."""

    _require_personal_mode()
    session = get_session()
    try:
        return read_screening_candidates(
            session,
            run_date=run_date,
            source=source,
            signal=signal,
            limit=limit,
        )
    finally:
        session.close()
