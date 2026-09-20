from __future__ import annotations

from decimal import Decimal, InvalidOperation
from enum import IntEnum
from typing import Protocol

from pydantic import BaseModel

from yowayowa.broker_models import (
    BrokerOrder,
    BrokerOrderIntent,
    BrokerOrderPreview,
    BrokerOrderSide,
    BrokerOrderStatus,
    BrokerOrderType,
    BrokerTransport,
)

RSS_STOCK_ORDER_V_FUNCTION = "RssStockOrder_V"
RSS_CANCEL_ORDER_V_FUNCTION = "RssCancelOrder_V"


class WorksheetReader(Protocol):
    def read_scalar_formula(self, formula: str) -> object: ...

    def read_table_formula(self, formula: str) -> list[list[object]]: ...


class RakutenOrderIdRecord(BaseModel):
    rss_order_id: int
    function_name: str | None = None
    broker_order_id: str | None = None
    result: str | None = None


def _id_text(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value).strip()
    return text or None


def _to_int(value: object) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(str(value).replace(",", "").strip())
    except ValueError:
        return 0


def _to_decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        text = str(value).replace(",", "").strip()
        return Decimal(text) if text else None
    except InvalidOperation:
        return None


def _table_rows(
    table: list[list[object]],
    required_headers: set[str],
) -> list[dict[str, object]]:
    for index, raw_header in enumerate(table):
        headers = [str(value).strip() if value is not None else "" for value in raw_header]
        if not required_headers.issubset(set(headers)):
            continue
        rows: list[dict[str, object]] = []
        for raw_row in table[index + 1 :]:
            if not any(value not in (None, "") for value in raw_row):
                continue
            rows.append(
                {
                    header: raw_row[position] if position < len(raw_row) else None
                    for position, header in enumerate(headers)
                    if header
                }
            )
        return rows
    return []


def parse_rss_order_status(value: object) -> BrokerOrderStatus:
    try:
        code = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return BrokerOrderStatus.UNKNOWN
    return {
        -1: BrokerOrderStatus.UNKNOWN,
        1: BrokerOrderStatus.INACTIVE,
        2: BrokerOrderStatus.PENDING,
        3: BrokerOrderStatus.FILLED,
    }.get(code, BrokerOrderStatus.UNKNOWN)


def parse_order_text_status(text: object, *, filled_quantity: int, quantity: int) -> BrokerOrderStatus:
    value = str(text or "").strip()
    if value == "約定":
        return BrokerOrderStatus.FILLED
    if "取消済" in value:
        return BrokerOrderStatus.CANCELLED
    if "出来ず" in value:
        return BrokerOrderStatus.REJECTED
    if "出来有" in value and 0 < filled_quantity < quantity:
        return BrokerOrderStatus.PARTIALLY_FILLED
    if any(marker in value for marker in ("執行", "待機", "受付", "取消中", "訂正済")):
        return BrokerOrderStatus.PENDING
    return BrokerOrderStatus.UNKNOWN


