"""Structured document extraction for the P1C IR acquisition pipeline.

Minimal, precedent-first extraction over raw document bytes:

- HTML tables reuse ``TableHtmlParser`` from the P1A toolkit.
- PDF text-layer extraction uses pdfminer.six (imported lazily; available via
  the ``operator-ir`` extra). Image-only PDFs fail closed with an explicit
  note — they are never silently treated as empty KPI sets.
- XLSX is captured with a per-sheet row table using the standard library's
  zipfile + XML reading (shared strings included). No new hard dependency.

Every extraction returns an ``ExtractedDocument`` that carries generic
structures (tables, text chunks) plus provenance-relevant parse notes. No
company-specific parsing lives here.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from typing import Any
from xml.etree import ElementTree

_PDF_MAX_CHARS = 200_000
_XLSX_MAX_ROWS = 10_000
_XLSX_MAX_COLUMNS = 256

_NAMESPACE_RE = re.compile(r"\{[^}]*\}")

_EXTENSIONS_HTML = frozenset({".html", ".htm", ".xhtml"})
_EXTENSIONS_PDF = frozenset({".pdf"})
_EXTENSIONS_XLSX = frozenset({".xlsx"})


@dataclass
class ExtractedDocument:
    """Generic extraction result with parse metadata; never raises."""

    format: str  # "html" | "pdf" | "xlsx" | "unknown"
    parsed: bool
    parse_note: str | None = None
    parser_version: str = "doc-v1"
    tables: list[dict[str, Any]] = field(default_factory=list)
    text: str | None = None  # pdf text layer (capped), if extracted
    text_chars: int = 0


def extension_of(filename: str | None, url: str | None = None) -> str | None:
    """Lowercase extension of the filename (or URL tail); None when absent."""

    source = filename
    if not source and url:
        tail = url.rstrip("/").rsplit("/", 1)[-1]
        source = tail.split("?", 1)[0]
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


def extract_pdf_tables(content: bytes) -> list[dict[str, Any]]:
    """Reconstruct row/column tables from PDF text-run coordinates.

    Generic layout heuristic: text runs whose vertical centers fall within
    ``_ROW_TOLERANCE`` points belong to one row; runs are split into columns
    by horizontal gaps larger than ``_COLUMN_TOLERANCE``. The first row of a
    vertically contiguous band becomes the header. Returns [] when layout
    analysis is unavailable (no pdfminer) or yields nothing — callers treat
    that as "no tables", never as an error.
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
                break
    except Exception:
        return tables
    return tables


def _nearest_cell(xs: list[float], cells: list[str], target_x: float) -> str:
    """Cell whose x anchor is closest to the header's x anchor."""

    if not xs:
        return ""
    best_index = min(range(len(xs)), key=lambda index: abs(xs[index] - target_x))
    return cells[best_index]


def extract_pdf_document(content: bytes) -> ExtractedDocument:
    """PDF extraction: text layer (always) + coordinate tables (best effort)."""

    document = extract_pdf_text(content)
    if not document.parsed:
        return document
    tables = extract_pdf_tables(content)
    if tables:
        document.tables = tables
    return document


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
    """Per-sheet row tables from a minimal OOXML reader (stdlib only)."""

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
        sheet_names = _sheet_names(archive)
        tables: list[dict[str, Any]] = []
        for sheet_index, sheet_name in enumerate(sheet_names, start=1):
            rows = _read_sheet_rows(archive, sheet_index, shared)
            if not rows:
                continue
            headers = rows[0]
            parsed_rows: list[dict[str, str]] = []
            for cells in rows[1:]:
                row: dict[str, str] = {}
                for column, header in enumerate(headers):
                    key = header or _column_letter(column + 1)
                    value = cells[column] if column < len(cells) else ""
                    row[key] = value
                parsed_rows.append(row)
            tables.append({"name": sheet_name, "headers": headers, "rows": parsed_rows})
        if not tables:
            return ExtractedDocument(
                format="xlsx",
                parsed=False,
                parse_note="xlsx contained no readable sheets",
            )
        return ExtractedDocument(format="xlsx", parsed=True, tables=tables)
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


def _sheet_names(archive: zipfile.ZipFile) -> list[str]:
    try:
        raw = archive.read("xl/workbook.xml")
    except KeyError:
        return []
    root = ElementTree.fromstring(raw)
    names: list[str] = []
    for node in root.iter():
        if _local_name(node.tag) == "sheet":
            names.append(node.attrib.get("name", ""))
    return names


def _read_sheet_rows(
    archive: zipfile.ZipFile, sheet_index: int, shared: list[str]
) -> list[list[str]]:
    try:
        raw = archive.read(f"xl/worksheets/sheet{sheet_index}.xml")
    except KeyError:
        return []
    root = ElementTree.fromstring(raw)
    rows: list[list[str]] = []
    for row_node in root.iter():
        if _local_name(row_node.tag) != "row":
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
                    for part in child.iter():
                        if _local_name(part.tag) == "t" and part.text:
                            inline_text.append(part.text)
            if inline_text:
                text = "".join(inline_text)
            elif value_node is not None and value_node.text is not None:
                if cell_type == "s":
                    try:
                        text = shared[int(value_node.text)]
                    except (ValueError, IndexError):
                        text = value_node.text
                else:
                    text = value_node.text
            else:
                text = ""
            if text != "":
                cells[column_index] = text
        if not cells:
            continue
        width = max(cells) + 1
        row_values = [cells.get(index, "") for index in range(min(width, _XLSX_MAX_COLUMNS))]
        rows.append(row_values)
        if len(rows) >= _XLSX_MAX_ROWS:
            break
    return rows


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
    lowered = (content_type or "").lower()
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
