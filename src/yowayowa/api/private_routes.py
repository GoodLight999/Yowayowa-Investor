from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from yowayowa.acquisition.models import (
    AcquisitionOutcome,
    SnapshotDiff,
    SnapshotRecord,
)
from yowayowa.acquisition.registry import ConnectorDefinition, ConnectorRuntime
from yowayowa.acquisition.service import PrivateAcquisitionService
from yowayowa.api.deps import (
    get_private_acquisition_service,
    require_api_token,
    require_private_connectors,
)

router = APIRouter(
    prefix="/v1/private",
    dependencies=[Depends(require_api_token), Depends(require_private_connectors)],
)


class ConnectorListResponse(BaseModel):
    connectors: list[ConnectorRuntime]


class FetchRequest(BaseModel):
    resource: str = Field(min_length=1, max_length=512)
    params: dict[str, str] | None = None
    force_refresh: bool = False


class AuthCheckRequest(BaseModel):
    resource: str | None = Field(default=None, max_length=512)


class SnapshotListResponse(BaseModel):
    snapshots: list[SnapshotRecord]


class DiffResponse(BaseModel):
    diff: SnapshotDiff | None


def _service(connector_id: str, svc: PrivateAcquisitionService) -> None:
    if svc.get_connector(connector_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown connector: {connector_id}",
        )


@router.get(
    "/connectors",
    response_model=ConnectorListResponse,
    operation_id="private_list_connectors",
)
def list_connectors(
    service: PrivateAcquisitionService = Depends(get_private_acquisition_service),
) -> ConnectorListResponse:
    return ConnectorListResponse(connectors=service.list_connectors())


@router.post(
    "/connectors",
    response_model=ConnectorRuntime,
    status_code=status.HTTP_201_CREATED,
    operation_id="private_register_connector",
)
def register_connector(
    definition: ConnectorDefinition,
    service: PrivateAcquisitionService = Depends(get_private_acquisition_service),
) -> ConnectorRuntime:
    return service.register_connector(definition)


@router.get(
    "/connectors/{connector_id}",
    response_model=ConnectorRuntime,
    operation_id="private_get_connector",
)
def get_connector(
    connector_id: str,
    service: PrivateAcquisitionService = Depends(get_private_acquisition_service),
) -> ConnectorRuntime:
    runtime = service.get_connector(connector_id)
    if runtime is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown connector: {connector_id}",
        )
    return runtime


@router.post(
    "/connectors/{connector_id}/fetch",
    response_model=AcquisitionOutcome,
    operation_id="private_fetch_connector",
)
def fetch_connector(
    connector_id: str,
    body: FetchRequest,
    service: PrivateAcquisitionService = Depends(get_private_acquisition_service),
) -> AcquisitionOutcome:
    _service(connector_id, service)
    return service.fetch(
        connector_id,
        body.resource,
        params=body.params,
        force_refresh=body.force_refresh,
    )


@router.post(
    "/connectors/{connector_id}/auth-check",
    response_model=AcquisitionOutcome,
    operation_id="private_auth_check_connector",
)
def auth_check_connector(
    connector_id: str,
    body: AuthCheckRequest,
    service: PrivateAcquisitionService = Depends(get_private_acquisition_service),
) -> AcquisitionOutcome:
    _service(connector_id, service)
    return service.auth_check(connector_id, body.resource)


@router.get(
    "/connectors/{connector_id}/snapshots",
    response_model=SnapshotListResponse,
    operation_id="private_list_snapshots",
)
def list_snapshots(
    connector_id: str,
    resource: str = Query(min_length=1, max_length=512),
    limit: int = Query(default=20, ge=1, le=1000),
    service: PrivateAcquisitionService = Depends(get_private_acquisition_service),
) -> SnapshotListResponse:
    _service(connector_id, service)
    return SnapshotListResponse(snapshots=service.snapshots(connector_id, resource, limit))


@router.get(
    "/connectors/{connector_id}/diff",
    response_model=DiffResponse,
    operation_id="private_get_diff",
)
def get_diff(
    connector_id: str,
    resource: str = Query(min_length=1, max_length=512),
    service: PrivateAcquisitionService = Depends(get_private_acquisition_service),
) -> DiffResponse:
    _service(connector_id, service)
    return DiffResponse(diff=service.diff(connector_id, resource))
