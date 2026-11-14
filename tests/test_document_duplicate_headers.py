"""Duplicate CSV/XLSX headers keep every column's value (the HTML rule).

``documents.py`` projects each row into a ``{key: value}`` dict. The CSV and
XLSX projections used to assign ``row[key] = value``, so a repeated header
silently overwrote the earlier column's value, while the HTML parser
(``acquisition.parsers._TableCollector``) always kept both via the
sequential ``_dedupe`` rule (``a, a`` -> ``a, a.2``). These tests pin the
same rule for CSV and XLSX:

- keys follow the original column order; an empty header falls back to its
  spreadsheet column letter (``A``, ``B``, ...);
- an already-used key gets the smallest unused ``.2``/``.3``/... suffix —
  the sequential HTML rule with no pre-reservation of natural suffix
  headers (``a, a, a.2`` -> ``a, a.2, a.2.2``; ``a, a, a`` -> ``a, a.2, a.3``);
- values pass through verbatim (no summing, no unit/whitespace/case
  conversion), ``headers`` and ``_source_rows`` stay raw, documents without
  duplicate headers keep every dataclass field identical, and the IR layer's
  raw grid and unique-input KPI extraction see the same results as before.

All fixtures are in-process byte strings; nothing touches the network.
"""

from __future__ import annotations

import io
import zipfile

from yowayowa.acquisition.documents import (
    ExtractedDocument,
    extract_csv_tables,
    extract_document,
    extract_html_tables,
    extract_xlsx_tables,
)
from yowayowa.acquisition.ir import _raw_cell_grid, extract_kpis_from_tables

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


def _xlsx_zip(
    sheets: dict[int, list[list[str]]],
    *,
    sheet_names: list[str] | None = None,
) -> bytes:
    names = sheet_names or [f"Sheet{index}" for index in sorted(sheets)]
    sheet_tags = "".join(f'<sheet name="{name}"/>' for name in names)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("xl/workbook.xml", f"<workbook><sheets>{sheet_tags}</sheets></workbook>")
        for index, rows in sheets.items():
            archive.writestr(f"xl/worksheets/sheet{index}.xml", _sheet_xml(rows))
    return buffer.getvalue()


def _shared_strings_xlsx(sheet_rows: list[list[str]]) -> bytes:
    """A workbook whose every cell is a shared-string reference (t="s")."""

    strings: list[str] = []
    for row in sheet_rows:
        for value in row:
            if value not in strings:
                strings.append(value)
    sst = "<sst>" + "".join(f"<si><t>{value}</t></si>" for value in strings) + "</sst>"
    parts: list[str] = []
    for row_index, row in enumerate(sheet_rows, start=1):
        cells = "".join(
            f'<c r="{_col_letters(column)}{row_index}" t="s"><v>{strings.index(value)}</v></c>'
            for column, value in enumerate(row, start=1)
        )
        parts.append(f'<row r="{row_index}">{cells}</row>')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "xl/workbook.xml",
            '<workbook><sheets><sheet name="KPI"/></sheets></workbook>',
        )
        archive.writestr("xl/sharedStrings.xml", sst)
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            "<worksheet><sheetData>" + "".join(parts) + "</sheetData></worksheet>",
        )
    return buffer.getvalue()


# ------------------------------------------------------- duplicate basics


def test_csv_duplicate_headers_keep_both_values() -> None:
    extracted = extract_csv_tables(b"a,a\n1,2\n")
    table = extracted.tables[0]
    assert table["headers"] == ["a", "a"]  # raw headers untouched
    assert table["rows"] == [{"a": "1", "a.2": "2"}]  # first column kept
    # key/value insertion order follows the original column order
    assert list(table["rows"][0].items()) == [("a", "1"), ("a.2", "2")]
    assert table["_source_rows"] == [["a", "a"], ["1", "2"]]


