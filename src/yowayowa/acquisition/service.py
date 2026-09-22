from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from yowayowa.acquisition.auth import AuthSignal, HeuristicAuthDetector
from yowayowa.acquisition.cache import AcquisitionCache
from yowayowa.acquisition.downloads import capture_download
from yowayowa.acquisition.models import (
    AcquisitionFetchState,
    AcquisitionOutcome,
    AuthState,
    DownloadCapture,
    NetworkExchange,
    SnapshotDiff,
    SnapshotRecord,
)
from yowayowa.acquisition.parsers import lookup_parser
from yowayowa.acquisition.registry import (
    ConnectorDefinition,
    ConnectorRegistry,
    ConnectorRuntime,
)
from yowayowa.acquisition.snapshots import (
    SnapshotStore,
    diff_payloads,
    payload_sha256,
)
from yowayowa.acquisition.transport import (
    HTTP_METHODS,
    HttpxSessionTransport,
    PrivateAcquisitionError,
    SessionTransport,
    TransportResponse,
    record_exchange,
)
from yowayowa.private_connectors import PrivateConnectorDescriptor

TransportFactory = Callable[[ConnectorDefinition], SessionTransport]

_AS_OF_KEYS = ("as_of", "asOf", "as-of")
_BODY_SCAN_LIMIT = 200_000


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _default_http_transport(definition: ConnectorDefinition) -> SessionTransport:
    import httpx

    client = httpx.Client(base_url=definition.base_url, follow_redirects=False)
    descriptor = PrivateConnectorDescriptor(
        id=definition.id,
        provider=definition.provider,
        method=definition.method,
        parser_version="1",
    )
    return HttpxSessionTransport(
        descriptor=descriptor,
        base_url=definition.base_url,
        client=client,
    )


