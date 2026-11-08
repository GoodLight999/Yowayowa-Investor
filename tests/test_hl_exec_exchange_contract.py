"""Offline checks of the SDK boundary: never retry or drop order semantics."""

from typing import Any

import pytest

from yowayowa.hl_exec.client import HLError, HyperliquidExchangeClient
from yowayowa.hl_exec.killswitch import KillSwitch

ADDRESS = "0x" + "a" * 40


class RecordingExchange:
    def __init__(self, result: Any = None, error: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.result = {"status": "ok"} if result is None else result
        self.error = error

    def order(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append({"args": args, **kwargs})
        if self.error is not None:
            raise self.error
        return self.result


def client(exchange: Any, **kwargs: Any) -> HyperliquidExchangeClient:
    # An injected transport needs no real key and makes no network requests.
    return HyperliquidExchangeClient(ADDRESS, "unused", exchange=exchange, **kwargs)


def place(c: HyperliquidExchangeClient, **kwargs: Any) -> dict[str, Any]:
    return c.order("BTC", False, 0.0002, 75000.0, {"limit": {"tif": "Ioc"}}, **kwargs)


@pytest.mark.parametrize("reduce_only", [False, True])
def test_transport_typeerror_is_propagated_without_resubmission(reduce_only: bool) -> None:
    error = TypeError("failure after possible acceptance")
    exchange = RecordingExchange(error=error)
    with pytest.raises(TypeError) as raised:
        place(client(exchange), reduce_only=reduce_only)
    assert raised.value is error
    assert len(exchange.calls) == 1
    assert exchange.calls[0]["reduce_only"] is reduce_only


@pytest.mark.parametrize("reduce_only", [False, True])
def test_reduce_only_reaches_transport_unchanged(reduce_only: bool) -> None:
    exchange = RecordingExchange()
    assert place(client(exchange), reduce_only=reduce_only) == {"status": "ok"}
    assert len(exchange.calls) == 1
    assert exchange.calls[0]["reduce_only"] is reduce_only


def test_default_order_is_not_reduce_only() -> None:
    exchange = RecordingExchange()
    place(client(exchange))
    assert exchange.calls[0]["reduce_only"] is False


def test_legacy_mock_is_rejected_instead_of_dropping_reduce_only() -> None:
    class LegacyExchange:
        def __init__(self) -> None:
            self.calls = 0

        def order(self, *args: Any, cloid: Any = None) -> dict[str, str]:
            self.calls += 1
            return {"status": "ok"}

    exchange = LegacyExchange()
    with pytest.raises(TypeError, match="reduce_only"):
        place(client(exchange), reduce_only=True)
    assert exchange.calls == 0


def test_killswitch_blocks_reduce_only_order_before_transport(tmp_path: Any) -> None:
    ks = KillSwitch(tmp_path / "kill")
    ks.engage(reason="offline test")
    exchange = RecordingExchange()
    with pytest.raises(RuntimeError):
        place(client(exchange, killswitch=ks), reduce_only=True)
    assert exchange.calls == []


def test_malformed_response_is_not_retried() -> None:
    exchange = RecordingExchange(result=[])
    with pytest.raises(HLError, match="unexpected exchange order response"):
        place(client(exchange), reduce_only=True)
    assert len(exchange.calls) == 1
