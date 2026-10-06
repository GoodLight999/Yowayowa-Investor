"""Authorized private information sources: mailbox-driven alert ingestion (P1D).

The first P1D source kind is the operator's own authorized mailbox. Broker
notification mail carries a holdings/watch-list-scoped earnings-date stream
that no public source provides, so this service turns that mail into
provenance-complete instrument timeline events.

Design constraints (see the P1D spec):

- read-only by construction: the mailbox reader is only ever asked to
  ``search``;
- ``license_class`` is ``personal_only``; nothing here redistributes content;
- fail-closed: an expired mail session produces ``AUTH_EXPIRED`` with an empty
  event list and an explicit note — never an empty list that looks like a
  successful "no announcements" run;
- idempotent: a message body is fingerprinted (sha256) and an already-recorded
  ``(symbol, announcement_date, source_message_id)`` event is never appended
  twice, so repeated runs are safe;
- provenance: every event carries provider, mailbox source URL, retrieval
  time, license class, and the originating message id/subject.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

from pydantic import BaseModel, Field, ValidationError, field_validator

from yowayowa.acquisition.alerts import extract_earnings_calendar
from yowayowa.acquisition.ir import IrTimelineStore, timeline_entry
from yowayowa.acquisition.mailbox import MailboxAccessError, MailboxReader
from yowayowa.acquisition.models import (
    AcquisitionFetchState,
    AuthState,
    NetworkExchange,
)
from yowayowa.acquisition.snapshots import _safe_component
from yowayowa.domain import LicenseClass

# The event kind written into the instrument timeline.
EARNINGS_ANNOUNCEMENT_KIND = "earnings_announcement"

# Successful outcomes are cached in memory for this long (seconds).
_CACHE_TTL_SECONDS = 900

# Bound on timeline lines scanned when rebuilding the observed-event set.
_MAX_OBSERVED_SCAN = 20_000


def utcnow() -> datetime:
    return datetime.now(UTC)


class PrivateMailboxSource(BaseModel):
    """One registered read-only mailbox source."""

    source_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,63}$")
    provider: str = Field(min_length=1, max_length=100)
    account: str = Field(min_length=3, max_length=320)
    kind: Literal["earnings_calendar"]
    query: str = Field(min_length=1, max_length=1000)
    license_class: str = LicenseClass.PERSONAL_ONLY.value
    notes: list[str] = Field(default_factory=list)

    @field_validator("license_class")
    @classmethod
    def _personal_only(cls, value: str) -> str:
        """P1D sources are personal-only; a broader class is a config error."""

        if value != LicenseClass.PERSONAL_ONLY.value:
            raise ValueError("private mailbox sources must be license_class personal_only")
        return value


class PrivateSourceEvent(BaseModel):
    """One earnings announcement observed in a mailbox source."""

    kind: str = EARNINGS_ANNOUNCEMENT_KIND
    symbol: str
    market: str
    announcement_date: date
    name: str | None = None
    market_provider: str
    source_message_id: str
    source_subject: str
    sent_at: datetime


class PrivateSourceFetchOutcome(BaseModel):
    """Full debug response for one private-source fetch."""

    source_id: str
    provider: str
    fetch_state: AcquisitionFetchState
    auth_state: AuthState
    source_url: str | None = None
    retrieved_at: datetime | None = None
    messages_scanned: int = 0
    messages_new: int = 0
    events: list[PrivateSourceEvent] = Field(default_factory=list)
    new_events: int = 0
    timeline_entries: int = 0
    network: list[NetworkExchange] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    cache_ttl_seconds: int = _CACHE_TTL_SECONDS


class PrivateSourceRegistryStore:
    """Durable registry of private source definitions (one JSON file)."""

    def __init__(self, root: Path) -> None:
        self.root = root

    @property
    def path(self) -> Path:
        return self.root / "sources.json"

    def load(self) -> list[PrivateMailboxSource]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
        if not isinstance(raw, list):
            return []
        sources: list[PrivateMailboxSource] = []
        for item in raw:
            if isinstance(item, dict):
                try:
                    sources.append(PrivateMailboxSource.model_validate(item))
                except ValidationError:
                    continue
        return sources

    def save(self, sources: list[PrivateMailboxSource]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = [source.model_dump(mode="json") for source in sources]
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


class PrivateMessageFingerprintStore:
    """Append-only JSONL of ``{"message_id", "sha256"}`` per source."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def path(self, source_id: str) -> Path:
        return self.root / f"{_safe_component(source_id)}.jsonl"

    def known(self, source_id: str) -> set[tuple[str, str]]:
        """All recorded (message_id, sha256) pairs."""

        path = self.path(source_id)
        if not path.exists():
            return set()
        pairs: set[tuple[str, str]] = set()
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    item = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                message_id = item.get("message_id")
                sha = item.get("sha256")
                if isinstance(message_id, str) and message_id and isinstance(sha, str) and sha:
                    pairs.add((message_id, sha))
        return pairs

    def record(self, source_id: str, pairs: list[tuple[str, str]]) -> None:
        if not pairs:
            return
        path = self.path(source_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            for message_id, sha in pairs:
                handle.write(json.dumps({"message_id": message_id, "sha256": sha}) + "\n")


def mailbox_source_url(account: str, query: str) -> str:
    """Provenance URL for a mailbox source: the query is URL-encoded."""

    return f"mailbox://{account}?q={quote(query, safe='')}"


def body_sha256(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


class PrivateSourceService:
    """Fetches, extracts, and records events from authorized private sources."""

    def __init__(
        self,
        *,
        data_dir: Path,
        reader_factory: Callable[[PrivateMailboxSource], MailboxReader],
        sources: list[PrivateMailboxSource] | None = None,
        now: Callable[[], datetime] = utcnow,
        max_messages: int = 40,
    ) -> None:
        self.data_dir = data_dir
        self._reader_factory = reader_factory
        self._registry = PrivateSourceRegistryStore(root=data_dir / "private-sources")
        persisted = self._registry.load()
        merged: dict[str, PrivateMailboxSource] = {source.source_id: source for source in persisted}
        for source in sources or []:
            merged[source.source_id] = source
        self._sources = merged
        if sources:
            self._registry.save(self.list_sources())
        self._now = now
        self._max_messages = max_messages
        self._fingerprints = PrivateMessageFingerprintStore(
            root=data_dir / "private-sources" / "fingerprints"
        )
        self._timeline = IrTimelineStore(root=data_dir / "private-source-timeline")
        self._cache: dict[str, tuple[datetime, PrivateSourceFetchOutcome]] = {}

    # ------------------------------------------------------------- registry

    def add_source(self, source: PrivateMailboxSource) -> None:
        """Register (or replace) a source and persist the registry."""

        self._sources[source.source_id] = source
        self._registry.save(self.list_sources())

    def list_sources(self) -> list[PrivateMailboxSource]:
        return [self._sources[key] for key in sorted(self._sources)]

    def get_source(self, source_id: str) -> PrivateMailboxSource | None:
        return self._sources.get(source_id)

    # ---------------------------------------------------------------- fetch

    def fetch(self, source_id: str, *, force_refresh: bool = False) -> PrivateSourceFetchOutcome:
        """Fetch one source and record newly observed events on the timeline."""

        cached = self._cache.get(source_id)
        if not force_refresh and cached is not None:
            cached_at, outcome = cached
            age = (self._now() - cached_at).total_seconds()
            if age < _CACHE_TTL_SECONDS:
                return outcome.model_copy(deep=True)

        source = self._sources.get(source_id)
        if source is None:
            return PrivateSourceFetchOutcome(
                source_id=source_id,
                provider="",
                fetch_state=AcquisitionFetchState.FAILED,
                auth_state=AuthState.UNKNOWN,
                notes=[f"unknown private source: {source_id}"],
            )

        now = self._now()
        source_url = mailbox_source_url(source.account, source.query)
        network: list[NetworkExchange] = []
        started = time.perf_counter()

        try:
            reader = self._reader_factory(source)
            messages = reader.search(source.query, max_results=self._max_messages)
        except MailboxAccessError as exc:
            elapsed_ms = (time.perf_counter() - started) * 1000
            network.append(self._exchange(source_url, elapsed_ms, now))
            if exc.state == AcquisitionFetchState.AUTH_EXPIRED:
                # Never report an expired session as an empty successful run.
                return PrivateSourceFetchOutcome(
                    source_id=source.source_id,
                    provider=source.provider,
                    fetch_state=AcquisitionFetchState.AUTH_EXPIRED,
                    auth_state=AuthState.UNAUTHENTICED,
                    source_url=source_url,
                    retrieved_at=now,
                    network=network,
                    notes=[f"operator reauthentication required: {exc.reason}"],
                )
            return PrivateSourceFetchOutcome(
                source_id=source.source_id,
                provider=source.provider,
                fetch_state=AcquisitionFetchState.FAILED,
                auth_state=AuthState.UNKNOWN,
                source_url=source_url,
                retrieved_at=now,
                network=network,
                notes=[exc.reason],
            )

        elapsed_ms = (time.perf_counter() - started) * 1000
        network.append(self._exchange(source_url, elapsed_ms, now))

        notes: list[str] = []
        if not messages:
            notes.append("no messages matched the source query")

        known_messages = self._fingerprints.known(source.source_id)
        observed_events = self._observed_event_keys()

        events: list[PrivateSourceEvent] = []
        fresh_events: list[PrivateSourceEvent] = []
        new_pairs: list[tuple[str, str]] = []
        new_message_ids: set[str] = set()
        for message in messages:
            digest = body_sha256(message.body)
            pair = (message.message_id, digest)
            is_new = pair not in known_messages
            if is_new:
                new_pairs.append(pair)
            extracted, extraction_notes = extract_earnings_calendar(message.body)
            for note in extraction_notes:
                notes.append(f"message {message.message_id}: {note}")
            for announcement in extracted:
                event = PrivateSourceEvent(
                    symbol=announcement.symbol,
                    market=announcement.market,
                    announcement_date=announcement.announcement_date,
                    name=announcement.name,
                    market_provider=source.provider,
                    source_message_id=message.message_id,
                    source_subject=message.subject,
                    sent_at=message.sent_at,
                )
                events.append(event)
                if not is_new:
                    continue
                fresh_events.append(event)
                new_message_ids.add(message.message_id)

        timeline_entries = 0
        for event in fresh_events:
            key = (event.symbol, str(event.announcement_date), event.source_message_id)
            if key in observed_events:
                continue
            observed_events.add(key)
            entry = timeline_entry(
                kind=EARNINGS_ANNOUNCEMENT_KIND,
                symbol=event.symbol,
                provider=source.provider,
                source_url=source_url,
                retrieved_at=now,
                as_of=None,
                license_class=source.license_class,
                payload={
                    **event.model_dump(mode="json"),
                    "source_id": source.source_id,
                    "query": source.query,
                },
                notes=[f"observed in mailbox message {event.source_message_id}"],
            )
            self._timeline.append(event.symbol, entry)
            timeline_entries += 1

        self._fingerprints.record(source.source_id, new_pairs)

        outcome = PrivateSourceFetchOutcome(
            source_id=source.source_id,
            provider=source.provider,
            fetch_state=AcquisitionFetchState.OK,
            auth_state=AuthState.AUTHENTICATED,
            source_url=source_url,
            retrieved_at=now,
            messages_scanned=len(messages),
            messages_new=len(new_message_ids) if new_message_ids else len(new_pairs),
            events=events,
            new_events=timeline_entries,
            timeline_entries=timeline_entries,
            network=network,
            notes=notes,
        )
        self._cache[source.source_id] = (now, outcome)
        return outcome.model_copy(deep=True)

    # --------------------------------------------------------------- events

    def events(self, source_id: str | None = None, *, limit: int = 200) -> list[dict[str, Any]]:
        """Recorded earnings announcements, newest announcement date first.

        The instrument timeline is the single durable record; events are read
        back from it so what the API/CLI shows is exactly what was recorded
        with its provenance.
        """

        collected: list[dict[str, Any]] = []
        for entries in self._timeline_entries_by_symbol():
            for entry in entries:
                if entry.get("kind") != EARNINGS_ANNOUNCEMENT_KIND:
                    continue
                payload = entry.get("payload")
                if not isinstance(payload, dict):
                    continue
                if source_id is not None and payload.get("source_id") != source_id:
                    continue
                provenance = entry.get("provenance") or {}
                collected.append(
                    {
                        **payload,
                        "recorded_at": entry.get("recorded_at"),
                        "license_class": provenance.get("license_class"),
                        "market_provider": payload.get("market_provider")
                        or provenance.get("provider"),
                        "source_url": provenance.get("source_url"),
                    }
                )
        collected.sort(
            key=lambda item: (
                str(item.get("announcement_date") or ""),
                str(item.get("recorded_at") or ""),
            ),
            reverse=True,
        )
        return collected[:limit]

    # ------------------------------------------------------------- internal

    @staticmethod
    def _exchange(source_url: str, elapsed_ms: float, occurred_at: datetime) -> NetworkExchange:
        """Provenance record for a non-HTTP carrier (no headers, no secrets).

        ``record_exchange`` is intentionally not used here: it strips query
        strings because HTTP queries routinely carry tokens, but a mailbox
        search query is the selector the operator needs to interpret the
        record and carries no credential.
        """

        return NetworkExchange(
            method="SEARCH",
            url=source_url,
            status_code=None,
            content_type=None,
            size_bytes=None,
            duration_ms=elapsed_ms,
            occurred_at=occurred_at,
        )

    def _timeline_entries_by_symbol(self) -> list[list[dict[str, Any]]]:
        root = self._timeline.root
        if not root.exists():
            return []
        grouped: list[list[dict[str, Any]]] = []
        for path in sorted(root.glob("*/timeline.jsonl")):
            entries: list[dict[str, Any]] = []
            with path.open("r", encoding="utf-8") as handle:
                for index, line in enumerate(handle):
                    if index >= _MAX_OBSERVED_SCAN:
                        break
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        entry = json.loads(stripped)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(entry, dict):
                        entries.append(entry)
            grouped.append(entries)
        return grouped

    def _observed_event_keys(self) -> set[tuple[str, str, str]]:
        """Already-recorded (symbol, announcement_date, source_message_id)."""

        keys: set[tuple[str, str, str]] = set()
        for entries in self._timeline_entries_by_symbol():
            for entry in entries:
                if entry.get("kind") != EARNINGS_ANNOUNCEMENT_KIND:
                    continue
                payload = entry.get("payload")
                if not isinstance(payload, dict):
                    continue
                keys.add(
                    (
                        str(payload.get("symbol") or ""),
                        str(payload.get("announcement_date") or ""),
                        str(payload.get("source_message_id") or ""),
                    )
                )
        return keys
