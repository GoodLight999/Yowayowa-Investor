"""API for free JPX daily margin publications and derived supply/demand signals."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query

from yowayowa.api.deps import require_api_token
from yowayowa.config import get_settings
from yowayowa.db import get_session
from yowayowa.jpx_models import normalize_jpx_code
from yowayowa.jpx_public_models import (
    JpxMarginDailyDetail,
    JpxMarginFlow,
    JpxMarginSignal,
    JpxMarginSignalKind,
    JpxMarginWatch,
    JpxPremiumCharge,
    JpxPublicIngestResult,
)
from yowayowa.providers.base import ProviderPolicyError
from yowayowa.providers.jpx_public_margin import enforce_jpx_public_margin_policy
from yowayowa.services.jpx_margin_signals import scan_jpx_margin_signals
from yowayowa.services.jpx_public_margin import (
    JpxPublicKind,
    read_jpx_margin_details,
    read_jpx_margin_flow,
    read_jpx_margin_watch,
    read_jpx_premium,
    sync_jpx_public_margin,
)

router = APIRouter(
    prefix="/v1/jpx/public",
    dependencies=[Depends(require_api_token)],
)

_ALLOWED_KINDS = {"balance", "watch", "premium", "flow"}


def _require_public_margin_source() -> None:
    try:
        enforce_jpx_public_margin_policy(mode=get_settings().mode)
    except ProviderPolicyError as exc:
        raise HTTPException(
            status_code=403,
            detail=(
                "Free JPX margin publications are not enabled for public "
                "redistribution; use personal mode"
            ),
        ) from exc


def _normalize_kinds(raw: str) -> list[JpxPublicKind]:
    result: list[JpxPublicKind] = []
    for item in raw.split(","):
        value = item.strip().lower()
        if not value:
            continue
        if value not in _ALLOWED_KINDS:
            raise HTTPException(
                status_code=422,
                detail=f"unknown JPX public kind: {value}",
            )
        if value not in result:
            result.append(value)  # type: ignore[arg-type]
    if not result:
        raise HTTPException(status_code=422, detail="kinds must not be empty")
    return result


@router.post("/sync", response_model=list[JpxPublicIngestResult])
def sync_public_margin(
    kinds: str = Query(default="balance,watch,premium,flow"),
) -> list[JpxPublicIngestResult]:
    """Fetch and atomically ingest the latest official JPX publications."""

    _require_public_margin_source()
    settings = get_settings()
    session = get_session()
    try:
        return sync_jpx_public_margin(
            session,
            kinds=_normalize_kinds(kinds),
            cache_dir=Path(settings.jpx_public_raw_cache_dir),
            timeout_seconds=settings.request_timeout_seconds,
        )
    finally:
        session.close()


@router.get("/details/{code}", response_model=list[JpxMarginDailyDetail])
def margin_details(
    code: str,
    limit: int = Query(default=30, ge=1, le=250),
) -> list[JpxMarginDailyDetail]:
    _require_public_margin_source()
    try:
        normalized = normalize_jpx_code(code)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session = get_session()
    try:
        return read_jpx_margin_details(session, normalized, limit=limit)
    finally:
        session.close()


@router.get("/watch/{code}", response_model=list[JpxMarginWatch])
def margin_watch(
    code: str,
    limit: int = Query(default=30, ge=1, le=250),
) -> list[JpxMarginWatch]:
    _require_public_margin_source()
    try:
        normalized = normalize_jpx_code(code)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session = get_session()
    try:
        return read_jpx_margin_watch(session, normalized, limit=limit)
    finally:
        session.close()


@router.get("/premium/{source_code}", response_model=list[JpxPremiumCharge])
def premium_history(
    source_code: str,
    limit: int = Query(default=30, ge=1, le=250),
) -> list[JpxPremiumCharge]:
    _require_public_margin_source()
    normalized = source_code.strip().upper()
    if not re.fullmatch(r"[0-9]{3}[0-9A-Z]", normalized):
        raise HTTPException(status_code=422, detail="source_code must be a 4-character JPX code")
    session = get_session()
    try:
        return read_jpx_premium(session, normalized, limit=limit)
    finally:
        session.close()


@router.get("/flow/{code}", response_model=list[JpxMarginFlow])
def margin_flow(
    code: str,
    limit: int = Query(default=30, ge=1, le=250),
) -> list[JpxMarginFlow]:
    _require_public_margin_source()
    try:
        normalized = normalize_jpx_code(code)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session = get_session()
    try:
        return read_jpx_margin_flow(session, normalized, limit=limit)
    finally:
        session.close()


@router.get("/signals/{signal}", response_model=list[JpxMarginSignal])
def margin_signals(
    signal: JpxMarginSignalKind,
    limit: int = Query(default=50, ge=1, le=250),
    long_ratio_threshold: float = Query(default=10.0, ge=0),
    short_ratio_threshold: float = Query(default=2.0, ge=0),
    unwind_threshold: float = Query(default=0.10, ge=0, le=1),
    flow_buy_threshold: float = Query(default=40.0, ge=0, le=100),
    flow_sell_threshold: float = Query(default=20.0, ge=0, le=100),
) -> list[JpxMarginSignal]:
    _require_public_margin_source()
    session = get_session()
    try:
        return scan_jpx_margin_signals(
            session,
            signal,
            limit=limit,
            long_ratio_threshold=long_ratio_threshold,
            short_ratio_threshold=short_ratio_threshold,
            unwind_threshold=unwind_threshold,
            flow_buy_threshold=flow_buy_threshold,
            flow_sell_threshold=flow_sell_threshold,
        )
    finally:
        session.close()
