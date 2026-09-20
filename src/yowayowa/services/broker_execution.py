from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from yowayowa.broker_models import BrokerOrderPreview
from yowayowa.config import Settings


class BrokerExecutionBlocked(RuntimeError):
    pass


@dataclass(frozen=True)
class BrokerExecutionDecision:
    allowed: bool
    reasons: tuple[str, ...]


def evaluate_broker_execution(
    settings: Settings,
    preview: BrokerOrderPreview,
    *,
    orders_submitted_today: int,
) -> BrokerExecutionDecision:
    reasons: list[str] = []
    if settings.mode != "personal":
        reasons.append("broker execution is operator/personal mode only")
    if not settings.private_connectors_enabled:
        reasons.append("private connectors are disabled")
    if not settings.broker_control_enabled:
        reasons.append("broker control is disabled")
    if not settings.broker_live_orders_enabled:
        reasons.append("live broker order submission is not armed")

    notional = preview.estimated_notional
    if notional is None:
        reasons.append("order notional cannot be estimated")
    elif preview.currency.upper() != settings.broker_risk_currency.upper():
        reasons.append(
            "order currency does not match configured broker risk currency; "
            "cross-currency live execution requires an explicit FX-aware gate"
        )
    elif (
        settings.broker_max_single_order_notional is not None
        and notional > Decimal(str(settings.broker_max_single_order_notional))
    ):
        reasons.append("order exceeds configured single-order notional limit")

    if (
        settings.broker_max_orders_per_day is not None
        and orders_submitted_today >= settings.broker_max_orders_per_day
    ):
        reasons.append("daily live-order limit reached")

    return BrokerExecutionDecision(allowed=not reasons, reasons=tuple(reasons))


def require_broker_execution(
    settings: Settings,
    preview: BrokerOrderPreview,
    *,
    orders_submitted_today: int,
) -> None:
    decision = evaluate_broker_execution(
        settings,
        preview,
        orders_submitted_today=orders_submitted_today,
    )
    if not decision.allowed:
        raise BrokerExecutionBlocked("; ".join(decision.reasons))
