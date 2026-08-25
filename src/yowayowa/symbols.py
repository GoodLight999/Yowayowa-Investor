from __future__ import annotations

import re

_SYMBOL_PATTERN = re.compile(r"^[A-Z0-9.^=_:/-]{1,32}$")
_CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")


class InputValidationError(ValueError):
    """Raised when user-supplied market identifiers violate the accepted grammar."""


def normalize_symbol(value: str) -> str:
    normalized = value.strip().upper()
    if not _SYMBOL_PATTERN.fullmatch(normalized):
        raise InputValidationError(f"Invalid market symbol: {value!r}")
    return normalized


def normalize_currency(value: str) -> str:
    normalized = value.strip().upper()
    if not _CURRENCY_PATTERN.fullmatch(normalized):
        raise InputValidationError(f"Invalid ISO-style currency code: {value!r}")
    return normalized
