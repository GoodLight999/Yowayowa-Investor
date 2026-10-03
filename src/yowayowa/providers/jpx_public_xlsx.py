"""Parsers for JPX free watch-list and premium-charge XLSX publications."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime

from yowayowa.jpx_public_models import JpxMarginWatch, JpxPremiumCharge
from yowayowa.providers.jpx_public_common import (
    JPX_CODE_RE,
    MARGIN_LABELS,
    SLASH_DATE_RE,
    JpxPublicMarginParseError,
    load_xlsx,
    parse_date,
    provenance,
    publication_at,
    required_xlsx_float,
    required_xlsx_int,
    sha256_bytes,
    xlsx_float,
    xlsx_int,
)


def parse_jpx_margin_watch_xlsx(
    data: bytes,
    *,
    source_url: str,
    retrieved_at: datetime | None = None,
) -> list[JpxMarginWatch]:
    """Parse 日々公表銘柄等信用取引残高 XLSX."""

    workbook = load_xlsx(data)
    sheet = workbook[workbook.sheetnames[0]]
    retrieved = retrieved_at or datetime.now(UTC)
    source_sha256 = sha256_bytes(data)

    application_date: date | None = None
    publication_date: date | None = None
    for row in sheet.iter_rows(
        min_row=1, max_row=min(sheet.max_row, 40), values_only=True
    ):
        values = list(row)
        dates: list[date] = []
        for raw in values:
            if isinstance(raw, str):
                match = SLASH_DATE_RE.fullmatch(raw.strip())
                if match:
                    dates.append(parse_date(*match.groups()))
        joined = " ".join(str(item or "") for item in values)
        if dates and ("申込み現在" in joined or "application based" in joined):
            application_date = dates[0]
            publication_date = dates[1] if len(dates) > 1 else None
            break
    if application_date is None or publication_date is None:
        raise JpxPublicMarginParseError(
            "application/publication date not found in watch XLSX"
        )

    published_at = publication_at(publication_date, 16, 0)
    row_provenance = provenance(
        source="JPX 日々公表銘柄等信用取引残高 XLSX",
        source_url=source_url,
        retrieved_at=retrieved,
        as_of=application_date,
        source_sha256=source_sha256,
        published_at=published_at,
    )
    rows: list[JpxMarginWatch] = []
    seen: set[str] = set()

    for row_number, row in enumerate(sheet.iter_rows(values_only=True), start=1):
        values = list(row)
        if len(values) < 23:
            values += [None] * (23 - len(values))
        code = str(values[6] or "").strip().upper()
        isin = str(values[7] or "").strip().upper()
        if not JPX_CODE_RE.fullmatch(code) or not re.fullmatch(
            r"[A-Z]{2}[A-Z0-9]{10}", isin
        ):
            continue
        if code in seen:
            raise JpxPublicMarginParseError(
                f"duplicate watch XLSX code {code} at row {row_number}"
            )
        seen.add(code)

        margin_marker = str(values[5]).strip() if values[5] else None
        if margin_marker not in MARGIN_LABELS:
            raise JpxPublicMarginParseError(
                f"unexpected watch margin marker {margin_marker!r} at row {row_number}"
            )

        short_total = required_xlsx_int(
            values[8], field=f"watch[{row_number}].short_total"
        )
        long_total = required_xlsx_int(
            values[11], field=f"watch[{row_number}].long_total"
        )
        short_negotiable = required_xlsx_int(
            values[15], field=f"watch[{row_number}].short_negotiable"
        )
        short_standardized = required_xlsx_int(
            values[17], field=f"watch[{row_number}].short_standardized"
        )
        long_negotiable = required_xlsx_int(
            values[19], field=f"watch[{row_number}].long_negotiable"
        )
        long_standardized = required_xlsx_int(
            values[21], field=f"watch[{row_number}].long_standardized"
        )
        if short_total != short_negotiable + short_standardized:
            raise JpxPublicMarginParseError(
                f"watch short identity violation for {code}"
            )
        if long_total != long_negotiable + long_standardized:
            raise JpxPublicMarginParseError(
                f"watch long identity violation for {code}"
            )

        rows.append(
            JpxMarginWatch(
                application_date=application_date,
                code=code,
                unit_marker=str(values[0]).strip() if values[0] else None,
                primary_status=str(values[1]).strip() if values[1] else None,
                jsf_status=str(values[2]).strip() if values[2] else None,
                company_name=str(values[3]).strip() if values[3] else None,
                section=str(values[4]).strip() if values[4] else None,
                margin_marker=margin_marker,
                isin=isin,
                short_total=short_total,
                short_change=xlsx_int(
                    values[9],
                    allow_missing=True,
                    field=f"watch[{row_number}].short_change",
                ),
                short_listed_ratio_pct=xlsx_float(
                    values[10],
                    allow_missing=True,
                    field=f"watch[{row_number}].short_ratio",
                ),
                long_total=long_total,
                long_change=xlsx_int(
                    values[12],
                    allow_missing=True,
                    field=f"watch[{row_number}].long_change",
                ),
                long_listed_ratio_pct=xlsx_float(
                    values[13],
                    allow_missing=True,
                    field=f"watch[{row_number}].long_ratio",
                ),
                sale_purchase_ratio_pct=xlsx_float(
                    values[14],
                    allow_missing=True,
                    field=f"watch[{row_number}].sale_purchase_ratio",
                ),
                short_negotiable=short_negotiable,
                short_negotiable_change=xlsx_int(
                    values[16],
                    allow_missing=True,
                    field=f"watch[{row_number}].short_negotiable_change",
                ),
                short_standardized=short_standardized,
                short_standardized_change=xlsx_int(
                    values[18],
                    allow_missing=True,
                    field=f"watch[{row_number}].short_standardized_change",
                ),
                long_negotiable=long_negotiable,
                long_negotiable_change=xlsx_int(
                    values[20],
                    allow_missing=True,
                    field=f"watch[{row_number}].long_negotiable_change",
                ),
                long_standardized=long_standardized,
                long_standardized_change=xlsx_int(
                    values[22],
                    allow_missing=True,
                    field=f"watch[{row_number}].long_standardized_change",
                ),
                published_at=published_at,
                retrieved_at=retrieved,
                source_sha256=source_sha256,
                provenance=row_provenance,
            )
        )

    if not rows:
        raise JpxPublicMarginParseError("watch XLSX contains no issue rows")
    return rows


def parse_jpx_premium_xlsx(
    data: bytes,
    *,
    source_url: str,
    retrieved_at: datetime | None = None,
) -> list[JpxPremiumCharge]:
    """Parse JPX 品貸料率一覧 XLSX without converting missing marks to zero."""

    workbook = load_xlsx(data)
    sheet = workbook[workbook.sheetnames[0]]
    retrieved = retrieved_at or datetime.now(UTC)
    source_sha256 = sha256_bytes(data)
    rows: list[JpxPremiumCharge] = []
    seen: set[tuple[date, str, str]] = set()

    for row_number, row in enumerate(
        sheet.iter_rows(min_row=4, values_only=True), start=4
    ):
        values = list(row)
        if len(values) < 7:
            continue

        raw_date, raw_code = values[0], values[1]
        date_text = str(raw_date).strip()
        if date_text.endswith(".0"):
            date_text = date_text[:-2]
        if not re.fullmatch(r"20\d{6}", date_text):
            continue
        trade_date = parse_date(date_text[:4], date_text[4:6], date_text[6:8])

        source_code = str(raw_code).strip().upper()
        if source_code.endswith(".0"):
            source_code = source_code[:-2]
        if not re.fullmatch(r"[0-9]{3}[0-9A-Z]", source_code):
            raise JpxPublicMarginParseError(
                f"invalid premium code at row {row_number}: {raw_code!r}"
            )
        exchange = str(values[3]).strip() if values[3] else ""
        key = (trade_date, source_code, exchange)
        if key in seen:
            raise JpxPublicMarginParseError(f"duplicate premium key {key}")
        seen.add(key)

        premium_raw = values[6]
        premium_charge = (
            None
            if str(premium_raw).strip() == "*****"
            else xlsx_float(
                premium_raw,
                allow_missing=True,
                field=f"premium[{row_number}].premium_charge",
            )
        )
        row_provenance = provenance(
            source="JPX 品貸料率一覧 XLSX",
            source_url=source_url,
            retrieved_at=retrieved,
            as_of=trade_date,
            source_sha256=source_sha256,
            published_at=retrieved,
            notes=[
                "Workbook has no delivery timestamp; published_at is first-observed retrieved_at."
            ],
        )
        rows.append(
            JpxPremiumCharge(
                trade_date=trade_date,
                source_code=source_code,
                company_name=str(values[2]).strip() if values[2] else None,
                exchange=exchange or None,
                over_lent_shares=required_xlsx_int(
                    values[4], field=f"premium[{row_number}].over_lent_shares"
                ),
                maximum_premium_charge=required_xlsx_float(
                    values[5],
                    field=f"premium[{row_number}].maximum_premium_charge",
                ),
                premium_charge=premium_charge,
                published_at=retrieved,
                retrieved_at=retrieved,
                source_sha256=source_sha256,
                provenance=row_provenance,
            )
        )

    if not rows:
        raise JpxPublicMarginParseError("premium XLSX contains no rows")
    return rows


def safe_jpx_code_from_premium(
    source_code: str, known_codes: set[str]
) -> str | None:
    """Resolve 4-char premium codes only to exact ordinary-code xxxx0 rows."""

    candidate = source_code.upper() + "0"
    return candidate if candidate in known_codes else None
