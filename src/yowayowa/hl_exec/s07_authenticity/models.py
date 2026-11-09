# ruff: noqa: RUF002, RUF003 — 日本語の句読点（全角）は本文テキストであり誤字ではない

"""S-07 X1（実データ真正性検証）のドメイン型と数値規約。

このパッケージは **読み取り専用** の検証器である。取引所への送信・署名・鍵の
読み出しは行わない。``/info`` の公開アドレス照会だけを任意で使う。

数値の単位と量子化（レポートで明示する許容範囲の根拠）:

* 金額はすべて **USDC**。Hyperliquid の ``userFills`` の ``fee``/``closedPnl`` と
  ``clearinghouseState`` の ``accountValue`` は 10 進 6 桁（1e-6 USDC）で返る。
* 台帳 ``daily_ledger.json`` の equity / daily_pnl は 10 進 4 桁（1e-4 USDC）。
* ジャーナル ``fill_px`` と台帳の ``fill_px`` は 10 進 1 桁へ切り捨て表示される。
* funding は ``userFunding`` の ``delta``（正=受取、負=支払）をそのまま合計する。

したがって「丸めない」残差は :class:`decimal.Decimal` のまま保持し、許容範囲は
上記の量子化から導いた式（各チェックの ``tolerance``）で判定する。
"""

from __future__ import annotations

import datetime as _dt
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from yowayowa.hl_exec.s07_offline.fills import Fill

#: USDC の最小表示単位（1e-6）。
USDC_QUANTUM = Decimal("0.000001")
#: 台帳 equity の表示単位（1e-4）。
LEDGER_QUANTUM = Decimal("0.0001")
#: ジャーナル fill_px の表示切り捨て単位（0.1）。
PRICE_DISPLAY_QUANTUM = Decimal("0.1")
MS_PER_HOUR = 3_600_000


class AuthenticityError(ValueError):
    """真正性検証を続けられない入力（欠損・型不正・重複衝突など）。"""


def to_decimal(value: Any, *, what: str) -> Decimal:
    """有限な Decimal へ変換する。bool・非数・無限は拒否する。"""
    if isinstance(value, bool):
        raise AuthenticityError(f"{what} must not be bool")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise AuthenticityError(f"{what} is not a decimal: {value!r}") from exc
    if not result.is_finite():
        raise AuthenticityError(f"{what} must be finite: {value!r}")
    return result


def to_int(value: Any, *, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise AuthenticityError(f"{what} must be an integer, got {value!r}")
    return int(value)


def iso_to_ms(text: str, *, what: str = "timestamp") -> int:
    """ISO-8601（UTC オフセット付き）を epoch ミリ秒へ変換する。"""
    try:
        parsed = _dt.datetime.fromisoformat(str(text))
    except ValueError as exc:
        raise AuthenticityError(f"{what} is not ISO-8601: {text!r}") from exc
    if parsed.tzinfo is None:
        raise AuthenticityError(f"{what} must carry a UTC offset: {text!r}")
    return int(parsed.timestamp() * 1000)


def ms_to_iso(value: int) -> str:
    return _dt.datetime.fromtimestamp(value / 1000, _dt.UTC).isoformat()


def day_of_ms(value: int) -> str:
    return _dt.datetime.fromtimestamp(value / 1000, _dt.UTC).date().isoformat()


@dataclass(frozen=True)
class LiveFill:
    """``/info`` の ``userFills`` 1 行（公開オンチェーン約定）。"""

    fill: Fill
    start_position: Decimal
    direction: str
    tx_hash: str

    @property
    def oid(self) -> int:
        return self.fill.oid

    @property
    def tid(self) -> int:
        return self.fill.tid

    @property
    def time_ms(self) -> int:
        return self.fill.time_ms

    @property
    def signed_sz(self) -> Decimal:
        return self.fill.signed_sz

    @property
    def end_position(self) -> Decimal:
        return self.start_position + self.signed_sz


@dataclass(frozen=True)
class FundingRow:
    """``userFunding`` の 1 行（実支払/受取。符号を保持）。"""

    time_ms: int
    delta: Decimal
    token: str
    coin: str | None


@dataclass(frozen=True)
class OrderRecord:
    """ジャーナル ORDER 段の 1 行（台帳側の注文記録）。"""

    day: str
    event_id: str
    ts_ms: int
    substage: str  # entry_fill / exit_fill
    oid: int
    fill_px: Decimal
    fill_sz: Decimal
    fee_usdc: Decimal
    duration_ms: int
    slippage_bp: Decimal | None

    @property
    def side_role(self) -> str:
        return "entry" if self.substage == "entry_fill" else "exit"


@dataclass(frozen=True)
class TradeLeg:
    oid: int
    fill_px: Decimal
    size: Decimal
    fee_usdc: Decimal
    partial: bool


@dataclass(frozen=True)
class TradeRecord:
    day: str
    direction: str  # SHORT / LONG
    coin: str
    entry: TradeLeg
    exit: TradeLeg
    gross_pnl_usdc: Decimal
    fees_usdc: Decimal
    funding_usdc: Decimal
    net_pnl_usdc: Decimal


@dataclass(frozen=True)
class CycleState:
    """1 日 1 サイクルの口座価値（``raw_events`` の pre/post）。"""

    day: str
    pre_equity: Decimal
    post_equity: Decimal
    pre_ts_ms: int
    post_ts_ms: int
    journal_gross: Decimal | None
    journal_net: Decimal | None
    entry_position_px: Decimal | None

    @property
    def delta_equity(self) -> Decimal:
        return self.post_equity - self.pre_equity


@dataclass(frozen=True)
class LedgerDay:
    day: str
    equity: Decimal
    daily_pnl: Decimal
    cumulative_pnl: Decimal
    funding_balance: Decimal
    trades_count: int
    position: Decimal
    governance: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LockCounts:
    day: str
    killswitch_acquired: int = 0
    killswitch_released: int = 0
    execution_acquired: int = 0
    execution_released: int = 0


@dataclass(frozen=True)
class LiveSnapshot:
    """``/info`` 由来の実データと出典（再現用の hash を含む）。"""

    account: str
    fills: tuple[LiveFill, ...]
    funding: tuple[FundingRow, ...]
    clearinghouse: Mapping[str, Any] | None
    open_orders: tuple[Mapping[str, Any], ...]
    provenance: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceFile:
    path: str
    sha256: str
    size: int
    rows: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "size": self.size,
            "rows": self.rows,
        }
