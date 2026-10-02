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
    get_portfolio,
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


def test_upsert_position_tracks_version_and_detects_conflicts() -> None:
    from decimal import Decimal

    import pytest

    from yowayowa.services.portfolios import PositionVersionConflictError, upsert_position

    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        with Session(engine, expire_on_commit=False) as session:
            portfolio = create_portfolio(session, "Core", "USD")

            # 1. Initial insert -> version=1
            p1 = upsert_position(
                session,
                portfolio.id,
                PositionUpsert(symbol="AAPL", quantity=Decimal("10"), currency="USD"),
            )
            assert p1.positions[0].version == 1
            assert p1.positions[0].quantity == Decimal("10")

            # 2. Update with matching expected_version=1 -> version=2
            p2 = upsert_position(
                session,
                portfolio.id,
                PositionUpsert(
                    symbol="AAPL",
                    quantity=Decimal("15"),
                    currency="USD",
                    expected_version=1,
                ),
            )
            assert p2.positions[0].version == 2
            assert p2.positions[0].quantity == Decimal("15")

            # 3. Update with stale expected_version=1 -> PositionVersionConflictError
            with pytest.raises(PositionVersionConflictError, match="changed"):
                upsert_position(
                    session,
                    portfolio.id,
                    PositionUpsert(
                        symbol="AAPL",
                        quantity=Decimal("99"),
                        currency="USD",
                        expected_version=1,
                    ),
                )
            # Verify rolled back: quantity remains 15, version remains 2
            reloaded = get_portfolio(session, portfolio.id)
            assert reloaded.positions[0].quantity == Decimal("15")
            assert reloaded.positions[0].version == 2

            # 4. Update with expected_version=2 -> version=3
            p3 = upsert_position(
                session,
                portfolio.id,
                PositionUpsert(
                    symbol="AAPL",
                    quantity=Decimal("20"),
                    currency="USD",
                    expected_version=2,
                ),
            )
            assert p3.positions[0].version == 3
            assert p3.positions[0].quantity == Decimal("20")

            # 5. Insert non-existent position with expected_version=1 -> conflict
            with pytest.raises(PositionVersionConflictError, match="removed or changed"):
                upsert_position(
                    session,
                    portfolio.id,
                    PositionUpsert(
                        symbol="GOOG",
                        quantity=Decimal("5"),
                        currency="USD",
                        expected_version=1,
                    ),
                )
    finally:
        engine.dispose()


def test_bulk_upsert_positions_tracks_version_and_detects_conflicts() -> None:
    from decimal import Decimal

    import pytest

    from yowayowa.services.portfolios import PositionVersionConflictError

    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        with Session(engine, expire_on_commit=False) as session:
            portfolio = create_portfolio(session, "Core", "USD")

            # Initial bulk insert
            first = bulk_upsert_positions(
                session,
                portfolio.id,
                PositionBulkUpsert(
                    positions=[
                        PositionUpsert(symbol="AAPL", quantity=Decimal("10"), currency="USD"),
                        PositionUpsert(symbol="MSFT", quantity=Decimal("20"), currency="USD"),
                    ]
                ),
            )
            by_sym = {pos.symbol: pos for pos in first.positions}
            assert by_sym["AAPL"].version == 1
            assert by_sym["MSFT"].version == 1

            # Update with expected_version
            second = bulk_upsert_positions(
                session,
                portfolio.id,
                PositionBulkUpsert(
                    positions=[
                        PositionUpsert(
                            symbol="AAPL",
                            quantity=Decimal("12"),
                            currency="USD",
                            expected_version=1,
                        ),
                        PositionUpsert(
                            symbol="MSFT",
                            quantity=Decimal("22"),
                            currency="USD",
                            expected_version=1,
                        ),
                    ]
                ),
            )
            by_sym2 = {pos.symbol: pos for pos in second.positions}
            assert by_sym2["AAPL"].version == 2
            assert by_sym2["MSFT"].version == 2

            # Mismatched expected_version on one item rolls back entire bulk
            with pytest.raises(PositionVersionConflictError):
                bulk_upsert_positions(
                    session,
                    portfolio.id,
                    PositionBulkUpsert(
                        positions=[
                            PositionUpsert(
                                symbol="AAPL",
                                quantity=Decimal("100"),
                                currency="USD",
                                expected_version=2,
                            ),
                            PositionUpsert(
                                symbol="MSFT",
                                quantity=Decimal("200"),
                                currency="USD",
                                expected_version=999,  # conflict!
                            ),
                        ]
                    ),
                )
            # Neither should have changed
            reloaded = get_portfolio(session, portfolio.id)
            by_sym_after = {pos.symbol: pos for pos in reloaded.positions}
            assert by_sym_after["AAPL"].quantity == Decimal("12")
            assert by_sym_after["AAPL"].version == 2
            assert by_sym_after["MSFT"].quantity == Decimal("22")
            assert by_sym_after["MSFT"].version == 2
    finally:
        engine.dispose()


