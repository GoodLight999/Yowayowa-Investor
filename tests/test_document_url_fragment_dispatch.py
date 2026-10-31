"""URL query/fragment must not change which extractor a document reaches.

Independent coverage (no xfail) for ``extension_of``'s URL fallback and
``extract_document``'s dispatch: the extension comes from the URL *path*
only, so ``summary.csv#tab=2`` and ``brief.pdf#page=2`` reach the same
extractor as their plain counterparts instead of degrading into the
unknown format. Filenames keep their literal meaning (``#``/``?`` inside
``filename`` are name characters, never URL syntax), the URL is never
percent-decoded, and no test here opens a socket.
"""

from __future__ import annotations

import dataclasses
import io
import socket
import zipfile

import pytest

from yowayowa.acquisition import documents as documents_module
from yowayowa.acquisition.documents import (
    ExtractedDocument,
    extension_of,
    extract_document,
)

_CSV_BYTES = b"kpi,value\nsales,1200\nprofit,80\n"

# Every suffix variant must resolve to the same path extension as the bare
# URL: query-only, fragment-only, both, and '?'/'/' hiding inside a fragment.
_URL_SUFFIXES = [
    "",
    "?token=abc&v=2",
    "#tab=2",
    "?token=abc&v=2#tab=2",
    "#tab?x=1",
    "#tab/2",
]


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def _blocked(*args: object, **kwargs: object) -> None:
        raise AssertionError("this test module must not open a socket")

    monkeypatch.setattr(socket.socket, "connect", _blocked)


# ------------------------------------------------- URL fallback: identity


@pytest.mark.parametrize("name", ["summary.csv", "brief.pdf", "book.xlsx", "notes.html"])
@pytest.mark.parametrize("suffix", _URL_SUFFIXES)
def test_url_query_and_fragment_keep_the_path_extension(name: str, suffix: str) -> None:
    url = f"https://example.co.jp/ir/{name}{suffix}"
    assert extension_of(None, url) == f".{name.rsplit('.', 1)[1]}"


def test_fragment_or_query_cannot_override_or_fake_the_extension() -> None:
    assert extension_of(None, "https://example.co.jp/ir/summary.csv#fake.pdf") == ".csv"
    assert extension_of(None, "https://example.co.jp/ir/summary.csv?fake=.pdf") == ".csv"
    assert extension_of(None, "https://example.co.jp/ir/summary.pdf?x=1#brief.xlsx") == ".pdf"


# ------------------------------------------------- URL fallback: boundaries


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://example.co.jp/ir/BRIEF.PDF", ".pdf"),  # lowercase rule kept
        ("https://example.co.jp/ir/archive.tar.csv", ".csv"),  # last dot wins
        ("https://example.co.jp/ir/a.csv/", ".csv"),  # trailing slash rule kept
        ("https://example.co.jp", None),  # no path component
        ("https://example.co.jp/", None),  # root path only
        ("https://example.co.jp/ir/download", None),  # no extension
        ("https://example.co.jp/ir/download?x=1", None),  # no extension + query
        ("https://example.co.jp/ir/.csv", None),  # dotfile stays None
        ("https://example.co.jp/ir/#f", None),  # empty tail before fragment
        ("", None),  # empty URL
        ("https://example.co.jp/ir/report%2Ecsv", None),  # never percent-decoded
        ("https://[::1/bad", None),  # malformed bracket URL -> None, no raise
        ("https://]example/ir/a.csv", None),  # unmatched bracket -> None, no raise
    ],
)
def test_extension_of_url_boundary_rules(url: str, expected: str | None) -> None:
    assert extension_of(None, url) == expected


# ------------------------------------------------- filename rules (kept)


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("filing.csv", ".csv"),  # plain filename
        ("report#2.csv", ".csv"),  # '#' is a name character
        ("report?v2.pdf", ".pdf"),  # '?' is a name character
        ("weird.csv#frag", ".csv#frag"),  # names are never repaired into URLs
    ],
)
def test_filename_keeps_its_literal_meaning_and_outranks_the_url(
    filename: str, expected: str | None
) -> None:
    conflicting_url = "https://example.co.jp/ir/brief.pdf#page=2"
    assert extension_of(filename, None) == expected
    assert extension_of(filename, conflicting_url) == expected


def test_extension_of_without_filename_or_url_is_none() -> None:
    assert extension_of(None, None) is None


