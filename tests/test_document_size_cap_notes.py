"""CSV/XLSX size-cap truncation must be explicit in ``parse_note``.

P5 card t_3501871f: the row/column caps in ``extract_csv_tables`` and
``extract_xlsx_tables`` silently dropped data with ``parse_note=None``, so a
truncated table was indistinguishable from a complete one (the strict-xfail
``test_size_truncation_is_reported_in_the_parse_note`` in
``tests/test_acquisition_documents_units.py`` pins that defect). This file
pins the fixed contract:

- actual truncation adds ONE fixed note per kind per document, rows before
  columns, '; '-joined after any existing note (missing parts, no-readable);
- wording is generated from the runtime cap constants (``_XLSX_MAX_ROWS`` /
  ``_XLSX_MAX_COLUMNS``), never embeds raw data, URLs, sheet names or errors;
- below the cap / exactly at the cap the whole result (including
  ``parse_note``) is unchanged from the previous implementation;
- CSV counts csv.reader raw rows (header and blank rows included) and keeps
  counting trailing empty cells as column width; XLSX counts readable
  non-empty rows per sheet as before;
- XLSX empty rows past the row cap and empty cells past the column cap stay
  note-free; the cap lookahead reads raw ``<v>``/inline text only and never
  resolves shared strings, so an invalid reference past the cap yields the
  row note only, while one inside the scanned range still fails the document
  closed with the fixed shared-string note;
- the note survives ``extract_document`` dispatch and the existing
  ``IrMonitorService`` record preservation path.

Caps are monkeypatched to 3 rows / 2 columns so cap-1 / cap / cap+1 needs no
10 001-row fixture; the real 10 000 / 256 boundary is additionally exercised
at the default constants. No test reaches the network.
"""

from __future__ import annotations

import io
import socket
import zipfile
from pathlib import Path
from typing import Any

import pytest

from yowayowa.acquisition import documents
from yowayowa.acquisition.documents import (
    extract_csv_tables,
    extract_document,
    extract_xlsx_tables,
)
from yowayowa.acquisition.transport import TransportResponse
from yowayowa.services.ir_monitor_service import IrMonitorService, IrSourceDefinition

# --------------------------------------------------------------- builders


def _col_letters(index: int) -> str:
    letters = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


def _sheet_xml(rows: list[list[str]]) -> str:
    parts: list[str] = []
    for row_index, row in enumerate(rows, start=1):
        cells = "".join(
            f'<c r="{_col_letters(column)}{row_index}" t="inlineStr"><is><t>{value}</t></is></c>'
            for column, value in enumerate(row, start=1)
        )
        parts.append(f'<row r="{row_index}">{cells}</row>')
    return "<worksheet><sheetData>" + "".join(parts) + "</sheetData></worksheet>"


def _raw_sheet_xml(body: str) -> str:
    return "<worksheet><sheetData>" + body + "</sheetData></worksheet>"


def _xlsx_zip(
    *,
    workbook: str | None = '<workbook><sheets><sheet name="KPI"/></sheets></workbook>',
    shared: str | None = None,
    sheets: dict[int, str] | None = None,
) -> bytes:
    if sheets is None:
        sheets = {1: _sheet_xml([["kpi"], ["1200"]])}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        if workbook is not None:
            archive.writestr("xl/workbook.xml", workbook)
        if shared is not None:
            archive.writestr("xl/sharedStrings.xml", shared)
        for index, xml in sheets.items():
            archive.writestr(f"xl/worksheets/sheet{index}.xml", xml)
    return buffer.getvalue()


_OVER_SHEET = _sheet_xml([["a", "b"], ["1", "2"], ["3", "4"], ["5", "6"]])
_EXACT_SHEET = _sheet_xml([["a", "b"], ["1", "2"], ["3", "4"]])
_TWO_SHEET_WORKBOOK = '<workbook><sheets><sheet name="one"/><sheet name="two"/></sheets></workbook>'


