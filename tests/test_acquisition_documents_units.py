"""Boundary unit tests for ``yowayowa.acquisition.documents``.

This card is tests-only: ``src/`` is read-only. Every case below pins the
*current* behaviour of the extraction layer, and the genuine defects found are
recorded as strict ``xfail`` cases so the suite stays green while the defect
stays visible (fixing one makes the XPASS fail loudly, forcing a test update).

Boundary table
--------------
| boundary            | case                                            | expected here |
|---------------------|-------------------------------------------------|---------------|
| duplicate identity  | same file via filename / URL / query string      | one extension |
| duplicate identity  | uppercase / dotted names (``v2.PDF``)            | same extractor|
| duplicate identity  | conflicting filename ext vs content-type         | extension wins|
| duplicate identity  | URL fragment (``brief.pdf#page=2``)              | same extension|
| duplicate identity  | repeated extraction is pure, no cache            | equal, distinct|
| naming              | no extension / dotfile / trailing dot            | None, None, "."|
| naming              | URL tail without extension or with trailing "/"  | None          |
| missing fields      | short row, empty header, long row                | "", "A", dropped|
| missing fields      | duplicate header (CSV/XLSX)                      | kept (a.2)    |
| missing fields      | XLSX shared-string ref without shared table      | fail closed    |
| missing fields      | XLSX without workbook / sheet part               | fail closed    |
| encoding            | UTF-8 BOM, cp932 fallback, undecodable bytes     | BOM/decoded/fail|
| encoding            | HTML with invalid UTF-8                          | replacement char|
| size                | PDF text layer past 200 000 chars                | capped + note |
| size                | CSV/XLSX row and column caps                     | capped + note |
| layout              | PDF row tolerance 4.0 / 4.1 pt                   | joined / split|
| layout              | PDF running-average drift (100/104/108)          | 2 rows        |
| layout              | PDF column gap under 12 pt                       | DEFECT (xfail)|
| layout              | PDF table cap, mid-stream error                  | 200, partial  |
| double fetch        | any extractor touches the network                | never         |
| double fetch        | PDF text / layout extractor call count           | exactly one   |

No test reaches the network: the PDF layer is exercised through an in-process
``pdfminer`` stub (installed in ``sys.modules``) or through the real installed
parser only via short, deterministic byte strings.
"""

from __future__ import annotations

import io
import socket
import sys
import types
import zipfile

import pytest

from yowayowa.acquisition.documents import (
    extension_of,
    extract_csv_tables,
    extract_document,
    extract_html_tables,
    extract_pdf_document,
    extract_pdf_tables,
    extract_pdf_text,
    extract_xlsx_tables,
)

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


def _shared_cell_sheet(index: int) -> str:
    """One sheet whose single cell is a shared-string reference."""

    return (
        "<worksheet><sheetData><row r='1'>"
        f"<c r='A1' t='s'><v>{index}</v></c>"
        "</row></sheetData></worksheet>"
    )


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


class _PdfCalls:
    def __init__(self) -> None:
        self.extract_text = 0
        self.extract_pages = 0


def _install_pdf_stub(
    monkeypatch: pytest.MonkeyPatch,
    *,
    text: object = "",
    error: BaseException | None = None,
    page_factory: object = None,
) -> tuple[_PdfCalls, type]:
    """Put a fake ``pdfminer`` in ``sys.modules`` and return its counters.

    Extraction imports pdfminer lazily (``from pdfminer.high_level import ...``),
    so installing the module here exercises the real code path without parsing
    a PDF: deterministic, fast and network-free.
    """

    calls = _PdfCalls()
    package = types.ModuleType("pdfminer")
    high_level = types.ModuleType("pdfminer.high_level")
    layout = types.ModuleType("pdfminer.layout")
    package.high_level = high_level
    package.layout = layout

    class LTTextContainer:
        def __init__(self, lines: object = ()) -> None:
            self.lines = list(lines)  # type: ignore[call-overload]

        def __iter__(self):
            return iter(self.lines)

    def extract_text(stream: object) -> object:
        calls.extract_text += 1
        if error is not None:
            raise error
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


