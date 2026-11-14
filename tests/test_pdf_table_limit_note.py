"""Independent acceptance tests for PDF table limit diagnostics and parse_note.

Task t_ef599ebf:
When ``len(tables) >= _MAX_TABLES_PER_PDF`` is reached in ``extract_pdf_tables``,
the keyword-only ``diagnostics`` dict records ``table_limit_reached=True``.
In ``extract_pdf_document``, this flag causes ``_append_parse_note`` to append
``f"pdf table limit reached at {_MAX_TABLES_PER_PDF}"`` to ``parse_note``.

Contract pinned here:
- Exact and cap+1 inputs get the limit reached note; cap-1 does not.
- Verified on both runtime cap=2 (local monkeypatch) and default 200.
- 0 tables, empty pages, and header-only pages do not advance the table count.
- Returned table dictionaries have identical keys and deep-equal content.
- Re-executing with the same input produces deterministic, deep-equal results.
- Direct ``extract_pdf_tables`` callers (without diagnostics or with empty dict)
  behave safely: omit diagnostics -> unchanged list; with diagnostics -> additive flag.
- Generators raising after the cap prove that consumption stops cleanly at the cap:
  no extra ``next()``, no exception interruptions, no double walk.
- Coexists with existing text cap note (fixed order: text capped -> incomplete -> limit).
- Traversal failure before reaching cap yields ``pdf table extraction incomplete`` only.
- Image PDFs (parsed=False) and missing optional extra stay note-free.
- Secret markers / raw contents are never exposed in parse_note.
- Dispatched via ``extract_document`` and preserved through ``IrMonitorService``.
"""

from __future__ import annotations

import socket
import sys
import types
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import yowayowa.acquisition.documents as docs_mod
from yowayowa.acquisition.documents import (
    extract_document,
    extract_pdf_document,
    extract_pdf_tables,
)
from yowayowa.acquisition.transport import TransportResponse
from yowayowa.services.ir_monitor_service import (
    IrMonitorService,
    IrSourceDefinition,
)