@pytest.fixture
def cap3x2(monkeypatch: pytest.MonkeyPatch) -> tuple[int, int]:
    """Local 3-row / 2-column caps so cap-1 / cap / cap+1 stays a small fixture."""

    monkeypatch.setattr(documents, "_XLSX_MAX_ROWS", 3)
    monkeypatch.setattr(documents, "_XLSX_MAX_COLUMNS", 2)
    return 3, 2


# --------------------------------------------------------------- CSV notes


def test_csv_row_cap_adds_the_fixed_note(cap3x2: tuple[int, int]) -> None:
    extracted = extract_csv_tables(b"a,b\n1,2\n3,4\n5,6\n")
    assert extracted.parsed
    assert extracted.parse_note == "csv rows truncated at 3"
    table = extracted.tables[0]
    assert table["_source_rows"] == [["a", "b"], ["1", "2"], ["3", "4"]]
    assert table["rows"] == [{"a": "1", "b": "2"}, {"a": "3", "b": "4"}]


def test_csv_row_cap_exact_stays_note_free(cap3x2: tuple[int, int]) -> None:
    extracted = extract_csv_tables(b"a,b\n1,2\n3,4\n")
    assert extracted.parsed
    assert extracted.parse_note is None
    assert extracted.tables[0]["rows"] == [{"a": "1", "b": "2"}, {"a": "3", "b": "4"}]


def test_csv_column_cap_adds_the_fixed_note(cap3x2: tuple[int, int]) -> None:
    extracted = extract_csv_tables(b"a,b,c\n1,2,3\n")
    assert extracted.parsed
    assert extracted.parse_note == "csv columns truncated at 2"
    assert extracted.tables[0]["headers"] == ["a", "b"]
    assert extracted.tables[0]["rows"] == [{"a": "1", "b": "2"}]


def test_csv_column_cap_exact_stays_note_free(cap3x2: tuple[int, int]) -> None:
    extracted = extract_csv_tables(b"a,b\n1,2\n")
    assert extracted.parsed
    assert extracted.parse_note is None
    assert extracted.tables[0]["headers"] == ["a", "b"]


def test_csv_trailing_empty_cells_count_as_column_width(cap3x2: tuple[int, int]) -> None:
    """Raw csv width counts trailing empty cells; losing them is a truncation."""

    extracted = extract_csv_tables(b"a,b\n1,2,\n")
    assert extracted.parsed
    assert extracted.parse_note == "csv columns truncated at 2"
    assert extracted.tables[0]["rows"] == [{"a": "1", "b": "2"}]


def test_csv_both_caps_note_once_in_rows_columns_order(cap3x2: tuple[int, int]) -> None:
    extracted = extract_csv_tables(b"a,b,c\n1,2,3\n4,5,6\n7,8,9\n")
    assert extracted.parsed
    assert extracted.parse_note == "csv rows truncated at 3; csv columns truncated at 2"
    assert extracted.parse_note.count("truncated") == 2


def test_csv_blank_rows_within_cap_stay_note_free(cap3x2: tuple[int, int]) -> None:
    """Blank rows inside the cap are the old drop-nothing case: no note."""

    extracted = extract_csv_tables(b"a,b\n\n\n")
    assert extracted.parsed
    assert extracted.parse_note is None
    assert extracted.tables[0]["rows"] == []


def test_csv_blank_rows_past_cap_fail_closed_with_note(cap3x2: tuple[int, int]) -> None:
    """Blank-only retained rows keep parsed=False; the row loss is still noted."""

    extracted = extract_csv_tables(b"\n\n\na,b\n")
    assert not extracted.parsed
    assert extracted.parse_note == "csv contained no readable rows; csv rows truncated at 3"


def test_csv_default_row_cap_note_uses_the_runtime_constant() -> None:
    lines = ["a,b"] + [f"{index},{index}" for index in range(10_100)]
    extracted = extract_csv_tables("\n".join(lines).encode())
    assert extracted.parsed
    assert extracted.parse_note == "csv rows truncated at 10000"
    assert len(extracted.tables[0]["_source_rows"]) == 10_000


