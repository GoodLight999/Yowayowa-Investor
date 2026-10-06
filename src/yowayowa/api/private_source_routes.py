"""Authorized private-source API surface (P1D).

Thin adapters over ``PrivateSourceService``: mailbox source registry, fetch
runs, and recorded earnings-calendar events.

Read-only by construction (mailbox access is read-only), gated by the same
private-connectors switch as the rest of the private acquisition surfaces, and
deliberately absent from the public read-only allowlist: in public mode every
endpoint here fails closed with 403.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from yowayowa.api.deps import (
    get_private_source_service,
    require_api_token,
    require_private_connectors,
)
from yowayowa.services.private_source_service import (
    PrivateMailboxSource,
    PrivateSourceFetchOutcome,
    PrivateSourceService,
)

router = APIRouter(
    prefix="/v1/private-sources",
    dependencies=[Depends(require_api_token), Depends(require_private_connectors)],
)


class PrivateSourceListResponse(BaseModel):
    sources: list[PrivateMailboxSource]


class PrivateSourceEventListResponse(BaseModel):
    events: list[dict[str, object]]
    count: int


class PrivateSourceFetchRequest(BaseModel):
    force_refresh: bool = False


def _source(source_id: str, service: PrivateSourceService) -> PrivateMailboxSource:
    found = service.get_source(source_id)
    if found is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown private source: {source_id}",
        )
    return found


@router.get(
    "/sources",
    response_model=PrivateSourceListResponse,
    operation_id="private_source_list_sources",
)
def list_sources(
    service: PrivateSourceService = Depends(get_private_source_service),
) -> PrivateSourceListResponse:
    return PrivateSourceListResponse(sources=service.list_sources())


@router.post(
    "/sources",
    response_model=PrivateMailboxSource,
    status_code=status.HTTP_201_CREATED,
    operation_id="private_source_register_source",
)
def register_source(
    source: PrivateMailboxSource,
    service: PrivateSourceService = Depends(get_private_source_service),
) -> PrivateMailboxSource:
    service.add_source(source)
    return source


@router.get(
    "/sources/{source_id}",
    response_model=PrivateMailboxSource,
    operation_id="private_source_get_source",
)
def get_source(
    source_id: str,
    service: PrivateSourceService = Depends(get_private_source_service),
) -> PrivateMailboxSource:
    return _source(source_id, service)


@router.post(
    "/sources/{source_id}/fetch",
    response_model=PrivateSourceFetchOutcome,
    operation_id="private_source_fetch",
)
def fetch_source(
    source_id: str,
    request: PrivateSourceFetchRequest | None = None,
    service: PrivateSourceService = Depends(get_private_source_service),
) -> PrivateSourceFetchOutcome:
    _source(source_id, service)
    force_refresh = request.force_refresh if request is not None else False
    return service.fetch(source_id, force_refresh=force_refresh)


@router.get(
    "/events",
    response_model=PrivateSourceEventListResponse,
    operation_id="private_source_events",
)
def list_events(
    source_id: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=200, ge=1, le=1000),
    service: PrivateSourceService = Depends(get_private_source_service),
) -> PrivateSourceEventListResponse:
    events = service.events(source_id, limit=limit)
    return PrivateSourceEventListResponse(events=events, count=len(events))
