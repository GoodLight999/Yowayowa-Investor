"""Parser for JPX 01.html free all-issue daily margin-balance PDF."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from typing import Final

from yowayowa.jpx_models import JpxMarginBalance
from yowayowa.jpx_public_models import JpxMarginDailyDetail
from yowayowa.providers.jpx_public_common import (
    JPX_CODE_RE,
    MARGIN_CODE,
    MARGIN_LABELS,
    SECTIONS,
    JpxPublicMarginParseError,
    extract_jpx_pdf_lines,
    parse_date,
    parse_int_token,
    parse_ratio_token,
    provenance,
    publication_at,
    required_int,
    sha256_bytes,
    utc_now,
)

APP_DATE_RE: Final = re.compile(r"(20\d{2})/(\d{1,2})/(\d{1,2})\s*申込み現在")
SLASH_DATE_RE: Final = re.compile(r"^(20\d{2})/(\d{1,2})/(\d{1,2})$")
ISIN_SHARE_RE: Final = re.compile(
    r"^(?P<isin>[A-Z]{2}[A-Z0-9]{10})\s+株数\s+Shs\.\s*$"
)
ISIN_VALUE_RE: Final = re.compile(
    r"^(?P<isin>[A-Z]{2}[A-Z0-9]{10})\s+金額\s+Val\.\s*$"
)
COUNT_LINE_RE: Final = re.compile(r"^(\d+)\s*銘柄$")
LOT_MARKERS: Final = frozenset({"A", "J", "K", "B", "M", "C", "T", "F"})


@dataclass(frozen=True, slots=True)
class PublicBalanceBatch:
    balances: list[JpxMarginBalance]
    details: list[JpxMarginDailyDetail]
    application_date: date
    publication_date: date
    published_at: datetime
    source_sha256: str
    declared_issue_count: int
    section_counts: dict[str, int]
    margin_counts: dict[str, int]


def _extract_issue_meta(
    lines: list[str], code_index: int
) -> tuple[str, str, str, str | None]:
    margin_marker = lines[code_index - 1].strip() if code_index >= 1 else ""
    if margin_marker not in MARGIN_LABELS:
        raise JpxPublicMarginParseError(
            f"missing/unknown margin marker before {lines[code_index]!r}: "
            f"{margin_marker!r}"
        )
    lot_index: int | None = None
    for index in range(code_index - 2, max(-1, code_index - 12), -1):
        if lines[index].strip() in LOT_MARKERS:
            lot_index = index
            break
    if lot_index is None:
        raise JpxPublicMarginParseError(
            f"unit marker not found before {lines[code_index]!r}"
        )
    issue_parts = [
        item.strip() for item in lines[lot_index + 1 : code_index - 1] if item.strip()
    ]
    if not issue_parts:
        raise JpxPublicMarginParseError(
            f"issue name missing before {lines[code_index]!r}"
        )
    section: str | None = None
    if issue_parts[-1] in SECTIONS:
        section = issue_parts.pop()
    else:
        last = issue_parts[-1]
        for candidate in SECTIONS:
            if last.endswith(candidate):
                section = candidate
                issue_parts[-1] = last[: -len(candidate)].rstrip()
                if not issue_parts[-1]:
                    issue_parts.pop()
                break
    if section is None:
        raise JpxPublicMarginParseError(
            f"market section not found before {lines[code_index]!r}: {issue_parts!r}"
        )
    issue = " ".join(issue_parts).strip()
    if not issue:
        raise JpxPublicMarginParseError(
            f"empty issue name before {lines[code_index]!r}"
        )
    return issue, section, margin_marker, lines[lot_index].strip()


def _source_values(
    lines: list[str], start: int, count: int, *, context: str
) -> list[str]:
    if start + count > len(lines):
        raise JpxPublicMarginParseError(f"truncated {context}")
    return [item.strip() for item in lines[start : start + count]]


def _declared_counts(lines: list[str]) -> tuple[int, dict[str, int], dict[str, int]]:
    total: int | None = None
    sections: dict[str, int] = {}
    margins: dict[str, int] = {}
    margin_labels = {"貸借銘柄": "貸", "制度信用銘柄": "制", "その他": "他"}
    for index, raw in enumerate(lines[:-1]):
        label = raw.strip()
        if label == "総合計":
            match = COUNT_LINE_RE.fullmatch(lines[index + 1].strip())
            if match:
                total = int(match.group(1))
        for section in SECTIONS:
            if label == f"{section} 小計":
                match = COUNT_LINE_RE.fullmatch(lines[index + 1].strip())
                if match:
                    sections[section] = max(
                        sections.get(section, 0), int(match.group(1))
                    )
        if label in margin_labels:
            match = COUNT_LINE_RE.fullmatch(lines[index + 1].strip())
            if match:
                margins[margin_labels[label]] = int(match.group(1))
    if total is None:
        raise JpxPublicMarginParseError("document total issue count not found")
    if set(sections) != set(SECTIONS):
        raise JpxPublicMarginParseError(
            f"document section counts incomplete: {sorted(sections)}"
        )
    if set(margins) != MARGIN_LABELS:
        raise JpxPublicMarginParseError(
            f"document margin-type counts incomplete: {sorted(margins)}"
        )
    return total, sections, margins


def parse_jpx_public_balance_pdf(
    data: bytes,
    *,
    source_url: str,
    retrieved_at: datetime | None = None,
) -> PublicBalanceBatch:
    """Parse the complete free all-issue PDF and fail closed on any count drift."""

    lines = extract_jpx_pdf_lines(data)
    header = "\n".join(lines[:80])
    match = APP_DATE_RE.search(header)
    if not match:
        raise JpxPublicMarginParseError(
            "application date not found in all-issue PDF"
        )
    application_date = parse_date(*match.groups())
    publication_date: date | None = None
    for line in lines[:80]:
        date_match = SLASH_DATE_RE.fullmatch(line.strip())
        if date_match:
            candidate = parse_date(*date_match.groups())
            if candidate != application_date:
                publication_date = candidate
                break
    if publication_date is None:
        raise JpxPublicMarginParseError(
            "publication date not found in all-issue PDF"
        )

    declared_count, declared_sections, declared_margins = _declared_counts(lines)
    retrieved = retrieved_at or utc_now()
    source_sha256 = sha256_bytes(data)
    published_at = publication_at(publication_date, 16, 0)

    balances: list[JpxMarginBalance] = []
    details: list[JpxMarginDailyDetail] = []
    seen: set[str] = set()
    index = 0

    while index + 1 < len(lines):
        code = lines[index].strip().upper()
        if not JPX_CODE_RE.fullmatch(code):
            index += 1
            continue
        share_match = ISIN_SHARE_RE.fullmatch(lines[index + 1].strip())
        if not share_match:
            index += 1
            continue
        if code in seen:
            raise JpxPublicMarginParseError(
                f"duplicate issue code in all-issue PDF: {code}"
            )

        issue, section, margin_marker, unit_marker = _extract_issue_meta(lines, index)
        isin = share_match.group("isin")
        raw = _source_values(lines, index + 2, 14, context=f"{code} share row")
        short_total = required_int(raw[0], field=f"{code}.short_total")
        short_change = parse_int_token(
            raw[1], allow_missing=True, field=f"{code}.short_change"
        )
        short_ratio = parse_ratio_token(raw[2], field=f"{code}.short_ratio")
        long_total = required_int(raw[3], field=f"{code}.long_total")
        long_change = parse_int_token(
            raw[4], allow_missing=True, field=f"{code}.long_change"
        )
        long_ratio = parse_ratio_token(raw[5], field=f"{code}.long_ratio")
        short_negotiable = required_int(
            raw[6], field=f"{code}.short_negotiable"
        )
        short_negotiable_change = parse_int_token(
            raw[7], allow_missing=True, field=f"{code}.short_negotiable_change"
        )
        short_standardized = required_int(
            raw[8], field=f"{code}.short_standardized"
        )
        short_standardized_change = parse_int_token(
            raw[9], allow_missing=True, field=f"{code}.short_standardized_change"
        )
        long_negotiable = required_int(
            raw[10], field=f"{code}.long_negotiable"
        )
        long_negotiable_change = parse_int_token(
            raw[11], allow_missing=True, field=f"{code}.long_negotiable_change"
        )
        long_standardized = required_int(
            raw[12], field=f"{code}.long_standardized"
        )
        long_standardized_change = parse_int_token(
            raw[13], allow_missing=True, field=f"{code}.long_standardized_change"
        )
        if short_total != short_negotiable + short_standardized:
            raise JpxPublicMarginParseError(
                f"share short identity violation for {code}"
            )
        if long_total != long_negotiable + long_standardized:
            raise JpxPublicMarginParseError(
                f"share long identity violation for {code}"
            )

        value_anchor: int | None = None
        for forward in range(index + 16, min(len(lines) - 1, index + 140)):
            candidate = lines[forward].strip().upper()
            if candidate == code:
                value_match = ISIN_VALUE_RE.fullmatch(lines[forward + 1].strip())
                if value_match and value_match.group("isin") == isin:
                    value_anchor = forward
                    break
            if candidate != code and JPX_CODE_RE.fullmatch(candidate):
                if ISIN_SHARE_RE.fullmatch(lines[forward + 1].strip()):
                    break
        if value_anchor is None:
            raise JpxPublicMarginParseError(f"value row not found for {code}")

        value_raw = _source_values(
            lines, value_anchor + 2, 14, context=f"{code} value row"
        )
        short_total_value = required_int(
            value_raw[0], field=f"{code}.short_total_value"
        )
        short_value_change = parse_int_token(
            value_raw[1], allow_missing=True, field=f"{code}.short_value_change"
        )
        long_total_value = required_int(
            value_raw[3], field=f"{code}.long_total_value"
        )
        long_value_change = parse_int_token(
            value_raw[4], allow_missing=True, field=f"{code}.long_value_change"
        )
        short_negotiable_value = required_int(
            value_raw[6], field=f"{code}.short_negotiable_value"
        )
        short_negotiable_value_change = parse_int_token(
            value_raw[7],
            allow_missing=True,
            field=f"{code}.short_negotiable_value_change",
        )
        short_standardized_value = required_int(
            value_raw[8], field=f"{code}.short_standardized_value"
        )
        short_standardized_value_change = parse_int_token(
            value_raw[9],
            allow_missing=True,
            field=f"{code}.short_standardized_value_change",
        )
        long_negotiable_value = required_int(
            value_raw[10], field=f"{code}.long_negotiable_value"
        )
        long_negotiable_value_change = parse_int_token(
            value_raw[11],
            allow_missing=True,
            field=f"{code}.long_negotiable_value_change",
        )
        long_standardized_value = required_int(
            value_raw[12], field=f"{code}.long_standardized_value"
        )
        long_standardized_value_change = parse_int_token(
            value_raw[13],
            allow_missing=True,
            field=f"{code}.long_standardized_value_change",
        )
        if short_total_value != short_negotiable_value + short_standardized_value:
            raise JpxPublicMarginParseError(
                f"value short identity violation for {code}"
            )
        if long_total_value != long_negotiable_value + long_standardized_value:
            raise JpxPublicMarginParseError(
                f"value long identity violation for {code}"
            )

        row_provenance = provenance(
            source="JPX 銘柄別信用取引残高（日次） public PDF",
            source_url=source_url,
            retrieved_at=retrieved,
            as_of=application_date,
            source_sha256=source_sha256,
            published_at=published_at,
        )
        balances.append(
            JpxMarginBalance(
                application_date=application_date,
                code=code,
                company_name=issue,
                isin=isin,
                market_code=None,
                margin_code=MARGIN_CODE[margin_marker],
                short_total=short_total,
                long_total=long_total,
                short_negotiable=short_negotiable,
                short_standardized=short_standardized,
                long_negotiable=long_negotiable,
                long_standardized=long_standardized,
                short_total_value=short_total_value,
                long_total_value=long_total_value,
                short_negotiable_value=short_negotiable_value,
                short_standardized_value=short_standardized_value,
                long_negotiable_value=long_negotiable_value,
                long_standardized_value=long_standardized_value,
                provenance=row_provenance,
            )
        )
        details.append(
            JpxMarginDailyDetail(
                application_date=application_date,
                code=code,
                issue=issue,
                section=section,
                margin_marker=margin_marker,
                unit_marker=unit_marker,
                isin=isin,
                short_source_change=short_change,
                long_source_change=long_change,
                short_listed_ratio_pct=short_ratio,
                long_listed_ratio_pct=long_ratio,
                short_negotiable_source_change=short_negotiable_change,
                short_standardized_source_change=short_standardized_change,
                long_negotiable_source_change=long_negotiable_change,
                long_standardized_source_change=long_standardized_change,
                short_value_source_change=short_value_change,
                long_value_source_change=long_value_change,
                short_negotiable_value_source_change=short_negotiable_value_change,
                short_standardized_value_source_change=short_standardized_value_change,
                long_negotiable_value_source_change=long_negotiable_value_change,
                long_standardized_value_source_change=long_standardized_value_change,
                published_at=published_at,
                retrieved_at=retrieved,
                source_sha256=source_sha256,
                provenance=row_provenance,
            )
        )
        seen.add(code)
        index = value_anchor + 16

    if len(balances) != declared_count or len(seen) != declared_count:
        raise JpxPublicMarginParseError(
            "all-issue PDF count mismatch: "
            f"declared={declared_count} parsed={len(balances)} unique={len(seen)}"
        )
    actual_sections = Counter(detail.section for detail in details)
    actual_margins = Counter(detail.margin_marker for detail in details)
    for section, expected in declared_sections.items():
        if actual_sections[section] != expected:
            raise JpxPublicMarginParseError(
                f"section count mismatch {section}: "
                f"declared={expected} parsed={actual_sections[section]}"
            )
    for marker, expected in declared_margins.items():
        if actual_margins[marker] != expected:
            raise JpxPublicMarginParseError(
                f"margin count mismatch {marker}: "
                f"declared={expected} parsed={actual_margins[marker]}"
            )

    return PublicBalanceBatch(
        balances=balances,
        details=details,
        application_date=application_date,
        publication_date=publication_date,
        published_at=published_at,
        source_sha256=source_sha256,
        declared_issue_count=declared_count,
        section_counts=dict(actual_sections),
        margin_counts=dict(actual_margins),
    )
