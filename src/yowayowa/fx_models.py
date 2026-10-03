"""FX pair normalization and analysis-surface models (Phase 1, propose-only).

FX is a personal-mode *analysis* surface only: this module never carries order
payloads or execution instructions, and ``FxProposalSpec.action`` is fixed to
``propose_only``. Missing market data stays ``None`` with provenance; it is
never substituted with zero.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from yowayowa.domain import Provenance
from yowayowa.symbols import InputValidationError

__all__ = [
    "DEFAULT_FX_RISK_NOTE",
    "INFERENCE_TOKEN_PREFIX",
    "SOURCED_TOKEN_PREFIX",
    "SUPPORTED_CRYPTO_ASSETS",
    "SUPPORTED_FX_CURRENCIES",
    "FxDirection",
    "FxHistory",
    "FxHistoryPoint",
    "FxProposalSpec",
    "FxRateSnapshot",
    "build_fx_proposal",
    "fx_pair_to_yahoo",
    "normalize_fx_pair",
    "separate_rationale_tokens",
    "yahoo_to_fx_pair",
]

# Curated set of currency codes this surface accepts. Unknown codes fail
# closed instead of being forwarded to a provider that would silently return
# unrelated data. Yahoo-delisted codes (e.g. RUB) are intentionally absent.
SUPPORTED_FX_CURRENCIES: frozenset[str] = frozenset(
    {
        "USD",
        "EUR",
        "JPY",
        "GBP",
        "CHF",
        "AUD",
        "CAD",
        "NZD",
        "CNY",
        "HKD",
        "SGD",
        "TWD",
        "KRW",
        "INR",
        "IDR",
        "MYR",
        "PHP",
        "THB",
        "VND",
        "ZAR",
        "TRY",
        "ILS",
        "AED",
        "SAR",
        "PLN",
        "SEK",
        "NOK",
        "DKK",
        "CZK",
        "HUF",
        "RON",
        "MXN",
        "BRL",
        "CLP",
        "COP",
        "PEN",
        "ARS",
    }
)

SUPPORTED_CRYPTO_ASSETS: frozenset[str] = frozenset({"BTC", "ETH"})

_FX_PAIR_PATTERN = re.compile(r"^[A-Z]{6}$")

SOURCED_TOKEN_PREFIX = "sourced:"
INFERENCE_TOKEN_PREFIX = "inference:"

DEFAULT_FX_RISK_NOTE = (
    "Analysis only: proposals carry no order payload and trigger no execution. "
    "Rates are provider-sourced snapshots and may be delayed; verify against "
    "live broker data before acting."
)


def _is_supported_fx_code(code: str) -> bool:
    return code in SUPPORTED_FX_CURRENCIES or code in SUPPORTED_CRYPTO_ASSETS


def normalize_fx_pair(value: str) -> str:
    """Validate a 6-letter FX pair in ``ABC/DEF`` notation and return it.

    Uppercase-only and no internal separators: lowercase or decorated input is
    rejected rather than silently repaired (fail-closed symbol handling).
    """

    candidate = value.strip()
    if not _FX_PAIR_PATTERN.fullmatch(candidate):
        raise InputValidationError(
            f"Invalid FX pair: {value!r} (expected 6 uppercase letters, e.g. 'USDJPY')"
        )
    base, quote = candidate[:3], candidate[3:]
    for code in (base, quote):
        if not _is_supported_fx_code(code):
            raise InputValidationError(f"Unsupported FX currency code: {code!r}")
    if base == quote:
        raise InputValidationError(
            f"Invalid FX pair: {value!r} (base and quote currency must differ)"
        )
    return candidate


def fx_pair_to_yahoo(pair: str) -> str:
    """Translate a normalized FX pair to Yahoo Finance ticker notation.

    - ``USDJPY`` -> ``JPY=X`` (Yahoo quotes USD-base pairs in the short form)
    - ``EURUSD`` -> ``EURUSD=X`` (generic cross)
    - ``BTCUSD`` -> ``BTC-USD`` (Yahoo quotes crypto as the dash-form base)
    """

    normalized = normalize_fx_pair(pair)
    base, quote = normalized[:3], normalized[3:]
    if base == "USD":
        return f"{quote}=X"
    if base in SUPPORTED_CRYPTO_ASSETS:
        return f"{base}-{quote}"
    if quote in SUPPORTED_CRYPTO_ASSETS:
        raise InputValidationError(
            f"Unsupported FX pair: {normalized!r} (Yahoo quotes crypto as the base asset only)"
        )
    return f"{normalized}=X"


def yahoo_to_fx_pair(symbol: str) -> str:
    """Translate a Yahoo Finance FX/crypto ticker back to a normalized pair."""

    candidate = symbol.strip().upper()
    if candidate.endswith("=X"):
        stem = candidate[:-2]
        if len(stem) == 3:
            if stem == "USD":
                raise InputValidationError(
                    f"Invalid Yahoo FX symbol: {symbol!r} ('USD=X' does not exist)"
                )
            # Short form: Yahoo's implicit base is USD ('JPY=X' is USD/JPY).
            return normalize_fx_pair(f"USD{stem}")
        return normalize_fx_pair(stem)
    base, dash, quote = candidate.partition("-")
    if dash:
        if not quote or base not in SUPPORTED_CRYPTO_ASSETS:
            raise InputValidationError(f"Unsupported Yahoo FX symbol: {symbol!r}")
        return normalize_fx_pair(base + quote)
    raise InputValidationError(f"Unsupported Yahoo FX symbol: {symbol!r}")


class FxDirection(StrEnum):
    LONG = "long"
    SHORT = "short"
    FLAT = "flat"


class FxRateSnapshot(BaseModel):
    pair: str
    rate: float
    previous_close: float | None = None
    as_of: datetime
    provenance: Provenance


class FxHistoryPoint(BaseModel):
    """One OHLC bar. Absent fields stay ``None`` (never zero-filled)."""

    timestamp: datetime
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: float | None = None


class FxHistory(BaseModel):
    pair: str
    interval: str
    points: list[FxHistoryPoint]
    provenance: Provenance


class FxProposalSpec(BaseModel):
    """Propose-only analysis artifact.

    ``action`` is a fixed literal: this surface cannot express an execution
    instruction. ``rationale_tokens`` separates sourced facts from derived /
    AI-authored reasoning via the ``sourced:`` / ``inference:`` prefixes so a
    consumer never has to guess which is which. ``strength`` is optional and
    stays ``None`` when there is no basis for it (fail-closed).
    """

    action: Literal["propose_only"] = "propose_only"
    pair: str
    direction: FxDirection
    strength: float | None = Field(default=None, ge=0.0, le=1.0)
    rationale_tokens: list[str] = Field(default_factory=list)
    risk_note: str | None = None
    generated_at: datetime
    provenance: Provenance


def separate_rationale_tokens(tokens: list[str]) -> tuple[list[str], list[str]]:
    """Split rationale tokens into ``(sourced facts, inference)``.

    Unknown tags raise instead of being silently passed through, so an
    untagged claim can never masquerade as either class.
    """

    sourced: list[str] = []
    inference: list[str] = []
    for token in tokens:
        if token.startswith(SOURCED_TOKEN_PREFIX):
            sourced.append(token)
        elif token.startswith(INFERENCE_TOKEN_PREFIX):
            inference.append(token)
        else:
            raise InputValidationError(
                f"Rationale token must start with '{SOURCED_TOKEN_PREFIX}' or "
                f"'{INFERENCE_TOKEN_PREFIX}': {token!r}"
            )
    return sourced, inference


def build_fx_proposal(
    pair: str,
    direction: FxDirection,
    strength: float | None,
    quote: FxRateSnapshot,
    *,
    generated_at: datetime | None = None,
    risk_note: str | None = None,
) -> FxProposalSpec:
    """Build a deterministic propose-only spec from a sourced rate snapshot.

    Inference tokens here are deterministic derivations of sourced data; any
    future AI-authored rationale must carry the same ``inference:`` tag so the
    separation stays machine-checkable. Without a previous close there is no
    change/momentum token at all (missing data is not inferred from zero).
    """

    normalized = normalize_fx_pair(pair)
    if normalized != quote.pair:
        raise InputValidationError(
            f"Proposal pair {normalized!r} does not match quote pair {quote.pair!r}"
        )
    tokens: list[str] = [
        f"{SOURCED_TOKEN_PREFIX}rate={quote.rate}",
        f"{SOURCED_TOKEN_PREFIX}as_of={quote.as_of.isoformat()}",
    ]
    change_1d: float | None = None
    if quote.previous_close is not None and quote.previous_close != 0:
        change_1d = quote.rate / quote.previous_close - 1.0
        tokens.append(f"{SOURCED_TOKEN_PREFIX}change_1d={change_1d:+.6f}")
    if change_1d is not None:
        if change_1d > 0:
            momentum = "up"
        elif change_1d < 0:
            momentum = "down"
        else:
            momentum = "flat"
        tokens.append(f"{INFERENCE_TOKEN_PREFIX}momentum={momentum}")
    return FxProposalSpec(
        pair=normalized,
        direction=direction,
        strength=strength,
        rationale_tokens=tokens,
        risk_note=risk_note or DEFAULT_FX_RISK_NOTE,
        generated_at=generated_at or datetime.now(UTC),
        provenance=quote.provenance,
    )
