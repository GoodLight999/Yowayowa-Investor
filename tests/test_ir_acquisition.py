"""Regression tests for the P1C IR acquisition pipeline.

Covers the task's acceptance boundaries without network access:

- new-document detection idempotency (second run reports zero new),
- revised detection when a known URL's bytes change,
- verified vs revised distinction (first content observation is not a revision),
- previous-version KPI diff (increase / decrease / sign-flip revision / added),
- provenance kept through normalization and failing closed when missing,
- document extraction fail-closed paths (image-only PDF, no pdfminer, xlsx),
- KPI unit handling (a table unit declaration multiplies the raw number).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from yowayowa.acquisition.discovery import (
    DiscoveredDocument,
    FingerprintStore,
    classify_documents,
    discover_ir_documents,
)
from yowayowa.acquisition.documents import (
    extract_document,
    extract_html_tables,
    extract_xlsx_tables,
)
from yowayowa.acquisition.ir import (
    IrTimelineStore,
    canonical_kpi_name,
    document_unit_hint,
    extract_kpis_from_tables,
    extract_kpis_from_text,
    merge_kpi_observations,
    parse_number,
    parse_yen_chain,
    table_unit_hint,
    timeline_entry,
)
from yowayowa.acquisition.transport import PrivateAcquisitionError, TransportResponse
from yowayowa.domain import LicenseClass
from yowayowa.services.ir_monitor_service import (
    IrMonitorService,
    IrSourceDefinition,
)

LISTING_URL = "https://ir.example.co.jp/library/briefing.html"

_LISTING_HTML = """
<html><body>
  <a href="/ir/items/fy2026_1q_tanshin.pdf">第1四半期 決算短信[621.2 KB]</a>
  <a href="/ir/items/fy2026_1q_factbook.xlsx">FACTBOOK[120.0 KB]</a>
  <a href="/ir/items/fy2026_1q_summary.csv">決算サマリー[4.0 KB]</a>
  <a href="/ir/library/briefing.html">IR資料室</a>
  <a href="https://other.example.com/ir/items/external.pdf">他社</a>
  <a href="#top">先頭へ</a>
