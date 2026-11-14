"""Offline checks of the SDK boundary: never retry or drop order semantics."""

from typing import Any

import pytest

from yowayowa.hl_exec.client import TESTNET_API_URL, HLError, HyperliquidExchangeClient
from yowayowa.hl_exec.killswitch import KillSwitch

ADDRESS = "0x" + "a" * 40


class RecordingExchange:
    base_url = TESTNET_API_URL

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
        base_url = TESTNET_API_URL

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


@pytest.mark.parametrize(
    "url",
    [
        "https://api.hyperliquid.xyz",
        "https://api.hyperliquid-testnet.xyz.attacker.invalid",
        "https://attacker.invalid/api.hyperliquid-testnet.xyz",
        "http://api.hyperliquid-testnet.xyz",
        "https://api.hyperliquid-testnet.xyz/evil",
    ],
)
def test_rejects_noncanonical_testnet_base_url(url: str) -> None:
    with pytest.raises(ValueError, match="testnet endpoint"):
        client(RecordingExchange(), base_url=url)


def test_injected_exchange_must_itself_be_exact_testnet() -> None:
    exchange = RecordingExchange()
    exchange.base_url = "https://api.hyperliquid.xyz"
    with pytest.raises(ValueError, match="injected exchange"):
        client(exchange)


def test_order_converts_cloid_str_to_official_sdk_cloid() -> None:
    sdk = pytest.importorskip("hyperliquid.utils.types")
    exchange = RecordingExchange()
    place(client(exchange), cloid="0x" + "ab" * 16)
    assert isinstance(exchange.calls[0]["cloid"], sdk.Cloid)
    assert str(exchange.calls[0]["cloid"]) == "0x" + "ab" * 16
