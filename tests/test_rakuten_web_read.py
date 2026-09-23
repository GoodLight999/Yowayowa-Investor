from __future__ import annotations

from decimal import Decimal

import pytest

from yowayowa.acquisition.transport import PrivateAcquisitionError, TransportResponse
from yowayowa.operator_bridge.rakuten_web import (
    RAKUTEN_RESOURCES,
    RAKUTEN_WEB_CONNECTOR_ID,
    RAKUTEN_WEB_HTML_CONNECTOR_ID,
    RAKUTEN_WEB_RESOURCE_CATALOG,
    RakutenWebFetchTransport,
    extract_fees,
    extract_margin_state,
    extract_margin_state_with_notes,
    extract_symbol_names,
    lookup_rakuten_resource,
    normalize_account,
    normalize_account_with_notes,
    normalize_executions,
    normalize_open_orders,
    normalize_order_history,
    normalize_positions,
    parse_jpy_amount,
    parse_quantity,
    parse_usd_amount,
)

# ---------------------------------------------------------------------------
# 数値パーサ
# ---------------------------------------------------------------------------


def test_parse_jpy_amount_formats() -> None:
    assert parse_jpy_amount("1,234,567円") == Decimal("1234567")
    assert parse_jpy_amount(" 1,234 円 ") == Decimal("1234")
    assert parse_jpy_amount("▲1,234") == Decimal("-1234")
    assert parse_jpy_amount("+500円") == Decimal("500")
    assert parse_jpy_amount("−750円") == Decimal("-750")  # U+2212 minus sign
    assert parse_jpy_amount("１２３") == Decimal("123")  # fullwidth digits


def test_parse_jpy_amount_unparseable_yields_none() -> None:
    assert parse_jpy_amount("—") is None
    assert parse_jpy_amount("") is None
    assert parse_jpy_amount(None) is None
    assert parse_jpy_amount("1,2o4") is None
    assert parse_jpy_amount("N/A") is None


def test_parse_usd_amount_formats() -> None:
    assert parse_usd_amount("$12.34") == Decimal("12.34")
    assert parse_usd_amount("1,234.56米ドル") == Decimal("1234.56")
    assert parse_usd_amount("▲$99.99") == Decimal("-99.99")
    assert parse_usd_amount("") is None
    assert parse_usd_amount("—") is None
    assert parse_usd_amount(None) is None


def test_parse_quantity_formats() -> None:
    assert parse_quantity("100株") == Decimal("100")
    assert parse_quantity("1,000口") == Decimal("1000")
    assert parse_quantity("10") == Decimal("10")
    assert parse_quantity("") is None
    assert parse_quantity("—") is None
    assert parse_quantity(None) is None


# ---------------------------------------------------------------------------
# トランスポート (read-only / host check)
# ---------------------------------------------------------------------------


class _ScriptedFetch:
    def __init__(self, response: TransportResponse) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def __call__(
        self,
        method: str,
        url: str,
        *,
        params: object = None,
        headers: object = None,
        **_: object,
    ) -> TransportResponse:
        self.calls.append({"method": method, "url": url})
        return self.response


def _ok_response(
    url: str = "https://trade.rakuten-sec.co.jp/web/positions/jp",
) -> TransportResponse:
    return TransportResponse(
        status_code=200,
        url=url,
        content_type="application/json",
        text="{}",
        content=b"{}",
        elapsed_ms=1.0,
    )


def test_transport_resolves_relative_urls_against_base() -> None:
    fetch = _ScriptedFetch(_ok_response())
    transport = RakutenWebFetchTransport(fetch=fetch)
    response = transport.fetch("GET", "web/positions/jp")
    assert response.status_code == 200
    assert fetch.calls == [
        {"method": "GET", "url": "https://trade.rakuten-sec.co.jp/web/positions/jp"}
    ]


def test_transport_allows_absolute_rakuten_hosts() -> None:
    fetch = _ScriptedFetch(_ok_response("https://www.rakuten-sec.co.jp/member/"))
    transport = RakutenWebFetchTransport(fetch=fetch)
    transport.fetch("GET", "https://www.rakuten-sec.co.jp/member/")
    assert len(fetch.calls) == 1


