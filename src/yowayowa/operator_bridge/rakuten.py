from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from yowayowa.broker_models import (
    BrokerAccountSnapshot,
    BrokerCapabilities,
    BrokerOrder,
    BrokerOrderIntent,
    BrokerOrderPreview,
    BrokerOrderReceipt,
    BrokerOrderStatus,
    BrokerPosition,
    BrokerQuote,
    BrokerTransport,
)
from yowayowa.operator_bridge.excel import MacroRunner, WorksheetRunner
from yowayowa.providers.rakuten_ms2_rss import (
    RSS_CANCEL_ORDER_V_FUNCTION,
    RSS_STOCK_ORDER_V_FUNCTION,
    RakutenRssInquiry,
    build_cancel_order_v_args,
    build_cash_stock_order_v_args,
    preview_cash_stock_order,
)


class RakutenMs2RssLocalConnector:
    capabilities = BrokerCapabilities(
        broker="rakuten-securities",
        transport=BrokerTransport.LOCAL_PROGRAMMABLE_INTERFACE,
        account_snapshot=False,
        positions=False,
        orders=False,
        order_submission=True,
        order_cancel=True,
        quotes=False,
        scraping=False,
    )

    def __init__(
        self,
        macro_runner: MacroRunner,
        *,
        allocate_rss_order_id: Callable[[str], int],
        worksheet_runner: WorksheetRunner | None = None,
    ) -> None:
        self._macro_runner = macro_runner
        self._allocate_rss_order_id = allocate_rss_order_id
        self._inquiry = (
            RakutenRssInquiry(worksheet_runner) if worksheet_runner is not None else None
        )
        inquiry_available = self._inquiry is not None
        self.capabilities = self.capabilities.model_copy(
            update={
                "account_snapshot": inquiry_available,
                "positions": inquiry_available,
                "orders": inquiry_available,
                "quotes": inquiry_available,
            }
        )

    def preview_order(self, intent: BrokerOrderIntent) -> BrokerOrderPreview:
        return preview_cash_stock_order(intent)

    def submit_order(self, intent: BrokerOrderIntent) -> BrokerOrderReceipt:
        rss_order_id = self._allocate_rss_order_id(intent.client_order_id)
        args = build_cash_stock_order_v_args(intent, rss_order_id=rss_order_id)
        raw = self._macro_runner.run_macro(RSS_STOCK_ORDER_V_FUNCTION, args)
        message = None if raw is None else str(raw)
        rejected_markers = (
            "エラー",
            "キャンセル",
            "使用済",
            "発注ロック",
            "接続待ち",
        )
        accepted = not (
            raw is False
            or raw is None
            or (isinstance(raw, str) and any(marker in raw for marker in rejected_markers))
        )
        broker_order_id = (
            self._inquiry.broker_order_id(rss_order_id)
            if accepted and self._inquiry is not None
            else None
        )
        return BrokerOrderReceipt(
            broker="rakuten-securities",
            client_order_id=intent.client_order_id,
            transport_order_id=str(rss_order_id),
            broker_order_id=broker_order_id,
            accepted=accepted,
            status=BrokerOrderStatus.ACCEPTED if accepted else BrokerOrderStatus.REJECTED,
            submitted_at=datetime.now(UTC),
            message=message,
        )

    def list_orders(self) -> list[BrokerOrder]:
        if self._inquiry is None:
            return []
        return self._inquiry.list_orders()

    def list_positions(self) -> list[BrokerPosition]:
        if self._inquiry is None:
            return []
        return self._inquiry.list_positions()

    def quote(self, symbol: str) -> BrokerQuote:
        if self._inquiry is None:
            raise RuntimeError("Rakuten RSS worksheet inquiry is not configured")
        return self._inquiry.quote(symbol)

    def order_status(self, transport_order_id: str) -> BrokerOrderStatus:
        if self._inquiry is None:
            return BrokerOrderStatus.UNKNOWN
        try:
            rss_order_id = int(transport_order_id)
        except ValueError:
            return BrokerOrderStatus.UNKNOWN
        return self._inquiry.order_status(rss_order_id)

    def cancel_order(
        self,
        broker_order_id: str,
        *,
        client_order_id: str,
    ) -> BrokerOrderReceipt:
        cancel_rss_order_id = self._allocate_rss_order_id(client_order_id)
        args = build_cancel_order_v_args(
            rss_order_id=cancel_rss_order_id,
            broker_order_id=broker_order_id,
        )
        raw = self._macro_runner.run_macro(RSS_CANCEL_ORDER_V_FUNCTION, args)
        message = None if raw is None else str(raw)
        rejected = raw is False or raw is None or (
            isinstance(raw, str)
            and any(
                marker in raw
                for marker in ("エラー", "キャンセル", "使用済", "発注ロック", "接続待ち")
            )
        )
        return BrokerOrderReceipt(
            broker="rakuten-securities",
            client_order_id=client_order_id,
            transport_order_id=str(cancel_rss_order_id),
            broker_order_id=broker_order_id,
            accepted=not rejected,
            status=BrokerOrderStatus.ACCEPTED if not rejected else BrokerOrderStatus.REJECTED,
            submitted_at=datetime.now(UTC),
            message=message,
        )

    def account_snapshot(self) -> BrokerAccountSnapshot:
        if self._inquiry is None:
            raise RuntimeError("Rakuten RSS worksheet inquiry is not configured")
        return self._inquiry.account_snapshot()
