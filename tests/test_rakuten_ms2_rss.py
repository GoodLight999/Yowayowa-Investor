from decimal import Decimal

import pytest

from yowayowa.broker_models import BrokerOrderIntent, BrokerOrderSide, BrokerOrderType
from yowayowa.providers.rakuten_ms2_rss import (
    RSS_STOCK_ORDER_V_FUNCTION,
    RakutenAccountType,
    build_cash_stock_order_v_args,
)


def test_rakuten_market_order_maps_to_official_vba_signature() -> None:
    intent = BrokerOrderIntent(
        client_order_id="rk-1",
        symbol="4755.T",
        side=BrokerOrderSide.BUY,
        quantity=100,
        order_type=BrokerOrderType.MARKET,
        reference_price=Decimal("1000"),
        currency="JPY",
    )

    args = build_cash_stock_order_v_args(intent, rss_order_id=1)

    assert RSS_STOCK_ORDER_V_FUNCTION == "RssStockOrder_V"
    assert len(args) == 19
    assert args[:11] == (
        1,
        "4755.T",
        3,
        0,
        0,
        100,
        0,
        None,
        1,
        None,
        int(RakutenAccountType.SPECIFIC),
    )
    assert args[11:] == (None, None, None, None, 0, None, None, None)


def test_rakuten_limit_sell_maps_price_and_sor() -> None:
    intent = BrokerOrderIntent(
        client_order_id="rk-2",
        symbol="7203.T",
        side=BrokerOrderSide.SELL,
        quantity=200,
        order_type=BrokerOrderType.LIMIT,
        limit_price=Decimal("3500"),
        currency="JPY",
    )

    args = build_cash_stock_order_v_args(
        intent,
        rss_order_id=99,
        sor=True,
        account_type=RakutenAccountType.GENERAL,
    )

    assert args[0] == 99
    assert args[2] == 1
    assert args[4] == 1
    assert args[5] == 200
    assert args[6] == 1
    assert args[7] == Decimal("3500")
    assert args[10] == int(RakutenAccountType.GENERAL)


def test_rakuten_order_rejects_non_jpy_and_invalid_id() -> None:
    usd = BrokerOrderIntent(
        client_order_id="rk-3",
        symbol="4755.T",
        side=BrokerOrderSide.BUY,
        quantity=100,
        order_type=BrokerOrderType.MARKET,
        reference_price=Decimal("1000"),
        currency="USD",
    )
    with pytest.raises(ValueError, match="require JPY"):
        build_cash_stock_order_v_args(usd, rss_order_id=1)

    jpy = usd.model_copy(update={"currency": "JPY"})
    with pytest.raises(ValueError, match="rss_order_id"):
        build_cash_stock_order_v_args(jpy, rss_order_id=0)