def test_transport_rejects_disallowed_host() -> None:
    transport = RakutenWebFetchTransport(fetch=_ScriptedFetch(_ok_response()))
    try:
        transport.fetch("GET", "https://evil.example.com/web/positions")
    except PrivateAcquisitionError as exc:
        assert "host not allowed" in exc.reason
    else:
        raise AssertionError("expected PrivateAcquisitionError")


def test_transport_rejects_non_get_read_only() -> None:
    transport = RakutenWebFetchTransport(fetch=_ScriptedFetch(_ok_response()))
    try:
        transport.fetch("POST", "web/orders")
    except PrivateAcquisitionError as exc:
        assert exc.reason == "read-only connector"
    else:
        raise AssertionError("expected PrivateAcquisitionError")


def test_catalog_urls_are_unverified_initial_assumptions() -> None:
    assert RAKUTEN_WEB_RESOURCE_CATALOG, "catalog must not be empty"
    for entry in RAKUTEN_WEB_RESOURCE_CATALOG.values():
        assert entry.verified is False, f"{entry.resource}/{entry.market} must start unverified"
        assert entry.parser_kind in ("json", "tables")


# ---------------------------------------------------------------------------
# 正規化器: account
# ---------------------------------------------------------------------------


def test_normalize_account_jp_json_payload() -> None:
    payload = {
        "cash_balance": "1,234,567円",
        "buying_power": "2,000,000円",
        "as_of": "2026-09-23T09:00:00+09:00",
    }
    snapshot = normalize_account(payload, market="jp")
    assert snapshot.broker == "rakuten-securities"
    assert snapshot.currency == "JPY"
    assert snapshot.cash_balance == Decimal("1234567")
    assert snapshot.buying_power == Decimal("2000000")
    assert snapshot.captured_at.year == 2026


def test_normalize_account_missing_fields_are_none_not_zero() -> None:
    snapshot = normalize_account({"rows": []}, market="jp")
    assert snapshot.cash_balance is None
    assert snapshot.buying_power is None


def test_normalize_account_us_tables_payload_vertical_layout() -> None:
    # tables-parser shape: vertical label/value rows (Japanese statements use this)
    payload = {
        "tables": [
            {
                "headers": ["項目", "金額"],
                "rows": [
                    {"項目": "買付余力", "金額": "$5,000.00"},
                    {"項目": "預り金", "金額": "$1,250.50"},
                ],
            }
        ]
    }
    snapshot = normalize_account(payload, market="us")
    assert snapshot.currency == "USD"
    assert snapshot.buying_power == Decimal("5000.00")
    assert snapshot.cash_balance == Decimal("1250.50")


# ---------------------------------------------------------------------------
# 正規化器: positions
# ---------------------------------------------------------------------------


def test_normalize_positions_jp_json() -> None:
    payload = {
        "positions": [
            {
                "symbol": "7203",
                "name": "トヨタ自動車",
                "quantity": "100株",
                "average_cost": "2,500円",
                "market_price": "2,650円",
                "market_value": "265,000円",
                "unrealized_pnl": "▲15,000円",
                "account_type": "特定",
            }
        ]
    }
    positions, notes = normalize_positions(payload, market="jp")
    assert not notes
    assert len(positions) == 1
    position = positions[0]
    assert position.symbol == "7203"
    assert position.quantity == Decimal("100")
    assert position.average_cost == Decimal("2500")
    assert position.market_price == Decimal("2650")
    assert position.market_value == Decimal("265000")
    assert position.unrealized_pnl == Decimal("-15000")
    assert position.currency == "JPY"
    assert position.account_type == "cash"  # 特定口座 is a cash account


def test_normalize_positions_missing_fields_become_none_with_notes() -> None:
    payload = {"positions": [{"symbol": "6501", "quantity": "200株"}]}
    positions, notes = normalize_positions(payload, market="jp")
    assert len(positions) == 1
    position = positions[0]
    assert position.average_cost is None
    assert position.market_price is None
    assert position.market_value is None
    assert position.unrealized_pnl is None
    assert position.account_type is None
    assert not notes  # absent optional fields are not errors


def test_normalize_positions_unparseable_quantity_skips_row_with_note() -> None:
    payload = {
        "positions": [
            {"symbol": "7203", "quantity": "1,2o4株"},
            {"symbol": "6758", "quantity": "10株"},
        ]
    }
    positions, notes = normalize_positions(payload, market="jp")
    assert [p.symbol for p in positions] == ["6758"]
    assert len(notes) == 1
    assert "unparseable quantity" in notes[0]
    assert "missing data is not zero" in notes[0]


