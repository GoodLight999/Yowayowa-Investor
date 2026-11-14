"""Boundary regression tests for ``IrMonitorService`` (P1C IR monitoring).

Complements ``tests/test_ir_acquisition.py`` (happy path + revision +
extraction) with the failure boundaries of one monitoring run. Every external
HTTP exchange is served by an in-memory transport stub; no test opens a socket,
and every filesystem write lands under ``tmp_path``.

Boundary inventory (each item is asserted by at least one test below):

    B1  unknown source id                 -> failed, storage untouched
    B2  listing transport error           -> failed, no documents
    B3  listing auth-expired state        -> auth_expired propagated
    B4  listing HTTP >= 400               -> failed, no document fetch attempted
    B5  listing returns a login page      -> auth_expired + reauth note
    B6  listing with zero document links  -> ok but explicitly noted, zero writes
    B7  listing 304 Not Modified          -> no documents, no fabricated success
    B8  duplicate / fragment links        -> one record, one fetch
    B9  document HTTP >= 400              -> noted per URL, never content-verified
    B10 zero documents fetched            -> never reported as a verified success
    B11 top-level state with zero fetch   -> failed, not ok (F1 pin, inverted)
    B12 document transport exception      -> run continues, other docs processed
    B13 oversized document                -> skipped with note, no hash recorded
    B14 unparsable document               -> parsed=False + note, empty KPI set
    B15 fetch budget boundary             -> at most N fetches, rest URL-only
    B16 identical rerun                   -> unchanged, zero timeline writes
    B17 changed bytes at a known URL      -> revised + KPI diff + timeline write
    B18 corrupt fingerprint lines         -> skipped, run still completes
    B18b corrupt fingerprint count        -> counted in outcome.notes (rows + re-fetch risk)
    B19 corrupt registry JSON             -> explicit registry error, no overwrite
    B20 transport factory raising         -> failed outcome + recovery note (F3, inverted)
    B21 KPI history read for unknown URL  -> empty, creates no directories
    B22 partial document failure          -> ok run, failure counted not hidden
    B23 budget-limited run                -> new_count vs fetched_count distinct
    B24 non-positive fetch budget         -> normalised, never a broken bound
    B25 factory fails mid-run             -> noted per document, never raised
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from yowayowa.acquisition.models import AcquisitionFetchState
from yowayowa.acquisition.transport import PrivateAcquisitionError, TransportResponse
from yowayowa.services.ir_monitor_service import (
    IrMonitorOutcome,
    IrMonitorService,
    IrSourceDefinition,
    IrSourceRegistryError,
    IrSourceRegistryStore,
)

LISTING_URL = "https://ir.example.co.jp/library/briefing.html"
LISTING_RESOURCE = "/library/briefing.html"
SYMBOL = "1234.T"
SOURCE_ID = "example-ir"

_CSV_V1 = "\u9805\u76ee,\u5024\nbacklog,1200\n".encode()
_CSV_V2 = "\u9805\u76ee,\u5024\nbacklog,900\n".encode()
_LISTING_HTML = (
    '<html><body><a href="/ir/a.csv">\u6c7a\u7b97\u8cc7\u6599</a></body></html>'
).encode()


class _StubTransport:
    """In-memory transport: resource -> (status, content_type, body) or exception.

    A route value may also be an exception instance, which is raised on fetch to
    exercise the transport-error paths (no network, no real client).
    """

    def __init__(self, routes: dict[str, Any]) -> None:
        self.routes = routes
        self.calls: list[str] = []

    def fetch(
        self,
        method: str,
        resource: str,
        *,
        params: Any = None,
        headers: Any = None,
        data: Any = None,
    ) -> TransportResponse:
        self.calls.append(resource)
        entry = self.routes.get(resource)
        if entry is None:
            raise PrivateAcquisitionError(
                AcquisitionFetchState.FAILED, f"unrouted resource: {resource}"
            )
        if isinstance(entry, BaseException):
            raise entry
        status, content_type, body = entry
        return TransportResponse(
            status_code=status,
            url=f"https://ir.example.co.jp{resource}",
            content_type=content_type,
            text=body.decode("utf-8", errors="replace"),
            content=body,
            elapsed_ms=1.0,
        )


def _listing(*hrefs: str) -> bytes:
    anchors = "".join(f'<a href="{href}">\u6c7a\u7b97\u8cc7\u6599</a>' for href in hrefs)
    return f"<html><body>{anchors}</body></html>".encode()


def _routes(*, listing: tuple[int, str | None, bytes], **documents: Any) -> dict[str, Any]:
    """Build a route table; ``ir__a_csv=...`` means ``/ir/a.csv``.

    Keyword names cannot carry "." or "/", so a document keyword is the path
    under ``/ir/`` with ``_`` standing in for ".".
    """

    routes: dict[str, Any] = {LISTING_RESOURCE: listing}
    for name, entry in documents.items():
        resource = name.removeprefix("ir__").replace("_", ".")
        routes["/ir/" + resource] = entry
    return routes


def _service(
    tmp_path: Path, routes: dict[str, Any], **kwargs: Any
) -> tuple[IrMonitorService, _StubTransport]:
    transport = _StubTransport(routes)
    service = IrMonitorService(
        data_dir=tmp_path,
        transport_factory=lambda source: transport,
        sources=[
            IrSourceDefinition(
                source_id=SOURCE_ID,
                symbol=SYMBOL,
                provider="ir.example.co.jp",
                listing_url=LISTING_URL,
            )
        ],
        **kwargs,
    )
    return service, transport


def _fetched(outcome: IrMonitorOutcome) -> list[Any]:
    return [document for document in outcome.documents if document.fetched]


def _statuses(outcome: IrMonitorOutcome) -> dict[str, str]:
    return {document.url.rsplit("/", 1)[-1]: document.status for document in outcome.documents}


def _fingerprint_lines(tmp_path: Path) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    for path in sorted((tmp_path / "ir-fingerprints").rglob("*.jsonl")):
        text = path.read_text(encoding="utf-8")
        lines.extend(json.loads(line) for line in text.splitlines() if line.strip())
    return lines


def _assert_no_run_storage(tmp_path: Path) -> None:
    for directory in ("ir-fingerprints", "ir-timeline", "ir-kpi-history"):
        assert not (tmp_path / directory).exists(), f"{directory} must not be created"


# ------------------------------------------------------------------- B1 / B2


def test_unknown_source_is_failed_and_touches_no_storage(tmp_path: Path) -> None:
    """B1: an unknown source id is an explicit failure, not an empty success."""

    routes = _routes(listing=(200, "text/html; charset=utf-8", _LISTING_HTML))
    service, transport = _service(tmp_path, routes)

    outcome = service.monitor("missing-source")

    assert outcome.fetch_state is AcquisitionFetchState.FAILED
    assert outcome.notes == ["unknown IR source: missing-source"]
    assert outcome.documents == []
    assert outcome.new_count == 0
    assert transport.calls == []
    _assert_no_run_storage(tmp_path)


def test_listing_transport_error_fails_closed(tmp_path: Path) -> None:
    """B2: a transport-level listing error yields failed and a carried reason."""

    routes = {
        LISTING_RESOURCE: PrivateAcquisitionError(
            AcquisitionFetchState.FAILED, "transport error: ConnectTimeout"
        )
    }
    service, transport = _service(tmp_path, routes)

    outcome = service.monitor(SOURCE_ID)

    assert outcome.fetch_state is AcquisitionFetchState.FAILED
    assert outcome.notes == ["transport error: ConnectTimeout"]
    assert outcome.documents == []
    assert outcome.timeline_entries == 0
    assert transport.calls == [LISTING_RESOURCE]
    _assert_no_run_storage(tmp_path)


# ------------------------------------------------------------------- B3 / B4


def test_listing_auth_expired_state_is_propagated(tmp_path: Path) -> None:
    """B3: the transport's AUTH_EXPIRED state must reach the outcome."""

    routes = {
        LISTING_RESOURCE: PrivateAcquisitionError(
            AcquisitionFetchState.AUTH_EXPIRED, "session expired"
        )
    }
    service, _ = _service(tmp_path, routes)

    outcome = service.monitor(SOURCE_ID)

    assert outcome.fetch_state is AcquisitionFetchState.AUTH_EXPIRED
    assert outcome.notes == ["session expired"]
    assert outcome.documents == []


