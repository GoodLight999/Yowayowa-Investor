from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any, Protocol

from yowayowa.acquisition.models import AcquisitionFetchState
from yowayowa.acquisition.transport import PrivateAcquisitionError


class HtmlParserAdapter(Protocol):
    name: str
    parser_version: str
    schema_version: str

    def parse(self, html: str) -> dict[str, Any]: ...


class _TableCollector(HTMLParser):
    """Collects <table> rows; the first <tr> of each table supplies headers."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[dict[str, Any]] = []
        self._current_cells: list[str] | None = None
        self._current_rows: list[list[str]] = []
        self._cell_chunks: list[str] | None = None
        self._in_table = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._in_table = True
            self._current_rows = []
        elif tag == "tr" and self._in_table:
            self._current_cells = []
        elif tag in ("td", "th") and self._current_cells is not None:
            self._cell_chunks = []

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._cell_chunks is not None:
            assert self._current_cells is not None
            self._current_cells.append(" ".join("".join(self._cell_chunks).split()))
            self._cell_chunks = None
        elif tag == "tr" and self._current_cells is not None:
            if self._current_cells:
                self._current_rows.append(self._current_cells)
            self._current_cells = None
        elif tag == "table" and self._in_table:
            self._finish_table()
            self._in_table = False

    def handle_data(self, data: str) -> None:
        if self._cell_chunks is not None:
            self._cell_chunks.append(data)

    def close(self) -> None:
        super().close()
        if self._in_table and self._current_rows:
            self._finish_table()

    def _finish_table(self) -> None:
        rows = self._current_rows
        if not rows:
            return
        headers = [cell for cell in rows[0]]
        parsed: list[dict[str, str]] = []
        for cells in rows[1:]:
            row: dict[str, str] = {}
            for index, header in enumerate(headers):
                value = cells[index] if index < len(cells) else ""
                row[_dedupe(row, header)] = value
            parsed.append(row)
        self.tables.append({"headers": headers, "rows": parsed})


def _dedupe(existing: dict[str, str], header: str) -> str:
    if header not in existing:
        return header
    suffix = 2
    while f"{header}.{suffix}" in existing:
        suffix += 1
    return f"{header}.{suffix}"


class TableHtmlParser:
    name = "tables"
    parser_version = "table-v1"
    schema_version = "tables-v1"

    def parse(self, html: str) -> dict[str, Any]:
        collector = _TableCollector()
        collector.feed(html)
        collector.close()
        return {"tables": collector.tables}


class TextHtmlParser:
    name = "text"
    parser_version = "text-v1"
    schema_version = "text-v1"

    _TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)

    def parse(self, html: str) -> dict[str, Any]:
        match = self._TITLE_RE.search(html)
        title = " ".join(match.group(1).split()) if match else None
        return {"title": title, "text_length": len(html)}


PARSER_REGISTRY: dict[str, HtmlParserAdapter] = {
    "tables": TableHtmlParser(),
    "text": TextHtmlParser(),
}


def lookup_parser(name: str) -> HtmlParserAdapter:
    try:
        return PARSER_REGISTRY[name]
    except KeyError as exc:
        raise PrivateAcquisitionError(
            AcquisitionFetchState.FAILED, f"unknown parser: {name}"
        ) from exc
