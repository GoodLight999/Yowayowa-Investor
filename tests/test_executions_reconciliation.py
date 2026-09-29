from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from yowayowa.acquisition.models import (
    AcquisitionFetchState,
    AcquisitionOutcome,
    AuthState,
    SnapshotRecord,
)
from yowayowa.broker_models import BrokerExecution, BrokerOrderSide
from yowayowa.db import Base
from yowayowa.domain import PositionUpsert
from yowayowa.operator_bridge.rakuten_web import (
    lookup_rakuten_resource,
    normalize_execution_records,
)
from yowayowa.services.broker_read_service import BrokerReadOutcome, BrokerReadService
from yowayowa.services.executions_reconciliation import (
    ExecutionPreviewChangedError,
    ExecutionsReconciliationService,
)
from yowayowa.services.order_inquiry_service import OrderInquiryService
from yowayowa.services.portfolios import (
    applied_execution_fingerprints,
    bulk_upsert_positions,
    create_portfolio,
    get_portfolio,
)

_NOW = datetime(2026, 9, 29, 3, 0, tzinfo=UTC)


def _execution(
    execution_id: str = "fill-1",
    *,
    symbol: str = "AAPL",
    side: BrokerOrderSide = BrokerOrderSide.BUY,
    quantity: Decimal = Decimal("2"),
    price: Decimal = Decimal("30"),
    currency: str | None = "USD",
    executed_at: datetime | None = _NOW,
) -> BrokerExecution:
    return BrokerExecution(
        broker="rakuten-securities",
        execution_id=execution_id,
        broker_order_id="order-1",
        symbol=symbol,
        side=side,
        quantity=quantity,
        price=price,
        currency=currency,
        executed_at=executed_at,
        executed_at_raw=executed_at.isoformat() if executed_at else None,
    )


def _outcome(
    executions: list[BrokerExecution],
    *,
    verified: bool = True,
    fetch_state: AcquisitionFetchState = AcquisitionFetchState.OK,
    auth_state: AuthState = AuthState.AUTHENTICATED,
) -> BrokerReadOutcome:
    return BrokerReadOutcome(
        connector_id="rakuten-web",
        resource="executions",
        market="jp",
        fetch_state=fetch_state,
        auth_state=auth_state,
        executions=executions,
        detail={"verified": verified},
        source_url="https://broker.example/executions?session=not-for-provenance",
        retrieved_at=_NOW.isoformat(),
        as_of=_NOW.isoformat(),
        parser_version="fake-executions-v1",
        schema_version="fake-v1",
        snapshot=SnapshotRecord(
            snapshot_id="snapshot-1",
            connector_id="rakuten-web",
            resource="executions",
            captured_at=_NOW,
            parser_version="fake-executions-v1",
            schema_version="fake-v1",
            fetch_state=fetch_state,
            payload_sha256="a" * 64,
            as_of=_NOW,
        ),
    )


class _FakeOrderInquiry:
    def __init__(self, outcome: BrokerReadOutcome) -> None:
        self.outcome = outcome
        self.calls: list[tuple[str, bool]] = []

    def list_executions(self, market: str, force_refresh: bool = False) -> BrokerReadOutcome:
        self.calls.append((market, force_refresh))
        return self.outcome


def _database(tmp_path: Path) -> tuple[Any, Session, int]:
    engine = create_engine(f"sqlite:///{tmp_path / 'reconciliation.sqlite'}")
    Base.metadata.create_all(engine)
    session = Session(engine, expire_on_commit=False)
    portfolio = create_portfolio(session, "Reconciliation test", "USD")
    return engine, session, portfolio.id


def test_application_ledger_is_created_additively_without_losing_portfolio_data(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'existing-portfolio.sqlite'}")
    old_schema_tables = [
        table
        for table in Base.metadata.sorted_tables
        if table.name != "broker_execution_applications"
    ]
    Base.metadata.create_all(engine, tables=old_schema_tables)
    with Session(engine, expire_on_commit=False) as session:
        portfolio = create_portfolio(session, "Existing portfolio", "USD")
        portfolio_id = portfolio.id
        bulk_upsert_positions(
            session,
            portfolio_id,
            __import__("yowayowa.domain", fromlist=["PositionBulkUpsert"]).PositionBulkUpsert(
                positions=[
                    PositionUpsert(
                        symbol="AAPL",
                        quantity=Decimal("7"),
                        average_cost=Decimal("12"),
                        currency="USD",
                    )
                ]
            ),
        )

    # This is the same additive metadata operation used by init_database().
    Base.metadata.create_all(engine)
    try:
        assert inspect(engine).has_table("broker_execution_applications")
        with Session(engine, expire_on_commit=False) as session:
            positions = get_portfolio(session, portfolio_id).positions
            assert len(positions) == 1
            assert positions[0].symbol == "AAPL"
            assert positions[0].quantity == Decimal("7")
    finally:
        engine.dispose()


