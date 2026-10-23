"""Price-currency vocabulary shared by quote providers and valuation (P5 R4).

Every surface that reports a price currency (``/v1/markets/quotes``,
``/v1/valuation/{symbol}``, CLI) uses these two fields:

``currency_source``
    Set to a ``CURRENCY_SOURCE_*`` value when a provider resolved a real
    ISO-4217 code, so the reading can be traced to the provider field it came
    from.

``currency_reason``
    Set to a ``CURRENCY_REASON_*`` value when ``currency`` is ``None``, so a
    missing currency is machine-readable instead of a silent ``null``.

Rules:
- A currency is only ever copied from provider data. Ticker suffixes, exchange
  names and currency tables are not evidence and must not fill these fields.
- When a provider cannot supply a code it reports ``currency=None`` plus the
  matching reason; it never omits both fields.
"""

from __future__ import annotations

CURRENCY_SOURCE_FAST_INFO = "yahoo:fast_info"

CURRENCY_REASON_NOT_PROVIDED = "provider_returned_no_currency"
CURRENCY_REASON_INVALID = "provider_returned_invalid_currency"
CURRENCY_REASON_LOOKUP_FAILED = "provider_lookup_failed"
