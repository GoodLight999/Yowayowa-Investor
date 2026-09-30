from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from yowayowa.api.deps import require_api_token
from yowayowa.backtest_models import (
    BacktestRunRequest,
    BacktestRunResponse,
    BacktestStrategyDefinition,
)
from yowayowa.config import get_settings
from yowayowa.providers.alpaca import AlpacaMarketDataProvider
from yowayowa.providers.base import ProviderPolicyError, enforce_provider_policy
from yowayowa.services.backtest import run_backtest
from yowayowa.services.backtest_definitions import get_strategy, list_strategies
from yowayowa.stock_acquisition import StockOhlcvStore, default_store

router = APIRouter(prefix="/v1/backtest", dependencies=[Depends(require_api_token)])


def _store() -> StockOhlcvStore:
    return default_store()


@router.get("/strategies", response_model=list[BacktestStrategyDefinition])
def strategies() -> list[BacktestStrategyDefinition]:
    return list_strategies()


@router.post("/run", response_model=BacktestRunResponse)
def run(payload: BacktestRunRequest) -> BacktestRunResponse:
    try:
        enforce_provider_policy(
            AlpacaMarketDataProvider.descriptor,
            mode=get_settings().mode,
            allow_personal_in_public=get_settings().allow_personal_provider_in_public,
        )
    except ProviderPolicyError as exc:
        raise HTTPException(
            status_code=404,
            detail="Personal-only OHLCV backtests are unavailable outside personal mode",
        ) from exc
    try:
        strategy = get_strategy(payload.strategy_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    store = _store()
    histories: dict[str, list[dict[str, Any]]] = {}
    for symbol in strategy.universe:
        histories[symbol] = store.read(symbol, provider=payload.provider, limit=10_000)
    try:
        return run_backtest(payload, strategy, histories)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