def test_listing_http_error_stops_before_any_document_fetch(tmp_path: Path) -> None:
    """B4: a 500 listing must not be parsed and must not trigger document GETs."""

    routes = _routes(
        listing=(500, "text/html", b"<html><body><a href='/ir/a.csv'>x</a></body></html>"),
        ir__a_csv=(200, "text/csv", _CSV_V1),
    )
    service, transport = _service(tmp_path, routes)

    outcome = service.monitor(SOURCE_ID)

    assert outcome.fetch_state is AcquisitionFetchState.FAILED
    assert outcome.documents == []
    assert "HTTP 500" in outcome.notes[0]
    assert transport.calls == [LISTING_RESOURCE]
    _assert_no_run_storage(tmp_path)


# ------------------------------------------------------------------- B5 / B6


def test_listing_login_page_marks_auth_expired(tmp_path: Path) -> None:
    """B5: an unauthenticated listing response becomes a reauthentication note."""

    routes = {LISTING_RESOURCE: (403, "text/html; charset=utf-8", b"<html>please sign in</html>")}
    service, _ = _service(tmp_path, routes)

    outcome = service.monitor(SOURCE_ID)

    assert outcome.fetch_state is AcquisitionFetchState.AUTH_EXPIRED
    assert outcome.notes == ["operator reauthentication required"]
    assert outcome.documents == []
    _assert_no_run_storage(tmp_path)


