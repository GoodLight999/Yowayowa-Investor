"""Credit margin weekly (信用残・週次) models, per code and week (P4-C).

Independent of :mod:`yowayowa.jpx_models` (P4-A): weekly credit-balance data
published by Yahoo!ファイナンス and 株探 carries sell/buy outstanding balances
only — no negotiated (一般) / standardized (制度) breakdown — so the identity
-bearing daily model is NOT reused and the identity check must never be
satisfied by zero-filling (CTO design smoke proved a reuse would validate the
daily identity trivially with zeros).

Financial-correctness invariants enforced here:

- Persisted unit is **shares (株 int)**: Yahoo values as published; 株探 is
  published in 千株 and multiplied by 1000 at parse time (±100 share rounding,
  see docs/CREDIT_MARGIN.md).
- No amount (金額) columns exist in the sources; none are modeled — absent
  data is never zero-filled, and no missing column is invented.
- ``code`` is the bare JPX local code as the scraped sources spell it (4
  digits, e.g. ``6758``; Yahoo's ``.T`` suffix is a transport detail handled
  by the providers, not the model). The validation follows the repo-wide
  symbol grammar (:func:`yowayowa.symbols.normalize_symbol`), which accepts
  the 4-digit spelling the two weekly sources publish.
"""

from __future__ import annotations

import re
from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator

from yowayowa.domain import Provenance

__all__ = [
    "CREDIT_MARGIN_HISTORY_WEEKS",
    "CreditMarginWeekly",
    "CreditMarginWeeklyPoint",
    "CreditMarginWeeklySeries",
    "normalize_credit_margin_code",
    "yahoo_credit_margin_symbol",
]

# JPX local code (4 digits) with the Yahoo .T suffix ("6758" -> "6758.T").
_YAHOO_SYMBOL_SUFFIX = ".T"

_CREDIT_MARGIN_CODE_RE = re.compile(r"^\d{4}$")


def normalize_credit_margin_code(value: str) -> str:
    """Validate input and return the bare 4-digit JPX local code.

    Accepts either a bare code (``6758``) or the Yahoo-style symbol
    (``6758.T``); any other decoration fails closed (never repaired).
    """

    candidate = value.strip()
    code = (
        candidate.removesuffix(_YAHOO_SYMBOL_SUFFIX)
        if candidate.endswith(_YAHOO_SYMBOL_SUFFIX)
        else candidate
    )
    if not _CREDIT_MARGIN_CODE_RE.fullmatch(code):
        raise ValueError(
            f"Invalid local code for credit margin: {value!r} "
            "(expected 4 digits, e.g. '6758', optionally with the '.T' suffix)"
        )
    return code


def yahoo_credit_margin_symbol(code: str) -> str:
    """``normalize_credit_margin_code`` + the Yahoo ``.T`` symbol suffix."""

    return normalize_credit_margin_code(code) + _YAHOO_SYMBOL_SUFFIX


class CreditMarginWeekly(BaseModel):
    """One code's credit balances for one week (金曜締め残).

    ``as_of_date`` is the week-ending balance date both sources agree on
    (e.g. ``2026-09-11``). Volumes are shares (株 int) for both providers.
    """

    as_of_date: date
    code: str
    short_total: int
    long_total: int

    provenance: Provenance

    @field_validator("code")
    @classmethod
    def check_code(cls, value: str) -> str:
        return normalize_credit_margin_code(value)


class CreditMarginWeeklyPoint(BaseModel):
    """Read-time view of one persisted weekly row plus derived fields.

    Derived fields follow the existing read-time rule (same style as
    :class:`yowayowa.jpx_models.JpxMarginBalancePoint`): changes vs the
    previous persisted week for the same code, ``None`` when the previous
    week is not persisted (never interpolated); ratio with a zero
    denominator is ``None``, never infinity.
    """

    as_of_date: date
    code: str
    short_total: int
    long_total: int
    short_change: int | None = None
    long_change: int | None = None
    short_long_ratio: float | None = None
    previous_as_of_date: date | None = None
    retrieved_at: datetime
    provenance: Provenance


class CreditMarginWeeklySeries(BaseModel):
    """Time series for one code, oldest first, with per-point provenance."""

    code: str
    points: list[CreditMarginWeeklyPoint] = Field(default_factory=list)


# Weekly datapoints each provider page covers, for documentation/tests only
# (never arithmetic): kabutan publishes 4 weeks per page, Yahoo 20.
CREDIT_MARGIN_HISTORY_WEEKS = {"yahoo_finance_margin": 20, "kabutan_margin": 4}