def test_csv_default_column_cap_note_uses_the_runtime_constant() -> None:
    header = ",".join(str(index) for index in range(300))
    extracted = extract_csv_tables(f"{header}\n".encode())
    assert extracted.parsed
    assert extracted.parse_note == "csv columns truncated at 256"
    assert len(extracted.tables[0]["headers"]) == 256


def test_csv_marker_values_never_reach_the_note(cap3x2: tuple[int, int]) -> None:
    extracted = extract_csv_tables(b"a,YWCOLMARK\nYWROWMARK,2\n3,4\n5,6\n")
    assert extracted.parsed
    assert extracted.parse_note == "csv rows truncated at 3"
    assert "YWROWMARK" not in (extracted.parse_note or "")
    assert "YWCOLMARK" not in (extracted.parse_note or "")


def test_csv_decode_failure_note_is_unchanged() -> None:
    extracted = extract_csv_tables(b"\x81\x82\x83")
    assert not extracted.parsed
    assert extracted.parse_note == "csv decode failed (neither utf-8-sig nor cp932); not parsed"


# --------------------------------------------------------------- XLSX notes


def test_xlsx_row_cap_adds_the_fixed_note(cap3x2: tuple[int, int]) -> None:
    extracted = extract_xlsx_tables(_xlsx_zip(sheets={1: _OVER_SHEET}))
    assert extracted.parsed
    assert extracted.parse_note == "xlsx rows truncated at 3"
    table = extracted.tables[0]
    assert table["_source_rows"] == [["a", "b"], ["1", "2"], ["3", "4"]]
    assert table["rows"] == [{"a": "1", "b": "2"}, {"a": "3", "b": "4"}]


def test_xlsx_row_cap_exact_stays_note_free(cap3x2: tuple[int, int]) -> None:
    extracted = extract_xlsx_tables(_xlsx_zip(sheets={1: _EXACT_SHEET}))
    assert extracted.parsed
    assert extracted.parse_note is None
    assert extracted.tables[0]["rows"] == [{"a": "1", "b": "2"}, {"a": "3", "b": "4"}]


def test_xlsx_empty_rows_past_the_cap_stay_note_free(cap3x2: tuple[int, int]) -> None:
    """Rows without payload past the cap are invisible: the lookahead is quiet."""

    exact_plus_empty = _EXACT_SHEET.replace(
        "</sheetData>",
        '<row r="4"/><row r="5"><c r="A5"/></row></sheetData>',
    )
    extracted = extract_xlsx_tables(_xlsx_zip(sheets={1: exact_plus_empty}))
    assert extracted.parsed
    assert extracted.parse_note is None
    assert len(extracted.tables[0]["rows"]) == 2


def test_xlsx_payload_row_past_the_cap_is_detected(cap3x2: tuple[int, int]) -> None:
    """The lookahead reads raw text only: a t=\"s\" ref past the cap counts."""

    exact_plus_shared = _EXACT_SHEET.replace(
        "</sheetData>",
        '<row r="4"><c r="A4" t="s"><v>5</v></c></row></sheetData>',
    )
    extracted = extract_xlsx_tables(_xlsx_zip(sheets={1: exact_plus_shared}))
    assert extracted.parsed
    assert extracted.parse_note == "xlsx rows truncated at 3"


def test_xlsx_column_cap_adds_the_fixed_note(cap3x2: tuple[int, int]) -> None:
    sheet = _raw_sheet_xml(
        '<row r="1"><c r="A1" t="inlineStr"><is><t>kept</t></is></c>'
        '<c r="C1" t="inlineStr"><is><t>dropped</t></is></c></row>'
    )
    extracted = extract_xlsx_tables(_xlsx_zip(sheets={1: sheet}))
    assert extracted.parsed
    assert extracted.parse_note == "xlsx columns truncated at 2"
    # Retained shape is unchanged from the pre-note implementation: the wide
    # first row is padded to the capped width with empty strings, and the
    # dropped cell's value is gone from every field.
    assert extracted.tables[0]["headers"] == ["kept", ""]
    assert extracted.tables[0]["_source_rows"] == [["kept", ""]]
    assert "dropped" not in str(extracted.tables)


