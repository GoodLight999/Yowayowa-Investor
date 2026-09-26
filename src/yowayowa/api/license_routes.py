from __future__ import annotations

from fastapi import APIRouter, Depends

from yowayowa.api.deps import require_api_token
from yowayowa.config import Settings, get_settings
from yowayowa.license_models import LicenseCatalog
from yowayowa.services.licensing import license_catalog

router = APIRouter(prefix="/v1/licensing", dependencies=[Depends(require_api_token)])


@router.get("/sources", response_model=LicenseCatalog)
def source_licenses(settings: Settings = Depends(get_settings)) -> LicenseCatalog:
    return license_catalog(settings.mode)
