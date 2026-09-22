from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin, urlsplit

from yowayowa.acquisition.models import AcquisitionFetchState
from yowayowa.acquisition.transport import (
    PrivateAcquisitionError,
    TransportResponse,
)
from yowayowa.broker_models import (
    BrokerAccountSnapshot,
    BrokerOrder,
    BrokerOrderSide,
    BrokerOrderStatus,
    BrokerPosition,
)

"""Rakuten Securities web read-side domain layer.

Pure code + data only: no playwright import here (browser wiring happens in
api/deps.py behind an injected fetch callable) and no side effects, so every
parser/normalizer is unit-testable without a session or network.

URL PROVENANCE NOTICE
    Every URL in ``RAKUTEN_WEB_RESOURCE_CATALOG`` is an UNVERIFIED INITIAL
    ASSUMPTION authored before any real session. Confirm the actual XHR/HTML
    URLs with browser devtools during the first real operator session, update
    the catalog, and flip ``verified=True`` (docs/RAKUTEN_WEB_SESSION.md,
    step 5). Until then the URLs must not be treated as confirmed fact.
"""

RAKUTEN_WEB_CONNECTOR_ID = "rakuten-web"
RAKUTEN_WEB_HTML_CONNECTOR_ID = "rakuten-web-html"
RAKUTEN_SECURITIES_BROKER = "rakuten-securities"
RAKUTEN_WEB_BASE_URL = "https://trade.rakuten-sec.co.jp/"
RAKUTEN_ALLOWED_HOSTS = ("www.rakuten-sec.co.jp", "trade.rakuten-sec.co.jp")
RAKUTEN_RESOURCES: tuple[str, ...] = ("account", "positions", "open_orders", "executions")
RAKUTEN_MARKETS: tuple[str, ...] = ("jp", "us")


# ---------------------------------------------------------------------------
# Resource catalog
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RakutenResourceEntry:
    """One catalogued Rakuten web resource with versioned parse assumptions."""

    resource: str  # "account" | "positions" | "open_orders" | "executions"
    market: str  # "jp" | "us"
    url: str  # relative to RAKUTEN_WEB_BASE_URL (or absolute allowed-host URL)
    parser_kind: str  # "json" | "tables"
    parser_version: str
    schema_version: str
    verified: bool


# IMPORTANT: 以下の URL は実機未検証の「初期仮定」である (verified=False)。
# 初回実セッション時に devtools で実際の XHR/HTML URL を確認し、この catalog を
# 更新して verified=True にすること (docs/RAKUTEN_WEB_SESSION.md 手順5)。
# 検証が済むまで、これらの URL を事実として扱ってはならない。
RAKUTEN_WEB_RESOURCE_CATALOG: dict[tuple[str, str], RakutenResourceEntry] = {
    ("account", "jp"): RakutenResourceEntry(
        resource="account",
        market="jp",
        url="web/account/summary",
        parser_kind="json",
        parser_version="rakuten-json-v1",
        schema_version="rakuten-web-v1",
        verified=False,
    ),
    ("account", "us"): RakutenResourceEntry(
        resource="account",
        market="us",
        url="web/us/account/summary",
        parser_kind="tables",
        parser_version="rakuten-tables-v1",
        schema_version="rakuten-web-tables-v1",
        verified=False,
    ),
    ("positions", "jp"): RakutenResourceEntry(
        resource="positions",
        market="jp",
        url="web/positions/jp",
        parser_kind="json",
        parser_version="rakuten-json-v1",
        schema_version="rakuten-web-v1",
        verified=False,
    ),
    ("positions", "us"): RakutenResourceEntry(
        resource="positions",
        market="us",
        url="web/positions/us",
        parser_kind="json",
        parser_version="rakuten-json-v1",
        schema_version="rakuten-web-v1",
        verified=False,
    ),
    ("open_orders", "jp"): RakutenResourceEntry(
        resource="open_orders",
        market="jp",
        url="web/orders/open/jp",
        parser_kind="json",
        parser_version="rakuten-json-v1",
        schema_version="rakuten-web-v1",
        verified=False,
    ),
    ("open_orders", "us"): RakutenResourceEntry(
        resource="open_orders",
        market="us",
        url="web/orders/open/us",
        parser_kind="tables",
        parser_version="rakuten-tables-v1",
        schema_version="rakuten-web-tables-v1",
        verified=False,
    ),
    ("executions", "jp"): RakutenResourceEntry(
        resource="executions",
        market="jp",
        url="web/executions/jp",
        parser_kind="json",
        parser_version="rakuten-json-v1",
        schema_version="rakuten-web-v1",
        verified=False,
    ),
    ("executions", "us"): RakutenResourceEntry(
        resource="executions",
        market="us",
        url="web/executions/us",
        parser_kind="json",
        parser_version="rakuten-json-v1",
        schema_version="rakuten-web-v1",
        verified=False,
    ),
}