class _FakeLine:
    def __init__(self, text: str, center_x: float, baseline_y: float) -> None:
        self._text = text
        self.x0 = center_x - 0.5
        self.x1 = center_x + 0.5
        self.y0 = baseline_y - 0.5
        self.y1 = baseline_y

    def get_text(self) -> str:
        return self._text


def _stub_page(container_cls: type, *cells: tuple[str, float, float]) -> list:
    return [container_cls([_FakeLine(text, x, y) for text, x, y in cells])]


_TWO_ROW_PAGE = (
    ("h1", 10.0, 100.0),
    ("h2", 100.0, 100.0),
    ("v1", 10.0, 50.0),
    ("v2", 100.0, 50.0),
)
_CSV = b"a,b\n1,2\n"

# ------------------------------------------------- identity and naming


@pytest.mark.parametrize(
    ("filename", "url", "expected"),
    [
        ("report.v2.pdf", None, ".pdf"),
        ("REPORT.CSV", None, ".csv"),
        ("archive.tar.gz", None, ".gz"),
        (".hidden.csv", None, ".csv"),
        ("report.", None, "."),
        ("noext", None, None),
        (".pdf", None, None),
        ("", None, None),
        (None, None, None),
        (None, "https://example.co.jp/ir/brief.pdf", ".pdf"),
        (None, "https://example.co.jp/ir/brief.PDF?download=1", ".pdf"),
        (None, "https://example.co.jp/ir/", None),
        (None, "https://example.co.jp/ir/brief", None),
        (None, "https://example.co.jp/ir/.csv", None),
        (None, "", None),
    ],
)
def test_extension_of_naming_boundaries(
    filename: str | None, url: str | None, expected: str | None
) -> None:
    assert extension_of(filename, url) == expected


def test_query_string_keeps_document_identity() -> None:
    """The same document fetched with/without a query string keeps one format."""

    plain = extract_document(content=_CSV, content_type=None, filename="summary.csv")
    with_query = extract_document(
        content=_CSV,
        content_type=None,
        filename=None,
        url="https://example.co.jp/ir/summary.csv?token=abc&v=2",
    )
    assert plain.format == with_query.format == "csv"
    assert plain.parsed and with_query.parsed
    assert plain.tables == with_query.tables


def test_url_fragment_does_not_change_document_identity() -> None:
    assert extension_of(None, "https://example.co.jp/ir/brief.pdf#page=2") == ".pdf"


def test_filename_extension_outranks_conflicting_content_type() -> None:
    """A mislabelled content-type must not re-classify a known document."""

    extracted = extract_document(
        content=_CSV,
        content_type="text/csv",
        filename="filing.pdf",
    )
    assert extracted.format == "pdf"
    assert not extracted.parsed


@pytest.mark.parametrize(
    ("content_type", "expected"),
    [
        ("TEXT/CSV", "csv"),
        ("text/csv; charset=utf-8", "csv"),
        ("application/pdf", "pdf"),
        ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"),
        ("text/html; charset=cp932", "html"),
    ],
)
def test_content_type_dispatch_is_case_insensitive(content_type: str, expected: str) -> None:
    content = {
        "csv": _CSV,
        "pdf": b"%PDF-1.4 truncated",
        "xlsx": _xlsx_zip(),
        "html": b"<table><tr><th>a</th></tr><tr><td>1</td></tr></table>",
    }[expected]
    extracted = extract_document(content=content, content_type=content_type, filename="download")
    assert extracted.format == expected


def test_unknown_format_fails_closed_with_note() -> None:
    extracted = extract_document(
        content=b"\x00\x01binary",
        content_type="application/zip",
        filename="bundle.zip",
    )
    assert extracted.format == "unknown"
    assert not extracted.parsed
    assert extracted.parse_note == "no extractor for this document format"
    assert extracted.tables == []


# ------------------------------------------------------------ encoding


def test_csv_utf8_bom_is_stripped_from_the_first_header() -> None:
    extracted = extract_csv_tables(b"\xef\xbb\xbf" + "項目,値\n受注残,1200\n".encode())
    assert extracted.parsed
    assert extracted.tables[0]["headers"] == ["項目", "値"]
    assert extracted.tables[0]["rows"] == [{"項目": "受注残", "値": "1200"}]


