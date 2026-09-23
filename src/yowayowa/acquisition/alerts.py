"""Earnings-announcement extraction from authorized notification mail (P1D).

Broker notification mail carries a compact earnings-calendar stream that no
public source provides for the operator's own holdings/watch list: a section
header with the mail's reference date, then ``name(code)`` lines, optionally
with an explicit announcement date.

The extractor is deliberately generic (any provider whose alert mail uses the
same shape) and fail-closed:

- a line without a usable date is never assigned a guessed date — it is
  dropped and reported in ``notes``;
- a line whose instrument code matches neither the Japanese nor the U.S.
  pattern is dropped and reported in ``notes``;
- date and code are only read from the line/section they appear in; nothing is
  inferred across sections.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Literal

from pydantic import BaseModel

# Section header marker used by Japanese broker alert mail ("■決算カレンダー…").
SECTION_MARKER = "\u25a0"

_MAX_NOTES = 200

# Section header date: the reference/announcement date of the section.
_SECTION_DATE_RE = re.compile(r"(\d{4})/(\d{1,2})/(\d{1,2})")

# ``name(code)`` / ``name(code):YYYY/MM/DD``. The name group is greedy so the
# LAST parenthesised token wins when a name itself contains parentheses.
_PAIR_RE = re.compile(
    r"^(?P<name>.*)[\uff08(](?P<code>[^\uff08\uff09()]+)[\uff09)]"
    r"\s*(?:[\uff1a:]\s*(?P<date>\d{4}/\d{1,2}/\d{1,2}))?$"
)

# Same parenthesised group, applied to the ORIGINAL (un-normalized) line so
# raw_symbol/raw name keep the character form the provider actually sent
# (full-width digits, full-width parentheses).
_RAW_PAREN_RE = re.compile(r"[\uff08(]([^\uff08\uff09()]*)[\uff09)]")


def _last_paren_group(line: str) -> re.Match[str] | None:
    """The LAST parenthesised group of a line (the instrument code).

    Names may themselves contain parentheses ("テスト(旧)ホールディングス(4444)"),
    so the code is the final group, matching how the normalized pattern is
    anchored at the end of the line.
    """

    matches = list(_RAW_PAREN_RE.finditer(line))
    return matches[-1] if matches else None


_JP_NUMERIC_CODE_RE = re.compile(r"^[0-9]{4}$")
_JP_ALPHANUMERIC_CODE_RE = re.compile(r"^[0-9]{3}[A-Z]$")
_US_CODE_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")

Market = Literal["jp", "us"]


class EarningsAnnouncement(BaseModel):
    """One scheduled earnings announcement for one instrument."""

    symbol: str
    market: Market
    name: str | None = None
    announcement_date: date
    raw_symbol: str
    raw_line: str
    section: str | None = None


def normalize_notification_text(text: str) -> str:
    """Normalize full-width alphanumerics/punctuation to half-width (NFKC)."""

    return unicodedata.normalize("NFKC", text)


def _section_date(line: str) -> date | None:
    match = _SECTION_DATE_RE.search(line)
    if match is None:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def _parse_line_date(raw: str) -> date | None:
    year, month, day = raw.split("/")
    try:
        return date(int(year), int(month), int(day))
    except ValueError:
        return None


def _classify_code(code: str) -> tuple[str, Market] | None:
    """(symbol, market) for a code, or None when the code is not recognized."""

    if _JP_NUMERIC_CODE_RE.match(code) or _JP_ALPHANUMERIC_CODE_RE.match(code):
        return f"{code}.T", "jp"
    if _US_CODE_RE.match(code):
        return code, "us"
    return None


class _NoteCollector:
    """Bounded note buffer: fail-closed visibility without unbounded growth."""

    def __init__(self) -> None:
        self._notes: list[str] = []
        self._suppressed = 0

    def add(self, message: str) -> None:
        if len(self._notes) >= _MAX_NOTES:
            self._suppressed += 1
            return
        self._notes.append(message)

    def result(self) -> list[str]:
        if self._suppressed:
            return [*self._notes, f"plus {self._suppressed} further dropped lines"]
        return list(self._notes)


def extract_earnings_calendar(text: str) -> tuple[list[EarningsAnnouncement], list[str]]:
    """Extract earnings announcements from one notification mail body.

    Returns ``(events, notes)``. ``events`` is deduplicated on
    ``(symbol, announcement_date)`` keeping the first occurrence; ``notes``
    lists every line dropped for a missing/unusable date or an unrecognized
    instrument code (a dropped line is never converted into a guessed event).
    """

    events: list[EarningsAnnouncement] = []
    notes = _NoteCollector()
    seen: set[tuple[str, date]] = set()
    section: str | None = None
    section_date: date | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        normalized = normalize_notification_text(line)
        if normalized.startswith(SECTION_MARKER):
            section = line
            section_date = _section_date(normalized)
            continue

        match = _PAIR_RE.match(normalized)
        if match is None:
            notes.add(f"unrecognized alert line (skipped): {line}")
            continue

        normalized_code = match.group("code").strip()
        classified = _classify_code(normalized_code)
        if classified is None:
            notes.add(f"unrecognized instrument code (skipped): {line}")
            continue
        symbol, market = classified

        raw_date = match.group("date")
        if raw_date is not None:
            announcement_date = _parse_line_date(raw_date)
            if announcement_date is None:
                notes.add(f"unusable announcement date (skipped): {line}")
                continue
        elif section_date is not None:
            announcement_date = section_date
        else:
            notes.add(f"no announcement date and no section date (skipped): {line}")
            continue

        key = (symbol, announcement_date)
        if key in seen:
            continue
        seen.add(key)

        raw_code_match = _last_paren_group(line)
        raw_symbol = raw_code_match.group(1).strip() if raw_code_match else normalized_code
        raw_name = match.group("name").strip()
        if raw_code_match is not None:
            raw_name = line[: raw_code_match.start()].strip() or raw_name
        events.append(
            EarningsAnnouncement(
                symbol=symbol,
                market=market,
                name=raw_name or None,
                announcement_date=announcement_date,
                raw_symbol=raw_symbol,
                raw_line=line,
                section=section,
            )
        )

    return events, notes.result()


__all__ = [
    "SECTION_MARKER",
    "EarningsAnnouncement",
    "extract_earnings_calendar",
    "normalize_notification_text",
]
