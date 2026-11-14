# ruff: noqa: RUF002, RUF003 — 日本語の句読点（全角）は本文テキストであり誤字ではない
"""SDK境界の防御契約（cloid 型変換・testnet URL allowlist・不明注文の非再送照合）。"""

from yowayowa.hl_exec.guards.cloid import (
    CloidContractError,
    canonicalize_cloid,
    cloid_to_str,
    to_sdk_cloid,
)
from yowayowa.hl_exec.guards.unknown_order import (
    AMBIGUOUS_TRANSPORT_ERRORS,
    OrderAttempt,
    OrderReconciliation,
    UnknownOrderStateError,
    reconcile_unknown_order,
    send_order_once,
)
from yowayowa.hl_exec.guards.urls import (
    CANONICAL_TESTNET_BASE_URL,
    TestnetURLRejected,
    assert_testnet_exchange,
    canonicalize_testnet_url,
)

__all__ = [
    "AMBIGUOUS_TRANSPORT_ERRORS",
    "CANONICAL_TESTNET_BASE_URL",
    "CloidContractError",
    "OrderAttempt",
    "OrderReconciliation",
    "TestnetURLRejected",
    "UnknownOrderStateError",
    "assert_testnet_exchange",
    "canonicalize_cloid",
    "canonicalize_testnet_url",
    "cloid_to_str",
    "reconcile_unknown_order",
    "send_order_once",
    "to_sdk_cloid",
]