def test_xlsx_duplicate_headers_keep_both_values() -> None:
    extracted = extract_xlsx_tables(_xlsx_zip({1: [["a", "a"], ["1", "2"]]}))
    table = extracted.tables[0]
    assert table["name"] == "Sheet1"
    assert table["headers"] == ["a", "a"]
    assert table["rows"] == [{"a": "1", "a.2": "2"}]
    assert table["_source_rows"] == [["a", "a"], ["1", "2"]]


def test_xlsx_duplicate_headers_via_shared_strings_keep_both_values() -> None:
    extracted = extract_xlsx_tables(_shared_strings_xlsx([["a", "a"], ["1", "2"]]))
    assert extracted.parsed
    assert extracted.tables[0]["rows"] == [{"a": "1", "a.2": "2"}]


def test_csv_three_duplicate_headers_keep_all_values() -> None:
    extracted = extract_csv_tables(b"k,k,k\n1,2,3\n")
    row = extracted.tables[0]["rows"][0]
    assert row == {"k": "1", "k.2": "2", "k.3": "3"}
    # every projected column survives: key count == header count
    assert len(row) == len(extracted.tables[0]["headers"]) == 3
    assert list(row.values()) == ["1", "2", "3"]


def test_xlsx_three_duplicate_headers_keep_all_values() -> None:
    extracted = extract_xlsx_tables(_xlsx_zip({1: [["k", "k", "k"], ["1", "2", "3"]]}))
    row = extracted.tables[0]["rows"][0]
    assert row == {"k": "1", "k.2": "2", "k.3": "3"}
    assert len(row) == len(extracted.tables[0]["headers"]) == 3
    assert list(row.values()) == ["1", "2", "3"]


# ------------------------------------------------- natural suffix headers


def test_csv_natural_suffix_collision_follows_the_sequential_rule() -> None:
    # a, a.2: both headers natural — nothing is suffixed.
    assert extract_csv_tables(b"a,a.2\n1,2\n").tables[0]["rows"] == [{"a": "1", "a.2": "2"}]
    # a, a, a.2: the second 'a' takes the first free a.2, the natural a.2
    # column then takes a.2.2 — sequential, never pre-reserved.
    extracted = extract_csv_tables(b"a,a,a.2\n1,2,3\n")
    assert extracted.tables[0]["rows"] == [{"a": "1", "a.2": "2", "a.2.2": "3"}]


def test_xlsx_natural_suffix_collision_follows_the_sequential_rule() -> None:
    extracted = extract_xlsx_tables(_xlsx_zip({1: [["a", "a", "a.2"], ["1", "2", "3"]]}))
    assert extracted.tables[0]["rows"] == [{"a": "1", "a.2": "2", "a.2.2": "3"}]


# ------------------------------------------- empty-header fallback results


def test_csv_empty_header_fallback_collisions_use_the_same_rule() -> None:
    # Real header 'B' first; the empty column's own fallback letter 'B'
    # collides and is suffixed instead of overwriting.
    assert extract_csv_tables(b"B,\n1,2\n").tables[0]["rows"] == [{"B": "1", "B.2": "2"}]
    # Fallback 'A' first; the real header 'A' is suffixed.
    assert extract_csv_tables(b",A\n1,2\n").tables[0]["rows"] == [{"A": "1", "A.2": "2"}]
    # Two empties keep distinct fallback letters when the header row is
    # readable (an empties-only header row is filtered as unreadable).
    assert extract_csv_tables(b",x\n1,2\n").tables[0]["rows"] == [{"A": "1", "x": "2"}]


def test_xlsx_empty_header_fallback_collision_uses_the_same_rule() -> None:
    # The '' header falls back to its own column letter 'B', which the real
    # header in column 1 already uses -> B.2, never an overwrite.
    extracted = extract_xlsx_tables(_xlsx_zip({1: [["B", "", "X"], ["1", "2", "3"]]}))
    assert extracted.tables[0]["headers"] == ["B", "", "X"]
    assert extracted.tables[0]["rows"] == [{"B": "1", "B.2": "2", "X": "3"}]


# ------------------------------------------------------------- row shapes


