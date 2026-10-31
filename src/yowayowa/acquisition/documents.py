"""Structured document extraction for the P1C IR acquisition pipeline.

Minimal, precedent-first extraction over raw document bytes:

- HTML tables reuse ``TableHtmlParser`` from the P1A toolkit.
- PDF text-layer extraction uses pdfminer.six (imported lazily; available via
  the ``operator-ir`` extra). Image-only PDFs fail closed with an explicit
  note — they are never silently treated as empty KPI sets.
- XLSX is captured with a per-sheet row table using the standard library's
  zipfile + XML reading (shared strings included). CSV is parsed with the
  standard library's ``csv`` module into the same row-table shape. No new
  hard dependency.

Every extraction returns an ``ExtractedDocument`` that carries generic
structures (tables, text chunks) plus provenance-relevant parse notes. No
company-specific parsing lives here.
"""

from __future__ import annotations

import csv
import io
import posixpath
import re
import zipfile
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit
from xml.etree import ElementTree

_PDF_MAX_CHARS = 200_000
_XLSX_MAX_ROWS = 10_000
_XLSX_MAX_COLUMNS = 256

# Fixed operator-facing notes for partial extractions. Wording is part of the
# contract: tests and downstream notes match these exact strings.
_PDF_INCOMPLETE_NOTE = "pdf table extraction incomplete"
_XLSX_MISSING_PARTS_PREFIX = "xlsx worksheet parts missing"
_XLSX_INVALID_SHARED_STRING_NOTE = "xlsx shared string reference invalid"
_XLSX_INVALID_RELATIONSHIP_NOTE = "xlsx worksheet relationship invalid"
_XLSX_NON_WORKSHEET_SKIP_PREFIX = "xlsx non-worksheet sheets skipped"

# OOXML package parts that pair a display sheet with its stored part.
_XLSX_WORKBOOK_PART = "xl/workbook.xml"
_XLSX_WORKBOOK_RELS_PART = "xl/_rels/workbook.xml.rels"
_XLSX_WORKSHEET_PART_PREFIX = "xl/worksheets/"
# Standard (transitional) relationship types for display sheets. Only a
# worksheet carries a cell grid; a chartsheet is a chart canvas.
_RELATIONSHIP_TYPE_WORKSHEET = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"
)
_RELATIONSHIP_TYPE_CHARTSHEET = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/chartsheet"
)
_URI_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")

_NAMESPACE_RE = re.compile(r"\{[^}]*\}")


class _XlsxRelationshipError(Exception):
    """A display sheet cannot be paired with its worksheet part provably."""


_EXTENSIONS_HTML = frozenset({".html", ".htm", ".xhtml"})
_EXTENSIONS_PDF = frozenset({".pdf"})
_EXTENSIONS_XLSX = frozenset({".xlsx"})
_EXTENSIONS_CSV = frozenset({".csv"})


@dataclass
class ExtractedDocument:
    """Generic extraction result with parse metadata; never raises."""

    format: str  # "html" | "pdf" | "xlsx" | "csv" | "unknown"
    parsed: bool
    parse_note: str | None = None
    parser_version: str = "doc-v1"
    tables: list[dict[str, Any]] = field(default_factory=list)
    text: str | None = None  # pdf text layer (capped), if extracted
    text_chars: int = 0


def extension_of(filename: str | None, url: str | None = None) -> str | None:
    """Lowercase extension of the filename (or URL tail); None when absent.

    The URL fallback reads the extension from the URL's *path* only (via
    ``urllib.parse.urlsplit``): query and fragment, in any order or
    combination, never change which extractor a document is dispatched to,
    and a URL ``urlsplit`` rejects yields None instead of raising.
    Filenames keep their literal meaning — ``#`` and ``?`` inside
    ``filename`` are name characters, never URL syntax — and the URL is
    never percent-decoded.
    """

    source = filename
    if not source and url:
        try:
            path = urlsplit(url).path
        except ValueError:
            return None
        source = path.rstrip("/").rsplit("/", 1)[-1]
    if not source:
        return None
    dot = source.rfind(".")
    if dot <= 0:
        return None
    return source[dot:].lower()


