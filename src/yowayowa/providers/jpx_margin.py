"""JPX daily issue-level margin balance provider: official CSV parsing (P4-A).

Parses the 銘柄別信用取引残高（日次） CSV distributed by JPX総研's paid
reference service (Web download and DFS/FTP feed use identical files; the
J-Quants Pro API carries the same business data under 1:1 field names). The
observed format is recorded in ``docs/JPX_DAILY_MARGIN.md`` (verified against
the official sample ZIP on 2026-09-24; live publication starts 2026-09-28).

This module intentionally contains **no network access**: fetching the daily
file is a separate post-launch task. Ingestion is fail-closed:

- encoding is detected BOM-first, then UTF-8, then CP932 (the Japanese
  distribution encoding); anything else fails instead of mojibake-ing;
- EN and JP header rows are both accepted and normalized through the explicit
  header map below — unknown or short headers fail the parse (format drift is
  never silently coerced);
- an unparseable numeric cell fails the parse; empty amount cells become
  ``None``, never zero (amounts exist only from 2026-09-25 onward);
- the total = negotiable + standardized identity is enforced by
  :class:`yowayowa.jpx_models.JpxMarginBalance` per row.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, date, datetime
from typing import Final

from yowayowa.domain import LicenseClass, Provenance
from yowayowa.jpx_models import JpxMarginBalance, normalize_jpx_code
from yowayowa.providers.base import ProviderDescriptor, enforce_provider_policy

__all__ = [
    "JPX_MARGIN_CODE_MEANINGS",
    "JpxMarginCsvError",
    "jpx_margin_descriptor",
    "parse_jpx_margin_csv",
]

# English header -> model field. Column order matches the observed 18-column
# layout; the parser maps by header name, not by position, so a re-ordered
# file still parses while a renamed header fails closed.
_JPX_MARGIN_HEADER_MAP: Final[dict[str, str]] = {
    "record date": "application_date",
    "申込日": "application_date",
    "local code": "code",
    "銘柄コード": "code",
    "company name (english)": "company_name",
    "銘柄名": "company_name",
    "isin": "isin",
    "market segment code": "market_code",
    "市場コード": "market_code",
    "margin code": "margin_code",
    "銘柄種別コード": "margin_code",
    "short margin outstanding (volume)": "short_total",
    "売合計信用残高（株数）": "short_total",
    "long margin outstanding (volume)": "long_total",
    "買合計信用残高（株数）": "long_total",
    "short negotiable margin outstanding (volume)": "short_negotiable",
    "売一般信用残高（株数）": "short_negotiable",
    "short standardized margin outstanding (volume)": "short_standardized",
    "売制度信用残高（株数）": "short_standardized",
    "long negotiable margin outstanding (volume)": "long_negotiable",
    "買一般信用残高（株数）": "long_negotiable",
    "long standardized margin outstanding (volume)": "long_standardized",
    "買制度信用残高（株数）": "long_standardized",
    "short margin outstanding (value)": "short_total_value",
    "売合計信用残高（金額）": "short_total_value",
    "long margin outstanding (value)": "long_total_value",
    "買合計信用残高（金額）": "long_total_value",
    "short negotiable margin outstanding (value)": "short_negotiable_value",
    "売一般信用残高（金額）": "short_negotiable_value",
    "short standardized margin outstanding (value)": "short_standardized_value",
    "売制度信用残高（金額）": "short_standardized_value",
    "long negotiable margin outstanding (value)": "long_negotiable_value",
    "買一般信用残高（金額）": "long_negotiable_value",
    "long standardized margin outstanding (value)": "long_standardized_value",
    "買制度信用残高（金額）": "long_standardized_value",
}

_EXPECTED_COLUMNS = 18

# Documented Margin Code values (docs/JPX_DAILY_MARGIN.md): 1 信用 (margin
# trading only), 2 貸借 (loan-consolidated), 3 その他 (other). Kept explicit so
# an unknown code fails closed instead of flowing into analysis unclassified.
JPX_MARGIN_CODE_MEANINGS: Final[dict[str, str]] = {
    "1": "margin",
    "2": "loan_consolidated",
    "3": "other",
}


class JpxMarginCsvError(ValueError):
    """Raised when a JPX margin CSV violates the observed format contract."""


def _decode_csv_bytes(data: bytes) -> str:
    """BOM-first encoding detection: UTF-8 (EN distribution) then CP932 (JP)."""

    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as utf8_exc:
        try:
            return data.decode("cp932")
        except UnicodeDecodeError as cp932_exc:
            raise JpxMarginCsvError(
                "JPX margin CSV is neither UTF-8 (English distribution) nor "
                f"CP932 (Japanese distribution): utf-8: {utf8_exc}; cp932: {cp932_exc}"
            ) from cp932_exc


def _header_field_map(header: list[str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for raw_name in header:
        name = raw_name.strip()
        field = _JPX_MARGIN_HEADER_MAP.get(name.casefold() if name.isascii() else name)
        if field is None:
            raise JpxMarginCsvError(
                f"Unrecognized JPX margin CSV header {raw_name!r}; refusing to guess column "
                "semantics (format drift). Expected the official English or Japanese headers."
            )
        if field in normalized.values():
            raise JpxMarginCsvError(f"Duplicate JPX margin CSV header for field {field!r}")
        normalized[name] = field
    if len(normalized) != _EXPECTED_COLUMNS:
        raise JpxMarginCsvError(
            f"JPX margin CSV header has {len(normalized)} columns, expected {_EXPECTED_COLUMNS}"
        )
    return normalized


def _parse_application_date(raw: str, source: str) -> date:
    text = raw.strip()
    if len(text) != 8 or not text.isdigit():
        raise JpxMarginCsvError(f"Invalid application date (申込日) {raw!r}: expected YYYYMMDD")
    try:
        return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
    except ValueError as exc:
        raise JpxMarginCsvError(f"Invalid application date (申込日) {raw!r}: {exc}") from exc


def _parse_int(raw: str, *, field: str, source: str) -> int | None:
    """Parse an integer cell; empty -> None (missing data is not zero)."""

    text = raw.strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError as exc:
        raise JpxMarginCsvError(
            f"Unparseable {field} value {raw!r} for {source}: refusing to coerce"
        ) from exc


def _parse_volume(raw: str, *, field: str, source: str) -> int:
    value = _parse_int(raw, field=field, source=source)
    if value is None:
        raise JpxMarginCsvError(
            f"Missing required {field} (株数) for {source}: volumes are never absent"
        )
    return value


def parse_jpx_margin_csv(
    data: bytes,
    *,
    source_url: str,
    retrieved_at: datetime | None = None,
) -> list[JpxMarginBalance]:
    """Parse a JPX daily margin balance CSV (EN or JP) into balances.

    ``source_url`` records where the bytes came from (website download URL,
    FTP transport note, or API request URL) and flows into every row's
    provenance together with ``retrieved_at`` (default: now, UTC).
    """

    text = _decode_csv_bytes(data)
    rows = list(csv.reader(io.StringIO(text, newline="")))
    if not rows or not any(cell.strip() for cell in rows[0]):
        raise JpxMarginCsvError("JPX margin CSV has no header row")
    field_map = _header_field_map(rows[0])

    parsed_at = retrieved_at or datetime.now(UTC)
    balances: list[JpxMarginBalance] = []
    seen: set[tuple[date, str]] = set()
    for line_number, row in enumerate(rows[1:], start=2):
        if not any(cell.strip() for cell in row):
            continue
        if len(row) != _EXPECTED_COLUMNS:
            raise JpxMarginCsvError(
                f"JPX margin CSV row {line_number} has {len(row)} columns, "
                f"expected {_EXPECTED_COLUMNS}"
            )
        values = {field: cell for cell, field in zip(row, field_map.values(), strict=True)}
        source = f"row {line_number}"
        application_date = _parse_application_date(values["application_date"], source)
        try:
            code = normalize_jpx_code(values["code"])
        except ValueError as exc:
            raise JpxMarginCsvError(f"{source}: {exc}") from exc
        key = (application_date, code)
        if key in seen:
            raise JpxMarginCsvError(f"Duplicate (application_date, code) in JPX margin CSV: {key}")
        seen.add(key)
        margin_code = values["margin_code"].strip() or None
        if margin_code is not None and margin_code not in JPX_MARGIN_CODE_MEANINGS:
            raise JpxMarginCsvError(
                f"{source}: unknown margin code {margin_code!r}; expected one of "
                f"{sorted(JPX_MARGIN_CODE_MEANINGS)}"
            )
        try:
            balance = JpxMarginBalance(
                application_date=application_date,
                code=code,
                company_name=values["company_name"].strip() or None,
                isin=values["isin"].strip() or None,
                market_code=values["market_code"].strip() or None,
                margin_code=margin_code,
                short_total=_parse_volume(
                    values["short_total"], field="short_total", source=source
                ),
                long_total=_parse_volume(values["long_total"], field="long_total", source=source),
                short_negotiable=_parse_volume(
                    values["short_negotiable"], field="short_negotiable", source=source
                ),
                short_standardized=_parse_volume(
                    values["short_standardized"],
                    field="short_standardized",
                    source=source,
                ),
                long_negotiable=_parse_volume(
                    values["long_negotiable"], field="long_negotiable", source=source
                ),
                long_standardized=_parse_volume(
                    values["long_standardized"], field="long_standardized", source=source
                ),
                short_total_value=_parse_int(
                    values["short_total_value"],
                    field="short_total_value",
                    source=source,
                ),
                long_total_value=_parse_int(
                    values["long_total_value"], field="long_total_value", source=source
                ),
                short_negotiable_value=_parse_int(
                    values["short_negotiable_value"],
                    field="short_negotiable_value",
                    source=source,
                ),
                short_standardized_value=_parse_int(
                    values["short_standardized_value"],
                    field="short_standardized_value",
                    source=source,
                ),
                long_negotiable_value=_parse_int(
                    values["long_negotiable_value"],
                    field="long_negotiable_value",
                    source=source,
                ),
                long_standardized_value=_parse_int(
                    values["long_standardized_value"],
                    field="long_standardized_value",
                    source=source,
                ),
                provenance=jpx_margin_provenance(
                    source_url=source_url,
                    retrieved_at=parsed_at,
                    as_of=application_date,
                ),
            )
            balances.append(balance)
        except ValueError as exc:
            raise JpxMarginCsvError(f"{source}: {exc}") from exc
    if not balances:
        raise JpxMarginCsvError("JPX margin CSV contains no data rows")
    return balances


def jpx_margin_descriptor() -> ProviderDescriptor:
    """Contracted JPX reference data is personal-only (docs/JPX_DAILY_MARGIN.md)."""

    return ProviderDescriptor(
        name="jpx_reference",
        license_class=LicenseClass.PERSONAL_ONLY,
        redistributable=False,
        description=(
            "JPX総研 銘柄別信用取引残高（日次） reference service; all TSE "
            "margin-tradable issues, paid contract, personal-only."
        ),
    )


def enforce_jpx_margin_policy(*, mode: str) -> None:
    """Fail closed outside personal mode (single policy enforcement point)."""

    enforce_provider_policy(jpx_margin_descriptor(), mode=mode)


def jpx_margin_provenance(
    *,
    source_url: str,
    retrieved_at: datetime,
    as_of: date,
) -> Provenance:
    return Provenance(
        provider="jpx_reference",
        source="JPX総研 銘柄別信用取引残高（日次） reference service",
        source_url=source_url,
        license_class=LicenseClass.PERSONAL_ONLY,
        retrieved_at=retrieved_at,
        as_of=as_of,
        notes=[
            "Personal-only JPX reference data; not redistributable (docs/JPX_DAILY_MARGIN.md).",
        ],
    )