def test_listing_without_document_links_is_noted_and_writes_nothing(tmp_path: Path) -> None:
    """B6: zero discovered documents is reported as a note and writes no state."""

    routes = _routes(
        listing=(200, "text/html; charset=utf-8", b"<html><body><p>IR</p></body></html>")
    )
    service, transport = _service(tmp_path, routes)

    outcome = service.monitor(SOURCE_ID)

    assert outcome.fetch_state is AcquisitionFetchState.OK
    assert outcome.documents == []
    assert outcome.new_count == outcome.revised_count == outcome.unchanged_count == 0
    assert outcome.timeline_entries == 0
    assert outcome.notes == ["no IR document links found on listing page"]
    assert service.timeline(SYMBOL) == []
    assert transport.calls == [LISTING_RESOURCE]
    _assert_no_run_storage(tmp_path)


# ------------------------------------------------------------------- B7 / B8


def test_listing_not_modified_304_yields_no_documents(tmp_path: Path) -> None:
    """B7: a 304 listing carries no body; the run must not invent documents."""

    routes = {LISTING_RESOURCE: (304, None, b"")}
    service, _ = _service(tmp_path, routes)

    outcome = service.monitor(SOURCE_ID)

    assert outcome.documents == []
    assert outcome.timeline_entries == 0
    assert "no IR document links" in outcome.notes[0]
    _assert_no_run_storage(tmp_path)


def test_duplicate_and_fragment_links_are_fetched_once(tmp_path: Path) -> None:
    """B8: repeated anchors and fragment variants collapse to one document."""

    listing = _listing("/ir/a.csv", "/ir/a.csv#p2", "/ir/a.csv")
    routes = _routes(
        listing=(200, "text/html; charset=utf-8", listing),
        ir__a_csv=(200, "text/csv", _CSV_V1),
    )
    service, transport = _service(tmp_path, routes)

    outcome = service.monitor(SOURCE_ID)

    assert len(outcome.documents) == 1
    assert outcome.documents[0].url == "https://ir.example.co.jp/ir/a.csv"
    assert transport.calls == [LISTING_RESOURCE, "/ir/a.csv"]
    assert outcome.timeline_entries == 1


# ------------------------------------------------------------------- B9 / B10


def test_document_http_error_is_noted_and_never_content_verified(tmp_path: Path) -> None:
    """B9: a failed document GET leaves no content hash and no timeline entry."""

    routes = _routes(
        listing=(200, "text/html; charset=utf-8", _listing("/ir/a.csv", "/ir/b.csv")),
        ir__a_csv=(500, "text/html", b"boom"),
        ir__b_csv=(200, "text/csv", _CSV_V1),
    )
    service, _ = _service(tmp_path, routes, max_documents_per_run=4)

    outcome = service.monitor(SOURCE_ID)

    failed = [document for document in outcome.documents if document.url.endswith("/a.csv")]
    assert len(failed) == 1
    assert failed[0].fetched is False
    assert failed[0].sha256 is None
    assert any("document fetch HTTP 500" in note for note in outcome.notes)
    assert any("not fetched" in note for note in failed[0].notes)
    stored = _fingerprint_lines(tmp_path)
    assert {"url": failed[0].url, "sha256": "", "label": failed[0].label} in stored
    timeline_urls = {entry["provenance"]["source_url"] for entry in service.timeline(SYMBOL)}
    assert failed[0].url not in timeline_urls


def test_zero_fetched_documents_is_never_a_verified_success(tmp_path: Path) -> None:
    """B10: a run that retrieved no document bytes must not read as "no change".

    Every document GET fails, so the run has zero fetched documents, zero
    timeline writes and no content hash. The next (healthy) run must classify
    the URLs as ``verified`` (first content observation) rather than
    ``unchanged`` - a zero-byte run can never prove that a document is
    unchanged.
    """

    routes = _routes(
        listing=(200, "text/html; charset=utf-8", _listing("/ir/a.csv", "/ir/b.csv")),
        ir__a_csv=(500, "text/html", b"boom"),
        ir__b_csv=(500, "text/html", b"boom"),
    )
    service, _ = _service(tmp_path, routes, max_documents_per_run=4)

    outcome = service.monitor(SOURCE_ID)

    assert _fetched(outcome) == []
    assert outcome.timeline_entries == 0
    assert service.timeline(SYMBOL) == []
    assert sum(1 for note in outcome.notes if "document fetch HTTP 500" in note) == 2
    assert all(document.sha256 is None for document in outcome.documents)
    assert all(not document.kpis for document in outcome.documents)
    assert all(line["sha256"] == "" for line in _fingerprint_lines(tmp_path))

    # the provider recovers: the same URLs are a FIRST content observation
    routes["/ir/a.csv"] = (200, "text/csv", _CSV_V1)
    routes["/ir/b.csv"] = (200, "text/csv", _CSV_V1)
    recovered = service.monitor(SOURCE_ID)

    assert _statuses(recovered) == {"a.csv": "verified", "b.csv": "verified"}
    assert recovered.unchanged_count == 0
    assert recovered.seen_count == 0
    assert recovered.timeline_entries == 2


