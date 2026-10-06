"""Read-only crypto daily-OHLCV API surface (P4-E phase 1).

Serves rows persisted by ``crypto_acquisition.CryptoOhlcvStore`` — this
router never fetches from the network. Sources are commercial aggregators
with a PERSONAL_ONLY classification, so the surface fails closed outside
personal mode with HTTP 404 (matching the Yahoo crypto split), before any
row is read.

Routes live under the dedicated ``/v1/crypto`` prefix: no collision with the
existing /v1/macro and /v1/fx surfaces (crypto/48-NW-HOME decision).
"""

from __future__ import annotations

import csv
import io
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse

from yowayowa.api.deps import require_api_token
from yowayowa.config import get_settings
from yowayowa.crypto_acquisition import CryptoOhlcvStore, default_store
from yowayowa.crypto_models import normalize_crypto_symbol
from yowayowa.providers.base import enforce_provider_policy
from yowayowa.providers.coingecko import CoinGeckoOhlcProvider

router = APIRouter(prefix="/v1/crypto", dependencies=[Depends(require_api_token)])

_DEFAULT_LIMIT = 30
_MAX_LIMIT = 250


def _require_personal_mode() -> None:
    """Crypto sources are personal-only: fail closed outside personal mode.

    HTTP 404 (not 403) so public mode does not even confirm the existence of
    crypto data behind a token, matching the Yahoo crypto provider split.
    """

    settings = get_settings()
    try:
        enforce_provider_policy(
            CoinGeckoOhlcProvider.descriptor,
            mode=settings.mode,
            allow_personal_in_public=settings.allow_personal_provider_in_public,
        )
    except Exception as exc:  # ProviderPolicyError -> fail closed for the surface
        raise HTTPException(
            status_code=404,
            detail="Crypto OHLCV sources are personal-only and are not served "
            "outside personal mode",
        ) from exc


def _filtered_rows(symbol: str, provider: str | None, limit: int) -> list[dict[str, Any]]:
    normalized_provider = provider.strip().lower() if provider else None
    return default_store().read(symbol, provider=normalized_provider, limit=limit)


@router.get("/ohlcv/{symbol}")
def get_crypto_ohlcv(
    symbol: str,
    provider: Annotated[str | None, Query(description="Filter by provider, e.g. binance")] = None,
    format: Literal["json", "csv"] = Query(default="json"),
    limit: int = Query(default=_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
) -> object:
    """Persisted daily OHLCV rows for one symbol, newest first, per-source separated."""

    _require_personal_mode()
    try:
        normalized = normalize_crypto_symbol(symbol)
    except Exception as exc:  # InputValidationError -> client error
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    rows = _filtered_rows(normalized, provider, limit)
    if not rows:
        raise HTTPException(
            status_code=404, detail=f"No persisted crypto OHLCV rows for {normalized}"
        )
    if format == "csv":
        buffer = io.StringIO()
        fieldnames = list(rows[0].keys())
        writer = csv.DictWriter(buffer, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
        return PlainTextResponse(
            content=buffer.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{normalized}-ohlcv.csv"'},
        )
    return rows


@router.get("/ohlcv")
def list_crypto_ohlcv(
    provider: Annotated[str | None, Query(description="Filter by provider")] = None,
    limit: int = Query(default=_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
) -> list[dict[str, Any]]:
    """Persisted rows across every stored symbol, newest first (JSONL read)."""

    _require_personal_mode()
    store: CryptoOhlcvStore = default_store()
    combined: list[dict[str, Any]] = []
    from yowayowa.fx_models import SUPPORTED_CRYPTO_ASSETS

    for symbol in sorted(SUPPORTED_CRYPTO_ASSETS):
        combined.extend(
            store.read(symbol, provider=provider.strip().lower() if provider else None, limit=limit)
        )
    if not combined:
        raise HTTPException(status_code=404, detail="No persisted crypto OHLCV rows")
    combined.sort(key=lambda row: str(row.get("as_of")), reverse=True)
    return combined[:limit]
