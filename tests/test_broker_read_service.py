from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from yowayowa.acquisition.cache import AcquisitionCache
from yowayowa.acquisition.models import AcquisitionFetchState, AuthState
from yowayowa.acquisition.registry import ConnectorRegistry
from yowayowa.acquisition.service import PrivateAcquisitionService, TransportFactory
from yowayowa.acquisition.snapshots import SnapshotStore
from yowayowa.acquisition.transport import (
    PrivateAcquisitionError,
    SessionTransport,
    TransportResponse,
)
from yowayowa.services.broker_read_service import BrokerReadService

_POSITIONS_BODY = json.dumps(
    {
        "positions": [
            {
                "symbol": "7203",
                "quantity": "100株",
                "average_cost": "2,500円",
                "market_price": "2,650円",
                "market_value": "265,000円",
                "unrealized_pnl": "▲15,000円",
            }
        ]
    },
    ensure_ascii=False,
).encode("utf-8")


class ScriptedTransport:
    """test_private_acquisition_service.py の型を踏襲した script transport。"""

    def __init__(self, responses: list[TransportResponse | Exception]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def fetch(
        self,
        method: str,
        resource: str,
        *,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        data: object | None = None,
    ) -> TransportResponse:
        self.calls.append({"method": method, "resource": resource})
        outcome = self._responses.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _response(
    body: bytes = _POSITIONS_BODY,
    status: int = 200,
    url: str = "https://trade.rakuten-sec.co.jp/web/positions/jp",
    content_type: str = "application/json",
) -> TransportResponse:
    return TransportResponse(
        status_code=status,
        url=url,
        content_type=content_type,
        text=body.decode("utf-8", errors="replace"),
        content=body,
        elapsed_ms=12.5,
    )


def _build_service(
    transport: SessionTransport,
    *,
    data_dir: Path,
    now: Callable[[], datetime] | None = None,
) -> BrokerReadService:
    factory: TransportFactory = lambda definition: transport  # noqa: E731
    kwargs: dict[str, Any] = {
        "registry": ConnectorRegistry(),
        "cache": AcquisitionCache(),
        "snapshot_store": SnapshotStore(data_dir),
        "data_dir": data_dir,
        "transport_factory": factory,
    }
    if now is not None:
        kwargs["now"] = now
    return BrokerReadService(acquisition=PrivateAcquisitionService(**kwargs))


def test_fetch_ok_first_time_snapshot_without_diff(tmp_path: Path) -> None:
    transport = ScriptedTransport([_response()])
    service = _build_service(transport, data_dir=tmp_path)

    outcome = service.fetch("positions", "jp")

    assert outcome.fetch_state == AcquisitionFetchState.OK
    assert outcome.auth_state == AuthState.AUTHENTICATED
    assert outcome.connector_id == "rakuten-web"
    assert outcome.resource == "positions"
    assert outcome.market == "jp"
    assert len(outcome.positions) == 1
    assert outcome.positions[0].symbol == "7203"
    assert str(outcome.positions[0].unrealized_pnl) == "-15000"
    assert outcome.snapshot is not None
    assert outcome.diff is None
    assert transport.calls[0]["resource"] == "web/positions/jp"


def test_fetch_same_payload_twice_diff_not_changed(tmp_path: Path) -> None:
    transport = ScriptedTransport([_response(), _response()])
    service = _build_service(transport, data_dir=tmp_path)
    service.fetch("positions", "jp")
    second = service.fetch("positions", "jp", force_refresh=True)
    assert second.fetch_state == AcquisitionFetchState.OK
    assert second.diff is not None
    assert second.diff.changed is False


def test_fetch_changed_payload_diff_changed(tmp_path: Path) -> None:
    body2 = json.dumps(
        {"positions": [{"symbol": "7203", "quantity": "200株"}]}, ensure_ascii=False
    ).encode("utf-8")
    transport = ScriptedTransport([_response(), _response(body2)])
    service = _build_service(transport, data_dir=tmp_path)
    service.fetch("positions", "jp")
    second = service.fetch("positions", "jp", force_refresh=True)
    assert second.diff is not None
    assert second.diff.changed is True
    assert len(second.positions) == 1
    assert str(second.positions[0].quantity) == "200"


def test_fetch_auth_expired_invalidates_cache(tmp_path: Path) -> None:
    login_body = _response(
        body="<html>楽天証券ログイン</html>".encode(),
        url="https://trade.rakuten-sec.co.jp/login",
    )
    transport = ScriptedTransport([_response(), login_body])
    service = _build_service(transport, data_dir=tmp_path)
    first = service.fetch("positions", "jp")
    assert first.fetch_state == AcquisitionFetchState.OK

    expired = service.fetch("positions", "jp", force_refresh=True)
    assert expired.fetch_state == AcquisitionFetchState.AUTH_EXPIRED
    assert expired.auth_state == AuthState.UNAUTHENTICED
    assert expired.positions == []
    assert expired.account is None
    assert "reauthentication" in " ".join(expired.notes)

    # Cache must be invalidated: a subsequent fetch goes back to the network.
    followup = ScriptedTransport([_response()])
    object.__setattr__(service._acquisition, "_transport_factory", lambda definition: followup)
    refetched = service.fetch("positions", "jp")
    assert refetched.fetch_state == AcquisitionFetchState.OK
    assert len(followup.calls) == 1


def test_fetch_transport_error_returns_failed(tmp_path: Path) -> None:
    transport = ScriptedTransport([PrivateAcquisitionError(AcquisitionFetchState.FAILED, "boom")])
    service = _build_service(transport, data_dir=tmp_path)
    outcome = service.fetch("positions", "jp")
    assert outcome.fetch_state == AcquisitionFetchState.FAILED
    assert outcome.positions == []
    assert "boom" in outcome.notes


def test_fetch_unknown_resource_returns_failed_without_exception(tmp_path: Path) -> None:
    transport = ScriptedTransport([])
    service = _build_service(transport, data_dir=tmp_path)
    outcome = service.fetch("nonexistent", "jp")
    assert outcome.fetch_state == AcquisitionFetchState.FAILED
    assert "unknown resource/market" in outcome.notes[0]
    assert transport.calls == []


def test_fetch_unknown_market_returns_failed(tmp_path: Path) -> None:
    transport = ScriptedTransport([])
    service = _build_service(transport, data_dir=tmp_path)
    outcome = service.fetch("positions", "eu")
    assert outcome.fetch_state == AcquisitionFetchState.FAILED
    assert "unknown resource/market" in outcome.notes[0]


def test_fetch_disallowed_host_url_returns_failed(tmp_path: Path) -> None:
    # Simulate the transport-level host guard raising for an off-host URL.
    def _raising_factory(definition: Any) -> SessionTransport:
        class _GuardTransport:
            def fetch(
                self,
                method: str,
                resource: str,
                *,
                params: Mapping[str, str] | None = None,
                headers: Mapping[str, str] | None = None,
                data: object | None = None,
            ) -> TransportResponse:
                raise PrivateAcquisitionError(
                    AcquisitionFetchState.FAILED, "host not allowed: evil.example.com"
                )

        return _GuardTransport()  # type: ignore[return-value]

    acquisition = PrivateAcquisitionService(
        registry=ConnectorRegistry(),
        cache=AcquisitionCache(),
        snapshot_store=SnapshotStore(tmp_path),
        data_dir=tmp_path,
        transport_factory=_raising_factory,
    )
    service = BrokerReadService(acquisition=acquisition)
    outcome = service.fetch("positions", "jp")
    assert outcome.fetch_state == AcquisitionFetchState.FAILED
    assert "host not allowed" in outcome.notes[0]


def test_fetch_stale_within_max_stale_serves_payload_with_reason(tmp_path: Path) -> None:
    clock = {"now": datetime(2026, 9, 23, 12, 0, 0, tzinfo=UTC)}

    def now_fn() -> datetime:
        return clock["now"]

    transport = ScriptedTransport([_response()])
    service = _build_service(transport, data_dir=tmp_path, now=now_fn)
    first = service.fetch("positions", "jp")
    assert first.fetch_state == AcquisitionFetchState.OK

    clock["now"] = clock["now"].replace(minute=2)  # past ttl(60s), within max-stale(600s)
    stale = service.fetch("positions", "jp")
    assert stale.fetch_state == AcquisitionFetchState.STALE
    assert stale.cache is not None
    assert stale.cache.state == AcquisitionFetchState.STALE
    assert stale.cache.reason is not None
    assert len(stale.positions) == 1  # stale payload still normalized
    assert len(transport.calls) == 1  # served without a new network fetch


def test_fetch_payload_none_yields_empty_lists(tmp_path: Path) -> None:
    # auth_expired path returns payload=None -> empty positions/orders
    login_body = _response(
        body="<html>ログインしてください</html>".encode(),
        url="https://trade.rakuten-sec.co.jp/login",
    )
    transport = ScriptedTransport([login_body])
    service = _build_service(transport, data_dir=tmp_path)
    outcome = service.fetch("positions", "jp")
    assert outcome.fetch_state == AcquisitionFetchState.AUTH_EXPIRED
    assert outcome.positions == []
    assert outcome.orders == []
    assert outcome.account is None


def test_fetch_account_normalizes_snapshot(tmp_path: Path) -> None:
    body = json.dumps(
        {"cash_balance": "1,234,567円", "buying_power": "2,000,000円"},
        ensure_ascii=False,
    ).encode("utf-8")
    url = "https://trade.rakuten-sec.co.jp/web/account/summary"
    transport = ScriptedTransport([_response(body, url=url)])
    service = _build_service(transport, data_dir=tmp_path)
    outcome = service.fetch("account", "jp")
    assert outcome.fetch_state == AcquisitionFetchState.OK
    assert outcome.account is not None
    assert str(outcome.account.cash_balance) == "1234567"
    assert str(outcome.account.buying_power) == "2000000"
    assert outcome.account.currency == "JPY"


def test_fetch_open_orders_normalizes_orders(tmp_path: Path) -> None:
    body = json.dumps(
        {
            "orders": [
                {
                    "order_id": "20260923-0001",
                    "symbol": "7203",
                    "side": "買い",
                    "quantity": "100株",
                    "status": "執行待ち",
                    "手数料": "55円",
                }
            ]
        },
        ensure_ascii=False,
    ).encode("utf-8")
    url = "https://trade.rakuten-sec.co.jp/web/orders/open/jp"
    transport = ScriptedTransport([_response(body, url=url)])
    service = _build_service(transport, data_dir=tmp_path)
    outcome = service.fetch("open_orders", "jp")
    assert outcome.fetch_state == AcquisitionFetchState.OK
    assert len(outcome.orders) == 1
    assert outcome.orders[0].status.value == "pending"
    assert outcome.detail is not None
    assert outcome.detail.get("fees") == {"20260923-0001": "55円"}


def _order_history_body() -> bytes:
    return json.dumps(
        {
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
        },
        ensure_ascii=False,
    ).encode("utf-8")


def test_fetch_order_history_jp_reaches_catalog(tmp_path: Path) -> None:
    transport = ScriptedTransport(
        [
            _response(
                _order_history_body(), url="https://trade.rakuten-sec.co.jp/web/orders/history/jp"
            )
        ]
    )
    service = _build_service(transport, data_dir=tmp_path)
    outcome = service.fetch("order_history", "jp")
    assert outcome.fetch_state == AcquisitionFetchState.OK
    assert "unknown resource" not in " ".join(outcome.notes)
    assert len(outcome.orders) == 3
    assert {order.status.value for order in outcome.orders} == {"cancelled", "filled", "pending"}


def test_fetch_order_history_us_reaches_catalog(tmp_path: Path) -> None:
    html = (
        "<table><tr><th>注文番号</th><th>銘柄コード</th><th>売買</th><th>注文数量</th><th>状況</th></tr>"
        "<tr><td>cancel-1</td><td>7203</td><td>買い</td><td>1株</td><td>取消</td></tr></table>"
    ).encode()
    transport = ScriptedTransport(
        [
            _response(
                html,
                url="https://trade.rakuten-sec.co.jp/web/us/orders/history/us",
                content_type="text/html",
            )
        ]
    )
    service = _build_service(transport, data_dir=tmp_path)
    outcome = service.fetch("order_history", "us")
    assert outcome.fetch_state == AcquisitionFetchState.OK
    assert "unknown resource" not in " ".join(outcome.notes)
    assert [(order.broker_order_id, order.status.value) for order in outcome.orders] == [
        ("cancel-1", "cancelled")
    ]
    assert outcome.source_url == "https://trade.rakuten-sec.co.jp/web/us/orders/history/us"


_ORDER_HISTORY_DETAIL_KEYS = ("orders", "history", "order_history", "orderHistory")


def _history_detail_body(list_key: str) -> bytes:
    return json.dumps(
        {
            list_key: [
                {
                    "order_id": "H-1",
                    "symbol": "7203",
                    "name": "トヨタ自動車",
                    "side": "買い",
                    "quantity": "100株",
                    "status": "取消",
                    "手数料": "55円",
                }
            ]
        },
        ensure_ascii=False,
    ).encode("utf-8")


@pytest.mark.parametrize("list_key", _ORDER_HISTORY_DETAIL_KEYS)
def test_fetch_order_history_detail_fees_and_names_all_list_keys(
    tmp_path: Path, list_key: str
) -> None:
    transport = ScriptedTransport(
        [
            _response(
                _history_detail_body(list_key),
                url="https://trade.rakuten-sec.co.jp/web/orders/history/jp",
            )
        ]
    )
    service = _build_service(transport, data_dir=tmp_path)
    outcome = service.fetch("order_history", "jp")
    assert outcome.fetch_state == AcquisitionFetchState.OK
    assert outcome.detail is not None
    assert outcome.detail.get("fees") == {"H-1": "55円"}
    assert outcome.detail.get("symbol_names") == {"7203": "トヨタ自動車"}
    assert outcome.detail.get("verified") is False
    assert "unparseable" not in " ".join(outcome.notes)


def test_fetch_order_history_stale_serves_detail_from_cache(tmp_path: Path) -> None:
    # STALE cache 経由でも detail.fees / detail.symbol_names / verified=False が保持される
    clock = {"now": datetime(2026, 9, 23, 12, 0, 0, tzinfo=UTC)}

    def now_fn() -> datetime:
        return clock["now"]

    transport = ScriptedTransport(
        [
            _response(
                _history_detail_body("history"),
                url="https://trade.rakuten-sec.co.jp/web/orders/history/jp",
            )
        ]
    )
    service = _build_service(transport, data_dir=tmp_path, now=now_fn)
    first = service.fetch("order_history", "jp")
    assert first.fetch_state == AcquisitionFetchState.OK

    clock["now"] = clock["now"].replace(minute=2)  # past ttl, within max-stale
    stale = service.fetch("order_history", "jp")
    assert stale.fetch_state == AcquisitionFetchState.STALE
    assert stale.detail is not None
    assert stale.detail.get("fees") == {"H-1": "55円"}
    assert stale.detail.get("symbol_names") == {"7203": "トヨタ自動車"}
    assert stale.detail.get("verified") is False
    assert len(transport.calls) == 1


def test_fetch_order_history_us_tables_detail_fees_not_inflated(tmp_path: Path) -> None:
    # N3: tables パーサ経由の order_history で detail.fees が list-key 族ぶん膨張しない
    html = (
        "<table><tr><th>銘柄コード</th><th>手数料</th></tr>"
        "<tr><td>7203</td><td>55円</td></tr>"
        "<tr><td>6758</td><td>110円</td></tr>"
        "<tr><td>9984</td><td>165円</td></tr></table>"
    ).encode()
    transport = ScriptedTransport(
        [
            _response(
                html,
                url="https://trade.rakuten-sec.co.jp/web/us/orders/history/us",
                content_type="text/html",
            )
        ]
    )
    service = _build_service(transport, data_dir=tmp_path)
    outcome = service.fetch("order_history", "us")
    assert outcome.fetch_state == AcquisitionFetchState.OK
    assert outcome.detail is not None
    fees = outcome.detail.get("fees")
    assert len(fees) == 3
    assert fees == {"row[0]": "55円", "row[1]": "110円", "row[2]": "165円"}
    assert outcome.detail.get("verified") is False


def test_fetch_open_orders_does_not_include_cancelled_and_notes_it(tmp_path: Path) -> None:
    transport = ScriptedTransport(
        [_response(_order_history_body(), url="https://trade.rakuten-sec.co.jp/web/orders/open/jp")]
    )
    service = _build_service(transport, data_dir=tmp_path)
    outcome = service.fetch("open_orders", "jp")
    assert [order.status.value for order in outcome.orders] == ["pending"]
    assert sum("excluded from open_orders" in note for note in outcome.notes) == 2


def test_fetch_executions_normalizes_filled_orders(tmp_path: Path) -> None:
    body = json.dumps(
        {
            "executions": [
                {
                    "order_id": "20260922-0099",
                    "symbol": "6501",
                    "side": "買い",
                    "quantity": "300株",
                    "平均約定単価": "3,800円",
                }
            ]
        },
        ensure_ascii=False,
    ).encode("utf-8")
    url = "https://trade.rakuten-sec.co.jp/web/executions/jp"
    transport = ScriptedTransport([_response(body, url=url)])
    service = _build_service(transport, data_dir=tmp_path)
    outcome = service.fetch("executions", "jp")
    assert outcome.fetch_state == AcquisitionFetchState.OK
    assert len(outcome.orders) == 1
    assert outcome.orders[0].status.value == "filled"
    assert outcome.orders[0].filled_quantity == 300
    assert str(outcome.orders[0].average_fill_price) == "3800"


def test_tables_resource_rides_html_connector(tmp_path: Path) -> None:
    # account/us is catalogued parser_kind="tables" -> rakuten-web-html definition.
    html = (
        "<table><tr><th>項目</th><th>金額</th></tr>"
        "<tr><td>買付余力</td><td>$5,000.00</td></tr>"
        "<tr><td>預り金</td><td>$1,250.50</td></tr></table>"
    ).encode()
    transport = ScriptedTransport(
        [
            _response(
                html,
                url="https://trade.rakuten-sec.co.jp/web/us/account/summary",
                content_type="text/html",
            )
        ]
    )
    service = _build_service(transport, data_dir=tmp_path)
    outcome = service.fetch("account", "us")
    assert outcome.fetch_state == AcquisitionFetchState.OK
    assert outcome.account is not None
    assert outcome.account.currency == "USD"
    assert str(outcome.account.buying_power) == "5000.00"
    assert str(outcome.account.cash_balance) == "1250.50"


def test_list_connectors_shows_single_rakuten_web(tmp_path: Path) -> None:
    transport = ScriptedTransport([])
    service = _build_service(transport, data_dir=tmp_path)
    connectors = service.list_connectors()
    assert [runtime.definition.id for runtime in connectors] == ["rakuten-web"]


def test_get_connector_hides_html_twin(tmp_path: Path) -> None:
    transport = ScriptedTransport([])
    service = _build_service(transport, data_dir=tmp_path)
    assert service.get_connector("rakuten-web") is not None
    assert service.get_connector("rakuten-web-html") is None


def test_auth_check_delegates_to_acquisition(tmp_path: Path) -> None:
    transport = ScriptedTransport([_response()])
    service = _build_service(transport, data_dir=tmp_path)
    outcome = service.auth_check("rakuten-web")
    assert outcome.fetch_state == AcquisitionFetchState.OK
    assert outcome.auth_state == AuthState.AUTHENTICATED
    # auth probe hits the jp account resource URL
    assert transport.calls[0]["resource"] == "web/account/summary"


def test_auth_detector_ignores_auth_path_but_flags_login(tmp_path: Path) -> None:
    # Rakuten serves legitimate pages under URLs containing "auth"; those must
    # NOT be misclassified as login (the default detector's "auth" marker would).
    auth_page = _response(
        url="https://trade.rakuten-sec.co.jp/web/auth/menu",
        body=b'{"ok": true}',
    )
    transport = ScriptedTransport([auth_page])
    service = _build_service(transport, data_dir=tmp_path)
    outcome = service.fetch("positions", "jp")
    assert outcome.fetch_state == AcquisitionFetchState.OK
    assert outcome.auth_state == AuthState.AUTHENTICATED

    login_page = _response(
        url="https://trade.rakuten-sec.co.jp/login",
        body=b"<html>please log in</html>",
    )
    transport2 = ScriptedTransport([login_page])
    service2 = _build_service(transport2, data_dir=tmp_path)
    expired = service2.fetch("positions", "jp")
    assert expired.fetch_state == AcquisitionFetchState.AUTH_EXPIRED
    assert expired.auth_state == AuthState.UNAUTHENTICED


def test_snapshots_and_diff_through_service(tmp_path: Path) -> None:
    body2 = json.dumps(
        {"positions": [{"symbol": "7203", "quantity": "200株"}]}, ensure_ascii=False
    ).encode("utf-8")
    transport = ScriptedTransport([_response(), _response(body2)])
    service = _build_service(transport, data_dir=tmp_path)
    service.fetch("positions", "jp")
    service.fetch("positions", "jp", force_refresh=True)
    history = service.snapshots("rakuten-web", "positions", "jp")
    assert len(history) == 2
    diff = service.diff("rakuten-web", "positions", "jp")
    assert diff is not None
    assert diff.changed is True


def test_snapshots_unknown_resource_returns_empty(tmp_path: Path) -> None:
    transport = ScriptedTransport([])
    service = _build_service(transport, data_dir=tmp_path)
    assert service.snapshots("rakuten-web", "bogus", "jp") == []
    assert service.diff("rakuten-web", "bogus", "jp") is None
