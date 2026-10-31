"""XLSX shared-string references must never become cell values.

One defect, pinned here independent of ``tests/test_acquisition_documents_units.py``:
a cell declared ``t="s"`` is a *reference* into ``xl/sharedStrings.xml``, not a
number and not a string. When the reference could not be resolved (no shared
table part, empty shared table, out-of-range index, negative index,
non-integer or empty value) the reader substituted the raw reference text
(``'0'``, ``'5'``, the last shared string for ``-1``, ``'bad'``) and the
document still reported ``parsed=True / parse_note=None`` — a fabricated
financial value that then flows into KPI extraction.

Contract pinned here
--------------------
- An unresolvable shared-string reference in ANY sheet inside the scanned
  range makes the whole document
  ``ExtractedDocument(format='xlsx', parsed=False, tables=[],
  parse_note='xlsx shared string reference invalid')``. Tables already read
  (an earlier sheet, the header row, the remaining cells) are not returned as a
  partially correct financial table.
- A document with no shared-string reference (``inlineStr`` / numeric only) is
  normal even when ``xl/sharedStrings.xml`` is absent or empty.
- Valid references (index 0 and the last index), rich-text shared strings,
  inline strings, numbers and empty cells are unchanged: ``name``, sheet
  order, ``headers``, ``rows``, ``_source_rows`` and ``parser_version`` keep
  their existing values, and reading the same bytes again is identical.
- ``_read_sheet_rows`` called directly never puts the unresolvable value into
  the row list it returns.
- The fixed note is the only thing that leaks: never raw exception text, never
  a cell value, never a URL.
- The IR pipeline keeps the same reason: ``parsed=False``, zero KPIs and the
  fixed note on the document record (in-process path, no network).
"""

from __future__ import annotations

import io
import socket
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from yowayowa.acquisition.documents import (
    ExtractedDocument,
    _read_shared_strings,
    _read_sheet_rows,
    extract_document,
    extract_xlsx_tables,
)
from yowayowa.services.ir_monitor_service import (
    IrMonitorService,
    IrSourceDefinition,
    TransportResponse,
)

XLSX_SHARED_STRING_NOTE = "xlsx shared string reference invalid"
# Values that must never surface in a note, a table or a record.
_SECRET_VALUE = "SECRET-CELL-VALUE-7f3a"
_SECRET_URL_TAIL = "secret-brief.xlsx"

# Distinguishes "the archive has no sharedStrings.xml part" from "the part is
# present but holds no <si> entries".
_MISSING = object()


# --------------------------------------------------------------- zip builders


def _col_letters(index: int) -> str:
    letters = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


def _shared_cell(reference: str, value: str | None) -> str:
    """A ``t="s"`` cell; ``value=None`` emits no ``<v>`` at all."""

    if value is None:
        return f"<c r='{reference}' t='s'/>"
    return f"<c r='{reference}' t='s'><v>{value}</v></c>"


def _inline_cell(reference: str, text: str) -> str:
    return f"<c r='{reference}' t='inlineStr'><is><t>{text}</t></is></c>"


def _number_cell(reference: str, value: str) -> str:
    return f"<c r='{reference}'><v>{value}</v></c>"


def _sheet(*rows: str) -> str:
    body = "".join(f'<row r="{index}">{cells}</row>' for index, cells in enumerate(rows, start=1))
    return f"<worksheet><sheetData>{body}</sheetData></worksheet>"


def _shared_table(*values: str) -> str:
    return "<sst>" + "".join(f"<si><t>{value}</t></si>" for value in values) + "</sst>"


def _xlsx(
    *,
    sheets: dict[int, str],
    shared: object = _MISSING,
    workbook: str | None = None,
) -> bytes:
    if workbook is None:
        declared = "".join(f'<sheet name="Sheet{index}"/>' for index in sorted(sheets))
        workbook = f"<workbook><sheets>{declared}</sheets></workbook>"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        if workbook is not None:
            archive.writestr("xl/workbook.xml", workbook)
        if isinstance(shared, str):
            archive.writestr("xl/sharedStrings.xml", shared)
        for index, xml in sheets.items():
            archive.writestr(f"xl/worksheets/sheet{index}.xml", xml)
    return buffer.getvalue()