def test_csv_cp932_is_used_only_when_utf8_fails() -> None:
    """cp932-only bytes decode through the fallback without mojibake."""

    extracted = extract_csv_tables("項目,値\n受注残,1200\n".encode("cp932"))
    assert extracted.parsed
    assert extracted.tables[0]["headers"] == ["項目", "値"]

    # valid utf-8 always wins: the BOM-less branch must not try cp932 first.
    utf8 = extract_csv_tables("項目,値\n受注残,1200\n".encode())
    assert utf8.tables[0]["headers"] == ["項目", "値"]
    assert utf8.parse_note is None


def test_csv_undecodable_bytes_fail_closed_without_inventing_text() -> None:
    extracted = extract_csv_tables(b"\xff\xfe\x00\x81")
    assert not extracted.parsed
    assert extracted.tables == []
    assert extracted.parse_note is not None
    assert "csv decode failed" in extracted.parse_note


def test_html_invalid_utf8_degrades_with_replacement_character() -> None:
    html = b"<table><tr><th>k</th></tr><tr><td>\xff</td></tr></table>"
    extracted = extract_document(content=html, content_type="text/html", filename="page.html")
    assert extracted.parsed
    assert extracted.tables[0]["rows"][0]["k"] == "\ufffd"


# ------------------------------------------------------- missing fields


def test_csv_short_row_fills_missing_cells_with_empty_string() -> None:
    extracted = extract_csv_tables(b"a,b,c\n1,2\n")
    assert extracted.tables[0]["rows"] == [{"a": "1", "b": "2", "c": ""}]


def test_csv_long_row_keeps_extra_cells_only_in_source_rows() -> None:
    extracted = extract_csv_tables(b"a,b\n1,2,3\n")
    table = extracted.tables[0]
    assert table["rows"] == [{"a": "1", "b": "2"}]
    assert table["_source_rows"][1] == ["1", "2", "3"]


def test_csv_empty_header_falls_back_to_column_letter() -> None:
    extracted = extract_csv_tables(b",b\n1,2\n")
    assert extracted.tables[0]["rows"] == [{"A": "1", "b": "2"}]


def test_csv_header_only_file_parses_with_zero_rows() -> None:
    extracted = extract_csv_tables(b"a,b\n")
    assert extracted.parsed
    assert extracted.tables[0]["rows"] == []


def test_csv_whitespace_only_file_fails_closed() -> None:
    extracted = extract_csv_tables(b" , \n\n\t\n")
    assert not extracted.parsed
    assert extracted.parse_note == "csv contained no readable rows"


def test_duplicate_headers_keep_both_columns_in_csv_and_xlsx() -> None:
    csv_doc = extract_csv_tables(b"a,a\n1,2\n")
    assert csv_doc.tables[0]["rows"] == [{"a": "1", "a.2": "2"}]

    xlsx_doc = extract_xlsx_tables(_xlsx_zip(sheets={1: _sheet_xml([["a", "a"], ["1", "2"]])}))
    assert xlsx_doc.tables[0]["rows"] == [{"a": "1", "a.2": "2"}]


def test_xlsx_empty_header_falls_back_to_column_letter() -> None:
    extracted = extract_xlsx_tables(_xlsx_zip(sheets={1: _sheet_xml([["", "b"], ["1", "2"]])}))
    assert extracted.tables[0]["rows"] == [{"A": "1", "b": "2"}]


def test_xlsx_short_row_fills_missing_cells() -> None:
    extracted = extract_xlsx_tables(_xlsx_zip(sheets={1: _sheet_xml([["a", "b"], ["1"]])}))
    assert extracted.tables[0]["rows"] == [{"a": "1", "b": ""}]


def test_xlsx_unresolvable_shared_string_index_fails_closed() -> None:
    # Fixed (t_04c17af3): an unresolvable t="s" reference now fails the whole
    # document closed instead of substituting the raw reference as cell text.
    no_shared = _xlsx_zip(sheets={1: _shared_cell_sheet(0)})
    out_of_range = _xlsx_zip(
        shared="<sst><si><t>x</t></si></sst>",
        sheets={1: _shared_cell_sheet(5)},
    )
    assert not extract_xlsx_tables(no_shared).parsed
    assert not extract_xlsx_tables(out_of_range).parsed


