from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class AcquisitionFetchState(StrEnum):
    OK = "ok"
    STALE = "stale"
    AUTH_EXPIRED = "auth_expired"
    FAILED = "failed"


class AuthState(StrEnum):
    AUTHENTICATED = "authenticated"
    UNAUTHENTICED = "unauthenticated"
    UNKNOWN = "unknown"


class NetworkExchange(BaseModel):
    """Provenance-safe record of one HTTP exchange. Never carries headers."""

    method: str
    url: str  # provenance form: no query, no fragment
    status_code: int | None
    content_type: str | None
    size_bytes: int | None
    duration_ms: float | None
    occurred_at: datetime


class DownloadCapture(BaseModel):
    filename: str | None
    content_type: str | None
    sha256: str
    size_bytes: int
    format: Literal["csv", "json", "xlsx", "other"]
    parsed: bool
    parse_note: str | None = None
    rows: list[dict[str, str]] | None = None  # csv parsed rows (cap 10000 rows)
    documents: list[dict[str, Any]] | None = None  # json parsed top-level items if list


class SnapshotRecord(BaseModel):
    snapshot_id: str  # sha256 of canonical payload json
    connector_id: str
    resource: str
    captured_at: datetime
    parser_version: str
    schema_version: str
    fetch_state: AcquisitionFetchState
    payload_sha256: str
    as_of: datetime | None = None


class FieldChange(BaseModel):
    """Values rendered as compact json string; None means absent/null."""

    path: str
    previous: str | None
    current: str | None


class SnapshotDiff(BaseModel):
    connector_id: str
    resource: str
    previous_snapshot_id: str | None
    current_snapshot_id: str
    previous_captured_at: datetime | None
    current_captured_at: datetime
    changed: bool
    changes: list[FieldChange]  # capped at 200 entries


class FreshnessPolicy(BaseModel):
    ttl_seconds: int = Field(default=900, ge=0)
    max_stale_seconds: int = Field(default=86400, ge=0)


class CacheStatus(BaseModel):
    state: AcquisitionFetchState
    fetched_at: datetime | None
    expires_at: datetime | None
    payload_sha256: str | None = None
    reason: str | None = None


class AcquisitionOutcome(BaseModel):
    """Full debug response for the API/CLI: state, provenance, network, diff."""

    connector_id: str
    resource: str
    fetch_state: AcquisitionFetchState
    auth_state: AuthState
    payload: dict[str, Any] | None  # None unless OK/STALE with data
    source_url: str | None = None
    retrieved_at: datetime | None = None
    as_of: datetime | None = None
    parser_version: str | None = None
    schema_version: str | None = None
    network: list[NetworkExchange] = Field(default_factory=list)
    downloads: list[DownloadCapture] = Field(default_factory=list)
    snapshot: SnapshotRecord | None = None
    diff: SnapshotDiff | None = None
    cache: CacheStatus | None = None
    notes: list[str] = Field(default_factory=list)