def lookup_rakuten_resource(resource: str, market: str) -> RakutenResourceEntry | None:
    return RAKUTEN_WEB_RESOURCE_CATALOG.get((resource, market))


def rakuten_connector_id_for(entry: RakutenResourceEntry) -> str:
    """User-facing catalog dispatch: tables resources ride the html connector."""
    if entry.parser_kind == "tables":
        return RAKUTEN_WEB_HTML_CONNECTOR_ID
    return RAKUTEN_WEB_CONNECTOR_ID


# ---------------------------------------------------------------------------
# Transport wrapper (read-only, host-checked)
# ---------------------------------------------------------------------------

FetchUrlCallable = Callable[..., TransportResponse]


class RakutenWebFetchTransport:
    """Read-only, host-checked transport for the Rakuten web session.

    Satisfies the ``SessionTransport`` protocol. The constructor receives a
    fetch callable that gets an already-resolved absolute URL: production
    wiring wraps the operator's persistent browser session lazily
    (api/deps.py), tests inject a scripted fetch. Any non-GET method is
    rejected so this connector can never issue a state-changing request.
    """

    def __init__(
        self,
        *,
        fetch: FetchUrlCallable,
        base_url: str = RAKUTEN_WEB_BASE_URL,
    ) -> None:
        self._fetch = fetch
        self._base_url = base_url if base_url.endswith("/") else base_url + "/"

    def fetch(
        self,
        method: str,
        resource: str,
        *,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        data: object | None = None,
    ) -> TransportResponse:
        if method.upper() != "GET":
            raise PrivateAcquisitionError(
                AcquisitionFetchState.FAILED,
                "read-only connector",
            )
        return self._fetch("GET", self._resolve_url(resource), params=params, headers=headers)

    def _resolve_url(self, resource: str) -> str:
        parts = urlsplit(resource)
        if parts.scheme or parts.netloc:
            host = (parts.hostname or "").lower()
            if host not in RAKUTEN_ALLOWED_HOSTS:
                raise PrivateAcquisitionError(
                    AcquisitionFetchState.FAILED,
                    f"host not allowed: {host}",
                )
            return resource
        return urljoin(self._base_url, resource)


# ---------------------------------------------------------------------------
# Japanese statement amount parsing
# ---------------------------------------------------------------------------

_NEGATIVE_MARK = "\u25b2"  # filled triangle used by Japanese statements for negatives
_FULLWIDTH_DIGITS = {
    chr(code): str(digit) for code, digit in zip(range(0xFF10, 0xFF1A), range(10), strict=True)
}
_AMOUNT_TRANSLATION = str.maketrans(
    {
        **_FULLWIDTH_DIGITS,
        "\uff0e": ".",  # fullwidth full stop
        "\uff0c": ",",  # fullwidth comma
        "\uff0d": "-",  # fullwidth hyphen-minus
        "\uff0b": "+",  # fullwidth plus sign
        "\u2212": "-",  # minus sign
    }
)
_JPY_TOKENS = ("\u5186",)  # 円
_USD_TOKENS = ("$", "\u7c73\u30c9\u30eb")  # 米ドル
_QUANTITY_TOKENS = ("\u682a", "\u53e3")  # 株, 口
_RATE_TOKENS = ("%",)  # maintenance-rate style percentages (kept as plain decimals)


