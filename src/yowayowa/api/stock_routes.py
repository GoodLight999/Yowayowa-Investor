"""Read-only US stock daily-OHLCV API surface (P4-F).

Serves rows persisted by ``stock_acquisition.StockOhlcvStore`` — this router
never fetches from the network. The Alpaca source is a commercial
broker/data-vendor with a PERSONAL_ONLY classification, so the surface fails
closed outside personal mode with HTTP 404 (matching the crypto split),
before any row is read.

Routes live under the dedicated ``/v1/stocks`` prefix. ``/latest`` is
declared before ``/{symbol}/bars`` so the literal segment can never be
captured by the parameterized path (P4-A acceptance finding).
"""

from __future__ import annotations

import csv
import io
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse

from yowayowa.api.deps import require_api_token
from yowayowa.config import get_settings
from yowayowa.providers.alpaca import AlpacaMarketDataProvider
from yowayowa.providers.base import enforce_provider_policy
from yowayowa.stock_acquisition import default_store
from yowayowa.stock_models import normalize_stock_symbol

router = APIRouter(prefix="/v1/stocks", dependencies=[Depends(require_api_token)])

_DEFAULT_LIMIT = 30
_MAX_LIMIT = 250


def _require_personal_mode() -> None:
    """Stock sources are personal-only: fail closed outside personal mode.

    HTTP 404 (not 403) so public mode does not even confirm the existence of
    stock data behind a token, matching the crypto surface.
    """

    settings = get_settings()
    try:
        enforce_provider_policy(
            AlpacaMarketDataProvider.descriptor,
            mode=settings.mode,
            allow_personal_in_public=settings.allow_personal_provider_in_public,
        )
    except Exception as exc:  # ProviderPolicyError -> fail closed for the surface
        raise HTTPException(
            status_code=404,
            detail="Stock OHLCV sources are personal-only and are not served outside personal mode",
        ) from exc


def _filtered_rows(symbol: str, provider: str | None, limit: int) -> list[dict[str, Any]]:
    normalized_provider = provider.strip().lower() if provider else None
    return default_store().read(symbol, provider=normalized_provider, limit=limit)


def _csv_response(symbol: str, rows: list[dict[str, Any]]) -> PlainTextResponse:
    buffer = io.StringIO()
    fieldnames = list(rows[0].keys())
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return PlainTextResponse(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{symbol}-ohlcv.csv"'},
    )


@router.get("/{symbol}/bars/latest")
def get_stock_ohlcv_latest(
    symbol: str,
    provider: Annotated[str | None, Query(description="Filter by provider, e.g. alpaca")] = None,
) -> object:
    """Newest persisted daily bar for one symbol (single row)."""

    _require_personal_mode()
    try:
        normalized = normalize_stock_symbol(symbol)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    rows = _filtered_rows(normalized, provider, limit=1)
    if not rows:
        raise HTTPException(
            status_code=404, detail=f"No persisted stock OHLCV rows for {normalized}"
        )
    return rows[0]


@router.get("/{symbol}/bars")
def get_stock_ohlcv(
    symbol: str,
    provider: Annotated[str | None, Query(description="Filter by provider, e.g. alpaca")] = None,
    format: Literal["json", "csv"] = Query(default="json"),
    limit: int = Query(default=_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
) -> object:
    """Persisted daily OHLCV rows for one symbol, newest first, per-source separated."""

    _require_personal_mode()
    try:
        normalized = normalize_stock_symbol(symbol)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    rows = _filtered_rows(normalized, provider, limit)
    if not rows:
        raise HTTPException(
            status_code=404, detail=f"No persisted stock OHLCV rows for {normalized}"
        )
    if format == "csv":
        return _csv_response(normalized, rows)
    return rows
