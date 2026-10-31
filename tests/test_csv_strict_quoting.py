"""CSV quoting that stdlib rejects must fail closed, not become a table.

P5 card t_6390a355: ``extract_csv_tables`` fed the decoded text to
``csv.reader`` with the default (tolerant) dialect. A document whose quoting
is structurally broken — an unterminated quote, or a stray character right
after a closing quote — was therefore *absorbed*: every following line was
swallowed into the same cell and the trailing text was silently reshaped
(``"123`` + newline + ``profit,456`` became one ``value`` of ``123\nprofit,456``).
``parsed`` stayed ``True`` with ``parse_note=None``, so a corrupt document
looked like a clean one and KPI extraction ran over invented cells.

The fix is ``strict=True`` on the *existing* ``csv.reader`` call, nothing
else: a ``csv.Error`` now reaches the pre-existing ``except csv.Error``
branch and the document fails closed with the pre-existing
``csv parse failed: <ExcType>`` note. This file pins that contract:

- unterminated quote and junk-after-closing-quote, at the head / middle /
  tail of the document, all yield ``parsed=False``,
  ``parse_note == "csv parse failed: Error"``, ``tables == []``, and never
  return the rows that were readable before the structural error;
- a readable prefix (header plus good rows) does not rescue the document;
- the note carries no raw text, URL, filename, input value or secret marker;
- repeating the call returns the identical result;
- every document ``strict`` still accepts keeps the *previous* contract
  field-for-field (``format``, ``parsed``, ``parse_note``,
  ``parser_version``, ``text``, ``text_chars`` and the whole
  ``tables``/``headers``/``rows``/``_source_rows`` payload), including
  quoted newlines (LF and CRLF), quoted commas, doubled quotes, Japanese
  text, empty cells, bare quotes inside an unquoted field, header-only /
  empty / whitespace-only input, short rows, surplus cells, duplicate
  headers, UTF-8 BOM, cp932 and the decode-failure path;
- the row/column caps still run *after* the whole document is parsed, so a
  structural error past the cap still fails the whole document closed while
  the normal cap-1 / cap / cap+1 tables and notes are unchanged;
- the existing ``extract_document`` filename/URL dispatch and the existing
  ``IrMonitorService`` record/timeline propagation carry
  ``parsed=False`` / ``parse_note`` / ``kpis == []`` without edits to the
  service.

``_legacy_extract_csv`` below is a faithful copy of the pre-change
implementation (default tolerant reader) and acts as the oracle for
"unchanged behaviour". It lives in the test only; the product keeps its
single ``csv.reader`` call. No test reaches the network (socket is blocked).
"""

from __future__ import annotations

import csv
import io
import socket
from pathlib import Path
from typing import Any

import pytest

from yowayowa.acquisition import documents
from yowayowa.acquisition.documents import (
    ExtractedDocument,
    extract_csv_tables,
    extract_document,
)
from yowayowa.acquisition.parsers import _dedupe
from yowayowa.acquisition.transport import TransportResponse
from yowayowa.services.ir_monitor_service import IrMonitorService, IrSourceDefinition