@pytest.fixture(autouse=True)
def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enforce complete socket isolation for all tests in this module."""

    def _blocked(*args: object, **kwargs: object) -> None:
        raise AssertionError("network access attempted during PDF extraction")

    monkeypatch.setattr(socket, "socket", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)


# ------------------------------------------------------------- pdfminer stub helpers


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
    text: object = "sample text layer",
    page_factory: object = None,
) -> tuple[_PdfCalls, type]:
    """Install an in-memory fake ``pdfminer`` into ``sys.modules``."""

    calls = _PdfCalls()
    package: Any = types.ModuleType("pdfminer")
    high_level: Any = types.ModuleType("pdfminer.high_level")
    layout: Any = types.ModuleType("pdfminer.layout")
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
        try:
            return page_factory(LTTextContainer)
        except TypeError:
            return page_factory()

    high_level.extract_text = extract_text
    if page_factory is not None:
        high_level.extract_pages = extract_pages
    layout.LTTextContainer = LTTextContainer

    monkeypatch.setitem(sys.modules, "pdfminer", package)
    monkeypatch.setitem(sys.modules, "pdfminer.high_level", high_level)
    monkeypatch.setitem(sys.modules, "pdfminer.layout", layout)
    return calls, LTTextContainer


_STANDARD_TABLE_RUNS = (
    ("HeaderA", 10.0, 100.0),
    ("HeaderB", 100.0, 100.0),
    ("Row1ValA", 10.0, 50.0),
    ("Row1ValB", 100.0, 50.0),
)


def _make_table_page(
    container: type,
    runs: tuple[tuple[str, float, float], ...] = _STANDARD_TABLE_RUNS,
) -> list[Any]:
    return [container([_FakeLine(txt, x, y) for txt, x, y in runs])]


def _make_header_only_page(container: type) -> list[Any]:
    runs = (("HeaderOnlyA", 10.0, 100.0), ("HeaderOnlyB", 100.0, 100.0))
    return [container([_FakeLine(txt, x, y) for txt, x, y in runs])]


def _make_empty_page(container: type) -> list[Any]:
    return []


# ------------------------------------------------------------- cap=2 local fixture suite


class TestLocalCapTwo:
    @pytest.fixture(autouse=True)
    def _set_cap_two(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(docs_mod, "_MAX_TABLES_PER_PDF", 2)

    def test_cap_minus_one_has_no_limit_note(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_pdf_stub(
            monkeypatch,
            page_factory=lambda c: [_make_table_page(c)],
        )
        doc = extract_pdf_document(b"%PDF-1.4")
        assert doc.parsed is True
        assert len(doc.tables) == 1
        assert doc.parse_note is None

    def test_cap_exact_has_limit_reached_note(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_pdf_stub(
            monkeypatch,
            page_factory=lambda c: [_make_table_page(c), _make_table_page(c)],
        )
        doc = extract_pdf_document(b"%PDF-1.4")
        assert doc.parsed is True
        assert len(doc.tables) == 2
        assert doc.parse_note == "pdf table limit reached at 2"

    def test_cap_plus_one_stops_at_cap_and_has_limit_reached_note(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_pdf_stub(
            monkeypatch,
            page_factory=lambda c: [
                _make_table_page(c),
                _make_table_page(c),
                _make_table_page(c),
            ],
        )
        doc = extract_pdf_document(b"%PDF-1.4")
        assert doc.parsed is True
        assert len(doc.tables) == 2
        assert doc.parse_note == "pdf table limit reached at 2"

    def test_zero_tables_has_no_limit_note(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_pdf_stub(
            monkeypatch,
            page_factory=lambda c: [],
        )
        doc = extract_pdf_document(b"%PDF-1.4")
        assert doc.parsed is True
        assert doc.tables == []
        assert doc.parse_note is None

    def test_mixed_empty_and_header_only_pages_do_not_count_as_tables(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Empty pages and header-only (single row) pages must not advance table count."""
        pages = [
            _make_header_only_page,
            _make_empty_page,
            _make_table_page,
            _make_header_only_page,
            _make_table_page,
            _make_table_page,  # 3rd table, should not be reached because cap is 2
        ]

        def factory(c: Any) -> Iterator[Any]:
            for p in pages:
                yield p(c)

        _install_pdf_stub(monkeypatch, page_factory=factory)
        doc = extract_pdf_document(b"%PDF-1.4")
        assert doc.parsed is True
        assert len(doc.tables) == 2
        assert doc.parse_note == "pdf table limit reached at 2"

    def test_table_fields_deep_equal_under_and_at_cap(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Extracted table dict structure must remain completely preserved."""
        _install_pdf_stub(
            monkeypatch,
            page_factory=lambda c: [_make_table_page(c), _make_table_page(c)],
        )
        doc = extract_pdf_document(b"%PDF-1.4")
        assert len(doc.tables) == 2
        expected_keys = {"headers", "header_xs", "rows", "row_xs", "cells"}
        for tbl in doc.tables:
            assert set(tbl.keys()) == expected_keys
            assert tbl["headers"] == ["HeaderA", "HeaderB"]
            assert tbl["rows"] == [{"HeaderA": "Row1ValA", "HeaderB": "Row1ValB"}]
            assert tbl["cells"] == [["Row1ValA", "Row1ValB"]]

    def test_reexecution_is_deterministic(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def factory(c: Any) -> list[Any]:
            return [
                _make_table_page(c),
                _make_table_page(c),
                _make_table_page(c),
            ]

        _install_pdf_stub(monkeypatch, page_factory=factory)
        first = extract_pdf_document(b"%PDF-1.4")
        second = extract_pdf_document(b"%PDF-1.4")
        assert first.tables == second.tables
        assert first.parse_note == second.parse_note == "pdf table limit reached at 2"
        assert first.parsed == second.parsed is True


# ------------------------------------------------------------- default cap=200 suite


class TestDefaultCapTwoHundred:
    def test_default_cap_is_two_hundred(self) -> None:
        assert docs_mod._MAX_TABLES_PER_PDF == 200

    def test_cap_minus_one_199_tables_no_note(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def factory(c: Any) -> Iterator[Any]:
            for _ in range(199):
                yield _make_table_page(c)

        _install_pdf_stub(monkeypatch, page_factory=factory)
        doc = extract_pdf_document(b"%PDF-1.4")
        assert doc.parsed is True
        assert len(doc.tables) == 199
        assert doc.parse_note is None

    def test_cap_exact_200_tables_has_note(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def factory(c: Any) -> Iterator[Any]:
            for _ in range(200):
                yield _make_table_page(c)

        _install_pdf_stub(monkeypatch, page_factory=factory)
        doc = extract_pdf_document(b"%PDF-1.4")
        assert doc.parsed is True
        assert len(doc.tables) == 200
        assert doc.parse_note == "pdf table limit reached at 200"

    def test_cap_plus_one_201_tables_stops_at_200_with_note(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def factory(c: Any) -> Iterator[Any]:
            for _ in range(201):
                yield _make_table_page(c)

        _install_pdf_stub(monkeypatch, page_factory=factory)
        doc = extract_pdf_document(b"%PDF-1.4")
        assert doc.parsed is True
        assert len(doc.tables) == 200
        assert doc.parse_note == "pdf table limit reached at 200"


# ------------------------------------------------------------- page consumption & error avoidance


class TestPageConsumption:
    def test_generator_raises_after_cap_proves_no_extra_next_or_double_walk(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A generator that raises immediately after the cap must NOT raise or set incomplete."""
        monkeypatch.setattr(docs_mod, "_MAX_TABLES_PER_PDF", 2)
        consumed_count = 0

        def factory(c: Any) -> Iterator[Any]:
            nonlocal consumed_count
            consumed_count += 1
            yield _make_table_page(c)
            consumed_count += 1
            yield _make_table_page(c)
            # If next() is called past cap 2, this raises!
            consumed_count += 1
            raise RuntimeError("Should never be called past cap!")

        _install_pdf_stub(monkeypatch, page_factory=factory)
        doc = extract_pdf_document(b"%PDF-1.4")
        assert doc.parsed is True
        assert len(doc.tables) == 2
        assert doc.parse_note == "pdf table limit reached at 2"
        # Consumed exactly 2 pages, never called page 3:
        assert consumed_count == 2

    def test_default_cap_generator_raises_at_201_consumed_exactly_200(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        consumed_count = 0

        def factory(c: Any) -> Iterator[Any]:
            nonlocal consumed_count
            for i in range(1, 205):
                if i > 200:
                    raise RuntimeError("Should never be called past 200!")
                consumed_count += 1
                yield _make_table_page(c)

        _install_pdf_stub(monkeypatch, page_factory=factory)
        doc = extract_pdf_document(b"%PDF-1.4")
        assert doc.parsed is True
        assert len(doc.tables) == 200
        assert doc.parse_note == "pdf table limit reached at 200"
        assert consumed_count == 200


# ------------------------------------------------------------- direct extract_pdf_tables suite


class TestDirectExtractPdfTables:
    def test_direct_call_without_diagnostics_omitted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(docs_mod, "_MAX_TABLES_PER_PDF", 2)
        _install_pdf_stub(
            monkeypatch,
            page_factory=lambda c: [
                _make_table_page(c),
                _make_table_page(c),
                _make_table_page(c),
            ],
        )
        tables = extract_pdf_tables(b"%PDF-1.4")
        assert isinstance(tables, list)
        assert len(tables) == 2

    def test_direct_call_with_diagnostics_dict_records_limit_reached(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(docs_mod, "_MAX_TABLES_PER_PDF", 2)
        _install_pdf_stub(
            monkeypatch,
            page_factory=lambda c: [
                _make_table_page(c),
                _make_table_page(c),
                _make_table_page(c),
            ],
        )
        diagnostics: dict[str, Any] = {}
        tables = extract_pdf_tables(b"%PDF-1.4", diagnostics=diagnostics)
        assert len(tables) == 2
        assert diagnostics.get("table_limit_reached") is True

    def test_direct_call_under_cap_does_not_set_table_limit_reached(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(docs_mod, "_MAX_TABLES_PER_PDF", 2)
        _install_pdf_stub(
            monkeypatch,
            page_factory=lambda c: [_make_table_page(c)],
        )
        diagnostics: dict[str, Any] = {}
        tables = extract_pdf_tables(b"%PDF-1.4", diagnostics=diagnostics)
        assert len(tables) == 1
        assert "table_limit_reached" not in diagnostics


# ------------------------------------------------------------- note ordering & coexistence


class TestNoteCoexistence:
    def test_text_cap_and_table_limit_coexistence_and_order(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(docs_mod, "_MAX_TABLES_PER_PDF", 2)
        _install_pdf_stub(
            monkeypatch,
            text="x" * 200_005,
            page_factory=lambda c: [_make_table_page(c), _make_table_page(c)],
        )
        doc = extract_pdf_document(b"%PDF-1.4")
        assert doc.parsed is True
        assert len(doc.tables) == 2
        assert doc.parse_note == "text capped at 200000 chars; pdf table limit reached at 2"

    def test_exception_before_cap_gives_incomplete_note_only(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When an exception occurs before cap is reached, table_traversal_incomplete is set."""
        monkeypatch.setattr(docs_mod, "_MAX_TABLES_PER_PDF", 2)

        def factory(c: Any) -> Iterator[Any]:
            yield _make_table_page(c)
            raise ValueError("corrupt stream on page 2")

        _install_pdf_stub(monkeypatch, page_factory=factory)
        doc = extract_pdf_document(b"%PDF-1.4")
        assert doc.parsed is True
        assert len(doc.tables) == 1
        assert doc.parse_note == "pdf table extraction incomplete"
        assert "limit" not in (doc.parse_note or "")

    def test_text_capped_plus_incomplete_plus_table_limit_order(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Synthetic verification of ordering: text capped -> incomplete -> table limit."""
        monkeypatch.setattr(docs_mod, "_MAX_TABLES_PER_PDF", 2)
        # Create a document where extract_pdf_tables sets both diagnostics flags
        orig_extract_tables = docs_mod.extract_pdf_tables

        def fake_extract_tables(
            content: bytes, *, diagnostics: dict[str, Any] | None = None
        ) -> list[dict[str, Any]]:
            res = orig_extract_tables(content, diagnostics=diagnostics)
            if diagnostics is not None:
                diagnostics["table_traversal_incomplete"] = True
                diagnostics["table_limit_reached"] = True
            return res

        monkeypatch.setattr(docs_mod, "extract_pdf_tables", fake_extract_tables)
        _install_pdf_stub(
            monkeypatch,
            text="x" * 200_001,
            page_factory=lambda c: [_make_table_page(c), _make_table_page(c)],
        )
        doc = extract_pdf_document(b"%PDF-1.4")
        expected = (
            "text capped at 200000 chars; pdf table extraction incomplete; "
            "pdf table limit reached at 2"
        )
        assert doc.parse_note == expected


# ------------------------------------------------------------- contracts & leak prevention


class TestContractsAndSafety:
    def test_image_pdf_parsed_false_stays_note_free(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Image PDF has parsed=False; should not run table extraction or add limit note."""
        _install_pdf_stub(monkeypatch, text="", page_factory=lambda c: [_make_table_page(c)])
        doc = extract_pdf_document(b"%PDF-1.4")
        assert doc.parsed is False
        assert doc.tables == []
        assert "limit" not in (doc.parse_note or "")

    def test_missing_pdfminer_extra_fails_cleanly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setitem(sys.modules, "pdfminer.high_level", None)
        doc = extract_pdf_document(b"%PDF-1.4")
        assert doc.parsed is False
        assert doc.parse_note is not None
        assert "operator-ir" in doc.parse_note
        assert "limit" not in doc.parse_note

    def test_secret_markers_not_exposed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(docs_mod, "_MAX_TABLES_PER_PDF", 2)
        secret_name = "TOP_SECRET_FILENAME_TOKEN_123.pdf"
        secret_url = "https://example.com/vault/SECRET_QUERY_987.pdf?auth=token"
        runs = (
            ("SECRET_HEADER", 10.0, 100.0),
            ("SECRET_HEADER2", 100.0, 100.0),
            ("SECRET_VALUE", 10.0, 50.0),
            ("SECRET_VALUE2", 100.0, 50.0),
        )
        _install_pdf_stub(
            monkeypatch,
            page_factory=lambda c: [
                _make_table_page(c, runs),
                _make_table_page(c, runs),
            ],
        )
        doc = extract_document(
            content=b"%PDF-1.4",
            content_type="application/pdf",
            filename=secret_name,
            url=secret_url,
        )
        assert doc.parse_note == "pdf table limit reached at 2"
        assert "TOP_SECRET" not in (doc.parse_note or "")
        assert "SECRET_QUERY" not in (doc.parse_note or "")
        assert "SECRET_HEADER" not in (doc.parse_note or "")
        assert "SECRET_VALUE" not in (doc.parse_note or "")


# ------------------------------------------------------------- service dispatch & IR monitor


class _StubTransport:
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


class TestServiceDispatchAndIrMonitor:
    def test_extract_document_dispatches_pdf_with_limit_note(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(docs_mod, "_MAX_TABLES_PER_PDF", 2)
        _install_pdf_stub(
            monkeypatch,
            page_factory=lambda c: [_make_table_page(c), _make_table_page(c)],
        )
        doc = extract_document(
            content=b"%PDF-1.4",
            content_type="application/pdf",
            filename="financials.pdf",
        )
        assert doc.format == "pdf"
        assert doc.parsed is True
        assert len(doc.tables) == 2
        assert doc.parse_note == "pdf table limit reached at 2"

    def test_ir_monitor_preserves_pdf_table_limit_parse_note(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(docs_mod, "_MAX_TABLES_PER_PDF", 2)
        _install_pdf_stub(
            monkeypatch,
            text="IR briefing doc",
            page_factory=lambda c: [_make_table_page(c), _make_table_page(c)],
        )

        listing = (
            b'<html><body><a href="/ir/report.pdf">'
            b"\xe6\xb1\xba\xe7\xae\x97\xe8\xb3\x87\xe6\x96\x99</a></body></html>"
        )
        transport = _StubTransport(
            {
                "/library/briefing.html": (200, "text/html; charset=utf-8", listing),
                "/ir/report.pdf": (200, "application/pdf", b"%PDF-1.4"),
            }
        )
        service = IrMonitorService(
            data_dir=tmp_path,
            transport_factory=lambda source: transport,
            sources=[
                IrSourceDefinition(
                    source_id="test-ir",
                    symbol="9999.T",
                    provider="ir.test.co.jp",
                    listing_url="https://ir.test.co.jp/library/briefing.html",
                )
            ],
        )

        outcome = service.monitor("test-ir")
        assert len(outcome.documents) == 1
        record = outcome.documents[0]
        assert record.format == "pdf"
        assert record.parsed is True
        assert record.parse_note == "pdf table limit reached at 2"
