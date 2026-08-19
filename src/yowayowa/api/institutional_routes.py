from __future__ import annotations

from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, Query

from yowayowa.api.deps import require_api_token
from yowayowa.config import get_settings
from yowayowa.institutional_models import ThirteenFManagerReport
from yowayowa.providers.sec_13f import Sec13FProvider
from yowayowa.services.institutional import manager_report

router = APIRouter(prefix="/v1", dependencies=[Depends(require_api_token)])


@lru_cache(maxsize=1)
def _provider() -> Sec13FProvider:
    return Sec13FProvider(get_settings())


@router.get("/institutional/13f/{cik}", response_model=ThirteenFManagerReport)
def get_13f_manager_report(
    cik: str,
    quarters: int = Query(default=2, ge=1, le=8),
) -> ThirteenFManagerReport:
    try:
        return manager_report(_provider(), cik, quarters=quarters)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
