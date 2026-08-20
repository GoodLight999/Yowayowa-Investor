from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from yowayowa.api.deps import require_api_token
from yowayowa.domain import (
    ComparisonRequest,
    ComparisonResponse,
    Fundamentals,
    ScreenRequest,
    ScreenResponse,
    ScreenRow,
    ValuationSnapshot,
)
from yowayowa.providers.base import ProviderPolicyError
from yowayowa.providers.registry import fundamentals_provider, yahoo_market_provider
from yowayowa.services.comparison import compare
from yowayowa.services.screening import screen
from yowayowa.services.valuation import valuation_snapshot
from yowayowa.symbols import normalize_symbol

router = APIRouter(prefix="/v1", dependencies=[Depends(require_api_token)])


def _fundamentals(symbol: str) -> Fundamentals:
    return fundamentals_provider().company_facts(normalize_symbol(symbol))


@router.get("/fundamentals/{symbol}", response_model=Fundamentals)
def fundamentals_anywhere(symbol: str) -> Fundamentals:
    try:
        return _fundamentals(symbol)
    except ProviderPolicyError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/valuation/{symbol}", response_model=ValuationSnapshot)
def valuation_anywhere(symbol: str) -> ValuationSnapshot:
    normalized = normalize_symbol(symbol)
    try:
        facts = _fundamentals(normalized)
        quotes = yahoo_market_provider().quotes([normalized])
        return valuation_snapshot(facts, quotes)
    except ProviderPolicyError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/screen", response_model=ScreenResponse)
def screen_anywhere(payload: ScreenRequest) -> ScreenResponse:
    data: list[Fundamentals] = []
    failures: list[str] = []
    for symbol in payload.symbols:
        try:
            data.append(_fundamentals(symbol))
        except Exception:
            failures.append(normalize_symbol(symbol))
    result = screen(data, payload.filters)
    for symbol in failures:
        result.rows.append(
            ScreenRow(symbol=symbol, metrics={}, matched=False, failures=["data_unavailable"])
        )
    return result


@router.post("/compare", response_model=ComparisonResponse)
def compare_anywhere(payload: ComparisonRequest) -> ComparisonResponse:
    data: list[Fundamentals] = []
    unavailable: list[str] = []
    for symbol in dict.fromkeys(normalize_symbol(item) for item in payload.symbols):
        try:
            data.append(_fundamentals(symbol))
        except Exception:
            unavailable.append(symbol)
    if len(data) < 2:
        detail = "At least two issuers with comparable financial statements are required"
        if unavailable:
            detail += f"; unavailable: {', '.join(unavailable)}"
        raise HTTPException(status_code=422, detail=detail)
    return compare(data, payload.metrics or None)
