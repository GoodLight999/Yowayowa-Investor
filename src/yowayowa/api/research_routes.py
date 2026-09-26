from __future__ import annotations

from datetime import date
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from yowayowa.api.deps import db_session, request_data_source_settings, require_api_token
from yowayowa.calendar_models import TrackedScope
from yowayowa.config import Settings, get_settings
from yowayowa.news_models import SavedNewsFeed
from yowayowa.preset_models import PresetKind, ResearchPreset, ResearchPresetCreate
from yowayowa.providers.base import ProviderPolicyError
from yowayowa.providers.fred import FredClient
from yowayowa.providers.yahoo_deep_research import YahooDeepResearchProvider
from yowayowa.providers.yahoo_research import YahooResearchProvider
from yowayowa.providers.yahoo_screener import YahooScreenerProvider
from yowayowa.research_models import (
    CompanyResearch,
    MarketScreenRequest,
    MarketScreenResponse,
    OptionChainSnapshot,
    ResearchSection,
)
from yowayowa.services.news import saved_news_feed
from yowayowa.services.presets import (
    create_preset,
    delete_preset,
    get_preset,
    list_presets,
    update_preset,
)

router = APIRouter(prefix="/v1", dependencies=[Depends(require_api_token)])


@router.get("/research-presets", response_model=list[ResearchPreset])
def research_presets(
    kind: PresetKind | None = Query(default=None),
    session: Session = Depends(db_session),
) -> list[ResearchPreset]:
    return list_presets(session, kind)


@router.get("/research-presets/{preset_id}", response_model=ResearchPreset)
def research_preset(preset_id: int, session: Session = Depends(db_session)) -> ResearchPreset:
    try:
        return get_preset(session, preset_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/research-presets", response_model=ResearchPreset, status_code=201)
def post_research_preset(
    payload: ResearchPresetCreate,
    session: Session = Depends(db_session),
) -> ResearchPreset:
    try:
        return create_preset(session, payload.name, payload.kind, payload.payload)
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"A {payload.kind} preset named '{payload.name}' already exists",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/research-presets/{preset_id}", response_model=ResearchPreset)
def put_research_preset(
    preset_id: int,
    payload: ResearchPresetCreate,
    session: Session = Depends(db_session),
) -> ResearchPreset:
    try:
        return update_preset(session, preset_id, payload.name, payload.kind, payload.payload)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"A {payload.kind} preset named '{payload.name}' already exists",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/research-presets/{preset_id}", response_model=ResearchPreset)
def remove_research_preset(
    preset_id: int, session: Session = Depends(db_session)
) -> ResearchPreset:
    try:
        existing = get_preset(session, preset_id)
        delete_preset(session, preset_id)
        return existing
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/news/saved", response_model=SavedNewsFeed)
def saved_news(
    scope: TrackedScope = Query(default="all"),
    scope_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=60, ge=1, le=200),
    per_symbol_limit: int = Query(default=6, ge=1, le=20),
    session: Session = Depends(db_session),
    settings: Settings = Depends(get_settings),
) -> SavedNewsFeed:
    if scope == "all" and scope_id is not None:
        raise HTTPException(status_code=422, detail="scope_id is not valid for all scope")
    if scope != "all" and scope_id is None:
        raise HTTPException(status_code=422, detail=f"scope_id is required for {scope} scope")
    try:
        return saved_news_feed(
            session,
            YahooResearchProvider(settings),
            scope,
            scope_id,
            limit,
            per_symbol_limit,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ProviderPolicyError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/research/{symbol}", response_model=CompanyResearch)
def company_research(
    symbol: str,
    sections: str | None = Query(default=None, max_length=240),
    settings: Settings = Depends(get_settings),
) -> CompanyResearch:
    requested: list[ResearchSection] | None = None
    if sections:
        raw = [token.strip().lower() for token in sections.split(",") if token.strip()]
        allowed = {section.value for section in ResearchSection}
        unknown = sorted(set(raw) - allowed)
        if unknown:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown research sections: {', '.join(unknown)}",
            )
        requested = [ResearchSection(token) for token in dict.fromkeys(raw)]
    try:
        return YahooDeepResearchProvider(settings).research(symbol, requested)
    except ProviderPolicyError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/options/{symbol}", response_model=OptionChainSnapshot)
def option_chain(
    symbol: str,
    expiration: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    settings: Settings = Depends(get_settings),
) -> OptionChainSnapshot:
    try:
        return YahooDeepResearchProvider(settings).option_chain(symbol, expiration)
    except ProviderPolicyError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/discover/catalog")
def discover_catalog(settings: Settings = Depends(get_settings)) -> dict[str, object]:
    try:
        return cast(dict[str, object], YahooScreenerProvider(settings).catalog())
    except ProviderPolicyError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/discover/screen", response_model=MarketScreenResponse)
def discover_screen(
    payload: MarketScreenRequest,
    settings: Settings = Depends(get_settings),
) -> MarketScreenResponse:
    try:
        return YahooScreenerProvider(settings).screen(payload)
    except ProviderPolicyError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/macro/fred/search")
def fred_search(
    request: Request,
    q: str = Query(min_length=1, max_length=200),
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=100000),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    try:
        scoped = request_data_source_settings(request, settings, "fred")
        return cast(dict[str, object], FredClient(scoped).search(q, limit, offset))
    except ProviderPolicyError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=424, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/macro/fred/releases")
def fred_releases(
    request: Request,
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0, le=100000),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    try:
        scoped = request_data_source_settings(request, settings, "fred")
        return cast(dict[str, object], FredClient(scoped).release_dates(limit, offset))
    except ProviderPolicyError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=424, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/macro/fred/{series_id}")
def fred_series(
    request: Request,
    series_id: str,
    observation_start: date | None = None,
    observation_end: date | None = None,
    units: str | None = Query(default=None, max_length=16),
    frequency: str | None = Query(default=None, max_length=16),
    aggregation_method: str | None = Query(default=None, max_length=16),
    limit: int = Query(default=5000, ge=1, le=100000),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    try:
        scoped = request_data_source_settings(request, settings, "fred")
        return cast(
            dict[str, object],
            FredClient(scoped).series(
                series_id,
                limit=limit,
                observation_start=observation_start,
                observation_end=observation_end,
                units=units,
                frequency=frequency,
                aggregation_method=aggregation_method,
            ),
        )
    except ProviderPolicyError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=424, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