def test_xlsx_without_workbook_fails_closed() -> None:
    extracted = extract_xlsx_tables(_xlsx_zip(workbook=None))
    assert not extracted.parsed
    assert extracted.parse_note == "xlsx contained no readable sheets"


def test_xlsx_missing_sheet_part_is_skipped() -> None:
    content = _xlsx_zip(
        workbook=(
            '<workbook><sheets><sheet name="missing"/><sheet name="present"/></sheets></workbook>'
        ),
        sheets={2: _sheet_xml([["a"], ["1"]])},
    )
    extracted = extract_xlsx_tables(content)
    assert extracted.parsed
    assert [table["name"] for table in extracted.tables] == ["present"]


def test_xlsx_not_a_zip_fails_closed() -> None:
    extracted = extract_xlsx_tables(b"not a zip")
    assert not extracted.parsed
    assert extracted.parse_note == "xlsx open failed: BadZipFile"


def test_xlsx_malformed_xml_fails_closed() -> None:
    extracted = extract_xlsx_tables(_xlsx_zip(workbook="<workbook><sheets>"))
    assert not extracted.parsed
    assert extracted.parse_note is not None
    assert extracted.parse_note.startswith("xlsx parse failed:")


def test_xlsx_cell_without_reference_collides_on_column_a() -> None:
    """Missing ``r`` attributes map every cell to column 0: the last one wins."""

    sheet = (
        "<worksheet><sheetData><row><c><v>1</v></c><c><v>2</v></c></row></sheetData></worksheet>"
    )
    content = _xlsx_zip(sheets={1: sheet})
    extracted = extract_xlsx_tables(content)
    assert extracted.tables[0]["_source_rows"] == [["2"]]


def test_html_duplicate_headers_are_suffixed() -> None:
    html = "<table><tr><th>a</th><th>a</th></tr><tr><td>1</td><td>2</td></tr></table>"
    extracted = extract_html_tables(html)
    assert extracted.tables[0]["rows"] == [{"a": "1", "a.2": "2"}]


def test_html_short_and_long_rows_fill_or_drop_cells() -> None:
    html = (
        "<table><tr><th>a</th><th>b</th></tr>"
        "<tr><td>1</td></tr>"
        "<tr><td>3</td><td>4</td><td>5</td></tr></table>"
    )
    extracted = extract_html_tables(html)
    assert extracted.tables[0]["rows"] == [{"a": "1", "b": ""}, {"a": "3", "b": "4"}]


def test_html_unclosed_table_still_parses() -> None:
    extracted = extract_html_tables("<table><tr><th>a</th></tr><tr><td>1</td></tr>")
    assert extracted.parsed
    assert extracted.tables[0]["rows"] == [{"a": "1"}]


def test_html_without_tables_is_parsed_with_zero_tables() -> None:
    extracted = extract_html_tables("<html><body>no tables here</body></html>")
    assert extracted.parsed
    assert extracted.tables == []


def test_pdf_unavailable_extra_fails_closed_with_install_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "pdfminer.high_level", None)
    extracted = extract_pdf_text(b"%PDF-1.4")
    assert not extracted.parsed
    assert extracted.parse_note is not None
    assert "operator-ir" in extracted.parse_note


def test_pdf_parser_exception_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_pdf_stub(monkeypatch, error=ValueError("bad xref"))
    extracted = extract_pdf_text(b"%PDF-1.4")
    assert not extracted.parsed
    assert extracted.parse_note == "pdf parse failed"


def test_pdf_non_text_result_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_pdf_stub(monkeypatch, text=b"bytes, not str")
    extracted = extract_pdf_text(b"%PDF-1.4")
    assert not extracted.parsed
    assert extracted.parse_note == "pdf text layer was not text"


def test_pdf_whitespace_only_text_layer_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_pdf_stub(monkeypatch, text="\f\f\n\f")
    extracted = extract_pdf_text(b"%PDF-1.4")
    assert not extracted.parsed
    assert extracted.parse_note is not None
    assert "text layer" in extracted.parse_note


# ---------------------------------------------------------------- size


