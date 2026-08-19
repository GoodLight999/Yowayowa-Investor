from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from yowayowa.db import Base
from yowayowa.domain import (
    LicenseClass,
    PortfolioAnalytics,
    PositionBulkUpsert,
    PositionUpsert,
    Provenance,
)
from yowayowa.services.portfolios import (
    bulk_upsert_positions,
    create_portfolio,
    list_portfolio_snapshots,
    record_portfolio_snapshot,
)


def test_bulk_import_normalizes_and_can_replace_positions() -> None:
    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        with Session(engine, expire_on_commit=False) as session:
            portfolio = create_portfolio(session, "Core", "usd")
            first = bulk_upsert_positions(
                session,
                portfolio.id,
                PositionBulkUpsert(
                    positions=[
                        PositionUpsert(symbol=" rklb ", quantity="10", currency="usd"),
                        PositionUpsert(symbol="asts", quantity="5", currency="USD"),
                    ]
                ),
            )
            assert [item.symbol for item in first.positions] == ["ASTS", "RKLB"]
            replaced = bulk_upsert_positions(
                session,
                portfolio.id,
                PositionBulkUpsert(
                    positions=[PositionUpsert(symbol="SOFI", quantity="20", currency="usd")],
                    replace=True,
                ),
            )
            assert [item.symbol for item in replaced.positions] == ["SOFI"]
    finally:
        engine.dispose()


def test_portfolio_snapshots_are_returned_chronologically() -> None:
    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        with Session(engine, expire_on_commit=False) as session:
            portfolio = create_portfolio(session, "Core", "USD")
            provenance = Provenance(
                provider="fake",
                source="fixture",
                license_class=LicenseClass.OFFICIAL_PUBLIC,
                retrieved_at=datetime.now(UTC),
            )
            for value, captured in [
                (110.0, datetime(2026, 8, 13, 2, tzinfo=UTC)),
                (100.0, datetime(2026, 8, 13, 1, tzinfo=UTC)),
            ]:
                record_portfolio_snapshot(
                    session,
                    PortfolioAnalytics(
                        portfolio_id=portfolio.id,
                        name=portfolio.name,
                        base_currency="USD",
                        net_market_value=value,
                        gross_market_value=value,
                        known_cost_basis=90,
                        known_cost_market_value=value,
                        unrealized_pnl=value - 90,
                        day_pnl=1,
                        largest_position_weight=1,
                        concentration_hhi=1,
                        positions=[],
                        currency_exposure=[],
                        provenance=provenance,
                        evaluated_at=captured,
                    ),
                )
            history = list_portfolio_snapshots(session, portfolio.id)
            assert [item.net_market_value for item in history] == [100.0, 110.0]
            assert history[0].captured_at < history[1].captured_at
    finally:
        engine.dispose()
