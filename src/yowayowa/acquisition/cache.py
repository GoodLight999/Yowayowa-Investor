from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from yowayowa.acquisition.models import (
    AcquisitionFetchState,
    CacheStatus,
    FreshnessPolicy,
)
from yowayowa.acquisition.snapshots import payload_sha256


@dataclass
class CacheEntry:
    fetched_at: datetime
    expires_at: datetime
    payload_sha256: str
    payload: dict[str, object]


def evaluate_freshness(entry: CacheEntry, policy: FreshnessPolicy, now: datetime) -> CacheStatus:
    """Pure freshness decision: OK inside TTL, STALE within max_stale, else gone."""
    if now < entry.expires_at:
        return CacheStatus(
            state=AcquisitionFetchState.OK,
            fetched_at=entry.fetched_at,
            expires_at=entry.expires_at,
            payload_sha256=entry.payload_sha256,
        )
    if now <= entry.fetched_at + timedelta(seconds=policy.max_stale_seconds):
        return CacheStatus(
            state=AcquisitionFetchState.STALE,
            fetched_at=entry.fetched_at,
            expires_at=entry.expires_at,
            payload_sha256=entry.payload_sha256,
            reason="past ttl; within max-stale window",
        )
    return CacheStatus(
        state=AcquisitionFetchState.FAILED,
        fetched_at=entry.fetched_at,
        expires_at=entry.expires_at,
        payload_sha256=entry.payload_sha256,
        reason="exceeded max-stale window; entry dropped",
    )


class AcquisitionCache:
    """Thread-safe in-memory TTL cache keyed by (connector_id, resource)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[tuple[str, str], CacheEntry] = {}

    def get(
        self,
        connector_id: str,
        resource: str,
        policy: FreshnessPolicy,
        *,
        now: datetime | None = None,
    ) -> tuple[CacheStatus, dict[str, object] | None]:
        moment = now if now is not None else datetime.now(UTC)
        with self._lock:
            entry = self._entries.get((connector_id, resource))
        if entry is None:
            return (
                CacheStatus(
                    state=AcquisitionFetchState.FAILED,
                    fetched_at=None,
                    expires_at=None,
                    reason="cache-miss",
                ),
                None,
            )
        status = evaluate_freshness(entry, policy, moment)
        if status.state == AcquisitionFetchState.FAILED:
            self.invalidate(connector_id, resource)
            return (status, None)
        return (status, entry.payload)

    def put(
        self,
        connector_id: str,
        resource: str,
        payload: dict[str, object],
        policy: FreshnessPolicy,
        *,
        now: datetime | None = None,
    ) -> CacheEntry:
        moment = now if now is not None else datetime.now(UTC)
        entry = CacheEntry(
            fetched_at=moment,
            expires_at=moment + timedelta(seconds=policy.ttl_seconds),
            payload_sha256=payload_sha256(payload),
            payload=payload,
        )
        with self._lock:
            self._entries[(connector_id, resource)] = entry
        return entry

    def invalidate(self, connector_id: str, resource: str) -> None:
        with self._lock:
            self._entries.pop((connector_id, resource), None)
