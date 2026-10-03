from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from yowayowa.acquisition.models import (
    AcquisitionFetchState,
    AuthState,
    FreshnessPolicy,
)
from yowayowa.private_connectors import PrivateAcquisitionMethod

_ALLOWED_METHODS = frozenset(
    {
        PrivateAcquisitionMethod.PRIVATE_HTTP,
        PrivateAcquisitionMethod.STRUCTURED_SCRAPE,
        PrivateAcquisitionMethod.HTML_SCRAPE,
        PrivateAcquisitionMethod.AUTHENTICATED_WEB_SESSION,
    }
)


class ConnectorDefinition(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,63}$")
    provider: str = Field(min_length=1, max_length=100)
    base_url: str
    method: PrivateAcquisitionMethod
    parser: Literal["tables", "text", "json", "download"] = "json"
    freshness: FreshnessPolicy = Field(default_factory=FreshnessPolicy)
    auth_recheck_resource: str | None = None
    notes: list[str] = Field(default_factory=list)

    @field_validator("base_url")
    @classmethod
    def _absolute_http(cls, value: str) -> str:
        from urllib.parse import urlsplit

        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise ValueError("base_url must be an absolute http(s) URL")
        return value

    @field_validator("method")
    @classmethod
    def _supported_method(cls, value: PrivateAcquisitionMethod) -> PrivateAcquisitionMethod:
        if value not in _ALLOWED_METHODS:
            raise ValueError("unsupported acquisition method")
        return value


class ConnectorRuntime(BaseModel):
    definition: ConnectorDefinition
    last_success_at: datetime | None = None
    last_fetch_state: AcquisitionFetchState | None = None
    last_auth_state: AuthState | None = None


class ConnectorRegistry:
    """In-memory registry of connector definitions plus runtime state."""

    def __init__(self) -> None:
        self._definitions: dict[str, ConnectorDefinition] = {}
        self._runtimes: dict[str, ConnectorRuntime] = {}

    def register(self, definition: ConnectorDefinition) -> ConnectorRuntime:
        if definition.id in self._definitions:
            previous = self._runtimes.get(definition.id)
            runtime = ConnectorRuntime(
                definition=definition,
                last_success_at=previous.last_success_at if previous else None,
                last_fetch_state=previous.last_fetch_state if previous else None,
                last_auth_state=previous.last_auth_state if previous else None,
            )
        else:
            runtime = ConnectorRuntime(definition=definition)
        self._definitions[definition.id] = definition
        self._runtimes[definition.id] = runtime
        return runtime

    def get(self, connector_id: str) -> ConnectorDefinition | None:
        return self._definitions.get(connector_id)

    def list_runtimes(self) -> list[ConnectorRuntime]:
        return [self._runtimes[key] for key in sorted(self._definitions)]

    def update_runtime(
        self,
        connector_id: str,
        *,
        last_success_at: datetime | None = None,
        last_fetch_state: AcquisitionFetchState | None = None,
        last_auth_state: AuthState | None = None,
        success: bool = False,
    ) -> ConnectorRuntime | None:
        definition = self._definitions.get(connector_id)
        if definition is None:
            return None
        runtime = self._runtimes.get(connector_id, ConnectorRuntime(definition=definition))
        updated = runtime.model_copy(
            update={
                "last_fetch_state": last_fetch_state,
                "last_auth_state": last_auth_state,
                "last_success_at": (
                    runtime.last_success_at
                    if not success and last_success_at is None
                    else last_success_at
                ),
            }
        )
        self._runtimes[connector_id] = updated
        return updated