def test_remove_position_tracks_version_and_detects_conflicts() -> None:
    from decimal import Decimal

    import pytest

    from yowayowa.services.portfolios import (
        PositionVersionConflictError,
        get_portfolio,
        remove_position,
        upsert_position,
    )

    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        with Session(engine, expire_on_commit=False) as session:
            portfolio = create_portfolio(session, "Core", "USD")
            upsert_position(
                session,
                portfolio.id,
                PositionUpsert(symbol="AAPL", quantity=Decimal("10"), currency="USD"),
            )

            # Stale expected_version fails
            with pytest.raises(PositionVersionConflictError):
                remove_position(session, portfolio.id, "AAPL", expected_version=99)
            assert len(get_portfolio(session, portfolio.id).positions) == 1

            # Matching expected_version succeeds
            remove_position(session, portfolio.id, "AAPL", expected_version=1)
            assert len(get_portfolio(session, portfolio.id).positions) == 0
    finally:
        engine.dispose()


def test_positions_version_migration_and_ddl_compatibility() -> None:
    from sqlalchemy import inspect, text

    from yowayowa.db import _ensure_positions_version

    engine = create_engine("sqlite:///:memory:")
    try:
        # Create a legacy positions table missing the version column
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE positions ("
                    "id INTEGER PRIMARY KEY, "
                    "portfolio_id INTEGER, "
                    "symbol VARCHAR(32), "
                    "quantity NUMERIC(28, 10), "
                    "average_cost NUMERIC(28, 10), "
                    "currency VARCHAR(3)"
                    ")"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO positions (id, portfolio_id, symbol, quantity, currency) "
                    "VALUES (1, 1, 'AAPL', 10, 'USD')"
                )
            )

        # Confirm version column is absent
        cols_before = {col["name"] for col in inspect(engine).get_columns("positions")}
        assert "version" not in cols_before

        # Run migration
        _ensure_positions_version(engine)

        # Confirm version column was added and existing row has default value 1
        cols_after = {col["name"] for col in inspect(engine).get_columns("positions")}
        assert "version" in cols_after

        with engine.connect() as conn:
            stmt = text("SELECT id, symbol, version FROM positions WHERE id = 1")
            row = conn.execute(stmt).fetchone()
            assert row is not None
            assert row[2] == 1  # version is 1

        # Idempotent: running again does not error
        _ensure_positions_version(engine)
    finally:
        engine.dispose()


def test_concurrent_initial_insert_unique_constraint() -> None:
    from decimal import Decimal

    import pytest

    from yowayowa.db import PortfolioRecord
    from yowayowa.services.portfolios import PositionVersionConflictError, upsert_position

    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as seed:
            portfolio = create_portfolio(seed, "Core", "USD")
            pid = portfolio.id

        with Session(engine) as s1, Session(engine) as s2:
            r1 = s1.get(PortfolioRecord, pid)
            r2 = s2.get(PortfolioRecord, pid)
            assert r1 is not None and r2 is not None
            assert list(r1.positions) == list(r2.positions) == []
            upsert_position(
                s1,
                pid,
                PositionUpsert(
                    symbol="AAPL", quantity=Decimal("10"), currency="USD", expected_version=0
                ),
            )
            with pytest.raises(PositionVersionConflictError, match="conflict / already exists"):
                upsert_position(
                    s2,
                    pid,
                    PositionUpsert(
                        symbol="AAPL", quantity=Decimal("20"), currency="USD", expected_version=0
                    ),
                )
    finally:
        engine.dispose()
