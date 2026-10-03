"""Batch 2 gap coverage for the Rakuten MS2 RSS provider.

Covers the remaining helpers/branches measured missing: ``_id_text`` /
``_to_int`` / ``_to_decimal`` / ``_table_rows`` exception & empty-cell paths,
the rest of the order-status text mapping, ``RakutenRssInquiry`` guard
branches, and ``build_*_order_v_args`` validation edges. All inputs are
in-process fakes; no Excel or network is touched.

Japanese header literals are written via unicode escapes: the production
module reads the exact same strings (verified by importing e.g.
``["\u767a\u6ce8ID", ...]`` against its ``_table_rows`` call sites).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from yowayowa.broker_models import (
    RAKUTEN_SECURITIES_BROKER,
    BrokerOrderIntent,
    BrokerOrderSide,
    BrokerOrderStatus,
    BrokerOrderType,
)
from yowayowa.providers.rakuten_ms2_rss import (
    RakutenAccountType,
    RakutenExecutionCondition,
    RakutenOrderIdRecord,
    RakutenRssInquiry,
    _id_text,
    _table_rows,
    _to_decimal,
    _to_int,
    build_cancel_order_v_args,
    build_cash_stock_order_v_args,
    parse_order_text_status,
    parse_rss_order_status,
    preview_cash_stock_order,
)

# Exact header literals as unicode escapes (byte-verified against production).
H_ID = "\u767a\u6ce8ID"  # hassuu ID
H_ORDER = "\u6ce8\u6587\u756a\u53f7"  # broker order number
H_RESULT = "\u767a\u6ce8\u7d50\u679c"  # submission result
H_FUNC = "\u95a2\u6570\u540d"  # function name
H_STATUS = "\u901a\u5e38\u6ce8\u6587\u72b6\u6cc1"  # normal-order status
H_SYMBOL = "\u9298\u67c4\u30b3\u30fc\u30c9"  # symbol code
H_SIDE = "\u58f2\u8cb7"  # buy/sell
H_QTY = "\u6ce8\u6587\u6570\u91cf"  # ordered quantity
H_FILLED = "\u7d04\u5b9a\u6570\u91cf"  # filled quantity
K_BUY = "\u8cb7"  # "buy" marker
K_FILLED_WORD = "\u7d04\u5b9a"  # filled
K_CANCELLED = "\u53d6\u6d88\u6e08"  # cancelled
K_REJECTED = "\u51fa\u6765\u305a"  # rejected
K_PARTIAL = "\u51fa\u6765\u6709"  # partially filled
K_EXEC = "\u57f7\u884c"  # executing
K_WAIT = "\u5f85\u6a5f"  # waiting
K_ACCEPT = "\u53d7\u4ed8"  # accepted
K_CANCELING = "\u53d6\u6d88\u4e2d"  # canceling
K_REVISED = "\u8a02\u6b63\u6e08"  # revised


# ------------------------------------------------------------------ helpers


def test_id_text_value_shapes() -> None:
    assert _id_text(None) is None
    assert _id_text(True) is None
    assert _id_text(False) is None
    assert _id_text(7) == "7"
    assert _id_text(7.5) == "7.5"
    assert _id_text(7.0) == "7"
    assert _id_text(" leading ") == "leading"
    assert _id_text("   ") is None
    assert _id_text("abc") == "abc"


def test_to_int_value_shapes() -> None:
    assert _to_int(None) == 0
    assert _to_int(True) == 0
    assert _to_int(False) == 0
    assert _to_int(5) == 5
    assert _to_int(2.9) == 2
    assert _to_int("1,234") == 1234
    assert _to_int(" 42 ") == 42
    assert _to_int("bad") == 0


def test_to_decimal_value_shapes() -> None:
    assert _to_decimal(None) is None
    assert _to_decimal(True) is None
    assert _to_decimal("") is None
    assert _to_decimal("   ") is None
    assert _to_decimal("1,234.5") == Decimal("1234.5")
    assert _to_decimal("-2.5") == Decimal("-2.5")
    assert _to_decimal("junk") is None


def test_table_rows_requires_required_headers() -> None:
    table = [
        ["=formula", None, None],
        [H_ID, H_ORDER, H_RESULT],
        [7, 123, "ok"],
    ]
    rows = _table_rows(table, {H_ID, H_ORDER, H_RESULT})

    assert rows == [{H_ID: 7, H_ORDER: 123, H_RESULT: "ok"}]


def test_table_rows_skips_empty_and_pads_short_rows() -> None:
    table = [
        [H_ID, H_ORDER],
        [None, ""],  # all-empty -> skipped
        [1, None],  # short row -> padded with header-name keys
        [2, "x"],
    ]
    rows = _table_rows(table, {H_ID})

    assert rows == [
        {H_ID: 1, H_ORDER: None},
        {H_ID: 2, H_ORDER: "x"},
    ]


def test_table_rows_returns_empty_without_header_match() -> None:
    assert _table_rows([["other", "headers"]], {H_ID}) == []
    assert _table_rows([], {H_ID}) == []


# ------------------------------------------------------- status text mapping


def test_parse_rss_order_status_numeric_shapes() -> None:
    assert parse_rss_order_status("2") is BrokerOrderStatus.PENDING
    assert parse_rss_order_status(" 3 ") is BrokerOrderStatus.FILLED
    assert parse_rss_order_status(-1) is BrokerOrderStatus.UNKNOWN
    assert parse_rss_order_status(99) is BrokerOrderStatus.UNKNOWN
    assert parse_rss_order_status("xx") is BrokerOrderStatus.UNKNOWN
    assert parse_rss_order_status(None) is BrokerOrderStatus.UNKNOWN
    assert parse_rss_order_status(True) is BrokerOrderStatus.UNKNOWN


def test_parse_order_text_status_full_mapping() -> None:
    assert parse_order_text_status(K_FILLED_WORD, filled_quantity=10, quantity=10) is (
        BrokerOrderStatus.FILLED
    )
    assert parse_order_text_status(K_CANCELLED, filled_quantity=0, quantity=10) is (
        BrokerOrderStatus.CANCELLED
    )
    assert parse_order_text_status(K_REJECTED, filled_quantity=0, quantity=10) is (
        BrokerOrderStatus.REJECTED
    )
    assert parse_order_text_status(K_PARTIAL, filled_quantity=5, quantity=10) is (
        BrokerOrderStatus.PARTIALLY_FILLED
    )
    # partial marker but no partial fill -> falls through to the pending word match
    assert parse_order_text_status(K_PARTIAL, filled_quantity=0, quantity=10) is (
        BrokerOrderStatus.UNKNOWN
    )
    assert parse_order_text_status("partial " + K_PARTIAL, filled_quantity=4, quantity=10) is (
        BrokerOrderStatus.PARTIALLY_FILLED
    )
    for marker in (K_EXEC, K_WAIT, K_ACCEPT, K_CANCELING, K_REVISED):
        assert parse_order_text_status(marker, filled_quantity=0, quantity=10) is (
            BrokerOrderStatus.PENDING
        )
    assert parse_order_text_status(None, filled_quantity=0, quantity=10) is (
        BrokerOrderStatus.UNKNOWN
    )
    assert parse_order_text_status("", filled_quantity=0, quantity=10) is (
        BrokerOrderStatus.UNKNOWN
    )
    # zero filled or full filled does not map to PARTIALLY_FILLED
    assert parse_order_text_status(K_PARTIAL, filled_quantity=10, quantity=10) is (
        BrokerOrderStatus.UNKNOWN
    )


# ------------------------------------------------------------- inquiry guards


class DictReader:
    def __init__(self, scalars: dict[str, object], tables: dict[str, list[list[object]]]):
        self.scalars = scalars
        self.tables = tables

    def read_scalar_formula(self, formula: str) -> object:
        return self.scalars[formula]

    def read_table_formula(self, formula: str) -> list[list[object]]:
        return self.tables[formula]


def test_order_status_range_guard() -> None:
    inquiry = RakutenRssInquiry(DictReader({}, {}))

    with pytest.raises(ValueError, match="between 1 and 2147483647"):
        inquiry.order_status(0)
    with pytest.raises(ValueError, match="between 1 and 2147483647"):
        inquiry.order_status(2_147_483_648)


def test_order_id_records_skips_non_positive_ids() -> None:
    table = [
        [H_ID, H_FUNC, H_ORDER, H_RESULT],
        [0, "fn", "111", "ok"],  # id <= 0 -> skipped
        [None, "fn", "222", "ok"],  # _to_int(None) == 0 -> skipped
        [5, "", "", ""],
    ]
    reader = DictReader({}, {"RssOrderIDList()": table})

    records = RakutenRssInquiry(reader).order_id_records()

    assert len(records) == 1
    assert records[0].rss_order_id == 5
    assert records[0].function_name is None  # empty string -> _id_text -> None
    assert records[0].broker_order_id is None


def test_broker_order_id_returns_none_for_unknown_id() -> None:
    table = [[H_ID, H_ORDER, H_RESULT], [7, 123, "ok"]]
    reader = DictReader({}, {"RssOrderIDList()": table})

    assert RakutenRssInquiry(reader).broker_order_id(7) == "123"
    assert RakutenRssInquiry(reader).broker_order_id(9) is None


def test_list_orders_joins_transport_and_dedups_values() -> None:
    id_table = [
        [H_ID, H_ORDER, H_RESULT],
        [7, 123456, "ok"],
        [8, None, "ok"],  # no broker id -> not eligible for join
    ]
    order_table = [
        [H_ORDER, H_STATUS, H_SYMBOL, H_SIDE, H_QTY, H_FILLED],
        [123456, K_EXEC, "4755", K_BUY, 100, 0],
        [None, K_EXEC, "6758", K_BUY, 100, 0],  # no id -> skipped
        [778899, K_FILLED_WORD, "6758", "\u58f2", 50, 50],  # sell side
    ]
    reader = DictReader({}, {"RssOrderIDList()": id_table, "RssOrderList()": order_table})
    orders = RakutenRssInquiry(reader).list_orders()

    assert len(orders) == 2
    first, second = orders
    assert first.transport_order_id == "7"
    assert first.broker_order_id == "123456"
    assert first.side is BrokerOrderSide.BUY
    assert first.status is BrokerOrderStatus.PENDING
    assert second.transport_order_id is None
    assert second.side is BrokerOrderSide.SELL
    assert second.status is BrokerOrderStatus.FILLED


def test_list_positions_skips_blank_symbols() -> None:
    pos_table = [
        [
            H_SYMBOL,
            "\u53e3\u5ea7\u533a\u5206",
            "\u4fdd\u6709\u6570\u91cf",
            "\u5e73\u5747\u53d6\u5f97\u4fa1\u984d",
            "\u6642\u4fa1",
            "\u6642\u4fa1\u8a55\u4fa1\u984d",
            "\u8a55\u4fa1\u640d\u76ca\u984d",
        ],
        ["   ", "x", 1, 1, 1, 1, 1],  # blank symbol -> skipped
        ["4755", "\u7279\u5b9a", 100, 800, 900, 90000, 4950],
    ]
    reader2 = DictReader({}, {"RssPositionList()": pos_table})

    positions = RakutenRssInquiry(reader2).list_positions()

    assert len(positions) == 1
    assert positions[0].symbol == "4755"
    assert str(positions[0].quantity) == "100"
    assert str(positions[0].average_cost) == "800"
    assert positions[0].account_type == "\u7279\u5b9a"


def test_quote_rejects_unsafe_and_blank_symbols() -> None:
    inquiry = RakutenRssInquiry(
        DictReader({}, {}),
    )

    with pytest.raises(ValueError, match="unsafe for an Excel RSS formula"):
        inquiry.quote("")  # blank
    with pytest.raises(ValueError, match="unsafe for an Excel RSS formula"):
        inquiry.quote("4755;DROP")  # formula-metacharacters


def test_quote_rejects_nonpositive_price() -> None:
    inquiry = RakutenRssInquiry(DictReader({'RssMarket("4755.T","\u73fe\u5728\u5024")': None}, {}))

    with pytest.raises(LookupError, match="quote unavailable"):
        inquiry.quote("4755.T")


# -------------------------------------------------------------- v_args build


def _intent(**overrides: object) -> BrokerOrderIntent:
    values: dict[str, object] = {
        "client_order_id": "rk-gap",
        "symbol": "4755.T",
        "side": BrokerOrderSide.BUY,
        "quantity": 100,
        "order_type": BrokerOrderType.MARKET,
        "reference_price": Decimal("1000"),
        "currency": "JPY",
    }
    values.update(overrides)
    return BrokerOrderIntent(**values)  # type: ignore[arg-type]


def test_cash_order_args_cover_sor_limit_and_account_types() -> None:
    args = build_cash_stock_order_v_args(
        _intent(order_type=BrokerOrderType.LIMIT, limit_price=Decimal("900")),
        rss_order_id=3,
        sor=True,
        account_type=RakutenAccountType.NISA_GROWTH,
    )

    assert args[0] == 3
    assert args[4] == 1  # sor
    assert args[6] == 1  # price kind = limit
    assert args[7] == Decimal("900")
    assert args[9] is None  # non-DATE -> no expiration
    assert args[10] == int(RakutenAccountType.NISA_GROWTH)


def test_cash_order_requires_expiration_for_date_condition() -> None:
    with pytest.raises(ValueError, match="expiration_yyyymmdd is required"):
        build_cash_stock_order_v_args(
            _intent(),
            rss_order_id=1,
            execution_condition=RakutenExecutionCondition.DATE,
        )

    dated = build_cash_stock_order_v_args(
        _intent(),
        rss_order_id=1,
        execution_condition=RakutenExecutionCondition.DATE,
        expiration_yyyymmdd="20260930",
    )
    assert dated[9] == "20260930"

    week = build_cash_stock_order_v_args(
        _intent(),
        rss_order_id=1,
        execution_condition=RakutenExecutionCondition.WEEK,
        expiration_yyyymmdd="20260930",
    )
    assert week[9] is None  # cleared for non-DATE conditions


def test_cash_order_rejects_out_of_range_ids() -> None:
    with pytest.raises(ValueError, match="rss_order_id"):
        build_cash_stock_order_v_args(_intent(), rss_order_id=2_147_483_648)


def test_cancel_args_validate_id_and_numeric_broker_order() -> None:
    with pytest.raises(ValueError, match="rss_order_id"):
        build_cancel_order_v_args(rss_order_id=0, broker_order_id="1")
    with pytest.raises(ValueError, match="numeric Rakuten order number"):
        build_cancel_order_v_args(rss_order_id=1, broker_order_id="")
    with pytest.raises(ValueError, match="numeric Rakuten order number"):
        build_cancel_order_v_args(rss_order_id=1, broker_order_id=" 12x ")
    # whitespace-only becomes None -> ValueError; numeric string succeeds
    assert build_cancel_order_v_args(rss_order_id=4, broker_order_id=" 0099 ") == (4, 99)
    assert build_cancel_order_v_args(rss_order_id=5, broker_order_id=str(7)) == (5, 7)


def test_order_id_record_model_defaults() -> None:
    record = RakutenOrderIdRecord(rss_order_id=1)

    assert record.function_name is None
    assert record.broker_order_id is None
    assert record.result is None


def test_order_status_upper_bound_guard() -> None:
    """The upper guard is distinct from the lower one."""
    inquiry = RakutenRssInquiry(DictReader({}, {}))

    with pytest.raises(ValueError, match="between 1 and 2147483647"):
        inquiry.order_status(2_147_483_647 + 1)


def test_account_snapshot_without_capacity_rows_yields_none() -> None:
    """An empty capacity table leaves buying_power None instead of crashing."""
    reader = DictReader({}, {"RssCapacityList()": []})

    account = RakutenRssInquiry(reader).account_snapshot()

    assert account.buying_power is None
    assert account.cash_balance is None


def test_quote_success_returns_broker_quote() -> None:
    reader = DictReader(
        {'RssMarket("4755.T","現在値")': 912.5},
        {},
    )

    quote = RakutenRssInquiry(reader).quote("4755.T")

    assert quote.symbol == "4755.T"
    assert str(quote.price) == "912.5"


def test_cash_order_upper_id_and_non_jpy_guard_remain() -> None:
    intent = _intent()

    with pytest.raises(ValueError, match="rss_order_id"):
        build_cash_stock_order_v_args(intent, rss_order_id=-3)


def test_preview_builds_broker_order_preview() -> None:
    from yowayowa.broker_models import BrokerTransport

    preview = preview_cash_stock_order(_intent())

    assert preview.broker == RAKUTEN_SECURITIES_BROKER
    assert preview.transport is BrokerTransport.LOCAL_PROGRAMMABLE_INTERFACE
    assert preview.estimated_notional is not None
    assert preview.currency == "JPY"


def test_order_status_lower_bound_guard_reads_formula() -> None:
    # rss_order_id=0 raises before the formula is read; id=1 reads it.
    reader = DictReader({"RssOrderStatus(1)": 3}, {})
    assert RakutenRssInquiry(reader).order_status(1) is BrokerOrderStatus.FILLED


def test_cash_order_rejects_non_jpy_currency_upper() -> None:
    intent = _intent(currency="usd")
    with pytest.raises(ValueError, match="require JPY"):
        build_cash_stock_order_v_args(intent, rss_order_id=1)