def test_preview_is_provenance_carrying_and_never_mutates_portfolio(tmp_path: Path) -> None:
    engine, session, portfolio_id = _database(tmp_path)
    try:
        bulk_upsert_positions(
            session,
            portfolio_id,
            __import__("yowayowa.domain", fromlist=["PositionBulkUpsert"]).PositionBulkUpsert(
                positions=[
                    PositionUpsert(
                        symbol="AAPL",
                        quantity=Decimal("10"),
                        average_cost=Decimal("20"),
                        currency="USD",
                    )
                ]
            ),
        )
        inquiry = _FakeOrderInquiry(_outcome([_execution()]))
        service = ExecutionsReconciliationService(order_inquiry=inquiry)  # type: ignore[arg-type]

        preview = service.preview(session, portfolio_id, "jp")

        assert preview.can_apply is True
        assert preview.source_url == "https://broker.example/executions"
        assert preview.snapshot_payload_sha256 == "a" * 64
        assert any(
            "overlap with manually maintained/imported holdings are unknown" in note
            for note in preview.notes
        )
        assert preview.executions[0].execution.executed_at == _NOW
        assert preview.changes[0].current_quantity == Decimal("10")
        assert preview.changes[0].target_quantity == Decimal("12")
        assert preview.changes[0].target_average_cost == Decimal(260) / Decimal(12)
        assert get_portfolio(session, portfolio_id).positions[0].quantity == Decimal("10")
        assert applied_execution_fingerprints(session, portfolio_id, "jp") == {}
    finally:
        session.close()
        engine.dispose()


def test_execution_price_display_parser_uses_explicit_row_currency() -> None:
    parsed, _ = normalize_execution_records(
        {
            "executions": [
                {
                    "execution_id": "usd-fill",
                    "symbol": "AAPL",
                    "side": "buy",
                    "quantity": "1",
                    "price": "$30.25",
                    "currency": "USD",
                    "executed_at": "2026-09-29T03:00:00Z",
                }
            ]
        },
        market="jp",
    )
    assert parsed[0].price == Decimal("30.25")
    assert parsed[0].currency == "USD"


def test_apply_requires_explicit_approval_and_is_idempotent(tmp_path: Path) -> None:
    engine, session, portfolio_id = _database(tmp_path)
    try:
        bulk_upsert_positions(
            session,
            portfolio_id,
            __import__("yowayowa.domain", fromlist=["PositionBulkUpsert"]).PositionBulkUpsert(
                positions=[
                    PositionUpsert(
                        symbol="AAPL",
                        quantity=Decimal("10"),
                        average_cost=Decimal("20"),
                        currency="USD",
                    )
                ]
            ),
        )
        inquiry = _FakeOrderInquiry(_outcome([_execution()]))
        service = ExecutionsReconciliationService(order_inquiry=inquiry)  # type: ignore[arg-type]
        preview = service.preview(session, portfolio_id, "jp")

        with pytest.raises(ValueError, match="operator_approved"):
            service.apply(
                session,
                portfolio_id,
                "jp",
                preview.preview_id,
                operator_approved=False,
            )
        assert get_portfolio(session, portfolio_id).positions[0].quantity == Decimal("10")

        result = service.apply(
            session,
            portfolio_id,
            "jp",
            preview.preview_id,
            operator_approved=True,
        )

        assert result.applied_execution_ids == ["fill-1"]
        assert result.portfolio.positions[0].quantity == Decimal("12")
        assert applied_execution_fingerprints(session, portfolio_id, "jp")
        repeated_preview = service.preview(session, portfolio_id, "jp")
        assert repeated_preview.can_apply is False
        assert repeated_preview.executions[0].state == "already_applied"
        assert get_portfolio(session, portfolio_id).positions[0].quantity == Decimal("12")
    finally:
        session.close()
        engine.dispose()


def test_apply_rejects_a_stale_preview_when_local_positions_change(tmp_path: Path) -> None:
    engine, session, portfolio_id = _database(tmp_path)
    try:
        inquiry = _FakeOrderInquiry(_outcome([_execution()]))
        service = ExecutionsReconciliationService(order_inquiry=inquiry)  # type: ignore[arg-type]
        preview = service.preview(session, portfolio_id, "jp")
        bulk_upsert_positions(
            session,
            portfolio_id,
            __import__("yowayowa.domain", fromlist=["PositionBulkUpsert"]).PositionBulkUpsert(
                positions=[PositionUpsert(symbol="AAPL", quantity=Decimal("1"), currency="USD")]
            ),
        )
        with pytest.raises(ExecutionPreviewChangedError):
            service.apply(
                session,
                portfolio_id,
                "jp",
                preview.preview_id,
                operator_approved=True,
            )
        assert get_portfolio(session, portfolio_id).positions[0].quantity == Decimal("1")
    finally:
        session.close()
        engine.dispose()


