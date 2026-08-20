from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from yowayowa.ai_network_policy import validate_ai_base_url
from yowayowa.api.deps import db_session, require_api_token
from yowayowa.config import Settings, get_settings
from yowayowa.research_models import AIChatRequest, AIChatResponse
from yowayowa.services.ai_agent import InvestmentResearchAgent

router = APIRouter(prefix="/v1/ai", dependencies=[Depends(require_api_token)])


@router.get("/status")
def ai_status(
    settings: Settings = Depends(get_settings),
    session: Session = Depends(db_session),
) -> dict[str, object]:
    status = InvestmentResearchAgent(settings, session).status()
    status["allow_unlisted_endpoints"] = settings.allow_unlisted_ai_endpoints
    return status


@router.post("/chat", response_model=AIChatResponse)
def ai_chat(
    payload: AIChatRequest,
    settings: Settings = Depends(get_settings),
    session: Session = Depends(db_session),
) -> AIChatResponse:
    if payload.provider is not None and payload.provider.base_url:
        try:
            safe_url = validate_ai_base_url(
                payload.provider.base_url,
                allow_unlisted=settings.allow_unlisted_ai_endpoints,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        payload = payload.model_copy(
            update={"provider": payload.provider.model_copy(update={"base_url": safe_url})}
        )
    try:
        return InvestmentResearchAgent(settings, session).chat(payload)
    except RuntimeError as exc:
        raise HTTPException(status_code=424, detail=str(exc)) from exc
    except Exception as exc:
        detail = f"AI provider error: {type(exc).__name__}: {exc}"
        raise HTTPException(status_code=502, detail=detail) from exc