def _single_reference_document(index_text: str | None, *, shared: object) -> bytes:
    """Header row via inline strings, one data row holding a shared reference."""

    return _xlsx(
        shared=shared,
        sheets={
            1: _sheet(
                _inline_cell("A1", "kpi"),
                _inline_cell("A2", "受注残") + _shared_cell("B2", index_text),
            )
        },
    )


# ------------------------------------------------- fail closed: the whole doc

_INVALID_REFERENCE_CASES = [
    ("no-shared-table-part", _MISSING, "0"),
    ("empty-shared-table", "<sst/>", "0"),
    ("index-out-of-range", _shared_table("x"), "5"),
    ("index-equals-length", _shared_table("x"), "1"),
    ("negative-index", _shared_table("x"), "-1"),
    ("non-integer-index", _shared_table("x"), "bad"),
    ("float-shaped-index", _shared_table("x"), "1.0"),
    ("empty-value-text", _shared_table("x"), ""),
    ("no-value-node", _shared_table("x"), None),
]


@pytest.mark.parametrize(
    ("shared", "value"),
    [(shared, value) for _, shared, value in _INVALID_REFERENCE_CASES],
    ids=[case_id for case_id, _, _ in _INVALID_REFERENCE_CASES],
)
def test_unresolvable_shared_string_reference_fails_the_document_closed(
    shared: object, value: str | None
) -> None:
    extracted = extract_xlsx_tables(_single_reference_document(value, shared=shared))

    assert extracted.format == "xlsx"
    assert extracted.parsed is False
    assert extracted.tables == []
    assert extracted.parse_note == XLSX_SHARED_STRING_NOTE


def test_invalid_reference_in_a_header_cell_fails_closed() -> None:
    content = _xlsx(
        shared=_shared_table("x"),
        sheets={
            1: _sheet(_shared_cell("A1", "9") + _inline_cell("B1", "b"), _inline_cell("A2", "1"))
        },
    )

    extracted = extract_xlsx_tables(content)

    assert extracted.parsed is False
    assert extracted.tables == []
    assert extracted.parse_note == XLSX_SHARED_STRING_NOTE


def test_invalid_reference_in_a_data_cell_fails_closed() -> None:
    content = _single_reference_document("9", shared=_shared_table("x"))

    extracted = extract_xlsx_tables(content)

    assert extracted.parsed is False
    assert extracted.tables == []
    assert extracted.parse_note == XLSX_SHARED_STRING_NOTE


def test_invalid_reference_in_a_later_sheet_returns_no_earlier_table() -> None:
    """A readable first sheet must not come back as a partially correct table."""

    workbook = (
        "<workbook><sheets><sheet name='readable'/><sheet name='broken'/></sheets></workbook>"
    )
    content = _xlsx(
        workbook=workbook,
        shared=_shared_table("kpi", "受注残", "1200"),
        sheets={
            1: _sheet(
                _shared_cell("A1", "0") + _shared_cell("B1", "1"),
                _inline_cell("A2", "受注残") + _number_cell("B2", "1200"),
            ),
            2: _sheet(_inline_cell("A1", "x") + _shared_cell("B1", "5")),
        },
    )

    extracted = extract_xlsx_tables(content)

    assert extracted.parsed is False
    assert extracted.tables == []
    assert extracted.parse_note == XLSX_SHARED_STRING_NOTE
    assert "1200" not in repr(extracted)