def test_nonempty_filename_wins_over_url_and_content_type() -> None:
    extracted = extract_document(
        content=_CSV_BYTES,
        content_type="application/pdf",
        filename="filing.csv",
        url="https://example.co.jp/ir/brief.pdf#page=2",
    )
    assert extracted.format == "csv"
    assert extracted.parsed


# ------------------------------------------------- extract_document dispatch


@pytest.mark.parametrize("suffix", _URL_SUFFIXES)
def test_same_csv_bytes_reach_the_same_document_regardless_of_url(suffix: str) -> None:
    plain = extract_document(
        content=_CSV_BYTES,
        content_type=None,
        filename=None,
        url="https://example.co.jp/ir/summary.csv",
    )
    variant = extract_document(
        content=_CSV_BYTES,
        content_type=None,
        filename=None,
        url=f"https://example.co.jp/ir/summary.csv{suffix}",
    )
    assert plain.format == variant.format == "csv"
    assert plain.parsed and variant.parsed
    assert dataclasses.asdict(plain) == dataclasses.asdict(variant)


def _minimal_xlsx() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "xl/workbook.xml",
            '<workbook><sheets><sheet name="Data"/></sheets></workbook>',
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            "<worksheet><sheetData>"
            '<row r="1"><c r="A1" t="inlineStr"><is><t>kpi</t></is></c></row>'
            '<row r="2"><c r="A2"><v>1200</v></c></row>'
            "</sheetData></worksheet>",
        )
    return buffer.getvalue()


@pytest.mark.parametrize("suffix", _URL_SUFFIXES)
def test_same_xlsx_bytes_reach_the_same_document_regardless_of_url(suffix: str) -> None:
    content = _minimal_xlsx()
    plain = extract_document(
        content=content,
        content_type=None,
        filename=None,
        url="https://example.co.jp/ir/book.xlsx",
    )
    variant = extract_document(
        content=content,
        content_type=None,
        filename=None,
        url=f"https://example.co.jp/ir/book.xlsx{suffix}",
    )
    assert plain.format == variant.format == "xlsx"
    assert plain.parsed and variant.parsed
    assert dataclasses.asdict(plain) == dataclasses.asdict(variant)


def _spy(
    monkeypatch: pytest.MonkeyPatch,
    attribute: str,
    sentinel: ExtractedDocument,
) -> list[object]:
    calls: list[object] = []

    def _record(content: object) -> ExtractedDocument:
        calls.append(content)
        return sentinel

    monkeypatch.setattr(documents_module, attribute, _record)
    return calls


def test_fragmented_pdf_url_dispatches_once_to_the_pdf_extractor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = ExtractedDocument(format="pdf", parsed=True, text="x", text_chars=1)
    calls = _spy(monkeypatch, "extract_pdf_document", sentinel)
    content = b"%PDF-1.4 in-memory bytes"
    result = extract_document(
        content=content,
        content_type=None,
        filename=None,
        url="https://example.co.jp/ir/brief.pdf#page=2",
    )
    assert calls == [content]  # exactly one dispatch, the exact same bytes
    assert result is sentinel


def test_fragmented_html_url_dispatches_once_to_the_html_extractor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = ExtractedDocument(format="html", parsed=True)
    calls = _spy(monkeypatch, "extract_html_tables", sentinel)
    content = b"<table><tr><th>a</th></tr></table>"
    result = extract_document(
        content=content,
        content_type=None,
        filename=None,
        url="https://example.co.jp/ir/notes.html#section-3",
    )
    # The HTML extractor's real contract takes the decoded text, exactly once.
    assert calls == [content.decode("utf-8")]
    assert result is sentinel


# ------------------------------------------------- preserved contracts


def test_unknown_format_note_unchanged_for_extensionless_url() -> None:
    extracted = extract_document(
        content=b"\x00\x01binary",
        content_type="application/zip",
        filename=None,
        url="https://example.co.jp/ir/blob",
    )
    assert (extracted.format, extracted.parsed, extracted.parse_note) == (
        "unknown",
        False,
        "no extractor for this document format",
    )


def test_content_type_fallback_unchanged_without_extension() -> None:
    extracted = extract_document(
        content=_CSV_BYTES,
        content_type="text/csv; charset=utf-8",
        filename=None,
        url="https://example.co.jp/ir/download",
    )
    assert extracted.format == "csv"
    assert extracted.parsed
