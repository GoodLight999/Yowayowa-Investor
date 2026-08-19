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
from yowayowa.services.portfolios import portfolio_analytics


class LongShortProvider:
    def quotes(self, symbols: list[str]) -> MarketQuoteBatch:
        now = datetime(2026, 8, 12, tzinfo=UTC)
        quotes = {
            "LONG": MarketQuote(symbol="LONG", price=100, previous_close=98, as_of=now),
            "SHORT": MarketQuote(symbol="SHORT", price=50, previous_close=55, as_of=now),
        }
        requested = {symbol.upper() for symbol in symbols}
        return MarketQuoteBatch(
            quotes={symbol: quote for symbol, quote in quotes.items() if symbol in requested},
            unavailable_symbols=sorted(requested - quotes.keys()),
            provenance=Provenance(
                provider="fixture",
                source="fixture",
                license_class=LicenseClass.PERSONAL_ONLY,
                retrieved_at=now,
                as_of=now,
            ),
        )


def test_long_short_portfolio_separates_net_and_gross_exposure() -> None:
    now = datetime(2026, 8, 12, tzinfo=UTC)
    portfolio = Portfolio(
        id=2,
        name="Hedged",
        base_currency="USD",
        positions=[
            Position(symbol="LONG", quantity=Decimal("10"), currency="USD"),
            Position(symbol="SHORT", quantity=Decimal("-4"), currency="USD"),
        ],
        created_at=now,
        updated_at=now,
    )

    result = portfolio_analytics(portfolio, LongShortProvider())

    assert result.net_market_value == pytest.approx(800)
    assert result.gross_market_value == pytest.approx(1200)
    assert sum(position.weight for position in result.positions) == pytest.approx(1)
    assert result.positions[0].symbol == "LONG"
    assert result.positions[0].weight == pytest.approx(1000 / 1200)
    assert result.positions[1].symbol == "SHORT"
    assert result.positions[1].weight == pytest.approx(200 / 1200)
    assert result.day_pnl == pytest.approx(40)
    assert result.day_change_pct == pytest.approx(40 / (980 + 220))
