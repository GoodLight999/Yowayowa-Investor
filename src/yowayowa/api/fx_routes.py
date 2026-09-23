from __future__ import annotations

from typing import Annotated, Protocol

from fastapi import APIRouter, Depends, HTTPException, Query

from yowayowa.api.deps import require_api_token
from yowayowa.fx_models import (
    SUPPORTED_CRYPTO_ASSETS,
    FxDirection,
    FxHistory,
    FxProposalSpec,
    FxRateSnapshot,
    build_fx_proposal,
    normalize_fx_pair,
)
from yowayowa.providers.frankfurter import ECB_REFERENCE_CURRENCIES
from yowayowa.providers.registry import frankfurter_fx_provider, yahoo_market_provider

router = APIRouter(prefix="/v1", dependencies=[Depends(require_api_token)])


class _FxAnalysisProvider(Protocol):
    """The slice of the provider surface the /v1/fx routes depend on."""

    def fx_quote(self, pair: str) -> FxRateSnapshot: ...

    def fx_history(self, pair: str, *, interval: str, period: str) -> FxHistory: ...


def _fx_provider(pair: str) -> _FxAnalysisProvider:
    """Resolve the FX provider for one normalized pair — the single routing point.

    Providers come from the registry's lru_cache, so the process holds one
    Frankfurter/Yahoo instance: the provider-internal TTL caches actually
    absorb repeated pairs and the httpx.Client is reused instead of leaked
    per request. ECB-covered fiat pairs always use Frankfurter
    (OFFICIAL_PUBLIC, public-mode safe). Crypto pairs use the personal-only
    Yahoo provider, which public mode refuses at provider construction; they
    404 there instead of silently mixing sources. No other fallback exists.
    """

    if pair[:3] in SUPPORTED_CRYPTO_ASSETS or pair[3:] in SUPPORTED_CRYPTO_ASSETS:
        return yahoo_market_provider()
    if pair[:3] in ECB_REFERENCE_CURRENCIES and pair[3:] in ECB_REFERENCE_CURRENCIES:
        return frankfurter_fx_provider()
    raise LookupError(f"Pair {pair!r} is not available from a licensed provider on this surface")


def _fx_quote(pair: str) -> FxRateSnapshot:
    provider = _fx_provider(pair)
    return provider.fx_quote(pair)


def _fx_history(pair: str, interval: str, period: str) -> FxHistory:
    """FX history through the resolved provider, with per-source interval rules.

    ECB reference rates are a once-daily fix: non-daily intervals are a client
    error (HTTP 422), not silently resampled data. Crypto pairs keep the
    personal Yahoo surface, which serves intraday bars.
    """

    base, quote = pair[:3], pair[3:]
    crypto = base in SUPPORTED_CRYPTO_ASSETS or quote in SUPPORTED_CRYPTO_ASSETS
    if not crypto and interval != "1d":
        raise ValueError(
            f"Unsupported FX history interval {interval!r}: ECB reference rates are "
            "daily only; use interval=1d"
        )
    provider = _fx_provider(pair)
    return provider.fx_history(pair, interval=interval, period=period)


@router.get("/fx/rate", response_model=FxRateSnapshot)
def fx_rate(
    pair: Annotated[str, Query(min_length=6, max_length=6, description="FX pair, e.g. USDJPY")],
) -> FxRateSnapshot:
    normalized = normalize_fx_pair(pair)
    try:
        return _fx_quote(normalized)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/fx/history", response_model=FxHistory)
def fx_history(
    pair: Annotated[str, Query(min_length=6, max_length=6, description="FX pair, e.g. USDJPY")],
    interval: Annotated[str, Query(pattern=r"^(?:1m|5m|15m|30m|1h|1d|1wk|1mo)$")] = "1d",
    period: Annotated[str, Query(pattern=r"^(?:1d|5d|1mo|3mo|6mo|1y|2y|5y|max)$")] = "1mo",
) -> FxHistory:
    normalized = normalize_fx_pair(pair)
    try:
        return _fx_history(normalized, interval, period)
    except ValueError as exc:
        # Daily-data-only / period constraints are client errors, not 5xx.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
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
        quote = _fx_quote(normalized)
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
