from __future__ import annotations

from datetime import UTC, datetime
from decimal import ROUND_FLOOR, Decimal, InvalidOperation
from math import isfinite
from typing import Literal

from yowayowa.domain import Portfolio, PortfolioAnalytics, PositionAnalytics
from yowayowa.sizing_models import (
    PortfolioSizingIdea,
    PortfolioSizingProposal,
    PortfolioSizingRequest,
    SizingIdeaProposal,
)
from yowayowa.symbols import InputValidationError, normalize_currency, normalize_symbol


class PortfolioSizingError(ValueError):
    """Sizing cannot be calculated safely from the supplied portfolio evidence."""


def _decimal(value: object, label: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise PortfolioSizingError(f"{label} is missing or invalid") from exc
    if not result.is_finite():
        raise PortfolioSizingError(f"{label} must be finite")
    return result


def _position_values(
    portfolio: Portfolio,
    valuation: PortfolioAnalytics,
) -> tuple[Decimal, dict[str, PositionAnalytics]]:
    base_currency = normalize_currency(portfolio.base_currency)
    if (
        valuation.portfolio_id != portfolio.id
        or normalize_currency(valuation.base_currency) != base_currency
    ):
        raise PortfolioSizingError("Portfolio valuation does not match the requested portfolio")
    if valuation.unavailable_symbols:
        raise PortfolioSizingError(
            "Portfolio valuation is incomplete; unavailable positions: "
            + ", ".join(sorted(valuation.unavailable_symbols))
        )
    if valuation.provenance.as_of is None:
        raise PortfolioSizingError("Portfolio valuation provenance is missing its as_of date")
    if not valuation.provenance.provider.strip() or not valuation.provenance.source.strip():
        raise PortfolioSizingError(
            "Portfolio valuation provenance must identify provider and source"
        )

    positions_by_symbol: dict[str, PositionAnalytics] = {}
    gross_from_positions = Decimal(0)
    for item in valuation.positions:
        symbol = normalize_symbol(item.symbol)
        if symbol in positions_by_symbol:
            raise PortfolioSizingError(f"Portfolio valuation contains duplicate position {symbol}")
        positions_by_symbol[symbol] = item

    portfolio_symbols: set[str] = set()
    for position in portfolio.positions:
        symbol = normalize_symbol(position.symbol)
        portfolio_symbols.add(symbol)
        valued = positions_by_symbol.get(symbol)
        if valued is None:
            raise PortfolioSizingError(f"Portfolio valuation is missing position {symbol}")
        if normalize_currency(position.currency) != normalize_currency(valued.currency):
            raise PortfolioSizingError(f"Portfolio position currency mismatch for {symbol}")
        if _decimal(position.quantity, f"{symbol} holding quantity") != _decimal(
            valued.quantity, f"{symbol} valued quantity"
        ):
            raise PortfolioSizingError(f"Portfolio valuation quantity mismatch for {symbol}")
        if not isfinite(valued.price) or valued.price <= 0:
            raise PortfolioSizingError(
                f"Portfolio valuation price is missing or invalid for {symbol}"
            )
        if not isfinite(valued.fx_to_base) or valued.fx_to_base <= 0:
            raise PortfolioSizingError(
                f"Portfolio FX conversion is missing or invalid for {symbol}"
            )
        if valued.as_of is None:
            raise PortfolioSizingError(f"Portfolio valuation as_of is missing for {symbol}")
        market_value = _decimal(valued.market_value_base, f"{symbol} market value")
        expected_market_value = (
            _decimal(valued.quantity, f"{symbol} valued quantity")
            * _decimal(valued.price, f"{symbol} price")
            * _decimal(valued.fx_to_base, f"{symbol} FX conversion")
        )
        value_tolerance = max(Decimal("0.00000001"), abs(expected_market_value) * Decimal("1e-9"))
        if abs(market_value - expected_market_value) > value_tolerance:
            raise PortfolioSizingError(f"Portfolio market value is inconsistent for {symbol}")
        gross_from_positions += abs(market_value)

    if portfolio_symbols != set(positions_by_symbol):
        raise PortfolioSizingError(
            "Portfolio valuation contains positions not held in the portfolio"
        )

    gross_value = _decimal(valuation.gross_market_value, "Portfolio gross market value")
    if gross_value <= 0:
        raise PortfolioSizingError(
            "A positive, fully valued holdings basis is required; cash and liabilities "
            "are not included"
        )
    gross_tolerance = max(Decimal("0.00000001"), abs(gross_value) * Decimal("1e-9"))
    if abs(gross_value - gross_from_positions) > gross_tolerance:
        raise PortfolioSizingError(
            "Portfolio gross market value is inconsistent with valued positions"
        )
    return gross_value, positions_by_symbol


def portfolio_sizing_proposals(
    portfolio: Portfolio,
    valuation: PortfolioAnalytics,
    request: PortfolioSizingRequest,
) -> PortfolioSizingProposal:
    """Return deterministic long-only share candidates; never create or send orders.

    Risk budget is a cap on the modeled loss to each idea's supplied stop price.
    The portfolio-level budget is divided equally across the supplied ideas. The
    concentration cap is measured against gross marked holdings, excluding cash
    and liabilities. Candidate prices must be in the portfolio base currency.
    """

    try:
        base_currency = normalize_currency(portfolio.base_currency)
        gross_value, current_positions = _position_values(portfolio, valuation)
    except InputValidationError as exc:
        raise PortfolioSizingError(str(exc)) from exc

    risk_budget_pct = _decimal(request.risk_budget_pct, "Risk budget percentage")
    max_position_pct = _decimal(request.max_position_pct, "Maximum position percentage")
    if not Decimal(0) < risk_budget_pct <= Decimal(1):
        raise PortfolioSizingError("Risk budget percentage must be greater than 0 and at most 1")
    if not Decimal(0) < max_position_pct <= Decimal(1):
        raise PortfolioSizingError(
            "Maximum position percentage must be greater than 0 and at most 1"
        )

    normalized_ideas: list[tuple[str, str, PortfolioSizingIdea]] = []
    seen_symbols: set[str] = set()
    for idea in request.ideas:
        try:
            symbol = normalize_symbol(idea.symbol)
            currency = normalize_currency(idea.currency)
        except InputValidationError as exc:
            raise PortfolioSizingError(str(exc)) from exc
        if symbol in seen_symbols:
            raise PortfolioSizingError(f"Duplicate buy idea for {symbol}")
        seen_symbols.add(symbol)
        if currency != base_currency:
            raise PortfolioSizingError(
                f"Buy idea {symbol} uses {currency}; candidate currency must match portfolio "
                f"base currency {base_currency} (cross-currency candidates fail closed)"
            )
        held = current_positions.get(symbol)
        if held is not None and normalize_currency(held.currency) != currency:
            raise PortfolioSizingError(f"Buy idea currency does not match held position {symbol}")
        if idea.stop_price >= idea.entry_price:
            raise PortfolioSizingError(
                f"Buy idea {symbol} stop price must be below its entry price for long sizing"
            )
        if idea.lot_size < 1:
            raise PortfolioSizingError(
                f"Buy idea {symbol} lot size must be a positive whole number"
            )
        normalized_ideas.append((symbol, currency, idea))

    total_risk_budget = gross_value * risk_budget_pct
    per_idea_budget = total_risk_budget / Decimal(len(normalized_ideas))
    max_position_value = gross_value * max_position_pct
    proposals: list[SizingIdeaProposal] = []
    for symbol, currency, idea in normalized_ideas:
        entry_price = _decimal(idea.entry_price, f"{symbol} entry price")
        stop_price = _decimal(idea.stop_price, f"{symbol} stop price")
        lot_size = Decimal(idea.lot_size)
        risk_per_share = entry_price - stop_price
        if entry_price <= 0 or stop_price <= 0 or risk_per_share <= 0:
            raise PortfolioSizingError(f"Buy idea {symbol} prices are invalid for long sizing")

        current_value = (
            _decimal(current_positions[symbol].market_value_base, f"{symbol} market value")
            if symbol in current_positions
            else Decimal(0)
        )
        risk_lots = (per_idea_budget / (risk_per_share * lot_size)).to_integral_value(
            rounding=ROUND_FLOOR
        )
        risk_quantity = int(risk_lots * lot_size)
        position_room = max(Decimal(0), max_position_value - current_value)
        position_lots = (position_room / (entry_price * lot_size)).to_integral_value(
            rounding=ROUND_FLOOR
        )
        position_quantity = int(position_lots * lot_size)
        quantity = min(risk_quantity, position_quantity)
        constraints: list[Literal["risk_budget", "position_limit"]] = []
        if risk_quantity == quantity:
            constraints.append("risk_budget")
        if position_quantity == quantity:
            constraints.append("position_limit")
        notional = Decimal(quantity) * entry_price
        estimated_loss = Decimal(quantity) * risk_per_share
        proposals.append(
            SizingIdeaProposal(
                symbol=symbol,
                currency=currency,
                entry_price=entry_price,
                stop_price=stop_price,
                lot_size=idea.lot_size,
                current_position_value_base=current_value,
                risk_budget_base=per_idea_budget,
                risk_per_share_base=risk_per_share,
                risk_limited_quantity=risk_quantity,
                position_limited_quantity=position_quantity,
                quantity=quantity,
                proposed_notional_base=notional,
                estimated_stop_loss_base=estimated_loss,
                projected_position_value_base=current_value + notional,
                limiting_constraints=constraints,
                price_provenance=idea.price_provenance,
            )
        )

    return PortfolioSizingProposal(
        portfolio_id=portfolio.id,
        base_currency=base_currency,
        gross_market_value_base=gross_value,
        risk_budget_pct=risk_budget_pct,
        total_risk_budget_base=total_risk_budget,
        per_idea_risk_budget_base=per_idea_budget,
        max_position_pct=max_position_pct,
        max_position_value_base=max_position_value,
        ideas=proposals,
        provenance=[valuation.provenance, *(idea.price_provenance for idea in request.ideas)],
        notes=[
            "Proposal only: no order, portfolio mutation, or broker request is created or sent.",
            "Risk budget is the maximum modeled loss to the supplied stop across this idea set, "
            "split equally; unused budget is not redistributed.",
            "Portfolio basis is gross marked holdings only; cash, liabilities, fees, taxes, "
            "slippage, and gap risk are excluded.",
            "Portfolio holdings are translated by the existing valuation service; each candidate "
            "must use the portfolio base currency, with cross-currency candidates rejected.",
            "Entry price, stop price, and lot size are explicit caller-supplied assumptions; "
            "quote provenance and as_of are returned unchanged.",
        ],
        calculated_at=datetime.now(UTC),
    )
