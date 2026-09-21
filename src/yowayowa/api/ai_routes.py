from __future__ import annotations

import secrets
from collections.abc import AsyncIterator

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from yowayowa.ai_network_policy import validate_ai_base_url
from yowayowa.api.deps import db_session, require_api_token
from yowayowa.config import Settings, get_settings
from yowayowa.research_models import (
    AIChatRequest,
    AIChatResponse,
    AIPromptPacketRequest,
    AIPromptPacketResponse,
    CodexCLIStatus,
    CodexSessionRequest,
)
from yowayowa.services.ai_agent import InvestmentResearchAgent
from yowayowa.services.codex_cli import (
    codex_bridge_session_status,
    codex_cli_status,
    start_codex_login,
)

router = APIRouter(prefix="/v1/ai", dependencies=[Depends(require_api_token)])

_CODEX_SESSION_COOKIE = "yowayowa_codex_session"


def _codex_session_id(request: Request) -> tuple[str, bool]:
    existing = request.cookies.get(_CODEX_SESSION_COOKIE)
    if existing and 20 <= len(existing) <= 200:
        return existing, False
    return secrets.token_urlsafe(32), True


def _set_codex_session_cookie(
    response: StreamingResponse,
    request: Request,
    session_id: str,
) -> None:
    response.set_cookie(
        _CODEX_SESSION_COOKIE,
        session_id,
        max_age=365 * 24 * 60 * 60,
        secure=request.url.scheme == "https",
        httponly=True,
        samesite="strict",
        path="/",
    )


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
    request: Request,
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
        codex_session = request.cookies.get(_CODEX_SESSION_COOKIE)
        return InvestmentResearchAgent(
            settings,
            session,
            codex_session_id=codex_session,
        ).chat(payload)
    except RuntimeError as exc:
        raise HTTPException(status_code=424, detail=str(exc)) from exc
    except Exception as exc:
        detail = f"AI provider error: {type(exc).__name__}: {exc}"
        raise HTTPException(status_code=502, detail=detail) from exc


@router.get("/codex/status", response_model=CodexCLIStatus)
def codex_status(settings: Settings = Depends(get_settings)) -> CodexCLIStatus:
    return codex_cli_status(settings)



@router.post("/codex/device-auth", response_class=StreamingResponse)
async def codex_device_auth(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    if not settings.codex_bridge_url:
        raise HTTPException(status_code=409, detail="Hosted Codex bridge is not configured")
    session_id, is_new = _codex_session_id(request)
    bridge_url = f"{settings.codex_bridge_url.rstrip('/')}/device-auth"

    async def stream() -> AsyncIterator[bytes]:
        try:
            async with (
                httpx.AsyncClient(timeout=None) as client,
                client.stream(
                    "POST",
                    bridge_url,
                    headers={"x-yowayowa-codex-session": session_id},
                ) as upstream,
            ):
                upstream.raise_for_status()
                async for chunk in upstream.aiter_bytes():
                    yield chunk
        except httpx.HTTPStatusError as exc:
            yield (
                '{"type":"error","message":"Hosted Codex auth returned HTTP '
                + str(exc.response.status_code)
                + '"}\n'
            ).encode()
        except httpx.HTTPError:
            yield b'{"type":"error","message":"Hosted Codex auth bridge failed"}\n'

    response = StreamingResponse(
        stream(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-store"},
    )
    if is_new:
        _set_codex_session_cookie(response, request, session_id)
    return response


@router.post("/codex/session-status", response_model=CodexCLIStatus)
def codex_session_status(
    payload: CodexSessionRequest,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> CodexCLIStatus:
    if not settings.codex_bridge_url:
        return codex_cli_status(settings)
    session_id = request.cookies.get(_CODEX_SESSION_COOKIE)
    if not session_id:
        return CodexCLIStatus(
            enabled=True,
            installed=True,
            authenticated=False,
            mode="hosted_bridge",
            reason="This browser needs to reconnect ChatGPT.",
        )
    try:
        return codex_bridge_session_status(
            settings,
            credential=payload.credential,
            session_id=session_id,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=424, detail=str(exc)) from exc

@router.post("/codex/login", response_model=CodexCLIStatus)
def codex_login(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> CodexCLIStatus:
    host = request.client.host if request.client is not None else ""
    if host not in {"127.0.0.1", "::1", "localhost", "testclient"}:
        raise HTTPException(
            status_code=403,
            detail="Codex browser login can only be started from the local machine.",
        )
    try:
        start_codex_login(settings)
    except RuntimeError as exc:
        raise HTTPException(status_code=424, detail=str(exc)) from exc
    return codex_cli_status(settings)


@router.post("/prompt-packet", response_model=AIPromptPacketResponse)
def ai_prompt_packet(
    payload: AIPromptPacketRequest,
    settings: Settings = Depends(get_settings),
    session: Session = Depends(db_session),
) -> AIPromptPacketResponse:
    try:
        return InvestmentResearchAgent(settings, session).prompt_packet(payload)
    except RuntimeError as exc:
        raise HTTPException(status_code=424, detail=str(exc)) from exc
    except Exception as exc:
        detail = f"Prompt packet error: {type(exc).__name__}: {exc}"
        raise HTTPException(status_code=502, detail=detail) from exc
