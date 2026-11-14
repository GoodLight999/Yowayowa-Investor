from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from yowayowa.api.deps import require_api_token
from yowayowa.providers.registry import treasury_yield_curve_provider
from yowayowa.rate_models import TreasuryYieldCurve
from yowayowa.vercel_observability import (
    UPSTREAM_UNAVAILABLE_DETAIL,
    log_upstream_exception,
)

router = APIRouter(prefix="/v1", dependencies=[Depends(require_api_token)])


@router.get("/rates/treasury/curve", response_model=TreasuryYieldCurve)
def treasury_curve(
    year: int | None = Query(default=None, ge=1990, le=2100),
) -> TreasuryYieldCurve:
    try:
        return treasury_yield_curve_provider().curve(year)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        log_upstream_exception(exc)
        raise HTTPException(status_code=502, detail=UPSTREAM_UNAVAILABLE_DETAIL) from exc