def test_duplicate_headers_resolve_per_row() -> None:
    extracted = extract_csv_tables(b"a,a\n1,2\n3,4\n")
    assert extracted.tables[0]["rows"] == [{"a": "1", "a.2": "2"}, {"a": "3", "a.2": "4"}]
    sheet = extract_xlsx_tables(_xlsx_zip({1: [["a", "a"], ["1", "2"], ["3", "4"]]}))
    assert sheet.tables[0]["rows"] == [{"a": "1", "a.2": "2"}, {"a": "3", "a.2": "4"}]


def test_duplicate_column_values_pass_through_verbatim() -> None:
    extracted = extract_csv_tables("売上高,売上高\n0,-5\n".encode())
    assert extracted.tables[0]["rows"] == [{"売上高": "0", "売上高.2": "-5"}]
    # an empty value keeps its own column; nothing is summed or shifted
    extracted = extract_csv_tables("売上高,売上高\n,1200\n".encode())
    assert extracted.tables[0]["rows"] == [{"売上高": "", "売上高.2": "1200"}]
    # whitespace and leading zeros are data, never normalized
    extracted = extract_csv_tables("値,値\n 7 ,007\n".encode())
    assert extracted.tables[0]["rows"] == [{"値": " 7 ", "値.2": "007"}]


def test_short_and_surplus_cells_keep_the_source_rows_contract() -> None:
    extracted = extract_csv_tables(b"a,a\n7\n8,9,10\n")
    table = extracted.tables[0]
    # short row pads "" in the projection, surplus cells stay unprojected
    assert table["rows"] == [{"a": "7", "a.2": ""}, {"a": "8", "a.2": "9"}]
    assert table["_source_rows"] == [["a", "a"], ["7"], ["8", "9", "10"]]
    sheet = extract_xlsx_tables(_xlsx_zip({1: [["a", "a"], ["1"], ["2", "3", "4"]]}))
    assert sheet.tables[0]["rows"] == [{"a": "1", "a.2": ""}, {"a": "2", "a.2": "3"}]
    assert sheet.tables[0]["_source_rows"] == [["a", "a"], ["1"], ["2", "3", "4"]]


def test_header_only_duplicate_document_is_unchanged() -> None:
    extracted = extract_csv_tables(b"a,a\n")
    assert extracted == ExtractedDocument(
        format="csv",
        parsed=True,
        parse_note=None,
        parser_version="doc-v1",
        tables=[{"headers": ["a", "a"], "rows": [], "_source_rows": [["a", "a"]]}],
    )


# ------------------------------------------------------- invariance guards


def test_unique_header_documents_keep_every_field() -> None:
    # Documents without duplicate headers must stay byte-equivalent.
    assert extract_csv_tables(b"a,b\n1,2\n") == ExtractedDocument(
        format="csv",
        parsed=True,
        parse_note=None,
        parser_version="doc-v1",
        tables=[
            {
                "headers": ["a", "b"],
                "rows": [{"a": "1", "b": "2"}],
                "_source_rows": [["a", "b"], ["1", "2"]],
            }
        ],
    )
    assert extract_xlsx_tables(_xlsx_zip({1: [["a", "b"], ["1", "2"]]})) == ExtractedDocument(
        format="xlsx",
        parsed=True,
        parse_note=None,
        parser_version="doc-v1",
        tables=[
            {
                "name": "Sheet1",
                "headers": ["a", "b"],
                "rows": [{"a": "1", "b": "2"}],
                "_source_rows": [["a", "b"], ["1", "2"]],
            }
        ],
    )


def test_same_bytes_reread_is_field_identical() -> None:
    content = b"k,k,k\n1,2,3\n0,-5,\n"
    assert extract_csv_tables(content) == extract_csv_tables(content)
    archive = _xlsx_zip(
        {1: [["k", "k", "k"], ["1", "2", "3"]], 2: [["k", "k"], ["4", "5"]]},
        sheet_names=["一覧", "明細"],
    )
    assert extract_xlsx_tables(archive) == extract_xlsx_tables(archive)


