from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from yowayowa.api.deps import request_data_source_settings, require_api_token
from yowayowa.bea_models import BeaNipaCatalog, BeaNipaTable
from yowayowa.bls_models import BlsCatalog, BlsSeries
from yowayowa.config import Settings, get_settings
from yowayowa.estat_models import EstatData, EstatMetadata, EstatTableSearch
from yowayowa.providers.base import ProviderPolicyError
from yowayowa.providers.bea import BeaClient
from yowayowa.providers.bls import BlsClient
from yowayowa.providers.estat import EstatClient

router = APIRouter(prefix="/v1/macro", dependencies=[Depends(require_api_token)])


def _estat_filters(values: list[str] | None) -> dict[str, str]:
    aliases = {
        "tab": "cd_tab",
        "time": "cd_time",
        "area": "cd_area",
        **{f"cat{index:02d}": f"cd_cat{index:02d}" for index in range(1, 16)},
    }
    result: dict[str, str] = {}
    for raw in values or []:
        key, separator, value = raw.partition("=")
        normalized_key = key.strip().lower()
        if not separator or not normalized_key or not value.strip():
            raise ValueError("e-Stat filter must look like area=00000 or cat01=0001")
        canonical = aliases.get(normalized_key, normalized_key)
        if canonical in result:
            raise ValueError(f"Duplicate e-Stat filter: {canonical}")
        result[canonical] = value.strip()
    return result


@router.get("/bls/catalog", response_model=BlsCatalog)
def bls_catalog(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> BlsCatalog:
    try:
        return BlsClient(request_data_source_settings(request, settings, "bls")).catalog()
    except ProviderPolicyError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/bls/{series_id}", response_model=BlsSeries)
def bls_series(
    request: Request,
    series_id: str,
    start_year: int | None = Query(default=None, ge=1900, le=9999),
    end_year: int | None = Query(default=None, ge=1900, le=9999),
    settings: Settings = Depends(get_settings),
) -> BlsSeries:
    try:
        scoped = request_data_source_settings(request, settings, "bls")
        return BlsClient(scoped).series(
            series_id,
            start_year=start_year,
            end_year=end_year,
        )
    except ProviderPolicyError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/bea/nipa/catalog", response_model=BeaNipaCatalog)
def bea_nipa_catalog(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> BeaNipaCatalog:
    try:
        return BeaClient(request_data_source_settings(request, settings, "bea")).catalog()
    except ProviderPolicyError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/bea/nipa/{table_name}", response_model=BeaNipaTable)
def bea_nipa_table(
    request: Request,
    table_name: str,
    frequency: str = Query(default="Q", pattern=r"^[AQMaqm]$"),
    years: list[int] | None = Query(default=None),
    line_number: int | None = Query(default=None, ge=1),
    settings: Settings = Depends(get_settings),
) -> BeaNipaTable:
    try:
        scoped = request_data_source_settings(request, settings, "bea")
        return BeaClient(scoped).nipa(
            table_name,
            frequency=frequency,
            years=years,
            line_number=line_number,
        )
    except ProviderPolicyError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=424, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/estat/tables", response_model=EstatTableSearch)
def estat_tables(
    request: Request,
    q: str = Query(min_length=1, max_length=200),
    lang: str = Query(default="J", pattern=r"^[JEje]$"),
    limit: int = Query(default=50, ge=1, le=100),
    settings: Settings = Depends(get_settings),
) -> EstatTableSearch:
    try:
        scoped = request_data_source_settings(request, settings, "estat")
        return EstatClient(scoped).search_tables(q, lang=lang, limit=limit)
    except ProviderPolicyError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=424, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=type(exc).__name__) from exc


@router.get("/estat/{stats_data_id}/meta", response_model=EstatMetadata)
def estat_metadata(
    request: Request,
    stats_data_id: str,
    lang: str = Query(default="J", pattern=r"^[JEje]$"),
    settings: Settings = Depends(get_settings),
) -> EstatMetadata:
    try:
        scoped = request_data_source_settings(request, settings, "estat")
        return EstatClient(scoped).metadata(stats_data_id, lang=lang)
    except ProviderPolicyError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=424, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=type(exc).__name__) from exc


@router.get("/estat/{stats_data_id}/data", response_model=EstatData)
def estat_data(
    request: Request,
    stats_data_id: str,
    lang: str = Query(default="J", pattern=r"^[JEje]$"),
    filter: list[str] | None = Query(default=None),
    limit: int = Query(default=5000, ge=1, le=10_000),
    start_position: int | None = Query(default=None, ge=1),
    settings: Settings = Depends(get_settings),
) -> EstatData:
    try:
        scoped = request_data_source_settings(request, settings, "estat")
        return EstatClient(scoped).data(
            stats_data_id,
            lang=lang,
            filters=_estat_filters(filter),
            limit=limit,
            start_position=start_position,
        )
    except ProviderPolicyError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=424, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=type(exc).__name__) from exc
