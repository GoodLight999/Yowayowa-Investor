from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from starlette.testclient import TestClient

from yowayowa.acquisition.models import AcquisitionFetchState, AuthState
from yowayowa.api.deps import get_order_inquiry_service
from yowayowa.broker_models import BrokerExecution, BrokerOrderSide
from yowayowa.services.broker_read_service import BrokerReadOutcome


class _FakeOrderInquiry:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bool]] = []
        now = datetime(2026, 9, 29, 3, 0, tzinfo=UTC)
        self.outcome = BrokerReadOutcome(
            connector_id="rakuten-web",
            resource="executions",
            market="us",
            fetch_state=AcquisitionFetchState.OK,
            auth_state=AuthState.AUTHENTICATED,
            executions=[
                BrokerExecution(
                    broker="rakuten-securities",
                    execution_id="fill-read-only-1",
                    broker_order_id="order-read-only-1",
                    symbol="AAPL",
                    side=BrokerOrderSide.BUY,
                    quantity=Decimal("2"),
                    price=Decimal("190.25"),
                    currency="USD",
                    executed_at=now,
                    executed_at_raw=now.isoformat(),
                )
            ],
            source_url="https://broker.example/executions",
            retrieved_at=now.isoformat(),
            as_of=now.isoformat(),
            parser_version="fake-parser-v1",
            schema_version="fake-schema-v1",
            detail={"verified": False},
        )

    def list_executions(self, market: str, force_refresh: bool = False) -> BrokerReadOutcome:
        self.calls.append((market, force_refresh))
        return self.outcome.model_copy(update={"market": market})


def test_executions_endpoint_is_get_only_and_returns_provenance(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'broker-executions.sqlite'}")
    monkeypatch.setenv("YOWAYOWA_MODE", "personal")
    monkeypatch.setenv("YOWAYOWA_PRIVATE_CONNECTORS_ENABLED", "true")
    monkeypatch.setenv("YOWAYOWA_PRIVATE_ACQUISITION_DATA_DIR", str(tmp_path / "private-data"))
    monkeypatch.setenv("YOWAYOWA_BROKER_RAKUTEN_WEB_PROFILE_DIR", str(tmp_path / "browser-profile"))
    monkeypatch.delenv("YOWAYOWA_API_TOKEN", raising=False)

    from yowayowa.config import get_settings

    get_settings.cache_clear()
    service = _FakeOrderInquiry()
    from yowayowa.api.app import app

    app.dependency_overrides[get_order_inquiry_service] = lambda: service  # type: ignore[assignment]
    try:
        with TestClient(app) as client:
            page = client.get("/broker-execution?lang=en")
            assert page.status_code == 200, page.text
            assert "Order status" in page.text
            asset_match = re.search(r'src="([^"]+/broker_execution\.js)"', page.text)
            assert asset_match is not None
            asset = client.get(asset_match.group(1))
            assert asset.status_code == 200
            assert "/v1/broker-execution/executions" in asset.text

            response = client.get(
                "/v1/broker-execution/executions",
                params={"market": "us", "force_refresh": "true"},
            )
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["resource"] == "executions"
            assert body["market"] == "us"
            assert body["fetch_state"] == "ok"
            assert body["auth_state"] == "authenticated"
            assert body["executions"][0]["price"] == "190.25"
            assert body["executions"][0]["currency"] == "USD"
            assert body["as_of"].startswith("2026-09-29T03:00:00")
            assert body["source_url"] == "https://broker.example/executions"
            assert service.calls == [("us", True)]

            write_method = client.post("/v1/broker-execution/executions", json={})
            assert write_method.status_code == 405
            assert service.calls == [("us", True)]
    finally:
        app.dependency_overrides.pop(get_order_inquiry_service, None)
        get_settings.cache_clear()
