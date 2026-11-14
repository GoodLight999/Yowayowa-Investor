"""XLSX display sheets must be paired with their worksheet part by relationship.

One defect, pinned here independently of the other document-extraction tests:
``_sheet_names`` read only the ``<sheet>`` names out of ``xl/workbook.xml`` and
the reader then opened ``xl/worksheets/sheet<N>.xml`` by *display position*,
ignoring the workbook's ``r:id`` and ``xl/_rels/workbook.xml.rels``. A workbook
whose parts are stored in another order therefore came back ``parsed=True``
with the year columns swapped — ``Current`` holding the prior year's number and
``Prior`` holding the current one — and a workbook whose parts are not named
``sheetN.xml`` came back ``parsed=False`` with a missing-parts note even though
every worksheet part was present.

Contract pinned here
--------------------
- Each ``<sheet>`` in display order is joined to its part through
  ``r:id`` -> ``xl/_rels/workbook.xml.rels``. ``sheetId`` and the display
  position never select a part.
- Relative (``worksheets/foo.xml``) and package-root
  (``/xl/worksheets/foo.xml``) targets both resolve to a ZIP part name under
  ``xl/worksheets/``. Arbitrary part file names are read.
- A standard chartsheet relationship is parsed and skipped, never KPI-ified;
  other worksheets keep their own names and values. The skip is named once,
  at the end of the note, as ``xlsx non-worksheet sheets skipped: <count>``.
- The legacy minimal layout (no ``r:id`` on any sheet and no relationships
  part) keeps the positional ``sheetN.xml`` reading, and its result is
  field-for-field identical to the relationship-resolved workbook.
- ``r:id`` or a relationships part on either side means relationship mode: a
  gap is refused, never filled by the positional guess. Unresolved id,
  duplicate id, missing ``r:id``, empty target, ``TargetMode="External"``,
  unknown relationship type and an invalid target all make the whole document
  ``parsed=False`` / ``tables=[]`` with the fixed note
  ``xlsx worksheet relationship invalid``.
- A resolved part that is absent stays a missing part: the count note and the
  partial extraction contract are unchanged, and the absent part is never
  substituted by a wrong ``sheetN.xml``.
- Malformed XML keeps the existing ``xlsx parse failed: <Type>`` contract.
- The fixed note leaks nothing: no target, URL, sheet name, cell value,
  exception text or secret marker.

All fixtures are built in-process and sockets are blocked for the whole module.
"""

from __future__ import annotations

import io
import socket
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from yowayowa.acquisition.documents import (
    _read_shared_strings,
    _read_sheet_rows,
    extract_document,
    extract_xlsx_tables,
)
from yowayowa.acquisition.transport import TransportResponse
from yowayowa.services.ir_monitor_service import (
    IrMonitorService,
    IrSourceDefinition,
)

MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
DOC_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
REL_TYPE_WORKSHEET = f"{DOC_REL_NS}/worksheet"
REL_TYPE_CHARTSHEET = f"{DOC_REL_NS}/chartsheet"

