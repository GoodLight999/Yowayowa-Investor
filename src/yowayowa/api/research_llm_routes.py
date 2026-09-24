"""LLM research brief / ask API surface (P5-A).

- ``POST /v1/research/ask``: deterministic cross-evidence lookup followed by
  one strict agent chat round. Returns answer + citations + tool trace.
- ``POST /v1/research/brief``: generate the morning brief (agent call) and
  persist it (delete+insert idempotent per run date).
- ``GET /v1/research/brief``: the latest persisted brief.

All routes are personal-only and fail closed with HTTP 403 outside personal
mode (same pattern as ``screening_routes._require_personal_mode``), and are
token-gated like every other personal surface. No route here ever invents a
value: missing inputs are recorded in ``coverage`` and rendered 未取得.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from yowayowa.api.deps import db_session, require_api_token
from yowayowa.config import get_settings
from yowayowa.providers.yahoo_screener import YahooScreenerProvider
from yowayowa.research_brief_models import ResearchAskResponse, ResearchBrief
from yowayowa.research_models import AIProviderConfig
from yowayowa.services.research_ask import research_ask
from yowayowa.services.research_brief import (
    MorningBriefService,
    persist_research_brief,
    read_latest_research_brief,
)

router = APIRouter(prefix="/v1/research", dependencies=[Depends(require_api_token)])


class ResearchAskHttpRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    provider: AIProviderConfig | None = None


class ResearchBriefHttpRequest(BaseModel):
    run_date: date | None = None
    provider: AIProviderConfig | None = None
    persist: bool = True


class ResearchBriefHttpResponse(BaseModel):
    brief: ResearchBrief
    persisted: dict[str, int] | None = None


def _require_personal_mode() -> None:
    settings = get_settings()
    try:
        # Same enforcement point as screening_routes: the personal-only
        # Yahoo screener descriptor is the mode probe; LLM briefs/answers are
        # grounded in personal scraped screening evidence, so the whole
        # surface stays personal-only.
        YahooScreenerProvider(settings)
    except Exception as exc:  # ProviderPolicyError -> fail closed for the surface
        raise HTTPException(
            status_code=403,
            detail="LLM research surfaces are grounded in personal-only evidence "
            "and are not served outside personal mode",
        ) from exc


@router.post("/ask", response_model=ResearchAskResponse)
def post_research_ask(
    payload: ResearchAskHttpRequest,
    session: Session = Depends(db_session),
) -> ResearchAskResponse:
    _require_personal_mode()
    settings = get_settings()
    try:
        return research_ask(
            payload.question,
            session,
            settings,
            provider_config=payload.provider,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=424, detail=str(exc)) from exc
    except Exception as exc:
        detail = f"Research ask error: {type(exc).__name__}: {exc}"
        raise HTTPException(status_code=502, detail=detail) from exc


@router.post("/brief", response_model=ResearchBriefHttpResponse)
def post_research_brief(
    payload: ResearchBriefHttpRequest,
    session: Session = Depends(db_session),
) -> ResearchBriefHttpResponse:
    _require_personal_mode()
    settings = get_settings()
    service = MorningBriefService(
        settings,
        session,
        provider_config=payload.provider,
    )
    try:
        brief = service.compose_brief(run_date=payload.run_date)
        persisted: dict[str, int] | None = None
        if payload.persist:
            persisted = persist_research_brief(session, brief)
    except RuntimeError as exc:
        raise HTTPException(status_code=424, detail=str(exc)) from exc
    except Exception as exc:
        detail = f"Brief generation error: {type(exc).__name__}: {exc}"
        raise HTTPException(status_code=502, detail=detail) from exc
    return ResearchBriefHttpResponse(brief=brief, persisted=persisted)


@router.get("/brief", response_model=ResearchBrief)
def get_research_brief(
    run_date: date | None = Query(default=None),
    session: Session = Depends(db_session),
) -> ResearchBrief:
    _require_personal_mode()
    if run_date is not None:
        from yowayowa.services.research_brief import read_research_brief

        brief = read_research_brief(session, run_date)
    else:
        brief = read_latest_research_brief(session)
    if brief is None:
        raise HTTPException(status_code=404, detail="No persisted research brief")
    return brief
