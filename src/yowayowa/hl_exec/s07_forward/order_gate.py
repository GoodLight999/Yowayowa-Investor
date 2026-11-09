# ruff: noqa: RUF002, RUF003 — 日本語の句読点（全角）は本文テキストであり誤字ではない
"""S-07 forward の発注ゲート（テストダブル専用・実送信なし）。

このモジュールは「発注経路に入ってよいか」を判定するだけで、送信先は呼び出し側が
注入した ``unittest.mock`` のテストダブルに限る。署名器・鍵・トランスポート・
ブローカー・アダプタはここにも、ここから呼ばれる先にも存在しない。

安全規則（いずれも fail-closed）:

* 明示フラグが無ければこのゲートは呼ばれない（既定は dry-run）。
* 注入された sender がテストダブルでなければ拒否する。実クライアントは通さない。
* ``unknown`` / ``pending`` / ``missing`` / ``unverified`` / 未指定の注文状態では
  再送しない。
* 取消確認は、照合済みの構造化証拠（``order_id`` / ``cancel_confirmed_at`` /
  ``remaining_qty`` / ``source == "reconciliation"``）だけを ``verified`` として扱う。
  裸の文字列 ``"cancel_confirmed"`` は受入テスト用の合成入力であり、
  ``verified: false`` の合成記録として残す（実取消証拠として扱わない）。
* 送信直前に排他的マーカーを作る。既にあれば再送しない（二重発注を不可能にする）。
"""

from __future__ import annotations

import datetime as dt
import json
import math
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from unittest.mock import Mock

CANCEL_CONFIRMED = "cancel_confirmed"
EVIDENCE_SOURCE = "reconciliation"
#: 再送を禁止する注文状態（大文字小文字は正規化して比較する）。
BLOCKING_STATUSES = frozenset(
    {"", "unknown", "pending", "missing", "unverified", "in_flight", "inflight"}
)


class OrderGateError(ValueError):
    """発注ゲートの前提を満たさない（呼び出し側は fail-closed で停止する）。"""


def require_test_double(sender: object) -> Callable[..., Any]:
    """注入 sender がテストダブルでなければ拒否して返す。

    実クライアント・署名器・アダプタを発注経路へ持ち込まないための構造的な関門。
    ``unittest.mock.Mock`` の系譜だけを通す。CLI は sender を注入しないため、
    CLI からこの関数に到達することは無い。
    """
    if not isinstance(sender, Mock):
        raise OrderGateError(
            "sender must be an injected unittest.mock test double; "
            "no real sender, signer or broker client is accepted"
        )
    return sender


def _status_text(value: object) -> str:
    return value.strip().lower() if isinstance(value, str) else ""


def _blocked(reason: str, evidence: dict[str, Any]) -> dict[str, Any]:
    return {"authorized": False, "reason": reason, "evidence": evidence}


