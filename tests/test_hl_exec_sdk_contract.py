"""Run the real optional Hyperliquid SDK through wire construction, offline."""

from types import SimpleNamespace
from typing import Any

import pytest

from yowayowa.hl_exec.client import TESTNET_API_URL, HyperliquidExchangeClient

# The core application does not depend on the optional execution SDK.
# Operational verification installs it in an isolated environment and runs this test.
sdk = pytest.importorskip("hyperliquid.exchange")


@pytest.mark.parametrize("reduce_only", [False, True])
def test_real_sdk_preserves_reduce_only_on_wire(
    monkeypatch: pytest.MonkeyPatch, reduce_only: bool
) -> None:
    # Skip __init__: it fetches metadata. Keep the real order/bulk_orders/serializer.
    exchange = sdk.Exchange.__new__(sdk.Exchange)
    exchange.base_url = TESTNET_API_URL
    exchange.info = SimpleNamespace(name_to_asset=lambda _coin: 0)
    exchange.wallet = None
    exchange.vault_address = None
    exchange.expires_after = None
    actions: list[dict[str, Any]] = []

    def capture(action: dict[str, Any], _signature: Any, _nonce: int) -> dict[str, str]:
        actions.append(action)
        return {"status": "ok"}

    monkeypatch.setattr(sdk, "sign_l1_action", lambda *args: {})
    monkeypatch.setattr(exchange, "_post_action", capture)

    def deny_network(*args: Any, **kwargs: Any) -> None:
        pytest.fail("SDK contract test attempted network access")

    monkeypatch.setattr("socket.socket.connect", deny_network)
    client = HyperliquidExchangeClient("0x" + "a" * 40, "unused", exchange=exchange)
    result = client.order(
        "BTC", False, 0.0002, 75000.0, {"limit": {"tif": "Ioc"}}, reduce_only=reduce_only
    )
    assert result == {"status": "ok"}
    assert len(actions) == 1
    assert actions[0]["orders"][0]["r"] is reduce_only