@pytest.mark.parametrize(
    ("length", "capped", "noted"),
    [
        (199_999, False, False),
        (200_000, False, False),
        (200_001, True, True),
        (1_000_000, True, True),
    ],
)
def test_pdf_text_cap_boundary(
    monkeypatch: pytest.MonkeyPatch, length: int, capped: bool, noted: bool
) -> None:
    _install_pdf_stub(monkeypatch, text="x" * length)
    extracted = extract_pdf_text(b"%PDF-1.4")
    assert extracted.parsed
    assert extracted.text_chars == length
    assert len(extracted.text or "") == (200_000 if capped else length)
    assert (extracted.parse_note == "text capped at 200000 chars") is noted


def test_csv_row_cap_truncates_at_ten_thousand_rows() -> None:
    lines = ["a,b"] + [f"{index},{index}" for index in range(10_100)]
    extracted = extract_csv_tables("\n".join(lines).encode())
    table = extracted.tables[0]
    assert extracted.parsed
    assert len(table["_source_rows"]) == 10_000
    assert len(table["rows"]) == 9_999


def test_csv_column_cap_truncates_at_256_columns() -> None:
    header = ",".join(str(index) for index in range(300))
    extracted = extract_csv_tables(f"{header}\n".encode())
    assert extracted.parsed
    assert len(extracted.tables[0]["headers"]) == 256


def test_size_truncation_is_reported_in_the_parse_note() -> None:
    """Fixed (t_3501871f): row/column truncation now records a parse note."""

    lines = ["a,b"] + [f"{index},{index}" for index in range(10_100)]
    assert extract_csv_tables("\n".join(lines).encode()).parse_note is not None


def test_xlsx_row_cap_truncates_at_ten_thousand_rows() -> None:
    rows = [["h"]] + [[str(index)] for index in range(10_100)]
    extracted = extract_xlsx_tables(_xlsx_zip(sheets={1: _sheet_xml(rows)}))
    assert extracted.parsed
    assert len(extracted.tables[0]["_source_rows"]) == 10_000
    assert len(extracted.tables[0]["rows"]) == 9_999


def test_xlsx_column_cap_drops_cells_past_256() -> None:
    sheet = (
        "<worksheet><sheetData><row r='1'>"
        "<c r='A1' t='inlineStr'><is><t>kept</t></is></c>"
        "<c r='IW1' t='inlineStr'><is><t>dropped</t></is></c>"
        "</row></sheetData></worksheet>"
    )
    extracted = extract_xlsx_tables(_xlsx_zip(sheets={1: sheet}))
    headers = extracted.tables[0]["headers"]
    assert len(headers) == 256
    assert headers[0] == "kept"
    assert "dropped" not in headers


# -------------------------------------------------------- PDF layout


def test_pdf_table_reconstruction_from_text_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    calls, container = _install_pdf_stub(
        monkeypatch, page_factory=lambda: [_stub_page(container, *_TWO_ROW_PAGE)]
    )
    tables = extract_pdf_tables(b"%PDF-1.4")
    assert calls.extract_pages == 1
    assert len(tables) == 1
    table = tables[0]
    assert table["headers"] == ["h1", "h2"]
    assert table["rows"] == [{"h1": "v1", "h2": "v2"}]
    assert table["header_xs"] == [10.0, 100.0]
    assert table["row_xs"] == [[10.0, 100.0]]
    assert table["cells"] == [["v1", "v2"]]


@pytest.mark.parametrize(("second_y", "joined"), [(104.0, True), (104.1, False)])
def test_pdf_row_tolerance_boundary(
    monkeypatch: pytest.MonkeyPatch, second_y: float, joined: bool
) -> None:
    """Exactly 4 pt joins a row; 4.1 pt starts a new one.

    Runs are ordered by descending y, so on the split branch the lower baseline
    (y=100) becomes a data row and the header is the upper run alone.
    """

    cells = (
        ("a", 10.0, 100.0),
        ("b" if joined else "c", 10.0, second_y),
        ("z", 10.0, 50.0),
    )
    _, container = _install_pdf_stub(
        monkeypatch, page_factory=lambda: [_stub_page(container, *cells)]
    )
    tables = extract_pdf_tables(b"%PDF-1.4")
    assert tables[0]["headers"] == (["a", "b"] if joined else ["c"])
    assert len(tables[0]["rows"]) == (1 if joined else 2)


