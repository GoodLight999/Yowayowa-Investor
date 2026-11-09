# ruff: noqa: RUF002, RUF003 — 日本語の句読点（全角）は本文テキストであり誤字ではない
"""cloid 文字列 ↔ 公式SDK ``Cloid`` 型の境界契約（fail-closed）。

公式 ``hyperliquid.utils.types.Cloid`` は前方 ``0x`` と全長34文字だけを検証し、
16進文字かどうか・大文字小文字の正規化・往復一致は検証しない。そのまま
文字列を SDK へ渡すと、不正な cloid が wire へ流れる余地が残る。

本モジュールは次を契約として固定する:

* 入力は ``str`` のみ（``bytes``・``int``・``Cloid`` インスタンスは拒否）。
* 形式は ``^0x[0-9a-fA-F]{32}$``（``0x`` + 16バイト = 32桁の16進）。
* 正規化は「16進部分の小文字化」だけ。``0X`` 接頭辞・空白・記号は拒否。
* 変換結果は公式 ``Cloid`` で、``str()``/``to_raw()`` が正規形と一致することを
  その場で再検証する（往復一致）。
* 不正入力は :class:`CloidContractError`（``ValueError`` 派生）で即時停止。
"""

from __future__ import annotations

import re
from typing import Any

CLOID_PREFIX = "0x"
CLOID_HEX_DIGITS = 32
CLOID_TOTAL_LENGTH = len(CLOID_PREFIX) + CLOID_HEX_DIGITS

_CLOID_RE = re.compile(r"^0x[0-9a-fA-F]{32}$")


class CloidContractError(ValueError):
    """cloid が境界契約を満たさない（送信前に停止する）。"""


def canonicalize_cloid(value: object) -> str:
    """cloid 文字列を検証し、正規形（``0x`` + 小文字16進32桁）を返す。"""
    if not isinstance(value, str):
        raise CloidContractError(f"cloid must be a str, got {type(value).__name__}")
    if len(value) != CLOID_TOTAL_LENGTH:
        raise CloidContractError(
            f"cloid must be exactly {CLOID_TOTAL_LENGTH} chars "
            f"(0x + {CLOID_HEX_DIGITS} hex digits); got {len(value)} chars"
        )
    if not _CLOID_RE.fullmatch(value):
        raise CloidContractError(
            "cloid must match 0x + 32 hex characters "
            f"(no whitespace, no 0X prefix, no non-hex chars): {value!r}"
        )
    return CLOID_PREFIX + value[len(CLOID_PREFIX) :].lower()


def to_sdk_cloid(value: str) -> Any:
    """検証済み cloid 文字列を公式SDKの ``Cloid`` インスタンスへ変換する。

    変換後に公式型の ``str()`` と ``to_raw()`` が正規形と一致することを
    再検証する。SDK 側の検証が緩いため、ここで往復一致を保証する。
    """
    canonical = canonicalize_cloid(value)
    from hyperliquid.utils.types import Cloid

    sdk_cloid = Cloid(canonical)
    if str(sdk_cloid) != canonical or sdk_cloid.to_raw() != canonical:
        raise CloidContractError(
            "official Cloid roundtrip mismatch: "
            f"expected {canonical!r}, got {str(sdk_cloid)!r}/{sdk_cloid.to_raw()!r}"
        )
    return sdk_cloid


def cloid_to_str(value: object) -> str:
    """公式 ``Cloid`` だけを受け取り、正規形の文字列へ逆変換する。

    生の文字列を受け取らないことで「文字列のまま送る経路」を型で塞ぐ。
    """
    from hyperliquid.utils.types import Cloid

    if not isinstance(value, Cloid):
        raise CloidContractError(
            f"cloid_to_str accepts only the official Cloid type, got {type(value).__name__}"
        )
    raw = value.to_raw()
    canonical = canonicalize_cloid(raw)
    if str(value) != canonical:
        raise CloidContractError(f"official Cloid is not normalized: {str(value)!r}")
    return canonical
