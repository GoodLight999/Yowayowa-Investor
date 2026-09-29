from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from yowayowa.acquisition.models import AcquisitionFetchState, AuthState, SnapshotRecord
from yowayowa.api.deps import db_session, get_executions_reconciliation_service
from yowayowa.broker_models import BrokerExecution, BrokerOrderSide
from yowayowa.db import Base
from yowayowa.services.broker_read_service import BrokerReadOutcome
from yowayowa.services.executions_reconciliation import ExecutionsReconciliationService
from yowayowa.services.portfolios import create_portfolio, get_portfolio


class _FakeOrderInquiry:
    def __init__(self, outcome: BrokerReadOutcome) -> None:
        self.outcome = outcome

    def list_executions(self, market: str, force_refresh: bool = False) -> BrokerReadOutcome:
        assert market == "jp"
        return self.outcome


def test_preview_api_is_read_only_and_apply_requires_explicit_approval(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    now = datetime(2026, 9, 29, 3, 0, tzinfo=UTC)
    execution = BrokerExecution(
        broker="rakuten-securities",
        execution_id="fill-api-1",
        broker_order_id="order-api-1",
        symbol="AAPL",
        side=BrokerOrderSide.BUY,
        quantity=Decimal("3"),
        price=Decimal("10"),
        currency="USD",
        executed_at=now,
        executed_at_raw=now.isoformat(),
    )
    outcome = BrokerReadOutcome(
        connector_id="rakuten-web",
        resource="executions",
        market="jp",
        fetch_state=AcquisitionFetchState.OK,
        auth_state=AuthState.AUTHENTICATED,
        executions=[execution],
        detail={"verified": True},
        source_url="https://broker.example/executions?private=query",
        retrieved_at=now.isoformat(),
        as_of=now.isoformat(),
        parser_version="fake-v1",
        schema_version="fake-v1",
        snapshot=SnapshotRecord(
            snapshot_id="snapshot-api",
            connector_id="rakuten-web",
            resource="executions",
            captured_at=now,
            parser_version="fake-v1",
            schema_version="fake-v1",
            fetch_state=AcquisitionFetchState.OK,
            payload_sha256="b" * 64,
            as_of=now,
        ),
    )
    service = ExecutionsReconciliationService(order_inquiry=_FakeOrderInquiry(outcome))  # type: ignore[arg-type]

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'api-lifespan.sqlite'}")
    monkeypatch.setenv("YOWAYOWA_MODE", "personal")
    monkeypatch.setenv("YOWAYOWA_PRIVATE_CONNECTORS_ENABLED", "true")
    monkeypatch.delenv("YOWAYOWA_API_TOKEN", raising=False)
    from yowayowa.config import get_settings

    get_settings.cache_clear()
    engine = create_engine(f"sqlite:///{tmp_path / 'api.sqlite'}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        portfolio = create_portfolio(session, "API reconciliation", "USD")
        portfolio_id = portfolio.id

    def session_override() -> Iterator[Session]:
        with Session(engine, expire_on_commit=False) as session:
            yield session

    from yowayowa.api.app import app

    app.dependency_overrides[db_session] = session_override
    app.dependency_overrides[get_executions_reconciliation_service] = lambda: service
    try:
        with TestClient(app) as client:
            preview_response = client.get(
                "/v1/broker-execution/reconciliation/preview",
                params={"portfolio_id": portfolio_id, "market": "jp"},
            )
            assert preview_response.status_code == 200, preview_response.text
            preview = preview_response.json()
            assert preview["can_apply"] is True
            assert preview["source_url"] == "https://broker.example/executions"
            assert preview["snapshot_payload_sha256"] == "b" * 64
            assert preview["changes"][0]["target_quantity"] == "3"
            with Session(engine, expire_on_commit=False) as session:
                assert get_portfolio(session, portfolio_id).positions == []

            refused = client.post(
                "/v1/broker-execution/reconciliation/apply",
                json={
                    "portfolio_id": portfolio_id,
                    "market": "jp",
                    "preview_id": preview["preview_id"],
                    "operator_approved": False,
                },
            )
            assert refused.status_code == 409
            with Session(engine, expire_on_commit=False) as session:
                assert get_portfolio(session, portfolio_id).positions == []

            applied = client.post(
                "/v1/broker-execution/reconciliation/apply",
                json={
                    "portfolio_id": portfolio_id,
                    "market": "jp",
                    "preview_id": preview["preview_id"],
                    "operator_approved": True,
                },
            )
            assert applied.status_code == 200, applied.text
            assert applied.json()["applied_execution_ids"] == ["fill-api-1"]
            with Session(engine, expire_on_commit=False) as session:
                assert get_portfolio(session, portfolio_id).positions[0].quantity == Decimal("3")
    finally:
        app.dependency_overrides.pop(db_session, None)
        app.dependency_overrides.pop(get_executions_reconciliation_service, None)
        get_settings.cache_clear()
        engine.dispose()
