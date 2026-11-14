# ruff: noqa: RUF002, RUF003 — 日本語の句読点（全角）は本文テキストであり誤字ではない
"""S-07 の fill・fee・funding 会計（設計書 §2・§3・S07-A03/A05/A06）。

このモジュールは **純粋な会計と状態再生** だけを行う。取引所へは送信せず、
注文クライアントも生成しない。入力は保存済みの合成 fill/funding。

会計規則（docstring が正本）:

* fill の個別数量・価格・手数料を保存し、``tid`` 完全一致の重複は 1 回だけ適用。
  同一 ``tid`` で内容が違う場合は衝突として拒否する。
* 要求数量を超える累計約定は拒否。残数量 = 要求数量 - 累計約定。
* 手数料は各 fill の実数合計（リベートの負値は符号を維持）。
* funding は率ではなく、同口座・同 network の実支払/受取（符号付き）を 1 回だけ適用。
* ``net = realized_gross - fees + funding``。通貨が混在し換算出典が無い場合は判定不能。
* 状態は ``NEW -> PARTIAL -> FILLED`` または ``PARTIAL -> CANCELED/REJECTED``。
  通信失敗・照合不能は ``UNKNOWN`` として保持し、再送しない。
* 約定数量が entry と exit で一致し、建玉 0・未約定 0・照合済みのときだけ往復完了。
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

MS_PER_HOUR = 3_600_000
DEFAULT_PAGE_LIMIT = 2000
DEFAULT_CURRENCY = "USDC"


class FillIntegrityError(ValueError):
    """fill の重複衝突・過剰約定・欠損など、会計を続けられない状態。"""


def _dec(value: Any, *, what: str) -> Decimal:
    if isinstance(value, bool):
        raise FillIntegrityError(f"{what} must not be bool")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise FillIntegrityError(f"{what} is not a decimal: {value!r}") from exc
    if not result.is_finite():
        raise FillIntegrityError(f"{what} must be finite: {value!r}")
    return result


def _int(value: Any, *, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise FillIntegrityError(f"{what} must be an integer, got {value!r}")
    return int(value)


def _canonical(obj: Mapping[str, Any]) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(obj: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(obj).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Fill:
    tid: int
    oid: int
    time_ms: int
    coin: str
    side: str  # "buy" / "sell"（HL wire の B/A は正規化して保持）
    sz: Decimal
    px: Decimal
    fee: Decimal
    fee_token: str
    closed_pnl: Decimal
    network: str

    @property
    def signed_sz(self) -> Decimal:
        return self.sz if self.side == "buy" else -self.sz

    def as_dict(self) -> dict[str, Any]:
        return {
            "tid": self.tid,
            "oid": self.oid,
            "time": self.time_ms,
            "coin": self.coin,
            "side": self.side,
            "sz": str(self.sz),
            "px": str(self.px),
            "fee": str(self.fee),
            "feeToken": self.fee_token,
            "closedPnl": str(self.closed_pnl),
            "network": self.network,
        }

    def content_hash(self) -> str:
        return _sha(self.as_dict())


def _normalize_side(raw: Any) -> str:
    if raw in ("B", "b", "buy", "BUY"):
        return "buy"
    if raw in ("A", "a", "sell", "SELL"):
        return "sell"
    raise FillIntegrityError(f"unknown fill side: {raw!r}")


def parse_fill(row: Mapping[str, Any], *, network: str) -> Fill:
    """1 fill を検証して正規化する。欠損・非数・非有限・負数は拒否。"""
    required = (
        "tid",
        "oid",
        "time",
        "coin",
        "side",
        "sz",
        "px",
        "fee",
        "feeToken",
        "closedPnl",
    )
    missing = [key for key in required if key not in row]
    if missing:
        raise FillIntegrityError(f"fill is missing required keys: {missing}")
    row_network = row.get("network")
    if row_network is not None and row_network != network:
        raise FillIntegrityError(
            f"fill tid={row.get('tid')!r} declares network={row_network!r}, expected {network!r}"
        )
    sz = _dec(row["sz"], what="fill.sz")
    px = _dec(row["px"], what="fill.px")
    if sz <= 0 or px <= 0:
        raise FillIntegrityError(f"fill tid={row['tid']!r} has non-positive size/price")
    fee_token = row["feeToken"]
    if not isinstance(fee_token, str) or not fee_token:
        raise FillIntegrityError("fill.feeToken must be a non-empty string")
    coin = row["coin"]
    if not isinstance(coin, str) or not coin:
        raise FillIntegrityError("fill.coin must be a non-empty string")
    time_ms = _int(row["time"], what="fill.time")
    if time_ms <= 0:
        raise FillIntegrityError("fill.time must be a positive epoch ms")
    return Fill(
        tid=_int(row["tid"], what="fill.tid"),
        oid=_int(row["oid"], what="fill.oid"),
        time_ms=time_ms,
        coin=coin,
        side=_normalize_side(row["side"]),
        sz=sz,
        px=px,
        fee=_dec(row["fee"], what="fill.fee"),
        fee_token=fee_token,
        closed_pnl=_dec(row["closedPnl"], what="fill.closedPnl"),
        network=network,
    )


@dataclass(frozen=True)
class FundingPayment:
    time_ms: int
    delta: Decimal  # 支払いが負、受取が正（原符号を保持）
    token: str
    coin: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "time": self.time_ms,
            "delta": str(self.delta),
            "token": self.token,
            "coin": self.coin,
        }


def parse_funding(row: Mapping[str, Any]) -> FundingPayment:
    required = ("time", "delta", "token")
    missing = [key for key in required if key not in row]
    if missing:
        raise FillIntegrityError(f"funding is missing required keys: {missing}")
    token = row["token"]
    if not isinstance(token, str) or not token:
        raise FillIntegrityError("funding.token must be a non-empty string")
    return FundingPayment(
        time_ms=_int(row["time"], what="funding.time"),
        delta=_dec(row["delta"], what="funding.delta"),
        token=token,
        coin=row.get("coin"),
    )


@dataclass(frozen=True)
class QuarantinedFill:
    tid: Any
    time_ms: Any
    reason: str
    raw_hash: str


@dataclass
class FillIngest:
    network: str
    accepted: list[Fill] = field(default_factory=list)
    quarantined: list[QuarantinedFill] = field(default_factory=list)
    duplicate_count: int = 0
    duplicate_hashes: list[str] = field(default_factory=list)
    applied_count: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "network": self.network,
            "accepted_count": len(self.accepted),
            "applied_count": self.applied_count,
            "quarantined_count": len(self.quarantined),
            "quarantined": [q.__dict__ for q in self.quarantined],
            "duplicate_count": self.duplicate_count,
            "duplicate_hashes": self.duplicate_hashes,
        }


def ingest_fills(
    rows: Sequence[Mapping[str, Any]],
    *,
    network: str = "testnet",
    window_start_ms: int | None = None,
    window_end_ms: int | None = None,
) -> FillIngest:
    """fill 列を検証・重複排除・窓フィルタする（窓外は隔離して証跡に残す）。

    * 同一 ``tid`` かつ内容同一: 1 回だけ適用し、件数と hash を記録。
    * 同一 ``tid`` で内容不一致: :class:`FillIntegrityError`（黙って上書きしない）。
    * 窓外: 適用せず ``quarantined`` に理由付きで残す（入力証跡は保持）。
    """
    result = FillIngest(network=network)
    seen: dict[int, Fill] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise FillIntegrityError(f"fill[{index}] must be an object")
        fill = parse_fill(row, network=network)
        if window_start_ms is not None and fill.time_ms < window_start_ms:
            result.quarantined.append(
                QuarantinedFill(
                    tid=fill.tid,
                    time_ms=fill.time_ms,
                    reason="before window start",
                    raw_hash=fill.content_hash(),
                )
            )
            continue
        if window_end_ms is not None and fill.time_ms >= window_end_ms:
            result.quarantined.append(
                QuarantinedFill(
                    tid=fill.tid,
                    time_ms=fill.time_ms,
                    reason="at or after window end",
                    raw_hash=fill.content_hash(),
                )
            )
            continue
        previous = seen.get(fill.tid)
        if previous is not None:
            if previous == fill:
                result.duplicate_count += 1
                result.duplicate_hashes.append(fill.content_hash())
                continue
            raise FillIntegrityError(
                f"fill tid={fill.tid} has conflicting content: "
                f"{previous.content_hash()} != {fill.content_hash()}"
            )
        seen[fill.tid] = fill
        result.accepted.append(fill)
    result.accepted.sort(key=lambda f: (f.time_ms, f.tid))
    result.applied_count = len(result.accepted)
    return result


class OrderStatus(StrEnum):
    NEW = "NEW"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class OrderReplay:
    cloid: str
    requested_sz: Decimal
    filled_sz: Decimal
    remaining_sz: Decimal | None
    average_px: Decimal | None
    fees: Decimal
    status: OrderStatus
    fills: tuple[Fill, ...]
    terminal_reason: str = ""

    @property
    def completed(self) -> bool:
        return self.status is OrderStatus.FILLED

    def as_dict(self) -> dict[str, Any]:
        return {
            "cloid": self.cloid,
            "requested_sz": str(self.requested_sz),
            "filled_sz": str(self.filled_sz),
            "remaining_sz": None if self.remaining_sz is None else str(self.remaining_sz),
            "average_px": None if self.average_px is None else str(self.average_px),
            "fees": str(self.fees),
            "status": str(self.status),
            "fill_count": len(self.fills),
            "terminal_reason": self.terminal_reason,
        }


def replay_order(
    *,
    cloid: str,
    requested_sz: Decimal | int | str,
    fills: Iterable[Fill],
    transport_failure: bool = False,
    rejected: bool = False,
    cancel_after_ms: int | None = None,
) -> OrderReplay:
    """個別 fill から注文状態を再生する（送信も再送もしない）。

    ``transport_failure=True`` のときは状態を ``UNKNOWN`` とし、残数量を推測しない。
    取消は実約定を消さない。過剰約定は拒否する。
    """
    requested = _dec(requested_sz, what="requested_sz")
    if requested <= 0:
        raise FillIntegrityError("requested_sz must be positive")
    ordered = sorted(fills, key=lambda f: (f.time_ms, f.tid))
    cumulative = Decimal(0)
    notional = Decimal(0)
    fees = Decimal(0)
    for fill in ordered:
        cumulative += fill.sz
        if cumulative > requested:
            raise FillIntegrityError(
                f"cumulative fills {cumulative} exceed requested size {requested} for {cloid}"
            )
        notional += fill.sz * fill.px
        fees += fill.fee

    if transport_failure:
        return OrderReplay(
            cloid=cloid,
            requested_sz=requested,
            filled_sz=cumulative,
            remaining_sz=None,
            average_px=(notional / cumulative) if cumulative > 0 else None,
            fees=fees,
            status=OrderStatus.UNKNOWN,
            fills=tuple(ordered),
            terminal_reason="transport failure; exchange state unresolved; no automatic resend",
        )
    if cumulative == requested:
        return OrderReplay(
            cloid=cloid,
            requested_sz=requested,
            filled_sz=cumulative,
            remaining_sz=Decimal(0),
            average_px=notional / cumulative,
            fees=fees,
            status=OrderStatus.FILLED,
            fills=tuple(ordered),
        )
    if rejected:
        return OrderReplay(
            cloid=cloid,
            requested_sz=requested,
            filled_sz=cumulative,
            remaining_sz=requested - cumulative,
            average_px=(notional / cumulative) if cumulative > 0 else None,
            fees=fees,
            status=OrderStatus.REJECTED,
            fills=tuple(ordered),
            terminal_reason="rejected after partial fill; fills are preserved",
        )
    if cancel_after_ms is not None and any(f.time_ms > cancel_after_ms for f in ordered):
        raise FillIntegrityError(
            "fill recorded after the cancel timestamp; cancel would erase a real fill"
        )
    if cancel_after_ms is not None:
        return OrderReplay(
            cloid=cloid,
            requested_sz=requested,
            filled_sz=cumulative,
            remaining_sz=requested - cumulative,
            average_px=(notional / cumulative) if cumulative > 0 else None,
            fees=fees,
            status=OrderStatus.CANCELED,
            fills=tuple(ordered),
            terminal_reason="canceled; filled quantity and fees remain",
        )
    return OrderReplay(
        cloid=cloid,
        requested_sz=requested,
        filled_sz=cumulative,
        remaining_sz=requested - cumulative,
        average_px=(notional / cumulative) if cumulative > 0 else None,
        fees=fees,
        status=OrderStatus.PARTIAL if cumulative > 0 else OrderStatus.NEW,
        fills=tuple(ordered),
    )


@dataclass(frozen=True)
class NetAccounting:
    gross_pnl: Decimal
    fees: Decimal
    funding: Decimal
    net: Decimal
    currency: str
    problems: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        return not self.problems

    def as_dict(self) -> dict[str, Any]:
        return {
            "gross_pnl": str(self.gross_pnl),
            "fees": str(self.fees),
            "funding": str(self.funding),
            "net": str(self.net),
            "currency": self.currency,
            "problems": list(self.problems),
            "complete": self.complete,
            "formula": "net = realized_gross - fees + signed_funding (each applied once)",
        }


def account_net(
    *,
    fills: Sequence[Fill],
    funding: Sequence[FundingPayment],
    realized_gross: Decimal | int | str | None = None,
    currency: str = DEFAULT_CURRENCY,
) -> NetAccounting:
    """手数料・funding を 1 回ずつ適用した net を返す（通貨混在は判定不能）。"""
    problems: list[str] = []
    fee_tokens = {fill.fee_token for fill in fills}
    funding_tokens = {payment.token for payment in funding}
    tokens = fee_tokens | funding_tokens
    if len(tokens) > 1:
        problems.append(
            "multiple fee/funding currencies without an explicit conversion source: "
            + ", ".join(sorted(tokens))
        )
    if tokens and currency not in tokens:
        problems.append(
            f"account currency {currency!r} is not present in fee/funding tokens {sorted(tokens)}"
        )
    gross = (
        _dec(realized_gross, what="realized_gross")
        if realized_gross is not None
        else sum((fill.closed_pnl for fill in fills), Decimal(0))
    )
    fees = sum((fill.fee for fill in fills), Decimal(0))
    funding_total = sum((payment.delta for payment in funding), Decimal(0))
    return NetAccounting(
        gross_pnl=gross,
        fees=fees,
        funding=funding_total,
        net=gross - fees + funding_total,
        currency=currency,
        problems=tuple(problems),
    )


@dataclass(frozen=True)
class CompletedRoundTrip:
    """entry/exit が数量一致し、建玉・未約定・照合がすべて閉じた往復。"""

    coin: str
    entry_sz: Decimal
    exit_sz: Decimal
    entry_notional: Decimal
    exit_notional: Decimal
    fees: Decimal
    funding: Decimal
    net: Decimal
    complete: bool
    reason: str = ""


def completed_round_trip(
    *,
    coin: str,
    entry_fills: Sequence[Fill],
    exit_fills: Sequence[Fill],
    position_sz: Decimal | int | str = Decimal(0),
    open_order_count: int = 0,
    funding: Sequence[FundingPayment] = (),
    reconciled: bool = True,
    realized_gross: Decimal | int | str | None = None,
) -> CompletedRoundTrip:
    """往復完了を判定する。未解決・建玉残・未約定残があれば数えない。"""
    position = _dec(position_sz, what="position_sz")
    entry_sz = sum((fill.sz for fill in entry_fills), Decimal(0))
    exit_sz = sum((fill.sz for fill in exit_fills), Decimal(0))
    fees = sum((fill.fee for fill in entry_fills), Decimal(0)) + sum(
        (fill.fee for fill in exit_fills), Decimal(0)
    )
    funding_total = sum((payment.delta for payment in funding), Decimal(0))
    gross = (
        _dec(realized_gross, what="realized_gross")
        if realized_gross is not None
        else sum((fill.closed_pnl for fill in exit_fills), Decimal(0))
    )
    reasons: list[str] = []
    if entry_sz != exit_sz:
        reasons.append(f"entry size {entry_sz} != exit size {exit_sz}")
    if position != 0:
        reasons.append(f"position is not flat: {position}")
    if open_order_count != 0:
        reasons.append(f"{open_order_count} open order(s) remain")
    if not reconciled:
        reasons.append("order state was not reconciled")
    return CompletedRoundTrip(
        coin=coin,
        entry_sz=entry_sz,
        exit_sz=exit_sz,
        entry_notional=sum((f.sz * f.px for f in entry_fills), Decimal(0)),
        exit_notional=sum((f.sz * f.px for f in exit_fills), Decimal(0)),
        fees=fees,
        funding=funding_total,
        net=gross - fees + funding_total,
        complete=not reasons,
        reason="; ".join(reasons),
    )


def adverse_slippage_bp(*, side: str, fill_px: Decimal, reference_px: Decimal) -> Decimal:
    """不利方向を正とする slippage(bp)。参照価格は判断時の価格。"""
    if reference_px <= 0:
        raise FillIntegrityError("reference_px must be positive")
    if side == "buy":
        return (fill_px - reference_px) / reference_px * Decimal(10_000)
    if side == "sell":
        return (reference_px - fill_px) / reference_px * Decimal(10_000)
    raise FillIntegrityError(f"unknown side: {side!r}")


def median_slippage_bp(samples: Sequence[Decimal]) -> Decimal | None:
    """slippage 中央値。空列は 0 ではなく ``None``（未測定）。"""
    if not samples:
        return None
    return Decimal(statistics.median(samples))


@dataclass(frozen=True)
class FillPage:
    index: int
    rows: tuple[Mapping[str, Any], ...]
    limit: int = DEFAULT_PAGE_LIMIT
    next_page_first_time_ms: int | None = None

    @property
    def last_time_ms(self) -> int | None:
        times = [
            row["time"]
            for row in self.rows
            if isinstance(row.get("time"), int) and not isinstance(row.get("time"), bool)
        ]
        return max(times) if times else None


@dataclass(frozen=True)
class PageCompleteness:
    complete: bool
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {"complete": self.complete, "reasons": list(self.reasons)}


def assess_page_completeness(pages: Sequence[FillPage]) -> PageCompleteness:
    """ページ取得の完全性を判定する（成功と完全性を同一視しない）。

    * 最終ページが limit 件ちょうどなら、そこで終端したことを証明できない。
    * 次ページの先頭時刻が前ページ末尾と同一なら、同時刻飽和で取りこぼし得る。
    * ページ番号の欠落・重複も不完全とする。
    """
    reasons: list[str] = []
    if not pages:
        return PageCompleteness(False, ("no fill pages were provided",))
    indexes = [page.index for page in pages]
    if indexes != list(range(len(pages))):
        reasons.append(f"page indexes are not contiguous: {indexes}")
    for position, page in enumerate(pages):
        if len(page.rows) >= page.limit:
            reasons.append(
                f"page {page.index} returned {len(page.rows)} rows at the {page.limit} limit"
            )
        if position + 1 < len(pages):
            nxt = pages[position + 1]
            first = nxt.rows[0].get("time") if nxt.rows else None
            if isinstance(first, int) and page.last_time_ms == first:
                reasons.append(f"page boundary at {first} is saturated by identical timestamps")
    return PageCompleteness(not reasons, tuple(dict.fromkeys(reasons)))


@dataclass(frozen=True)
class FundingCompleteness:
    complete: bool
    missing: tuple[int, ...]
    unexpected: tuple[int, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "complete": self.complete,
            "missing_count": len(self.missing),
            "missing": list(self.missing),
            "unexpected_count": len(self.unexpected),
            "unexpected": list(self.unexpected),
        }


def assess_funding_coverage(
    payments: Sequence[FundingPayment],
    *,
    expected_times_ms: Sequence[int],
    tolerance_ms: int = 0,
) -> FundingCompleteness:
    """既知の支払予定時刻に対する funding の欠測・余剰を検査する。"""
    actual = sorted(payment.time_ms for payment in payments)
    if tolerance_ms < 0:
        raise FillIntegrityError("tolerance_ms must be >= 0")

    def _match(expected: int) -> bool:
        return any(abs(actual_time - expected) <= tolerance_ms for actual_time in actual)

    missing = tuple(sorted(t for t in expected_times_ms if not _match(t)))
    expected_set = set(expected_times_ms)
    unexpected = tuple(
        time
        for time in actual
        if not any(abs(time - expected) <= tolerance_ms for expected in expected_set)
    )
    return FundingCompleteness(not missing and not unexpected, missing, unexpected)


CORRECTABLE_FIELDS = frozenset({"sz", "px", "fee", "feeToken", "closedPnl", "side"})


@dataclass(frozen=True)
class Correction:
    """原本を書き換えずに追記する訂正（出典必須）。"""

    tid: int
    field: str
    value: Any
    source: str
    reason: str
    recorded_at_ms: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "tid": self.tid,
            "field": self.field,
            "value": self.value,
            "source": self.source,
            "reason": self.reason,
            "recorded_at_ms": self.recorded_at_ms,
        }


def apply_corrections(
    fills: Sequence[Fill], corrections: Sequence[Correction]
) -> tuple[list[Fill], list[dict[str, Any]]]:
    """訂正を「追記ログ」として適用し、原本は変更しない。

    同じ fill の同じ field に対する訂正が 2 件以上ある場合は衝突として拒否する。
    出典・理由が空の訂正も拒否する。戻り値は (新しい fill 列, 適用証跡)。
    """
    by_tid = {fill.tid: fill for fill in fills}
    trail: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    working: dict[int, Fill] = {}
    for correction in corrections:
        if correction.field not in CORRECTABLE_FIELDS:
            raise FillIntegrityError(
                f"correction field {correction.field!r} is not allowed; "
                f"allowed={sorted(CORRECTABLE_FIELDS)}"
            )
        if not correction.source.strip() or not correction.reason.strip():
            raise FillIntegrityError("correction requires a non-empty source and reason")
        key = (correction.tid, correction.field)
        if key in seen:
            raise FillIntegrityError(f"conflicting corrections for tid={key[0]} field={key[1]}")
        seen.add(key)
        original = working.get(correction.tid, by_tid.get(correction.tid))
        if original is None:
            raise FillIntegrityError(f"correction references unknown tid={correction.tid}")
        updated = _with_correction(original, correction)
        working[correction.tid] = updated
        trail.append(
            {
                **correction.as_dict(),
                "original_hash": original.content_hash(),
                "corrected_hash": updated.content_hash(),
                "applied_once": True,
            }
        )
    result = [working.get(fill.tid, fill) for fill in fills]
    return result, trail


def _with_correction(fill: Fill, correction: Correction) -> Fill:
    value = correction.value
    if correction.field == "feeToken":
        if not isinstance(value, str) or not value:
            raise FillIntegrityError("correction feeToken must be a non-empty string")
        return replace(fill, fee_token=value)
    if correction.field == "side":
        return replace(fill, side=_normalize_side(value))
    if correction.field == "sz":
        size = _dec(value, what="correction.sz")
        if size <= 0:
            raise FillIntegrityError("correction sz must be positive")
        return replace(fill, sz=size)
    if correction.field == "px":
        price = _dec(value, what="correction.px")
        if price <= 0:
            raise FillIntegrityError("correction px must be positive")
        return replace(fill, px=price)
    if correction.field == "fee":
        return replace(fill, fee=_dec(value, what="correction.fee"))
    if correction.field == "closedPnl":
        return replace(fill, closed_pnl=_dec(value, what="correction.closedPnl"))
    raise FillIntegrityError(f"unsupported correction field: {correction.field}")


def utc_hour_starts(start_ms: int, end_ms: int) -> list[int]:
    """[start, end) の毎正時 epoch ms（funding 予定時刻の生成補助）。"""
    if end_ms <= start_ms:
        raise FillIntegrityError("end_ms must be greater than start_ms")
    first = start_ms - (start_ms % MS_PER_HOUR)
    return list(range(first, end_ms, MS_PER_HOUR))


def iso_utc(time_ms: int) -> str:
    return _dt.datetime.fromtimestamp(time_ms / 1000, _dt.UTC).isoformat()