_FAILED_NOTE = "csv parse failed: Error"


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Block every socket so a passing test can never be a network accident."""

    def _blocked(*args: object, **kwargs: object) -> None:
        raise AssertionError("csv extraction attempted network access")

    monkeypatch.setattr(socket, "socket", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)


# ------------------------------------------------------------------ oracles


def _legacy_extract_csv(content: bytes) -> ExtractedDocument:
    """Pre-change ``extract_csv_tables``: the default (tolerant) reader.

    Copied verbatim from the implementation this card changes, so the
    'strict accepts it' tests can prove the result is unchanged rather than
    re-stating the expectations the change would make true by construction.
    """

    text, decode_note = documents._decode_csv_bytes(content)
    if decode_note is not None:
        return ExtractedDocument(format="csv", parsed=False, parse_note=decode_note)
    try:
        rows = list(csv.reader(io.StringIO(text)))
    except csv.Error:
        return ExtractedDocument(format="csv", parsed=False, parse_note="csv parse failed: Error")
    parse_note: str | None = None
    if len(rows) > documents._XLSX_MAX_ROWS:
        parse_note = documents._append_parse_note(
            parse_note, f"csv rows truncated at {documents._XLSX_MAX_ROWS}"
        )
    if any(len(row) > documents._XLSX_MAX_COLUMNS for row in rows[: documents._XLSX_MAX_ROWS]):
        parse_note = documents._append_parse_note(
            parse_note, f"csv columns truncated at {documents._XLSX_MAX_COLUMNS}"
        )
    rows = [row[: documents._XLSX_MAX_COLUMNS] for row in rows[: documents._XLSX_MAX_ROWS]]
    rows = [row for row in rows if any(cell.strip() for cell in row)]
    if not rows:
        note = "csv contained no readable rows"
        if parse_note is not None:
            note = documents._append_parse_note(note, parse_note)
        return ExtractedDocument(format="csv", parsed=False, parse_note=note)
    headers = rows[0]
    parsed_rows: list[dict[str, str]] = []
    for cells in rows[1:]:
        row: dict[str, str] = {}
        for column, header in enumerate(headers):
            key = header or documents._column_letter(column + 1)
            value = cells[column] if column < len(cells) else ""
            row[_dedupe(row, key)] = value
        parsed_rows.append(row)
    return ExtractedDocument(
        format="csv",
        parsed=True,
        parse_note=parse_note,
        tables=[{"headers": headers, "rows": parsed_rows, "_source_rows": rows}],
    )


def _stdlib_accepts(text: str) -> bool:
    """Independent check that stdlib strict parsing accepts this document."""

    try:
        list(csv.reader(io.StringIO(text), strict=True))
    except csv.Error:
        return False
    return True


# ------------------------------------------------- fail-closed (strict)

# Head / middle / tail placement of both structural defects. The defect is in
# a quoted field so the tolerant reader had to absorb the rest of the file.
_UNTERMINATED = {
    "head": b'metric,value\n"revenue,123\nprofit,456\n',
    "middle": b'metric,value\nrevenue,123\n"profit,456\ncost,789\n',
    "tail": b'metric,value\nrevenue,123\nprofit,456\n"cost,789\n',
}

_JUNK_AFTER_CLOSE = {
    "head": b'metric,value\n"revenue"x,123\nprofit,456\n',
    "middle": b'metric,value\nrevenue,123\n"profit"x,456\ncost,789\n',
    "tail": b'metric,value\nrevenue,123\nprofit,456\n"cost"x,789\n',
}


@pytest.mark.parametrize(("position", "content"), sorted(_UNTERMINATED.items()))
def test_unterminated_quote_fails_closed(position: str, content: bytes) -> None:
    extracted = extract_csv_tables(content)

    assert extracted.format == "csv"
    assert extracted.parsed is False
    assert extracted.parse_note == _FAILED_NOTE
    assert extracted.tables == []
    # the readable prefix must not survive as a table
    assert extracted.text is None
    assert extracted.text_chars == 0


@pytest.mark.parametrize(("position", "content"), sorted(_JUNK_AFTER_CLOSE.items()))
def test_junk_after_closing_quote_fails_closed(position: str, content: bytes) -> None:
    extracted = extract_csv_tables(content)

    assert extracted.format == "csv"
    assert extracted.parsed is False
    assert extracted.parse_note == _FAILED_NOTE
    assert extracted.tables == []


@pytest.mark.parametrize(
    "content",
    [
        pytest.param(b'metric,value\nrevenue,"123\nprofit,456\n', id="unterminated-doubled-open"),
        pytest.param(b'metric,value\n"revenue\n', id="unterminated-single-line"),
        pytest.param(b'"a\nb\nc\n', id="unterminated-no-header"),
        pytest.param(b'metric,value\nrevenue,"12"3"\n', id="quote-inside-quoted-field"),
        pytest.param(b'metric,value\nrevenue,"1"junk"\n', id="junk-between-quotes"),
    ],
)
def test_other_structural_quoting_errors_fail_closed(content: bytes) -> None:
    assert _stdlib_accepts(content.decode("utf-8")) is False
    extracted = extract_csv_tables(content)
    assert extracted.parsed is False
    assert extracted.parse_note == _FAILED_NOTE
    assert extracted.tables == []


def test_readable_prefix_does_not_rescue_the_document() -> None:
    """99 clean rows followed by one broken quote still fails closed."""

    good = b"metric,value\n" + b"".join(f"metric{index},{index}\n".encode() for index in range(99))
    extracted = extract_csv_tables(good + b'"revenue,123\nprofit,456\n')

    assert extracted.parsed is False
    assert extracted.parse_note == _FAILED_NOTE
    assert extracted.tables == []


def test_note_never_leaks_input_text_url_filename_or_secret() -> None:
    secret = "sk-live-CANARY-9f3a"
    content = f'metric,value\n{secret},"{secret}\n'.encode()
    extracted = extract_csv_tables(content)

    assert extracted.parse_note == _FAILED_NOTE
    assert secret not in (extracted.parse_note or "")
    assert "documents" not in (extracted.parse_note or "")
    assert "http" not in (extracted.parse_note or "")
    assert extracted.tables == []
    # the failure note is a fixed string, not a formatted exception message
    assert (extracted.parse_note or "").strip() == "csv parse failed: Error"


def test_failure_is_deterministic() -> None:
    content = b'metric,value\nrevenue,"123\nprofit,456\n'
    first = extract_csv_tables(content)
    second = extract_csv_tables(content)
    assert first == second
    assert first.parse_note == second.parse_note == _FAILED_NOTE


# ------------------------------------------- unchanged (strict-accepted)

_UNCHANGED_CASES: dict[str, bytes] = {
    "quoted-lf": b'metric,value\nrevenue,"line1\nline2"\nprofit,456\n',
    "quoted-crlf": b'metric,value\r\nrevenue,"line1\r\nline2"\r\nprofit,456\r\n',
    "quoted-comma": b'metric,value\nrevenue,"1,234"\nprofit,456\n',
    "doubled-quote": b'metric,value\nrevenue,"say ""hi"""\nprofit,456\n',
    "japanese": '指標,値\n売上高,"1,234"\n営業利益,456\n'.encode(),
    "empty-cells": b"a,b,c\n1,,3\n,,\n",
    "bare-quote-in-unquoted": b'metric,value\nrevenue,12"3\nprofit,456\n',
    "quoted-empty": b'a,b\n"",""\n',
    "header-only": b"a,b\n",
    "header-only-trailing-newline": b"a,b\n\n",
    "short-rows": b"a,b,c\n1\n1,2\n",
    "surplus-cells": b"a,b\n1,2,3,4\n",
    "duplicate-headers": b"a,a,a\n1,2,3\n",
    "dup-header-with-empty": b"a,,a\n1,2,3\n",
    "utf8-bom": b"\xef\xbb\xbfmetric,value\nrevenue,123\n",
    "cp932": "指標,値\n売上高,123\n".encode("cp932"),
    "empty": b"",
    "whitespace-only": b"   \n\t\n",
    "blank-lines-between": b"a,b\n\n1,2\n\n",
}