def test_xlsx_column_cap_exact_stays_note_free(cap3x2: tuple[int, int]) -> None:
    sheet = _raw_sheet_xml(
        '<row r="1"><c r="A1" t="inlineStr"><is><t>kept</t></is></c>'
        '<c r="B1" t="inlineStr"><is><t>kept2</t></is></c></row>'
    )
    extracted = extract_xlsx_tables(_xlsx_zip(sheets={1: sheet}))
    assert extracted.parsed
    assert extracted.parse_note is None
    assert extracted.tables[0]["headers"] == ["kept", "kept2"]


def test_xlsx_empty_cells_past_the_column_cap_stay_note_free(cap3x2: tuple[int, int]) -> None:
    sheet = _raw_sheet_xml(
        '<row r="1"><c r="A1" t="inlineStr"><is><t>kept</t></is></c><c r="C1"/></row>'
    )
    extracted = extract_xlsx_tables(_xlsx_zip(sheets={1: sheet}))
    assert extracted.parsed
    assert extracted.parse_note is None


def test_xlsx_both_caps_note_once_in_rows_columns_order(cap3x2: tuple[int, int]) -> None:
    extracted = extract_xlsx_tables(_xlsx_zip(sheets={1: _OVER_SHEET.replace("A1", "C1")}))
    assert extracted.parsed
    assert extracted.parse_note == "xlsx rows truncated at 3; xlsx columns truncated at 2"
    assert extracted.parse_note.count("truncated") == 2


def test_xlsx_invalid_shared_ref_past_cap_yields_only_the_row_note(
    cap3x2: tuple[int, int],
) -> None:
    """A ref past the cap is raw-lookahead-only: no fail-closed, row note only."""

    exact_plus_bad_ref = _EXACT_SHEET.replace(
        "</sheetData>",
        '<row r="4"><c r="A4" t="s"><v>99</v></c></row></sheetData>',
    )
    extracted = extract_xlsx_tables(_xlsx_zip(sheets={1: exact_plus_bad_ref}))
    assert extracted.parsed
    assert extracted.tables
    assert extracted.parse_note == "xlsx rows truncated at 3"
    assert "shared string" not in (extracted.parse_note or "")


def test_xlsx_invalid_shared_ref_within_cap_keeps_fail_closed_note(
    cap3x2: tuple[int, int],
) -> None:
    """Inside the scanned range the fixed fail-closed contract is untouched."""

    sheet = _raw_sheet_xml(
        '<row r="1"><c r="A1" t="inlineStr"><is><t>a</t></is></c></row>'
        '<row r="2"><c r="A2" t="s"><v>99</v></c></row>'
    )
    extracted = extract_xlsx_tables(_xlsx_zip(sheets={1: sheet}))
    assert not extracted.parsed
    assert extracted.tables == []
    assert extracted.parse_note == "xlsx shared string reference invalid"


def test_xlsx_row_cap_on_a_later_sheet_is_noted(cap3x2: tuple[int, int]) -> None:
    extracted = extract_xlsx_tables(
        _xlsx_zip(
            workbook=_TWO_SHEET_WORKBOOK,
            sheets={1: _sheet_xml([["a"], ["1"]]), 2: _OVER_SHEET},
        )
    )
    assert extracted.parsed
    assert extracted.parse_note == "xlsx rows truncated at 3"
    assert [table["name"] for table in extracted.tables] == ["one", "two"]
    assert extracted.tables[0]["rows"] == [{"a": "1"}]


def test_xlsx_truncation_note_appears_once_across_sheets(cap3x2: tuple[int, int]) -> None:
    extracted = extract_xlsx_tables(
        _xlsx_zip(workbook=_TWO_SHEET_WORKBOOK, sheets={1: _OVER_SHEET, 2: _OVER_SHEET})
    )
    assert extracted.parsed
    assert extracted.parse_note == "xlsx rows truncated at 3"
    assert (extracted.parse_note or "").count("xlsx rows truncated") == 1


