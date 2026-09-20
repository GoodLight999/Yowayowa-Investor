from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from yowayowa.broker_models import (
    BrokerAccountSnapshot,
    BrokerCapabilities,
    BrokerOrder,
    BrokerOrderIntent,
    BrokerOrderPreview,
    BrokerOrderReceipt,
    BrokerOrderStatus,
    BrokerTransport,
)
from yowayowa.operator_bridge.excel import MacroRunner
from yowayowa.providers.rakuten_ms2_rss import (
    RSS_STOCK_ORDER_V_FUNCTION,
    build_cash_stock_order_v_args,
    preview_cash_stock_order,
)


class OrderReader(Protocol):
    def list_orders(self) -> list[BrokerOrder]: ...


class RakutenMs2RssLocalConnector:
    capabilities = BrokerCapabilities(
        broker="rakuten-securities",
        transport=BrokerTransport.LOCAL_PROGRAMMABLE_INTERFACE,
        account_snapshot=False,
        positions=False,
        orders=True,
        order_submission=True,
        order_cancel=False,
        quotes=False,
        scraping=False,
    )

    def __init__(
        self,
        macro_runner: MacroRunner,
        *,
        allocate_rss_order_id: Callable[[str], int],
        order_reader: OrderReader | None = None,
    ) -> None:
        self._macro_runner = macro_runner
        self._allocate_rss_order_id = allocate_rss_order_id
        self._order_reader = order_reader

    def preview_order(self, intent: BrokerOrderIntent) -> BrokerOrderPreview:
        return preview_cash_stock_order(intent)

    def submit_order(self, intent: BrokerOrderIntent) -> BrokerOrderReceipt:
        rss_order_id = self._allocate_rss_order_id(intent.client_order_id)
        args = build_cash_stock_order_v_args(intent, rss_order_id=rss_order_id)
        raw = self._macro_runner.run_macro(RSS_STOCK_ORDER_V_FUNCTION, args)
        message = None if raw is None else str(raw)
        accepted = not (
            raw is False
            or raw is None
            or (isinstance(raw, str) and ("エラー" in raw or "キャンセル" in raw))
        )
        return BrokerOrderReceipt(
            broker="rakuten-securities",
            client_order_id=intent.client_order_id,
            broker_order_id=str(rss_order_id),
            accepted=accepted,
            status=BrokerOrderStatus.ACCEPTED if accepted else BrokerOrderStatus.REJECTED,
            submitted_at=datetime.now(UTC),
            message=message,
        )

    def list_orders(self) -> list[BrokerOrder]:
        if self._order_reader is None:
            return []
        return self._order_reader.list_orders()

    def cancel_order(self, broker_order_id: str) -> BrokerOrderReceipt:
        raise NotImplementedError("Rakuten RSS cancellation mapping is not implemented yet")

    def account_snapshot(self) -> BrokerAccountSnapshot:
        raise NotImplementedError("Rakuten RSS account snapshot is not implemented yet")