def test_normalize_positions_missing_symbol_skips_row_with_note() -> None:
    payload = {"positions": [{"quantity": "10株"}]}
    positions, notes = normalize_positions(payload, market="jp")
    assert positions == []
    assert any("missing symbol" in note for note in notes)


def test_normalize_positions_empty_list() -> None:
    positions, notes = normalize_positions({"positions": []}, market="jp")
    assert positions == []
    assert notes == []


def test_normalize_positions_html_tables_shape() -> None:
    payload = {
        "tables": [
            {
                "headers": ["銘柄コード", "銘柄名", "数量", "取得単価", "現在値", "評価損益"],
                "rows": [
                    {
                        "銘柄コード": "7203",
                        "銘柄名": "トヨタ自動車",
                        "数量": "100",
                        "取得単価": "2,500円",
                        "現在値": "2,650円",
                        "評価損益": "15,000円",
                    }
                ],
            }
        ]
    }
    positions, notes = normalize_positions(payload, market="jp")
    assert not notes
    assert len(positions) == 1
    position = positions[0]
    assert position.symbol == "7203"
    assert position.quantity == Decimal("100")
    assert position.average_cost == Decimal("2500")
    assert position.market_price == Decimal("2650")
    assert position.unrealized_pnl == Decimal("15000")


def test_normalize_positions_usd_market_and_per_position_currency() -> None:
    payload = {
        "positions": [
            {"symbol": "AAPL", "quantity": "10", "average_cost": "$180.50"},
            {"symbol": "7203", "quantity": "100", "currency": "円", "average_cost": "2,500円"},
        ]
    }
    positions, notes = normalize_positions(payload, market="us")
    assert not notes
    assert positions[0].currency == "USD"  # market default
    assert positions[0].average_cost == Decimal("180.50")
    assert positions[1].currency == "JPY"  # explicit per-position override kept
    assert positions[1].average_cost == Decimal("2500")


# ---------------------------------------------------------------------------
# 正規化器: open orders / executions
# ---------------------------------------------------------------------------


def test_normalize_open_orders_jp_json() -> None:
    payload = {
        "orders": [
            {
                "order_id": "20260923-0001",
                "symbol": "7203",
                "side": "買い",
                "quantity": "100株",
                "status": "執行待ち",
                "limit_price": "2,600円",
            }
        ]
    }
    orders, notes = normalize_open_orders(payload, market="jp")
    assert not notes
    assert len(orders) == 1
    order = orders[0]
    assert order.broker_order_id == "20260923-0001"
    assert order.symbol == "7203"
    assert order.side == "buy"
    assert order.quantity == 100
    assert order.status.value == "pending"


def test_normalize_open_orders_missing_order_number_empty_id_plus_note() -> None:
    payload = {"orders": [{"symbol": "7203", "side": "sell", "quantity": "100", "status": "待機"}]}
    orders, notes = normalize_open_orders(payload, market="jp")
    assert len(orders) == 1
    assert orders[0].broker_order_id == ""
    assert any("missing order number" in note for note in notes)


def test_normalize_open_orders_fees_go_to_detail_not_model() -> None:
    payload = {
        "orders": [
            {
                "order_id": "A-1",
                "symbol": "7203",
                "side": "buy",
                "quantity": "100",
                "status": "pending",
                "手数料": "55円",
            }
        ]
    }
    orders, _notes = normalize_open_orders(payload, market="jp")
    assert len(orders) == 1
    fees = extract_fees(payload)
    assert fees == {"A-1": "55円"}


def test_normalize_open_orders_html_tables_shape() -> None:
    payload = {
        "tables": [
            {
                "headers": ["注文番号", "銘柄コード", "売買", "注文数量", "状況"],
                "rows": [
                    {
                        "注文番号": "20260923-0002",
                        "銘柄コード": "6758",
                        "売買": "売却",
                        "注文数量": "20株",
                        "状況": "執行待ち",
                    }
                ],
            }
        ]
    }
    orders, notes = normalize_open_orders(payload, market="jp")
    assert not notes
    assert len(orders) == 1
    assert orders[0].side == "sell"
    assert orders[0].quantity == 20
    assert orders[0].status.value == "pending"