def _parse_decimal(text: str | None, *, unit_tokens: tuple[str, ...]) -> Decimal | None:
    if text is None:
        return None
    cleaned = text.strip().translate(_AMOUNT_TRANSLATION)
    if not cleaned:
        return None
    cleaned = cleaned.replace(",", "")
    negative = False
    while cleaned[:1] in (_NEGATIVE_MARK, "-", "+"):
        if cleaned[0] in (_NEGATIVE_MARK, "-"):
            negative = True
        cleaned = cleaned[1:].strip()
    for token in (*unit_tokens, *_RATE_TOKENS):
        cleaned = cleaned.replace(token, "")
    cleaned = cleaned.strip()
    if not cleaned:
        return None
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    if not value.is_finite():
        return None
    return -value if negative else value


def parse_jpy_amount(text: str | None) -> Decimal | None:
    """Parse a Japanese JPY display string; unparseable input yields None."""
    return _parse_decimal(text, unit_tokens=_JPY_TOKENS)


def parse_usd_amount(text: str | None) -> Decimal | None:
    """Parse a USD display string; JPY-looking input yields None (no mixing)."""
    return _parse_decimal(text, unit_tokens=_USD_TOKENS)


def parse_quantity(text: str | None) -> Decimal | None:
    """Parse a quantity string with 株/口 units; unparseable input yields None."""
    return _parse_decimal(text, unit_tokens=_QUANTITY_TOKENS)


AmountParser = Callable[[str | None], Decimal | None]


def _parser_for_currency(currency: str) -> AmountParser:
    if currency.upper() == "USD":
        return parse_usd_amount
    return parse_jpy_amount


def _coerce_decimal(value: object | None, parser: AmountParser) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value)) if math.isfinite(value) else None
    if isinstance(value, str):
        return parser(value)
    return None


# ---------------------------------------------------------------------------
# Generic payload helpers (JSON/XHR shape and tables-parser shape)
# ---------------------------------------------------------------------------

_SYMBOL_KEYS = (
    "symbol",
    "\u8a3c\u5238\u30b3\u30fc\u30c9",
    "\u9298\u67c4\u30b3\u30fc\u30c9",
    "\u30b3\u30fc\u30c9",
    "ticker",
)
_CURRENCY_KEYS = ("currency", "\u901a\u8ca8", "\u901a\u8ca8\u5358\u4f4d")
_CURRENCY_MAP = {
    "JPY": "JPY",
    "USD": "USD",
    "\u5186": "JPY",
    "\u7c73\u30c9\u30eb": "USD",
    "US\u30c9\u30eb": "USD",
}
_ACCOUNT_TYPE_KEYS = (
    "account_type",
    "\u53e3\u5ea7",
    "\u53e3\u5ea7\u533a\u5206",
    "\u9810\u304b\u308a",
)
_ACCOUNT_TYPE_MAP = {
    "cash": "cash",
    "margin": "margin",
    "credit": "margin",
    "general": "cash",
    "specific": "cash",
    "\u73fe\u7269": "cash",
    "\u4e00\u822c": "cash",
    "\u7279\u5b9a": "cash",
    "\u4fe1\u7528": "margin",
}
_TIMESTAMP_KEYS = (
    "as_of",
    "asOf",
    "captured_at",
    "timestamp",
    "base_datetime",
    "\u57fa\u6e96\u65e5\u6642",
    "\u57fa\u6e96\u65e5",
)
_VALUE_KEYS = ("\u91d1\u984d", "\u5024", "\u6570\u5024", "value", "amount", "Value")

_POSITION_LIST_KEYS = ("positions", "rows", "list")
_ORDER_LIST_KEYS = ("orders", "open_orders", "rows", "list")
_EXECUTION_LIST_KEYS = ("executions", "fills", "deals", "rows", "list")

_POSITION_QUANTITY_KEYS = ("quantity", "\u6570\u91cf", "\u4fdd\u6709\u6570\u91cf", "qty")
_POSITION_AVERAGE_COST_KEYS = (
    "average_cost",
    "\u5e73\u5747\u53d6\u5f97\u5358\u4fa1",
    "\u53d6\u5f97\u5358\u4fa1",
    "\u5e73\u5747\u5358\u4fa1",
)
_POSITION_MARKET_PRICE_KEYS = ("market_price", "\u73fe\u5728\u5024", "\u8a55\u4fa1\u5358\u4fa1")
_POSITION_MARKET_VALUE_KEYS = ("market_value", "\u8a55\u4fa1\u984d", "\u8a55\u4fa1\u91d1\u984d")
_POSITION_PNL_KEYS = (
    "unrealized_pnl",
    "\u8a55\u4fa1\u640d\u76ca",
    "\u8a55\u4fa1\u640d\u76ca\u984d",
)
_POSITION_NAME_KEYS = ("name", "\u9298\u67c4\u540d", "\u9298\u67c4")

