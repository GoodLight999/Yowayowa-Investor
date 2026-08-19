from __future__ import annotations

from fastapi import APIRouter, Depends

from yowayowa.api.deps import require_api_token
from yowayowa.config import Settings, get_settings

router = APIRouter(prefix="/v1/settings", dependencies=[Depends(require_api_token)])


@router.get("/status")
def settings_status(settings: Settings = Depends(get_settings)) -> dict[str, bool]:
    return {
        "sec": True,
        "yahoo_personal": settings.mode == "personal" or settings.allow_personal_provider_in_public,
        "edinet": bool(settings.edinet_api_key),
        "estat": bool(settings.estat_app_id),
        "fred": bool(settings.fred_api_key),
        "bea": bool(settings.bea_api_key),
        "allow_unlisted_ai_endpoints": settings.allow_unlisted_ai_endpoints,
    }