def test_normalize_executions_become_filled_orders() -> None:
    payload = {
        "executions": [
            {
                "order_id": "20260922-0099",
                "symbol": "6501",
                "side": "買い",
                "quantity": "300株",
                "平均約定単価": "3,800円",
                "手数料": "825円",
                "受渡日": "2026-09-24",
            }
        ]
    }
    orders, notes = normalize_executions(payload, market="jp")
    assert not notes
    assert len(orders) == 1
    order = orders[0]
    assert order.status.value == "filled"
    assert order.filled_quantity == 300
    assert order.average_fill_price == Decimal("3800")
    assert extract_fees(payload) == {"20260922-0099": "825円"}


def test_normalize_executions_empty_list() -> None:
    orders, notes = normalize_executions({"executions": []}, market="jp")
    assert orders == []
    assert notes == []


def test_normalize_orders_unknown_status_maps_to_unknown() -> None:
    payload = {
        "orders": [
            {"order_id": "X-1", "symbol": "7203", "side": "buy", "quantity": "1", "status": "謎"}
        ]
    }
    orders, _notes = normalize_open_orders(payload, market="jp")
    assert orders[0].status.value == "unknown"


# ---------------------------------------------------------------------------
# detail 抽出 (margin_state / fees)
# ---------------------------------------------------------------------------


def test_extract_margin_state_present_fields() -> None:
    payload = {
        "margin_deposit": "300,000円",
        "maintenance_rate": "250.5%",
        "margin_positions": [{"symbol": "7203", "quantity": "100"}],
    }
    margin = extract_margin_state(payload)
    assert margin is not None
    assert margin["margin_deposit"] == Decimal("300000")
    assert margin["maintenance_rate"] == Decimal("250.5")
    assert isinstance(margin["margin_positions"], list)


def test_extract_margin_state_absent_yields_none() -> None:
    assert extract_margin_state({"cash_balance": "1円"}) is None


def test_extract_fees_absent_yields_none() -> None:
    assert extract_fees({"orders": [{"order_id": "1", "symbol": "7203"}]}) is None


def test_connector_ids_and_catalog_dispatch() -> None:
    assert RAKUTEN_WEB_CONNECTOR_ID == "rakuten-web"
    assert RAKUTEN_WEB_HTML_CONNECTOR_ID == "rakuten-web-html"
    json_entry = RAKUTEN_WEB_RESOURCE_CATALOG[("positions", "jp")]
    tables_entry = RAKUTEN_WEB_RESOURCE_CATALOG[("account", "us")]
    assert json_entry.parser_kind == "json"
    assert tables_entry.parser_kind == "tables"


def _history_payload() -> dict[str, object]:
    return {
        "orders": [
            {
                "order_id": "cancel-1",
                "symbol": "7203",
                "side": "買い",
                "quantity": "1",
                "status": "取消",
            },
            {
                "order_id": "fill-1",
                "symbol": "6758",
                "side": "売り",
                "quantity": "2",
                "status": "約定",
            },
            {
                "order_id": "pending-1",
                "symbol": "6501",
                "side": "買い",
                "quantity": "3",
                "status": "執行待ち",
            },
        ]
    }


def test_order_history_catalog_entries_unverified_assumptions() -> None:
    for market, parser in (("jp", "json"), ("us", "tables")):
        entry = lookup_rakuten_resource("order_history", market)
        assert entry is not None and entry.verified is False
        assert "order_history" in RAKUTEN_RESOURCES
        assert entry.parser_kind == parser


def test_normalize_order_history_keeps_cancelled_and_filled() -> None:
    orders, _ = normalize_order_history(_history_payload(), market="jp")
    assert len(orders) == 3
    assert {order.status.value for order in orders} == {"cancelled", "filled", "pending"}


def test_normalize_open_orders_excludes_terminal_statuses() -> None:
    orders, notes = normalize_open_orders(_history_payload(), market="jp")
    assert [order.status.value for order in orders] == ["pending"]
    assert sum("excluded from open_orders" in note for note in notes) == 2


