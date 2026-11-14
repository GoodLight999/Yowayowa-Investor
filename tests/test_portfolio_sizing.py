from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from yowayowa.domain import (
    LicenseClass,
    Portfolio,
    PortfolioAnalytics,
    Position,
    PositionAnalytics,
    Provenance,
)
from yowayowa.services.portfolio_sizing import (
    PortfolioSizingError,
    portfolio_sizing_proposals,
)
from yowayowa.sizing_models import PortfolioSizingIdea, PortfolioSizingRequest

_NOW = datetime(2026, 9, 29, 0, 0, tzinfo=UTC)


def _provenance(source: str, *, as_of: datetime | None = _NOW) -> Provenance:
    return Provenance(
        provider="fixture",
        source=source,
        license_class=LicenseClass.OFFICIAL_PUBLIC,
        retrieved_at=_NOW,
        as_of=as_of,
    )


def _portfolio() -> Portfolio:
    return Portfolio(
        id=7,
        name="Core",
        base_currency="USD",
        positions=[
            Position(symbol="CORE", quantity=Decimal("90"), currency="USD"),
            Position(symbol="AAPL", quantity=Decimal("10"), currency="USD"),
        ],
        created_at=_NOW,
        updated_at=_NOW,
    )


def _valuation(
    *,
    unavailable_symbols: list[str] | None = None,
    omit_symbol: str | None = None,
) -> PortfolioAnalytics:
    position_data = [
        ("CORE", Decimal("90"), 100.0, 9000.0),
        ("AAPL", Decimal("10"), 100.0, 1000.0),
    ]
    positions = [
        PositionAnalytics(
            symbol=symbol,
            quantity=quantity,
            currency="USD",
            price=price,
            fx_to_base=1.0,
            market_value_base=market_value,
            weight=market_value / 10000.0,
            as_of=_NOW,
        )
        for symbol, quantity, price, market_value in position_data
        if symbol != omit_symbol
    ]
    return PortfolioAnalytics(
        portfolio_id=7,
        name="Core",
        base_currency="USD",
        net_market_value=10000.0,
        gross_market_value=10000.0,
        known_cost_basis=10000.0,
        known_cost_market_value=10000.0,
        unrealized_pnl=0.0,
        day_pnl=0.0,
        largest_position_weight=0.9,
        concentration_hhi=0.82,
        positions=positions,
        currency_exposure=[],
        unavailable_symbols=unavailable_symbols or [],
        provenance=_provenance("portfolio quotes"),
        evaluated_at=_NOW,
    )


def _idea(
    symbol: str,
    *,
    entry: str = "100",
    stop: str = "90",
    currency: str = "USD",
    lot_size: int = 1,
    provenance_as_of: datetime | None = _NOW,
) -> PortfolioSizingIdea:
    return PortfolioSizingIdea(
        symbol=symbol,
        entry_price=Decimal(entry),
        stop_price=Decimal(stop),
        currency=currency,
        lot_size=lot_size,
        price_provenance=_provenance(f"{symbol} quote", as_of=provenance_as_of),
    )


def _request(*ideas: PortfolioSizingIdea, risk: str = "0.02", max_position: str = "0.20"):
    return PortfolioSizingRequest(
        ideas=list(ideas),
        risk_budget_pct=Decimal(risk),
        max_position_pct=Decimal(max_position),
    )


def test_sizing_uses_existing_position_and_risk_limit_deterministically() -> None:
    request = _request(_idea("AAPL"))

    result = portfolio_sizing_proposals(_portfolio(), _valuation(), request)
    proposal = result.ideas[0]

    assert proposal.quantity == 10  # $1,000 remaining under the 20% position cap.
    assert proposal.risk_limited_quantity == 20
    assert proposal.position_limited_quantity == 10
    assert proposal.estimated_stop_loss_base == Decimal("100")
    assert proposal.projected_position_value_base == Decimal("2000")
    assert proposal.limiting_constraints == ["position_limit"]
    assert result.total_risk_budget_base == Decimal("200.00")
    assert result.provenance[0].source == "portfolio quotes"
    assert result.provenance[1].source == "AAPL quote"
    assert result.executable is False


def test_risk_budget_is_split_across_buy_ideas_and_respects_lot_size() -> None:
    request = _request(
        _idea("NEW1", lot_size=5),
        _idea("NEW2", lot_size=5),
        max_position="1",
    )

    result = portfolio_sizing_proposals(_portfolio(), _valuation(), request)

    assert result.per_idea_risk_budget_base == Decimal("100.00")
    assert [item.quantity for item in result.ideas] == [10, 10]
    assert all(item.quantity % item.lot_size == 0 for item in result.ideas)


def test_sizing_returns_zero_shares_when_one_lot_exceeds_position_capacity() -> None:
    request = _request(_idea("EXPENSIVE", entry="1000", stop="900"), max_position="0.01")

    result = portfolio_sizing_proposals(_portfolio(), _valuation(), request)
    proposal = result.ideas[0]

    assert proposal.quantity == 0
    assert proposal.proposed_notional_base == 0
    assert proposal.estimated_stop_loss_base == 0
    assert proposal.limiting_constraints == ["position_limit"]


def test_sizing_fails_closed_for_unavailable_or_missing_holdings() -> None:
    request = _request(_idea("NEW"))

    with pytest.raises(PortfolioSizingError, match="valuation is incomplete"):
        portfolio_sizing_proposals(_portfolio(), _valuation(unavailable_symbols=["CORE"]), request)
    with pytest.raises(PortfolioSizingError, match="missing position CORE"):
        portfolio_sizing_proposals(_portfolio(), _valuation(omit_symbol="CORE"), request)


def test_sizing_rejects_cross_currency_ideas() -> None:
    request = _request(_idea("NEW", currency="EUR"))

    with pytest.raises(PortfolioSizingError, match="cross-currency candidates fail closed"):
        portfolio_sizing_proposals(_portfolio(), _valuation(), request)


def test_sizing_requires_price_provenance_as_of() -> None:
    with pytest.raises(ValidationError, match="as_of"):
        _idea("NEW", provenance_as_of=None)
