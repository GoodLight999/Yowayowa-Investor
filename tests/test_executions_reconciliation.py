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


def test_apply_rejects_lost_update_when_concurrent_update_interleaves_between_preview_and_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Astra finding: concurrent holding update between reconfirmation and save.

    If another session commits a position change after `self.preview()` reconfirms
    in `apply()` but before `apply_execution_reconciliation()` commits, the optimistic
    lock (version mismatch) must reject the save, roll back, and raise
    ExecutionPreviewChangedError. The prior update must be preserved, and the execution
    ledger must NOT record the rejected execution.
    """
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
        inquiry = _FakeOrderInquiry(
            _outcome([_execution(quantity=Decimal("2"), price=Decimal("30"))])
        )
        service = ExecutionsReconciliationService(order_inquiry=inquiry)  # type: ignore[arg-type]
        preview = service.preview(session, portfolio_id, "jp")
        assert preview.can_apply is True
        assert preview.changes[0].expected_version == 1

        import yowayowa.services.executions_reconciliation as exec_mod

        real_apply_func = exec_mod.apply_execution_reconciliation

        def interleaved_apply(*args: Any, **kwargs: Any) -> Any:
            with Session(engine, expire_on_commit=False) as concurrent_session:
                from yowayowa.domain import PositionBulkUpsert

                bulk_upsert_positions(
                    concurrent_session,
                    portfolio_id,
                    PositionBulkUpsert(
                        positions=[
                            PositionUpsert(
                                symbol="AAPL",
                                quantity=Decimal("50"),
                                average_cost=Decimal("25"),
                                currency="USD",
                                expected_version=1,
                            )
                        ]
                    ),
                )
            return real_apply_func(*args, **kwargs)

        monkeypatch.setattr(exec_mod, "apply_execution_reconciliation", interleaved_apply)

        with pytest.raises(
            ExecutionPreviewChangedError, match="local position changed after preview"
        ):
            service.apply(
                session,
                portfolio_id,
                "jp",
                preview.preview_id,
                operator_approved=True,
            )

        # Preceding concurrent update remains intact
        current_portfolio = get_portfolio(session, portfolio_id)
        assert len(current_portfolio.positions) == 1
        assert current_portfolio.positions[0].quantity == Decimal("50")
        assert current_portfolio.positions[0].version == 2

        # Rejected execution is NOT in the applied ledger
        applied_ledger = applied_execution_fingerprints(session, portfolio_id, "jp")
        assert "fill-1" not in applied_ledger
    finally:
        session.close()
        engine.dispose()


def test_concurrent_apply_prevents_duplicate_application(tmp_path: Path) -> None:
    """Applying an execution twice or retrying stale preview prevents double application."""
    engine, session, portfolio_id = _database(tmp_path)
    try:
        bulk_upsert_positions(
            session,
            portfolio_id,
            __import__("yowayowa.domain", fromlist=["PositionBulkUpsert"]).PositionBulkUpsert(
                positions=[PositionUpsert(symbol="AAPL", quantity=Decimal("10"), currency="USD")]
            ),
        )
        inquiry = _FakeOrderInquiry(_outcome([_execution()]))
        service = ExecutionsReconciliationService(order_inquiry=inquiry)  # type: ignore[arg-type]
        preview = service.preview(session, portfolio_id, "jp")
        assert preview.can_apply is True

        res = service.apply(session, portfolio_id, "jp", preview.preview_id, operator_approved=True)
        assert res.applied_execution_ids == ["fill-1"]
        assert get_portfolio(session, portfolio_id).positions[0].quantity == Decimal("12")
        assert get_portfolio(session, portfolio_id).positions[0].version == 2

        with pytest.raises((ExecutionPreviewChangedError, ValueError)):
            service.apply(session, portfolio_id, "jp", preview.preview_id, operator_approved=True)

        assert get_portfolio(session, portfolio_id).positions[0].quantity == Decimal("12")
        assert get_portfolio(session, portfolio_id).positions[0].version == 2
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


# --- D2 supplementary coverage (CTO integration) ---
def test_changed_broker_row_after_application_blocks_and_keeps_holdings(tmp_path: Path) -> None:
    """D2: a previously applied execution whose broker row changed must block.

    The ledger stores the fingerprint of the applied row; when the broker later
    reports a different price for the same execution id the preview must refuse
    to apply (``changed_after_application``) and must not touch holdings.
    """
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
        applied = _execution(execution_id="fill-1", price=Decimal("30"))
        service = ExecutionsReconciliationService(
            order_inquiry=_FakeOrderInquiry(_outcome([applied]))  # type: ignore[arg-type]
        )
        preview = service.preview(session, portfolio_id, "jp")
        assert preview.can_apply is True
        result = service.apply(
            session, portfolio_id, "jp", preview.preview_id, operator_approved=True
        )
        assert result.applied_execution_ids == ["fill-1"]
        assert get_portfolio(session, portfolio_id).positions[0].quantity == Decimal("12")

        # Same execution id, different price -> the broker row changed.
        changed = _execution(execution_id="fill-1", price=Decimal("31"))
        changed_service = ExecutionsReconciliationService(
            order_inquiry=_FakeOrderInquiry(_outcome([changed]))  # type: ignore[arg-type]
        )
        changed_preview = changed_service.preview(session, portfolio_id, "jp")

        assert changed_preview.can_apply is False
        assert changed_preview.executions[0].state == "changed_after_application"
        assert any(
            "previously applied execution changed" in item for item in changed_preview.blockers
        )
        assert any(
            "differs from the previously applied" in item
            for item in changed_preview.executions[0].issues
        )
        # Holdings and the ledger are untouched by a rejected preview.
        assert get_portfolio(session, portfolio_id).positions[0].quantity == Decimal("12")
        assert (
            applied_execution_fingerprints(session, portfolio_id, "jp")["fill-1"]
            != changed_preview.executions[0].fingerprint
        )
        # The change is also detected at apply time: the preview id moved and the
        # changed row is not appliable.
        with pytest.raises(ValueError, match="not applicable"):
            changed_service.apply(
                session,
                portfolio_id,
                "jp",
                changed_preview.preview_id,
                operator_approved=True,
            )
        assert get_portfolio(session, portfolio_id).positions[0].quantity == Decimal("12")
    finally:
        session.close()
        engine.dispose()


def test_stale_broker_fetch_is_preview_only_and_never_appliable(tmp_path: Path) -> None:
    """D2: AcquisitionFetchState.STALE must stay preview-only.

    A stale read may be projected for operator awareness but can never be
    applied, even with explicit approval, and the note/blocker must say so.
    """
    engine, session, portfolio_id = _database(tmp_path)
    try:
        stale = ExecutionsReconciliationService(
            order_inquiry=_FakeOrderInquiry(
                _outcome([_execution()], fetch_state=AcquisitionFetchState.STALE)
            )  # type: ignore[arg-type]
        )
        preview = stale.preview(session, portfolio_id, "jp")

        assert preview.fetch_state == AcquisitionFetchState.STALE
        assert preview.can_apply is False
        assert any("stale broker data is preview-only" in note for note in preview.notes)
        assert any("only a fresh OK read can apply" in item for item in preview.blockers)
        assert preview.changes and preview.changes[0].current_quantity == Decimal("0")
        assert preview.changes[0].target_quantity == Decimal("2")

        with pytest.raises(ValueError, match="not applicable"):
            stale.apply(session, portfolio_id, "jp", preview.preview_id, operator_approved=True)
        assert get_portfolio(session, portfolio_id).positions == []
    finally:
        session.close()
        engine.dispose()


def test_sell_and_short_fills_project_the_weighted_average_cost(tmp_path: Path) -> None:
    """D2: SELL-side fills across long, short-covering and short-extending paths.

    AAPL long 10 @ 20    + SELL 2 @ 25  ->  8 @ 20   (basis retained on a sale)
    MSFT short -4 @ 50   + BUY  1 @ 44  -> -3 @ 50   (cover retains the basis)
    TSLA short -4 @ 50   + SELL 2 @ 40  -> -6 @ 46.66... (short extended -> weighted)
    """
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
                    ),
                    PositionUpsert(
                        symbol="MSFT",
                        quantity=Decimal("-4"),
                        average_cost=Decimal("50"),
                        currency="USD",
                    ),
                    PositionUpsert(
                        symbol="TSLA",
                        quantity=Decimal("-4"),
                        average_cost=Decimal("50"),
                        currency="USD",
                    ),
                ]
            ),
        )
        sell = _execution(
            execution_id="sell-1",
            symbol="AAPL",
            side=BrokerOrderSide.SELL,
            quantity=Decimal("2"),
            price=Decimal("25"),
        )
        cover = _execution(
            execution_id="cover-1",
            symbol="MSFT",
            side=BrokerOrderSide.BUY,
            quantity=Decimal("1"),
            price=Decimal("44"),
        )
        extend_short = _execution(
            execution_id="short-1",
            symbol="TSLA",
            side=BrokerOrderSide.SELL,
            quantity=Decimal("2"),
            price=Decimal("40"),
        )
        service = ExecutionsReconciliationService(
            order_inquiry=_FakeOrderInquiry(
                _outcome([sell, cover, extend_short])  # type: ignore[arg-type]
            )
        )
        preview = service.preview(session, portfolio_id, "jp")
        assert preview.can_apply is True
        by_symbol = {change.symbol: change for change in preview.changes}

        assert by_symbol["AAPL"].current_quantity == Decimal("10")
        assert by_symbol["AAPL"].target_quantity == Decimal("8")
        assert by_symbol["AAPL"].target_average_cost == Decimal("20")
        # Covering part of a short keeps the original basis.
        assert by_symbol["MSFT"].current_quantity == Decimal("-4")
        assert by_symbol["MSFT"].target_quantity == Decimal("-3")
        assert by_symbol["MSFT"].target_average_cost == Decimal("50")
        # Extending a short re-averages: (4*50 + 2*40) / 6.
        assert by_symbol["TSLA"].current_quantity == Decimal("-4")
        assert by_symbol["TSLA"].target_quantity == Decimal("-6")
        # The projection keeps full Decimal precision in the preview; persistence
        # stores average_cost as Numeric(28, 10) (see PositionRecord), so the
        # applied row is the same value rounded to the column scale.
        assert by_symbol["TSLA"].target_average_cost == Decimal("46.66666666666666666666666667")

        result = service.apply(
            session, portfolio_id, "jp", preview.preview_id, operator_approved=True
        )
        positions = {position.symbol: position for position in result.portfolio.positions}
        assert (positions["AAPL"].quantity, positions["AAPL"].average_cost) == (
            Decimal("8"),
            Decimal("20"),
        )
        assert (positions["MSFT"].quantity, positions["MSFT"].average_cost) == (
            Decimal("-3"),
            Decimal("50"),
        )
        assert (positions["TSLA"].quantity, positions["TSLA"].average_cost) == (
            Decimal("-6"),
            Decimal("46.6666666667"),
        )
        assert positions["TSLA"].average_cost == by_symbol["TSLA"].target_average_cost.quantize(
            Decimal("0.0000000001")
        )
        assert set(applied_execution_fingerprints(session, portfolio_id, "jp")) == {
            "sell-1",
            "cover-1",
            "short-1",
        }
    finally:
        session.close()
        engine.dispose()
