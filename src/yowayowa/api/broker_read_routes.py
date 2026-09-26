from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from yowayowa.acquisition.models import AcquisitionOutcome, SnapshotDiff, SnapshotRecord
from yowayowa.acquisition.registry import ConnectorRuntime
from yowayowa.api.deps import (
    get_broker_read_service,
    require_api_token,
    require_private_connectors,
)
from yowayowa.services.broker_read_service import BrokerReadOutcome, BrokerReadService

router = APIRouter(
    prefix="/v1/broker-read",
    dependencies=[Depends(require_api_token), Depends(require_private_connectors)],
)


class ConnectorListResponse(BaseModel):
    connectors: list[ConnectorRuntime]


class FetchRequest(BaseModel):
    resource: str = Field(min_length=1, max_length=64)
    market: str = Field(pattern="^(jp|us)$")
    force_refresh: bool = False


class SnapshotListResponse(BaseModel):
    snapshots: list[SnapshotRecord]


class DiffResponse(BaseModel):
    diff: SnapshotDiff | None


def _service(connector_id: str, svc: BrokerReadService) -> None:
    if svc.get_connector(connector_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown connector: {connector_id}",
        )


@router.get(
    "/connectors",
    response_model=ConnectorListResponse,
    operation_id="broker_read_list_connectors",
)
def list_connectors(
    service: BrokerReadService = Depends(get_broker_read_service),
) -> ConnectorListResponse:
    return ConnectorListResponse(connectors=service.list_connectors())


@router.get(
    "/connectors/{connector_id}",
    response_model=ConnectorRuntime,
    operation_id="broker_read_get_connector",
)
def get_connector(
    connector_id: str,
    service: BrokerReadService = Depends(get_broker_read_service),
) -> ConnectorRuntime:
    runtime = service.get_connector(connector_id)
    if runtime is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown connector: {connector_id}",
        )
    return runtime


@router.post(
    "/connectors/{connector_id}/auth-check",
    response_model=AcquisitionOutcome,
    operation_id="broker_read_auth_check",
)
def auth_check(
    connector_id: str,
    service: BrokerReadService = Depends(get_broker_read_service),
) -> AcquisitionOutcome:
    _service(connector_id, service)
    return service.auth_check(connector_id)


@router.post(
    "/connectors/{connector_id}/fetch",
    response_model=BrokerReadOutcome,
    operation_id="broker_read_fetch_resource",
)
def fetch_resource(
    connector_id: str,
    body: FetchRequest,
    service: BrokerReadService = Depends(get_broker_read_service),
) -> BrokerReadOutcome:
    _service(connector_id, service)
    return service.fetch(body.resource, body.market, force_refresh=body.force_refresh)


@router.get(
    "/connectors/{connector_id}/snapshots",
    response_model=SnapshotListResponse,
    operation_id="broker_read_list_snapshots",
)
def list_snapshots(
    connector_id: str,
    resource: str = Query(min_length=1, max_length=64),
    market: str = Query(pattern="^(jp|us)$"),
    limit: int = Query(default=20, ge=1, le=1000),
    service: BrokerReadService = Depends(get_broker_read_service),
) -> SnapshotListResponse:
    _service(connector_id, service)
    return SnapshotListResponse(
        snapshots=service.snapshots(connector_id, resource, market, limit=limit)
    )


@router.get(
    "/connectors/{connector_id}/diff",
    response_model=DiffResponse,
    operation_id="broker_read_get_diff",
)
def get_diff(
    connector_id: str,
    resource: str = Query(min_length=1, max_length=64),
    market: str = Query(pattern="^(jp|us)$"),
    service: BrokerReadService = Depends(get_broker_read_service),
) -> DiffResponse:
    _service(connector_id, service)
    return DiffResponse(diff=service.diff(connector_id, resource, market))