def test_xlsx_missing_part_note_comes_before_truncation(cap3x2: tuple[int, int]) -> None:
    """Existing note first, '; ', then rows: the fixed composition order."""

    extracted = extract_xlsx_tables(
        _xlsx_zip(workbook=_TWO_SHEET_WORKBOOK, sheets={1: _OVER_SHEET})
    )
    assert extracted.parsed
    assert extracted.parse_note == "xlsx worksheet parts missing: 1; xlsx rows truncated at 3"


def test_xlsx_missing_part_only_note_is_unchanged(cap3x2: tuple[int, int]) -> None:
    extracted = extract_xlsx_tables(
        _xlsx_zip(
            workbook=_TWO_SHEET_WORKBOOK,
            sheets={2: _sheet_xml([["a"], ["1"]])},
        )
    )
    assert extracted.parsed
    assert extracted.parse_note == "xlsx worksheet parts missing: 1"


def test_xlsx_default_row_cap_note_uses_the_runtime_constant() -> None:
    rows = [["h"]] + [[str(index)] for index in range(10_100)]
    extracted = extract_xlsx_tables(_xlsx_zip(sheets={1: _sheet_xml(rows)}))
    assert extracted.parsed
    assert extracted.parse_note == "xlsx rows truncated at 10000"
    assert len(extracted.tables[0]["_source_rows"]) == 10_000


def test_xlsx_default_column_cap_note_uses_the_runtime_constant() -> None:
    sheet = _raw_sheet_xml(
        '<row r="1"><c r="A1" t="inlineStr"><is><t>kept</t></is></c>'
        '<c r="IW1" t="inlineStr"><is><t>dropped</t></is></c></row>'
    )
    extracted = extract_xlsx_tables(_xlsx_zip(sheets={1: sheet}))
    assert extracted.parsed
    assert extracted.parse_note == "xlsx columns truncated at 256"
    assert len(extracted.tables[0]["headers"]) == 256


def test_xlsx_sheet_names_and_values_never_reach_the_note(cap3x2: tuple[int, int]) -> None:
    workbook = (
        '<workbook><sheets><sheet name="YWSHEETMARK"/><sheet name="plain"/></sheets></workbook>'
    )
    over_with_marker = _sheet_xml([["a", "YWMARK"], ["1", "2"], ["3", "4"], ["5", "6"]])
    extracted = extract_xlsx_tables(
        _xlsx_zip(workbook=workbook, sheets={1: over_with_marker, 2: over_with_marker})
    )
    assert extracted.parsed
    assert extracted.parse_note == "xlsx rows truncated at 3"
    assert "YWSHEETMARK" not in (extracted.parse_note or "")
    assert "YWMARK" not in (extracted.parse_note or "")


# ----------------------------------------------- dispatch / determinism / IR


def test_extract_document_dispatch_carries_truncation_notes(cap3x2: tuple[int, int]) -> None:
    csv_doc = extract_document(content=b"a,b\n1,2\n3,4\n5,6\n", content_type=None, filename="t.csv")
    assert csv_doc.format == "csv"
    assert csv_doc.parsed
    assert csv_doc.parse_note == "csv rows truncated at 3"

    xlsx_doc = extract_document(
        content=_xlsx_zip(sheets={1: _OVER_SHEET}), content_type=None, filename="t.xlsx"
    )
    assert xlsx_doc.format == "xlsx"
    assert xlsx_doc.parsed
    assert xlsx_doc.parse_note == "xlsx rows truncated at 3"


def test_truncated_extraction_is_deterministic(cap3x2: tuple[int, int]) -> None:
    content = b"a,b\n1,2\n3,4\n5,6\n"
    first = extract_csv_tables(content)
    second = extract_csv_tables(content)
    assert first == second
    assert extract_xlsx_tables(_xlsx_zip(sheets={1: _OVER_SHEET})).parse_note == (
        extract_xlsx_tables(_xlsx_zip(sheets={1: _OVER_SHEET})).parse_note
    )


