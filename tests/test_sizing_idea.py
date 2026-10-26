"""Quote-backed sizing idea construction (P5 R5).

Service/model/agent-tool contract: entry price, currency, and provenance come
from one provider quote; stop price and lot size stay caller-supplied; every
unprovable value fails closed with a fixed code. Provider is called exactly
once and no input object is mutated.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from yowayowa.domain import LicenseClass, MarketQuote, MarketQuoteBatch, Provenance
from yowayowa.services.ai_agent import InvestmentResearchAgent
from yowayowa.services.sizing_idea import QuoteSizingIdeaError, build_quote_sizing_idea
from yowayowa.sizing_models import PortfolioSizingIdea, QuoteSizingIdeaRequest

_QUOTE_AS_OF = datetime(2026, 10, 22, 0, 0, tzinfo=UTC)
_RETRIEVED_AT = datetime(2026, 10, 23, 1, 2, 3, tzinfo=UTC)
_BATCH_NOTES = ["Batched 5-day daily history used for latest and previous closes."]


def _request(
    symbol: str = "7203.T",
    *,
    stop: str = "2800",
    lot: int = 100,
) -> QuoteSizingIdeaRequest:
    return QuoteSizingIdeaRequest.model_validate(
        {"symbol": symbol, "stop_price": stop, "lot_size": lot}
    )


def _provenance(**overrides: object) -> Provenance:
    fields: dict[str, object] = {
        "provider": "yahoo/yfinance",
        "source": "Yahoo Finance",
        "source_url": "https://finance.yahoo.com/markets/",
        "license_class": LicenseClass.PERSONAL_ONLY,
        "retrieved_at": _RETRIEVED_AT,
        "as_of": _QUOTE_AS_OF,
        "notes": list(_BATCH_NOTES),
    }
    fields.update(overrides)
    return Provenance.model_validate(fields)


def _quote(
    symbol: str = "7203.T",
    *,
    price: float = 3105.5,
    currency: str | None = "JPY",
    currency_source: str | None = "yahoo:fast_info",
    currency_reason: str | None = None,
) -> MarketQuote:
    return MarketQuote(
        symbol=symbol,
        price=price,
        previous_close=3050.0,
        currency=currency,
        currency_source=currency_source,
        currency_reason=currency_reason,
        as_of=_QUOTE_AS_OF,
    )


def _batch(
    quotes: dict[str, MarketQuote] | None = None,
    *,
    unavailable: list[str] | None = None,
    provenance: Provenance | None = None,
) -> MarketQuoteBatch:
    return MarketQuoteBatch(
        quotes=quotes if quotes is not None else {"7203.T": _quote()},
        unavailable_symbols=unavailable or [],
        provenance=provenance or _provenance(),
    )


class _StubProvider:
    """Records quotes() calls and replays one prepared batch."""

    def __init__(self, batch: MarketQuoteBatch) -> None:
        self._batch = batch
        self.calls: list[list[str]] = []

    def quotes(self, symbols: list[str]) -> MarketQuoteBatch:
        self.calls.append(list(symbols))
        return self._batch


def _build(
    batch: MarketQuoteBatch,
    request: QuoteSizingIdeaRequest | None = None,
) -> tuple[PortfolioSizingIdea, _StubProvider]:
    provider = _StubProvider(batch)
    idea = build_quote_sizing_idea(provider, request or _request())
    return idea, provider


def test_jpy_quote_builds_idea_from_provider_evidence() -> None:
    idea, provider = _build(_batch())

    assert provider.calls == [["7203.T"]]
    assert idea.symbol == "7203.T"
    assert idea.entry_price == Decimal(str(3105.5))
    assert idea.currency == "JPY"
    assert idea.stop_price == Decimal("2800")
    assert idea.lot_size == 100
    assert idea.price_provenance.provider == "yahoo/yfinance"
    assert idea.price_provenance.source == "Yahoo Finance"
    assert idea.price_provenance.license_class == LicenseClass.PERSONAL_ONLY
    assert idea.price_provenance.retrieved_at == _RETRIEVED_AT
    # The single requested quote's as_of is carried explicitly, not "now".
    assert idea.price_provenance.as_of == _QUOTE_AS_OF


def test_usd_quote_keeps_full_provider_precision() -> None:
    raw = 348.2200012207031
    idea, _ = _build(
        _batch({"AAPL": _quote("AAPL", price=raw, currency="USD")}),
        _request("AAPL", stop="300", lot=1),
    )

    assert idea.entry_price == Decimal(str(raw))
    assert idea.currency == "USD"


def test_request_symbol_is_normalized_without_any_conversion() -> None:
    idea, provider = _build(_batch(), _request("  7203.t "))
    assert idea.symbol == "7203.T"
    assert provider.calls == [["7203.T"]]

    # Five-digit EDINET-style codes are never rewritten into tickers.
    provider2 = _StubProvider(_batch(quotes={}, unavailable=["29010"]))
    with pytest.raises(QuoteSizingIdeaError) as excinfo:
        build_quote_sizing_idea(provider2, _request("29010", stop="1", lot=1))
    assert excinfo.value.code == "quote_unavailable"
    assert provider2.calls == [["29010"]]


def test_unavailable_symbol_fails_closed_after_one_provider_call() -> None:
    provider = _StubProvider(_batch(quotes={}, unavailable=["BAD"]))
    with pytest.raises(QuoteSizingIdeaError) as excinfo:
        build_quote_sizing_idea(provider, _request("BAD"))
    assert excinfo.value.code == "quote_unavailable"
    assert provider.calls == [["BAD"]]  # exactly one call, no retry


def test_quote_absent_from_batch_fails_closed() -> None:
    with pytest.raises(QuoteSizingIdeaError) as excinfo:
        _build(_batch(quotes={}))
    assert excinfo.value.code == "quote_unavailable"


def test_quote_key_and_quote_symbol_mismatch_fail_closed() -> None:
    # Requested symbol missing while another key exists.
    with pytest.raises(QuoteSizingIdeaError) as keyed:
        _build(_batch(quotes={"6758.T": _quote("6758.T")}))
    assert keyed.value.code == "quote_unavailable"

    # Key matches but the quote itself claims another symbol.
    with pytest.raises(QuoteSizingIdeaError) as fielded:
        _build(_batch(quotes={"7203.T": _quote(symbol="7203")}))
    assert fielded.value.code == "quote_symbol_mismatch"


@pytest.mark.parametrize("price", [0.0, -3.0, float("nan"), float("inf"), float("-inf")])
def test_non_positive_or_non_finite_prices_fail_closed(price: float) -> None:
    with pytest.raises(QuoteSizingIdeaError) as excinfo:
        _build(_batch(quotes={"7203.T": _quote(price=price)}))
    assert excinfo.value.code == "price_invalid"


def test_missing_conflicting_and_unsourced_currency_fails_closed() -> None:
    missing = _quote(currency=None, currency_reason="provider_returned_no_currency")
    with pytest.raises(QuoteSizingIdeaError) as none_reason:
        _build(_batch(quotes={"7203.T": missing}))
    assert none_reason.value.code == "currency_missing"

    contract_broken = _quote(currency=None, currency_reason=None)
    with pytest.raises(QuoteSizingIdeaError) as silent:
        _build(_batch(quotes={"7203.T": contract_broken}))
    assert silent.value.code == "currency_missing"

    conflict = _quote(currency_reason="provider_returned_no_currency")
    with pytest.raises(QuoteSizingIdeaError) as conflicted:
        _build(_batch(quotes={"7203.T": conflict}))
    assert conflicted.value.code == "currency_conflict"

    unsourced = _quote(currency_source=None)
    with pytest.raises(QuoteSizingIdeaError) as unsourced_exc:
        _build(_batch(quotes={"7203.T": unsourced}))
    assert unsourced_exc.value.code == "currency_source_missing"

    invalid = _quote(currency="JP")
    with pytest.raises(QuoteSizingIdeaError) as invalid_exc:
        _build(_batch(quotes={"7203.T": invalid}))
    assert invalid_exc.value.code == "currency_invalid"


def test_empty_provenance_fails_closed() -> None:
    for overrides in ({"provider": " "}, {"source": ""}, {"as_of": None}):
        with pytest.raises(QuoteSizingIdeaError) as excinfo:
            _build(_batch(provenance=_provenance(**overrides)))
        assert excinfo.value.code == "provenance_insufficient"


def test_naive_provider_timestamps_fail_closed() -> None:
    naive = datetime(2026, 10, 23, 1, 2, 3)
    with pytest.raises(QuoteSizingIdeaError) as retrieved:
        _build(_batch(provenance=_provenance(retrieved_at=naive)))
    assert retrieved.value.code == "provenance_insufficient"

    naive_quote = _quote()
    naive_quote.as_of = naive
    with pytest.raises(QuoteSizingIdeaError) as as_of:
        _build(_batch(quotes={"7203.T": naive_quote}))
    assert as_of.value.code == "provenance_insufficient"


@pytest.mark.parametrize("stop", ["3105.5", "3200"])
def test_stop_at_or_above_entry_fails_closed(stop: str) -> None:
    with pytest.raises(QuoteSizingIdeaError) as excinfo:
        _build(_batch(), _request(stop=stop))
    assert excinfo.value.code == "stop_not_below_entry"


def test_provenance_copy_carries_fixed_notes_without_mutating_the_batch() -> None:
    batch = _batch()
    snapshot = batch.model_dump()

    idea, _ = _build(batch)

    assert batch.model_dump() == snapshot  # inputs are never mutated
    notes = idea.price_provenance.notes
    assert notes[: len(_BATCH_NOTES)] == _BATCH_NOTES
    assert any("yahoo:fast_info" in note for note in notes[len(_BATCH_NOTES) :])
    assert any(
        "Stop price and lot size are caller-supplied assumptions" in note
        for note in notes[len(_BATCH_NOTES) :]
    )
    assert any(
        "not a guarantee of an executable price" in note for note in notes[len(_BATCH_NOTES) :]
    )


def test_idea_round_trips_into_the_existing_portfolio_sizing_request() -> None:
    idea, _ = _build(_batch())
    payload = idea.model_dump(mode="json")

    from yowayowa.sizing_models import PortfolioSizingRequest

    request = PortfolioSizingRequest.model_validate(
        {
            "ideas": [payload],
            "risk_budget_pct": "0.01",
            "max_position_pct": "0.20",
        }
    )
    assert request.ideas[0].entry_price == Decimal(str(3105.5))
    assert request.ideas[0].currency == "JPY"


# --------------------------------------------------------------------------- #
# QuoteSizingIdeaRequest model contract
# --------------------------------------------------------------------------- #


def test_request_rejects_extra_and_derived_fields() -> None:
    with pytest.raises(ValidationError):
        _request().__class__.model_validate(
            {
                "symbol": "7203.T",
                "stop_price": "2800",
                "lot_size": 100,
                "entry_price": "3105.5",
                "currency": "JPY",
                "price_provenance": _provenance().model_dump(mode="json"),
            }
        )


@pytest.mark.parametrize("stop", ["0", "-1", "NaN", "Infinity", "-Infinity", ""])
def test_request_rejects_non_positive_or_non_finite_stop(stop: str) -> None:
    with pytest.raises(ValidationError):
        _request(stop=stop)


@pytest.mark.parametrize("lot", [0, -1, 1.5, 1_000_001])
def test_request_rejects_invalid_lot_sizes(lot: int) -> None:
    with pytest.raises(ValidationError):
        _request(lot=lot)


@pytest.mark.parametrize("lot", [1, 100, 1_000_000])
def test_request_accepts_whole_lot_range(lot: int) -> None:
    assert _request(lot=lot).lot_size == lot


def test_request_requires_symbol_stop_and_lot() -> None:
    with pytest.raises(ValidationError):
        QuoteSizingIdeaRequest.model_validate({"symbol": "7203.T", "stop_price": "2800"})


# --------------------------------------------------------------------------- #
# Agent tool (direct handler calls only; no built-in LLM)
# --------------------------------------------------------------------------- #


def test_agent_tool_catalog_exposes_quote_sizing_idea() -> None:
    from yowayowa.config import Settings

    agent = InvestmentResearchAgent(Settings(database_url="sqlite:///:memory:"), Mock())
    tool = agent.tools["build_quote_sizing_idea"]

    assert "propose_portfolio_sizing" in agent.tools  # existing tool unchanged
    assert tool.mutating is False
    assert tool.parameters["required"] == ["symbol", "stop_price", "lot_size"]
    assert tool.parameters["additionalProperties"] is False


def test_agent_tool_builds_idea_through_the_shared_service(monkeypatch) -> None:
    from yowayowa.config import Settings
    from yowayowa.services import ai_agent

    provider = _StubProvider(_batch({"AAPL": _quote("AAPL", price=348.22, currency="USD")}))
    monkeypatch.setattr(ai_agent, "yahoo_market_provider", lambda: provider)

    agent = InvestmentResearchAgent(Settings(database_url="sqlite:///:memory:"), Mock())
    result = agent._tool_build_quote_sizing_idea(
        {"symbol": "aapl", "stop_price": "300", "lot_size": 1}
    )

    assert provider.calls == [["AAPL"]]
    assert result["symbol"] == "AAPL"
    assert result["entry_price"] == "348.22"
    assert result["currency"] == "USD"
    assert result["price_provenance"]["as_of"] == _QUOTE_AS_OF.isoformat().replace("+00:00", "Z")


def test_agent_tool_fails_closed_without_building_numbers(monkeypatch) -> None:
    from yowayowa.config import Settings
    from yowayowa.services import ai_agent

    provider = _StubProvider(_batch(quotes={}, unavailable=["MISSING"]))
    monkeypatch.setattr(ai_agent, "yahoo_market_provider", lambda: provider)

    agent = InvestmentResearchAgent(Settings(database_url="sqlite:///:memory:"), Mock())
    with pytest.raises(QuoteSizingIdeaError) as excinfo:
        agent._tool_build_quote_sizing_idea({"symbol": "MISSING", "stop_price": "1", "lot_size": 1})
    assert excinfo.value.code == "quote_unavailable"
    assert provider.calls == [["MISSING"]]