_ACCOUNT_BUYING_KEYS = (
    "buying_power",
    "buyingPower",
    "buying power",
    "\u53d6\u5f15\u4f59\u529b",
    "\u8cb7\u4ed8\u4f59\u529b",
    "\u4f59\u529b",
)
_ACCOUNT_CASH_KEYS = (
    "cash_balance",
    "cashBalance",
    "cash balance",
    "\u9810\u308a\u91d1",
    "\u9810\u304b\u308a\u91d1",
    "\u73fe\u91d1\u6b8b\u9ad8",
    "\u6b8b\u9ad8",
)

_ORDER_ID_KEYS = ("order_id", "orderId", "\u6ce8\u6587\u756a\u53f7", "\u53d7\u4ed8\u756a\u53f7")
_SIDE_KEYS = ("side", "\u58f2\u8cb7", "\u58f2\u8cb7\u533a\u5206", "trade")
_SIDE_MAP = {
    "buy": "buy",
    "sell": "sell",
    "b": "buy",
    "s": "sell",
    "\u8cfc\u5165": "buy",
    "\u8cb7\u3044": "buy",
    "\u8cb7": "buy",
    "\u58f2\u5374": "sell",
    "\u58f2\u308a": "sell",
    "\u58f2": "sell",
}
_ORDER_QUANTITY_KEYS = ("quantity", "\u6ce8\u6587\u6570\u91cf", "\u6570\u91cf", "qty")
_STATUS_KEYS = (
    "status",
    "\u72b6\u614b",
    "\u72b6\u6cc1",
    "\u6ce8\u6587\u72b6\u614b",
    "\u7269\u5f8a",
)
_STATUS_MAP = {
    "preview": "preview",
    "accepted": "accepted",
    "pending": "pending",
    "partially_filled": "partially_filled",
    "filled": "filled",
    "cancelled": "cancelled",
    "cancel": "cancelled",
    "inactive": "inactive",
    "rejected": "rejected",
    "\u57f7\u884c\u5f85\u3061": "pending",
    "\u5f85\u6a5f": "pending",
    "\u4e00\u90e8\u7d04\u5b9a": "partially_filled",
    "\u7d04\u5b9a": "filled",
    "\u53d6\u6d88": "cancelled",
    "\u5931\u52b9": "inactive",
    "\u62d2\u5426": "rejected",
}
_FILLED_QUANTITY_KEYS = (
    "filled_quantity",
    "filledQuantity",
    "executed_quantity",
    "\u7d04\u5b9a\u6570\u91cf",
    "filled",
)
_ORDER_FILL_PRICE_KEYS = (
    "average_fill_price",
    "avg_fill_price",
    "fill_price",
    "average_price",
    "\u5e73\u5747\u7d04\u5b9a\u5358\u4fa1",
    "\u7d04\u5b9a\u5358\u4fa1",
    "\u5e73\u5747\u6210\u4ea4\u5358\u4fa1",
    "exec_price",
)
_FEE_KEYS = ("fee", "fees", "commission", "\u624b\u6570\u6599", "\u8af8\u8cbb\u7528")
_MARGIN_KEYS = (
    ("margin_deposit", "\u62d8\u675f\u4fdd\u8a3c\u91d1", "\u8a3c\u62e0\u91d1\u62d8\u675f"),
    ("maintenance_rate", "\u7dad\u6301\u7387"),
    ("margin_positions", "\u5efa\u7389", "\u5efa\u7389\u660e\u7d30"),
)


def _get(row: Mapping[str, object], keys: tuple[str, ...]) -> object | None:
    for key in keys:
        if key in row:
            return row[key]
    return None


