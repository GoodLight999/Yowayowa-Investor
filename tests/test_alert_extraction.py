"""P1D earnings-calendar extraction from alert mail bodies.

Fixtures reproduce the *shape* of the broker notification mail (CRLF line
endings, a U+25A0 section header, full-width digits/parentheses/colon) with
FICTIONAL companies, codes, and dates. No real message content is used.
"""

from __future__ import annotations

from datetime import date

from yowayowa.acquisition.alerts import (
    EarningsAnnouncement,
    extract_earnings_calendar,
    normalize_notification_text,
)

# Section header: full-width parentheses around the reference date.
US_SECTION = (
    "\u25a0\u6c7a\u7b97\u30ab\u30ec\u30f3\u30c0\u30fc\uff082026/09/23\uff09\u66f4\u65b0\u9298\u67c4"
)
JP_SECTION = (
    "\u25a0\u6c7a\u7b97\u30ab\u30ec\u30f3\u30c0\u30fc\uff082026/09/07\uff09\u66f4\u65b0\u9298\u67c4"
)
# Section header whose date is the announcement date itself ("1 business day before").
SECTION_DATE_ONLY = (
    "\u25a0\u6c7a\u7b97\u767a\u8868\u65e5\uff082026/08/12\uff09"
    "1\u55b6\u696d\u65e5\u524d\u9298\u67c4"
)

US_BODY = "\r\n".join(
    [
        US_SECTION,
        "\u30b5\u30f3\u30d7\u30eb\u30c6\u30c3\u30af(SMPL):2026/10/21",
        "\u30a2\u30eb\u30d5\u30a1\u30c7\u30fc\u30bf(ALFA):2026/10/21",
    ]
)

JP_BODY = "\r\n".join(
    [
        JP_SECTION,
        # Full-width code digits and full-width colon.
        "\u30c6\u30b9\u30c8\u5de5\u696d\uff08\uff11\uff12\uff13\uff14\uff09\uff1a2026/10/14",
    ]
)

# A name that itself contains parentheses: the LAST parenthesised group is the code.
PARENTHESIS_NAME_BODY = "\r\n".join(
    [
        US_SECTION,
        "\u30c6\u30b9\u30c8(\u65e7)\u30db\u30fc\u30eb\u30c7\u30a3\u30f3\u30b0\u30b9(4444):2026/10/20",
    ]
)


def test_us_section_lines_yield_us_events() -> None:
    events, notes = extract_earnings_calendar(US_BODY)
    assert notes == []
    assert [(event.symbol, event.market) for event in events] == [
        ("SMPL", "us"),
        ("ALFA", "us"),
    ]
    assert all(event.announcement_date == date(2026, 10, 21) for event in events)


def test_us_events_keep_name_raw_symbol_and_raw_line() -> None:
    events, _ = extract_earnings_calendar(US_BODY)
    first = events[0]
    assert first.name == "\u30b5\u30f3\u30d7\u30eb\u30c6\u30c3\u30af"
    assert first.raw_symbol == "SMPL"
    assert first.raw_line == "\u30b5\u30f3\u30d7\u30eb\u30c6\u30c3\u30af(SMPL):2026/10/21"
    assert first.section == US_SECTION


def test_jp_numeric_code_is_suffixed_with_t() -> None:
    events, notes = extract_earnings_calendar(
        "\r\n".join([JP_SECTION, "\u30c6\u30b9\u30c8\u5de5\u696d(1234):2026/10/14"])
    )
    assert notes == []
    assert events[0].symbol == "1234.T"
    assert events[0].market == "jp"


def test_jp_alphanumeric_code_is_suffixed_with_t() -> None:
    events, _ = extract_earnings_calendar(
        "\r\n".join([JP_SECTION, "\u30c6\u30b9\u30c8\u5de5\u696d(123A):2026/10/14"])
    )
    assert events[0].symbol == "123A.T"
    assert events[0].market == "jp"


def test_full_width_code_and_colon_are_normalized() -> None:
    events, notes = extract_earnings_calendar(JP_BODY)
    assert notes == []
    assert len(events) == 1
    event = events[0]
    assert event.symbol == "1234.T"
    assert event.market == "jp"
    assert event.announcement_date == date(2026, 10, 14)
    # The raw code keeps the character form the provider actually sent.
    assert event.raw_symbol == "\uff11\uff12\uff13\uff14"


def test_full_width_name_is_preserved_verbatim() -> None:
    events, _ = extract_earnings_calendar(JP_BODY)
    assert events[0].name == "\u30c6\u30b9\u30c8\u5de5\u696d"


def test_section_date_from_full_width_parentheses_header() -> None:
    events, _ = extract_earnings_calendar(US_BODY)
    assert events[0].announcement_date == date(2026, 10, 21)


def test_section_date_is_used_when_line_has_no_date() -> None:
    """`name(code)` alone takes the section date (never an invented date)."""
    events, notes = extract_earnings_calendar(
        "\r\n".join([SECTION_DATE_ONLY, "\u30c6\u30b9\u30c8\u30de\u30a4\u30af\u30ed(6871)"])
    )
    assert notes == []
    assert len(events) == 1
    assert events[0].symbol == "6871.T"
    assert events[0].announcement_date == date(2026, 8, 12)


def test_line_date_wins_over_section_date() -> None:
    events, _ = extract_earnings_calendar(
        "\r\n".join(
            [
                SECTION_DATE_ONLY,
                "\u30c6\u30b9\u30c8\u30de\u30a4\u30af\u30ed(6871):2026/11/01",
            ]
        )
    )
    assert events[0].announcement_date == date(2026, 11, 1)