def test_zero_fetched_top_level_state_documents_defect(tmp_path: Path) -> None:
    """B11: a run with zero retrieved documents must not report ``ok`` (F1).

    This started as a defect pin: ``fetch_state`` was unconditionally ``OK``
    and ``new_count`` counted URL-only records, so a consumer reading only the
    top-level fields could not distinguish "two new documents were retrieved"
    from "every document fetch failed". The service now derives
    ``fetch_state`` from what was actually retrieved and reports
    ``fetched_count`` / ``failed_fetch_count``, so the assertion is inverted
    here (not deleted) to lock the corrected behaviour in.
    """

    routes = _routes(
        listing=(200, "text/html; charset=utf-8", _listing("/ir/a.csv", "/ir/b.csv")),
        ir__a_csv=(500, "text/html", b"boom"),
        ir__b_csv=(500, "text/html", b"boom"),
    )
    service, _ = _service(tmp_path, routes, max_documents_per_run=4)

    outcome = service.monitor(SOURCE_ID)

    # inverted: zero retrieved bytes is an explicit retrieval failure
    assert outcome.fetch_state is AcquisitionFetchState.FAILED
    assert outcome.fetch_state is not AcquisitionFetchState.OK
    # the URL-level detections are still counted, but the summary now says how
    # many bytes were actually retrieved and how many attempts failed.
    assert outcome.new_count == 2
    assert outcome.fetched_count == 0
    assert outcome.failed_fetch_count == 2
    assert any("no document could be retrieved" in note for note in outcome.notes)
    assert _fetched(outcome) == []
    assert outcome.timeline_entries == 0


# ----------------------------------------------------------------- B12 / B13 / B14


def test_document_transport_exception_does_not_abort_the_run(tmp_path: Path) -> None:
    """B12: one raised transport error is noted; the remaining documents run."""

    routes = _routes(
        listing=(200, "text/html; charset=utf-8", _listing("/ir/a.csv", "/ir/b.csv")),
        ir__a_csv=PrivateAcquisitionError(AcquisitionFetchState.FAILED, "connection reset"),
        ir__b_csv=(200, "text/csv", _CSV_V1),
    )
    service, _ = _service(tmp_path, routes, max_documents_per_run=4)

    outcome = service.monitor(SOURCE_ID)

    assert _statuses(outcome) == {"a.csv": "new", "b.csv": "new"}
    assert "connection reset" in " ".join(outcome.notes)
    assert "/a.csv" in " ".join(outcome.notes)
    assert outcome.timeline_entries == 1
    assert [entry["provenance"]["source_url"] for entry in service.timeline(SYMBOL)] == [
        "https://ir.example.co.jp/ir/b.csv"
    ]


def test_oversized_document_is_skipped_with_note(tmp_path: Path) -> None:
    """B13: a document above the byte cap is skipped, noted, and not hashed."""

    routes = _routes(
        listing=(200, "text/html; charset=utf-8", _listing("/ir/big.csv")),
        ir__big_csv=(200, "text/csv", b"x" * 10_000_001),
    )
    service, _ = _service(tmp_path, routes, max_documents_per_run=4)

    outcome = service.monitor(SOURCE_ID)

    assert _fetched(outcome) == []
    assert any("too large" in note for note in outcome.notes)
    assert outcome.timeline_entries == 0
    assert all(line["sha256"] == "" for line in _fingerprint_lines(tmp_path))


def test_unparsable_document_fails_closed_with_empty_kpis(tmp_path: Path) -> None:
    """B14: a document that cannot be parsed reports parsed=False and no KPIs."""

    routes = _routes(
        listing=(200, "text/html; charset=utf-8", _listing("/ir/broken.pdf")),
        ir__broken_pdf=(200, "application/pdf", b"this is not a pdf"),
    )
    service, _ = _service(tmp_path, routes, max_documents_per_run=4)

    outcome = service.monitor(SOURCE_ID)

    record = outcome.documents[0]
    assert record.fetched is True
    assert record.format == "pdf"
    assert record.parsed is False
    assert record.parse_note
    assert record.kpis == []
    assert record.kpi_diff == []
    assert any("pdf" in note for note in record.notes)


