from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

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
    return InvestmentResearchAgent(settings, session).status()


@router.post("/chat", response_model=AIChatResponse)
def ai_chat(
    payload: AIChatRequest,
    settings: Settings = Depends(get_settings),
    session: Session = Depends(db_session),
) -> AIChatResponse:
    try:
        return InvestmentResearchAgent(settings, session).chat(payload)
    except RuntimeError as exc:
        raise HTTPException(status_code=424, detail=str(exc)) from exc
    except Exception as exc:
        detail = f"AI provider error: {type(exc).__name__}: {exc}"
        raise HTTPException(status_code=502, detail=detail) from exc