def test_line_without_date_and_without_section_is_not_invented() -> None:
    events, notes = extract_earnings_calendar("\u30c6\u30b9\u30c8\u4f01\u696d(1234)")
    assert events == []
    assert len(notes) == 1
    assert "no announcement date and no section date" in notes[0]


def test_unrecognized_code_is_dropped_with_a_note() -> None:
    events, notes = extract_earnings_calendar(
        "\r\n".join([US_SECTION, "\u30c6\u30b9\u30c8\u4f01\u696d(TOOLONGCODE99):2026/10/21"])
    )
    assert events == []
    assert any("unrecognized instrument code" in note for note in notes)


def test_unrecognized_line_is_dropped_with_a_note() -> None:
    events, notes = extract_earnings_calendar(
        "\r\n".join([US_SECTION, "\u3053\u308c\u306f\u666e\u901a\u306e\u672c\u6587\u3067\u3059"])
    )
    assert events == []
    assert any("unrecognized alert line" in note for note in notes)


def test_duplicate_symbol_and_date_are_collapsed() -> None:
    events, _ = extract_earnings_calendar(
        "\r\n".join(
            [
                US_SECTION,
                "\u30c6\u30b9\u30c8(SMPL):2026/10/21",
                "\u30c6\u30b9\u30c8(SMPL):2026/10/21",
            ]
        )
    )
    assert len(events) == 1


def test_same_symbol_and_date_from_different_sections_are_collapsed() -> None:
    events, _ = extract_earnings_calendar(
        "\r\n".join(
            [
                US_SECTION,
                "\u30c6\u30b9\u30c8(SMPL):2026/10/21",
                "\u5225\u898b\u51fa\u3057\uff082026/10/21\uff09\u66f4\u65b0\u9298\u67c4",
                "\u5225\u540d\u524d(SMPL):2026/10/21",
            ]
        )
    )
    assert len(events) == 1


def test_same_symbol_with_different_dates_is_kept_twice() -> None:
    events, _ = extract_earnings_calendar(
        "\r\n".join(
            [
                US_SECTION,
                "\u30c6\u30b9\u30c8(SMPL):2026/10/21",
                "\u30c6\u30b9\u30c8(SMPL):2026/11/21",
            ]
        )
    )
    assert [event.announcement_date for event in events] == [date(2026, 10, 21), date(2026, 11, 21)]


def test_empty_body_yields_no_events_and_no_notes() -> None:
    assert extract_earnings_calendar("") == ([], [])


def test_whitespace_only_body_yields_no_events() -> None:
    assert extract_earnings_calendar("\r\n   \r\n\r\n") == ([], [])


def test_invalid_calendar_date_is_dropped_not_raised() -> None:
    events, notes = extract_earnings_calendar(
        "\r\n".join([US_SECTION, "\u30c6\u30b9\u30c8(SMPL):2026/13/45"])
    )
    assert events == []
    assert any("unusable announcement date" in note for note in notes)


def test_invalid_section_date_falls_back_to_no_date() -> None:
    events, notes = extract_earnings_calendar(
        "\r\n".join(
            [
                "\u25a0\u6c7a\u7b97\u30ab\u30ec\u30f3\u30c0\u30fc\uff082026/13/45\uff09\u66f4\u65b0\u9298\u67c4",
                "\u30c6\u30b9\u30c8(SMPL)",
            ]
        )
    )
    assert events == []
    assert any("no announcement date" in note for note in notes)


def test_parenthesis_in_name_uses_the_last_group_as_the_code() -> None:
    events, notes = extract_earnings_calendar(PARENTHESIS_NAME_BODY)
    assert notes == []
    assert events[0].symbol == "4444.T"
    assert events[0].raw_symbol == "4444"


def test_section_string_is_recorded_for_each_event() -> None:
    events, _ = extract_earnings_calendar(US_BODY)
    assert {event.section for event in events} == {US_SECTION}


def test_section_is_none_when_no_header_precedes_the_line() -> None:
    events, _ = extract_earnings_calendar("\u30c6\u30b9\u30c8(SMPL):2026/10/21")
    assert events[0].section is None


def test_events_are_pydantic_models_with_date_typed_field() -> None:
    events, _ = extract_earnings_calendar(US_BODY)
    assert isinstance(events[0], EarningsAnnouncement)
    assert isinstance(events[0].announcement_date, date)
    assert events[0].model_dump(mode="json")["announcement_date"] == "2026-10-21"


def test_normalize_applies_nfkc_to_full_width_digits() -> None:
    assert normalize_notification_text("\uff11\uff12\uff13\uff14") == "1234"


def test_note_text_never_invents_a_symbol() -> None:
    """A dropped line's note must not smuggle in a synthesised symbol."""

    _, notes = extract_earnings_calendar(
        "\r\n".join([US_SECTION, "\u30c6\u30b9\u30c8\u4f01\u696d(NOTALETTERS):2026/10/21"])
    )
    assert notes
    assert ".T" not in " ".join(notes)


def test_mixed_body_separates_events_and_notes() -> None:
    events, notes = extract_earnings_calendar(
        "\r\n".join(
            [
                US_SECTION,
                "\u30c6\u30b9\u30c8(SMPL):2026/10/21",
                "\u30c6\u30b9\u30c8\u4f01\u696d(NOTALETTERS):2026/10/21",
                "\u30c6\u30b9\u30c8\u884c\u306e\u307f",
                "\u30c6\u30b9\u30c8(\uff11\uff12\uff13\uff14):2026/10/22",
            ]
        )
    )
    assert [(event.symbol, event.market) for event in events] == [
        ("SMPL", "us"),
        ("1234.T", "jp"),
    ]
    assert len(notes) == 2