# ----------------------------------------------------------------- B15 / B16 / B17


def test_fetch_budget_one_fetches_exactly_one_document(tmp_path: Path) -> None:
    """B15: the run budget caps fetches; the remainder stays URL-only."""

    listing = _listing("/ir/a.csv", "/ir/b.csv", "/ir/c.csv")
    routes = _routes(
        listing=(200, "text/html; charset=utf-8", listing),
        ir__a_csv=(200, "text/csv", _CSV_V1),
        ir__b_csv=(200, "text/csv", _CSV_V1),
        ir__c_csv=(200, "text/csv", _CSV_V1),
    )
    service, transport = _service(tmp_path, routes, max_documents_per_run=1)

    outcome = service.monitor(SOURCE_ID)

    assert len(_fetched(outcome)) == 1
    assert transport.calls[1:] == ["/ir/a.csv"]
    assert outcome.timeline_entries == 1
    unfetched = [document for document in outcome.documents if not document.fetched]
    assert len(unfetched) == 2
    assert all("not fetched" in " ".join(document.notes) for document in unfetched)


def test_fetch_budget_keeps_new_count_and_fetched_count_distinct(tmp_path: Path) -> None:
    """B23: URL-only new detections are never reported as retrievals (F1)."""

    routes = _routes(
        listing=(
            200,
            "text/html; charset=utf-8",
            _listing("/ir/a.csv", "/ir/b.csv", "/ir/c.csv"),
        ),
        ir__a_csv=(200, "text/csv", _CSV_V1),
        ir__b_csv=(200, "text/csv", _CSV_V1),
        ir__c_csv=(200, "text/csv", _CSV_V1),
    )
    service, _ = _service(tmp_path, routes, max_documents_per_run=1)

    outcome = service.monitor(SOURCE_ID)

    # three documents are new by URL identity, one byte stream was retrieved
    assert outcome.new_count == 3
    assert outcome.fetched_count == 1
    assert outcome.failed_fetch_count == 0
    assert outcome.fetch_state is AcquisitionFetchState.OK


def test_partial_document_failure_is_counted_not_hidden(tmp_path: Path) -> None:
    """B22: a partial run stays usable but the failure is a summary field."""

    routes = _routes(
        listing=(200, "text/html; charset=utf-8", _listing("/ir/a.csv", "/ir/b.csv")),
        ir__a_csv=(500, "text/html", b"boom"),
        ir__b_csv=(200, "text/csv", _CSV_V1),
    )
    service, _ = _service(tmp_path, routes, max_documents_per_run=4)

    outcome = service.monitor(SOURCE_ID)

    # one document was retrieved, so the run is not a failure...
    assert outcome.fetch_state is AcquisitionFetchState.OK
    assert outcome.fetched_count == 1
    # ...but the failed attempt is visible without reading every record
    assert outcome.failed_fetch_count == 1
    assert outcome.timeline_entries == 1
    assert any("document fetch HTTP 500" in note for note in outcome.notes)


def test_non_positive_fetch_budget_is_normalised_not_bypassed(tmp_path: Path) -> None:
    """B24: ``max_documents_per_run <= 0`` is normalised, never a broken bound.

    A zero budget used to fetch ``max(1, 0 // 2) == 1`` document - past the
    caller's own limit - and a negative budget made ``reverify_pool[:-N]``
    silently drop the trailing re-verification candidates. Both are normalised
    to the module default instead.
    """

    routes = _routes(
        listing=(
            200,
            "text/html; charset=utf-8",
            _listing("/ir/a.csv", "/ir/b.csv", "/ir/c.csv"),
        ),
        ir__a_csv=(200, "text/csv", _CSV_V1),
        ir__b_csv=(200, "text/csv", _CSV_V1),
        ir__c_csv=(200, "text/csv", _CSV_V1),
    )
    zero_budget, _ = _service(tmp_path, routes, max_documents_per_run=0)
    first = zero_budget.monitor(SOURCE_ID)

    # every available document is fetched: the bound is the default, not zero
    assert first.fetched_count == 3
    assert first.failed_fetch_count == 0
    assert first.fetch_state is AcquisitionFetchState.OK
    assert first.timeline_entries == 3

    # negative budget re-verifies all three, nothing is sliced away
    negative_budget, _ = _service(tmp_path, routes, max_documents_per_run=-1)
    second = negative_budget.monitor(SOURCE_ID)

    assert second.fetched_count == 3
    assert second.failed_fetch_count == 0
    assert second.unchanged_count == 3
    assert second.fetch_state is AcquisitionFetchState.OK


