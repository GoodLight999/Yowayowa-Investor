"""JPX daily issue-level margin balance models (P4-A ingestion surface).

Models the 銘柄別信用取引残高（日次） service that JPX総研 publishes for all
TSE margin-tradable issues (publication starts 2026-09-28; contract in
``docs/JPX_DAILY_MARGIN.md``). This is distinct from the pre-existing
日々公表信用取引残高, which only covers 日々公表銘柄.

Financial-correctness invariants enforced here:

- Missing data is not zero: amount columns (金額) exist only for application
  dates from 2026-09-25 onward and stay ``None`` when absent. Amounts are
  never zero-filled and volumes are never optional.
- Per-row identity: total = negotiable (一般) + standardized (制度) for both
  the sell and the buy side, validated on the model. A violation fails the
  batch instead of being silently coerced.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field, model_validator

from yowayowa.domain import Provenance

__all__ = [
    "JPX_VALUE_AVAILABLE_FROM",
    "JpxMarginBalance",
    "JpxMarginBalancePoint",
    "JpxMarginSeries",
    "normalize_jpx_code",
]

# JPX publishes amount (value) columns only for application dates from this
# day onward (docs/JPX_DAILY_MARGIN.md, verified against the official sample
# on 2026-09-24). Earlier amounts are absent, never zero.
JPX_VALUE_AVAILABLE_FROM = date(2026, 9, 25)

_JPX_CODE_LENGTH = 5
_JPX_CODE_ALPHABET = frozenset("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ")


def normalize_jpx_code(value: str) -> str:
    """Validate a 5-character JPX local code and return it unchanged.

    JPX codes are five characters from digits plus the upper-case letters used
    in newer issues (e.g. ``13010``, ``135A0``). Lowercase or decorated input
    is rejected rather than repaired (fail-closed identifier handling).
    """

    candidate = value.strip()
    if len(candidate) != _JPX_CODE_LENGTH or not set(candidate) <= _JPX_CODE_ALPHABET:
        raise ValueError(
            f"Invalid JPX local code: {value!r} "
            f"(expected {_JPX_CODE_LENGTH} characters of digits/A-Z, e.g. '13010')"
        )
    return candidate


class JpxMarginBalance(BaseModel):
    """One issue's margin balances for one application date (申込日).

    Volumes (株数) are always present. Amounts (金額, JPY notional) are
    ``None`` for application dates before :data:`JPX_VALUE_AVAILABLE_FROM` —
    absent data is never substituted with zero.
    """

    application_date: date
    code: str
    company_name: str | None = None
    isin: str | None = None
    market_code: str | None = None
    margin_code: str | None = None

    short_total: int
    long_total: int
    short_negotiable: int
    short_standardized: int
    long_negotiable: int
    long_standardized: int

    short_total_value: int | None = None
    long_total_value: int | None = None
    short_negotiable_value: int | None = None
    short_standardized_value: int | None = None
    long_negotiable_value: int | None = None
    long_standardized_value: int | None = None

    provenance: Provenance

    @model_validator(mode="after")
    def check_total_identity(self) -> JpxMarginBalance:
        if self.short_total != self.short_negotiable + self.short_standardized:
            raise ValueError(
                f"JPX margin identity violation for {self.code} on {self.application_date}: "
                f"short_total {self.short_total} != negotiable {self.short_negotiable} "
                f"+ standardized {self.short_standardized}"
            )
        if self.long_total != self.long_negotiable + self.long_standardized:
            raise ValueError(
                f"JPX margin identity violation for {self.code} on {self.application_date}: "
                f"long_total {self.long_total} != negotiable {self.long_negotiable} "
                f"+ standardized {self.long_standardized}"
            )
        return self


class JpxMarginBalancePoint(BaseModel):
    """Read-time view of one persisted balance row plus derived fields.

    ``previous_application_date`` is ``None`` unless the previous application
    date for the same code is persisted; daily changes are computed only from
    persisted balances (never interpolated). Ratios with a zero denominator
    are ``None``, never infinity.
    """

    application_date: date
    code: str
    short_total: int
    long_total: int
    short_total_value: int | None = None
    long_total_value: int | None = None
    short_change: int | None = None
    long_change: int | None = None
    short_long_ratio: float | None = None
    previous_application_date: date | None = None
    retrieved_at: datetime
    provenance: Provenance


class JpxMarginSeries(BaseModel):
    """Time series for one code, oldest first, with per-point provenance."""

    code: str
    points: list[JpxMarginBalancePoint] = Field(default_factory=list)
