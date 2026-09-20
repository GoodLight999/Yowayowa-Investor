from __future__ import annotations

from enum import IntEnum

from yowayowa.broker_models import (
    BrokerOrderIntent,
    BrokerOrderPreview,
    BrokerOrderSide,
    BrokerOrderType,
    BrokerTransport,
)


RSS_STOCK_ORDER_V_FUNCTION = "RssStockOrder_V"


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