def test_identical_rerun_reports_unchanged_and_writes_no_timeline(tmp_path: Path) -> None:
    """B16: a second identical run is unchanged and appends nothing."""

    routes = _routes(
        listing=(200, "text/html; charset=utf-8", _listing("/ir/a.csv")),
        ir__a_csv=(200, "text/csv", _CSV_V1),
    )
    service, _ = _service(tmp_path, routes, max_documents_per_run=4)

    first = service.monitor(SOURCE_ID)
    second = service.monitor(SOURCE_ID)

    assert first.new_count == 1
    assert first.timeline_entries == 1
    assert second.new_count == 0
    assert second.unchanged_count == 1
    assert second.timeline_entries == 0
    assert len(service.timeline(SYMBOL)) == 1
    assert service.timeline(SYMBOL)[0]["payload"]["status"] == "new"


def test_changed_bytes_at_known_url_are_revised_with_kpi_diff(tmp_path: Path) -> None:
    """B17: same URL, different bytes -> revised, KPI diff, timeline write."""

    routes = _routes(
        listing=(200, "text/html; charset=utf-8", _listing("/ir/a.csv")),
        ir__a_csv=(200, "text/csv", _CSV_V1),
    )
    service, _ = _service(tmp_path, routes, max_documents_per_run=4)
    service.monitor(SOURCE_ID)

    routes["/ir/a.csv"] = (200, "text/csv", _CSV_V2)
    revised = service.monitor(SOURCE_ID)

    assert _statuses(revised) == {"a.csv": "revised"}
    assert revised.revised_count == 1
    assert revised.timeline_entries == 1
    record = revised.documents[0]
    assert record.sha256
    backlog = [item for item in record.kpi_diff if item["kpi"] == "order_backlog"]
    assert backlog and backlog[0]["change"] == "decrease"
    assert backlog[0]["previous_value"] == 1200.0
    assert backlog[0]["current_value"] == 900.0


# ------------------------------------------------------------ B18 / B19 / B20


def test_corrupt_fingerprint_lines_are_skipped(tmp_path: Path) -> None:
    """B18: an unreadable fingerprint line must not abort the next run."""

    store_dir = tmp_path / "ir-fingerprints" / SOURCE_ID
    store_dir.mkdir(parents=True)
    (store_dir / "fingerprints.jsonl").write_text(
        "{not json\n" + json.dumps({"url": "https://stale.example/x.pdf", "sha256": ""}) + "\n",
        encoding="utf-8",
    )
    routes = _routes(
        listing=(200, "text/html; charset=utf-8", _listing("/ir/a.csv")),
        ir__a_csv=(200, "text/csv", _CSV_V1),
    )
    service, _ = _service(tmp_path, routes, max_documents_per_run=4)

    outcome = service.monitor(SOURCE_ID)

    assert _statuses(outcome) == {"a.csv": "new"}
    assert outcome.fetch_state is AcquisitionFetchState.OK
    assert outcome.timeline_entries == 1


def test_corrupt_fingerprint_line_count_lands_in_notes(tmp_path: Path) -> None:
    """B18b: the skipped-line count, row number and re-fetch risk are noted."""

    store_dir = tmp_path / "ir-fingerprints" / SOURCE_ID
    store_dir.mkdir(parents=True)
    known_line = json.dumps({"url": "https://ir.example.co.jp/ir/old.csv", "sha256": "a" * 64})
    (store_dir / "fingerprints.jsonl").write_text(
        known_line + "\n{not json\n",
        encoding="utf-8",
    )
    routes = _routes(
        listing=(200, "text/html; charset=utf-8", _listing("/ir/a.csv")),
        ir__a_csv=(200, "text/csv", _CSV_V1),
    )
    service, _ = _service(tmp_path, routes, max_documents_per_run=4)

    outcome = service.monitor(SOURCE_ID)

    corrupt_notes = [note for note in outcome.notes if "corrupt line(s) skipped" in note]
    assert len(corrupt_notes) == 1
    assert "1 corrupt line(s)" in corrupt_notes[0]
    assert "rows [2]" in corrupt_notes[0]
    assert "duplicate alerts" in corrupt_notes[0]
    # The skipped row is treated as unseen, never as a failure of the run.
    assert outcome.fetch_state is AcquisitionFetchState.OK


def test_clean_fingerprint_reads_add_no_corrupt_note(tmp_path: Path) -> None:
    """B18b: a clean read (file absent, then file valid) adds no corrupt note."""

    routes = _routes(
        listing=(200, "text/html; charset=utf-8", _listing("/ir/a.csv")),
        ir__a_csv=(200, "text/csv", _CSV_V1),
    )
    service, _ = _service(tmp_path, routes, max_documents_per_run=4)

    first = service.monitor(SOURCE_ID)
    second = service.monitor(SOURCE_ID)

    for outcome in (first, second):
        assert all("corrupt line(s) skipped" not in note for note in outcome.notes)