def _verify_cancel_evidence(payload: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """構造化された取消証拠を検証する。欠損・型不正はすべて問題として返す。"""
    problems: list[str] = []
    order_id = payload.get("order_id")
    if not isinstance(order_id, str) or not order_id.strip():
        problems.append("order_id")
    stamp = payload.get("cancel_confirmed_at")
    if not isinstance(stamp, str):
        problems.append("cancel_confirmed_at")
    else:
        try:
            dt.datetime.fromisoformat(stamp)
        except ValueError:
            problems.append("cancel_confirmed_at")
    qty_value: float | None = None
    qty = payload.get("remaining_qty")
    if isinstance(qty, bool) or not isinstance(qty, int | float | str):
        problems.append("remaining_qty")
    else:
        try:
            qty_value = float(qty)
        except (TypeError, ValueError):
            problems.append("remaining_qty")
        else:
            if not math.isfinite(qty_value) or qty_value < 0:
                problems.append("remaining_qty")
    if payload.get("source") != EVIDENCE_SOURCE:
        problems.append("source")
    evidence = {
        "kind": "structured_evidence",
        "verified": not problems,
        "order_id": order_id if isinstance(order_id, str) else None,
        "cancel_confirmed_at": stamp if isinstance(stamp, str) else None,
        "remaining_qty": qty_value,
        "source": payload.get("source") if isinstance(payload.get("source"), str) else None,
        "problems": problems,
    }
    return evidence, problems


def authorize(order_status: object) -> dict[str, Any]:
    """再送可否を判定する。戻り値はそのまま監査記録として保存できる dict。

    ``authorized`` が True でも、送信先になり得るのは :func:`require_test_double`
    を通ったテストダブルだけである。この文字列や dict 自体が実発注を解錠する
    経路は存在しない。
    """
    if order_status is None:
        return _blocked(
            "order_status_missing",
            {"kind": "order_status", "verified": False, "status": None},
        )

    if isinstance(order_status, str):
        status = _status_text(order_status)
        if status == CANCEL_CONFIRMED:
            # 受入テストが渡す合成文字列。実取消証拠ではないため verified=False。
            return {
                "authorized": True,
                "reason": "synthetic_test_assertion",
                "evidence": {
                    "kind": "synthetic_test_only",
                    "verified": False,
                    "status": CANCEL_CONFIRMED,
                    "note": (
                        "caller-supplied string is not real cancellation evidence; "
                        "only an injected test double is invoked"
                    ),
                },
            }
        if status in BLOCKING_STATUSES:
            return _blocked(
                f"order_status_{status or 'missing'}_blocks_resend",
                {"kind": "order_status", "verified": False, "status": status or None},
            )
        return _blocked(
            "order_status_unrecognized",
            {"kind": "order_status", "verified": False, "status": status},
        )

    if isinstance(order_status, Mapping):
        status = _status_text(order_status.get("status"))
        if status != CANCEL_CONFIRMED:
            return _blocked(
                "order_status_blocked_resend",
                {"kind": "order_status", "verified": False, "status": status or None},
            )
        evidence, problems = _verify_cancel_evidence(order_status)
        if problems:
            return _blocked("cancel_evidence_incomplete", evidence)
        return {"authorized": True, "reason": "verified_cancel_evidence", "evidence": evidence}

    return _blocked(
        "order_status_unrecognized",
        {"kind": "order_status", "verified": False, "status": None},
    )


def _encode(payload: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n"
    ).encode()


def _write_all(fd: int, data: bytes) -> None:
    if os.write(fd, data) != len(data):
        raise OSError("short write; manual reconciliation required")
    os.fsync(fd)


def _guard_dir(directory: Path) -> None:
    if directory.is_symlink():
        raise OrderGateError(f"unsafe order journal directory: {directory}")
    directory.mkdir(mode=0o700, exist_ok=True)
    if not directory.is_dir():
        raise OrderGateError(f"unsafe order journal directory: {directory}")


def record_event(directory: Path, day: str, payload: Mapping[str, Any]) -> None:
    """発注経路の判定を追記専用ログに残す（送信の有無にかかわらず記録する）。"""
    _guard_dir(directory)
    path = directory / f"{day}.events.jsonl"
    if path.is_symlink():
        raise OrderGateError(f"unsafe event log: {path}")
    fd = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        _write_all(fd, _encode({"day": day, **payload}))
    finally:
        os.close(fd)


def claim_submission(directory: Path, day: str, payload: Mapping[str, Any]) -> Path:
    """送信直前に排他的マーカーを作る。既にあれば :class:`OrderGateError`。"""
    _guard_dir(directory)
    marker = directory / f"{day}.submitted"
    try:
        fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    except FileExistsError as exc:
        raise OrderGateError(
            f"submission marker already exists for {day}; resend is blocked"
        ) from exc
    try:
        _write_all(fd, _encode({"day": day, **payload}))
    finally:
        os.close(fd)
    return marker