@pytest.mark.parametrize(("label", "content"), sorted(_UNCHANGED_CASES.items()))
def test_strict_accepted_documents_keep_the_previous_contract(label: str, content: bytes) -> None:
    text, decode_note = documents._decode_csv_bytes(content)
    if decode_note is None:
        # the fixture set must stay inside what stdlib strict accepts, else the
        # 'unchanged' claim would be vacuous for that case
        assert _stdlib_accepts(text) is True, label

    previous = _legacy_extract_csv(content)
    current = extract_csv_tables(content)

    # whole-value deep equality: every field, every row, in order
    assert current == previous
    assert current.parser_version == previous.parser_version
    assert current.text == previous.text
    assert current.text_chars == previous.text_chars
    assert current.parse_note == previous.parse_note
    assert current.parsed is previous.parsed
    if previous.tables:
        assert current.tables[0]["headers"] == previous.tables[0]["headers"]
        assert current.tables[0]["rows"] == previous.tables[0]["rows"]
        assert current.tables[0]["_source_rows"] == previous.tables[0]["_source_rows"]


def test_quoted_newline_still_produces_the_same_cells() -> None:
    extracted = extract_csv_tables(b'a,b\n"x\ny",2\n')

    assert extracted.parsed is True
    assert extracted.parse_note is None
    # the quoted newline stays inside one cell; strict does not split it
    assert extracted.tables[0]["rows"] == [{"a": "x\ny", "b": "2"}]


