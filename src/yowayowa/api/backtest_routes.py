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
from yowayowa.services.strategy_signals import compute_daily_strategy_signals
from yowayowa.stock_acquisition import StockOhlcvStore, default_store

router = APIRouter(prefix="/v1/backtest", dependencies=[Depends(require_api_token)])
strategy_router = APIRouter(prefix="/v1/screening", dependencies=[Depends(require_api_token)])


def _store() -> StockOhlcvStore:
    return default_store()


@router.get("/strategies", response_model=list[BacktestStrategyDefinition])
def strategies() -> list[BacktestStrategyDefinition]:
    return list_strategies()


@router.get("/signals/daily")
@strategy_router.get("/strategy")
def daily_signals() -> dict[str, Any]:
    """Rank persisted stock symbols using daily low-vol and mean-reversion signals."""
    try:
        enforce_provider_policy(
            AlpacaMarketDataProvider.descriptor,
            mode=get_settings().mode,
            allow_personal_in_public=get_settings().allow_personal_provider_in_public,
        )
    except ProviderPolicyError as exc:
        raise HTTPException(
            status_code=404, detail="Personal-only signals are unavailable"
        ) from exc
    store = _store()
    histories = {
        symbol: list(reversed(store.read(symbol, provider="alpaca", limit=10_000)))
        for symbol in store.list_symbols()
    }
    return compute_daily_strategy_signals(histories)


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