def test_fixed_note_never_exposes_cell_values_exceptions_or_urls() -> None:
    content = _xlsx(
        shared=_shared_table("x"),
        sheets={1: _sheet(_inline_cell("A1", _SECRET_VALUE), _shared_cell("A2", _SECRET_VALUE))},
    )

    extracted = extract_document(
        content=content,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=_SECRET_URL_TAIL,
        url=f"https://example.co.jp/ir/{_SECRET_URL_TAIL}?token=secret",
    )

    assert extracted.parse_note == XLSX_SHARED_STRING_NOTE
    assert extracted.tables == []
    note = extracted.parse_note or ""
    assert _SECRET_VALUE not in note
    assert _SECRET_URL_TAIL not in note
    assert "example.co.jp" not in note
    assert "Error" not in note and "Traceback" not in note
    assert extracted.text is None


# ------------------------------------------------------- unchanged valid reads


def _valid_document() -> tuple[bytes, ExtractedDocument]:
    """Two sheets: shared index 0, rich text, numeric, inline and empty cells."""

    shared = (
        "<sst>"
        "<si><t>受注残</t></si>"
        "<si><r><t>当</t></r><r><t>連結</t></r></si>"
        "<si><t>1200</t></si>"
        "</sst>"
    )
    workbook = "<workbook><sheets><sheet name='KPI'/><sheet name='Summary'/></sheets></workbook>"
    content = _xlsx(
        workbook=workbook,
        shared=shared,
        sheets={
            1: _sheet(
                _shared_cell("A1", "0") + _shared_cell("B1", "1") + _shared_cell("C1", "2"),
                _inline_cell("A2", "受注残") + _number_cell("B2", "1200") + "<c r='C2'/>",
            ),
            2: _sheet(
                _inline_cell("A1", "memo"),
                _shared_cell("A2", "2") + _inline_cell("B2", "x"),
            ),
        },
    )
    return content, extract_xlsx_tables(content)


def test_valid_shared_string_references_keep_every_existing_value() -> None:
    _, extracted = _valid_document()

    assert extracted.format == "xlsx"
    assert extracted.parsed is True
    assert extracted.parse_note is None
    assert extracted.parser_version == "doc-v1"
    assert [table["name"] for table in extracted.tables] == ["KPI", "Summary"]

    first = extracted.tables[0]
    assert first["headers"] == ["受注残", "当連結", "1200"]
    assert first["rows"] == [{"受注残": "受注残", "当連結": "1200", "1200": ""}]
    assert first["_source_rows"] == [["受注残", "当連結", "1200"], ["受注残", "1200"]]

    second = extracted.tables[1]
    assert second["headers"] == ["memo"]
    assert second["rows"] == [{"memo": "1200"}]
    assert second["_source_rows"] == [["memo"], ["1200", "x"]]


def test_numeric_and_inline_only_document_is_normal_without_shared_table() -> None:
    """No shared-string reference at all: a missing shared table is not a defect."""

    content = _xlsx(
        shared=_MISSING,
        sheets={
            1: _sheet(
                _inline_cell("A1", "kpi") + _inline_cell("B1", "値"),
                _number_cell("A2", "1200") + "<c r='B2'/>",
            )
        },
    )

    extracted = extract_xlsx_tables(content)

    assert extracted.parsed is True
    assert extracted.parse_note is None
    assert extracted.tables[0]["headers"] == ["kpi", "値"]
    assert extracted.tables[0]["rows"] == [{"kpi": "1200", "値": ""}]

    empty_shared = extract_xlsx_tables(
        _xlsx(
            shared="<sst/>",
            sheets={1: _sheet(_inline_cell("A1", "kpi"), _number_cell("A2", "1"))},
        )
    )
    assert empty_shared.parsed is True
    assert empty_shared.parse_note is None


def test_same_bytes_are_read_identically() -> None:
    content, first = _valid_document()
    second = extract_xlsx_tables(content)

    assert first == second