def extract_html_tables(html: str) -> ExtractedDocument:
    from yowayowa.acquisition.parsers import TableHtmlParser

    parser = TableHtmlParser()
    payload = parser.parse(html)
    return ExtractedDocument(
        format="html",
        parsed=True,
        parser_version=parser.parser_version,
        tables=list(payload.get("tables", [])),
    )


def extract_pdf_text(content: bytes) -> ExtractedDocument:
    """Extract the PDF text layer via pdfminer.six (lazy import)."""

    try:
        from pdfminer.high_level import extract_text
    except ImportError:  # pragma: no cover - depends on extras installed
        return ExtractedDocument(
            format="pdf",
            parsed=False,
            parse_note=(
                "pdf text-layer extraction unavailable (install yowayowa-investor[operator-ir])"
            ),
        )
    import io as _io

    try:
        text = extract_text(_io.BytesIO(content))
    except Exception:  # pdfminer raises assorted parse errors
        return ExtractedDocument(
            format="pdf",
            parsed=False,
            parse_note="pdf parse failed",
        )
    if not isinstance(text, str):
        return ExtractedDocument(
            format="pdf",
            parsed=False,
            parse_note="pdf text layer was not text",
        )
    text_chars = len(text)
    capped = text[:_PDF_MAX_CHARS]
    note = None if text_chars <= _PDF_MAX_CHARS else f"text capped at {_PDF_MAX_CHARS} chars"
    # A text layer of form-feed/whitespace only means every page is drawn as an
    # image: reporting parsed=True would let KPI extraction run on an empty
    # document and report "no attributes" instead of "not machine readable".
    if not text.strip():
        return ExtractedDocument(
            format="pdf",
            parsed=False,
            parse_note="pdf has no extractable text layer (image-only or scanned)",
        )
    return ExtractedDocument(
        format="pdf",
        parsed=True,
        parse_note=note,
        text=capped,
        text_chars=text_chars,
    )


_ROW_TOLERANCE = 4.0
_COLUMN_TOLERANCE = 12.0
_MAX_TABLES_PER_PDF = 200


def _text_item(node: object) -> str:
    return str(getattr(node, "get_text", lambda: "")()).strip()


def _bbox(node: object) -> tuple[float, float, float, float]:
    return (float(node.x0), float(node.y0), float(node.x1), float(node.y1))  # type: ignore[attr-defined]


def _append_parse_note(note: str | None, addition: str) -> str:
    """Append one fixed degradation note without erasing an existing one."""

    if note:
        return f"{note}; {addition}"
    return addition


