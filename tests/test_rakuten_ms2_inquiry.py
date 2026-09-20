from yowayowa.broker_models import BrokerOrderStatus
from yowayowa.providers.rakuten_ms2_rss import (
    RakutenRssInquiry,
    build_cancel_order_v_args,
    parse_rss_order_status,
)


class FakeWorksheetRunner:
    def __init__(
        self,
        *,
        scalars: dict[str, object] | None = None,
        tables: dict[str, list[list[object]]] | None = None,
    ) -> None:
        self.scalars = scalars or {}
        self.tables = tables or {}

    def read_scalar_formula(self, formula: str) -> object:
        return self.scalars[formula]

    def read_table_formula(self, formula: str) -> list[list[object]]:
        return self.tables[formula]


def test_rakuten_order_status_mapping_preserves_inactive_state() -> None:
    assert parse_rss_order_status(-1) is BrokerOrderStatus.UNKNOWN
    assert parse_rss_order_status(1) is BrokerOrderStatus.INACTIVE
    assert parse_rss_order_status(2) is BrokerOrderStatus.PENDING
    assert parse_rss_order_status(3) is BrokerOrderStatus.FILLED


def test_rakuten_inquiry_resolves_transport_id_to_broker_order_number() -> None:
    reader = FakeWorksheetRunner(
        tables={
            "RssOrderIDList()": [
                ["=RssOrderIDList()", None, None, None, None, None],
                ["発注ID", "関数名", "発注日", "発注時刻", "注文番号", "発注結果"],
                [7, "RssStockOrder", "2026/09/21", "09:00:01", 123456, "発注済み"],
            ]
        }
    )
    inquiry = RakutenRssInquiry(reader)

    records = inquiry.order_id_records()

    assert len(records) == 1
    assert records[0].rss_order_id == 7
    assert records[0].broker_order_id == "123456"
    assert inquiry.broker_order_id(7) == "123456"


def test_rakuten_inquiry_lists_orders_and_joins_transport_id() -> None:
    reader = FakeWorksheetRunner(
        tables={
            "RssOrderIDList()": [
                ["formula"],
                ["発注ID", "関数名", "発注日", "発注時刻", "注文番号", "発注結果"],
                [7, "RssStockOrder", "2026/09/21", "09:00:01", 123456, "発注済み"],
            ],
            "RssOrderList()": [
                ["formula"],
                [
                    "注文番号",
                    "通常注文状況",
                    "銘柄コード",
                    "売買",
                    "注文数量",
                    "約定数量",
                ],
                [123456, "出来有", "4755", "買付", 100, 40],
            ],
        }
    )
    inquiry = RakutenRssInquiry(reader)

    orders = inquiry.list_orders()

    assert len(orders) == 1
    order = orders[0]
    assert order.transport_order_id == "7"
    assert order.broker_order_id == "123456"
    assert order.symbol == "4755"
    assert order.quantity == 100
    assert order.filled_quantity == 40
    assert order.status is BrokerOrderStatus.PARTIALLY_FILLED


def test_rakuten_inquiry_reads_scalar_order_status() -> None:
    inquiry = RakutenRssInquiry(
        FakeWorksheetRunner(scalars={"RssOrderStatus(7)": 3})
    )

    assert inquiry.order_status(7) is BrokerOrderStatus.FILLED


def test_rakuten_cancel_vba_args_require_numeric_broker_order_number() -> None:
    assert build_cancel_order_v_args(
        rss_order_id=8,
        broker_order_id="123456",
    ) == (8, 123456)


def test_rakuten_inquiry_reads_positions_and_capacity() -> None:
    reader = FakeWorksheetRunner(
        tables={
            "RssPositionList()": [
                ["formula"],
                [
                    "銘柄コード",
                    "口座区分",
                    "保有数量",
                    "平均取得価額",
                    "時価",
                    "時価評価額",
                    "評価損益額",
                ],
                ["4755", "特定", 100, 850.5, 900, 90000, 4950],
            ],
            "RssCapacityList()": [
                ["formula"],
                ["現物買付可能額", "信用口座_保証金余裕額"],
                [1234567, 500000],
            ],
        }
    )
    inquiry = RakutenRssInquiry(reader)

    positions = inquiry.list_positions()
    account = inquiry.account_snapshot()

    assert len(positions) == 1
    assert positions[0].symbol == "4755"
    assert str(positions[0].quantity) == "100"
    assert str(positions[0].average_cost) == "850.5"
    assert str(positions[0].market_price) == "900"
    assert str(positions[0].market_value) == "90000"
    assert str(positions[0].unrealized_pnl) == "4950"
    assert account.currency == "JPY"
    assert str(account.buying_power) == "1234567"


def test_rakuten_inquiry_reads_current_quote_without_html_scraping() -> None:
    reader = FakeWorksheetRunner(
        scalars={'RssMarket("4755.T","現在値")': 912.5}
    )
    inquiry = RakutenRssInquiry(reader)

    quote = inquiry.quote("4755.T")

    assert quote.symbol == "4755.T"
    assert str(quote.price) == "912.5"
    assert quote.currency == "JPY"
