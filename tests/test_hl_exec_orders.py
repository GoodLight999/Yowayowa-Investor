# ruff: noqa: RUF002, RUF003 — 日本語の句読点（全角）は本文テキストであり誤字ではない
"""hl_exec.orders の単体テスト（丸め境界・公式例・wire組立）。

運用補足（2026-11-02 COO）の境界4ケースを含む:
  78012.1 -> 78012、0.1234567 -> 0.12345、1234.5 -> 1234.5、12345.6 -> 12345
丸め後の値が桁数上限を超えないことを price_is_valid / size_is_valid で証明する。
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from yowayowa.hl_exec.orders import (
    OrderAssemblyError,
    build_entry_bundle,
    build_limit_order,
    build_trigger_order,
    price_is_valid,
    round_price,
    round_size,
    size_is_valid,
    to_wire_str,
    validate_assembled,
)

# (入力価格, szDecimals, 期待値) — 運用補足 2026-11-02 の4ケース
OPERATOR_BOUNDARY_CASES = [
    (78012.1, 5, 78012.0),
    (0.1234567, 0, 0.12345),
    (1234.5, 0, 1234.5),
    (12345.6, 0, 12345.0),
]


@pytest.mark.parametrize(("px", "sz_dec", "expected"), OPERATOR_BOUNDARY_CASES)
def test_truncation_boundaries_from_operator(px: float, sz_dec: int, expected: float) -> None:
    assert round_price(px, sz_decimals=sz_dec) == expected


@pytest.mark.parametrize(("px", "sz_dec", "expected"), OPERATOR_BOUNDARY_CASES)
def test_rounded_price_stays_within_official_digit_limits(
    px: float, sz_dec: int, expected: float
) -> None:
    out = round_price(px, sz_decimals=sz_dec)
    assert price_is_valid(out, sz_decimals=sz_dec) is True
    assert out <= px  # 切り捨て契約: 四捨五入で上方向へ増えない


def test_truncation_not_rounding_half_up() -> None:
    # 四捨五入なら 78011 になるが、切り捨て契約では 78010
    assert round_price(78010.9, sz_decimals=5) == 78010.0


# BTC（szDecimals=5 → 小数は 6-5=1 桁まで）で「有効数字5桁」と「tick桁」の
# 厳しい方が効くことを示すケース。(入力, szDecimals, 期待値)
TICK_INTERACTION_CASES = [
    (0.1234567, 5, 0.1),  # 有効数字では 0.12345 だが tick が 1 桁に制限
    (1234.5678, 5, 1234.5),  # 小数 1 桁で切れる
    (7801.24, 5, 7801.2),
    (78012.99, 5, 78012.0),  # 整数部5桁は整数化（整数価格は常に許可）
]


@pytest.mark.parametrize(("px", "sz_dec", "expected"), TICK_INTERACTION_CASES)
def test_tick_and_significant_figure_interaction(px: float, sz_dec: int, expected: float) -> None:
    out = round_price(px, sz_decimals=sz_dec)
    assert out == expected
    assert out <= px
    assert price_is_valid(out, sz_decimals=sz_dec) is True


def test_price_truncating_to_zero_is_rejected_not_silently_zero() -> None:
    # tick 桁に丸めると 0 になる価格は「0 円注文」を作らず即座に拒否する
    with pytest.raises(OrderAssemblyError, match="truncates to zero"):
        round_price(0.000001, sz_decimals=5)  # 小数1桁まで → 0.0
    with pytest.raises(OrderAssemblyError, match="truncates to zero"):
        round_price(1e-9, sz_decimals=5)


def test_very_large_price_becomes_integer_price() -> None:
    assert round_price(1234567.8, sz_decimals=5) == 1234567.0
    assert price_is_valid(1234567.0, sz_decimals=5) is True


def test_integer_price_is_passed_through_unrounded() -> None:
    # 整数価格は有効数字に関係なく常に許可（公式仕様）のため丸めない
    assert round_price(123456, sz_decimals=5) == 123456.0


@pytest.mark.parametrize(
    ("px", "sz_dec", "valid"),
    [
        ("1234.5", 0, True),
        ("1234.56", 0, False),
        ("0.001234", 0, True),
        ("0.0012345", 0, False),
        ("0.01234", 1, True),
        ("0.012345", 1, False),
        ("123456", 0, True),  # 整数は常に許可
        ("12345.6", 0, False),
        ("78012.1", 5, False),  # 設計書 §2.3 の無効例（有効数字6桁）
        ("7801.2", 5, True),  # 設計書 §2.3 の有効例
    ],
)
def test_official_tick_examples(px: str, sz_dec: int, valid: bool) -> None:
    assert price_is_valid(Decimal(px), sz_decimals=sz_dec) is valid


def test_round_price_rejects_non_positive() -> None:
    with pytest.raises(OrderAssemblyError):
        round_price(0, sz_decimals=5)
    with pytest.raises(OrderAssemblyError):
        round_price(-1.5, sz_decimals=5)


def test_round_size_truncates_at_szdecimals() -> None:
    assert round_size(1.00019, sz_decimals=3) == 1.0
    assert round_size(1.999999, sz_decimals=3) == 1.999
    assert round_size("0.123456", sz_decimals=4) == 0.1234


@pytest.mark.parametrize(
    ("sz", "sz_dec", "valid"),
    [
        ("1.001", 3, True),
        ("1.0001", 3, False),
        ("0.00001", 5, True),
        ("0.000001", 5, False),
    ],
)
def test_official_lot_examples(sz: str, sz_dec: int, valid: bool) -> None:
    assert size_is_valid(Decimal(sz), sz_decimals=sz_dec) is valid


def test_round_size_rejects_zero_after_truncation() -> None:
    with pytest.raises(OrderAssemblyError, match="truncates to zero"):
        round_size(0.000001, sz_decimals=3)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (78012.0, "78012"),
        (0.1234, "0.1234"),
        ("1.500", "1.5"),
        (Decimal("100"), "100"),
        (123456, "123456"),
    ],
)
def test_to_wire_str_removes_trailing_zeroes(value: object, expected: str) -> None:
    assert to_wire_str(value) == expected  # type: ignore[arg-type]


def _build(**overrides: object) -> dict:
    kwargs: dict = {
        "asset_index": 0,
        "coin": "BTC",
        "is_buy": True,
        "size": 0.0007,
        "price": 78012.1,
        "sz_decimals": 5,
    }
    kwargs.update(overrides)
    return build_limit_order(**kwargs)


def test_build_limit_order_produces_official_wire_shape() -> None:
    order = _build()
    assert order["wire"] == {
        "a": 0,
        "b": True,
        "p": "78012",  # 78012.1 は丸めで 78012（整数価格は常に許可）
        "s": "0.0007",
        "r": False,
        "t": {"limit": {"tif": "Ioc"}},
    }
    assert order["coin"] == "BTC"
    assert order["notional_usdc"] == pytest.approx(78012.0 * 0.0007)


def test_build_limit_order_reduce_only_and_gtc() -> None:
    order = _build(reduce_only=True, tif="Gtc")
    assert order["wire"]["r"] is True
    assert order["wire"]["t"] == {"limit": {"tif": "Gtc"}}


def test_build_limit_order_with_cloid() -> None:
    cloid = "0x" + "ab" * 16
    order = _build(cloid=cloid)
    assert order["wire"]["c"] == cloid
    assert order["cloid"] == cloid


def test_build_limit_order_rejects_bad_cloid() -> None:
    with pytest.raises(OrderAssemblyError, match="cloid"):
        _build(cloid="0xZZZZ")


def test_build_limit_order_rejects_bad_tif() -> None:
    with pytest.raises(OrderAssemblyError, match="tif"):
        _build(tif="FOO")


def test_build_limit_order_rejects_below_min_notional() -> None:
    # 9999 x 0.00099 = 9.899... < 10.0（丸め後の値で判定）
    with pytest.raises(OrderAssemblyError, match="below min"):
        _build(price=9999.0, size=0.00099)


def test_build_limit_order_accepts_notional_exactly_min() -> None:
    order = _build(price=10000.0, size=0.001)
    assert order["notional_usdc"] == pytest.approx(10.0)


def test_build_limit_order_rejects_above_cap_notional() -> None:
    # 60001 x 0.001 = 60.001 > 60.0
    with pytest.raises(OrderAssemblyError, match="exceeds cap"):
        _build(price=60001.0, size=0.001)


def test_build_limit_order_accepts_notional_exactly_cap() -> None:
    order = _build(price=60000.0, size=0.001)
    assert order["notional_usdc"] == pytest.approx(60.0)


def test_build_trigger_order_sl_shape() -> None:
    sl = build_trigger_order(
        asset_index=0,
        coin="BTC",
        is_buy=False,
        size=0.0007,
        trigger_price=78000.5,
        sz_decimals=5,
        tpsl="sl",
    )
    assert sl["wire"]["t"] == {"trigger": {"isMarket": True, "triggerPx": "78000", "tpsl": "sl"}}
    assert sl["wire"]["r"] is True
    assert sl["wire"]["p"] == "78000"  # 必須フィールドのため triggerPx と同値


def test_build_trigger_order_rejects_bad_tpsl() -> None:
    with pytest.raises(OrderAssemblyError, match="tpsl"):
        build_trigger_order(
            asset_index=0,
            coin="BTC",
            is_buy=False,
            size=0.0007,
            trigger_price=78000.0,
            sz_decimals=5,
            tpsl="xx",
        )


def test_build_entry_bundle_pairs_sl_against_entry_side() -> None:
    bundle = build_entry_bundle(
        asset_index=0,
        coin="BTC",
        is_buy=True,
        size=0.0007,
        price=78012.1,
        sz_decimals=5,
        sl_trigger_price=76000.0,
        tp_trigger_price=81000.0,
    )
    assert bundle["entry"]["wire"]["r"] is False  # エントリーは reduce-only ではない
    assert bundle["entry"]["wire"]["b"] is True
    assert bundle["sl"]["wire"]["b"] is False  # SLは必ず逆サイド
    assert bundle["sl"]["wire"]["t"]["trigger"]["tpsl"] == "sl"
    assert bundle["tp"]["wire"]["b"] is False
    assert bundle["tp"]["wire"]["t"]["trigger"]["tpsl"] == "tp"
    for part in ("entry", "sl", "tp"):
        validate_assembled(bundle[part], sz_decimals=5)


def test_build_entry_bundle_without_tp() -> None:
    bundle = build_entry_bundle(
        asset_index=0,
        coin="BTC",
        is_buy=False,
        size=0.0007,
        price=78000.0,
        sz_decimals=5,
        sl_trigger_price=80000.0,
    )
    assert bundle["tp"] is None
    assert bundle["entry"]["wire"]["b"] is False
    assert bundle["sl"]["wire"]["b"] is True  # 売り建てのSLは買い戻し


def test_validate_assembled_detects_altered_price() -> None:
    order = _build()
    order["wire"]["p"] = "78012.1"  # 無効 tick を後から仕込む
    with pytest.raises(OrderAssemblyError, match="tick contract"):
        validate_assembled(order, sz_decimals=5)


def test_validate_assembled_detects_altered_size() -> None:
    order = _build()
    order["wire"]["s"] = "0.0007001"  # szDecimals=5 を超える小数
    with pytest.raises(OrderAssemblyError, match="lot contract"):
        validate_assembled(order, sz_decimals=5)
