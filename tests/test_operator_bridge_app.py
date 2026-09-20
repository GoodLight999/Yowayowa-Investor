from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient

from yowayowa.broker_models import BrokerOrderIntent, BrokerOrderSide, BrokerOrderType
from yowayowa.config import Settings
from yowayowa.operator_bridge.app import create_operator_bridge_app
from yowayowa.operator_bridge.rakuten import RakutenMs2RssLocalConnector
from yowayowa.operator_bridge.state import SQLiteOperatorState


class FakeMacroRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def run_macro(self, name: str, args: Sequence[object]) -> object:
        self.calls.append((name, tuple(args)))
        return "発注済み"


def _intent() -> dict[str, object]:
    return {
        "client_order_id": "bridge-test-1",
        "symbol": "4755.T",
        "side": "buy",
        "quantity": 100,
        "order_type": "limit",
        "limit_price": "900",
        "currency": "JPY",
    }


def _app(
    tmp_path: Path,
    settings: Settings,
) -> tuple[TestClient, FakeMacroRunner, SQLiteOperatorState]:
    state = SQLiteOperatorState(tmp_path / "operator.db")
    runner = FakeMacroRunner()
    connector = RakutenMs2RssLocalConnector(
        runner,
        allocate_rss_order_id=state.allocate_rss_order_id,
    )
    app = create_operator_bridge_app(
        token="bridge-secret",
        settings=settings,
        state=state,
        connector=connector,
    )
    return TestClient(app), runner, state


def test_operator_bridge_requires_token(tmp_path: Path) -> None:
    client, _, _ = _app(tmp_path, Settings(mode="personal"))

    response = client.get("/health")

    assert response.status_code == 401


def test_operator_bridge_previews_without_live_arm(tmp_path: Path) -> None:
    client, runner, _ = _app(tmp_path, Settings(mode="personal"))

    response = client.post(
        "/v1/brokers/rakuten/orders/preview",
        headers={"Authorization": "Bearer bridge-secret"},
        json=_intent(),
    )

    assert response.status_code == 200
    assert response.json()["estimated_notional"] == "90000"
    assert runner.calls == []


def test_operator_bridge_blocks_unarmed_live_submission(tmp_path: Path) -> None:
    client, runner, state = _app(tmp_path, Settings(mode="personal"))

    response = client.post(
        "/v1/brokers/rakuten/orders",
        headers={"Authorization": "Bearer bridge-secret"},
        json=_intent(),
    )

    assert response.status_code == 409
    assert runner.calls == []
    assert state.count_submission_attempts_today() == 0


def test_operator_bridge_submits_and_audits_armed_order(tmp_path: Path) -> None:
    settings = Settings(
        mode="personal",
        broker_live_orders_enabled=True,
        broker_max_single_order_notional=Decimal("100000"),
        broker_max_orders_per_day=5,
    )
    client, runner, state = _app(tmp_path, settings)

    response = client.post(
        "/v1/brokers/rakuten/orders",
        headers={"Authorization": "Bearer bridge-secret"},
        json=_intent(),
    )

    assert response.status_code == 200
    assert response.json()["accepted"] is True
    assert response.json()["broker_order_id"] == "1"
    assert len(runner.calls) == 1
    assert state.count_submission_attempts_today() == 1
    events = state.audit_events()
    assert events[0]["event_type"] == "order_submit_attempt"
    assert events[1]["event_type"] == "order_submit_result"


def test_operator_bridge_retry_reuses_same_rss_order_id(tmp_path: Path) -> None:
    settings = Settings(
        mode="personal",
        broker_live_orders_enabled=True,
        broker_max_single_order_notional=Decimal("100000"),
        broker_max_orders_per_day=5,
    )
    client, runner, state = _app(tmp_path, settings)
    headers = {"Authorization": "Bearer bridge-secret"}

    first = client.post("/v1/brokers/rakuten/orders", headers=headers, json=_intent())
    second = client.post("/v1/brokers/rakuten/orders", headers=headers, json=_intent())

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["broker_order_id"] == "1"
    assert second.json()["broker_order_id"] == "1"
    assert runner.calls[0][1][0] == 1
    assert len(runner.calls) == 1
    assert state.count_submission_attempts_today() == 1