def test_corrupt_registry_json_documents_defect(tmp_path: Path) -> None:
    """B19: an unreadable ``sources.json`` fails closed instead of "no sources".

    ``IrSourceRegistryStore.load`` used to swallow ``JSONDecodeError``, so a
    truncated or hand-edited registry dropped every operator-registered company
    with no note anywhere and the monitor reported "unknown IR source". It now
    raises ``IrSourceRegistryError``: an empty source list is never reported,
    the file is never overwritten, and the error carries the recovery step.
    """

    sources_dir = tmp_path / "ir-sources"
    sources_dir.mkdir(parents=True)
    path = sources_dir / "sources.json"
    path.write_text("{not valid json", encoding="utf-8")
    sha_before = hashlib.sha256(path.read_bytes()).hexdigest()

    service = IrMonitorService(
        data_dir=tmp_path, transport_factory=lambda source: _StubTransport({})
    )

    # (a) a corrupt registry is never presented as a normal, empty registry
    with pytest.raises(IrSourceRegistryError):
        service.list_sources()
    with pytest.raises(IrSourceRegistryError):
        service.get_source(SOURCE_ID)

    outcome = service.monitor(SOURCE_ID)
    assert outcome.fetch_state is AcquisitionFetchState.FAILED
    assert outcome.notes[0].startswith("IR source registry unusable")
    assert f"unknown IR source: {SOURCE_ID}" in outcome.notes

    # (c) the recovery procedure is recorded on the error the caller sees
    assert service.registry_error is not None
    assert "restore the file from backup" in str(service.registry_error)
    assert f"{path}.corrupt" in str(service.registry_error)

    # (b) the corrupt file is left byte-identical: not rewritten, not deleted
    assert hashlib.sha256(path.read_bytes()).hexdigest() == sha_before


def test_corrupt_registry_is_never_overwritten(tmp_path: Path) -> None:
    """A payload must never be persisted over a registry that cannot be read."""

    sources_dir = tmp_path / "ir-sources"
    sources_dir.mkdir(parents=True)
    path = sources_dir / "sources.json"
    path.write_text("{not valid json", encoding="utf-8")
    before = path.read_bytes()
    definition = IrSourceDefinition(
        source_id=SOURCE_ID,
        symbol=SYMBOL,
        provider="ir.example.co.jp",
        listing_url=LISTING_URL,
    )

    service = IrMonitorService(
        data_dir=tmp_path,
        transport_factory=lambda source: _StubTransport({}),
        sources=[definition],
    )

    assert service.registry_error is not None
    # The explicitly supplied source still works in memory...
    assert service.get_source(SOURCE_ID) == definition
    assert service.list_sources() == [definition]
    # ...but nothing may be written over the unreadable file.
    with pytest.raises(IrSourceRegistryError):
        service.add_source(definition)
    assert path.read_bytes() == before


def test_registry_store_refuses_to_save_over_unreadable_file(tmp_path: Path) -> None:
    root = tmp_path / "ir-sources"
    root.mkdir(parents=True)
    store = IrSourceRegistryStore(root=root)
    store.path.write_text('[{"source_id": ', encoding="utf-8")
    before = store.path.read_bytes()

    with pytest.raises(IrSourceRegistryError):
        store.save(
            [
                IrSourceDefinition(
                    source_id=SOURCE_ID,
                    symbol=SYMBOL,
                    provider="ir.example.co.jp",
                    listing_url=LISTING_URL,
                )
            ]
        )

    assert store.path.read_bytes() == before


def test_registry_store_refuses_to_overwrite_invalid_entries(tmp_path: Path) -> None:
    """Entries that no longer validate block the write instead of vanishing."""

    root = tmp_path / "ir-sources"
    root.mkdir(parents=True)
    store = IrSourceRegistryStore(root=root)
    definition = IrSourceDefinition(
        source_id=SOURCE_ID,
        symbol=SYMBOL,
        provider="ir.example.co.jp",
        listing_url=LISTING_URL,
    )
    store.path.write_text(
        json.dumps([{"source_id": "!!invalid!!"}, definition.model_dump(mode="json")]),
        encoding="utf-8",
    )

    assert [item.source_id for item in store.load()] == [SOURCE_ID]
    with pytest.raises(IrSourceRegistryError):
        store.save([definition])
    assert "!!invalid!!" in store.path.read_text(encoding="utf-8")


