from datetime import UTC, datetime
from decimal import Decimal

import pytest

from yowayowa.domain import (
    LicenseClass,
    MarketQuote,
    MarketQuoteBatch,
    Portfolio,
    Position,
    Provenance,
)
from yowayowa.services.portfolios import _fx_symbol, portfolio_analytics


class FakeMarketProvider:
    def quotes(self, symbols: list[str]) -> MarketQuoteBatch:
        now = datetime(2026, 8, 12, tzinfo=UTC)
        available = {
            "RKLB": MarketQuote(symbol="RKLB", price=20, previous_close=18, as_of=now),
            "7203.T": MarketQuote(symbol="7203.T", price=3000, previous_close=2900, as_of=now),
            "JPYUSD=X": MarketQuote(
                symbol="JPYUSD=X",
                price=0.0065,
                previous_close=0.0064,
                as_of=now,
            ),
        }
        requested = {symbol.upper() for symbol in symbols}
        return MarketQuoteBatch(
            quotes={symbol: quote for symbol, quote in available.items() if symbol in requested},
            unavailable_symbols=sorted(requested - available.keys()),
            provenance=Provenance(
                provider="fixture",
                source="fixture",
                license_class=LicenseClass.PERSONAL_ONLY,
                retrieved_at=now,
                as_of=now,
            ),
        )


def sample_portfolio() -> Portfolio:
    now = datetime(2026, 8, 12, tzinfo=UTC)
    return Portfolio(
        id=1,
        name="Mixed",
        base_currency="USD",
        positions=[
            Position(
                symbol="RKLB",
                quantity=Decimal("10"),
                average_cost=Decimal("15"),
                currency="USD",
            ),
            Position(
                symbol="7203.T",
                quantity=Decimal("2"),
                average_cost=Decimal("2800"),
                currency="JPY",
            ),
        ],
        created_at=now,
        updated_at=now,
    )


def test_fx_symbol_uses_direct_conversion_quote() -> None:
    assert _fx_symbol("USD", "JPY") == "JPY=X"
    assert _fx_symbol("JPY", "USD") == "JPYUSD=X"
    assert _fx_symbol("EUR", "JPY") == "EURJPY=X"
    assert _fx_symbol("USD", "USD") is None


def test_portfolio_analytics_includes_security_and_fx_daily_move() -> None:
    result = portfolio_analytics(sample_portfolio(), FakeMarketProvider())

    assert result.net_market_value == pytest.approx(239.0)
    assert result.gross_market_value == pytest.approx(239.0)
    assert result.known_cost_basis == pytest.approx(186.4)
    assert result.unrealized_pnl == pytest.approx(52.6)
    assert result.unrealized_pnl_pct == pytest.approx(52.6 / 186.4)
    assert result.day_pnl == pytest.approx(21.88)
    assert result.day_change_pct == pytest.approx(21.88 / (180 + 37.12))

    rklb, toyota = result.positions
    assert rklb.symbol == "RKLB"
    assert rklb.weight == pytest.approx(200 / 239)
    assert toyota.symbol == "7203.T"
    assert toyota.fx_to_base == pytest.approx(0.0065)
    assert toyota.previous_fx_to_base == pytest.approx(0.0064)
    assert toyota.day_pnl_base == pytest.approx(1.88)
    assert toyota.unrealized_pnl_base == pytest.approx(2.6)
    assert result.largest_position_weight == pytest.approx(200 / 239)
    assert result.concentration_hhi == pytest.approx((200 / 239) ** 2 + (39 / 239) ** 2)
    assert [item.currency for item in result.currency_exposure] == ["USD", "JPY"]
    assert any("acquisition-time FX" in note for note in result.provenance.notes)


def test_missing_fx_marks_foreign_position_unavailable() -> None:
    portfolio = sample_portfolio()

    class MissingFxProvider(FakeMarketProvider):
        def quotes(self, symbols: list[str]) -> MarketQuoteBatch:
            batch = super().quotes(symbols)
            batch.quotes.pop("JPYUSD=X", None)
            return batch

    result = portfolio_analytics(portfolio, MissingFxProvider())
    assert result.unavailable_symbols == ["7203.T"]
    assert [item.symbol for item in result.positions] == ["RKLB"]
    assert result.net_market_value == pytest.approx(200)
