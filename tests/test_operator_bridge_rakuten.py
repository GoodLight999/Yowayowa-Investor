from decimal import Decimal

from yowayowa.broker_models import (
    BrokerOrderIntent,
    BrokerOrderSide,
    BrokerOrderStatus,
    BrokerOrderType,
)
from yowayowa.operator_bridge.rakuten import RakutenMs2RssLocalConnector
from yowayowa.providers.rakuten_ms2_rss import RSS_STOCK_ORDER_V_FUNCTION


class FakeMacroRunner:
    def __init__(self, result: object = "発注済み(発注ID=42)") -> None:
        self.result = result
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def run_macro(self, name: str, args) -> object:  # type: ignore[no-untyped-def]
        self.calls.append((name, tuple(args)))
        return self.result


def _intent() -> BrokerOrderIntent:
    return BrokerOrderIntent(
        client_order_id="rk-live-1",
        symbol="4755.T",
        side=BrokerOrderSide.BUY,
        quantity=100,
        order_type=BrokerOrderType.LIMIT,
        limit_price=Decimal("900"),
        currency="JPY",
    )


def test_local_rakuten_connector_calls_vba_order_function() -> None:
    runner = FakeMacroRunner()
    connector = RakutenMs2RssLocalConnector(
        runner,
        next_rss_order_id=lambda: 42,
    )

    receipt = connector.submit_order(_intent())

    assert len(runner.calls) == 1
    name, args = runner.calls[0]
    assert name == RSS_STOCK_ORDER_V_FUNCTION
    assert len(args) == 19
    assert args[0] == 42
    assert args[1] == "4755.T"
    assert receipt.accepted is True
    assert receipt.status == BrokerOrderStatus.ACCEPTED
    assert receipt.broker_order_id == "42"


def test_local_rakuten_connector_treats_rss_error_as_rejected() -> None:
    runner = FakeMacroRunner("入力エラー: 注文数量")
    connector = RakutenMs2RssLocalConnector(
        runner,
        next_rss_order_id=lambda: 43,
    )

    receipt = connector.submit_order(_intent())

    assert receipt.accepted is False
    assert receipt.status == BrokerOrderStatus.REJECTED