INVALID_RELATIONSHIP_NOTE = "xlsx worksheet relationship invalid"
SKIP_NOTE_PREFIX = "xlsx non-worksheet sheets skipped"
SECRET_MARKER = "SECRET-TARGET-9c1f"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every fixture is local bytes: reaching the network is a defect, not a skip."""

    def _blocked(*args: object, **kwargs: object) -> None:
        raise AssertionError("xlsx relationship tests must not open a socket")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked)


# --------------------------------------------------------------- zip builders


def _cell(reference: str, text: str, *, inline: bool = False) -> str:
    if inline:
        return f"<c r='{reference}' t='inlineStr'><is><t>{text}</t></is></c>"
    return f"<c r='{reference}'><v>{text}</v></c>"


def _sheet(*rows: str) -> str:
    body = "".join(f'<row r="{index}">{cells}</row>' for index, cells in enumerate(rows, start=1))
    return f'<worksheet xmlns="{MAIN_NS}"><sheetData>{body}</sheetData></worksheet>'


def _single_value_sheet(label: str, value: str) -> str:
    """Header row + one label/value row — the shape KPI extraction reads."""

    return _sheet(
        _cell("A1", "KPI", inline=True) + _cell("B1", "当連結", inline=True),
        _cell("A2", label, inline=True) + _cell("B2", value),
    )


def _workbook_xml(sheets: list[tuple[str, int, str | None]], *, prefix: str = "r") -> str:
    """``(name, sheetId, r:id or None)`` in display order."""

    declared = "".join(
        f'<sheet name="{name}" sheetId="{sheet_id}"'
        + (f' {prefix}:id="{rel_id}"' if rel_id is not None else "")
        + "/>"
        for name, sheet_id, rel_id in sheets
    )
    return (
        f'<workbook xmlns="{MAIN_NS}" xmlns:{prefix}="{DOC_REL_NS}">'
        f"<sheets>{declared}</sheets></workbook>"
    )


def _rels_xml(
    *entries: tuple[str, str, str], extra_attributes: dict[str, str] | None = None
) -> str:
    """``(Id, Type, Target)`` relationships; ``extra_attributes`` for edge cases."""

    body = ""
    for rel_id, rel_type, target in entries:
        attributes = {"Id": rel_id, "Type": rel_type, "Target": target}
        if extra_attributes:
            attributes.update(extra_attributes)
        rendered = " ".join(f'{key}="{value}"' for key, value in attributes.items())
        body += f"<Relationship {rendered}/>"
    return f'<Relationships xmlns="{PKG_REL_NS}">{body}</Relationships>'


def _xlsx(parts: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, xml in parts.items():
            archive.writestr(name, xml)
    return buffer.getvalue()


def _values(extracted: Any) -> dict[str, str]:
    """``{sheet name: last cell of the first data row}`` for compact assertions."""

    values: dict[str, str] = {}
    for table in extracted.tables:
        row = table["rows"][0]
        cells = [str(value) for value in row.values()]
        values[str(table["name"])] = cells[-1]
    return values


# ------------------------------------------------ the reported workbooks (CTO)


def reordered_workbook() -> bytes:
    """Current -> sheet2.xml (200), Prior -> sheet1.xml (100): parts reversed."""

    return _xlsx(
        {
            "xl/workbook.xml": _workbook_xml([("Current", 9, "rCurrent"), ("Prior", 4, "rPrior")]),
            "xl/_rels/workbook.xml.rels": _rels_xml(
                ("rCurrent", REL_TYPE_WORKSHEET, "worksheets/sheet2.xml"),
                ("rPrior", REL_TYPE_WORKSHEET, "worksheets/sheet1.xml"),
            ),
            "xl/worksheets/sheet1.xml": _single_value_sheet("売上高", "100"),
            "xl/worksheets/sheet2.xml": _single_value_sheet("売上高", "200"),
        }
    )


def nonsequential_workbook() -> bytes:
    """Current -> worksheets/financial.xml (300): no sheetN.xml part at all."""

    return _xlsx(
        {
            "xl/workbook.xml": _workbook_xml([("Current", 99, "rCurrent")]),
            "xl/_rels/workbook.xml.rels": _rels_xml(
                ("rCurrent", REL_TYPE_WORKSHEET, "worksheets/financial.xml"),
            ),
            "xl/worksheets/financial.xml": _single_value_sheet("売上高", "300"),
        }
    )


def legacy_workbook() -> bytes:
    """The minimal layout: no ``r:id`` and no relationships part anywhere."""

    return _xlsx(
        {
            "xl/workbook.xml": _workbook_xml([("Current", 9, None), ("Prior", 4, None)]),
            "xl/worksheets/sheet1.xml": _single_value_sheet("売上高", "200"),
            "xl/worksheets/sheet2.xml": _single_value_sheet("売上高", "100"),
        }
    )


# ------------------------------------------------------ the reported defect


def test_reordered_parts_do_not_swap_the_year_columns() -> None:
    extracted = extract_xlsx_tables(reordered_workbook())

    assert extracted.format == "xlsx"
    assert extracted.parsed is True
    assert extracted.parse_note is None
    assert [table["name"] for table in extracted.tables] == ["Current", "Prior"]
    assert _values(extracted) == {"Current": "200", "Prior": "100"}
    # The positional guess would have produced exactly the swap below.
    assert _values(extracted) != {"Current": "100", "Prior": "200"}


def test_nonsequential_worksheet_part_is_found() -> None:
    extracted = extract_xlsx_tables(nonsequential_workbook())

    assert extracted.parsed is True
    assert extracted.parse_note is None
    assert [table["name"] for table in extracted.tables] == ["Current"]
    assert _values(extracted) == {"Current": "300"}


def test_sheet_id_never_selects_the_part() -> None:
    """sheetId 99 / 4 / 1 disagree with the stored part order on purpose."""

    content = _xlsx(
        {
            "xl/workbook.xml": _workbook_xml(
                [("Current", 99, "rA"), ("Prior", 4, "rB"), ("Older", 1, "rC")]
            ),
            "xl/_rels/workbook.xml.rels": _rels_xml(
                ("rA", REL_TYPE_WORKSHEET, "worksheets/third.xml"),
                ("rB", REL_TYPE_WORKSHEET, "worksheets/first.xml"),
                ("rC", REL_TYPE_WORKSHEET, "worksheets/second.xml"),
            ),
            "xl/worksheets/first.xml": _single_value_sheet("売上高", "11"),
            "xl/worksheets/second.xml": _single_value_sheet("売上高", "22"),
            "xl/worksheets/third.xml": _single_value_sheet("売上高", "33"),
        }
    )

    extracted = extract_xlsx_tables(content)

    assert extracted.parsed is True
    assert _values(extracted) == {"Current": "33", "Prior": "11", "Older": "22"}


# ------------------------------------------------------------ target forms


@pytest.mark.parametrize(
    "target",
    [
        "worksheets/ledger.xml",
        "/xl/worksheets/ledger.xml",
        "./worksheets/ledger.xml",
        "../xl/worksheets/ledger.xml",
        "worksheets/sub/ledger.xml",
    ],
    ids=["relative", "package-root", "dot-relative", "dotdot-then-package", "nested"],
)
def test_internal_target_forms_resolve_to_the_same_part(target: str) -> None:
    content = _xlsx(
        {
            "xl/workbook.xml": _workbook_xml([("Current", 1, "r1")]),
            "xl/_rels/workbook.xml.rels": _rels_xml(("r1", REL_TYPE_WORKSHEET, target)),
            "xl/worksheets/ledger.xml": _single_value_sheet("売上高", "300"),
            "xl/worksheets/sub/ledger.xml": _single_value_sheet("売上高", "300"),
        }
    )

    extracted = extract_xlsx_tables(content)

    assert extracted.parsed is True
    assert extracted.parse_note is None
    assert _values(extracted) == {"Current": "300"}


# --------------------------------------------------------------- chartsheets


def test_chartsheet_is_skipped_while_worksheets_keep_their_values() -> None:
    content = _xlsx(
        {
            "xl/workbook.xml": _workbook_xml(
                [("Current", 1, "rCur"), ("Graph", 2, "rChart"), ("Prior", 3, "rPri")]
            ),
            "xl/_rels/workbook.xml.rels": _rels_xml(
                ("rCur", REL_TYPE_WORKSHEET, "worksheets/sheet1.xml"),
                ("rChart", REL_TYPE_CHARTSHEET, "chartsheets/sheet1.xml"),
                ("rPri", REL_TYPE_WORKSHEET, "worksheets/sheet2.xml"),
            ),
            "xl/worksheets/sheet1.xml": _single_value_sheet("売上高", "200"),
            "xl/worksheets/sheet2.xml": _single_value_sheet("売上高", "100"),
            "xl/chartsheets/sheet1.xml": "<chartsheet/>",
        }
    )

    extracted = extract_xlsx_tables(content)

    assert extracted.parsed is True
    assert extracted.parse_note == f"{SKIP_NOTE_PREFIX}: 1"
    assert [table["name"] for table in extracted.tables] == ["Current", "Prior"]
    assert _values(extracted) == {"Current": "200", "Prior": "100"}
    # A chartsheet is not a sheet with rows: it must not become a table.
    assert "Graph" not in [table["name"] for table in extracted.tables]


def test_skip_note_is_named_once_for_several_chartsheets() -> None:
    content = _xlsx(
        {
            "xl/workbook.xml": _workbook_xml(
                [("Graph1", 1, "rC1"), ("Current", 2, "rCur"), ("Graph2", 3, "rC2")]
            ),
            "xl/_rels/workbook.xml.rels": _rels_xml(
                ("rC1", REL_TYPE_CHARTSHEET, "chartsheets/sheet1.xml"),
                ("rCur", REL_TYPE_WORKSHEET, "worksheets/sheet1.xml"),
                ("rC2", REL_TYPE_CHARTSHEET, "chartsheets/sheet2.xml"),
            ),
            "xl/worksheets/sheet1.xml": _single_value_sheet("売上高", "200"),
            "xl/chartsheets/sheet1.xml": "<chartsheet/>",
            "xl/chartsheets/sheet2.xml": "<chartsheet/>",
        }
    )

    extracted = extract_xlsx_tables(content)

    assert extracted.parsed is True
    assert extracted.parse_note == f"{SKIP_NOTE_PREFIX}: 2"
    assert (extracted.parse_note or "").count(SKIP_NOTE_PREFIX) == 1
    assert _values(extracted) == {"Current": "200"}


def test_all_chartsheets_keeps_the_no_readable_sheets_reason() -> None:
    content = _xlsx(
        {
            "xl/workbook.xml": _workbook_xml([("Graph1", 1, "rC1"), ("Graph2", 2, "rC2")]),
            "xl/_rels/workbook.xml.rels": _rels_xml(
                ("rC1", REL_TYPE_CHARTSHEET, "chartsheets/sheet1.xml"),
                ("rC2", REL_TYPE_CHARTSHEET, "chartsheets/sheet2.xml"),
            ),
            "xl/chartsheets/sheet1.xml": "<chartsheet/>",
            "xl/chartsheets/sheet2.xml": "<chartsheet/>",
        }
    )

    extracted = extract_xlsx_tables(content)

    assert extracted.parsed is False
    assert extracted.tables == []
    assert extracted.parse_note == (f"xlsx contained no readable sheets; {SKIP_NOTE_PREFIX}: 2")


# ------------------------------------------------------------------- legacy


def test_legacy_minimal_workbook_keeps_its_positional_reading() -> None:
    extracted = extract_xlsx_tables(legacy_workbook())

    assert extracted.parsed is True
    assert extracted.parse_note is None
    # sheet1.xml holds 200 for Current, sheet2.xml holds 100 for Prior.
    assert _values(extracted) == {"Current": "200", "Prior": "100"}


def test_relationship_workbook_and_legacy_workbook_are_identical() -> None:
    """Same sheet order and same parts: every field must match, not just values."""

    relationship = _xlsx(
        {
            "xl/workbook.xml": _workbook_xml([("Current", 9, "rA"), ("Prior", 4, "rB")]),
            "xl/_rels/workbook.xml.rels": _rels_xml(
                ("rA", REL_TYPE_WORKSHEET, "worksheets/sheet1.xml"),
                ("rB", REL_TYPE_WORKSHEET, "worksheets/sheet2.xml"),
            ),
            "xl/worksheets/sheet1.xml": _single_value_sheet("売上高", "200"),
            "xl/worksheets/sheet2.xml": _single_value_sheet("売上高", "100"),
        }
    )

    assert extract_xlsx_tables(relationship) == extract_xlsx_tables(legacy_workbook())


def test_relationship_mode_never_falls_back_to_positional_parts() -> None:
    """A rels part alone (no r:id) is relationship mode: the gap is refused."""

    content = _xlsx(
        {
            "xl/workbook.xml": _workbook_xml([("Current", 1, None), ("Prior", 2, None)]),
            "xl/_rels/workbook.xml.rels": _rels_xml(
                ("r1", REL_TYPE_WORKSHEET, "worksheets/sheet1.xml"),
                ("r2", REL_TYPE_WORKSHEET, "worksheets/sheet2.xml"),
            ),
            "xl/worksheets/sheet1.xml": _single_value_sheet("売上高", "200"),
            "xl/worksheets/sheet2.xml": _single_value_sheet("売上高", "100"),
        }
    )

    extracted = extract_xlsx_tables(content)

    assert extracted.parsed is False
    assert extracted.tables == []
    assert extracted.parse_note == INVALID_RELATIONSHIP_NOTE


def test_mixed_r_id_presence_is_refused() -> None:
    content = _xlsx(
        {
            "xl/workbook.xml": _workbook_xml([("Current", 1, "r1"), ("Prior", 2, None)]),
            "xl/_rels/workbook.xml.rels": _rels_xml(
                ("r1", REL_TYPE_WORKSHEET, "worksheets/sheet1.xml"),
            ),
            "xl/worksheets/sheet1.xml": _single_value_sheet("売上高", "200"),
            "xl/worksheets/sheet2.xml": _single_value_sheet("売上高", "100"),
        }
    )

    extracted = extract_xlsx_tables(content)

    assert extracted.parsed is False
    assert extracted.tables == []
    assert extracted.parse_note == INVALID_RELATIONSHIP_NOTE


def test_workbook_without_workbook_part_stays_unreadable() -> None:
    content = _xlsx({"xl/worksheets/sheet1.xml": _single_value_sheet("x", "1")})

    extracted = extract_xlsx_tables(content)

    assert extracted.parsed is False
    assert extracted.tables == []
    assert extracted.parse_note == "xlsx contained no readable sheets"


# --------------------------------------------- invalid relationship (fail-closed)


def _invalid_relationship_document(
    *,
    workbook: str | None = None,
    rels: str | None = None,
    worksheets: dict[str, str] | None = None,
) -> bytes:
    parts: dict[str, str] = {
        "xl/workbook.xml": workbook
        if workbook is not None
        else _workbook_xml([("Current", 1, "r1")]),
    }
    if rels is not None:
        parts["xl/_rels/workbook.xml.rels"] = rels
    for name, xml in (
        worksheets or {"xl/worksheets/sheet1.xml": _single_value_sheet("x", "1")}
    ).items():
        parts[name] = xml
    return _xlsx(parts)


_INVALID_CASES: list[tuple[str, bytes]] = [
    (
        "unresolved-id",
        _invalid_relationship_document(
            rels=_rels_xml(("rOther", REL_TYPE_WORKSHEET, "worksheets/sheet1.xml"))
        ),
    ),
    (
        "no-rels-part",
        _invalid_relationship_document(rels=None),
    ),
    (
        "empty-r-id",
        _invalid_relationship_document(
            workbook=_workbook_xml([("Current", 1, "")]),
            rels=_rels_xml(("r1", REL_TYPE_WORKSHEET, "worksheets/sheet1.xml")),
        ),
    ),
    (
        "duplicate-id",
        _invalid_relationship_document(
            workbook=_workbook_xml([("Current", 1, "r1"), ("Prior", 2, "r1")]),
            rels=_rels_xml(
                ("r1", REL_TYPE_WORKSHEET, "worksheets/sheet1.xml"),
                ("r1", REL_TYPE_WORKSHEET, "worksheets/sheet2.xml"),
            ),
            worksheets={
                "xl/worksheets/sheet1.xml": _single_value_sheet("x", "1"),
                "xl/worksheets/sheet2.xml": _single_value_sheet("x", "2"),
            },
        ),
    ),
    (
        "duplicate-unreferenced-id",
        _invalid_relationship_document(
            rels=_rels_xml(
                ("r1", REL_TYPE_WORKSHEET, "worksheets/sheet1.xml"),
                ("styles", f"{DOC_REL_NS}/styles", "styles.xml"),
                ("styles", f"{DOC_REL_NS}/styles", "styles2.xml"),
            ),
        ),
    ),
    (
        "external-target-mode",
        _invalid_relationship_document(
            rels=_rels_xml(
                ("r1", REL_TYPE_WORKSHEET, "worksheets/sheet1.xml"),
                extra_attributes={"TargetMode": "External"},
            )
        ),
    ),
    (
        "unknown-relationship-type",
        _invalid_relationship_document(
            rels=_rels_xml(("r1", f"{DOC_REL_NS}/dialogsheet", "worksheets/sheet1.xml"))
        ),
    ),
    (
        "unknown-relationship-type-empty",
        _invalid_relationship_document(rels=_rels_xml(("r1", "", "worksheets/sheet1.xml"))),
    ),
]

_BAD_TARGETS = [
    ("empty", ""),
    ("blank", "   "),
    ("query", "worksheets/sheet1.xml?token=1"),
    ("fragment", "worksheets/sheet1.xml#frag"),
    ("backslash", "worksheets\\\\sheet1.xml"),
    ("scheme-http", "http://example.co.jp/xl/worksheets/sheet1.xml"),
    ("scheme-file", "file:///xl/worksheets/sheet1.xml"),
    ("authority", "//example.co.jp/xl/worksheets/sheet1.xml"),
    ("escape-outside", "../../etc/passwd"),
    ("outside-worksheets", "worksheets/../media/image.xml"),
    ("not-xml", "worksheets/sheet1.txt"),
    ("directory", "worksheets/"),
    ("no-part-name", "worksheets/.xml"),
    ("absolute-outside", "/media/image.xml"),
]


@pytest.mark.parametrize(
    ("case_id", "content"), _INVALID_CASES, ids=[case for case, _ in _INVALID_CASES]
)
def test_invalid_sheet_part_pairing_fails_the_whole_document(case_id: str, content: bytes) -> None:
    extracted = extract_xlsx_tables(content)

    assert extracted.format == "xlsx"
    assert extracted.parsed is False
    assert extracted.tables == []
    assert extracted.parse_note == INVALID_RELATIONSHIP_NOTE
    assert case_id  # parameterisation id is the reported case


@pytest.mark.parametrize(
    ("case_id", "target"), _BAD_TARGETS, ids=[case for case, _ in _BAD_TARGETS]
)
def test_invalid_worksheet_target_fails_the_whole_document(case_id: str, target: str) -> None:
    content = _invalid_relationship_document(
        rels=_rels_xml(("r1", REL_TYPE_WORKSHEET, target)),
    )

    extracted = extract_xlsx_tables(content)

    assert extracted.parsed is False
    assert extracted.tables == []
    assert extracted.parse_note == INVALID_RELATIONSHIP_NOTE
    assert case_id


def test_fixed_note_never_exposes_targets_names_values_or_exceptions() -> None:
    content = _invalid_relationship_document(
        workbook=_workbook_xml([(f"{SECRET_MARKER}-sheet", 1, "r1")]),
        rels=_rels_xml(("r1", REL_TYPE_WORKSHEET, f"../{SECRET_MARKER}/x.xml")),
        worksheets={"xl/worksheets/sheet1.xml": _single_value_sheet(SECRET_MARKER, "4242")},
    )

    extracted = extract_document(
        content=content,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"{SECRET_MARKER}.xlsx",
        url=f"https://example.co.jp/ir/{SECRET_MARKER}.xlsx?token={SECRET_MARKER}",
    )

    assert extracted.parse_note == INVALID_RELATIONSHIP_NOTE
    assert extracted.tables == []
    note = extracted.parse_note or ""
    assert SECRET_MARKER not in note
    assert "example.co.jp" not in note
    assert "worksheets" not in note
    assert "4242" not in note
    assert "Error" not in note and "Traceback" not in note
    assert SECRET_MARKER not in repr(extracted)


@pytest.mark.parametrize(
    ("case_id", "content"),
    [
        (
            "malformed-rels",
            _xlsx(
                {
                    "xl/workbook.xml": _workbook_xml([("Current", 1, "r1")]),
                    "xl/_rels/workbook.xml.rels": "<Relationships><Relationship",
                    "xl/worksheets/sheet1.xml": _single_value_sheet("x", "1"),
                }
            ),
        ),
        (
            "malformed-workbook",
            _xlsx(
                {
                    "xl/workbook.xml": "<workbook><sheets><sheet",
                    "xl/_rels/workbook.xml.rels": _rels_xml(
                        ("r1", REL_TYPE_WORKSHEET, "worksheets/sheet1.xml")
                    ),
                    "xl/worksheets/sheet1.xml": _single_value_sheet("x", "1"),
                }
            ),
        ),
        (
            "nul-in-target",
            # XML cannot carry a literal NUL, so such a target can only arrive
            # as a malformed part: the parse-failed contract is the fail-closed
            # outcome here.
            _xlsx(
                {
                    "xl/workbook.xml": _workbook_xml([("Current", 1, "r1")]),
                    "xl/_rels/workbook.xml.rels": _rels_xml(
                        ("r1", REL_TYPE_WORKSHEET, "worksheets/sheet1.xml\x00")
                    ),
                    "xl/worksheets/sheet1.xml": _single_value_sheet("x", "1"),
                }
            ),
        ),
    ],
    ids=["malformed-rels", "malformed-workbook", "nul-in-target"],
)
def test_malformed_relationship_xml_keeps_the_parse_failed_contract(
    case_id: str, content: bytes
) -> None:
    extracted = extract_xlsx_tables(content)

    assert extracted.parsed is False
    assert extracted.tables == []
    assert extracted.parse_note == "xlsx parse failed: ParseError"
    assert case_id


@pytest.mark.parametrize(
    "target",
    [
        "worksheets/sheet1.xml\x00",
        "worksheets/sheet1.xml\\n",
        "worksheets/sheet1.xml\t",
    ],
    ids=["nul", "newline", "tab"],
)
def test_target_control_characters_are_refused_before_any_read(target: str) -> None:
    """The NUL guard is exercised directly: XML text can never carry one."""

    from yowayowa.acquisition.documents import (
        _resolve_worksheet_target,
        _XlsxRelationshipError,
    )

    with pytest.raises(_XlsxRelationshipError):
        _resolve_worksheet_target(target, None)


def test_internal_target_passes_the_resolver_unchanged() -> None:
    from yowayowa.acquisition.documents import _resolve_worksheet_target

    assert _resolve_worksheet_target("worksheets/sheet1.xml", None) == "xl/worksheets/sheet1.xml"
    assert _resolve_worksheet_target("/xl/worksheets/a.xml", "Internal") == "xl/worksheets/a.xml"


# ------------------------------------------------------------- missing parts


def test_absent_resolved_part_is_counted_and_not_filled_by_sheet_n() -> None:
    """Ghost's part is absent while sheet2.xml exists: it must stay missing."""

    content = _xlsx(
        {
            "xl/workbook.xml": _workbook_xml([("Current", 1, "rCur"), ("Ghost", 2, "rGhost")]),
            "xl/_rels/workbook.xml.rels": _rels_xml(
                ("rCur", REL_TYPE_WORKSHEET, "worksheets/current.xml"),
                ("rGhost", REL_TYPE_WORKSHEET, "worksheets/ghost.xml"),
            ),
            "xl/worksheets/current.xml": _single_value_sheet("売上高", "200"),
            "xl/worksheets/sheet2.xml": _single_value_sheet("売上高", "999"),
        }
    )

    extracted = extract_xlsx_tables(content)

    assert extracted.parsed is True
    assert extracted.parse_note == "xlsx worksheet parts missing: 1"
    assert [table["name"] for table in extracted.tables] == ["Current"]
    assert _values(extracted) == {"Current": "200"}
    assert "999" not in repr(extracted)