class PrivateAcquisitionService:
    """Service-layer orchestrator for private/family data acquisition.

    Fail-closed: unknown connectors, transport errors, auth expiry, and parse
    failures all return explicit outcomes; nothing raises to the caller and
    missing data is never silently coerced.
    """

    def __init__(
        self,
        *,
        registry: ConnectorRegistry,
        cache: AcquisitionCache,
        snapshot_store: SnapshotStore,
        data_dir: Path,
        now: Callable[[], datetime] = _utcnow,
        transport_factory: TransportFactory | None = None,
        detector: HeuristicAuthDetector | None = None,
    ) -> None:
        self.registry = registry
        self.cache = cache
        self.snapshot_store = snapshot_store
        self.data_dir = data_dir
        self._now = now
        self._transport_factory = transport_factory
        self._detector = detector or HeuristicAuthDetector()

    @classmethod
    def build_default(cls, data_dir: Path) -> PrivateAcquisitionService:
        return cls(
            registry=ConnectorRegistry(),
            cache=AcquisitionCache(),
            snapshot_store=SnapshotStore(data_dir),
            data_dir=data_dir,
        )

    # -------------------------------------------------------------- registry

    def register_connector(self, definition: ConnectorDefinition) -> ConnectorRuntime:
        return self.registry.register(definition)

    def list_connectors(self) -> list[ConnectorRuntime]:
        return self.registry.list_runtimes()

    def get_connector(self, connector_id: str) -> ConnectorRuntime | None:
        for runtime in self.registry.list_runtimes():
            if runtime.definition.id == connector_id:
                return runtime
        return None

    # ----------------------------------------------------------------- fetch

    def fetch(
        self,
        connector_id: str,
        resource: str,
        *,
        params: dict[str, str] | None = None,
        force_refresh: bool = False,
    ) -> AcquisitionOutcome:
        definition = self.registry.get(connector_id)
        if definition is None:
            return self._failure(connector_id, resource, f"unknown connector: {connector_id}")

        if not force_refresh:
            status, cached_payload = self.cache.get(
                connector_id, resource, definition.freshness, now=self._now()
            )
            if status.state in (AcquisitionFetchState.OK, AcquisitionFetchState.STALE):
                snapshot, diff = self._snapshot_context(connector_id, resource)
                note = (
                    "cache-hit"
                    if status.state == AcquisitionFetchState.OK
                    else (status.reason or "stale-cache")
                )
                return AcquisitionOutcome(
                    connector_id=connector_id,
                    resource=resource,
                    fetch_state=status.state,
                    auth_state=AuthState.UNKNOWN,
                    payload=cached_payload,
                    snapshot=snapshot,
                    diff=diff,
                    cache=status,
                    notes=[note],
                )

        transport = self._transport_for(definition)
        network: list[NetworkExchange] = []
        now = self._now()

        try:
            response = transport.fetch("GET", resource, params=params)
        except PrivateAcquisitionError as exc:
            return self._failure(connector_id, resource, exc.reason, network)

        network.append(
            record_exchange(
                "GET",
                response.url,
                response.status_code,
                content_type=response.content_type,
                size_bytes=len(response.content),
                duration_ms=response.elapsed_ms,
                occurred_at=now,
            )
        )

        auth_state = self._detect_auth(response)
        if auth_state == AuthState.UNAUTHENTICED:
            self.cache.invalidate(connector_id, resource)
            self.registry.update_runtime(
                connector_id,
                last_fetch_state=AcquisitionFetchState.AUTH_EXPIRED,
                last_auth_state=auth_state,
            )
            return AcquisitionOutcome(
                connector_id=connector_id,
                resource=resource,
                fetch_state=AcquisitionFetchState.AUTH_EXPIRED,
                auth_state=auth_state,
                payload=None,
                network=network,
                cache=None,
                notes=["operator reauthentication required"],
            )

        notes: list[str] = []
        try:
            payload, parser_version, schema_version, capture = self._parse(
                definition, response, notes
            )
        except PrivateAcquisitionError as exc:
            return self._failure(connector_id, resource, exc.reason, network)
        downloads = [capture] if capture is not None else []

        as_of = _extract_as_of(payload)
        snapshot, diff = self._append_snapshot(
            connector_id, resource, payload, parser_version, schema_version, as_of, now
        )
        self.cache.put(connector_id, resource, dict(payload), definition.freshness, now=now)
        self.registry.update_runtime(
            connector_id,
            last_success_at=now,
            last_fetch_state=AcquisitionFetchState.OK,
            last_auth_state=auth_state,
        )
        return AcquisitionOutcome(
            connector_id=connector_id,
            resource=resource,
            fetch_state=AcquisitionFetchState.OK,
            auth_state=auth_state,
            payload=payload,
            source_url=response.url,
            retrieved_at=now,
            as_of=as_of,
            parser_version=parser_version,
            schema_version=schema_version,
            network=network,
            downloads=downloads,
            snapshot=snapshot,
            diff=diff,
            cache=None,
            notes=notes,
        )

    def auth_check(self, connector_id: str, resource: str | None = None) -> AcquisitionOutcome:
        definition = self.registry.get(connector_id)
        if definition is None:
            return self._failure(connector_id, resource or "", f"unknown connector: {connector_id}")
        target = resource or definition.auth_recheck_resource or ""
        transport = self._transport_for(definition)
        now = self._now()
        try:
            response = transport.fetch("GET", target or "/")
        except PrivateAcquisitionError as exc:
            return self._failure(connector_id, target, exc.reason)
        exchange = record_exchange(
            "GET",
            response.url,
            response.status_code,
            content_type=response.content_type,
            size_bytes=len(response.content),
            duration_ms=response.elapsed_ms,
            occurred_at=now,
        )
        auth_state = self._detect_auth(response)
        if auth_state == AuthState.AUTHENTICATED:
            fetch_state = AcquisitionFetchState.OK
            reason = "authenticated"
        elif auth_state == AuthState.UNAUTHENTICED:
            fetch_state = AcquisitionFetchState.AUTH_EXPIRED
            reason = "operator reauthentication required"
        else:
            fetch_state = AcquisitionFetchState.FAILED
            reason = "auth state unknown"
        self.registry.update_runtime(
            connector_id,
            last_fetch_state=fetch_state,
            last_auth_state=auth_state,
        )
        return AcquisitionOutcome(
            connector_id=connector_id,
            resource=target,
            fetch_state=fetch_state,
            auth_state=auth_state,
            payload=None,
            source_url=response.url,
            retrieved_at=now,
            network=[exchange],
            notes=[reason],
        )

    def snapshots(self, connector_id: str, resource: str, limit: int = 20) -> list[SnapshotRecord]:
        return self.snapshot_store.history(connector_id, resource, limit)

    def diff(self, connector_id: str, resource: str) -> SnapshotDiff | None:
        return self._snapshot_context(connector_id, resource)[1]

    # ------------------------------------------------------------- internals

    def _transport_for(self, definition: ConnectorDefinition) -> SessionTransport:
        if self._transport_factory is not None:
            return self._transport_factory(definition)
        if definition.method in HTTP_METHODS:
            return _default_http_transport(definition)
        raise PrivateAcquisitionError(
            AcquisitionFetchState.FAILED,
            "no default transport for browser-session connectors; inject a transport factory",
        )

    def _detect_auth(self, response: TransportResponse) -> AuthState:
        return self._detector.detect(
            AuthSignal(
                url=response.url,
                status_code=response.status_code,
                title=None,
                body_text=response.text[:_BODY_SCAN_LIMIT],
            )
        )

    def _parse(
        self,
        definition: ConnectorDefinition,
        response: TransportResponse,
        notes: list[str],
    ) -> tuple[dict[str, Any], str, str, DownloadCapture | None]:
        if definition.parser == "json":
            try:
                parsed = json.loads(response.text)
            except json.JSONDecodeError as exc:
                raise PrivateAcquisitionError(
                    AcquisitionFetchState.FAILED, f"invalid json: {exc.msg}"
                ) from exc
            if not isinstance(parsed, dict):
                raise PrivateAcquisitionError(AcquisitionFetchState.FAILED, "expected object")
            return parsed, "json-v1", "json-v1", None
        if definition.parser in ("tables", "text"):
            adapter = lookup_parser(definition.parser)
            html_payload = adapter.parse(response.text)
            return (
                html_payload,
                adapter.parser_version,
                adapter.schema_version,
                None,
            )
        capture = capture_download(
            content=response.content,
            content_type=response.content_type,
            filename=_filename_from_url(response.url),
        )
        payload: dict[str, Any] = {
            "sha256": capture.sha256,
            "size_bytes": capture.size_bytes,
            "format": capture.format,
            "parsed": capture.parsed,
        }
        if capture.parse_note is not None:
            payload["parse_note"] = capture.parse_note
        return payload, "raw-v1", "raw-v1", capture

    def _append_snapshot(
        self,
        connector_id: str,
        resource: str,
        payload: dict[str, Any],
        parser_version: str,
        schema_version: str,
        as_of: datetime | None,
        now: datetime,
    ) -> tuple[SnapshotRecord, SnapshotDiff | None]:
        prior = self.snapshot_store.recent_entries(connector_id, resource, 1)
        digest = payload_sha256(payload)
        record = SnapshotRecord(
            snapshot_id=digest,
            connector_id=connector_id,
            resource=resource,
            captured_at=now,
            parser_version=parser_version,
            schema_version=schema_version,
            fetch_state=AcquisitionFetchState.OK,
            payload_sha256=digest,
            as_of=as_of,
        )
        self.snapshot_store.append(connector_id, resource, record, payload)
        diff: SnapshotDiff | None = None
        if prior:
            previous_record, previous_payload = prior[0]
            changed, changes = diff_payloads(previous_payload, payload)
            diff = SnapshotDiff(
                connector_id=connector_id,
                resource=resource,
                previous_snapshot_id=previous_record.snapshot_id,
                current_snapshot_id=digest,
                previous_captured_at=previous_record.captured_at,
                current_captured_at=now,
                changed=changed,
                changes=changes,
            )
        return record, diff

    def _snapshot_context(
        self, connector_id: str, resource: str
    ) -> tuple[SnapshotRecord | None, SnapshotDiff | None]:
        entries = self.snapshot_store.recent_entries(connector_id, resource, 2)
        if not entries:
            return None, None
        current_record, current_payload = entries[0]
        if len(entries) < 2:
            return current_record, None
        previous_record, previous_payload = entries[1]
        changed, changes = diff_payloads(previous_payload, current_payload)
        diff = SnapshotDiff(
            connector_id=connector_id,
            resource=resource,
            previous_snapshot_id=previous_record.snapshot_id,
            current_snapshot_id=current_record.snapshot_id,
            previous_captured_at=previous_record.captured_at,
            current_captured_at=current_record.captured_at,
            changed=changed,
            changes=changes,
        )
        return current_record, diff

    def _failure(
        self,
        connector_id: str,
        resource: str,
        reason: str,
        network: list[NetworkExchange] | None = None,
    ) -> AcquisitionOutcome:
        if self.registry.get(connector_id) is not None:
            self.registry.update_runtime(
                connector_id,
                last_fetch_state=AcquisitionFetchState.FAILED,
                last_auth_state=None,
            )
        return AcquisitionOutcome(
            connector_id=connector_id,
            resource=resource,
            fetch_state=AcquisitionFetchState.FAILED,
            auth_state=AuthState.UNKNOWN,
            payload=None,
            network=network or [],
            notes=[reason],
        )


def _filename_from_url(url: str) -> str | None:
    tail = urlsplit(url).path.rsplit("/", 1)[-1]
    return tail or None


def _extract_as_of(payload: dict[str, Any]) -> datetime | None:
    for key in _AS_OF_KEYS:
        value = payload.get(key)
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value)
            except ValueError:
                continue
    return None