def test_extraction_of_truncated_documents_never_touches_the_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _blocked(*args: object, **kwargs: object) -> None:
        raise AssertionError("document extraction attempted network access")

    monkeypatch.setattr(documents, "_XLSX_MAX_ROWS", 3)
    monkeypatch.setattr(documents, "_XLSX_MAX_COLUMNS", 2)
    monkeypatch.setattr(socket, "socket", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)

    assert extract_csv_tables(b"a,b\n1,2\n3,4\n5,6\n").parse_note
    assert extract_xlsx_tables(_xlsx_zip(sheets={1: _OVER_SHEET})).parse_note


class _StubTransport:
    """In-memory transport for the IR propagation check; no socket is opened."""

    def __init__(self, routes: dict[str, tuple[int, str, bytes]]) -> None:
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
        status, content_type, body = self.routes[resource]
        return TransportResponse(
            status_code=status,
            url=f"https://ir.example.co.jp{resource}",
            content_type=content_type,
            text=body.decode("utf-8", errors="replace"),
            content=body,
            elapsed_ms=1.0,
        )


def test_ir_document_record_preserves_truncation_note(
    tmp_path: Path, cap3x2: tuple[int, int]
) -> None:
    """The existing IR path (record.parse_note = extracted.parse_note) carries it."""

    listing = (
        '<html><body><a href="/ir/big.csv">\u6c7a\u7b97\u8cc7\u6599</a></body></html>'.encode()
    )
    transport = _StubTransport(
        {
            "/library/briefing.html": (200, "text/html; charset=utf-8", listing),
            "/ir/big.csv": (200, "text/csv", b"a,b\n1,2\n3,4\n5,6\n"),
        }
    )
    service = IrMonitorService(
        data_dir=tmp_path,
        transport_factory=lambda source: transport,
        sources=[
            IrSourceDefinition(
                source_id="example-ir",
                symbol="1234.T",
                provider="ir.example.co.jp",
                listing_url="https://ir.example.co.jp/library/briefing.html",
            )
        ],
    )

    outcome = service.monitor("example-ir")

    record = outcome.documents[0]
    assert record.format == "csv"
    assert record.parsed is True
    assert record.parse_note == "csv rows truncated at 3"


# ------------------------------------------------ boundary suites (R1, round 2)
#
# Audit round 1 (t_3501871f, audit-sol): the first round pinned the local caps
# at exact/over and the real caps only with a large overshoot (10 101 rows /
# 300 columns). This section keeps the cap-1 / cap / cap+1 edge as permanent
# parametrized tests for BOTH formats at BOTH the local (3 rows / 2 columns)
# and the default (10 000 rows / 256 columns) constants, pins the payload
# presence rules right past each cap ('0' / whitespace count, the empty string
# does not), and pins the counting difference between CSV's raw-row cap and
# XLSX's non-empty-row cap. The product code is untouched by this section.


@pytest.mark.parametrize(
    ("total_rows", "noted"),
    [(2, False), (3, False), (4, True)],
    ids=["cap-1", "cap", "cap+1"],
)
def test_csv_local_row_cap_boundary(cap3x2: tuple[int, int], total_rows: int, noted: bool) -> None:
    lines = ["a,b"] + [f"{index},{index}" for index in range(total_rows - 1)]
    extracted = extract_csv_tables("\n".join(lines).encode() + b"\n")
    assert extracted.parsed
    assert (extracted.parse_note == "csv rows truncated at 3") is noted
    assert (extracted.parse_note is None) is (not noted)
    assert len(extracted.tables[0]["_source_rows"]) == min(total_rows, 3)


