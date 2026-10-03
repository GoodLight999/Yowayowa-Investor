"""Parser for JPX 03.html margin-trading-ratio PDF."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Final

from yowayowa.jpx_public_models import JpxMarginFlow
from yowayowa.providers.jpx_public_common import (
    JPX_CODE_RE,
    MARGIN_LABELS,
    SECTIONS,
    STATUS_MARKERS,
    JpxPublicMarginParseError,
    extract_jpx_pdf_lines,
    parse_date,
    parse_ratio_token,
    provenance,
    publication_at,
    sha256_bytes,
)

JP_TRADE_DATE_RE: Final = re.compile(
    r"(20\d{2})年(\d{1,2})月(\d{1,2})日売買分"
)


def parse_jpx_margin_flow_pdf(
    data: bytes,
    *,
    source_url: str,
    retrieved_at: datetime | None = None,
) -> list[JpxMarginFlow]:
    """Parse 信用取引売買比率 into one observation per issue and trade date."""

    lines = extract_jpx_pdf_lines(data)
    retrieved = retrieved_at or datetime.now(UTC)
    source_sha256 = sha256_bytes(data)

    dates = []
    for line in lines:
        match = JP_TRADE_DATE_RE.fullmatch(line.strip())
        if match:
            trade_date = parse_date(*match.groups())
            if trade_date not in dates:
                dates.append(trade_date)
    if not dates:
        raise JpxPublicMarginParseError("flow dates not found")
    if len(dates) > 3:
        raise JpxPublicMarginParseError(
            f"unexpected flow date count: {len(dates)}"
        )

    publication_date = dates[0]
    published_at = publication_at(publication_date, 16, 30)
    rows: list[JpxMarginFlow] = []
    issue_codes: set[str] = set()
    seen: set[tuple[object, str]] = set()

    for index in range(0, len(lines) - 6):
        code = lines[index].strip().upper()
        if not JPX_CODE_RE.fullmatch(code):
            continue

        ratio_tokens = [lines[index + offset].strip() for offset in range(1, 7)]
        try:
            parsed_ratios = [
                parse_ratio_token(token, field=f"flow.{code}.{offset}")
                for offset, token in enumerate(ratio_tokens)
            ]
        except JpxPublicMarginParseError:
            continue

        margin_marker = lines[index - 1].strip() if index >= 1 else ""
        section = lines[index - 2].strip() if index >= 2 else ""
        if margin_marker not in MARGIN_LABELS or section not in SECTIONS:
            continue

        company_name = lines[index - 3].strip()
        status_marker: str | None = None
        for marker in STATUS_MARKERS:
            prefix = marker + " "
            if company_name.startswith(prefix):
                status_marker = marker
                company_name = company_name[len(prefix) :].strip()
                break
        if (
            status_marker is None
            and index >= 4
            and lines[index - 4].strip() in STATUS_MARKERS
        ):
            status_marker = lines[index - 4].strip()
        if not company_name or company_name in STATUS_MARKERS:
            continue
        issue_codes.add(code)

        for date_index, trade_date in enumerate(dates):
            key = (trade_date, code)
            if key in seen:
                raise JpxPublicMarginParseError(f"duplicate flow key {key}")
            seen.add(key)
            sales_raw = ratio_tokens[2 * date_index]
            purchase_raw = ratio_tokens[2 * date_index + 1]
            row_provenance = provenance(
                source="JPX 信用取引売買比率 PDF",
                source_url=source_url,
                retrieved_at=retrieved,
                as_of=trade_date,
                source_sha256=source_sha256,
                published_at=published_at,
                notes=[
                    "JPX states published margin-trading ratios are not retroactively corrected."
                ],
            )
            rows.append(
                JpxMarginFlow(
                    trade_date=trade_date,
                    code=code,
                    company_name=company_name,
                    section=section,
                    margin_marker=margin_marker,
                    status_marker=status_marker,
                    new_sales_ratio_pct=parsed_ratios[2 * date_index],
                    new_purchase_ratio_pct=parsed_ratios[2 * date_index + 1],
                    sales_star="★" in sales_raw,
                    purchase_star="★" in purchase_raw,
                    published_at=published_at,
                    retrieved_at=retrieved,
                    source_sha256=source_sha256,
                    provenance=row_provenance,
                )
            )

    if not rows:
        raise JpxPublicMarginParseError("flow PDF contains no issue rows")
    if len(rows) != len(issue_codes) * len(dates):
        raise JpxPublicMarginParseError(
            "flow matrix incomplete: "
            f"issues={len(issue_codes)} dates={len(dates)} observations={len(rows)}"
        )
    return rows
