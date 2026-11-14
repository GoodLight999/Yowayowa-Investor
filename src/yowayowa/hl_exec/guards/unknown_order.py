# ruff: noqa: RUF002, RUF003 — 日本語の句読点（全角）は本文テキストであり誤字ではない
"""不明注文状態の照合契約（timeout/unknown 時に絶対に再送しない）。

発注は at-most-once である。送信が timeout / 不明応答になった時点で、注文が
取引所に受理されたかどうかは分からない。ここで再送すると二重発注になるため、
本モジュールは **再送経路を持たない**。代わりに既存 cloid を使って

* 未約定注文一覧（open orders）
* 注文照会（cloid 指定）

へ突合し、結果を3値で返す:

==============  ==========================================================
``CONFIRMED``   確定。どちらかの照会で cloid（または oid）と一致した。
``ABSENT``      不在。注文照会が「存在しない」と確定回答した。
``UNRESOLVED``  照合不能。照会失敗・想定外応答で、存在を否定できない。
==============  ==========================================================

``UNRESOLVED`` は :class:`UnknownOrderStateError` として明示的に上位へ返す。
再送で握り潰さない。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any

from yowayowa.hl_exec.guards.cloid import canonicalize_cloid, to_sdk_cloid

# 送信後に受理可否が不明になる transport 例外。ここに載る例外だけを照合へ回す。
AMBIGUOUS_TRANSPORT_ERRORS: tuple[type[BaseException], ...] = (
    TimeoutError,
    ConnectionError,
    OSError,
)

_UNKNOWN_STATUS_VALUES = frozenset({"unknownOid", "unknownCloid"})


class OrderReconciliation(StrEnum):
    """不明注文の照合結果（3値）。"""

    CONFIRMED = "confirmed"
    ABSENT = "absent"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class OrderAttempt:
    """発注1回の結果。``reconciled`` は照合を経たかどうか。"""

    status: OrderReconciliation
    cloid: str
    oid: int | None = None
    response: Mapping[str, Any] | None = None
    reconciled: bool = False
    detail: str = ""


class UnknownOrderStateError(RuntimeError):
    """照合不能。注文の有無を確定できないため、再送せず上位へ返す。"""

    def __init__(self, message: str, *, attempt: OrderAttempt | None = None) -> None:
        super().__init__(message)
        self.attempt = attempt


def _row_cloid(row: Mapping[str, Any]) -> str | None:
    raw = row.get("cloid")
    if isinstance(raw, str):
        return raw.lower()
    to_raw = getattr(raw, "to_raw", None)
    if callable(to_raw):
        value = to_raw()
        if isinstance(value, str):
            return value.lower()
    return None


def _matches(row: object, canonical_cloid: str, oid: int | None) -> bool:
    if not isinstance(row, Mapping):
        return False
    if _row_cloid(row) == canonical_cloid:
        return True
    row_oid = row.get("oid")
    return oid is not None and isinstance(row_oid, int) and row_oid == oid


def _status_verdict(payload: object) -> OrderReconciliation | None:
    """``/info`` の ``orderStatus`` 応答を3値のうち確定2値へ写す。"""
    if not isinstance(payload, Mapping):
        return None
    status = payload.get("status")
    if status in _UNKNOWN_STATUS_VALUES:
        return OrderReconciliation.ABSENT
    if status == "order" and isinstance(payload.get("order"), Mapping):
        return OrderReconciliation.CONFIRMED
    return None


def reconcile_unknown_order(
    *,
    cloid: str,
    oid: int | None = None,
    open_orders: Callable[[], object] | None = None,
    order_status: Callable[[], object] | None = None,
) -> OrderAttempt:
    """既存 cloid を open orders / 注文照会へ突合し、3値で返す（再送しない）。

    どちらかの照会で一致すれば ``CONFIRMED``。注文照会が存在しないと確定回答
    した場合のみ ``ABSENT``。照会不能・想定外応答は ``UNRESOLVED``。
    """
    canonical = canonicalize_cloid(cloid)
    problems: list[str] = []

    if open_orders is not None:
        try:
            rows = open_orders()
        except Exception as exc:  # 照会失敗は「不在」ではなく「照合不能」
            problems.append(f"open_orders raised {type(exc).__name__}: {exc}")
        else:
            if isinstance(rows, Sequence) and not isinstance(rows, (str, bytes)):
                for row in rows:
                    if _matches(row, canonical, oid):
                        return OrderAttempt(
                            status=OrderReconciliation.CONFIRMED,
                            cloid=canonical,
                            oid=oid,
                            reconciled=True,
                            detail="cloid/oid matched an open order",
                        )
            else:
                problems.append(f"open_orders returned {type(rows).__name__}, expected a sequence")
    else:
        problems.append("open_orders lookup is unavailable")

    if order_status is not None:
        try:
            payload = order_status()
        except Exception as exc:
            problems.append(f"order_status raised {type(exc).__name__}: {exc}")
        else:
            verdict = _status_verdict(payload)
            if verdict is OrderReconciliation.CONFIRMED:
                return OrderAttempt(
                    status=OrderReconciliation.CONFIRMED,
                    cloid=canonical,
                    oid=oid,
                    reconciled=True,
                    detail="order status reports the order exists",
                )
            if verdict is OrderReconciliation.ABSENT:
                return OrderAttempt(
                    status=OrderReconciliation.ABSENT,
                    cloid=canonical,
                    oid=oid,
                    reconciled=True,
                    detail="order status definitively reports no such order",
                )
            problems.append("order_status returned an unrecognized payload")
    else:
        problems.append("order_status lookup is unavailable")

    return OrderAttempt(
        status=OrderReconciliation.UNRESOLVED,
        cloid=canonical,
        oid=oid,
        reconciled=True,
        detail="; ".join(problems),
    )


def _classify_submit_response(
    response: object,
) -> tuple[OrderReconciliation, str]:
    """発注応答を「確定」「不在」「不明」へ分類する。"""
    if not isinstance(response, Mapping):
        return OrderReconciliation.UNRESOLVED, f"response is {type(response).__name__}"
    inner = response.get("response")
    data = inner.get("data") if isinstance(inner, Mapping) else None
    statuses = data.get("statuses") if isinstance(data, Mapping) else None
    if not isinstance(statuses, Sequence) or isinstance(statuses, (str, bytes)) or not statuses:
        return OrderReconciliation.UNRESOLVED, "response has no statuses"
    errors = 0
    unknowns = 0
    for status in statuses:
        if isinstance(status, Mapping):
            if "resting" in status or "filled" in status:
                return OrderReconciliation.CONFIRMED, "exchange accepted the order"
            if "error" in status:
                errors += 1
                continue
        unknowns += 1
    if errors and not unknowns:
        return OrderReconciliation.ABSENT, "exchange rejected the order"
    return OrderReconciliation.UNRESOLVED, "exchange response is ambiguous"


def _reconcile_after_ambiguity(
    *,
    canonical_cloid: str,
    open_orders: Callable[[], object] | None,
    order_status: Callable[[], object] | None,
    response: object | None,
    cause: BaseException | None,
) -> OrderAttempt:
    attempt = reconcile_unknown_order(
        cloid=canonical_cloid,
        open_orders=open_orders,
        order_status=order_status,
    )
    if response is not None and isinstance(response, Mapping):
        attempt = replace(attempt, response=response)
    if attempt.status is OrderReconciliation.UNRESOLVED:
        reason = f" ({type(cause).__name__}: {cause})" if cause is not None else ""
        raise UnknownOrderStateError(
            "order state is unresolved"
            f"{reason}; refusing to resubmit automatically: {attempt.detail}",
            attempt=attempt,
        ) from cause
    return attempt


def send_order_once(
    exchange: Any,
    *,
    coin: str,
    is_buy: bool,
    sz: float,
    px: float,
    order_type: Mapping[str, Any],
    cloid: str,
    reduce_only: bool = False,
    open_orders: Callable[[], object] | None = None,
    order_status: Callable[[], object] | None = None,
    ambiguous_exceptions: tuple[type[BaseException], ...] = AMBIGUOUS_TRANSPORT_ERRORS,
) -> OrderAttempt:
    """注文を **1回だけ** 送信し、不明時は照合へ回す（再送しない）。

    cloid は送信前に公式 ``Cloid`` へ変換する。文字列のまま transport へ渡す経路は
    存在しない。timeout/不明応答では :func:`reconcile_unknown_order` の3値を使い、
    ``UNRESOLVED`` は :class:`UnknownOrderStateError` として返す。
    """
    canonical = canonicalize_cloid(cloid)
    sdk_cloid = to_sdk_cloid(canonical)
    try:
        response = exchange.order(
            coin,
            is_buy,
            sz,
            px,
            order_type,
            reduce_only=reduce_only,
            cloid=sdk_cloid,
        )
    except ambiguous_exceptions as exc:
        return _reconcile_after_ambiguity(
            canonical_cloid=canonical,
            open_orders=open_orders,
            order_status=order_status,
            response=None,
            cause=exc,
        )

    status, detail = _classify_submit_response(response)
    if status is OrderReconciliation.CONFIRMED:
        return OrderAttempt(
            status=status,
            cloid=canonical,
            response=response if isinstance(response, Mapping) else None,
            detail=detail,
        )
    if status is OrderReconciliation.ABSENT:
        return OrderAttempt(
            status=status,
            cloid=canonical,
            response=response if isinstance(response, Mapping) else None,
            detail=detail,
        )
    return _reconcile_after_ambiguity(
        canonical_cloid=canonical,
        open_orders=open_orders,
        order_status=order_status,
        response=response,
        cause=None,
    )