def test_xlsx_multiple_sheets_dedupe_independently() -> None:
    extracted = extract_xlsx_tables(
        _xlsx_zip(
            {1: [["k", "k"], ["1", "2"]], 2: [["m", "m", "m"], ["3", "4", "5"]]},
            sheet_names=["一覧", "明細"],
        )
    )
    assert [table["name"] for table in extracted.tables] == ["一覧", "明細"]
    assert extracted.tables[0]["rows"] == [{"k": "1", "k.2": "2"}]
    assert extracted.tables[1]["rows"] == [{"m": "3", "m.2": "4", "m.3": "5"}]


def test_extract_document_dispatch_projects_the_same_way() -> None:
    csv_bytes = b"a,a\n1,2\n"
    via_filename = extract_document(content=csv_bytes, content_type=None, filename="r.csv")
    assert via_filename.tables == extract_csv_tables(csv_bytes).tables
    # content-type fallback (no extension anywhere) takes the same extractor
    via_type = extract_document(content=csv_bytes, content_type="text/csv", filename=None)
    assert via_type.tables == extract_csv_tables(csv_bytes).tables
    # URL query + fragment dispatch to the same extractor (extension lives
    # in the URL path) and project identically
    via_url = extract_document(
        content=csv_bytes,
        content_type=None,
        filename=None,
        url="https://ir.example.co.jp/r.csv?download=1#row2",
    )
    assert via_url.tables == extract_csv_tables(csv_bytes).tables
    xlsx_bytes = _xlsx_zip({1: [["a", "a"], ["1", "2"]]})
    via_xlsx = extract_document(content=xlsx_bytes, content_type=None, filename="r.xlsx")
    assert via_xlsx.tables == extract_xlsx_tables(xlsx_bytes).tables


def test_html_duplicate_rule_is_unchanged() -> None:
    # The HTML parser is the parity target: its sequential rule (including
    # the natural-suffix case) must stay exactly as it is.
    extracted = extract_html_tables(
        "<table><tr><th>a</th><th>a</th><th>a.2</th></tr>"
        "<tr><td>1</td><td>2</td><td>3</td></tr></table>"
    )
    assert extracted.tables[0]["rows"] == [{"a": "1", "a.2": "2", "a.2.2": "3"}]


def test_ir_raw_cell_grid_keeps_duplicate_columns() -> None:
    # The IR layer rebuilds its raw grid from headers + _source_rows, so the
    # duplicate column must be present there before and after the fix.
    table = extract_csv_tables('"売上高","売上高"\n"912,248","999,999"\n'.encode()).tables[0]
    grid = _raw_cell_grid(table)
    assert grid[0] == ["売上高", "売上高"]
    assert grid[1] == ["912,248", "999,999"]


def test_unique_period_headers_grid_is_unchanged() -> None:
    # Period headers feed header-layout/period selection through the same
    # grid; unique inputs must reach it byte-for-byte as before.
    table = extract_csv_tables(
        '"項目","当期","前期"\n"売上高","912,248","900,000"\n'.encode()
    ).tables[0]
    grid = _raw_cell_grid(table)
    assert grid[0] == ["項目", "当期", "前期"]
    assert grid[1] == ["売上高", "912,248", "900,000"]


def test_kpi_extraction_on_unique_input_is_unchanged() -> None:
    # Value/unit/source carried through the projection are untouched for
    # documents without duplicate headers.
    extracted = extract_csv_tables('"売上高"\n"912,248"\n'.encode())
    observations, notes = extract_kpis_from_tables(extracted.tables)
    assert notes == []
    assert observations == [
        {
            "kpi": "revenue",
            "label": "売上高",
            "value": 912248.0,
            "raw_value": "912,248",
            "unit": None,
            "source": "table[0]",
        }
    ]
    sheet = extract_xlsx_tables(_shared_strings_xlsx([["受注残"], ["1200"]]))
    observations, notes = extract_kpis_from_tables(sheet.tables)
    assert notes == []
    assert observations == [
        {
            "kpi": "order_backlog",
            "label": "受注残",
            "value": 1200.0,
            "raw_value": "1200",
            "unit": None,
            "source": "table[0]",
        }
    ]
