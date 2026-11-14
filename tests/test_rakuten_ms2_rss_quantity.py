# ruff: noqa: RUF001  # full-width digit samples are broker source data, not typos
"""MS2 RSS quantity parsing: unreadable input is unknown, never zero.

Audit reference: docs/SILENT_FAILURE_AUDIT_20261023.md §3 #8.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from yowayowa.acquisition.models import (
    AcquisitionFetchState,
    AuthState,
    SnapshotRecord,
)
from yowayowa.broker_models import (
    RAKUTEN_SECURITIES_BROKER,
    BrokerExecution,
    BrokerOrder,
    BrokerOrderSide,
    BrokerOrderStatus,
    BrokerPosition,
)
from yowayowa.config import Settings
from yowayowa.db import Base
from yowayowa.operator_bridge.app import create_operator_bridge_app
from yowayowa.operator_bridge.rakuten import RakutenMs2RssLocalConnector
from yowayowa.operator_bridge.state import SQLiteOperatorState
from yowayowa.providers.rakuten_ms2_rss import (
    RakutenRssInquiry,
    _filled_quantity,
    _order_quantity,
    _to_int,
    parse_order_text_status,
)
from yowayowa.services.broker_read_service import BrokerReadOutcome
from yowayowa.services.executions_reconciliation import (
    ExecutionsReconciliationService,
)
from yowayowa.services.portfolios import create_portfolio

H_ORDER = "注文番号"
H_STATUS = "通常注文状況"
H_SYMBOL = "銘柄コード"
H_SIDE = "売買"
H_QTY = "注文数量"
H_FILLED = "約定数量"
_POSITION_HEADERS: list[object] = [
    "銘柄コード",
    "口座区分",
    "保有数量",
    "平均取得価額",
    "時価",
    "時価評価額",
    "評価損益額",
]
_NOW = datetime(2026, 10, 23, 3, 0, tzinfo=UTC)


class DictReader:
    def __init__(self, scalars: dict[str, object], tables: dict[str, list[list[object]]]):
        self.scalars = scalars
        self.tables = tables

    def read_scalar_formula(self, formula: str) -> object:
        return self.scalars[formula]

    def read_table_formula(self, formula: str) -> list[list[object]]:
        return self.tables.get(formula, [])


def _order_table(*rows: list[object]) -> list[list[object]]:
    return [[H_ORDER, H_STATUS, H_SYMBOL, H_SIDE, H_QTY, H_FILLED], *rows]


def _inquiry(*rows: list[object]) -> RakutenRssInquiry:
    return RakutenRssInquiry(DictReader({}, {"RssOrderList()": _order_table(*rows)}))


def _position_inquiry(*rows: list[object]) -> RakutenRssInquiry:
    return RakutenRssInquiry(DictReader({}, {"RssPositionList()": [_POSITION_HEADERS, *rows]}))


# --------------------------------------------------------------- parse boundaries


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (100, 100),
        (0, 0),
        (-100, -100),
        (100.0, 100),
        ("100", 100),
        (" 1,000 ", 1000),
        ("1,000,000", 1_000_000),
        ("+100", 100),
        ("-100", -100),
        (None, None),
        (True, None),
        (False, None),
        ("", None),
        ("   ", None),
        ("１００", 100),  # full-width digits are the same number (NFKC)
        ("１，０００", 1000),  # full-width digits + full-width separator
        ("1,000株", None),  # unit suffix is not silently stripped
        ("100.5", None),
        (100.5, None),
        ("1_000", None),
        ("N/A", None),
    ],
)
def test_to_int_returns_none_for_unreadable_cells(raw: object, expected: int | None) -> None:
    assert _to_int(raw) == expected


def test_order_quantity_requires_a_positive_integer() -> None:
    assert _order_quantity("1,000") == 1000
    assert _order_quantity(0) is None
    assert _order_quantity(-100) is None
    assert _order_quantity("1,000株") is None
    assert _order_quantity(None) is None


def test_filled_quantity_keeps_zero_but_not_unreadable_input() -> None:
    assert _filled_quantity(0) == 0
    assert _filled_quantity("0") == 0
    assert _filled_quantity("40") == 40
    assert _filled_quantity("-5") is None
    assert _filled_quantity("") is None


# ------------------------------------------------------------- order normalization


def test_unreadable_order_quantity_is_unknown_not_zero() -> None:
    orders = _inquiry([123456, "出来有", "4755", "買付", "1,000株", "40"]).list_orders()

    assert len(orders) == 1
    assert orders[0].quantity is None
    assert orders[0].filled_quantity == 40
    assert orders[0].quantity != 0


def test_unreadable_filled_quantity_is_unknown_not_zero() -> None:
    orders = _inquiry([123456, "出来有", "4755", "買付", 100, "４０株"]).list_orders()

    assert orders[0].quantity == 100
    assert orders[0].filled_quantity is None


def test_full_width_quantities_are_read_as_numbers_not_unknown() -> None:
    orders = _inquiry([123456, "出来有", "4755", "買付", "１００", "４０"]).list_orders()

    assert orders[0].quantity == 100
    assert orders[0].filled_quantity == 40
    assert orders[0].status is BrokerOrderStatus.PARTIALLY_FILLED


def test_zero_order_quantity_is_unknown_while_zero_fill_stays_zero() -> None:
    orders = _inquiry(
        [123456, "執行中", "4755", "買付", 0, 0],
    ).list_orders()

    assert orders[0].quantity is None
    assert orders[0].filled_quantity == 0


def test_partial_fill_status_is_not_guessed_from_unknown_counts() -> None:
    orders = _inquiry(
        [123456, "出来有", "4755", "買付", "1,000株", "40"],
        [123457, "出来有", "6758", "買付", 100, 40],
    ).list_orders()

    unknown, known = orders
    assert unknown.status is BrokerOrderStatus.UNKNOWN
    assert known.status is BrokerOrderStatus.PARTIALLY_FILLED


def test_status_text_mapping_still_works_with_known_counts() -> None:
    assert (
        parse_order_text_status("約定", filled_quantity=100, quantity=100)
        is BrokerOrderStatus.FILLED
    )
    assert (
        parse_order_text_status("出来有", filled_quantity=None, quantity=None)
        is BrokerOrderStatus.UNKNOWN
    )
    assert (
        parse_order_text_status("出来有", filled_quantity=0, quantity=None)
        is BrokerOrderStatus.UNKNOWN
    )


# ---------------------------------------------------------- position normalization


def test_unreadable_position_quantity_is_none_not_zero_decimal() -> None:
    positions = _position_inquiry(
        ["4755", "特定", "1,000株", 850.5, 900, 90000, 4950],
        ["6758", "特定", 100, 3000, 3100, 310000, 10000],
    ).list_positions()

    unknown, known = positions
    assert unknown.symbol == "4755"
    assert unknown.quantity is None
    assert unknown.quantity != Decimal(0)
    assert known.quantity == Decimal("100")


def test_negative_position_quantity_is_preserved() -> None:
    positions = _position_inquiry(["4755", "信用", -100, 800, 900, -90000, -4950]).list_positions()

    assert positions[0].symbol == "4755"
    assert positions[0].quantity == Decimal("-100")


# ------------------------------------------------------------- model / wire contract


def test_unknown_quantities_serialize_to_null_not_zero() -> None:
    order = BrokerOrder(
        broker=RAKUTEN_SECURITIES_BROKER,
        broker_order_id="123456",
        symbol="4755",
        side=BrokerOrderSide.BUY,
        quantity=None,
        filled_quantity=None,
        status=BrokerOrderStatus.UNKNOWN,
    )
    position = BrokerPosition(broker=RAKUTEN_SECURITIES_BROKER, symbol="4755", quantity=None)

    order_payload = order.model_dump(mode="json")
    position_payload = position.model_dump(mode="json")

    assert order_payload["quantity"] is None
    assert order_payload["filled_quantity"] is None
    assert position_payload["quantity"] is None
    assert "quantity" in order_payload and "quantity" in position_payload


def test_required_quantity_fields_are_not_dropped_from_the_contract() -> None:
    with pytest.raises(ValidationError):
        BrokerOrder(  # type: ignore[call-arg]
            broker=RAKUTEN_SECURITIES_BROKER,
            broker_order_id="123456",
            symbol="4755",
            side=BrokerOrderSide.BUY,
            status=BrokerOrderStatus.PENDING,
        )
    with pytest.raises(ValidationError):
        BrokerPosition(broker=RAKUTEN_SECURITIES_BROKER, symbol="4755")  # type: ignore[call-arg]


def test_connector_passes_unknown_quantities_through_unchanged() -> None:
    class _Runner:
        def read_scalar_formula(self, formula: str) -> object:
            raise AssertionError("no scalar read expected")

        def read_table_formula(self, formula: str) -> list[list[object]]:
            if formula == "RssOrderList()":
                return _order_table([123456, "執行中", "4755", "買付", "", ""])
            if formula == "RssPositionList()":
                return [_POSITION_HEADERS, ["4755", "特定", "不明", None, None, None, None]]
            return []

    connector = RakutenMs2RssLocalConnector(
        macro_runner=type("_Macro", (), {"run_macro": lambda self, name, args: None})(),
        allocate_rss_order_id=lambda _: 1,
        worksheet_runner=_Runner(),
    )

    orders = connector.list_orders()
    positions = connector.list_positions()

    assert orders[0].quantity is None and orders[0].filled_quantity is None
    assert positions[0].quantity is None


# ------------------------------------------------------- operator bridge response


class _BridgeRunner:
    def read_scalar_formula(self, formula: str) -> object:
        raise AssertionError("no scalar read expected")

    def read_table_formula(self, formula: str) -> list[list[object]]:
        if formula == "RssOrderList()":
            return _order_table([123456, "執行中", "4755", "買付", "", ""])
        if formula == "RssPositionList()":
            return [_POSITION_HEADERS, ["4755", "特定", "不明", None, None, None, None]]
        return []

    def run_macro(self, name: str, args: object) -> object:
        raise AssertionError("the bridge must not submit orders in this test")


def test_operator_bridge_returns_null_for_unknown_quantities(tmp_path: Path) -> None:
    connector = RakutenMs2RssLocalConnector(
        _BridgeRunner(),
        allocate_rss_order_id=lambda _: 1,
        worksheet_runner=_BridgeRunner(),
    )
    state = SQLiteOperatorState(tmp_path / "operator-state.sqlite")
    app = create_operator_bridge_app(
        token="bridge-token",
        settings=Settings(database_url="sqlite:///:memory:", mode="personal"),
        state=state,
        connector=connector,
    )
    headers = {"Authorization": "Bearer bridge-token"}

    with TestClient(app) as client:
        orders = client.get("/v1/brokers/rakuten/orders", headers=headers)
        positions = client.get("/v1/brokers/rakuten/positions", headers=headers)

    assert orders.status_code == 200
    assert orders.json()[0]["quantity"] is None
    assert orders.json()[0]["filled_quantity"] is None
    assert positions.status_code == 200
    assert positions.json()[0]["quantity"] is None


# ------------------------------------------------- reconciliation keeps them distinct


def _execution(quantity: Decimal | None) -> BrokerExecution:
    return BrokerExecution(
        broker=RAKUTEN_SECURITIES_BROKER,
        execution_id="fill-1",
        broker_order_id="order-1",
        symbol="4755",
        side=BrokerOrderSide.BUY,
        quantity=quantity,
        price=Decimal("900"),
        currency="JPY",
        executed_at=_NOW,
        executed_at_raw=_NOW.isoformat(),
    )


def _outcome(execution: BrokerExecution) -> BrokerReadOutcome:
    return BrokerReadOutcome(
        connector_id="rakuten-web",
        resource="executions",
        market="jp",
        fetch_state=AcquisitionFetchState.OK,
        auth_state=AuthState.AUTHENTICATED,
        executions=[execution],
        detail={"verified": True},
        source_url="https://broker.example/executions",
        retrieved_at=_NOW.isoformat(),
        as_of=_NOW.isoformat(),
        parser_version="fake-v1",
        schema_version="fake-v1",
        snapshot=SnapshotRecord(
            snapshot_id="snapshot-1",
            connector_id="rakuten-web",
            resource="executions",
            captured_at=_NOW,
            parser_version="fake-v1",
            schema_version="fake-v1",
            fetch_state=AcquisitionFetchState.OK,
            payload_sha256="a" * 64,
            as_of=_NOW,
        ),
    )


class _FakeOrderInquiry:
    def __init__(self, outcome: BrokerReadOutcome) -> None:
        self.outcome = outcome

    def list_executions(self, market: str, force_refresh: bool = False) -> BrokerReadOutcome:
        return self.outcome


def _database(tmp_path: Path) -> tuple[Any, Session, int]:
    engine = create_engine(f"sqlite:///{tmp_path / 'ms2-quantity.sqlite'}")
    Base.metadata.create_all(engine)
    session = Session(engine, expire_on_commit=False)
    portfolio = create_portfolio(session, "MS2 quantity test", "JPY")
    return engine, session, portfolio.id


def _preview(tmp_path: Path, execution: BrokerExecution):
    engine, session, portfolio_id = _database(tmp_path)
    try:
        service = ExecutionsReconciliationService(
            order_inquiry=_FakeOrderInquiry(_outcome(execution))  # type: ignore[arg-type]
        )
        return service.preview(session, portfolio_id, "jp")
    finally:
        session.close()
        engine.dispose()


def test_unknown_execution_quantity_is_not_projected_as_zero(tmp_path: Path) -> None:
    preview = _preview(tmp_path, _execution(None))

    assert preview.changes == []
    assert preview.can_apply is False
    assert "quantity missing; value remains missing" in " ".join(preview.blockers)
    assert preview.executions[0].state == "incomplete"


def test_zero_execution_quantity_is_reported_differently_from_unknown(tmp_path: Path) -> None:
    preview = _preview(tmp_path, _execution(Decimal("0")))

    joined = " ".join(preview.blockers)
    assert preview.changes == []
    assert "quantity must be positive; value is not coerced" in joined
    assert "quantity missing; value remains missing" not in joined
