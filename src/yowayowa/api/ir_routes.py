"""IR monitoring API surface (P1C).

Thin adapters over ``IrMonitorService``: source registry, monitoring runs,
instrument timeline, and per-document KPI history. Read-only by construction
(IR acquisition is read-only), gated by the same private-connectors switch as
the rest of the private acquisition surfaces.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from yowayowa.api.deps import get_ir_monitor_service, require_api_token, require_private_connectors
from yowayowa.services.ir_monitor_service import (
    IrMonitorOutcome,
    IrMonitorService,
    IrSourceDefinition,
)

router = APIRouter(
    prefix="/v1/ir",
    dependencies=[Depends(require_api_token), Depends(require_private_connectors)],
)


class SourceListResponse(BaseModel):
    sources: list[IrSourceDefinition]


class TimelineResponse(BaseModel):
    symbol: str
    entries: list[dict[str, object]]


class KpiHistoryResponse(BaseModel):
    url: str
    entries: list[dict[str, object]]


class MonitorRequest(BaseModel):
    source_id: str = Field(min_length=1, max_length=128)


def _service(source_id: str, svc: IrMonitorService) -> IrSourceDefinition:
    source = svc.get_source(source_id)
    if source is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown IR source: {source_id}",
        )
    return source


@router.get("/sources", response_model=SourceListResponse, operation_id="ir_list_sources")
def list_sources(
    service: IrMonitorService = Depends(get_ir_monitor_service),
) -> SourceListResponse:
    return SourceListResponse(sources=service.list_sources())


@router.post(
    "/sources",
    response_model=IrSourceDefinition,
    status_code=status.HTTP_201_CREATED,
    operation_id="ir_register_source",
)
def register_source(
    definition: IrSourceDefinition,
    service: IrMonitorService = Depends(get_ir_monitor_service),
) -> IrSourceDefinition:
    service.add_source(definition)
    return definition


@router.get(
    "/sources/{source_id}",
    response_model=IrSourceDefinition,
    operation_id="ir_get_source",
)
def get_source(
    source_id: str,
    service: IrMonitorService = Depends(get_ir_monitor_service),
) -> IrSourceDefinition:
    return _service(source_id, service)


@router.post(
    "/sources/{source_id}/monitor",
    response_model=IrMonitorOutcome,
    operation_id="ir_monitor_source",
)
def monitor_source(
    source_id: str,
    service: IrMonitorService = Depends(get_ir_monitor_service),
) -> IrMonitorOutcome:
    _service(source_id, service)
    return service.monitor(source_id)


@router.get(
    "/instruments/{symbol}/timeline",
    response_model=TimelineResponse,
    operation_id="ir_instrument_timeline",
)
def instrument_timeline(
    symbol: str,
    kind: str | None = Query(default=None, max_length=32),
    limit: int = Query(default=200, ge=1, le=1000),
    service: IrMonitorService = Depends(get_ir_monitor_service),
) -> TimelineResponse:
    return TimelineResponse(symbol=symbol, entries=service.timeline(symbol, kind=kind, limit=limit))


@router.get(
    "/documents/kpi-history",
    response_model=KpiHistoryResponse,
    operation_id="ir_document_kpi_history",
)
def document_kpi_history(
    url: str = Query(min_length=1, max_length=2048),
    kpi: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=10, ge=1, le=200),
    service: IrMonitorService = Depends(get_ir_monitor_service),
) -> KpiHistoryResponse:
    return KpiHistoryResponse(url=url, entries=service.document_kpi_history(url, kpi=kpi))
