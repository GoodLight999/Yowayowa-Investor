"""Shared helpers for JPX free public margin publications."""

from __future__ import annotations

import hashlib
import io
import re
from datetime import UTC, date, datetime, time
from typing import Any, Final
from zoneinfo import ZoneInfo

from yowayowa.domain import LicenseClass, Provenance
from yowayowa.providers.base import ProviderDescriptor, enforce_provider_policy

JPX_CODE_RE: Final = re.compile(r"^[0-9]{3}[0-9A-Z][0-9A-Z]$")
SLASH_DATE_RE: Final = re.compile(r"^(20\d{2})/(\d{1,2})/(\d{1,2})$")
STATUS_MARKERS: Final = frozenset({"規", "日", "監", "株", "喚", "○"})
SECTIONS: Final = ("プライム", "スタンダード", "グロース", "投信等")
MARGIN_LABELS: Final = frozenset({"貸", "制", "他"})
MARGIN_CODE: Final = {"制": "1", "貸": "2", "他": "3"}
JST = ZoneInfo("Asia/Tokyo")


class JpxPublicMarginParseError(ValueError):
    """The public JPX artifact violated the observed format contract."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def jpx_public_margin_descriptor() -> ProviderDescriptor:
    """Describe the official free source without assuming redistribution rights."""

    return ProviderDescriptor(
        name="jpx_public_margin",
        license_class=LicenseClass.OFFICIAL_PUBLIC,
        redistributable=False,
        description=(
            "JPX/TSE free public daily margin publications: all-issue balances, "
            "watch flags, premium charges, and margin trading ratios."
        ),
    )


def enforce_jpx_public_margin_policy(*, mode: str) -> None:
    enforce_provider_policy(jpx_public_margin_descriptor(), mode=mode)


def parse_date(year: str, month: str, day: str) -> date:
    try:
        return date(int(year), int(month), int(day))
    except ValueError as exc:
        raise JpxPublicMarginParseError(
            f"invalid JPX date {year}/{month}/{day}"
        ) from exc


def publication_at(day: date, hour: int, minute: int) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=JST)


def provenance(
    *,
    source: str,
    source_url: str,
    retrieved_at: datetime,
    as_of: date,
    source_sha256: str,
    published_at: datetime,
    notes: list[str] | None = None,
) -> Provenance:
    return Provenance(
        provider="jpx_public_margin",
        source=source,
        source_url=source_url,
        license_class=LicenseClass.OFFICIAL_PUBLIC,
        retrieved_at=retrieved_at,
        as_of=as_of,
        notes=[
            f"sha256:{source_sha256}",
            f"published_at:{published_at.isoformat()}",
            *(notes or []),
        ],
    )


def extract_jpx_pdf_lines(data: bytes) -> list[str]:
    """Extract table rows with the existing MIT-licensed pdfminer dependency.

    The tight layout parameters reproduce JPX's visual row order rather than
    pdfminer's default multi-column reading order. Empty lines are discarded;
    the parsers still validate row identities and document-level counts.
    """

    try:
        from pdfminer.high_level import extract_text
        from pdfminer.layout import LAParams
    except ImportError as exc:  # pragma: no cover
        raise JpxPublicMarginParseError(
            "pdfminer.six is required; install yowayowa-investor[operator-jpx]"
        ) from exc
    try:
        text = extract_text(
            io.BytesIO(data),
            laparams=LAParams(
                boxes_flow=None,
                line_margin=0.1,
                char_margin=1.0,
                word_margin=0.1,
            ),
        )
    except Exception as exc:
        raise JpxPublicMarginParseError(
            f"invalid JPX PDF: {type(exc).__name__}: {exc}"
        ) from exc
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        raise JpxPublicMarginParseError("JPX PDF has no extractable text")
    return lines


def parse_int_token(raw: str, *, allow_missing: bool, field: str) -> int | None:
    value = raw.strip().replace("－", "-")
    if value in {"-", "*", ""}:
        if allow_missing:
            return None
        raise JpxPublicMarginParseError(f"missing required {field}: {raw!r}")
    negative = value.startswith("▲")
    value = value.removeprefix("▲").strip().replace(",", "")
    if not re.fullmatch(r"\d+", value):
        raise JpxPublicMarginParseError(f"invalid integer {field}: {raw!r}")
    number = int(value)
    return -number if negative else number


def required_int(raw: str, *, field: str) -> int:
    value = parse_int_token(raw, allow_missing=False, field=field)
    assert value is not None
    return value


def parse_ratio_token(raw: str, *, field: str) -> float | None:
    value = raw.strip().replace("％", "%").replace("－", "-")
    if value in {"-", "*", ""}:
        return None
    value = value.removesuffix("★").strip()
    if not value.endswith("%"):
        raise JpxPublicMarginParseError(f"invalid percentage {field}: {raw!r}")
    try:
        return float(value[:-1].replace(",", ""))
    except ValueError as exc:
        raise JpxPublicMarginParseError(f"invalid percentage {field}: {raw!r}") from exc


def load_xlsx(data: bytes):
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover
        raise JpxPublicMarginParseError(
            "openpyxl is required; install yowayowa-investor[operator-jpx]"
        ) from exc
    try:
        return load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:
        raise JpxPublicMarginParseError(
            f"invalid JPX XLSX: {type(exc).__name__}: {exc}"
        ) from exc


def xlsx_int(raw: Any, *, allow_missing: bool, field: str) -> int | None:
    if raw is None or (
        isinstance(raw, str) and raw.strip() in {"-", "－", "*", "*****", ""}
    ):
        if allow_missing:
            return None
        raise JpxPublicMarginParseError(f"missing required {field}: {raw!r}")
    if isinstance(raw, bool):
        raise JpxPublicMarginParseError(f"invalid integer {field}: {raw!r}")
    if isinstance(raw, (int, float)):
        if float(raw).is_integer():
            return int(raw)
        raise JpxPublicMarginParseError(f"non-integral {field}: {raw!r}")
    return parse_int_token(str(raw), allow_missing=allow_missing, field=field)


def required_xlsx_int(raw: Any, *, field: str) -> int:
    value = xlsx_int(raw, allow_missing=False, field=field)
    assert value is not None
    return value


def xlsx_float(raw: Any, *, allow_missing: bool, field: str) -> float | None:
    if raw is None or (
        isinstance(raw, str) and raw.strip() in {"-", "－", "*", "*****", ""}
    ):
        if allow_missing:
            return None
        raise JpxPublicMarginParseError(f"missing required {field}: {raw!r}")
    try:
        return float(raw)
    except (TypeError, ValueError) as exc:
        raise JpxPublicMarginParseError(f"invalid numeric {field}: {raw!r}") from exc


def required_xlsx_float(raw: Any, *, field: str) -> float:
    value = xlsx_float(raw, allow_missing=False, field=field)
    assert value is not None
    return value


def utc_now() -> datetime:
    return datetime.now(UTC)