def test_pdf_row_grouping_drifts_with_running_average(monkeypatch: pytest.MonkeyPatch) -> None:
    """Row membership compares against the running mean, not the first baseline.

    Runs at y=108/104 join (the mean becomes 106), so the next run at y=100 is
    6 pt away and starts a new row even though it is only 4 pt below y=104.
    """

    cells = (("a", 10.0, 100.0), ("b", 10.0, 104.0), ("c", 10.0, 108.0), ("z", 10.0, 40.0))
    _, container = _install_pdf_stub(
        monkeypatch, page_factory=lambda: [_stub_page(container, *cells)]
    )
    tables = extract_pdf_tables(b"%PDF-1.4")
    assert tables[0]["headers"] == ["b", "c"]
    assert len(tables[0]["rows"]) == 2


@pytest.mark.xfail(
    strict=True,
    reason=(
        "DEFECT: _COLUMN_TOLERANCE is declared and documented but never read, so text runs "
        "5 pt apart become two columns although the module docstring promises a split only "
        "past 12 pt. Repro: two header runs at x=10 and x=15 yield headers ['a', 'b']."
    ),
)
def test_pdf_column_gap_tolerance_is_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    cells = (("a", 10.0, 100.0), ("b", 15.0, 100.0), ("v", 10.0, 50.0))
    _, container = _install_pdf_stub(
        monkeypatch, page_factory=lambda: [_stub_page(container, *cells)]
    )
    tables = extract_pdf_tables(b"%PDF-1.4")
    assert len(tables[0]["headers"]) == 1


def test_pdf_table_cap_stops_at_two_hundred(monkeypatch: pytest.MonkeyPatch) -> None:
    def pages() -> object:
        for _ in range(250):
            yield _stub_page(container, *_TWO_ROW_PAGE)

    _, container = _install_pdf_stub(monkeypatch, page_factory=pages)
    tables = extract_pdf_tables(b"%PDF-1.4")
    assert len(tables) == 200


def test_pdf_layout_error_returns_already_collected_tables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def pages() -> object:
        yield _stub_page(container, *_TWO_ROW_PAGE)
        raise ValueError("corrupt content stream")

    _, container = _install_pdf_stub(monkeypatch, page_factory=pages)
    tables = extract_pdf_tables(b"%PDF-1.4")
    assert len(tables) == 1
    assert tables[0]["rows"] == [{"h1": "v1", "h2": "v2"}]


# -------------------------------------------------- no double fetch


def test_extraction_never_touches_the_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def _blocked(*args: object, **kwargs: object) -> None:
        raise AssertionError("document extraction attempted network access")

    monkeypatch.setattr(socket, "socket", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)

    assert extract_csv_tables(_CSV).parsed
    assert extract_html_tables("<table><tr><th>a</th></tr><tr><td>1</td></tr></table>").parsed
    assert extract_xlsx_tables(_xlsx_zip()).parsed
    assert extract_document(content=b"x", content_type=None, filename="x.bin").format == "unknown"


def test_repeated_extraction_is_deterministic_and_independent() -> None:
    """Same bytes -> equal results, distinct objects, no shared mutable state.

    Equality keeps the same document on one identity; object independence keeps
    one caller's mutation from rewriting another document's table.
    """

    first = extract_csv_tables(_CSV)
    second = extract_csv_tables(_CSV)
    assert first.tables == second.tables
    assert first.tables is not second.tables
    first.tables[0]["headers"].append("mutated")
    assert second.tables[0]["headers"] == ["a", "b"]

    other = extract_csv_tables(b"z\n9\n")
    assert other.tables != second.tables


def test_pdf_text_extractor_runs_exactly_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls, _ = _install_pdf_stub(monkeypatch, text="table text", page_factory=lambda: [])
    document = extract_pdf_document(b"%PDF-1.4")
    assert document.parsed
    assert document.text == "table text"
    assert calls.extract_text == 1
    assert calls.extract_pages == 1


def test_pdf_tables_are_not_retried_when_the_text_layer_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, _ = _install_pdf_stub(monkeypatch, error=ValueError("bad"), page_factory=lambda: [])
    document = extract_pdf_document(b"%PDF-1.4")
    assert not document.parsed
    assert calls.extract_text == 1
    assert calls.extract_pages == 0