def test_extract_document_transports_the_fixed_fail_closed_result() -> None:
    content = _single_reference_document("0", shared=_MISSING)

    by_extension = extract_document(content=content, content_type=None, filename="kpi.xlsx")
    by_content_type = extract_document(
        content=content,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=None,
    )

    for extracted in (by_extension, by_content_type):
        assert extracted.format == "xlsx"
        assert extracted.parsed is False
        assert extracted.tables == []
        assert extracted.parse_note == XLSX_SHARED_STRING_NOTE


# ------------------------------------------------------- direct helper contract


def test_read_sheet_rows_never_returns_an_unresolvable_reference() -> None:
    content = _single_reference_document("5", shared=_shared_table("x"))
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        with_diagnostics: dict[str, Any] = {}
        rows = _read_sheet_rows(
            archive, 1, _read_shared_strings(archive), diagnostics=with_diagnostics
        )
        rows_without_diagnostics = _read_sheet_rows(archive, 1, [])
        assert _read_shared_strings(archive) == ["x"]

    assert rows == []
    assert rows_without_diagnostics == []
    assert with_diagnostics, "the caller must be able to observe the failure"
    assert all(value is not False and value is not None for value in with_diagnostics.values())


def test_read_sheet_rows_keeps_resolvable_rows() -> None:
    content = _valid_document()[0]
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        rows = _read_sheet_rows(archive, 1, _read_shared_strings(archive))

    assert rows == [["受注残", "当連結", "1200"], ["受注残", "1200"]]


# ------------------------------------------------------------ IR pipeline path


class _StubTransport:
    """In-process transport: no sockets, deterministic bytes per resource."""

    def __init__(self, *, listing: str, documents: dict[str, bytes]) -> None:
        self._listing = listing
        self._documents = documents
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
        self.calls.append(f"{method} {resource}")
        if resource in self._documents:
            payload = self._documents[resource]
            return TransportResponse(
                status_code=200,
                url=f"https://example.co.jp{resource}",
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                text="",
                content=payload,
                elapsed_ms=1.0,
            )
        return TransportResponse(
            status_code=200,
            url=f"https://example.co.jp{resource}",
            content_type="text/html; charset=utf-8",
            text=self._listing,
            content=self._listing.encode("utf-8"),
            elapsed_ms=1.0,
        )


def test_ir_pipeline_reports_zero_kpis_with_the_same_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _no_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("the IR pipeline must not open a socket in this test")

    monkeypatch.setattr(socket.socket, "connect", _no_network)

    document_url = f"https://example.co.jp/ir/{_SECRET_URL_TAIL}"
    listing = f'<html><body><a href="{_SECRET_URL_TAIL}">決算短信</a></body></html>'
    transport = _StubTransport(
        listing=listing,
        documents={
            f"/ir/{_SECRET_URL_TAIL}": _single_reference_document("0", shared=_MISSING),
        },
    )
    service = IrMonitorService(
        data_dir=tmp_path,
        transport_factory=lambda source: transport,
        sources=[
            IrSourceDefinition(
                source_id="ir-test",
                symbol="7203",
                provider="official-ir",
                listing_url="https://example.co.jp/ir/",
            )
        ],
        now=lambda: datetime(2026, 10, 24, tzinfo=UTC),
    )

    outcome = service.monitor("ir-test")

    records = [record for record in outcome.documents if record.url == document_url]
    assert len(records) == 1
    record = records[0]
    assert record.fetched is True
    assert record.format == "xlsx"
    assert record.parsed is False
    assert record.parse_note == XLSX_SHARED_STRING_NOTE
    assert record.kpis == []
    assert XLSX_SHARED_STRING_NOTE in record.notes

    entries = service.timeline("7203")
    assert entries, "a new document must still reach the timeline"
    payload = entries[0]["payload"]
    assert payload["parsed"] is False
    assert payload["parse_note"] == XLSX_SHARED_STRING_NOTE
    assert payload["kpis"] == []
    assert transport.calls == ["GET /ir/", f"GET /ir/{_SECRET_URL_TAIL}"]