def test_bare_quote_in_unquoted_field_is_still_accepted() -> None:
    """Csv.Dialect.strict does not reject a quote inside an unquoted field."""

    extracted = extract_csv_tables(b'metric,value\nrevenue,12"3\n')

    assert extracted.parsed is True
    assert extracted.parse_note is None
    assert extracted.tables[0]["rows"] == [{"metric": "revenue", "value": '12"3'}]


def test_decode_failure_path_is_unchanged() -> None:
    # 0x81 0x20 is valid cp932 lead byte + invalid trail -> both decodes fail
    content = b"\x81\x20\x81\x20"
    extracted = extract_csv_tables(content)

    assert extracted == _legacy_extract_csv(content)
    assert extracted.parsed is False
    assert extracted.parse_note == ("csv decode failed (neither utf-8-sig nor cp932); not parsed")


def test_header_only_and_empty_notes_are_unchanged() -> None:
    for content in (b"a,b\n", b"", b"   \n\t\n"):
        assert extract_csv_tables(content) == _legacy_extract_csv(content)

    assert extract_csv_tables(b"a,b\n").parsed is True
    assert extract_csv_tables(b"a,b\n").parse_note is None
    assert extract_csv_tables(b"").parsed is False
    assert extract_csv_tables(b"").parse_note == "csv contained no readable rows"


# ------------------------------------------------- caps with strict parsing


@pytest.fixture
def cap3x2(monkeypatch: pytest.MonkeyPatch) -> tuple[int, int]:
    monkeypatch.setattr(documents, "_XLSX_MAX_ROWS", 3)
    monkeypatch.setattr(documents, "_XLSX_MAX_COLUMNS", 2)
    return 3, 2


@pytest.mark.parametrize(
    ("total_rows", "expect_note"),
    [(2, None), (3, None), (4, "csv rows truncated at 3")],
    ids=["cap-1", "cap", "cap+1"],
)
def test_row_cap_boundary_unchanged(
    cap3x2: tuple[int, int], total_rows: int, expect_note: str | None
) -> None:
    lines = ["a,b"] + [f"{index},{index}" for index in range(total_rows - 1)]
    content = ("\n".join(lines) + "\n").encode()

    extracted = extract_csv_tables(content)
    assert extracted.parsed is True
    assert extracted.parse_note == expect_note
    assert len(extracted.tables[0]["_source_rows"]) == min(total_rows, 3)
    assert extracted == _legacy_extract_csv(content)


@pytest.mark.parametrize(
    ("total_columns", "expect_note"),
    [(1, None), (2, None), (3, "csv columns truncated at 2")],
    ids=["cap-1", "cap", "cap+1"],
)
def test_column_cap_boundary_unchanged(
    cap3x2: tuple[int, int], total_columns: int, expect_note: str | None
) -> None:
    header = ",".join(f"c{index}" for index in range(total_columns))
    values = ",".join(str(index) for index in range(total_columns))
    content = f"{header}\n{values}\n".encode()

    extracted = extract_csv_tables(content)
    assert extracted.parsed is True
    assert extracted.parse_note == expect_note
    assert extracted == _legacy_extract_csv(content)


