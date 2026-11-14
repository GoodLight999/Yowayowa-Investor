"""P1C IR monitoring service: discovery -> detection -> acquisition ->
structured extraction -> previous-version diff -> provenance -> timeline.

The service composes the P1A acquisition toolkit pieces (transports, auth
detection, download capture) with the P1C IR modules (discovery,
documents, KPI extraction, timeline store). It is company-agnostic: sources
are described by ``IrSourceDefinition`` records and all extraction is
generic. Every step is fail-closed: a missing provenance field, an unparsable
document, or a transport error produces an explicit outcome/note — never a
silent zero or dropped record.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, ValidationError

from yowayowa.acquisition.auth import AuthSignal, HeuristicAuthDetector
from yowayowa.acquisition.discovery import (
    DiscoveredDocument,
    FingerprintStore,
    classify_documents,
    discover_ir_documents,
)
from yowayowa.acquisition.documents import ExtractedDocument, extract_document
from yowayowa.acquisition.ir import (
    IrTimelineStore,
    document_fingerprint,
    document_unit_hint,
    extract_kpis_from_tables,
    extract_kpis_from_text,
    merge_kpi_observations,
    timeline_entry,
)
from yowayowa.acquisition.models import (
    AcquisitionFetchState,
    AuthState,
    NetworkExchange,
)
from yowayowa.acquisition.snapshots import corrupt_rows_note
from yowayowa.acquisition.transport import (
    PrivateAcquisitionError,
    SessionTransport,
    TransportResponse,
    provenance_url,
    record_exchange,
)
from yowayowa.domain import LicenseClass

_MAX_DOCUMENTS_PER_RUN = 10
_MAX_DOCUMENT_BYTES = 10_000_000


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _default_http_transport(source: IrSourceDefinition) -> SessionTransport:
    """Plain unauthenticated GET transport for public IR pages.

    IR pages are public documents: no session, no credentials, no cookies.
    ``HttpxSessionTransport`` is not used here because it enforces the
    authenticated private-HTTP contract (same-origin private client), which the
    IR pipeline must not depend on. Browser-session connectors inject their own
    transport factory instead.
    """

    import httpx

    parts = urlsplit(source.listing_url)
    base_url = f"{parts.scheme}://{parts.netloc}"

    class _PublicGetTransport:
        def __init__(self) -> None:
            self._client = httpx.Client(
                base_url=base_url,
                follow_redirects=True,
                timeout=45.0,
                headers={
                    # Identifies the operator's client honestly. Do NOT add a
                    # "+https://..." reference URL: nitorihd.co.jp's WAF drops
                    # such user-agents (observed: silent connection hold until
                    # timeout), and a UA without one is served normally.
                    "user-agent": "Yowayowa-Investor/0.1 (personal research)",
                    "accept-language": "ja,en;q=0.8",
                },
            )

        def fetch(
            self,
            method: str,
            resource: str,
            *,
            params: Any = None,
            headers: Any = None,
            data: Any = None,
        ) -> TransportResponse:
            import time

            started = time.perf_counter()
            try:
                response = self._client.request(method, resource, params=params)
            except httpx.HTTPError as exc:
                raise PrivateAcquisitionError(
                    AcquisitionFetchState.FAILED,
                    f"transport error: {type(exc).__name__}",
                ) from exc
            elapsed = (time.perf_counter() - started) * 1000
            return TransportResponse(
                status_code=response.status_code,
                url=provenance_url(str(response.url)),
                content_type=response.headers.get("content-type"),
                text=response.text,
                content=response.content,
                elapsed_ms=elapsed,
            )

    return _PublicGetTransport()


class IrSourceDefinition(BaseModel):
    """One monitored non-API IR source (company IR page or disclosure set)."""

    source_id: str
    symbol: str
    provider: str
    listing_url: str
    license_class: str = LicenseClass.OFFICIAL_PUBLIC.value
    notes: list[str] = Field(default_factory=list)


class IrDocumentRecord(BaseModel):
    """Outcome record for one discovered document in a monitoring run."""

    url: str
    label: str
    kind: str
    status: str  # new | revised | unchanged
    sha256: str | None = None
    fetched: bool = False
    fetch_state: AcquisitionFetchState | None = None
    format: str | None = None
    parsed: bool | None = None
    parse_note: str | None = None
    kpis: list[dict[str, Any]] = Field(default_factory=list)
    kpi_diff: list[dict[str, Any]] = Field(default_factory=list)
    size_bytes: int | None = None
    notes: list[str] = Field(default_factory=list)


class IrSourceRegistryError(RuntimeError):
    """The persisted IR source registry exists but cannot be used.

    Fail closed: an unreadable registry must never be read as "no sources"
    (that silently stops IR monitoring) and must never be overwritten (that
    would destroy every operator-registered company). The file on disk is left
    untouched so it can be inspected, repaired, or restored from backup.
    """

    def __init__(self, path: Path, detail: str) -> None:
        self.path = path
        self.detail = detail
        super().__init__(
            f"IR source registry unusable ({detail}): {path}. "
            f"Recovery: restore the file from backup, repair the JSON by hand, or move it "
            f"aside (mv {path} {path}.corrupt) to start from an empty registry. "
            "This service never modifies or deletes the file it cannot read."
        )


class IrSourceRegistryStore:
    """Durable registry of IR source definitions (one JSON file).

    Company IR sources are operator-owned configuration, not per-run state:
    they must survive a process restart, otherwise the API surface silently
    loses every registered company.
    """

    def __init__(self, root: Path) -> None:
        self.root = root

    @property
    def path(self) -> Path:
        return self.root / "sources.json"

    def load(self) -> list[IrSourceDefinition]:
        definitions, _ = self._read()
        return definitions

    def load_checked(self) -> list[IrSourceDefinition]:
        """Read the registry, refusing to hide entries that cannot be parsed.

        ``load`` deliberately returns the parseable subset for inspection;
        the service uses this stricter form so a partially unreadable registry
        is an explicit error state instead of a silent loss of configuration.
        """

        definitions, invalid_entries = self._read()
        if invalid_entries:
            raise IrSourceRegistryError(
                self.path,
                f"{invalid_entries} of the persisted entries failed validation",
            )
        return definitions

    def save(self, definitions: list[IrSourceDefinition]) -> None:
        # Never persist over a registry we could not read in full: that is how
        # operator-defined sources get destroyed.
        _, invalid_entries = self._read()
        if invalid_entries:
            raise IrSourceRegistryError(
                self.path,
                f"{invalid_entries} of the persisted entries failed validation",
            )
        self.root.mkdir(parents=True, exist_ok=True)
        payload = [definition.model_dump(mode="json") for definition in definitions]
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _read(self) -> tuple[list[IrSourceDefinition], int]:
        """Parsed definitions plus the count of entries that failed validation.

        An unreadable file raises instead of looking like an empty registry.
        """

        if not self.path.exists():
            return [], 0
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise IrSourceRegistryError(self.path, f"{type(exc).__name__}: {exc}") from exc
        if not isinstance(raw, list):
            raise IrSourceRegistryError(
                self.path, f"expected a JSON list, found {type(raw).__name__}"
            )
        definitions: list[IrSourceDefinition] = []
        invalid_entries = 0
        for item in raw:
            if isinstance(item, dict):
                try:
                    definitions.append(IrSourceDefinition.model_validate(item))
                    continue
                except ValidationError:
                    pass
            invalid_entries += 1
        return definitions, invalid_entries


class IrMonitorOutcome(BaseModel):
    """Full outcome for one monitoring run over one IR source.

    ``fetch_state`` is derived from what the run actually retrieved, not from
    the listing GET alone: a run that discovered documents but could not
    retrieve a single byte is ``FAILED``. Counts are split so a caller reading
    only the summary cannot confuse "new documents detected on the listing
    page" with "new document bytes retrieved": ``new_count`` counts URL-level
    new detections (including documents beyond the fetch budget), while
    ``fetched_count`` counts records whose content was actually retrieved this
    run.
    """

    source_id: str
    symbol: str
    fetch_state: AcquisitionFetchState
    listing_url: str
    documents: list[IrDocumentRecord] = Field(default_factory=list)
    # New documents detected by URL identity. Includes documents that were not
    # fetched this run (budget) — always read it together with fetched_count.
    new_count: int = 0
    revised_count: int = 0
    unchanged_count: int = 0
    seen_count: int = 0
    # Documents whose bytes were retrieved and fingerprinted in this run.
    fetched_count: int = 0
    # Document fetch attempts that produced no response (HTTP error, transport
    # error, oversized document). Zero means every attempt succeeded.
    failed_fetch_count: int = 0
    timeline_entries: int = 0
    network: list[NetworkExchange] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class IrRevisionDiff(BaseModel):
    """Previous-version KPI diff for one document (revised or unchanged)."""

    url: str
    kpi: str
    label: str | None = None
    previous_value: float | None = None
    current_value: float | None = None
    change: str  # increase | decrease | revision (sign flip) | added | removed
    delta: float | None = None
    note: str | None = None


class IrTransportUnavailableError(PrivateAcquisitionError):
    """The per-source transport could not be built (connector / browser session).

    Fail closed: connector and browser-session construction happens *outside*
    the HTTP call, so a failure there is not an ``httpx`` error and used to
    escape ``monitor()`` as an unhandled exception (HTTP 500 at the API layer)
    instead of a FAILED outcome. The factory is now invoked inside the same
    ``PrivateAcquisitionError`` guard as the fetches, and any construction
    failure is normalised into this error so every acquisition path reports an
    explicit state plus a recovery procedure.

    This is a ``PrivateAcquisitionError`` on purpose: the guards in
    ``_fetch_listing`` / ``_fetch_document`` already treat that type as a
    fail-closed acquisition outcome, so no path can regress to a bare raise.
    """

    def __init__(self, source_id: str, detail: str) -> None:
        self.source_id = source_id
        self.detail = detail
        super().__init__(
            AcquisitionFetchState.FAILED,
            f"transport unavailable for source {source_id} ({detail}). "
            "Recovery: re-connect the operator session for this source (re-run the "
            "login/reauthentication flow), rebuild the connector/browser session that "
            "provides the transport factory, then re-run monitoring. "
            "No document was fetched in this run.",
        )


TransportFactory = Callable[[IrSourceDefinition], SessionTransport]


class IrMonitorService:
    """Orchestrates the P1C pipeline for a set of IR source definitions."""

    def __init__(
        self,
        *,
        data_dir: Path,
        transport_factory: TransportFactory,
        sources: list[IrSourceDefinition] | None = None,
        now: Callable[[], datetime] = _utcnow,
        detector: HeuristicAuthDetector | None = None,
        max_documents_per_run: int = _MAX_DOCUMENTS_PER_RUN,
    ) -> None:
        self.data_dir = data_dir
        self._transport_factory = transport_factory
        self._registry = IrSourceRegistryStore(root=data_dir / "ir-sources")
        self._registry_error: IrSourceRegistryError | None = None
        try:
            persisted = self._registry.load_checked()
        except IrSourceRegistryError as exc:
            # Fail closed: a corrupt registry is an explicit error state, never
            # "no sources", and it must not be overwritten.
            self._registry_error = exc
            persisted = []
        merged: dict[str, IrSourceDefinition] = {source.source_id: source for source in persisted}
        for source in sources or []:
            merged[source.source_id] = source
        self._sources = merged
        if sources and self._registry_error is None:
            try:
                self._registry.save(self.list_sources())
            except IrSourceRegistryError as exc:
                # The file parsed but holds entries that no longer validate.
                self._registry_error = exc
        self._now = now
        self._detector = detector or HeuristicAuthDetector()
        # A non-positive run budget cannot bound anything: ``max(1, 0 // 2)``
        # would fetch one document past a zero budget and a negative remainder
        # made ``reverify_pool[:-N]`` slice targets away. Normalise instead of
        # silently breaking the caller's bound.
        self._max_documents = (
            max_documents_per_run if max_documents_per_run > 0 else _MAX_DOCUMENTS_PER_RUN
        )
        self._fingerprints = FingerprintStore(root=data_dir / "ir-fingerprints")
        self._timeline = IrTimelineStore(root=data_dir / "ir-timeline")
        self._kpi_history = IrKpiHistoryStore(root=data_dir / "ir-kpi-history")

    @property
    def registry_error(self) -> IrSourceRegistryError | None:
        """Why the persisted registry is unusable, or ``None`` when it is fine."""

        return self._registry_error

    # ------------------------------------------------------------- registry

    def add_source(self, definition: IrSourceDefinition) -> None:
        """Register (or replace) a source and persist the registry.

        Refuses to write while the persisted registry is unusable: saving the
        in-memory list would silently drop every source already on disk.
        """

        if self._registry_error is not None:
            raise self._registry_error
        self._sources[definition.source_id] = definition
        self._registry.save(self.list_sources())

    def list_sources(self) -> list[IrSourceDefinition]:
        definitions = [self._sources[key] for key in sorted(self._sources)]
        if not definitions and self._registry_error is not None:
            # An unreadable registry must never look like "no sources registered".
            raise self._registry_error
        return definitions

    def get_source(self, source_id: str) -> IrSourceDefinition | None:
        definition = self._sources.get(source_id)
        if definition is None and self._registry_error is not None:
            raise self._registry_error
        return definition

    # ------------------------------------------------------------- timeline

    def timeline(
        self, symbol: str, *, kind: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        return self._timeline.entries(symbol, kind=kind, limit=limit)

    def document_kpi_history(
        self, url: str, kpi: str | None = None, *, limit: int = 10
    ) -> list[dict[str, Any]]:
        return self._kpi_history.entries(url, kpi=kpi, limit=limit)

    # ----------------------------------------------------------- monitoring

    def monitor(self, source_id: str) -> IrMonitorOutcome:
        source = self._sources.get(source_id)
        if source is None:
            unknown_notes = [f"unknown IR source: {source_id}"]
            if self._registry_error is not None:
                unknown_notes.insert(0, str(self._registry_error))
            return IrMonitorOutcome(
                source_id=source_id,
                symbol="",
                fetch_state=AcquisitionFetchState.FAILED,
                listing_url="",
                notes=unknown_notes,
            )
        network: list[NetworkExchange] = []
        notes: list[str] = []
        if self._registry_error is not None:
            notes.append(str(self._registry_error))
        now = self._now()

        # 1. discovery: fetch the listing page
        listing_html, listing_state = self._fetch_listing(source, network, notes)
        if listing_html is None:
            return IrMonitorOutcome(
                source_id=source.source_id,
                symbol=source.symbol,
                fetch_state=listing_state or AcquisitionFetchState.FAILED,
                listing_url=source.listing_url,
                network=network,
                notes=notes,
            )

        discovered, discovery_notes = discover_ir_documents(
            listing_html, listing_url=source.listing_url
        )
        notes.extend(discovery_notes)

        # 2. detection: pre-classify ALL discovered documents by URL so the
        # run can (a) record every discovered URL as seen and (b) fetch only
        # candidates that are potentially new/revised (bounded).
        # Both readers share one diagnostics dict: each read clears and
        # rewrites it, so the note is built after the second read (both read
        # the same file, the counts are identical).
        fingerprint_diagnostics: dict[str, Any] = {}
        known = self._fingerprints.known_keys(source.source_id, diagnostics=fingerprint_diagnostics)
        known_content = self._fingerprints.known_content_urls(
            source.source_id, diagnostics=fingerprint_diagnostics
        )
        corrupt_note = corrupt_rows_note(fingerprint_diagnostics)
        if corrupt_note is not None:
            # Same one-line notes contract as every other degradation here:
            # the skipped rows make those URLs classify as new until they are
            # re-fetched, so re-fetch and duplicate alerts can fire.
            notes.append(
                corrupt_note + "; until re-fetched, the affected URLs classify as new and"
                " may trigger re-fetch and duplicate alerts"
            )
        fetched: dict[str, dict[str, str]] = {}
        responses: dict[str, TransportResponse] = {}

        def _url_only_fingerprint(document: DiscoveredDocument) -> dict[str, str]:
            # URL-level identity for documents we will not fetch this run;
            # sha stays empty so a later real fetch always re-classifies.
            return {"url": document.url, "sha256": "", "label": document.label}

        candidates = classify_documents(
            discovered, {}, known, known_content_urls=known_content
        )  # URL-only pass: new vs seen
        # Budget split: new documents and re-verification share the run
        # budget. Re-verification (catching revised earnings documents)
        # reserves at least half the budget so a stream of never-fetched
        # historical documents cannot starve revision detection.
        new_budget = max(1, self._max_documents // 2)
        fetch_targets = [item for item in candidates if item["status"] == "new"][:new_budget]
        # Re-verification budget prefers URLs with a prior content hash:
        # those can prove "unchanged" or surface a real "revised"; URLs never
        # content-verified wait until new-document budget frees up.
        reverify_budget = max(0, self._max_documents - len(fetch_targets))
        if reverify_budget > 0:
            reverify_pool = [item for item in candidates if item["status"] == "seen"]
            reverify_pool.sort(key=lambda item: item["url"] not in known_content)
            reverify_targets = reverify_pool[:reverify_budget]
        else:
            reverify_targets = []
        # Leftover budget (no seen docs left to reverify) goes to new docs.
        if not reverify_targets and len(fetch_targets) < self._max_documents:
            fetch_targets = [item for item in candidates if item["status"] == "new"][
                : self._max_documents
            ]
        by_url = {document.url: document for document in discovered}
        fetch_attempts = 0
        for item in fetch_targets + reverify_targets:
            document = by_url.get(item["url"])
            if document is None:
                continue
            fetch_attempts += 1
            response = self._fetch_document(source, document, network, notes)
            if response is None:
                continue
            fingerprint = document_fingerprint(
                url=document.url, content=response.content, label=document.label
            )
            fetched[document.url] = fingerprint
            responses[document.url] = response

        # Final classification: fetched documents are classified by content
        # fingerprint; unfetched keep the URL-only classification. known_keys
        # already carries URL-only records as "url|" so a document recorded in
        # an earlier run (fetch budget exhausted) is never re-flagged "new".
        classified = classify_documents(
            discovered, fetched, known, known_content_urls=known_content
        )

        # 3-6. extraction / diff / provenance / timeline for new/revised/
        # verified (first content observation) docs
        records: list[IrDocumentRecord] = []
        timeline_entries = 0
        for item in classified:
            record, wrote = self._process_document(
                source, item, responses.get(item["url"]), now, notes
            )
            records.append(record)
            timeline_entries += 1 if wrote else 0

        # Persist fingerprints: content fingerprints for fetched documents,
        # URL-only fingerprints for the rest so the next run sees them.
        fingerprints: list[dict[str, str]] = [fetched[url] for url in sorted(fetched)]
        seen_urls = {fingerprint["url"] for fingerprint in fingerprints}
        for document in discovered:
            if document.url not in seen_urls:
                fingerprints.append(_url_only_fingerprint(document))
        if fingerprints:
            self._fingerprints.record(source.source_id, fingerprints)

        new_count = sum(1 for record in records if record.status == "new")
        revised_count = sum(1 for record in records if record.status == "revised")
        # unchanged counts CONTENT-verified records only; URL-only records
        # (never fetched) are reported separately as seen.
        unchanged_count = sum(1 for record in records if record.status == "unchanged")
        seen_count = sum(1 for record in records if record.status == "seen")
        # Derive the top-level state from what was actually retrieved. A run
        # that discovered documents but retrieved none of them is a failed
        # retrieval: reporting ``ok`` next to a nonzero new_count made callers
        # read "N new documents fetched" out of a run with zero bytes fetched.
        fetched_count = len(fetched)
        failed_fetch_count = max(0, fetch_attempts - fetched_count)
        fetch_state = AcquisitionFetchState.OK
        if fetch_attempts and not fetched_count:
            fetch_state = AcquisitionFetchState.FAILED
            notes.append(
                f"no document could be retrieved: all {fetch_attempts} document "
                "fetch attempt(s) failed (new_count counts URL-only detections)"
            )
        return IrMonitorOutcome(
            source_id=source.source_id,
            symbol=source.symbol,
            fetch_state=fetch_state,
            listing_url=source.listing_url,
            documents=records,
            new_count=new_count,
            revised_count=revised_count,
            unchanged_count=unchanged_count,
            seen_count=seen_count,
            fetched_count=fetched_count,
            failed_fetch_count=failed_fetch_count,
            timeline_entries=timeline_entries,
            network=network,
            notes=notes,
        )

    # ------------------------------------------------------------- fetches

    def _transport(self, source: IrSourceDefinition) -> SessionTransport:
        """Build the per-source transport inside the fail-closed guard.

        Connector/browser-session construction is part of acquisition, not
        plumbing: a failure there must surface as a FAILED outcome with a
        recovery note, never as an exception out of ``monitor()`` (which the
        API layer would turn into HTTP 500). A ``PrivateAcquisitionError`` is
        re-raised unchanged so its own state (AUTH_EXPIRED / FAILED) survives.
        """

        try:
            return self._transport_factory(source)
        except PrivateAcquisitionError:
            raise
        except Exception as exc:  # any factory failure is an acquisition failure
            raise IrTransportUnavailableError(
                source.source_id, f"{type(exc).__name__}: {exc}"
            ) from exc

    def _fetch_listing(
        self,
        source: IrSourceDefinition,
        network: list[NetworkExchange],
        notes: list[str],
    ) -> tuple[str | None, AcquisitionFetchState | None]:
        now = self._now()
        try:
            response = self._fetch_url(source, source.listing_url)
        except PrivateAcquisitionError as exc:
            notes.append(exc.reason)
            return None, exc.state
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
            notes.append("operator reauthentication required")
            return None, AcquisitionFetchState.AUTH_EXPIRED
        if response.status_code >= 400:
            notes.append(f"listing fetch failed with HTTP {response.status_code}")
            return None, AcquisitionFetchState.FAILED
        return response.text, None

    def _fetch_document(
        self,
        source: IrSourceDefinition,
        document: DiscoveredDocument,
        network: list[NetworkExchange],
        notes: list[str],
    ) -> TransportResponse | None:
        now = self._now()
        try:
            response = self._fetch_url(source, document.url)
        except PrivateAcquisitionError as exc:
            notes.append(f"document fetch failed ({document.url}): {exc.reason}")
            return None
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
        if response.status_code >= 400:
            notes.append(f"document fetch HTTP {response.status_code}: {document.url}")
            return None
        if len(response.content) > _MAX_DOCUMENT_BYTES:
            notes.append(f"document too large, skipped: {document.url}")
            return None
        return response

    def _fetch_url(self, source: IrSourceDefinition, url: str) -> TransportResponse:
        transport = self._transport(source)
        parts = urlsplit(url)
        resource = parts.path or "/"
        if parts.query:
            resource = f"{resource}?{parts.query}"
        return transport.fetch("GET", resource)

    def _detect_auth(self, response: TransportResponse) -> AuthState:
        return self._detector.detect(
            AuthSignal(
                url=response.url,
                status_code=response.status_code,
                title=None,
                body_text=response.text[:200_000],
            )
        )

    # ----------------------------------------------------------- processing

    def _process_document(
        self,
        source: IrSourceDefinition,
        item: dict[str, Any],
        response: TransportResponse | None,
        now: datetime,
        notes: list[str],
    ) -> tuple[IrDocumentRecord, bool]:
        """Process one classified document.

        Returns ``(record, wrote_timeline)``: ``wrote_timeline`` is True only
        immediately after a successful ``self._timeline.append(...)`` — never
        for URL-only classifications that did not reach the timeline.
        """
        url: str = item["url"]
        record = IrDocumentRecord(
            url=url,
            label=item["label"],
            kind=item["kind"],
            status=item["status"],
            sha256=item.get("sha256"),
            fetched=response is not None,
        )
        if response is None or not item.get("sha256"):
            record.notes.append("not fetched; classified by URL only")
            return record, False
        record.fetch_state = AcquisitionFetchState.OK
        record.size_bytes = len(response.content)

        extracted = extract_document(
            content=response.content,
            content_type=response.content_type,
            filename=url.rsplit("/", 1)[-1],
            url=url,
        )
        record.format = extracted.format
        record.parsed = extracted.parsed
        record.parse_note = extracted.parse_note

        kpis, kpi_notes = self._extract_kpis(extracted)
        record.kpis = kpis
        record.notes.extend(kpi_notes)

        active = item["status"] in ("new", "revised", "verified")
        if item["status"] in ("new", "revised"):
            kpi_diff = self._diff_against_previous(url, kpis)
            record.kpi_diff = kpi_diff

        if active:
            self._kpi_history.append(
                url,
                kpis,
                provenance={
                    "provider": source.provider,
                    "source_url": url,
                    "license_class": source.license_class,
                    "retrieved_at": now.isoformat(),
                    "as_of": None,
                },
            )
            entry = timeline_entry(
                kind="document" if item["kind"] == "document" else "page",
                symbol=source.symbol,
                provider=source.provider,
                source_url=url,
                retrieved_at=now,
                as_of=None,
                license_class=source.license_class,
                payload={
                    "status": item["status"],
                    "label": item["label"],
                    "sha256": item["sha256"],
                    "format": extracted.format,
                    "parsed": extracted.parsed,
                    "parse_note": extracted.parse_note,
                    "size_bytes": len(response.content),
                    "kpis": kpis,
                    "kpi_diff": record.kpi_diff,
                },
                notes=record.notes,
            )
            self._timeline.append(source.symbol, entry)
            return record, True
        return record, False

    def _extract_kpis(self, extracted: ExtractedDocument) -> tuple[list[dict[str, Any]], list[str]]:
        if not extracted.parsed:
            return [], [extracted.parse_note or "document not parsed"]
        observations: list[dict[str, Any]] = []
        notes: list[str] = []
        # A document-level unit declaration ("(Millions of yen)") applies to
        # the tables too: English summary PDFs print it as a caption line
        # outside the reconstructed table grid, so table_unit_hint misses it.
        doc_unit = document_unit_hint(extracted.text) if extracted.text else None
        if extracted.tables:
            table_kpis, table_notes = extract_kpis_from_tables(extracted.tables, unit_hint=doc_unit)
            observations.extend(table_kpis)
            notes.extend(table_notes)
        if extracted.text:
            text_kpis, text_notes = extract_kpis_from_text(extracted.text)
            observations.extend(text_kpis)
            notes.extend(text_notes)
        return merge_kpi_observations(observations), notes

    def _diff_against_previous(
        self, url: str, current: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        previous_entries = self._kpi_history.entries(url, limit=2)
        if not previous_entries:
            return []
        # entries are newest-first; the previous version is the tail entry
        previous = previous_entries[-1]
        previous_kpis = {item["kpi"]: item.get("value") for item in previous.get("kpis", [])}
        current_kpis = {item["kpi"]: item.get("value") for item in current}
        diffs: list[dict[str, Any]] = []
        for kpi in sorted(set(previous_kpis) | set(current_kpis)):
            prev_value = previous_kpis.get(kpi)
            cur_value = current_kpis.get(kpi)
            if prev_value == cur_value:
                continue
            if prev_value is None:
                change = "added"
                delta = None
            elif cur_value is None:
                change = "removed"
                delta = None
            else:
                delta = round(float(cur_value) - float(prev_value), 6)
                sign_flip = (prev_value > 0) != (cur_value > 0)
                change = "revision" if sign_flip else ("increase" if delta > 0 else "decrease")
            diffs.append(
                {
                    "kpi": kpi,
                    "previous_value": prev_value,
                    "current_value": cur_value,
                    "change": change,
                    "delta": delta,
                }
            )
        return diffs


class IrKpiHistoryStore:
    """Append-only JSONL of KPI observations per document URL."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def _path(self, url: str, *, create: bool = False) -> Path:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
        directory = self._root / digest[:2]
        if create:
            directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{digest}.jsonl"

    def append(
        self,
        url: str,
        kpis: list[dict[str, Any]],
        *,
        provenance: dict[str, Any] | None = None,
    ) -> None:
        entry: dict[str, Any] = {
            "recorded_at": _utcnow().isoformat(),
            "url": url,
            "kpis": kpis,
        }
        if provenance is not None:
            entry["provenance"] = provenance
        with self._path(url, create=True).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")

    def entries(self, url: str, *, kpi: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
        path = self._path(url)
        if not path.exists():
            return []
        with path.open("r", encoding="utf-8") as handle:
            lines = handle.readlines()
        result: list[dict[str, Any]] = []
        for line in reversed(lines):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                entry = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if kpi is not None:
                entry["kpis"] = [i for i in entry.get("kpis", []) if i.get("kpi") == kpi]
            result.append(entry)
            if len(result) >= limit:
                break
        return result