def _symbol_of(row: Mapping[str, object]) -> str | None:
    value = _get(row, _SYMBOL_KEYS)
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return value.strip() or None
    return None


def _currency_of(row: Mapping[str, object], default_currency: str) -> str:
    value = _get(row, _CURRENCY_KEYS)
    if isinstance(value, str):
        return _CURRENCY_MAP.get(value.strip(), default_currency)
    return default_currency


def _account_type_of(row: Mapping[str, object]) -> str | None:
    value = _get(row, _ACCOUNT_TYPE_KEYS)
    if isinstance(value, str):
        return _ACCOUNT_TYPE_MAP.get(value.strip())
    return None


def _table_row_maps(payload: Mapping[str, object]) -> list[Mapping[str, object]]:
    tables = payload.get("tables")
    maps: list[Mapping[str, object]] = []
    if isinstance(tables, list):
        for table in tables:
            if not isinstance(table, dict):
                continue
            rows = table.get("rows")
            if isinstance(rows, list):
                maps.extend(row for row in rows if isinstance(row, dict))
    return maps


def _payload_rows(
    payload: Mapping[str, object],
    list_keys: tuple[str, ...],
) -> list[dict[str, object]]:
    """Row dicts from JSON list fields and/or tables-parser output."""
    rows: list[dict[str, object]] = []
    for key in list_keys:
        value = payload.get(key)
        if isinstance(value, list):
            rows.extend(item for item in value if isinstance(item, dict))
    rows.extend(dict(row) for row in _table_row_maps(payload))
    return rows


def _find_amount(
    maps: list[Mapping[str, object]],
    candidates: tuple[str, ...],
    *,
    currency: str,
) -> Decimal | None:
    """Locate an amount in horizontal mappings first, then vertical tables."""
    parser = _parser_for_currency(currency)
    for mapping in maps:
        for key in candidates:
            if key in mapping:
                value = _coerce_decimal(mapping[key], parser)
                if value is not None:
                    return value
    for mapping in maps:
        for label_key, label in mapping.items():
            if not (isinstance(label, str) and label.strip() in candidates):
                continue
            for value_key in _VALUE_KEYS:
                if value_key != label_key and value_key in mapping:
                    value = _coerce_decimal(mapping[value_key], parser)
                    if value is not None:
                        return value
    return None


def _row_amount(
    row: Mapping[str, object],
    keys: tuple[str, ...],
    *,
    currency: str,
) -> Decimal | None:
    parser = _parser_for_currency(currency)
    return _coerce_decimal(_get(row, keys), parser)


def _currency_for_market(market: str) -> str:
    return "JPY" if market == "jp" else "USD"


def _captured_at(payload: Mapping[str, object]) -> datetime:
    """Payload timestamp when present; otherwise normalization time (UTC now).

    The fallback is an event time for the required model field, not financial
    data; payloads without a timestamp are still flagged via outcome notes at
    the service layer when normalization drops information.
    """
    value = _get(payload, _TIMESTAMP_KEYS)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Normalizers: AcquisitionOutcome payload dicts -> generic broker models
# ---------------------------------------------------------------------------


def normalize_account(payload: Mapping[str, object], *, market: str) -> BrokerAccountSnapshot:
    """Normalize an account payload into a BrokerAccountSnapshot.

    buying_power / cash_balance stay None when absent or unparseable; currency
    follows the market (JPY/USD) and is never converted or mixed.
    """
    currency = _currency_for_market(market)
    maps: list[Mapping[str, object]] = [payload, *_table_row_maps(payload)]
    return BrokerAccountSnapshot(
        broker=RAKUTEN_SECURITIES_BROKER,
        currency=currency,
        buying_power=_find_amount(maps, _ACCOUNT_BUYING_KEYS, currency=currency),
        cash_balance=_find_amount(maps, _ACCOUNT_CASH_KEYS, currency=currency),
        captured_at=_captured_at(payload),
    )


