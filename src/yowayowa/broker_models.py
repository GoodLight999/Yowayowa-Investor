from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, Field, model_validator

RAKUTEN_SECURITIES_BROKER = "rakuten-securities"
RAKUTEN_LOGIN_URL_MARKERS: tuple[str, ...] = ("login", "signin", "sign-in")


class BrokerTransport(StrEnum):
    OFFICIAL_API = "official_api"
    AUTHENTICATED_WEB_SESSION = "authenticated_web_session"
    PRIVATE_PROTOCOL = "private_protocol"
    SCRAPER = "scraper"
    LOCAL_PROGRAMMABLE_INTERFACE = "local_programmable_interface"
    UI_AUTOMATION_FALLBACK = "ui_automation_fallback"


class BrokerOrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class BrokerOrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"


class BrokerOrderStatus(StrEnum):
    PREVIEW = "preview"
    ACCEPTED = "accepted"
    PENDING = "pending"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    INACTIVE = "inactive"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


class BrokerCapabilities(BaseModel):
    broker: str
    transport: BrokerTransport
    account_snapshot: bool = False
    positions: bool = False
    orders: bool = False
    order_submission: bool = False
    order_cancel: bool = False
    quotes: bool = False
    scraping: bool = False


class BrokerOrderIntent(BaseModel):
    client_order_id: str = Field(min_length=1, max_length=128)
    symbol: str = Field(min_length=1, max_length=32)
    side: BrokerOrderSide
    quantity: int = Field(gt=0)
    order_type: BrokerOrderType
    limit_price: Decimal | None = Field(default=None, gt=0)
    reference_price: Decimal | None = Field(default=None, gt=0)
    currency: str = Field(default="JPY", min_length=3, max_length=3)
    reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_price(self) -> BrokerOrderIntent:
        if self.order_type == BrokerOrderType.LIMIT and self.limit_price is None:
            raise ValueError("limit_price is required for limit orders")
        return self

    def estimated_notional(self) -> Decimal | None:
        price = self.limit_price or self.reference_price
        if price is None:
            return None
        return price * self.quantity


class BrokerOrderPreview(BaseModel):
    broker: str
    transport: BrokerTransport
    intent: BrokerOrderIntent
    estimated_notional: Decimal | None = None
    currency: str
    warnings: list[str] = Field(default_factory=list)


class BrokerCancelRequest(BaseModel):
    client_order_id: str = Field(min_length=1, max_length=128)
    broker_order_id: str = Field(min_length=1, max_length=128)


class BrokerOrderReceipt(BaseModel):
    broker: str
    client_order_id: str
    transport_order_id: str | None = None
    broker_order_id: str | None = None
    accepted: bool
    status: BrokerOrderStatus
    submitted_at: datetime
    message: str | None = None


class BrokerOrder(BaseModel):
    broker: str
    client_order_id: str | None = None
    transport_order_id: str | None = None
    broker_order_id: str
    symbol: str
    side: BrokerOrderSide
    quantity: int
    filled_quantity: int = 0
    average_fill_price: Decimal | None = None
    status: BrokerOrderStatus


class BrokerPosition(BaseModel):
    broker: str
    symbol: str
    quantity: Decimal
    average_cost: Decimal | None = None
    market_price: Decimal | None = None
    market_value: Decimal | None = None
    unrealized_pnl: Decimal | None = None
    currency: str = "JPY"
    account_type: str | None = None


class BrokerQuote(BaseModel):
    broker: str
    symbol: str
    price: Decimal
    currency: str
    captured_at: datetime


class BrokerAccountSnapshot(BaseModel):
    broker: str
    currency: str
    buying_power: Decimal | None = None
    cash_balance: Decimal | None = None
    captured_at: datetime


class BrokerConnector(Protocol):
    capabilities: BrokerCapabilities

    def preview_order(self, intent: BrokerOrderIntent) -> BrokerOrderPreview: ...

    def submit_order(self, intent: BrokerOrderIntent) -> BrokerOrderReceipt: ...

    def list_orders(self) -> list[BrokerOrder]: ...

    def list_positions(self) -> list[BrokerPosition]: ...

    def quote(self, symbol: str) -> BrokerQuote: ...

    def cancel_order(
        self,
        broker_order_id: str,
        *,
        client_order_id: str,
    ) -> BrokerOrderReceipt: ...

    def account_snapshot(self) -> BrokerAccountSnapshot: ...