def test_structural_error_past_the_cap_still_fails_the_whole_document(
    cap3x2: tuple[int, int],
) -> None:
    """The cap is applied *after* strict parsing of the whole document."""

    content = b"a,b\n1,2\n3,4\n5,6\n7," + b'"unterminated' + b"\n8,9\n"

    extracted = extract_csv_tables(content)

    assert extracted.parsed is False
    assert extracted.parse_note == _FAILED_NOTE
    assert extracted.tables == []


def test_structural_error_past_the_cap_with_clean_prefix_fails_closed(
    cap3x2: tuple[int, int],
) -> None:
    content = b"a,b\n1,2\n3,4\n5,6\n7,8\n9,10\n11," + b'"oops\n'

    extracted = extract_csv_tables(content)

    assert extracted.parsed is False
    assert extracted.parse_note == _FAILED_NOTE
    assert extracted.tables == []


def test_error_at_the_cap_boundary_fails_closed(cap3x2: tuple[int, int]) -> None:
    # the broken row is the 3rd row, i.e. exactly the last row inside the cap
    content = b'a,b\n1,2\n3,"4\n'

    extracted = extract_csv_tables(content)

    assert extracted.parsed is False
    assert extracted.parse_note == _FAILED_NOTE
    assert extracted.tables == []


# ------------------------------------------------------ dispatch + IR path


def test_extract_document_dispatches_csv_by_filename_and_url() -> None:
    content = b'metric,value\nrevenue,"123\nprofit,456\n'

    by_name = extract_document(content=content, content_type=None, filename="x.csv")
    by_url = extract_document(
        content=content,
        content_type=None,
        filename=None,
        url="https://ir.example.co.jp/ir/x.csv",
    )

    for extracted in (by_name, by_url):
        assert extracted.format == "csv"
        assert extracted.parsed is False
        assert extracted.parse_note == _FAILED_NOTE
        assert extracted.tables == []


def test_extract_document_csv_accepting_case_is_unchanged() -> None:
    content = b"metric,value\nrevenue,123\n"
    extracted = extract_document(content=content, content_type=None, filename="x.csv")

    assert extracted == _legacy_extract_csv(content)
    assert extracted.parsed is True
    assert extracted.tables[0]["rows"] == [{"metric": "revenue", "value": "123"}]


class _StubTransport:
    """In-memory transport; no socket is opened."""

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


def test_ir_record_carries_fail_closed_state_and_no_kpis(tmp_path: Path) -> None:
    listing = (
        '<html><body><a href="/ir/broken.csv">\u6c7a\u7b97\u8cc7\u6599</a></body></html>'
    ).encode()
    transport = _StubTransport(
        {
            "/library/briefing.html": (200, "text/html; charset=utf-8", listing),
            "/ir/broken.csv": (200, "text/csv", b'metric,value\nrevenue,"123\nprofit,456\n'),
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
    assert record.parsed is False
    assert record.parse_note == _FAILED_NOTE
    # the pre-existing 'not parsed' branch keeps KPI extraction empty
    assert record.kpis == []
    assert _FAILED_NOTE in record.notes


def test_ir_record_csv_accepting_case_still_parses(tmp_path: Path) -> None:
    listing = (
        '<html><body><a href="/ir/ok.csv">\u6c7a\u7b97\u8cc7\u6599</a></body></html>'
    ).encode()
    transport = _StubTransport(
        {
            "/library/briefing.html": (200, "text/html; charset=utf-8", listing),
            "/ir/ok.csv": (200, "text/csv", b"metric,value\nrevenue,123\n"),
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
    assert record.parsed is True
    assert record.parse_note is None