def test_registry_with_invalid_entries_is_an_explicit_error(tmp_path: Path) -> None:
    """A registry whose entries no longer validate is not a partial success."""

    root = tmp_path / "ir-sources"
    root.mkdir(parents=True)
    (root / "sources.json").write_text(json.dumps([{"source_id": "!!invalid!!"}]), encoding="utf-8")

    service = IrMonitorService(
        data_dir=tmp_path, transport_factory=lambda source: _StubTransport({})
    )

    assert service.registry_error is not None
    with pytest.raises(IrSourceRegistryError):
        service.list_sources()
    outcome = service.monitor(SOURCE_ID)
    assert outcome.notes[0].startswith("IR source registry unusable")


def test_healthy_registry_still_round_trips(tmp_path: Path) -> None:
    """Normal behaviour is unchanged: a readable registry persists and reloads."""

    definition = IrSourceDefinition(
        source_id=SOURCE_ID,
        symbol=SYMBOL,
        provider="ir.example.co.jp",
        listing_url=LISTING_URL,
    )
    IrMonitorService(
        data_dir=tmp_path,
        transport_factory=lambda source: _StubTransport({}),
        sources=[definition],
    )

    reloaded = IrMonitorService(
        data_dir=tmp_path, transport_factory=lambda source: _StubTransport({})
    )

    assert reloaded.registry_error is None
    assert [item.source_id for item in reloaded.list_sources()] == [SOURCE_ID]
    assert reloaded.get_source(SOURCE_ID) == definition


def test_transport_factory_exception_documents_defect(tmp_path: Path) -> None:
    """B20: a failing transport factory is fail-closed, not an escaping raise.

    Every fetch path is documented as fail-closed. Connector/browser-session
    construction used to sit outside the ``PrivateAcquisitionError`` guard, so a
    construction error raised out of ``monitor()`` (HTTP 500 at the API layer).
    The factory call is now guarded, so the run returns FAILED with a note that
    carries the reconnect/rebuild recovery procedure. This test keeps its F3
    name and inverts the pin: same scenario, the expected contract instead of
    the defect.
    """

    def _broken_factory(source: IrSourceDefinition) -> Any:
        raise RuntimeError("browser session could not be created")

    service = IrMonitorService(
        data_dir=tmp_path,
        transport_factory=_broken_factory,
        sources=[
            IrSourceDefinition(
                source_id=SOURCE_ID,
                symbol=SYMBOL,
                provider="ir.example.co.jp",
                listing_url=LISTING_URL,
            )
        ],
    )

    outcome = service.monitor(SOURCE_ID)

    assert outcome.fetch_state is AcquisitionFetchState.FAILED
    assert outcome.documents == []
    assert outcome.fetched_count == 0
    assert outcome.new_count == 0
    assert outcome.network == []
    note = " ".join(outcome.notes)
    assert "transport unavailable for source example-ir" in note
    assert "browser session could not be created" in note
    assert "Recovery:" in note
    _assert_no_run_storage(tmp_path)


def test_transport_factory_failure_mid_run_is_noted_not_raised(tmp_path: Path) -> None:
    """B25: a factory that starts failing mid-run still cannot escape monitor().

    The listing succeeds (first factory call), then session construction breaks
    (second call). The run must complete with a per-document failure note and a
    FAILED top-level state -- never a raise, never a fabricated success.
    """

    transport = _StubTransport(
        _routes(
            listing=(200, "text/html; charset=utf-8", _LISTING_HTML),
            ir__a_csv=(200, "text/csv", _CSV_V1),
        )
    )
    calls: list[str] = []

    def _flaky_factory(source: IrSourceDefinition) -> Any:
        calls.append(source.source_id)
        if len(calls) > 1:
            raise RuntimeError("browser session expired mid-run")
        return transport

    service = IrMonitorService(
        data_dir=tmp_path,
        transport_factory=_flaky_factory,
        sources=[
            IrSourceDefinition(
                source_id=SOURCE_ID,
                symbol=SYMBOL,
                provider="ir.example.co.jp",
                listing_url=LISTING_URL,
            )
        ],
    )

    outcome = service.monitor(SOURCE_ID)

    assert outcome.fetch_state is AcquisitionFetchState.FAILED
    assert outcome.fetched_count == 0
    assert outcome.failed_fetch_count == 1
    joined = " ".join(outcome.notes)
    assert "document fetch failed" in joined
    assert "browser session expired mid-run" in joined
    assert "Recovery:" in joined
    assert all(document.fetched is False for document in outcome.documents)
    # Only the listing request reached the transport; the document never did.
    assert transport.calls == [LISTING_RESOURCE]


# ---------------------------------------------------------------------- B21


def test_kpi_history_read_for_unknown_url_creates_no_directories(tmp_path: Path) -> None:
    """B21: a read for an unobserved document returns [] without touching disk."""

    service, _ = _service(tmp_path, {})

    assert service.document_kpi_history("https://ir.example.co.jp/ir/never-seen.pdf") == []
    assert not (tmp_path / "ir-kpi-history").exists()
