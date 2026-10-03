from decimal import Decimal

import pytest

from yowayowa.broker_models import (
    BrokerOrderIntent,
    BrokerOrderPreview,
    BrokerOrderSide,
    BrokerOrderType,
    BrokerTransport,
)
from yowayowa.config import Settings
from yowayowa.services.broker_execution import evaluate_broker_execution


def _preview(notional: str = "300000") -> BrokerOrderPreview:
    intent = BrokerOrderIntent(
        client_order_id="test-1",
        symbol="4755.T",
        side=BrokerOrderSide.BUY,
        quantity=100,
        order_type=BrokerOrderType.LIMIT,
        limit_price=Decimal(notional) / 100,
        currency="JPY",
    )
    return BrokerOrderPreview(
        broker="rakuten-securities",
        transport=BrokerTransport.LOCAL_PROGRAMMABLE_INTERFACE,
        intent=intent,
        estimated_notional=Decimal(notional),
        currency="JPY",
    )


def test_public_mode_forces_private_operator_capabilities_off() -> None:
    settings = Settings(
        mode="public",
        api_token="secret",
        private_connectors_enabled=True,
        scraping_enabled=True,
        broker_control_enabled=True,
        broker_live_orders_enabled=True,
        broker_max_single_order_notional=500000,
        broker_max_orders_per_day=20,
    )

    assert settings.private_connectors_enabled is False
    assert settings.scraping_enabled is False
    assert settings.broker_control_enabled is False
    assert settings.broker_live_orders_enabled is False


def test_live_orders_require_explicit_limits() -> None:
    with pytest.raises(ValueError, match="BROKER_MAX_SINGLE_ORDER_NOTIONAL"):
        Settings(mode="personal", broker_live_orders_enabled=True)


def test_execution_gate_requires_live_arm_switch() -> None:
    settings = Settings(mode="personal")
    decision = evaluate_broker_execution(settings, _preview(), orders_submitted_today=0)

    assert decision.allowed is False
    assert any("not armed" in reason for reason in decision.reasons)


def test_execution_gate_allows_deliberately_armed_order_within_limits() -> None:
    settings = Settings(
        mode="personal",
        broker_live_orders_enabled=True,
        broker_max_single_order_notional=500000,
        broker_max_orders_per_day=20,
    )
    decision = evaluate_broker_execution(settings, _preview(), orders_submitted_today=3)

    assert decision.allowed is True
    assert decision.reasons == ()


def test_execution_gate_blocks_notional_and_daily_limit() -> None:
    settings = Settings(
        mode="personal",
        broker_live_orders_enabled=True,
        broker_max_single_order_notional=250000,
        broker_max_orders_per_day=3,
    )
    decision = evaluate_broker_execution(settings, _preview(), orders_submitted_today=3)

    assert decision.allowed is False
    assert "order exceeds configured single-order notional limit" in decision.reasons
    assert "daily live-order limit reached" in decision.reasons