@pytest.mark.parametrize(
    ("total_rows", "noted"),
    [(9_999, False), (10_000, False), (10_001, True)],
    ids=["cap-1", "cap", "cap+1"],
)
def test_csv_default_row_cap_boundary(total_rows: int, noted: bool) -> None:
    lines = ["a,b"] + [f"{index},{index}" for index in range(total_rows - 1)]
    extracted = extract_csv_tables("\n".join(lines).encode() + b"\n")
    assert extracted.parsed
    assert (extracted.parse_note == "csv rows truncated at 10000") is noted
    assert (extracted.parse_note is None) is (not noted)
    assert len(extracted.tables[0]["_source_rows"]) == min(total_rows, 10_000)


@pytest.mark.parametrize(
    ("width", "noted"),
    [(1, False), (2, False), (3, True)],
    ids=["cap-1", "cap", "cap+1"],
)
def test_csv_local_column_cap_boundary(cap3x2: tuple[int, int], width: int, noted: bool) -> None:
    payload = ",".join(f"v{index}" for index in range(width))
    extracted = extract_csv_tables(f"{payload}\n{payload}\n".encode())
    assert extracted.parsed
    assert (extracted.parse_note == "csv columns truncated at 2") is noted
    assert (extracted.parse_note is None) is (not noted)
    assert len(extracted.tables[0]["headers"]) == min(width, 2)


@pytest.mark.parametrize(
    ("width", "noted"),
    [(255, False), (256, False), (257, True)],
    ids=["cap-1", "cap", "cap+1"],
)
def test_csv_default_column_cap_boundary(width: int, noted: bool) -> None:
    payload = ",".join(f"v{index}" for index in range(width))
    extracted = extract_csv_tables(f"{payload}\n{payload}\n".encode())
    assert extracted.parsed
    assert (extracted.parse_note == "csv columns truncated at 256") is noted
    assert (extracted.parse_note is None) is (not noted)
    assert len(extracted.tables[0]["headers"]) == min(width, 256)


@pytest.mark.parametrize(("tail_blanks", "noted"), [(0, False), (1, True)])
def test_csv_blank_rows_crossing_the_cap_are_counted(
    cap3x2: tuple[int, int], tail_blanks: int, noted: bool
) -> None:
    """Raw csv rows include blank lines: a blank row past the cap still counts."""

    lines = ["a,b", "1,2", "3,4"] + [""] * tail_blanks
    extracted = extract_csv_tables(("\n".join(lines) + "\n").encode())
    assert extracted.parsed
    assert (extracted.parse_note == "csv rows truncated at 3") is noted
    assert (extracted.parse_note is None) is (not noted)


def test_csv_counts_raw_blank_rows_while_xlsx_counts_non_empty_rows(
    cap3x2: tuple[int, int],
) -> None:
    """The formats count different things: CSV's cap sees raw rows (header and
    blank lines included), XLSX's cap sees readable non-empty rows only."""

    csv_doc = extract_csv_tables(b"a,b\n1,2\n\n\n")
    assert csv_doc.parsed
    assert csv_doc.parse_note == "csv rows truncated at 3"

    blank_sheet = _sheet_xml([["a", "b"], ["1", "2"], ["", ""], ["", ""]])
    xlsx_doc = extract_xlsx_tables(_xlsx_zip(sheets={1: blank_sheet}))
    assert xlsx_doc.parsed
    assert xlsx_doc.parse_note is None
    assert len(xlsx_doc.tables[0]["rows"]) == 1


@pytest.mark.parametrize(
    ("total_rows", "noted"),
    [(2, False), (3, False), (4, True)],
    ids=["cap-1", "cap", "cap+1"],
)
def test_xlsx_local_row_cap_boundary(cap3x2: tuple[int, int], total_rows: int, noted: bool) -> None:
    rows = [["a", "b"]] + [[str(index), str(index)] for index in range(total_rows - 1)]
    extracted = extract_xlsx_tables(_xlsx_zip(sheets={1: _sheet_xml(rows)}))
    assert extracted.parsed
    assert (extracted.parse_note == "xlsx rows truncated at 3") is noted
    assert (extracted.parse_note is None) is (not noted)
    assert len(extracted.tables[0]["_source_rows"]) == min(total_rows, 3)


