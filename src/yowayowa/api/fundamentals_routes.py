from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from yowayowa.api.deps import require_api_token
from yowayowa.config import Settings, get_settings
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
from yowayowa.providers.registry import sec_client, yahoo_market_provider
from yowayowa.providers.yahoo_fundamentals import YahooFundamentalsProvider
from yowayowa.services.comparison import compare
from yowayowa.services.screening import screen
from yowayowa.services.valuation import valuation_snapshot
from yowayowa.symbols import normalize_symbol

router = APIRouter(prefix="/v1", dependencies=[Depends(require_api_token)])


def _looks_like_non_us_listing(symbol: str) -> bool:
    """Recognize exchange-suffixed symbols without pretending this is a full exchange registry."""

    return "." in symbol


def _fundamentals(symbol: str, settings: Settings) -> Fundamentals:
    normalized = normalize_symbol(symbol)
    if settings.mode == "personal" and _looks_like_non_us_listing(normalized):
        return YahooFundamentalsProvider(settings).company_facts(normalized)
    return sec_client().company_facts(normalized)


@router.get("/fundamentals/{symbol}", response_model=Fundamentals)
def fundamentals_anywhere(
    symbol: str,
    settings: Settings = Depends(get_settings),
) -> Fundamentals:
    try:
        return _fundamentals(symbol, settings)
    except ProviderPolicyError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/valuation/{symbol}", response_model=ValuationSnapshot)
def valuation_anywhere(
    symbol: str,
    settings: Settings = Depends(get_settings),
) -> ValuationSnapshot:
    normalized = normalize_symbol(symbol)
    try:
        facts = _fundamentals(normalized, settings)
        quotes = yahoo_market_provider().quotes([normalized])
        return valuation_snapshot(facts, quotes)
    except ProviderPolicyError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/screen", response_model=ScreenResponse)
def screen_anywhere(
    payload: ScreenRequest,
    settings: Settings = Depends(get_settings),
) -> ScreenResponse:
    data: list[Fundamentals] = []
    failures: list[str] = []
    for symbol in payload.symbols:
        try:
            data.append(_fundamentals(symbol, settings))
        except Exception:
            failures.append(normalize_symbol(symbol))
    result = screen(data, payload.filters)
    for symbol in failures:
        result.rows.append(
            ScreenRow(symbol=symbol, metrics={}, matched=False, failures=["data_unavailable"])
        )
    return result


@router.post("/compare", response_model=ComparisonResponse)
def compare_anywhere(
    payload: ComparisonRequest,
    settings: Settings = Depends(get_settings),
) -> ComparisonResponse:
    data: list[Fundamentals] = []
    unavailable: list[str] = []
    for symbol in dict.fromkeys(normalize_symbol(item) for item in payload.symbols):
        try:
            data.append(_fundamentals(symbol, settings))
        except Exception:
            unavailable.append(symbol)
    if len(data) < 2:
        detail = "At least two issuers with comparable financial statements are required"
        if unavailable:
            detail += f"; unavailable: {', '.join(unavailable)}"
        raise HTTPException(status_code=422, detail=detail)
    return compare(data, payload.metrics or None)
