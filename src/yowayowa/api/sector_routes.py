from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from yowayowa.api.deps import require_api_token
from yowayowa.domain import MarketOverview
from yowayowa.providers.base import ProviderPolicyError
from yowayowa.providers.registry import yahoo_sector_provider

router = APIRouter(prefix="/v1", dependencies=[Depends(require_api_token)])


@router.get("/markets/sectors", response_model=MarketOverview)
def sector_overview() -> MarketOverview:
    try:
        return yahoo_sector_provider().overview()
    except ProviderPolicyError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
