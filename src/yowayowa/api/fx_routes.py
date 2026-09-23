from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from yowayowa.api.deps import require_api_token
from yowayowa.fx_models import (
    FxDirection,
    FxHistory,
    FxProposalSpec,
    FxRateSnapshot,
    build_fx_proposal,
    normalize_fx_pair,
)
from yowayowa.providers.registry import yahoo_market_provider

router = APIRouter(prefix="/v1", dependencies=[Depends(require_api_token)])


@router.get("/fx/rate", response_model=FxRateSnapshot)
def fx_rate(
    pair: Annotated[str, Query(min_length=6, max_length=6, description="FX pair, e.g. USDJPY")],
) -> FxRateSnapshot:
    normalized = normalize_fx_pair(pair)
    try:
        return yahoo_market_provider().fx_quote(normalized)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/fx/history", response_model=FxHistory)
def fx_history(
    pair: Annotated[str, Query(min_length=6, max_length=6, description="FX pair, e.g. USDJPY")],
    interval: str = Query(default="1d", pattern=r"^(?:1m|5m|15m|30m|1h|1d|1wk|1mo)$"),
    period: str = Query(default="1mo", pattern=r"^(?:1d|5d|1mo|3mo|6mo|1y|2y|5y|max)$"),
) -> FxHistory:
    normalized = normalize_fx_pair(pair)
    try:
        return yahoo_market_provider().fx_history(
            normalized,
            interval=interval,
            period=period,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/fx/proposal", response_model=FxProposalSpec)
def fx_proposal(
    pair: Annotated[str, Query(min_length=6, max_length=6, description="FX pair, e.g. USDJPY")],
    direction: FxDirection = Query(default=FxDirection.FLAT),
    strength: float | None = Query(default=None, ge=0.0, le=1.0),
) -> FxProposalSpec:
    """Propose-only FX analysis artifact (never an execution instruction)."""

    normalized = normalize_fx_pair(pair)
    try:
        quote = yahoo_market_provider().fx_quote(normalized)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return build_fx_proposal(
        normalized,
        direction,
        strength,
        quote,
    )