def test_open_orders_and_order_history_do_not_mix() -> None:
    history, _ = normalize_order_history(_history_payload(), market="jp")
    open_orders, _ = normalize_open_orders(_history_payload(), market="jp")
    history_ids = {order.broker_order_id for order in history}
    open_ids = {order.broker_order_id for order in open_orders}
    assert open_ids <= history_ids
    assert open_ids != history_ids


def test_extract_margin_state_jp_availability_labels_are_decimal() -> None:
    payload = {
        label: "1,000円"
        for labels in (
            ("信用新規建余力",),
            ("信用建余力",),
            ("信用余力",),
            ("保証金余裕額",),
            ("委託保証金率",),
            ("委託保証金維持率",),
            ("保証金現金",),
            ("受入保証金合計",),
            ("必要保証金合計",),
            ("現物買付可能額",),
        )
        for label in labels
    }
    margin = extract_margin_state(payload, market="jp")
    assert margin is not None
    assert all(isinstance(value, Decimal) for value in margin.values())
    assert len(margin) == 10


def test_extract_margin_state_us_availability_labels_are_decimal() -> None:
    rows = [
        {"項目": label, "金額": "$1,000.00"}
        for label in (
            "信用新規建余力",
            "信用建余力",
            "信用余力",
            "保証金余裕額",
            "委託保証金率",
            "委託保証金維持率",
            "保証金現金",
            "受入保証金合計",
            "必要保証金合計",
            "現物買付可能額",
        )
    ]
    margin = extract_margin_state({"tables": [{"rows": rows}]}, market="us")
    assert margin is not None
    assert isinstance(margin["margin_buying_power"], Decimal)
    assert isinstance(margin["margin_collateral_surplus"], Decimal)


def test_extract_margin_state_us_collateral_currency_no_longer_dropped() -> None:
    payload = {
        "tables": [
            {
                "headers": ["項目", "金額"],
                "rows": [
                    {"項目": "拘束保証金", "金額": "$900.00"},
                    {"項目": "維持率", "金額": "300.0%"},
                    {"項目": "建玉", "金額": "$2,000.00"},
                ],
            }
        ]
    }
    margin, notes = extract_margin_state_with_notes(payload, market="us")
    assert margin is not None
    assert margin["margin_deposit"] == Decimal("900.00")
    assert margin["margin_positions"] == Decimal("2000.00")
    assert not any("unparseable" in note for note in notes)


def test_extract_margin_state_present_but_unparseable_yields_note() -> None:
    margin, notes = extract_margin_state_with_notes(
        {"tables": [{"rows": [{"項目": "拘束保証金", "金額": "—"}]}]}, market="us"
    )
    assert margin is None or "margin_deposit" not in margin
    assert any("拘束保証金" in note and "unparseable" in note for note in notes)


def test_extract_margin_state_vertical_unparseable_label_yields_note() -> None:
    margin, notes = extract_margin_state_with_notes(
        {"tables": [{"headers": ["項目", "金額"], "rows": [{"項目": "拘束保証金", "金額": "—"}]}]},
        market="us",
    )
    assert margin is None or "margin_deposit" not in margin
    assert any("拘束保証金" in note and "unparseable for USD" in note for note in notes)


def test_extract_margin_state_horizontal_unparseable_keeps_raw_without_note() -> None:
    margin, notes = extract_margin_state_with_notes({"拘束保証金": "—"}, market="us")
    assert margin == {"margin_deposit": "—"}
    assert not any("unparseable" in note for note in notes)


def test_normalize_positions_unrecognized_shape_yields_note() -> None:
    positions, notes = normalize_positions({"positions": "not-a-list"}, market="jp")
    assert positions == []
    assert any(
        "present but not a list" in note and "not proof of an empty account" in note
        for note in notes
    )


def test_normalize_positions_nested_shape_yields_note() -> None:
    positions, notes = normalize_positions(
        {"data": {"positions": [{"symbol": "7203", "quantity": "100株"}]}}, market="jp"
    )
    assert positions == []
    assert any("no list field found" in note for note in notes)


def test_normalize_account_nested_shape_yields_note() -> None:
    _, notes = normalize_account_with_notes(
        {"summary": {"現物買付余力": "1,000,000円"}}, market="jp"
    )
    assert any("amount-like values" in note and "summary.現物買付余力" in note for note in notes)