def normalize_positions(
    payload: Mapping[str, object],
    *,
    market: str,
) -> tuple[list[BrokerPosition], list[str]]:
    """Normalize positions; rows without parseable quantity are skipped+noted."""
    default_currency = _currency_for_market(market)
    notes: list[str] = []
    positions: list[BrokerPosition] = []
    for index, row in enumerate(_payload_rows(payload, _POSITION_LIST_KEYS)):
        symbol = _symbol_of(row)
        if symbol is None:
            notes.append(f"positions[{index}]: missing symbol; row skipped")
            continue
        quantity = _coerce_decimal(_get(row, _POSITION_QUANTITY_KEYS), parse_quantity)
        if quantity is None:
            notes.append(
                f"positions[{index}] {symbol}: unparseable quantity; row skipped "
                "(missing data is not zero)"
            )
            continue
        currency = _currency_of(row, default_currency)
        positions.append(
            BrokerPosition(
                broker=RAKUTEN_SECURITIES_BROKER,
                symbol=symbol,
                quantity=quantity,
                average_cost=_row_amount(row, _POSITION_AVERAGE_COST_KEYS, currency=currency),
                market_price=_row_amount(row, _POSITION_MARKET_PRICE_KEYS, currency=currency),
                market_value=_row_amount(row, _POSITION_MARKET_VALUE_KEYS, currency=currency),
                unrealized_pnl=_row_amount(row, _POSITION_PNL_KEYS, currency=currency),
                currency=currency,
                account_type=_account_type_of(row),
            )
        )
    return positions, notes


def _normalize_order_like(
    payload: Mapping[str, object],
    *,
    market: str,
    list_keys: tuple[str, ...],
    filled_defaults_to_quantity: bool,
) -> tuple[list[BrokerOrder], list[str]]:
    default_currency = _currency_for_market(market)
    notes: list[str] = []
    orders: list[BrokerOrder] = []
    for index, row in enumerate(_payload_rows(payload, list_keys)):
        label = f"orders[{index}]"
        symbol = _symbol_of(row)
        side_raw = _get(row, _SIDE_KEYS)
        side = None
        if isinstance(side_raw, str):
            side = _SIDE_MAP.get(side_raw.strip().lower()) or _SIDE_MAP.get(side_raw.strip())
        if symbol is None or side is None:
            notes.append(f"{label}: missing symbol or side; row skipped")
            continue
        label = f"{label} {symbol}"
        quantity = _coerce_decimal(_get(row, _ORDER_QUANTITY_KEYS), parse_quantity)
        if quantity is None or quantity != quantity.to_integral_value():
            notes.append(f"{label}: unparseable or fractional quantity; row skipped")
            continue
        order_id_raw = _get(row, _ORDER_ID_KEYS)
        broker_order_id = str(order_id_raw).strip() if order_id_raw is not None else ""
        if not broker_order_id:
            notes.append(f"{label}: missing order number; broker_order_id left empty")
        filled_raw = _get(row, _FILLED_QUANTITY_KEYS)
        filled = _coerce_decimal(filled_raw, parse_quantity)
        if filled is not None and filled != filled.to_integral_value():
            filled = None
        if filled is None and filled_defaults_to_quantity:
            # Execution rows carry the executed quantity as the quantity.
            filled = quantity
        elif filled is None and filled_raw is not None:
            notes.append(f"{label}: unparseable filled quantity; treated as 0")
        status_raw = _get(row, _STATUS_KEYS)
        status_value = str(status_raw).strip().lower() if isinstance(status_raw, str) else ""
        status = (
            BrokerOrderStatus(_STATUS_MAP[status_value])
            if status_value in _STATUS_MAP
            else BrokerOrderStatus.UNKNOWN
        )
        if filled_defaults_to_quantity:
            status = BrokerOrderStatus.FILLED
        currency = _currency_of(row, default_currency)
        orders.append(
            BrokerOrder(
                broker=RAKUTEN_SECURITIES_BROKER,
                broker_order_id=broker_order_id,
                symbol=symbol,
                side=BrokerOrderSide(side),
                quantity=int(quantity),
                filled_quantity=int(filled) if filled is not None else 0,
                average_fill_price=_row_amount(row, _ORDER_FILL_PRICE_KEYS, currency=currency),
                status=status,
            )
        )
    return orders, notes


def normalize_open_orders(
    payload: Mapping[str, object],
    *,
    market: str,
) -> tuple[list[BrokerOrder], list[str]]:
    """Normalize open orders; fees are not in BrokerOrder and go to detail."""
    return _normalize_order_like(
        payload,
        market=market,
        list_keys=_ORDER_LIST_KEYS,
        filled_defaults_to_quantity=False,
    )