def test_missing_execution_fields_and_currency_mismatch_block_application(tmp_path: Path) -> None:
    payload = {
        "executions": [
            {"execution_id": "missing-fields", "symbol": "AAPL", "side": "buy", "price": "12"}
        ]
    }
    parsed, notes = normalize_execution_records(payload, market="jp")
    assert parsed[0].quantity is None
    assert parsed[0].currency is None
    assert parsed[0].executed_at is None
    assert any("not treated as zero" in note for note in notes)

    engine, session, portfolio_id = _database(tmp_path)
    try:
        bulk_upsert_positions(
            session,
            portfolio_id,
            __import__("yowayowa.domain", fromlist=["PositionBulkUpsert"]).PositionBulkUpsert(
                positions=[PositionUpsert(symbol="AAPL", quantity=Decimal("5"), currency="USD")]
            ),
        )
        missing = _execution(
            execution_id="missing",
            quantity=Decimal("0"),
            currency=None,
            executed_at=None,
        )
        missing_preview = ExecutionsReconciliationService(
            order_inquiry=_FakeOrderInquiry(_outcome([missing]))  # type: ignore[arg-type]
        ).preview(session, portfolio_id, "jp")
        assert missing_preview.can_apply is False
        assert missing_preview.changes == []
        assert any("quantity must be positive" in item for item in missing_preview.blockers)
        assert any("currency missing" in item for item in missing_preview.blockers)

        mismatch_preview = ExecutionsReconciliationService(
            order_inquiry=_FakeOrderInquiry(
                _outcome([_execution(execution_id="yen-fill", currency="JPY")])
            )  # type: ignore[arg-type]
        ).preview(session, portfolio_id, "jp")
        assert mismatch_preview.can_apply is False
        assert any("currency mismatch" in item for item in mismatch_preview.blockers)
        assert get_portfolio(session, portfolio_id).positions[0].quantity == Decimal("5")
    finally:
        session.close()
        engine.dispose()


def test_unverified_or_failed_broker_read_is_preview_only(tmp_path: Path) -> None:
    engine, session, portfolio_id = _database(tmp_path)
    try:
        unverified = ExecutionsReconciliationService(
            order_inquiry=_FakeOrderInquiry(_outcome([_execution()], verified=False))  # type: ignore[arg-type]
        ).preview(session, portfolio_id, "jp")
        assert unverified.can_apply is False
        assert any("NEED-HUMAN" in item for item in unverified.blockers)

        failed = ExecutionsReconciliationService(
            order_inquiry=_FakeOrderInquiry(
                _outcome([_execution()], fetch_state=AcquisitionFetchState.FAILED)
            )  # type: ignore[arg-type]
        ).preview(session, portfolio_id, "jp")
        assert failed.can_apply is False
        assert any("fresh OK read" in item for item in failed.blockers)
    finally:
        session.close()
        engine.dispose()


def test_broker_read_normalizes_catalogued_execution_resource_without_live_access() -> None:
    entry = lookup_rakuten_resource("executions", "jp")
    assert entry is not None
    payload = {
        "executions": [
            {
                "execution_id": "fill-2026-09-29-1",
                "order_id": "order-1",
                "symbol": "AAPL",
                "side": "buy",
                "quantity": "2",
                "price": "30.25",
                "currency": "USD",
                "executed_at": "2026-09-29T03:00:00Z",
            }
        ]
    }
    acquisition_outcome = AcquisitionOutcome(
        connector_id="rakuten-web",
        resource=entry.url,
        fetch_state=AcquisitionFetchState.OK,
        auth_state=AuthState.AUTHENTICATED,
        payload=payload,
        source_url="https://broker.example/executions?private=query",
        retrieved_at=_NOW,
        as_of=_NOW,
        parser_version=entry.parser_version,
        schema_version=entry.schema_version,
    )
    broker_read = object.__new__(BrokerReadService)

    outcome = broker_read._normalize_outcome(
        acquisition_outcome,
        resource="executions",
        market="jp",
        entry=entry,
    )

    assert len(outcome.executions) == 1
    record = outcome.executions[0]
    assert record.execution_id == "fill-2026-09-29-1"
    assert record.quantity == Decimal("2")
    assert record.price == Decimal("30.25")
    assert record.currency == "USD"
    assert record.executed_at == _NOW
    assert outcome.orders[0].filled_quantity == 2


def test_order_inquiry_delegates_to_broker_read_executions_resource() -> None:
    expected = _outcome([_execution()])

    class FetchSpy:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, bool]] = []

        def fetch(
            self,
            resource: str,
            market: str,
            *,
            force_refresh: bool = False,
        ) -> BrokerReadOutcome:
            self.calls.append((resource, market, force_refresh))
            return expected

    read = FetchSpy()
    service = OrderInquiryService(broker_read=read, execution=object())  # type: ignore[arg-type]

    outcome = service.list_executions("us", force_refresh=True)

    assert outcome is expected
    assert read.calls == [("executions", "us", True)]
