from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from test_operator_bridge_app import _intent
from test_operator_bridge_dispatch import _clock

from yowayowa.config import Settings
from yowayowa.operator_bridge.app import create_operator_bridge_app
from yowayowa.operator_bridge.excel import XlwingsMacroRunner
from yowayowa.operator_bridge.rakuten import RakutenMs2RssLocalConnector
from yowayowa.operator_bridge.state import SQLiteOperatorState


@pytest.mark.parametrize("pause", ["book", "macro"])
def test_excel_preparation_rollover_is_checked_before_com_invocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pause: str
) -> None:
    current = _clock(monkeypatch)
    physical: list[str] = []

    def macro(name: str) -> Any:
        if pause == "macro":
            current[0] += timedelta(seconds=2)

        def invoke(*args: object) -> str:
            physical.append(current[0].date().isoformat())
            return "発注済み"

        return invoke

    def resolve() -> Any:
        if pause == "book":
            current[0] += timedelta(seconds=2)
        return SimpleNamespace(app=SimpleNamespace(macro=macro))

    runner = XlwingsMacroRunner()
    monkeypatch.setattr(runner, "_resolve_book", resolve)
    state = SQLiteOperatorState(tmp_path / "state.db")
    cfg = Settings(
        _env_file=None,
        mode="personal",
        broker_live_orders_enabled=True,
        broker_max_orders_per_day=1,
        broker_max_single_order_notional=1_000_000,
    )
    connector = RakutenMs2RssLocalConnector(
        runner, allocate_rss_order_id=state.allocate_rss_order_id
    )
    app = create_operator_bridge_app(
        token="bridge-secret", settings=cfg, state=state, connector=connector
    )
    client = TestClient(app)
    response = client.post(
        "/v1/brokers/rakuten/orders",
        headers={"Authorization": "Bearer bridge-secret"},
        json=_intent(),
    )
    assert response.status_code == 409
    assert physical == []
    assert state.count_submission_attempts_today() == 0
    assert state.audit_events()[0]["event_type"] == "order_submit_attempt"