def normalize_executions(
    payload: Mapping[str, object],
    *,
    market: str,
) -> tuple[list[BrokerOrder], list[str]]:
    """Normalize executions as FILLED orders; fees/settlement go to detail."""
    return _normalize_order_like(
        payload,
        market=market,
        list_keys=_EXECUTION_LIST_KEYS,
        filled_defaults_to_quantity=True,
    )


# ---------------------------------------------------------------------------
# Detail extraction (information the generic models do not carry)
# ---------------------------------------------------------------------------


def summarize_payload(payload: Mapping[str, object]) -> dict[str, object]:
    """Compact shape summary of a raw payload (never the full raw data)."""
    summary: dict[str, object] = {}
    tables = payload.get("tables")
    if isinstance(tables, list):
        summary["tables"] = [
            {"headers": table.get("headers"), "rows": len(table.get("rows") or [])}
            for table in tables
            if isinstance(table, dict)
        ]
        return summary
    summary["keys"] = sorted(payload.keys())
    for key, value in payload.items():
        if isinstance(value, list):
            summary[f"{key}_count"] = len(value)
    return summary


def extract_symbol_names(payload: Mapping[str, object]) -> dict[str, str]:
    """Symbol -> display name pairs for detail storage (names are not modeled)."""
    names: dict[str, str] = {}
    rows = [
        *_payload_rows(payload, _POSITION_LIST_KEYS),
        *_payload_rows(payload, _ORDER_LIST_KEYS),
        *_payload_rows(payload, _EXECUTION_LIST_KEYS),
    ]
    for row in rows:
        symbol = _symbol_of(row)
        name = _get(row, _POSITION_NAME_KEYS)
        if symbol is not None and isinstance(name, str) and name.strip():
            names[symbol] = name.strip()
    return names


def extract_fees(payload: Mapping[str, object]) -> dict[str, str] | None:
    """Raw fee strings keyed by order number (or row index); None when absent."""
    fees: dict[str, str] = {}
    rows = [
        *_payload_rows(payload, _ORDER_LIST_KEYS),
        *_payload_rows(payload, _EXECUTION_LIST_KEYS),
    ]
    for index, row in enumerate(rows):
        fee = _get(row, _FEE_KEYS)
        if fee is None:
            continue
        order_id_raw = _get(row, _ORDER_ID_KEYS)
        order_id = str(order_id_raw).strip() if order_id_raw is not None else ""
        key = order_id or f"row[{index}]"
        fees[key] = str(fee)
    return fees or None


def extract_margin_state(payload: Mapping[str, object]) -> dict[str, object] | None:
    """Margin/collateral fields the generic models do not carry; None if absent."""
    result: dict[str, object] = {}
    maps: list[Mapping[str, object]] = [payload, *_table_row_maps(payload)]
    for keys in _MARGIN_KEYS:
        value = _find_amount(maps, keys, currency="JPY")
        if value is not None:
            result[keys[0]] = value
            continue
        for mapping in maps:
            raw = _get(mapping, keys)
            if raw is not None:
                result[keys[0]] = raw
                break
    return result or None


__all__ = [
    "RAKUTEN_ALLOWED_HOSTS",
    "RAKUTEN_MARKETS",
    "RAKUTEN_RESOURCES",
    "RAKUTEN_SECURITIES_BROKER",
    "RAKUTEN_WEB_BASE_URL",
    "RAKUTEN_WEB_CONNECTOR_ID",
    "RAKUTEN_WEB_HTML_CONNECTOR_ID",
    "RAKUTEN_WEB_RESOURCE_CATALOG",
    "FetchUrlCallable",
    "RakutenResourceEntry",
    "RakutenWebFetchTransport",
    "extract_fees",
    "extract_margin_state",
    "extract_symbol_names",
    "lookup_rakuten_resource",
    "normalize_account",
    "normalize_executions",
    "normalize_open_orders",
    "normalize_positions",
    "parse_jpy_amount",
    "parse_quantity",
    "parse_usd_amount",
    "rakuten_connector_id_for",
    "summarize_payload",
]