</body></html>
"""


class FakeTransport:
    """In-memory transport: resource path -> (status, content_type, bytes)."""

    def __init__(self, routes: dict[str, tuple[int, str | None, bytes]]) -> None:
        self.routes = routes

    def fetch(
        self,
        method: str,
        resource: str,
        *,
        params: Any = None,
        headers: Any = None,
        data: Any = None,
    ) -> TransportResponse:
        entry = self.routes.get(resource)
        if entry is None:
            raise PrivateAcquisitionError(
                state=__import__(
                    "yowayowa.acquisition.models", fromlist=["AcquisitionFetchState"]
                ).AcquisitionFetchState.FAILED,
                reason=f"unrouted resource: {resource}",
            )
        status, content_type, body = entry
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            text = body.decode("utf-8", errors="replace")
        return TransportResponse(
            status_code=status,
            url=f"https://ir.example.co.jp{resource}",
            content_type=content_type,
            text=text,
            content=body,
            elapsed_ms=1.0,
        )


def _service(tmp_path: Path, routes: dict[str, tuple[int, str | None, bytes]], **kwargs: Any):
    return IrMonitorService(
        data_dir=tmp_path,
        transport_factory=lambda source: FakeTransport(routes),
        sources=[
            IrSourceDefinition(
                source_id="example-ir",
                symbol="1234.T",
                provider="ir.example.co.jp",
                listing_url=LISTING_URL,
                license_class=LicenseClass.OFFICIAL_PUBLIC.value,
            )
        ],
        **kwargs,
    )


_CSV = "項目,値\n受注残,1200\n契約数,340\n".encode()
_HTML_TABLE = (
    "<html><body><table>"
    "<tr><th>KPI</th><th>当期</th></tr>"
    "<tr><td>受注残</td><td>1,200</td></tr>"
    "</table></body></html>"
).encode()


# --------------------------------------------------------------- discovery


def test_discover_keeps_only_same_origin_documents() -> None:
    documents, notes = discover_ir_documents(_LISTING_HTML, listing_url=LISTING_URL)
    urls = [document.url for document in documents]
    assert "https://ir.example.co.jp/ir/items/fy2026_1q_tanshin.pdf" in urls
    assert "https://ir.example.co.jp/ir/items/fy2026_1q_factbook.xlsx" in urls
    assert "https://ir.example.co.jp/ir/items/fy2026_1q_summary.csv" in urls
    # HTML pages are not documents and off-origin links are skipped
    assert all(not url.endswith(".html") for url in urls)
    assert all("other.example.com" not in url for url in urls)
    assert any("skipped" in note for note in notes)


def test_classify_is_idempotent_without_content(tmp_path: Path) -> None:
    store = FingerprintStore(root=tmp_path)
    documents = [
        DiscoveredDocument(url="https://a/1.pdf", label="one", kind="document"),
        DiscoveredDocument(url="https://a/2.pdf", label="two", kind="document"),
    ]
    first = classify_documents(documents, {}, store.known_keys("s"))
    assert [item["status"] for item in first] == ["new", "new"]

    store.record(
        "s",
        [
            {"url": "https://a/1.pdf", "sha256": "", "label": "one"},
            {"url": "https://a/2.pdf", "sha256": "", "label": "two"},
        ],
    )
    second = classify_documents(documents, {}, store.known_keys("s"))
    assert [item["status"] for item in second] == ["seen", "seen"]


def test_classify_distinguishes_revised_verified_unchanged(tmp_path: Path) -> None:
    url = "https://a/1.pdf"
    documents = [DiscoveredDocument(url=url, label="one", kind="document")]
    # first content observation on a URL recorded URL-only -> verified
    store = FingerprintStore(root=tmp_path)
    store.record("s", [{"url": url, "sha256": "", "label": "one"}])
    verified = classify_documents(
        documents,
        {url: {"url": url, "sha256": "a" * 64, "label": "one"}},
        store.known_keys("s"),
        known_content_urls=store.known_content_urls("s"),
    )
    assert verified[0]["status"] == "verified"

    # same content again -> unchanged
    store.record("s", [{"url": url, "sha256": "a" * 64, "label": "one"}])
    unchanged = classify_documents(
        documents,
        {url: {"url": url, "sha256": "a" * 64, "label": "one"}},
        store.known_keys("s"),
        known_content_urls=store.known_content_urls("s"),
    )
    assert unchanged[0]["status"] == "unchanged"

    # content changed -> revised
    revised = classify_documents(
        documents,
        {url: {"url": url, "sha256": "b" * 64, "label": "one"}},
        store.known_keys("s"),
        known_content_urls=store.known_content_urls("s"),
    )
    assert revised[0]["status"] == "revised"


# -------------------------------------------------------------- extraction


def test_html_table_extraction_parses_rows() -> None:
    extracted = extract_html_tables(_HTML_TABLE.decode())
    assert extracted.parsed
    assert extracted.tables[0]["rows"][0]["KPI"] == "受注残"


def test_xlsx_extraction_reads_shared_strings(tmp_path: Path) -> None:
    # minimal OOXML workbook: shared string + one sheet, one data row
    import zipfile

    buffer = tmp_path / "book.xlsx"
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "xl/workbook.xml",
            '<workbook><sheets><sheet name="KPI"/></sheets></workbook>',
        )
        archive.writestr(
            "xl/sharedStrings.xml",
            "<sst><si><t>受注残</t></si><si><t>1200</t></si></sst>",
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            "<worksheet><sheetData>"
            "<row r='1'><c r='A1' t='s'><v>0</v></c><c r='B1' t='s'><v>1</v></c></row>"
            "</sheetData></worksheet>",
        )
    extracted = extract_xlsx_tables(buffer.read_bytes())
    assert extracted.parsed
    assert extracted.tables[0]["name"] == "KPI"
    assert extracted.tables[0]["headers"] == ["受注残", "1200"]


def test_extract_document_fails_closed_for_unknown_format() -> None:
    extracted = extract_document(content=b"\x00\x01binary", content_type=None, filename="data.bin")
    assert not extracted.parsed
    assert extracted.parse_note is not None


def test_extract_document_pdf_without_text_layer_fails_closed() -> None:
    extracted = extract_document(
        content=b"%PDF-1.4 not really a pdf",
        content_type="application/pdf",
        filename="doc.pdf",
    )
    # pdfminer may be absent (extra not installed) or the bytes unparsable:
    # both must be explicit non-parsed results, never a silent empty success.
    assert not extracted.parsed
    assert extracted.parse_note


def test_extract_pdf_with_whitespace_only_text_layer_fails_closed() -> None:
    """An image-only PDF yields a form-feed-only text layer, not zero bytes.

    Treating that as parsed=True would report "no KPIs found" for a document
    that is simply not machine readable, which is a different fact.
    """
    pytest.importorskip("pdfminer")
    pdf = _image_only_pdf(page_count=3)
    extracted = extract_document(content=pdf, content_type="application/pdf", filename="scan.pdf")
    assert not extracted.parsed
    assert extracted.parse_note
    assert "text layer" in extracted.parse_note


def _image_only_pdf(page_count: int) -> bytes:
    """A PDF whose pages contain no text operators at all (scanned image).

    pdfminer still emits one form-feed per page, so the resulting text layer is
    non-empty but whitespace-only.
    """

    kids = " ".join(f"{4 + index} 0 R" for index in range(page_count))
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        (f"<< /Type /Pages /Kids [{kids}] /Count {page_count} >>".encode()),
    ]
    for _ in range(page_count):
        objects.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>")

    buffer = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(buffer))
        buffer += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_offset = len(buffer)
    buffer += f"xref\n0 {len(objects) + 1}\n".encode()
    buffer += b"0000000000 65535 f \n"
    for offset in offsets:
        buffer += f"{offset:010d} 00000 n \n".encode()
    buffer += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n"
    ).encode()
    return bytes(buffer)


def _tiny_pdf(lines: list[tuple[float, float, str]]) -> bytes:
    """Build a one-page PDF with text placed at explicit coordinates.

    Hand-rolled so the pdfminer layout-table path can be exercised without
    binary fixtures: columns are separated by x so the coordinate-based
    table reconstruction sees distinct cells.
    """

    text_ops = ["BT /F1 10 Tf"]
    for x, y, value in lines:
        escaped = value.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        text_ops.append(f"1 0 0 1 {x} {y} Tm ({escaped}) Tj")
    text_ops.append("ET")
    stream = "\n".join(text_ops).encode("latin-1")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]

    buffer = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(buffer))
        buffer += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_offset = len(buffer)
    buffer += f"xref\n0 {len(objects) + 1}\n".encode()
    buffer += b"0000000000 65535 f \n"
    for offset in offsets:
        buffer += f"{offset:010d} 00000 n \n".encode()
    buffer += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n"
    ).encode()
    return bytes(buffer)


def test_extract_pdf_text_and_layout_table_with_real_parser() -> None:
    """The pdfminer path must yield both linear text and coordinate tables."""
    pytest.importorskip("pdfminer")
    pdf = _tiny_pdf(
        [
            (72, 720, "FY2026 3Q Consolidated Financial Summary"),
            (72, 690, "Revenue"),
            (280, 690, "912,248"),
            (72, 660, "Operating profit"),
            (280, 660, "125,526"),
        ]
    )
    extracted = extract_document(
        content=pdf, content_type="application/pdf", filename="summary.pdf"
    )
    assert extracted.parsed, extracted.parse_note
    assert extracted.format == "pdf"
    assert "912,248" in (extracted.text or "")
    # coordinate reconstruction puts label and value in the same row
    flat = [cell for table in extracted.tables for row in table.get("cells", []) for cell in row]
    assert any("Revenue" in cell for cell in flat)
    assert any("912,248" in cell for cell in flat)


def test_extract_pdf_kpis_with_table_unit_hint() -> None:
    """A unit declared in table chrome must multiply the extracted KPI."""
    pytest.importorskip("pdfminer")
    pdf = _tiny_pdf(
        [
            (72, 730, "(million)"),
            (72, 700, "Revenue"),
            (200, 700, "800,000"),
            (330, 700, "912,248"),
        ]
    )
    extracted = extract_document(
        content=pdf, content_type="application/pdf", filename="summary.pdf"
    )
    assert extracted.parsed, extracted.parse_note
    unit = table_unit_hint(extracted.tables[0]) if extracted.tables else None
    assert unit == "million"
    kpis, _ = extract_kpis_from_tables(extracted.tables)
    revenue = next(item for item in kpis if item["kpi"] == "revenue")
    # current-period cell (2nd numeric column) times the table's unit
    assert revenue["raw_value"] == "912,248"
    assert revenue["value"] == pytest.approx(912_248_000_000.0)


# --------------------------------------------------------------------- KPI


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("912,248", 912248.0),
        ("\u25b316,580", -16580.0),  # triangle minus
        ("\u22129.5", -9.5),  # unicode minus
        ("1,200", 1200.0),
        ("abc", None),
        ("", None),
    ],
)
def test_parse_number_variants(raw: str, expected: float | None) -> None:
    assert parse_number(raw) == expected


def test_canonical_kpi_names_jp_and_en() -> None:
    assert canonical_kpi_name("売上収益") == "revenue"
    assert canonical_kpi_name("営業利益") == "operating_profit"
    assert canonical_kpi_name("受注残") == "order_backlog"
    assert canonical_kpi_name("Order Backlog") == "order_backlog"
    assert canonical_kpi_name("relation to customer") is None


def test_table_unit_hint_and_multiplier() -> None:
    table = {
        "headers": ["株式会社サンプル 2026年3月期 決算短信"],
        "rows": [],
        "cells": [
            ["(\u5358\u4f4d\uff1a\u767e\u4e07\u5186)", "", ""],
            ["売上収益", "928,828", "912,248"],
        ],
    }
    assert table_unit_hint(table) == "百万円"
    observations, _ = extract_kpis_from_tables([table])
    revenue = [item for item in observations if item["kpi"] == "revenue"]
    assert revenue
    # 912,248 million yen -> normalized to yen
    assert revenue[0]["value"] == 912_248_000_000.0
    assert revenue[0]["unit"] == "百万円"


def test_merge_prefers_unit_bearing_observation() -> None:
    merged = merge_kpi_observations(
        [
            {"kpi": "revenue", "value": 912248.0, "unit": None},
            {"kpi": "revenue", "value": 912_248_000_000.0, "unit": "百万円"},
            {"kpi": "operating_profit", "value": 125526.0, "unit": None},
        ]
    )
    by_kpi = {item["kpi"]: item for item in merged}
    assert by_kpi["revenue"]["value"] == 912_248_000_000.0
    assert by_kpi["operating_profit"]["value"] == 125526.0


def test_parse_yen_chain_compound_and_single() -> None:
    # 892億74百万円 must not be read as bare "892" (four orders too small)
    assert parse_yen_chain("892\u510474\u767e\u4e07\u5186の計上") == (
        892 * 100_000_000.0 + 74 * 1_000_000.0,
        "892\u510474\u767e\u4e07\u5186",
    )
    # single-part chain returns the canonical unit token
    assert parse_yen_chain("125,526\u767e\u4e07\u5186") == (125_526_000_000.0, "\u767e\u4e07\u5186")
    # plain yen is not a chain (no scale token)
    assert parse_yen_chain("100\u5186") is None
    assert parse_yen_chain("not a number") is None


def test_text_kpi_consumes_compound_yen_chain() -> None:
    observations, _ = extract_kpis_from_text(
        "資本は、当期利益892\u510474\u767e\u4e07\u5186の計上等により増加した。"
    )
    profit = next(item for item in observations if item["kpi"] == "profit")
    assert profit["value"] == 892 * 100_000_000.0 + 74 * 1_000_000.0


def test_document_unit_hint_applies_only_when_unambiguous() -> None:
    assert document_unit_hint("Consolidated (Millions of yen) statements") == "millions of yen"
    assert document_unit_hint("(\u5358\u4f4d\uff1a\u767e\u4e07\u5186)") == "\u767e\u4e07\u5186"
    # mixed scales -> ambiguous -> no hint (fail closed, never guess)
    assert document_unit_hint("(Millions of yen) and (Thousands of yen)") is None
    assert (
        document_unit_hint("(\u5358\u4f4d\uff1a\u767e\u4e07\u5186) \u4e00\u90e8\u306f\u5343\u5186")
        is None
    )
    # a compound-yen prose token does not create its own unit class
    assert document_unit_hint("当期利益892\u510474\u767e\u4e07\u5186") == "\u767e\u4e07\u5186"
    assert document_unit_hint("no unit here") is None


def test_english_summary_tables_get_document_unit() -> None:
    """English summaries print the unit as a caption, not in the table grid."""
    table = {
        "headers": ["Consolidated Financial Results"],
        "rows": [],
        "cells": [["Net sales", "816,196", "912,248"]],
    }
    text = "Consolidated Financial Results for the Fiscal Year (Millions of yen)"
    observations, _ = extract_kpis_from_tables([table], unit_hint=document_unit_hint(text))
    revenue = next(item for item in observations if item["kpi"] == "revenue")
    assert revenue["value"] == 912_248_000_000.0
    assert revenue["unit"] == "millions of yen"


def test_kpi_text_extraction_skips_period_headers() -> None:
    text = "2026年3月期 売上収益 912,248\n営業利益: 125,526"
    observations, _ = extract_kpis_from_text(text)
    names = {item["kpi"] for item in observations}
    assert "revenue" in names
    assert "operating_profit" in names


# ---------------------------------------------------------------- timeline


def test_timeline_entry_requires_provenance() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValueError):
        timeline_entry(
            kind="document",
            symbol="1234.T",
            provider="",
            source_url="https://a/1.pdf",
            retrieved_at=now,
            as_of=None,
            license_class=LicenseClass.OFFICIAL_PUBLIC.value,
            payload={},
        )
    with pytest.raises(ValueError):
        timeline_entry(
            kind="document",
            symbol="1234.T",
            provider="ir.example.co.jp",
            source_url="",
            retrieved_at=now,
            as_of=None,
            license_class=LicenseClass.OFFICIAL_PUBLIC.value,
            payload={},
        )
    with pytest.raises(ValueError):
        timeline_entry(
            kind="document",
            symbol="1234.T",
            provider="ir.example.co.jp",
            source_url="https://a/1.pdf",
            retrieved_at=now,
            as_of=None,
            license_class="",
            payload={},
        )


def test_timeline_survives_roundtrip_with_provenance(tmp_path: Path) -> None:
    store = IrTimelineStore(root=tmp_path)
    now = datetime.now(UTC)
    entry = timeline_entry(
        kind="document",
        symbol="1234.T",
        provider="ir.example.co.jp",
        source_url="https://ir.example.co.jp/1.pdf",
        retrieved_at=now,
        as_of=now,
        license_class=LicenseClass.OFFICIAL_PUBLIC.value,
        payload={"kpis": [{"kpi": "revenue", "value": 1.0}]},
    )
    store.append("1234.T", entry)
    entries = store.entries("1234.T")
    assert entries[0]["provenance"]["provider"] == "ir.example.co.jp"
    assert entries[0]["provenance"]["license_class"] == "official_public"
    assert entries[0]["provenance"]["source_url"].endswith("/1.pdf")
    assert entries[0]["provenance"]["retrieved_at"]


def test_kpi_history_diff_detects_increase_decrease_and_revision(tmp_path: Path) -> None:
    service = _service(tmp_path, {})
    url = "https://ir.example.co.jp/1.pdf"
    service._kpi_history.append(
        url,
        [
            {"kpi": "revenue", "value": 1000.0},
            {"kpi": "operating_profit", "value": 200.0},
            {"kpi": "order_backlog", "value": -50.0},
            {"kpi": "utilization", "value": 80.0},
        ],
    )
    diffs = service._diff_against_previous(
        url,
        [
            {"kpi": "revenue", "value": 1200.0},  # increase
            {"kpi": "operating_profit", "value": 150.0},  # decrease
            {"kpi": "order_backlog", "value": 30.0},  # sign flip -> revision
            # utilization removed
            {"kpi": "contracts", "value": 5.0},  # added
        ],
    )
    by_kpi = {item["kpi"]: item for item in diffs}
    assert by_kpi["revenue"]["change"] == "increase"
    assert by_kpi["revenue"]["delta"] == 200.0
    assert by_kpi["operating_profit"]["change"] == "decrease"
    assert by_kpi["order_backlog"]["change"] == "revision"
    assert by_kpi["utilization"]["change"] == "removed"
    assert by_kpi["contracts"]["change"] == "added"
    # untouched values produce no diff entries
    assert "eps" not in by_kpi


# --------------------------------------------------------------- pipeline


def _routes(content: bytes | None = None, *, pdf_bytes: bytes | None = None):
    body = _HTML_TABLE if content is None else content
    return {
        "/library/briefing.html": (200, "text/html; charset=utf-8", _LISTING_HTML.encode()),
        "/ir/items/fy2026_1q_tanshin.pdf": (
            200,
            "text/html; charset=utf-8",  # route serves html bytes; extractor
            body,  # dispatches on extension
        ),
        "/ir/items/fy2026_1q_factbook.xlsx": (200, "application/vnd.ms-excel", b"PK\x03\x04bad"),
        "/ir/items/fy2026_1q_summary.csv": (200, "text/csv", _CSV),
    }


def test_monitor_end_to_end_and_idempotent(tmp_path: Path) -> None:
    service = _service(tmp_path, _routes(), max_documents_per_run=3)
    first = service.monitor("example-ir")
    assert first.fetch_state.value == "ok"
    assert first.new_count >= 1
    fetched = [document for document in first.documents if document.fetched]
    assert fetched
    # csv KPIs extracted, provenance recorded on the timeline
    timeline = service.timeline("1234.T")
    assert timeline
    assert all(entry["provenance"]["provider"] == "ir.example.co.jp" for entry in timeline)

    second = service.monitor("example-ir")
    assert second.new_count == 0, "second run must not report false new documents"


def test_monitor_reports_unknown_source(tmp_path: Path) -> None:
    service = _service(tmp_path, _routes())
    outcome = service.monitor("does-not-exist")
    assert outcome.fetch_state.value == "failed"
    assert "unknown IR source" in outcome.notes[0]


def test_monitor_fails_closed_on_listing_error(tmp_path: Path) -> None:
    routes = {"/library/briefing.html": (500, "text/html", b"boom")}
    service = _service(tmp_path, routes)
    outcome = service.monitor("example-ir")
    assert outcome.fetch_state.value == "failed"
    assert not outcome.documents


def test_monitor_marks_auth_expiry(tmp_path: Path) -> None:
    routes = {"/library/briefing.html": (403, "text/html", b"forbidden")}
    service = _service(tmp_path, routes)
    outcome = service.monitor("example-ir")
    assert outcome.fetch_state.value in {"auth_expired", "failed"}


def test_monitor_detects_revision_on_second_run(tmp_path: Path) -> None:
    routes = _routes(content=b"<html><body><table><tr><th>KPI</th></tr></table></body></html>")
    service = _service(tmp_path, routes, max_documents_per_run=6)
    service.monitor("example-ir")
    # change one document's bytes -> that URL must be classified "revised"
    routes["/ir/items/fy2026_1q_summary.csv"] = (200, "text/csv", b"item,value\nbacklog,9\n")
    second = service.monitor("example-ir")
    statuses = {document.url.rsplit("/", 1)[-1]: document.status for document in second.documents}
    assert statuses["fy2026_1q_summary.csv"] == "revised"


def test_timeline_entry_payload_keeps_kpis_and_diff(tmp_path: Path) -> None:
    routes = _routes(content=b"<html><body><table><tr><th>KPI</th></tr></table></body></html>")
    service = _service(tmp_path, routes, max_documents_per_run=6)
    service.monitor("example-ir")
    entries = service.timeline("1234.T")
    payload = entries[0]["payload"]
    assert "kpis" in payload
    assert "kpi_diff" in payload
    assert payload["sha256"]
    # raw payload json round-trips (timeline is JSONL on disk; the symbol is
    # sanitized to a filename-safe component, dots become dashes)
    line = (
        (Path(tmp_path) / "ir-timeline" / "1234-T" / "timeline.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()[0]
    )
    assert json.loads(line)["kind"] in {"document", "page"}
