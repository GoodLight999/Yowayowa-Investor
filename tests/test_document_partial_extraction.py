"""Partial extraction must be visible, never silent.

Focused tests for SILENT_FAILURE_AUDIT_20261023 #9/#10: an interrupted
pdfminer page walk (``extract_pdf_tables``) and a missing
``xl/worksheets/sheetN.xml`` part (``extract_xlsx_tables``) used to pass as a
complete parse. Both degradations must now surface through the existing
``ExtractedDocument.parse_note`` with fixed wording, without changing any
extracted value and without new models, columns or surfaces.

Contract pinned here
--------------------
- ``parsed`` keeps its meaning "usable extraction exists"; the notes add
  incompleteness, they do not turn it into a completeness guarantee.
- Normal paths stay note-free: no tables, an empty-but-present sheet, and a
  missing optional pdfminer extra must not grow warnings.
- Fixed notes appear exactly once, never carry raw exception text, and coexist
  with the existing "text capped" note.
- Direct ``extract_pdf_tables`` callers (list return, partial tables on a
  traversal error) behave exactly as before.
"""

from __future__ import annotations

import io
import sys
import types
import zipfile
from typing import Any

import pytest

from yowayowa.acquisition.documents import (
    extract_document,
    extract_pdf_document,
    extract_pdf_tables,
    extract_xlsx_tables,
)

PDF_INCOMPLETE_NOTE = "pdf table extraction incomplete"
XLSX_MISSING_PREFIX = "xlsx worksheet parts missing"


# --------------------------------------------------------------- xlsx builders


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


def _xlsx_zip(
    *,
    workbook: str | None,
    sheets: dict[int, str] | None = None,
) -> bytes:
    """Minimal xlsx: workbook sheet list plus the worksheet parts that exist.

    ``workbook=None`` omits the workbook part entirely (absent-part edge).
    """

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        if workbook is not None:
            archive.writestr("xl/workbook.xml", workbook)
        for index, xml in (sheets or {}).items():
            archive.writestr(f"xl/worksheets/sheet{index}.xml", xml)
    return buffer.getvalue()


def _workbook(*names: str) -> str:
    entries = "".join(f'<sheet name="{name}"/>' for name in names)
    return f"<workbook><sheets>{entries}</sheets></workbook>"


# --------------------------------------------------------------- pdfminer stub


class _PdfCalls:
    def __init__(self) -> None:
        self.extract_text = 0
        self.extract_pages = 0


class _FakeLine:
    def __init__(self, text: str, center_x: float, baseline_y: float) -> None:
        self._text = text
        self.x0 = center_x - 0.5
        self.x1 = center_x + 0.5
        self.y0 = baseline_y - 0.5
        self.y1 = baseline_y

    def get_text(self) -> str:
        return self._text


def _install_pdf_stub(
    monkeypatch: pytest.MonkeyPatch,
    *,
    text: object = "",
    page_factory: object = None,
) -> tuple[_PdfCalls, type]:
    """Put a fake ``pdfminer`` in ``sys.modules`` (lazy imports hit the stub)."""

    calls = _PdfCalls()
    package = types.ModuleType("pdfminer")
    high_level = types.ModuleType("pdfminer.high_level")
    layout = types.ModuleType("pdfminer.layout")
    package.high_level = high_level
    package.layout = layout

    class LTTextContainer:
        def __init__(self, lines: object = ()) -> None:
            self.lines = list(lines)  # type: ignore[call-overload]

        def __iter__(self) -> object:
            return iter(self.lines)

    def extract_text(stream: object) -> object:
        calls.extract_text += 1
        return text

    def extract_pages(stream: object) -> object:
        calls.extract_pages += 1
        assert callable(page_factory)
        return page_factory()

    high_level.extract_text = extract_text
    if page_factory is not None:
        high_level.extract_pages = extract_pages
    layout.LTTextContainer = LTTextContainer
    monkeypatch.setitem(sys.modules, "pdfminer", package)
    monkeypatch.setitem(sys.modules, "pdfminer.high_level", high_level)
    monkeypatch.setitem(sys.modules, "pdfminer.layout", layout)
    return calls, LTTextContainer


_TWO_ROW_PAGE = (("h1", 10.0, 100.0), ("h2", 100.0, 100.0), ("v1", 10.0, 50.0), ("v2", 100.0, 50.0))