class RakutenRssInquiry:
    def __init__(self, reader: WorksheetReader) -> None:
        self._reader = reader

    def order_status(self, rss_order_id: int) -> BrokerOrderStatus:
        if rss_order_id < 1 or rss_order_id > 2_147_483_647:
            raise ValueError("rss_order_id must be between 1 and 2147483647")
        raw = self._reader.read_scalar_formula(f"RssOrderStatus({rss_order_id})")
        return parse_rss_order_status(raw)

    def order_id_records(self) -> list[RakutenOrderIdRecord]:
        table = self._reader.read_table_formula("RssOrderIDList()")
        rows = _table_rows(table, {"発注ID", "注文番号", "発注結果"})
        records: list[RakutenOrderIdRecord] = []
        for row in rows:
            rss_order_id = _to_int(row.get("発注ID"))
            if rss_order_id <= 0:
                continue
            records.append(
                RakutenOrderIdRecord(
                    rss_order_id=rss_order_id,
                    function_name=_id_text(row.get("関数名")),
                    broker_order_id=_id_text(row.get("注文番号")),
                    result=_id_text(row.get("発注結果")),
                )
            )
        return records

    def broker_order_id(self, rss_order_id: int) -> str | None:
        for record in reversed(self.order_id_records()):
            if record.rss_order_id == rss_order_id:
                return record.broker_order_id
        return None

    def list_orders(self) -> list[BrokerOrder]:
        submissions = {
            record.broker_order_id: record.rss_order_id
            for record in self.order_id_records()
            if record.broker_order_id is not None
        }
        table = self._reader.read_table_formula("RssOrderList()")
        rows = _table_rows(
            table,
            {
                "注文番号",
                "通常注文状況",
                "銘柄コード",
                "売買",
                "注文数量",
                "約定数量",
            },
        )
        orders: list[BrokerOrder] = []
        for row in rows:
            broker_order_id = _id_text(row.get("注文番号"))
            if broker_order_id is None:
                continue
            quantity = _to_int(row.get("注文数量"))
            filled_quantity = _to_int(row.get("約定数量"))
            side_text = str(row.get("売買") or "")
            side = BrokerOrderSide.BUY if "買" in side_text else BrokerOrderSide.SELL
            transport_id = submissions.get(broker_order_id)
            orders.append(
                BrokerOrder(
                    broker="rakuten-securities",
                    transport_order_id=(
                        str(transport_id) if transport_id is not None else None
                    ),
                    broker_order_id=broker_order_id,
                    symbol=str(row.get("銘柄コード") or "").strip(),
                    side=side,
                    quantity=quantity,
                    filled_quantity=filled_quantity,
                    average_fill_price=None,
                    status=parse_order_text_status(
                        row.get("通常注文状況"),
                        filled_quantity=filled_quantity,
                        quantity=quantity,
                    ),
                )
            )
        return orders


class RakutenAccountType(IntEnum):
    SPECIFIC = 0
    GENERAL = 1
    NISA_GROWTH = 2
    OLD_NISA = 3


class RakutenExecutionCondition(IntEnum):
    DAY = 1
    WEEK = 2
    OPEN = 3
    CLOSE = 4
    DATE = 5
    CLOSE_MARKET = 6
    MARKET_TO_LIMIT = 7


def build_cash_stock_order_v_args(
    intent: BrokerOrderIntent,
    *,
    rss_order_id: int,
    sor: bool = False,
    account_type: RakutenAccountType = RakutenAccountType.SPECIFIC,
    execution_condition: RakutenExecutionCondition = RakutenExecutionCondition.DAY,
    expiration_yyyymmdd: str | None = None,
) -> tuple[object, ...]:
    if rss_order_id < 1 or rss_order_id > 2_147_483_647:
        raise ValueError("rss_order_id must be between 1 and 2147483647")
    if intent.currency.upper() != "JPY":
        raise ValueError("MARKET SPEED II RSS domestic cash-stock orders require JPY")
    if execution_condition == RakutenExecutionCondition.DATE and not expiration_yyyymmdd:
        raise ValueError("expiration_yyyymmdd is required for date-specified orders")
    if execution_condition != RakutenExecutionCondition.DATE:
        expiration_yyyymmdd = None

    side = 3 if intent.side == BrokerOrderSide.BUY else 1
    price_kind = 0 if intent.order_type == BrokerOrderType.MARKET else 1
    price = None if price_kind == 0 else intent.limit_price

    return (
        rss_order_id,
        intent.symbol,
        side,
        0,  # normal order
        1 if sor else 0,
        intent.quantity,
        price_kind,
        price,
        int(execution_condition),
        expiration_yyyymmdd,
        int(account_type),
        None,  # stop trigger price
        None,  # stop trigger condition
        None,  # stop order price kind
        None,  # stop order price
        0,  # no set order
        None,  # set order price
        None,  # set order execution condition
        None,  # set order expiration
    )


def build_cancel_order_v_args(
    *,
    rss_order_id: int,
    broker_order_id: str,
) -> tuple[object, ...]:
    if rss_order_id < 1 or rss_order_id > 2_147_483_647:
        raise ValueError("rss_order_id must be between 1 and 2147483647")
    normalized = _id_text(broker_order_id)
    if normalized is None or not normalized.isdigit():
        raise ValueError("broker_order_id must be a numeric Rakuten order number")
    return rss_order_id, int(normalized)


def preview_cash_stock_order(
    intent: BrokerOrderIntent,
) -> BrokerOrderPreview:
    return BrokerOrderPreview(
        broker="rakuten-securities",
        transport=BrokerTransport.LOCAL_PROGRAMMABLE_INTERFACE,
        intent=intent,
        estimated_notional=intent.estimated_notional(),
        currency=intent.currency.upper(),
        warnings=[],
    )