def test_every_resolved_part_absent_keeps_the_no_readable_reason() -> None:
    content = _xlsx(
        {
            "xl/workbook.xml": _workbook_xml([("Current", 1, "rCur")]),
            "xl/_rels/workbook.xml.rels": _rels_xml(
                ("rCur", REL_TYPE_WORKSHEET, "worksheets/financial.xml")
            ),
        }
    )

    extracted = extract_xlsx_tables(content)

    assert extracted.parsed is False
    assert extracted.tables == []
    assert extracted.parse_note == (
        "xlsx contained no readable sheets; xlsx worksheet parts missing: 1"
    )


# --------------------------------------------------------------- dispatch


@pytest.mark.parametrize(
    "call",
    [
        {"filename": "kpi.xlsx", "content_type": None},
        {
            "filename": None,
            "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        },
    ],
    ids=["by-extension", "by-content-type"],
)
def test_extract_document_transports_the_resolved_parts(call: dict[str, Any]) -> None:
    extracted = extract_document(content=nonsequential_workbook(), **call)

    assert extracted.format == "xlsx"
    assert extracted.parsed is True
    assert extracted.parse_note is None
    assert _values(extracted) == {"Current": "300"}


# -------------------------------------------------- direct helper compat


def test_read_sheet_rows_keeps_its_positional_contract_and_accepts_a_part() -> None:
    content = reordered_workbook()
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        shared = _read_shared_strings(archive)
        positional = _read_sheet_rows(archive, 1, shared)
        by_part = _read_sheet_rows(archive, 1, shared, part="xl/worksheets/sheet2.xml")
        diagnostics: dict[str, Any] = {}
        absent = _read_sheet_rows(
            archive, 1, shared, part="xl/worksheets/missing.xml", diagnostics=diagnostics
        )

    assert positional == [["KPI", "当連結"], ["売上高", "100"]]
    assert by_part == [["KPI", "当連結"], ["売上高", "200"]]
    assert absent == []
    assert diagnostics.get("missing_part") is True


def test_reading_the_same_bytes_again_is_identical() -> None:
    content = reordered_workbook()

    first = extract_xlsx_tables(content)
    second = extract_xlsx_tables(content)

    assert first == second
    assert first.parser_version == "doc-v1"
    assert [table["_source_rows"] for table in first.tables] == [
        [["KPI", "当連結"], ["売上高", "200"]],
        [["KPI", "当連結"], ["売上高", "100"]],
    ]


# ------------------------------------------------------------ IR pipeline


class _StubTransport:
    """In-process transport: deterministic bytes per resource, no sockets."""

    def __init__(self, *, listing: str, documents: dict[str, bytes]) -> None:
        self._listing = listing
        self._documents = documents
        self.calls: list[str] = []

    def fetch(
        self,
        method: str,
        resource: str,
        *,
        params: Any = None,
        headers: Any = None,
        data: Any = None,
    ) -> TransportResponse:
        self.calls.append(f"{method} {resource}")
        if resource in self._documents:
            return TransportResponse(
                status_code=200,
                url=f"https://example.co.jp{resource}",
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                text="",
                content=self._documents[resource],
                elapsed_ms=1.0,
            )
        return TransportResponse(
            status_code=200,
            url=f"https://example.co.jp{resource}",
            content_type="text/html; charset=utf-8",
            text=self._listing,
            content=self._listing.encode("utf-8"),
            elapsed_ms=1.0,
        )


def _monitor(tmp_path: Path, content: bytes) -> tuple[Any, _StubTransport, IrMonitorService]:
    filename = "kessan.xlsx"
    listing = f'<html><body><a href="{filename}">決算短信</a></body></html>'
    transport = _StubTransport(listing=listing, documents={f"/ir/{filename}": content})
    service = IrMonitorService(
        data_dir=tmp_path,
        transport_factory=lambda source: transport,
        sources=[
            IrSourceDefinition(
                source_id="ir-test",
                symbol="7203",
                provider="official-ir",
                listing_url="https://example.co.jp/ir/",
            )
        ],
        now=lambda: datetime(2026, 10, 25, tzinfo=UTC),
    )
    return service.monitor("ir-test"), transport, service


def test_ir_pipeline_carries_the_relationship_resolved_value(tmp_path: Path) -> None:
    outcome, transport, _service = _monitor(tmp_path, reordered_workbook())

    records = [record for record in outcome.documents if record.url.endswith("kessan.xlsx")]
    assert len(records) == 1
    record = records[0]
    assert record.fetched is True
    assert record.format == "xlsx"
    assert record.parsed is True
    assert record.parse_note is None
    # Current is sheet2.xml (200) and Prior is sheet1.xml (100): the KPI must be
    # the current year, never the swapped positional read.
    revenue = [kpi for kpi in record.kpis if kpi.get("kpi") == "revenue"]
    assert revenue, record.kpis
    assert float(revenue[0]["value"]) == 200.0
    assert transport.calls == ["GET /ir/", "GET /ir/kessan.xlsx"]


def test_ir_pipeline_propagates_the_fixed_relationship_note(tmp_path: Path) -> None:
    content = _invalid_relationship_document(
        rels=_rels_xml(("rOther", REL_TYPE_WORKSHEET, "worksheets/sheet1.xml"))
    )

    outcome, _transport, service = _monitor(tmp_path, content)

    records = [record for record in outcome.documents if record.url.endswith("kessan.xlsx")]
    assert len(records) == 1
    record = records[0]
    assert record.parsed is False
    assert record.parse_note == INVALID_RELATIONSHIP_NOTE
    assert record.kpis == []
    assert INVALID_RELATIONSHIP_NOTE in record.notes

    entries = service.timeline("7203")
    assert entries, "a new document must still reach the timeline"
    payload = entries[0]["payload"]
    assert payload["parsed"] is False
    assert payload["parse_note"] == INVALID_RELATIONSHIP_NOTE
    assert payload["kpis"] == []