def _table_page(container: type) -> list[Any]:
    return [container([_FakeLine(text, x, y) for text, x, y in _TWO_ROW_PAGE])]


# ------------------------------------------------------------------ PDF cases


def test_pdf_document_with_tables_keeps_note_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _, container = _install_pdf_stub(
        monkeypatch, text="table text", page_factory=lambda: [_table_page(container)]
    )
    document = extract_pdf_document(b"%PDF-1.4")
    assert document.parsed
    assert len(document.tables) == 1
    assert document.tables[0]["headers"] == ["h1", "h2"]
    assert document.parse_note is None


def test_pdf_document_without_tables_keeps_note_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_pdf_stub(monkeypatch, text="plain text", page_factory=lambda: [])
    document = extract_pdf_document(b"%PDF-1.4")
    assert document.parsed
    assert document.tables == []
    assert document.parse_note is None


def test_pdf_first_page_traversal_failure_records_incomplete_note(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def pages() -> object:
        raise ValueError("corrupt xref on page 1")

    _install_pdf_stub(monkeypatch, text="readable text", page_factory=pages)
    document = extract_pdf_document(b"%PDF-1.4")
    assert document.parsed  # the text layer is usable, so parsed stays True
    assert document.tables == []
    assert document.parse_note == PDF_INCOMPLETE_NOTE
    assert "corrupt" not in (document.parse_note or "")
    assert "ValueError" not in (document.parse_note or "")


def test_pdf_mid_traversal_failure_notes_incomplete_and_keeps_partial_tables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def pages() -> object:
        yield _table_page(container)
        raise ValueError("corrupt content stream on page 2")

    _, container = _install_pdf_stub(monkeypatch, text="readable text", page_factory=pages)
    document = extract_pdf_document(b"%PDF-1.4")
    assert document.parsed
    assert len(document.tables) == 1
    assert document.tables[0]["rows"] == [{"h1": "v1", "h2": "v2"}]
    assert document.parse_note == PDF_INCOMPLETE_NOTE
    assert (document.parse_note or "").count(PDF_INCOMPLETE_NOTE) == 1
    assert "corrupt" not in (document.parse_note or "")


def test_pdf_direct_tables_call_keeps_partial_list_without_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Existing direct callers see the old contract: list out, partial on error."""

    def pages() -> object:
        yield _table_page(container)
        raise ValueError("mid-walk failure")

    _, container = _install_pdf_stub(monkeypatch, text="readable text", page_factory=pages)
    direct = extract_pdf_tables(b"%PDF-1.4")
    document = extract_pdf_document(b"%PDF-1.4")
    assert isinstance(direct, list)
    assert len(direct) == 1
    assert document.tables == direct


def test_pdf_incomplete_note_coexists_with_text_cap_note(monkeypatch: pytest.MonkeyPatch) -> None:
    def pages() -> object:
        raise ValueError("traversal stopped on the first page")

    _install_pdf_stub(monkeypatch, text="x" * 200_001, page_factory=pages)
    document = extract_pdf_document(b"%PDF-1.4")
    assert document.parsed
    note = document.parse_note
    assert note is not None
    assert note.count("text capped at 200000 chars") == 1
    assert note.count(PDF_INCOMPLETE_NOTE) == 1
    assert note == "text capped at 200000 chars; pdf table extraction incomplete"


def test_pdf_missing_optional_extra_grows_no_incomplete_note(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "pdfminer.high_level", None)
    document = extract_pdf_document(b"%PDF-1.4")
    assert not document.parsed
    assert document.parse_note is not None
    assert "operator-ir" in document.parse_note
    assert PDF_INCOMPLETE_NOTE not in document.parse_note


def test_pdf_same_bytes_reread_is_consistent_and_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def pages() -> object:
        yield _table_page(container)
        raise ValueError("mid-walk failure")

    calls, container = _install_pdf_stub(monkeypatch, text="readable text", page_factory=pages)
    first = extract_pdf_document(b"%PDF-1.4")
    second = extract_pdf_document(b"%PDF-1.4")
    assert first.parse_note == second.parse_note == PDF_INCOMPLETE_NOTE
    assert first.tables == second.tables
    assert first.text_chars == second.text_chars
    assert calls.extract_pages == 2  # one table pass per document, no retry


def test_extract_document_carries_pdf_incomplete_note(monkeypatch: pytest.MonkeyPatch) -> None:
    """The service entry point (``extract_document``) transports the note."""

    def pages() -> object:
        raise ValueError("traversal stopped")

    _install_pdf_stub(monkeypatch, text="readable text", page_factory=pages)
    extracted = extract_document(
        content=b"%PDF-1.4", content_type="application/pdf", filename="tanshin.pdf"
    )
    assert extracted.parsed
    assert extracted.parse_note == PDF_INCOMPLETE_NOTE


# ----------------------------------------------------------------- XLSX cases


def test_xlsx_all_declared_sheets_readable_keeps_note_none() -> None:
    content = _xlsx_zip(
        workbook=_workbook("s1", "s2"),
        sheets={
            1: _sheet_xml([["kpi"], ["1200"]]),
            2: _sheet_xml([["kpi"], ["7"]]),
        },
    )
    extracted = extract_xlsx_tables(content)
    assert extracted.parsed
    assert [table["name"] for table in extracted.tables] == ["s1", "s2"]
    assert extracted.parse_note is None


def test_xlsx_empty_normal_sheet_is_not_counted_missing() -> None:
    content = _xlsx_zip(
        workbook=_workbook("empty", "data"),
        sheets={1: _sheet_xml([]), 2: _sheet_xml([["kpi"], ["1200"]])},
    )
    extracted = extract_xlsx_tables(content)
    assert extracted.parsed
    assert [table["name"] for table in extracted.tables] == ["data"]
    assert extracted.parse_note is None


def test_xlsx_missing_parts_are_counted_and_readable_sheets_kept() -> None:
    content = _xlsx_zip(
        workbook=_workbook("gone1", "gone2", "present"),
        sheets={3: _sheet_xml([["kpi"], ["1200"]])},
    )
    extracted = extract_xlsx_tables(content)
    assert extracted.parsed  # usable extraction exists -> parsed stays True
    assert [table["name"] for table in extracted.tables] == ["present"]
    assert extracted.tables[0]["rows"] == [{"kpi": "1200"}]
    assert extracted.parse_note == f"{XLSX_MISSING_PREFIX}: 2"


def test_xlsx_all_parts_missing_fails_closed_with_missing_reason() -> None:
    content = _xlsx_zip(workbook=_workbook("gone1", "gone2"), sheets={})
    extracted = extract_xlsx_tables(content)
    assert not extracted.parsed
    assert extracted.tables == []
    assert extracted.parse_note == f"xlsx contained no readable sheets; {XLSX_MISSING_PREFIX}: 2"


def test_xlsx_no_declared_sheets_keeps_existing_note_exactly() -> None:
    content = _xlsx_zip(workbook=_workbook(), sheets={})
    extracted = extract_xlsx_tables(content)
    assert not extracted.parsed
    assert extracted.parse_note == "xlsx contained no readable sheets"


def test_xlsx_missing_workbook_part_note_unchanged() -> None:
    content = _xlsx_zip(workbook=None, sheets={1: _sheet_xml([["kpi"], ["1200"]])})
    extracted = extract_xlsx_tables(content)
    assert not extracted.parsed
    assert extracted.parse_note == "xlsx contained no readable sheets"


def test_xlsx_same_bytes_reread_is_consistent() -> None:
    content = _xlsx_zip(
        workbook=_workbook("gone", "present"),
        sheets={2: _sheet_xml([["kpi"], ["1200"]])},
    )
    first = extract_xlsx_tables(content)
    second = extract_xlsx_tables(content)
    assert first.parse_note == second.parse_note == f"{XLSX_MISSING_PREFIX}: 1"
    assert first.tables == second.tables
    assert first.parsed == second.parsed


def test_extract_document_carries_xlsx_missing_note() -> None:
    """The service entry point (``extract_document``) transports the note."""

    content = _xlsx_zip(
        workbook=_workbook("gone", "present"),
        sheets={2: _sheet_xml([["kpi"], ["1200"]])},
    )
    extracted = extract_document(content=content, content_type=None, filename="summary.xlsx")
    assert extracted.parsed
    assert extracted.parse_note == f"{XLSX_MISSING_PREFIX}: 1"
