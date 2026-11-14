# ruff: noqa: RUF002, RUF003 — 日本語の句読点（全角）は本文テキストであり誤字ではない

"""約定の集計・位置チェーン再構成・損益の厳密計算・許容範囲の導出。

丸めは表示の都合であり、ここでは ``Decimal`` のまま計算する。どの許容範囲も
「表示量子化 × 件数」の式で導き、根拠をレポートへ出す（黙って緩めない）。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from itertools import pairwise

from yowayowa.hl_exec.s07_authenticity.models import (
    MS_PER_HOUR,
    PRICE_DISPLAY_QUANTUM,
    USDC_QUANTUM,
    AuthenticityError,
    LiveFill,
)


@dataclass(frozen=True)
class OidAggregate:
    oid: int
    fill_count: int
    size: Decimal
    notional: Decimal
    fee: Decimal
    closed_pnl: Decimal
    first_ms: int
    last_ms: int
    tids: tuple[int, ...]
    sides: tuple[str, ...]
    coins: tuple[str, ...]

    @property
    def avg_px(self) -> Decimal | None:
        return None if self.size == 0 else self.notional / self.size


def aggregate_by_oid(fills: tuple[LiveFill, ...] | list[LiveFill]) -> dict[int, OidAggregate]:
    """同一 OID の複数 fill（部分約定）を数量・出来高加重平均・fee で集計する。"""
    buckets: dict[int, list[LiveFill]] = {}
    for fill in fills:
        buckets.setdefault(fill.oid, []).append(fill)
    result: dict[int, OidAggregate] = {}
    for oid, rows in buckets.items():
        ordered = sorted(rows, key=lambda row: (row.time_ms, row.tid))
        size = sum((row.fill.sz for row in ordered), Decimal(0))
        notional = sum((row.fill.sz * row.fill.px for row in ordered), Decimal(0))
        result[oid] = OidAggregate(
            oid=oid,
            fill_count=len(ordered),
            size=size,
            notional=notional,
            fee=sum((row.fill.fee for row in ordered), Decimal(0)),
            closed_pnl=sum((row.fill.closed_pnl for row in ordered), Decimal(0)),
            first_ms=ordered[0].time_ms,
            last_ms=ordered[-1].time_ms,
            tids=tuple(row.tid for row in ordered),
            sides=tuple(row.fill.side for row in ordered),
            coins=tuple(sorted({row.fill.coin for row in ordered})),
        )
    return result


@dataclass(frozen=True)
class ChainReconstruction:
    oid: int
    tids: tuple[int, ...]
    start_position: Decimal | None
    end_position: Decimal | None
    breaks: tuple[str, ...]
    time_inversions: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return not self.breaks and not self.time_inversions


def reconstruct_position_chain(rows: list[LiveFill] | tuple[LiveFill, ...]) -> ChainReconstruction:
    """``startPosition`` の連鎖から、同一 OID の約定順を時刻に依存せず再構成する。

    部分約定は同一時刻に並ぶことがあるため、時刻だけで順序を決めない。
    ``startPosition + signed_sz`` が次の fill の ``startPosition`` と一致する
    鎖を組み、鎖の時刻が非減少であることを確かめる。
    """
    if not rows:
        raise AuthenticityError("cannot reconstruct a chain from zero fills")
    oid = rows[0].oid
    if any(row.oid != oid for row in rows):
        raise AuthenticityError("chain reconstruction requires a single oid")
    by_start: dict[Decimal, LiveFill] = {}
    breaks: list[str] = []
    ends = {row.end_position for row in rows}
    for row in rows:
        if row.start_position in by_start:
            breaks.append(f"duplicate startPosition {row.start_position} for oid {oid}")
            continue
        by_start[row.start_position] = row
    roots = [row for row in rows if row.start_position not in ends]
    if len(roots) != 1:
        breaks.append(f"expected one chain root, found {len(roots)} for oid {oid}")
    ordered: list[LiveFill] = []
    if roots:
        current: LiveFill | None = roots[0]
        visited: set[int] = set()
        while current is not None and current.tid not in visited:
            ordered.append(current)
            visited.add(current.tid)
            current = by_start.get(current.end_position)
        if len(visited) != len(rows):
            breaks.append(
                f"chain covers {len(visited)}/{len(rows)} fills for oid {oid} (gap or cycle)"
            )
    inversions: list[str] = []
    for previous, following in pairwise(ordered):
        if following.time_ms < previous.time_ms:
            inversions.append(
                f"oid {oid}: tid {following.tid} time {following.time_ms} "
                f"precedes tid {previous.tid} time {previous.time_ms} in chain order"
            )
    return ChainReconstruction(
        oid=oid,
        tids=tuple(row.tid for row in ordered),
        start_position=(ordered[0].start_position if ordered else None),
        end_position=(ordered[-1].end_position if ordered else None),
        breaks=tuple(breaks),
        time_inversions=tuple(inversions),
    )


def fill_notional_and_size(
    rows: list[LiveFill] | tuple[LiveFill, ...],
) -> tuple[Decimal, Decimal]:
    """(出来高, 数量合計) を Decimal のまま返す。"""
    size = sum((row.fill.sz for row in rows), Decimal(0))
    notional = sum((row.fill.sz * row.fill.px for row in rows), Decimal(0))
    return notional, size


def weighted_average_price(rows: list[LiveFill] | tuple[LiveFill, ...]) -> Decimal:
    notional, size = fill_notional_and_size(rows)
    if size <= 0:
        raise AuthenticityError("cannot average fills with zero total size")
    return notional / size


def exact_realized_pnl(
    *,
    entry_fills: list[LiveFill] | tuple[LiveFill, ...],
    exit_fills: list[LiveFill] | tuple[LiveFill, ...],
    direction: str,
) -> Decimal:
    """fill 価格から往復の実現損益を厳密に計算する（丸めた closedPnl を使わない）。

    数量が一致する往復では ``Σ entry_px*sz - Σ exit_px*sz`` と等価であり、
    除算を挟まないため Decimal の循環小数誤差が出ない。
    SHORT: ``entry_notional - exit_notional`` / LONG: その逆。
    """
    if direction not in ("SHORT", "LONG"):
        raise AuthenticityError(f"unknown trade direction: {direction!r}")
    entry_notional, entry_size = fill_notional_and_size(entry_fills)
    exit_notional, exit_size = fill_notional_and_size(exit_fills)
    if entry_size != exit_size:
        raise AuthenticityError(
            f"entry size {entry_size} != exit size {exit_size}; cannot realize a round trip"
        )
    if direction == "SHORT":
        return entry_notional - exit_notional
    return exit_notional - entry_notional


def quantization_effect(
    *,
    entry_px_recorded: Decimal | None,
    entry_notional: Decimal,
    entry_size: Decimal,
    direction: str,
) -> Decimal | None:
    """台帳の entryPx（1 桁表示）を使った closedPnl と厳密値の差。

    ``closedPnl_reported - pnl_exact = sign * (entry_px_recorded*size - entry_notional)``。
    除算を避けるため出来高と数量から直接計算する。
    """
    if entry_px_recorded is None:
        return None
    sign = Decimal(-1) if direction == "SHORT" else Decimal(1)
    return sign * (entry_px_recorded * entry_size - entry_notional)


def hour_boundaries(open_ms: int, close_ms: int) -> tuple[int, ...]:
    """(open, close] に入る毎正時 epoch ms。保有が短ければ空（funding 0 の根拠）。"""
    if close_ms < open_ms:
        raise AuthenticityError("close_ms must not precede open_ms")
    first = open_ms - (open_ms % MS_PER_HOUR) + MS_PER_HOUR
    return tuple(range(first, close_ms + 1, MS_PER_HOUR))


def fee_tolerance(fill_count: int) -> Decimal:
    """fill ごとの fee 1e-6 切り捨て + 口座価値 1e-6 の合成上限。"""
    if fill_count < 0:
        raise AuthenticityError("fill_count must be >= 0")
    return (Decimal(fill_count) + Decimal(1)) * USDC_QUANTUM


def journal_net_tolerance(*, fill_count: int, size: Decimal) -> Decimal:
    """ジャーナル net 用: fee 量子化 + entry/exit の fill_px を 0.1 で切った影響。"""
    if size < 0:
        raise AuthenticityError("size must be >= 0")
    return fee_tolerance(fill_count) + Decimal(2) * PRICE_DISPLAY_QUANTUM * size


def max_abs(values: list[Decimal] | tuple[Decimal, ...]) -> Decimal:
    return max((abs(value) for value in values), default=Decimal(0))


def dstr(value: Decimal | None) -> str | None:
    """:class:`Decimal` を指数表記せず文字列化する（残差を丸めない）。"""
    if value is None:
        return None
    return format(value, "f")