def test_normalize_account_flat_margin_only_payload_yields_no_note() -> None:
    payload = {
        label: "1,000,000円"
        for label in (
            "信用新規建余力",
            "信用建余力",
            "信用余力",
            "保証金余裕額",
            "委託保証金率",
            "委託保証金維持率",
            "保証金現金",
            "受入保証金合計",
            "必要保証金合計",
            "現物買付可能額",
        )
    }
    _, notes = normalize_account_with_notes(payload, market="jp")
    assert notes == []


def test_normalize_account_empty_shape_yields_no_note() -> None:
    assert normalize_account_with_notes({}, market="jp")[1] == []
    assert normalize_account_with_notes({"rows": []}, market="jp")[1] == []


def test_normalize_account_us_tables_shape_yields_no_note() -> None:
    payload = {"tables": [{"rows": [{"項目": "信用余力", "金額": "$250,000.00"}]}]}
    _, notes = normalize_account_with_notes(payload, market="us")
    assert notes == []


# ---------------------------------------------------------------------------
# order_history 経由の detail 抽出(fees / symbol_names) — N1 regression guard
# ---------------------------------------------------------------------------

_HISTORY_KEY_CASES = ("orders", "rows", "list", "history", "order_history", "orderHistory")


def _history_row(key_specific: dict[str, str]) -> dict[str, object]:
    """One order row; key_specific differentiates per-key payloads (e.g. order ids)."""
    row: dict[str, object] = {
        "order_id": "H-1",
        "symbol": "7203",
        "name": "トヨタ自動車",
        "side": "買い",
        "quantity": "100株",
        "status": "取消",
        "手数料": "55円",
    }
    row.update(key_specific)
    return row


@pytest.mark.parametrize("key", _HISTORY_KEY_CASES)
def test_extract_fees_from_order_history_keys(key: str) -> None:
    payload = {key: [_history_row({})]}
    assert extract_fees(payload) == {"H-1": "55円"}


@pytest.mark.parametrize("key", _HISTORY_KEY_CASES)
def test_extract_symbol_names_from_order_history_keys(key: str) -> None:
    payload = {key: [_history_row({})]}
    assert extract_symbol_names(payload) == {"7203": "トヨタ自動車"}


def test_extract_fees_order_history_multiple_keys_no_double_count() -> None:
    # order_historyキーとordersキーが共存するpayload: 同一行の二重計上はしない
    payload = {"order_history": [_history_row({})], "orders": ["not-a-dict"]}
    assert extract_fees(payload) == {"H-1": "55円"}


def test_extract_fees_disjoint_list_keys_collect_all_rows() -> None:
    # 別々のlistキーに別行がある場合は両方拾う
    payload = {
        "order_history": [_history_row({"order_id": "H-1"})],
        "executions": [_history_row({"order_id": "E-9", "手数料": "825円"})],
    }
    fees = extract_fees(payload)
    assert fees == {"H-1": "55円", "E-9": "825円"}


def test_extract_fees_non_list_history_key_yields_note_via_detail_extractor() -> None:
    # 認識キーが list でない場合、無言で落とさず形状 note を出す(open_ordersと同様)
    from yowayowa.operator_bridge.rakuten_web import extract_fees_with_notes

    payload = {"history": "not-a-list"}
    fees, notes = extract_fees_with_notes(payload)
    assert fees is None
    assert any("present but not a list" in note for note in notes)


def test_extract_fees_no_order_like_payload_yields_none_without_note() -> None:
    # account 等、注文明細を持たない payload には note を出さない
    from yowayowa.operator_bridge.rakuten_web import extract_fees_with_notes

    fees, notes = extract_fees_with_notes({"cash_balance": "1,000円"})
    assert fees is None
    assert notes == []


def test_extract_symbol_names_order_history_no_dedupe_loss() -> None:
    # history/orders 同一 payload 内の同一銘柄行は名前を上書きしない(最初の値を維持)
    payload = {
        "order_history": [_history_row({})],
        "executions": [_history_row({"order_id": "E-9", "name": "トヨタ自動車(重複)"})],
    }
    assert extract_symbol_names(payload) == {"7203": "トヨタ自動車"}


# ---------------------------------------------------------------------------
# margin note の誤警報窄め込み — N2
# ---------------------------------------------------------------------------


