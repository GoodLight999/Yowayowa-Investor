from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from yowayowa.orderbook_models import (
    ExecutionImpactRequest,
    ExecutionImpactResult,
    OrderbookDepthLadder,
    OrderbookMetrics,
    OrderbookSnapshot,
)
from yowayowa.services.orderbook_service import OrderbookService

router = APIRouter(prefix="/v1/orderbook", tags=["orderbook"])
_service = OrderbookService()


@router.get(
    "/{symbol}",
    response_model=OrderbookSnapshot,
    summary="Get latest L2 orderbook snapshot",
    operation_id="get_orderbook_snapshot",
)
def get_orderbook(symbol: str) -> OrderbookSnapshot:
    """Retrieve the latest Level-2 orderbook with top bids, asks, and microstructure metrics."""
    clean_sym = symbol.strip()
    if not clean_sym:
        raise HTTPException(status_code=400, detail="Symbol cannot be empty.")
    return _service.get_snapshot(clean_sym)


@router.get(
    "/{symbol}/depth",
    response_model=OrderbookDepthLadder,
    summary="Get cumulative market depth ladder",
    operation_id="get_orderbook_depth_ladder",
)
def get_orderbook_depth(
    symbol: str,
    max_levels: Annotated[int, Query(ge=1, le=100, description="Max levels per side")] = 20,
) -> OrderbookDepthLadder:
    """Retrieve cumulative volume ladder for market depth visualization."""
    clean_sym = symbol.strip()
    if not clean_sym:
        raise HTTPException(status_code=400, detail="Symbol cannot be empty.")
    return _service.get_depth_ladder(clean_sym, max_levels=max_levels)


@router.get(
    "/{symbol}/metrics",
    response_model=OrderbookMetrics,
    summary="Get microstructure and liquidity metrics",
    operation_id="get_orderbook_metrics",
)
def get_orderbook_metrics_endpoint(symbol: str) -> OrderbookMetrics:
    """Retrieve spread, order flow imbalance, and HFT activity indicators."""
    clean_sym = symbol.strip()
    if not clean_sym:
        raise HTTPException(status_code=400, detail="Symbol cannot be empty.")
    return _service.get_metrics(clean_sym)


@router.post(
    "/{symbol}/impact",
    response_model=ExecutionImpactResult,
    summary="Simulate market order execution impact and slippage",
    operation_id="estimate_orderbook_execution_impact",
)
def estimate_execution_impact_endpoint(
    symbol: str,
    payload: ExecutionImpactRequest,
) -> ExecutionImpactResult:
    """Walk the current orderbook to estimate market impact, average fill price, and slippage."""
    clean_sym = symbol.strip()
    if not clean_sym:
        raise HTTPException(status_code=400, detail="Symbol cannot be empty.")
    return _service.estimate_impact(clean_sym, side=payload.side, quantity=payload.quantity)


@router.post(
    "/{symbol}/snapshots",
    response_model=OrderbookSnapshot,
    summary="Record or update an orderbook snapshot",
    operation_id="record_orderbook_snapshot_endpoint",
)
def record_orderbook_snapshot_endpoint(
    symbol: str,
    snapshot: OrderbookSnapshot,
) -> OrderbookSnapshot:
    """Ingest and persist a newly captured orderbook snapshot."""
    clean_sym = symbol.strip()
    if not clean_sym or clean_sym != snapshot.symbol:
        raise HTTPException(status_code=400, detail="Path symbol does not match snapshot symbol.")
    return _service.record_snapshot(snapshot)