@pytest.mark.parametrize(
    ("total_rows", "noted"),
    [(9_999, False), (10_000, False), (10_001, True)],
    ids=["cap-1", "cap", "cap+1"],
)
def test_xlsx_default_row_cap_boundary(total_rows: int, noted: bool) -> None:
    rows = [["h"]] + [[str(index)] for index in range(total_rows - 1)]
    extracted = extract_xlsx_tables(_xlsx_zip(sheets={1: _sheet_xml(rows)}))
    assert extracted.parsed
    assert (extracted.parse_note == "xlsx rows truncated at 10000") is noted
    assert (extracted.parse_note is None) is (not noted)
    assert len(extracted.tables[0]["_source_rows"]) == min(total_rows, 10_000)


@pytest.mark.parametrize(
    ("width", "noted"),
    [(1, False), (2, False), (3, True)],
    ids=["cap-1", "cap", "cap+1"],
)
def test_xlsx_local_column_cap_boundary(cap3x2: tuple[int, int], width: int, noted: bool) -> None:
    rows = [[f"v{index}" for index in range(width)]]
    extracted = extract_xlsx_tables(_xlsx_zip(sheets={1: _sheet_xml(rows)}))
    assert extracted.parsed
    assert (extracted.parse_note == "xlsx columns truncated at 2") is noted
    assert (extracted.parse_note is None) is (not noted)
    assert len(extracted.tables[0]["headers"]) == min(width, 2)


@pytest.mark.parametrize(
    ("width", "noted"),
    [(255, False), (256, False), (257, True)],
    ids=["cap-1", "cap", "cap+1"],
)
def test_xlsx_default_column_cap_boundary(width: int, noted: bool) -> None:
    rows = [[f"v{index}" for index in range(width)]]
    extracted = extract_xlsx_tables(_xlsx_zip(sheets={1: _sheet_xml(rows)}))
    assert extracted.parsed
    assert (extracted.parse_note == "xlsx columns truncated at 256") is noted
    assert (extracted.parse_note is None) is (not noted)
    assert len(extracted.tables[0]["headers"]) == min(width, 256)


@pytest.mark.parametrize(
    ("payload", "noted"),
    [("0", True), (" ", True), ("\t", True), ("", False)],
    ids=["zero", "space", "tab", "empty"],
)
def test_xlsx_payload_past_row_cap_presence(
    cap3x2: tuple[int, int], payload: str, noted: bool
) -> None:
    """Row-cap lookahead presence: '0' and whitespace count, '' does not —
    the same presence rule the in-cap adoption loop uses."""

    rows = [["a", "b"], ["1", "2"], ["3", "4"], [payload]]
    extracted = extract_xlsx_tables(_xlsx_zip(sheets={1: _sheet_xml(rows)}))
    assert extracted.parsed
    assert (extracted.parse_note == "xlsx rows truncated at 3") is noted
    assert (extracted.parse_note is None) is (not noted)
    assert len(extracted.tables[0]["rows"]) == 2


@pytest.mark.parametrize(
    ("payload", "noted"),
    [("0", True), (" ", True), ("\t", True), ("", False)],
    ids=["zero", "space", "tab", "empty"],
)
def test_xlsx_payload_past_column_cap_presence(
    cap3x2: tuple[int, int], payload: str, noted: bool
) -> None:
    """Column-cap presence: a non-empty value at/after the cap is noted, an
    empty cell past the cap is not."""

    extracted = extract_xlsx_tables(_xlsx_zip(sheets={1: _sheet_xml([["a", "b", payload]])}))
    assert extracted.parsed
    assert (extracted.parse_note == "xlsx columns truncated at 2") is noted
    assert (extracted.parse_note is None) is (not noted)
    assert len(extracted.tables[0]["headers"]) == 2
    assert extracted.tables[0]["_source_rows"] == [["a", "b"]]
