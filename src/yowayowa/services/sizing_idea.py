"""Quote-backed sizing idea construction (P5 R5).

Builds one :class:`PortfolioSizingIdea` from a single provider quote so the
operator never hand-writes entry price, currency, or provenance JSON.

Contract (AGENTS invariants 2/3, R4 currency vocabulary):

- the provider is called exactly once for the normalized requested symbol;
  no EDINET 5-digit conversion, suffix completion, or retry happens here;
- ``entry_price`` is ``Decimal(str(quote.price))`` — never rounded;
- ``currency`` is the provider-reported price currency only when it is
  resolved (``currency_source`` present, ``currency_reason`` absent);
  missing, conflicting, or invalid currencies fail closed;
- ``price_provenance`` carries the batch provenance unchanged (provider,
  source, source_url, license class, retrieved_at, notes) with the single
  requested quote's ``as_of`` made explicit — never "now";
- stop price and lot size stay caller-supplied assumptions; no stop/lot
  recommendation is generated and no lot is inferred;
- every unprovable value fails closed with a fixed machine code and a fixed
  operator-safe reason sentence.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation

from yowayowa.domain import Provenance
from yowayowa.providers.base import MarketDataProvider
from yowayowa.sizing_models import PortfolioSizingIdea, QuoteSizingIdeaRequest
from yowayowa.symbols import InputValidationError, normalize_currency, normalize_symbol

_ENTRY_ASSUMPTION_NOTE = (
    "Stop price and lot size are caller-supplied assumptions; the entry price is "
    "the latest retrieved close/price and is not a guarantee of an executable price."
)


class QuoteSizingIdeaError(ValueError):
    """Fail-closed: the quote cannot back a sizing idea (fixed code + reason)."""

    def __init__(self, code: str, reason: str) -> None:
        super().__init__(f"{code}: {reason}")
        self.code = code
        self.reason = reason


def _timezone_aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.tzinfo.utcoffset(value) is not None


def _provenance_for_quote(
    batch_provenance: Provenance, currency: str, currency_source: str, quote_as_of: datetime
) -> Provenance:
    """Copy the batch provenance; the original object is never mutated."""

    notes = [
        *batch_provenance.notes,
        f"Price currency {currency} resolved from currency_source {currency_source}.",
        _ENTRY_ASSUMPTION_NOTE,
    ]
    return batch_provenance.model_copy(update={"as_of": quote_as_of, "notes": notes})


def build_quote_sizing_idea(
    provider: MarketDataProvider, request: QuoteSizingIdeaRequest
) -> PortfolioSizingIdea:
    """Build one sizing idea from a single live provider quote. No orders."""

    try:
        symbol = normalize_symbol(request.symbol)
    except InputValidationError as exc:
        raise QuoteSizingIdeaError(
            "symbol_invalid", "Requested symbol is not a valid market symbol."
        ) from exc

    batch = provider.quotes([symbol])

    if symbol in batch.unavailable_symbols or symbol not in batch.quotes:
        raise QuoteSizingIdeaError(
            "quote_unavailable",
            "Provider returned no usable quote for the requested symbol.",
        )
    quote = batch.quotes[symbol]
    if quote.symbol != symbol:
        raise QuoteSizingIdeaError(
            "quote_symbol_mismatch",
            "Provider quote symbol does not match the requested symbol.",
        )

    try:
        entry_price = Decimal(str(quote.price))
    except (InvalidOperation, ValueError) as exc:
        raise QuoteSizingIdeaError(
            "price_invalid", "Provider quote price is missing or invalid."
        ) from exc
    if not entry_price.is_finite() or entry_price <= 0:
        raise QuoteSizingIdeaError("price_invalid", "Provider quote price is missing or invalid.")

    if quote.currency is None:
        raise QuoteSizingIdeaError(
            "currency_missing",
            "Provider did not resolve a price currency; no idea is built.",
        )
    if quote.currency_reason is not None:
        raise QuoteSizingIdeaError(
            "currency_conflict",
            "Provider reported both a price currency and a currency reason; no idea is built.",
        )
    currency_source = (quote.currency_source or "").strip()
    if not currency_source:
        raise QuoteSizingIdeaError(
            "currency_source_missing",
            "Provider price currency has no resolvable currency_source; no idea is built.",
        )
    try:
        currency = normalize_currency(quote.currency)
    except InputValidationError as exc:
        raise QuoteSizingIdeaError(
            "currency_invalid", "Provider price currency is not an ISO-style currency code."
        ) from exc

    provenance = batch.provenance
    if not provenance.provider.strip() or not provenance.source.strip() or provenance.as_of is None:
        raise QuoteSizingIdeaError(
            "provenance_insufficient",
            "Quote provenance is missing provider, source, or as_of; no idea is built.",
        )
    if not _timezone_aware(provenance.retrieved_at):
        raise QuoteSizingIdeaError(
            "provenance_insufficient",
            "Quote retrieval timestamp carries no timezone; no idea is built.",
        )
    if not _timezone_aware(quote.as_of):
        raise QuoteSizingIdeaError(
            "provenance_insufficient",
            "Quote as-of timestamp carries no timezone; no idea is built.",
        )

    if request.stop_price >= entry_price:
        raise QuoteSizingIdeaError(
            "stop_not_below_entry",
            "Stop price must be below the fetched entry price for long sizing.",
        )

    price_provenance = _provenance_for_quote(provenance, currency, currency_source, quote.as_of)
    return PortfolioSizingIdea(
        symbol=symbol,
        entry_price=entry_price,
        stop_price=request.stop_price,
        currency=currency,
        lot_size=request.lot_size,
        price_provenance=price_provenance,
    )