def test_extract_margin_state_label_valued_key_does_not_raise_note() -> None:
    # キーは無関係(メモ)、値がたまたまラベル文字列でも note を出さない
    _margin, notes = extract_margin_state_with_notes(
        {"メモ": "維持率", "買付余力": "1,000円"}, market="us"
    )
    assert not any("unparseable" in note for note in notes)


def test_extract_margin_state_label_stock_name_does_not_raise_note() -> None:
    _margin, notes = extract_margin_state_with_notes(
        {"銘柄名": "信用余力", "数量": "10株"}, market="us"
    )
    assert not any("unparseable" in note for note in notes)


def test_extract_margin_state_vertical_unparseable_still_notes() -> None:
    # 縦持ちテーブル行の「ラベル列」が拘束保証金で値が解釈不能な場合は従来どおり note
    _margin, notes = extract_margin_state_with_notes(
        {"tables": [{"rows": [{"項目": "拘束保証金", "金額": "—"}]}]}, market="us"
    )
    assert any("拘束保証金" in note and "unparseable" in note for note in notes)


# ---------------------------------------------------------------------------
# N3: tables 行の重複計上防止 (fees / symbol_names)
# ---------------------------------------------------------------------------


def test_extract_fees_tables_rows_counted_once() -> None:
    # N3: 注文番号列のない tables 行は list-key 族ごとに複製され 4 倍に膨張していた
    payload = {
        "tables": [
            {
                "rows": [
                    {"symbol": "7203", "手数料": "55円"},
                    {"symbol": "6758", "手数料": "110円"},
                    {"symbol": "9984", "手数料": "165円"},
                ]
            }
        ]
    }
    assert extract_fees(payload) == {"row[0]": "55円", "row[1]": "110円", "row[2]": "165円"}


def test_extract_fees_tables_rows_with_order_id_not_inflated() -> None:
    # N3: 注文番号列がある場合もキー上書きで 3 件のまま(膨張しない)
    payload = {
        "tables": [
            {
                "rows": [
                    {"symbol": "7203", "注文番号": "T-1", "手数料": "55円"},
                    {"symbol": "6758", "注文番号": "T-2", "手数料": "110円"},
                    {"symbol": "9984", "注文番号": "T-3", "手数料": "165円"},
                ]
            }
        ]
    }
    assert extract_fees(payload) == {"T-1": "55円", "T-2": "110円", "T-3": "165円"}


def test_extract_symbol_names_tables_rows_counted_once() -> None:
    # N3: tables 行の銘柄名も 1 回ずつ(件数==3、値も確認)
    payload = {
        "tables": [
            {
                "rows": [
                    {"symbol": "7203", "銘柄名": "トヨタ自動車"},
                    {"symbol": "6758", "銘柄名": "アドバンテスト"},
                    {"symbol": "9984", "銘柄名": "ソフトバンクグループ"},
                ]
            }
        ]
    }
    names = extract_symbol_names(payload)
    assert len(names) == 3
    assert names == {
        "7203": "トヨタ自動車",
        "6758": "アドバンテスト",
        "9984": "ソフトバンクグループ",
    }


def test_extract_fees_tables_and_explicit_lists_coexist_no_inflation() -> None:
    # N3: JSON list 行と tables 行の共存。修正前は tables 3行が list-key 族ごとに
    # 複製され膨張していた。修正後は J-1 + tables 3行の 4件(row index は J-1 が 0)。
    payload = {
        "orders": [{"order_id": "J-1", "symbol": "6501", "手数料": "495円"}],
        "tables": [
            {
                "rows": [
                    {"symbol": "7203", "手数料": "55円"},
                    {"symbol": "6758", "手数料": "110円"},
                    {"symbol": "9984", "手数料": "165円"},
                ]
            }
        ],
    }
    fees = extract_fees(payload)
    assert len(fees) == 4
    assert fees == {
        "J-1": "495円",
        "row[1]": "55円",
        "row[2]": "110円",
        "row[3]": "165円",
    }


def test_extract_fees_shared_json_list_keys_still_dedup() -> None:
    # N1 挙動維持の回帰: 同一 list オブジェクトを orders/history で共有すれば 1 件のみ
    shared = [{"order_id": "H-1", "symbol": "7203", "手数料": "55円"}]
    payload = {"orders": shared, "history": shared}
    assert extract_fees(payload) == {"H-1": "55円"}
