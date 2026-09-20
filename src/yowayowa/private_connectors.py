from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, Field


class PrivateAcquisitionMethod(StrEnum):
    PRIVATE_HTTP = "private_http"
    PRIVATE_WEBSOCKET = "private_websocket"
    STRUCTURED_SCRAPE = "structured_scrape"
    HTML_SCRAPE = "html_scrape"


class PrivateConnectorDescriptor(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    provider: str = Field(min_length=1, max_length=100)
    method: PrivateAcquisitionMethod
    authenticated: bool = True
    redistributable: bool = False
    parser_version: str = Field(min_length=1, max_length=100)


class PrivateConnectorResult(BaseModel):
    descriptor: PrivateConnectorDescriptor
    source_url: str | None = None
    retrieved_at: datetime
    as_of: datetime | None = None
    payload: dict[str, Any]
    notes: list[str] = Field(default_factory=list)


class PrivateConnector(Protocol):
    descriptor: PrivateConnectorDescriptor

    def fetch(self, resource: str, **parameters: object) -> PrivateConnectorResult: ...