def extract_pdf_tables(
    content: bytes, *, diagnostics: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Reconstruct row/column tables from PDF text-run coordinates.

    Generic layout heuristic: text runs whose vertical centers fall within
    ``_ROW_TOLERANCE`` points belong to one row; runs are split into columns
    by horizontal gaps larger than ``_COLUMN_TOLERANCE``. The first row of a
    vertically contiguous band becomes the header. Returns [] when layout
    analysis is unavailable (no pdfminer) or yields nothing — callers treat
    that as "no tables", never as an error.

    ``diagnostics`` is an optional keyword-only out-param (repo convention):
    when the page walk stops early — first page or mid-traversal — the flag
    ``table_traversal_incomplete`` is set so ``extract_pdf_document`` can
    record the partial extraction in ``parse_note``. The list return value and
    the no-diagnostics call are unchanged; raw exception text never leaves
    this module.
    """

    try:
        from pdfminer.high_level import extract_pages
        from pdfminer.layout import LTTextContainer
    except ImportError:  # pragma: no cover - extra not installed
        return []
    import io as _io

    tables: list[dict[str, Any]] = []
    try:
        for page in extract_pages(_io.BytesIO(content)):
            runs: list[tuple[float, float, str]] = []
            for element in page:
                if not isinstance(element, LTTextContainer):
                    continue
                for text_line in element:
                    line_text = _text_item(text_line)
                    if not line_text:
                        continue
                    x0, _, x1, y1 = _bbox(text_line)
                    runs.append(((x0 + x1) / 2, y1, line_text))
                    if len(runs) >= 4_000:
                        break
            if not runs:
                continue
            runs.sort(key=lambda item: (-item[1], item[0]))
            rows: list[list[tuple[float, str]]] = []
            current: list[tuple[float, str]] = []
            current_y: float | None = None
            for center_x, y, text in runs:
                if current_y is None or abs(y - current_y) <= _ROW_TOLERANCE:
                    current.append((center_x, text))
                    current_y = y if current_y is None else (current_y + y) / 2
                else:
                    rows.append(sorted(current))
                    current = [(center_x, text)]
                    current_y = y
            if current:
                rows.append(sorted(current))
            if len(rows) < 2:
                continue
            header_cells = [text for _, text in rows[0]]
            # Keep x anchors so downstream KPI extraction can align value
            # columns with period headers ("当連結会計年度" etc.) instead of
            # relying on positional index alone.
            header_xs = [x for x, _ in rows[0]]
            row_anchors: list[list[float]] = []
            row_cells: list[list[str]] = []
            parsed_rows: list[dict[str, str]] = []
            for row in rows[1:]:
                xs = [x for x, _ in row]
                cells = [text for _, text in row]
                entry: dict[str, str] = {}
                for index, header in enumerate(header_cells):
                    key = header or f"col{index + 1}"
                    value = _nearest_cell(xs, cells, header_xs[index])
                    entry[key] = value
                parsed_rows.append(entry)
                row_anchors.append(xs)
                row_cells.append(cells)
            tables.append(
                {
                    "headers": header_cells,
                    "header_xs": header_xs,
                    "rows": parsed_rows,
                    "row_xs": row_anchors,
                    "cells": row_cells,
                }
            )
            if len(tables) >= _MAX_TABLES_PER_PDF:
                if diagnostics is not None:
                    diagnostics["table_limit_reached"] = True
                break
    except Exception:
        if diagnostics is not None:
            # The page walk stopped early: everything collected so far is a
            # partial extraction. The caller turns this flag into a parse
            # note; the exception itself stays internal.
            diagnostics["table_traversal_incomplete"] = True
        return tables
    return tables


def _nearest_cell(xs: list[float], cells: list[str], target_x: float) -> str:
    """Cell whose x anchor is closest to the header's x anchor."""

    if not xs:
        return ""
    best_index = min(range(len(xs)), key=lambda index: abs(xs[index] - target_x))
    return cells[best_index]


def extract_pdf_document(content: bytes) -> ExtractedDocument:
    """PDF extraction: text layer (always) + coordinate tables (best effort).

    ``parsed`` keeps its existing meaning "usable extraction exists" — the
    text layer. An interrupted table walk (first page or mid-traversal) is
    recorded in ``parse_note`` so a partial extraction is never presented as
    complete; a clean no-tables document and a missing optional pdfminer
    extra stay note-free.
    """

    document = extract_pdf_text(content)
    if not document.parsed:
        return document
    diagnostics: dict[str, Any] = {}
    tables = extract_pdf_tables(content, diagnostics=diagnostics)
    if tables:
        document.tables = tables
    if diagnostics.get("table_traversal_incomplete"):
        document.parse_note = _append_parse_note(document.parse_note, _PDF_INCOMPLETE_NOTE)
    if diagnostics.get("table_limit_reached"):
        document.parse_note = _append_parse_note(
            document.parse_note, f"pdf table limit reached at {_MAX_TABLES_PER_PDF}"
        )
    return document


def _decode_csv_bytes(content: bytes) -> tuple[str, str | None]:
    """Decode CSV bytes: utf-8-sig first, cp932 fallback, fail closed.

    Japanese IR exports are commonly cp932-encoded; a BOM-marked utf-8 file
    decodes with ``utf-8-sig``. When BOTH decodings fail the note makes the
    document fail closed instead of guessing an encoding.
    """

    try:
        return content.decode("utf-8-sig"), None
    except UnicodeDecodeError:
        pass
    try:
        return content.decode("cp932"), None
    except UnicodeDecodeError:
        return "", ("csv decode failed (neither utf-8-sig nor cp932); not parsed")


def extract_csv_tables(content: bytes) -> ExtractedDocument:
    """Per-document table from a CSV file's rows (stdlib csv).

    The first row becomes the headers and every following row becomes a
    ``{header: value}`` dict, matching ``extract_xlsx_tables`` output shape
    so KPI extraction needs no format-specific branch.

    Size caps are not silent: when raw csv.reader rows (header and blank rows
    included) or column width was actually discarded, ``parse_note`` carries
    one fixed note per kind — ``csv rows truncated at <cap>`` / ``csv columns
    truncated at <cap>`` — so a truncated table is never mistaken for a
    complete one.
    """

    from yowayowa.acquisition.parsers import _dedupe

    text, decode_note = _decode_csv_bytes(content)
    if decode_note is not None:
        return ExtractedDocument(format="csv", parsed=False, parse_note=decode_note)
    try:
        rows = list(csv.reader(io.StringIO(text), strict=True))
    except csv.Error as exc:
        return ExtractedDocument(
            format="csv",
            parsed=False,
            parse_note=f"csv parse failed: {type(exc).__name__}",
        )
    # Size caps are a silent loss unless they are reported: when input rows or
    # columns were actually discarded, say so in parse_note with one fixed
    # note per kind (rows first, then columns). The wording is part of the
    # contract and carries no raw data.
    parse_note: str | None = None
    if len(rows) > _XLSX_MAX_ROWS:
        parse_note = _append_parse_note(parse_note, f"csv rows truncated at {_XLSX_MAX_ROWS}")
    if any(len(row) > _XLSX_MAX_COLUMNS for row in rows[:_XLSX_MAX_ROWS]):
        parse_note = _append_parse_note(parse_note, f"csv columns truncated at {_XLSX_MAX_COLUMNS}")
    rows = [row[:_XLSX_MAX_COLUMNS] for row in rows[:_XLSX_MAX_ROWS]]
    rows = [row for row in rows if any(cell.strip() for cell in row)]
    if not rows:
        note = "csv contained no readable rows"
        if parse_note is not None:
            note = _append_parse_note(note, parse_note)
        return ExtractedDocument(
            format="csv",
            parsed=False,
            parse_note=note,
        )
    headers = rows[0]
    parsed_rows: list[dict[str, str]] = []
    for cells in rows[1:]:
        row: dict[str, str] = {}
        for column, header in enumerate(headers):
            key = header or _column_letter(column + 1)
            value = cells[column] if column < len(cells) else ""
            # Same sequential duplicate rule as the HTML parser: a repeated
            # key keeps its own value under the smallest unused .2/.3
            # suffix instead of silently overwriting the earlier column.
            row[_dedupe(row, key)] = value
        parsed_rows.append(row)
    return ExtractedDocument(
        format="csv",
        parsed=True,
        parse_note=parse_note,
        tables=[{"headers": headers, "rows": parsed_rows, "_source_rows": rows}],
    )


def _column_letter(index: int) -> str:
    """1-based column index -> spreadsheet column letters (1 -> A)."""

    letters = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


def _local_name(tag: str) -> str:
    return _NAMESPACE_RE.sub("", tag)


def extract_xlsx_tables(content: bytes) -> ExtractedDocument:
    """Per-sheet row tables from a minimal OOXML reader (stdlib only).

    Each display sheet is paired with its stored part through the workbook
    part itself — ``<sheet r:id>`` joined against
    ``xl/_rels/workbook.xml.rels`` — never by display position or ``sheetId``,
    so a reordered workbook keeps ``Current`` and ``Prior`` on their own
    worksheets. A workbook whose sheets declare neither ``r:id`` nor a
    relationships part (the legacy minimal layout) keeps the positional
    ``sheetN.xml`` reading.

    Size caps are not silent: when a sheet had readable non-empty rows past
    the row cap or non-empty values at/after the column cap, ``parse_note``
    carries one fixed note per kind — ``xlsx rows truncated at <cap>`` /
    ``xlsx columns truncated at <cap>`` — once per document, rows before
    columns, appended after any existing note. Chartsheets are counted, not
    parsed, and named once at the end of the note as
    ``xlsx non-worksheet sheets skipped: <count>``.
    """

    from yowayowa.acquisition.parsers import _dedupe

    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except (zipfile.BadZipFile, OSError) as exc:
        return ExtractedDocument(
            format="xlsx",
            parsed=False,
            parse_note=f"xlsx open failed: {type(exc).__name__}",
        )
    try:
        shared = _read_shared_strings(archive)
        try:
            sheets, skipped_sheets = _worksheet_sheets(archive)
        except _XlsxRelationshipError:
            # The sheet -> part pairing cannot be proven, so no table can be
            # attributed to a sheet name. Handing back tables read so far (or
            # filling the gap with a positional guess) would let a year column
            # come from another sheet's numbers. Fixed note only: never the
            # raw target, a URL, a cell value or exception text.
            return ExtractedDocument(
                format="xlsx",
                parsed=False,
                tables=[],
                parse_note=_XLSX_INVALID_RELATIONSHIP_NOTE,
            )
        tables: list[dict[str, Any]] = []
        missing_parts = 0
        rows_truncated = False
        columns_truncated = False
        for sheet_index, (sheet_name, sheet_part) in enumerate(sheets, start=1):
            sheet_diagnostics: dict[str, Any] = {}
            rows = _read_sheet_rows(
                archive, sheet_index, shared, part=sheet_part, diagnostics=sheet_diagnostics
            )
            if sheet_diagnostics.get("shared_string_reference_invalid"):
                # A t="s" cell is a reference into sharedStrings.xml. When one
                # reference in the scanned range cannot be resolved, every
                # table in this workbook is untrustworthy: handing back the
                # sheets already read (or the rest of this sheet) would make a
                # partial table look like a complete financial table.
                return ExtractedDocument(
                    format="xlsx",
                    parsed=False,
                    tables=[],
                    parse_note=_XLSX_INVALID_SHARED_STRING_NOTE,
                )
            if sheet_diagnostics.get("missing_part"):
                missing_parts += 1
            # Per-sheet truncation flags OR-combine at document level: the
            # note names the workbook-level fact once, not once per sheet.
            rows_truncated = rows_truncated or sheet_diagnostics.get("rows_truncated", False)
            columns_truncated = columns_truncated or sheet_diagnostics.get(
                "columns_truncated", False
            )
            if not rows:
                continue
            headers = rows[0]
            # rows (the raw sheet rows incl. the header row) are carried on
            # the table so KPI header-layout detection can reconstruct
            # pre-header title/unit rows that the header projection cannot
            # represent.
            parsed_rows: list[dict[str, str]] = []
            for cells in rows[1:]:
                row: dict[str, str] = {}
                for column, header in enumerate(headers):
                    key = header or _column_letter(column + 1)
                    value = cells[column] if column < len(cells) else ""
                    # Same sequential duplicate rule as the HTML parser: a
                    # repeated key keeps its own value under the smallest
                    # unused .2/.3 suffix instead of silently overwriting the
                    # earlier column.
                    row[_dedupe(row, key)] = value
                parsed_rows.append(row)
            tables.append(
                {
                    "name": sheet_name,
                    "headers": headers,
                    "rows": parsed_rows,
                    "_source_rows": rows,
                }
            )
        # Empty-but-present sheets were skipped above and are NOT missing;
        # only declared sheets whose part is absent count here.
        missing_note = f"{_XLSX_MISSING_PARTS_PREFIX}: {missing_parts}" if missing_parts else None
        # Existing note first, then the fixed truncation notes (rows before
        # columns), '; '-joined; wording carries no raw data or sheet names.
        note = missing_note
        if rows_truncated:
            note = _append_parse_note(note, f"xlsx rows truncated at {_XLSX_MAX_ROWS}")
        if columns_truncated:
            note = _append_parse_note(note, f"xlsx columns truncated at {_XLSX_MAX_COLUMNS}")
        if skipped_sheets:
            # Chartsheets carry no cell grid: counted, never KPI-ified, and
            # named once per document after the existing notes.
            note = _append_parse_note(note, f"{_XLSX_NON_WORKSHEET_SKIP_PREFIX}: {skipped_sheets}")
        if not tables:
            no_readable = "xlsx contained no readable sheets"
            if note is not None:
                no_readable = _append_parse_note(no_readable, note)
            return ExtractedDocument(format="xlsx", parsed=False, parse_note=no_readable)
        return ExtractedDocument(format="xlsx", parsed=True, tables=tables, parse_note=note)
    except Exception as exc:  # malformed archives raise assorted errors
        return ExtractedDocument(
            format="xlsx",
            parsed=False,
            parse_note=f"xlsx parse failed: {type(exc).__name__}",
        )


def _read_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        raw = archive.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    root = ElementTree.fromstring(raw)
    values: list[str] = []
    for item in root:
        # si -> (t | r)* : concatenate all text runs
        chunks: list[str] = []
        for node in item.iter():
            if _local_name(node.tag) == "t" and node.text:
                chunks.append(node.text)
        values.append("".join(chunks))
    return values


def _worksheet_sheets(archive: zipfile.ZipFile) -> tuple[list[tuple[str, str | None]], int]:
    """Display sheets paired with their worksheet ZIP part, in workbook order.

    Returns ``(sheets, skipped)``: each entry is ``(sheet name, part)``, and
    ``part`` is None only for the legacy minimal layout (no ``r:id`` on any
    sheet and no relationships part), where the positional ``sheetN.xml``
    reading stands in. ``skipped`` counts standard chartsheets, which carry no
    cell grid and must not be KPI-ified.

    The pairing is read out of the package: the ``<sheet>`` display order and
    name come from ``xl/workbook.xml``, and each ``r:id`` is joined against
    ``xl/_rels/workbook.xml.rels``. ``sheetId`` and the display order are
    never used to guess a part name. As soon as either side declares a
    relationship the workbook is resolved only this way — a gap is refused,
    never filled by the positional guess.

    Raises ``_XlsxRelationshipError`` when the pairing is not exact and
    provable; XML/zip failures are left to the caller's existing
    ``xlsx parse failed`` contract.
    """

    try:
        raw = archive.read(_XLSX_WORKBOOK_PART)
    except KeyError:
        return [], 0
    root = ElementTree.fromstring(raw)
    declared: list[tuple[str, str | None]] = []
    for node in root.iter():
        if _local_name(node.tag) != "sheet":
            continue
        declared.append((node.attrib.get("name", ""), _relationship_id(node)))
    if not declared:
        return [], 0
    if all(rel_id is None for _, rel_id in declared):
        if _has_workbook_relationships_part(archive):
            raise _XlsxRelationshipError()
        return [(name, None) for name, _ in declared], 0
    relationships = _workbook_relationships(archive)
    sheets: list[tuple[str, str | None]] = []
    skipped = 0
    for name, rel_id in declared:
        if not rel_id:
            raise _XlsxRelationshipError()
        relationship = relationships.get(rel_id)
        if relationship is None:
            raise _XlsxRelationshipError()
        relationship_type = relationship.attrib.get("Type", "")
        if relationship_type == _RELATIONSHIP_TYPE_WORKSHEET:
            sheets.append(
                (
                    name,
                    _resolve_worksheet_target(
                        relationship.attrib.get("Target", ""),
                        relationship.attrib.get("TargetMode"),
                    ),
                )
            )
        elif relationship_type == _RELATIONSHIP_TYPE_CHARTSHEET:
            skipped += 1
        else:
            raise _XlsxRelationshipError()
    return sheets, skipped


def _relationship_id(node: ElementTree.Element) -> str | None:
    """The ``r:id`` a workbook ``<sheet>`` declares, or None when it has none."""

    for key, value in node.attrib.items():
        if not key.startswith("{"):
            continue
        if "relationships" not in key.split("}", 1)[0]:
            continue
        if _local_name(key) == "id":
            return value
    return None


def _has_workbook_relationships_part(archive: zipfile.ZipFile) -> bool:
    try:
        archive.read(_XLSX_WORKBOOK_RELS_PART)
    except KeyError:
        return False
    return True


def _workbook_relationships(archive: zipfile.ZipFile) -> dict[str, ElementTree.Element]:
    """``Id -> <Relationship>`` of the workbook part; duplicate ids are refused."""

    try:
        raw = archive.read(_XLSX_WORKBOOK_RELS_PART)
    except KeyError:
        return {}
    root = ElementTree.fromstring(raw)
    relationships: dict[str, ElementTree.Element] = {}
    for node in root:
        if _local_name(node.tag) != "Relationship":
            continue
        relationship_id = node.attrib.get("Id", "")
        if not relationship_id or relationship_id in relationships:
            # An id-less or duplicated id makes every join through it
            # ambiguous, including the ones this workbook does not reference.
            raise _XlsxRelationshipError()
        relationships[relationship_id] = node
    return relationships


def _resolve_worksheet_target(target: str, target_mode: str | None) -> str:
    """ZIP part name of one worksheet ``Target`` (internal references only).

    Accepts the workbook-relative form (``worksheets/sheet1.xml``) and the
    package-root form (``/xl/worksheets/sheet1.xml``), normalised with
    ``posixpath`` against ``xl/workbook.xml``'s directory. An external target
    mode, a URI scheme or authority, a query/fragment, backslashes, NUL, or a
    reference that does not normalise to a ``.xml`` part directly inside
    ``xl/worksheets/`` is refused rather than followed: the decision is made
    on the normalised part name, without touching the host filesystem or
    extracting the archive.
    """

    if target_mode is not None and target_mode.strip().lower() not in ("", "internal"):
        raise _XlsxRelationshipError()
    if not target.strip():
        raise _XlsxRelationshipError()
    if "\\" in target or "\x00" in target or "?" in target or "#" in target:
        raise _XlsxRelationshipError()
    if target.startswith("//") or _URI_SCHEME_RE.match(target):
        raise _XlsxRelationshipError()
    # Package-root absolute (/xl/worksheets/a.xml) drops its leading slash;
    # a relative target starts at the directory of xl/workbook.xml ('xl').
    candidate = target[1:] if target.startswith("/") else posixpath.join("xl", target)
    normalized = posixpath.normpath(candidate)
    if not normalized.startswith(_XLSX_WORKSHEET_PART_PREFIX):
        raise _XlsxRelationshipError()
    if not normalized.endswith(".xml"):
        raise _XlsxRelationshipError()
    if not normalized[len(_XLSX_WORKSHEET_PART_PREFIX) : -len(".xml")]:
        raise _XlsxRelationshipError()
    return normalized


def _resolve_shared_string(
    value_node: ElementTree.Element | None, shared: list[str]
) -> tuple[str, bool]:
    """Resolve one ``t="s"`` reference; ``(text, resolved)``.

    ``resolved`` is False whenever the reference cannot be resolved: no
    ``<v>``, empty value text, a non-integer value, a negative index, or an
    index outside ``0 <= index < len(shared)``. A negative index is rejected
    explicitly so Python's tail indexing can never substitute the wrong
    shared string for it.
    """

    if value_node is None or value_node.text is None:
        return "", False
    raw = value_node.text.strip()
    if not raw:
        return "", False
    try:
        index = int(raw)
    except ValueError:
        return "", False
    if index < 0 or index >= len(shared):
        return "", False
    return shared[index], True


def _read_sheet_rows(
    archive: zipfile.ZipFile,
    sheet_index: int,
    shared: list[str],
    *,
    part: str | None = None,
    diagnostics: dict[str, Any] | None = None,
) -> list[list[str]]:
    """Rows of one worksheet (empty list when it cannot be read safely).

    ``part`` is the resolved ZIP part name of the worksheet. ``sheet_index``
    keeps its historical meaning and is used only when ``part`` is None (the
    legacy ``xl/worksheets/sheet<index>.xml`` layout), so direct callers of
    this helper behave exactly as before.

    ``diagnostics`` is the existing keyword-only out-param: ``missing_part``
    marks a declared sheet whose part is absent, and
    ``shared_string_reference_invalid`` marks a ``t="s"`` cell whose reference
    cannot be resolved. In the latter case no row is returned at all, so an
    unresolvable reference is never substituted by its raw index text.
    Additive truncation flags: ``rows_truncated`` when a row past the row cap
    still carries raw text, ``columns_truncated`` when a retained row has a
    non-empty value at or past the column cap. The return type and the two
    original flags are unchanged.
    """

    if part is None:
        part = f"xl/worksheets/sheet{sheet_index}.xml"
    try:
        raw = archive.read(part)
    except KeyError:
        if diagnostics is not None:
            # Declared in workbook.xml but the worksheet part is absent
            # (trimmed workbooks, chart-sheet layouts): reported by the
            # caller instead of silently dropping the sheet.
            diagnostics["missing_part"] = True
        return []
    root = ElementTree.fromstring(raw)
    rows: list[list[str]] = []
    scanning_past_row_cap = False
    for row_node in root.iter():
        if _local_name(row_node.tag) != "row":
            continue
        if scanning_past_row_cap:
            # Cheap lookahead past the row cap: raw <v>/inline text only, so
            # a payload row hiding after the break is still noticed without
            # resolving shared strings, adopting values, or validating
            # references (an invalid ref out of range must not fail the
            # document; the caller reports the truncation instead).
            if _raw_row_has_payload(row_node) and diagnostics is not None:
                diagnostics["rows_truncated"] = True
                break
            continue
        cells: dict[int, str] = {}
        for cell_node in row_node:
            if _local_name(cell_node.tag) != "c":
                continue
            reference = cell_node.attrib.get("r", "")
            column_index = _column_index(reference)
            cell_type = cell_node.attrib.get("t", "")
            value_node = None
            inline_text: list[str] = []
            for child in cell_node:
                name = _local_name(child.tag)
                if name == "v":
                    value_node = child
                elif name == "is":
                    for run in child.iter():
                        if _local_name(run.tag) == "t" and run.text:
                            inline_text.append(run.text)
            if cell_type == "s":
                # A t="s" cell references sharedStrings.xml; it is never a
                # value of its own. An unresolvable reference must not degrade
                # into the raw index text ('0', '5', 'bad') or, for -1, into
                # the wrong shared string via Python's tail indexing.
                text, resolved = _resolve_shared_string(value_node, shared)
                if not resolved:
                    if diagnostics is not None:
                        # The caller maps this to one fixed fail-closed note;
                        # the cell value and the raw reference stay internal.
                        diagnostics["shared_string_reference_invalid"] = True
                    return []
            elif inline_text:
                text = "".join(inline_text)
            elif value_node is not None and value_node.text is not None:
                text = value_node.text
            else:
                text = ""
            if text != "":
                cells[column_index] = text
        if not cells:
            continue
        width = max(cells) + 1
        if diagnostics is not None and any(index >= _XLSX_MAX_COLUMNS for index in cells):
            # A non-empty value sits at or past the column cap: the retained
            # row_values silently lose it, so the caller must note that.
            diagnostics["columns_truncated"] = True
        row_values = [cells.get(index, "") for index in range(min(width, _XLSX_MAX_COLUMNS))]
        rows.append(row_values)
        if len(rows) >= _XLSX_MAX_ROWS:
            scanning_past_row_cap = True
    return rows


def _raw_row_has_payload(row_node: ElementTree.Element) -> bool:
    """Whether any cell of this row carries raw text past the row cap.

    Same presence rule as the value adoption above: any text other than the
    empty string counts, including a literal ``0`` and whitespace-only text
    (both would have been counted as a readable non-empty row inside the
    cap). ``t="s"`` references are deliberately NOT resolved here — this
    lookahead reads ``<v>``/inline text as-is so it can never fail a document
    or change which note it gets.
    """

    for cell_node in row_node:
        if _local_name(cell_node.tag) != "c":
            continue
        for child in cell_node:
            name = _local_name(child.tag)
            if name == "v":
                if child.text:
                    return True
            elif name == "is":
                for part in child.iter():
                    if _local_name(part.tag) == "t" and part.text:
                        return True
    return False


def _column_index(reference: str) -> int:
    """A1-style reference -> 0-based column index; unknown refs map to 0."""

    letters = ""
    for char in reference:
        if char.isalpha():
            letters += char.upper()
        else:
            break
    if not letters:
        return 0
    index = 0
    for char in letters:
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index - 1


def extract_document(
    *,
    content: bytes,
    content_type: str | None,
    filename: str | None,
    url: str | None = None,
) -> ExtractedDocument:
    """Dispatch extraction by extension/content-type; fail closed per format."""

    extension = extension_of(filename, url)
    if extension in _EXTENSIONS_HTML:
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            text = content.decode("utf-8", errors="replace")
        return extract_html_tables(text)
    if extension in _EXTENSIONS_PDF:
        return extract_pdf_document(content)
    if extension in _EXTENSIONS_XLSX:
        return extract_xlsx_tables(content)
    if extension in _EXTENSIONS_CSV:
        return extract_csv_tables(content)
    lowered = (content_type or "").lower()
    if "csv" in lowered:
        return extract_csv_tables(content)
    if "pdf" in lowered:
        return extract_pdf_document(content)
    if "spreadsheetml" in lowered:
        return extract_xlsx_tables(content)
    if "html" in lowered:
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            text = content.decode("utf-8", errors="replace")
        return extract_html_tables(text)
    return ExtractedDocument(
        format="unknown",
        parsed=False,
        parse_note="no extractor for this document format",
    )
