# ruff: noqa: RUF001, RUF002, RUF003 — 日本語の句読点（全角）は本文テキストであり誤字ではない

"""S-07 X1: 実データ真正性の検証器（読み取り専用・fail-closed）。

検証する契約（設計書 §6 / 傘カード t_38d18e3e の X1）:

1. ``userFills`` の OID と台帳の注文記録の 1 対 1 対応（欠落・孤立・重複）。
2. 同一 OID の複数 fill（部分約定）を数量・出来高加重平均・fee で正しく集計。
3. fee と funding が サイクル内 Δequity に符号込みで整合するか。
4. 時刻の単調性（同一 OID の約定順）と重複行の検出。

判定は **fail-closed**: 必須入力（実 fill）が無ければ PASS にせず ``BLOCKED``
（総合 ``UNVERIFIABLE``）。欠落・孤立・重複・残差超過は ``FAIL``。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any

from yowayowa.hl_exec.s07_authenticity.bookkeeping import (
    ChainReconstruction,
    OidAggregate,
    aggregate_by_oid,
    dstr,
    exact_realized_pnl,
    fee_tolerance,
    fill_notional_and_size,
    hour_boundaries,
    journal_net_tolerance,
    max_abs,
    quantization_effect,
    reconstruct_position_chain,
)
from yowayowa.hl_exec.s07_authenticity.models import (
    LEDGER_QUANTUM,
    PRICE_DISPLAY_QUANTUM,
    USDC_QUANTUM,
    CycleState,
    LedgerDay,
    LiveFill,
    LiveSnapshot,
    OrderRecord,
    SourceFile,
    TradeRecord,
)
from yowayowa.hl_exec.s07_authenticity.sources import SourceBundle

#: 出力内で列挙する ID の最大件数（件数そのものは counts に全数を残す）。
SAMPLE_LIMIT = 20


class Status(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    WARN = "WARN"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class Check:
    id: str
    title: str
    status: Status
    counts: Mapping[str, Any] = field(default_factory=dict)
    details: tuple[str, ...] = ()
    tolerance: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "status": str(self.status),
            "tolerance": self.tolerance,
            "counts": jsonable(self.counts),
            "details": list(self.details),
        }


def jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return dstr(value)
    if isinstance(value, Mapping):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    return value


def _sample(values: Sequence[Any]) -> list[Any]:
    return list(values[:SAMPLE_LIMIT])


def _check(
    check_id: str,
    title: str,
    *,
    failures: int,
    counts: Mapping[str, Any] | None = None,
    details: Sequence[str] = (),
    tolerance: str = "",
    blocked: bool = False,
) -> Check:
    if blocked:
        status = Status.BLOCKED
    elif failures:
        status = Status.FAIL
    else:
        status = Status.PASS
    return Check(
        id=check_id,
        title=title,
        status=status,
        counts=dict(counts or {}),
        details=tuple(details),
        tolerance=tolerance,
    )


@dataclass
class _Context:
    bundle: SourceBundle
    account: str | None
    live: LiveSnapshot | None
    orders_by_day: dict[str, dict[str, OrderRecord]]
    orders_by_oid: dict[int, OrderRecord]
    duplicate_order_oids: list[int]
    duplicate_order_events: list[str]
    trades_by_day: dict[str, TradeRecord]
    cycles_by_day: dict[str, CycleState]
    ledger_by_day: dict[str, LedgerDay]
    live_by_oid: dict[int, list[LiveFill]]
    duplicate_live_tids: list[int]
    aggregates: dict[int, OidAggregate]
    chains: dict[int, ChainReconstruction]


def _build_context(
    bundle: SourceBundle, live: LiveSnapshot | None, account: str | None
) -> _Context:
    orders_by_day: dict[str, dict[str, OrderRecord]] = {}
    orders_by_oid: dict[int, OrderRecord] = {}
    duplicate_order_oids: list[int] = []
    duplicate_order_events: list[str] = []
    for order in bundle.orders:
        day_slot = orders_by_day.setdefault(order.day, {})
        previous = day_slot.get(order.side_role)
        if previous is not None:
            duplicate_order_events.append(
                f"{order.day}: multiple {order.side_role} ORDER rows "
                f"({previous.event_id}, {order.event_id})"
            )
        day_slot[order.side_role] = order
        if order.oid in orders_by_oid:
            duplicate_order_oids.append(order.oid)
        else:
            orders_by_oid[order.oid] = order

    live_by_oid: dict[int, list[LiveFill]] = {}
    duplicate_live_tids: list[int] = []
    seen_tids: set[int] = set()
    if live is not None:
        for fill in live.fills:
            live_by_oid.setdefault(fill.oid, []).append(fill)
            if fill.tid in seen_tids:
                duplicate_live_tids.append(fill.tid)
            seen_tids.add(fill.tid)
        for rows in live_by_oid.values():
            rows.sort(key=lambda row: (row.time_ms, row.tid))

    aggregates = aggregate_by_oid(live.fills) if live is not None else {}
    chains = {oid: reconstruct_position_chain(tuple(rows)) for oid, rows in live_by_oid.items()}
    return _Context(
        bundle=bundle,
        account=account,
        live=live,
        orders_by_day=orders_by_day,
        orders_by_oid=orders_by_oid,
        duplicate_order_oids=sorted(set(duplicate_order_oids)),
        duplicate_order_events=duplicate_order_events,
        trades_by_day={trade.day: trade for trade in bundle.trades},
        cycles_by_day={cycle.day: cycle for cycle in bundle.raw.cycles},
        ledger_by_day={day.day: day for day in bundle.ledger_days},
        live_by_oid=live_by_oid,
        duplicate_live_tids=sorted(set(duplicate_live_tids)),
        aggregates=aggregates,
        chains=chains,
    )


# ---------------------------------------------------------------- 各チェック
def _check_journal_chain(ctx: _Context) -> Check:
    chain = ctx.bundle.journal_chain
    return _check(
        "journal-hash-chain",
        "ジャーナル（SIGNAL→INTENT→ORDER）のハッシュチェーン",
        failures=0 if chain.ok else 1,
        counts={"lines": chain.line_count, "first_bad_line": chain.first_bad_line},
        details=() if chain.ok else (chain.reason,),
        tolerance="seq 連続・prev_sha256 連結・行 sha256 再計算の完全一致",
    )


def _check_source_duplicates(ctx: _Context) -> Check:
    duplicates = {name: list(rows) for name, rows in ctx.bundle.duplicate_lines.items()}
    total = sum(len(rows) for rows in duplicates.values())
    details = [
        f"{name}: duplicate row lines {_sample(rows)}" for name, rows in duplicates.items() if rows
    ]
    details.extend(ctx.duplicate_order_events)
    details.extend(f"ORDER oid {oid} appears more than once" for oid in ctx.duplicate_order_oids)
    return _check(
        "source-duplicates",
        "重複行の検出（ジャーナル / trades / raw_events）",
        failures=total + len(ctx.duplicate_order_events) + len(ctx.duplicate_order_oids),
        counts={"duplicate_rows": total, "per_file": {k: len(v) for k, v in duplicates.items()}},
        details=details,
    )


def _check_oid_journal_trades(ctx: _Context) -> Check:
    journal_oids = [order.oid for order in ctx.bundle.orders]
    trade_oids = [leg.oid for trade in ctx.bundle.trades for leg in (trade.entry, trade.exit)]
    missing_in_trades = sorted(set(journal_oids) - set(trade_oids))
    orphan_in_trades = sorted(set(trade_oids) - set(journal_oids))
    return _check(
        "oid-journal-trades",
        "台帳内 1 対 1 対応（ジャーナル ORDER ↔ trades entry/exit）",
        failures=len(missing_in_trades) + len(orphan_in_trades),
        counts={
            "journal_orders": len(journal_oids),
            "trade_legs": len(trade_oids),
            "missing_in_trades": len(missing_in_trades),
            "orphan_in_trades": len(orphan_in_trades),
        },
        details=([f"trades に無い OID: {_sample(missing_in_trades)}"] if missing_in_trades else [])
        + ([f"trades にだけある OID: {_sample(orphan_in_trades)}"] if orphan_in_trades else []),
    )


def _check_oid_live(ctx: _Context) -> Check:
    if ctx.live is None:
        return _check(
            "oid-journal-live",
            "userFills の OID ↔ 台帳注文記録の 1 対 1 対応",
            failures=0,
            blocked=True,
            details=("実 fill（公開 /info 応答）が無いため照合できない",),
        )
    journal_oids = {order.oid for order in ctx.bundle.orders}
    live_oids = set(ctx.live_by_oid)
    missing_in_live = sorted(journal_oids - live_oids)
    orphan_in_live = sorted(live_oids - journal_oids)
    failures = len(missing_in_live) + len(orphan_in_live) + len(ctx.duplicate_live_tids)
    details = []
    if missing_in_live:
        details.append(f"userFills に無い台帳 OID（欠落）: {_sample(missing_in_live)}")
    if orphan_in_live:
        details.append(f"台帳に無い userFills OID（孤立）: {_sample(orphan_in_live)}")
    if ctx.duplicate_live_tids:
        details.append(f"userFills の tid 重複: {_sample(ctx.duplicate_live_tids)}")
    return _check(
        "oid-journal-live",
        "userFills の OID ↔ 台帳注文記録の 1 対 1 対応",
        failures=failures,
        counts={
            "ledger_oids": len(journal_oids),
            "live_oids": len(live_oids),
            "live_fills": len(ctx.live.fills),
            "missing_in_live": len(missing_in_live),
            "orphan_in_live": len(orphan_in_live),
            "duplicate_tids": len(ctx.duplicate_live_tids),
        },
        details=details,
        tolerance="OID 完全一致（欠落 0・孤立 0・tid 重複 0）",
    )


def _direction_side(direction: str, role: str) -> str:
    if direction == "SHORT":
        return "sell" if role == "entry" else "buy"
    if direction == "LONG":
        return "buy" if role == "entry" else "sell"
    raise ValueError(f"unknown direction: {direction!r}")


def _check_fill_aggregation(ctx: _Context) -> Check:
    if ctx.live is None:
        return _check(
            "fill-aggregation",
            "fill 集計（数量・出来高加重平均・fee）と台帳の一致",
            failures=0,
            blocked=True,
        )
    size_mismatches: list[str] = []
    price_mismatches: list[str] = []
    fee_mismatches: list[str] = []
    side_mismatches: list[str] = []
    coin_mismatches: list[str] = []
    missing: list[str] = []
    price_deltas: list[Decimal] = []
    fee_deltas: list[Decimal] = []
    price_tolerance = PRICE_DISPLAY_QUANTUM + USDC_QUANTUM
    for order in ctx.bundle.orders:
        aggregate = ctx.aggregates.get(order.oid)
        trade = ctx.trades_by_day.get(order.day)
        if aggregate is None or trade is None:
            missing.append(f"{order.day} {order.side_role} oid {order.oid}")
            continue
        if aggregate.size != order.fill_sz:
            size_mismatches.append(
                f"{order.day} {order.side_role} oid {order.oid}: "
                f"fills={dstr(aggregate.size)} ledger={dstr(order.fill_sz)}"
            )
        avg = aggregate.avg_px
        if avg is None:
            missing.append(f"{order.day} {order.side_role} oid {order.oid}: zero size")
            continue
        price_delta = avg - order.fill_px
        price_deltas.append(price_delta)
        if abs(price_delta) > price_tolerance:
            price_mismatches.append(
                f"{order.day} {order.side_role} oid {order.oid}: "
                f"fill_avg={dstr(avg)} ledger_px={dstr(order.fill_px)} "
                f"delta={dstr(price_delta)}"
            )
        fee_delta = order.fee_usdc - aggregate.fee
        fee_deltas.append(fee_delta)
        if abs(fee_delta) > fee_tolerance(aggregate.fill_count):
            fee_mismatches.append(
                f"{order.day} {order.side_role} oid {order.oid}: "
                f"ledger_fee={dstr(order.fee_usdc)} fills_fee={dstr(aggregate.fee)} "
                f"delta={dstr(fee_delta)}"
            )
        expected_side = _direction_side(trade.direction, order.side_role)
        if any(side != expected_side for side in aggregate.sides):
            side_mismatches.append(
                f"{order.day} {order.side_role} oid {order.oid}: "
                f"sides={aggregate.sides} expected={expected_side} ({trade.direction})"
            )
        if aggregate.coins != (trade.coin,):
            coin_mismatches.append(
                f"{order.day} {order.side_role} oid {order.oid}: "
                f"coins={aggregate.coins} expected={trade.coin}"
            )
    failures = (
        len(missing)
        + len(size_mismatches)
        + len(price_mismatches)
        + len(fee_mismatches)
        + len(side_mismatches)
        + len(coin_mismatches)
    )
    return _check(
        "fill-aggregation",
        "fill 集計（数量・出来高加重平均・fee）と台帳の一致",
        failures=failures,
        counts={
            "orders_checked": len(ctx.bundle.orders),
            "missing": len(missing),
            "size_mismatches": len(size_mismatches),
            "price_mismatches": len(price_mismatches),
            "fee_mismatches": len(fee_mismatches),
            "side_mismatches": len(side_mismatches),
            "coin_mismatches": len(coin_mismatches),
            "max_price_delta": max_abs(price_deltas),
            "max_fee_delta": max_abs(fee_deltas),
        },
        details=(
            missing[:SAMPLE_LIMIT]
            + size_mismatches[:SAMPLE_LIMIT]
            + price_mismatches[:SAMPLE_LIMIT]
            + fee_mismatches[:SAMPLE_LIMIT]
            + side_mismatches[:SAMPLE_LIMIT]
            + coin_mismatches[:SAMPLE_LIMIT]
        ),
        tolerance=(
            "数量は完全一致。価格は表示切り捨て 0.1 USDC 以内。"
            "fee は fill 数+1 × 1e-6 USDC 以内（fill ごとの 6 桁量子化）"
        ),
    )


def _check_partial_fills(ctx: _Context) -> Check:
    total_fills = sum(aggregate.fill_count for aggregate in ctx.aggregates.values())
    multi = {oid: agg.fill_count for oid, agg in ctx.aggregates.items() if agg.fill_count > 1}
    extra_prints = total_fills - len(ctx.aggregates)
    details = [
        f"oid {oid}: {ctx.aggregates[oid].fill_count} fills, "
        f"size={dstr(ctx.aggregates[oid].size)}, "
        f"avg_px={dstr(ctx.aggregates[oid].avg_px)}, "
        f"fee={dstr(ctx.aggregates[oid].fee)}"
        for oid in sorted(multi)
    ]
    return _check(
        "partial-fills",
        "部分約定（同一 OID で複数 fill）の集計",
        failures=0,
        counts={
            "orders": len(ctx.aggregates),
            "fill_prints": total_fills,
            "multi_fill_orders": len(multi),
            "extra_prints": extra_prints,
            "multi_fill_oids": sorted(multi),
        },
        details=details,
        tolerance="数量は完全一致で集計。trades の partial 真偽値は複数約定の指標に使わない",
    )


def _check_time_order(ctx: _Context) -> Check:
    if ctx.live is None:
        return _check("time-order", "時刻の単調性と約定時刻の窓", failures=0, blocked=True)
    payload_violations = [
        f"index {index}: {previous.time_ms} -> {following.time_ms}"
        for index, (previous, following) in enumerate(
            zip(ctx.live.fills, ctx.live.fills[1:], strict=False)
        )
        if following.time_ms > previous.time_ms
    ]
    inversions = [item for chain in ctx.chains.values() for item in chain.time_inversions]
    out_of_window: list[str] = []
    equal_time_groups = 0
    for order in ctx.bundle.orders:
        rows = ctx.live_by_oid.get(order.oid, [])
        if len({row.time_ms for row in rows}) < len(rows):
            equal_time_groups += 1
        lower = order.ts_ms - order.duration_ms - 1000
        upper = order.ts_ms + 1000
        for row in rows:
            if not (lower <= row.time_ms <= upper):
                out_of_window.append(
                    f"{order.day} {order.side_role} oid {order.oid}: fill time "
                    f"{row.time_ms} outside [{lower}, {upper}] (duration={order.duration_ms}ms)"
                )
    failures = len(payload_violations) + len(inversions) + len(out_of_window)
    return _check(
        "time-order",
        "時刻の単調性（API 応答順・同一 OID の約定順・記録窓）",
        failures=failures,
        counts={
            "api_order_violations": len(payload_violations),
            "chain_inversions": len(inversions),
            "out_of_window": len(out_of_window),
            "equal_timestamp_oid_groups": equal_time_groups,
        },
        details=(payload_violations + inversions + out_of_window)[:SAMPLE_LIMIT],
        tolerance="API は新しい順（非増加）。同一 OID の鎖は時刻非減少。同時刻は許容",
    )


def _check_position_chain(ctx: _Context) -> Check:
    if ctx.live is None:
        return _check(
            "position-chain",
            "startPosition 連鎖（建玉の連続性）",
            failures=0,
            blocked=True,
        )
    breaks = [item for chain in ctx.chains.values() for item in chain.breaks]
    continuity: list[str] = []
    for day in ctx.cycles_by_day:
        orders = ctx.orders_by_day.get(day, {})
        entry = orders.get("entry")
        exit_ = orders.get("exit")
        if entry is None or exit_ is None:
            continuity.append(f"{day}: entry/exit ORDER row missing")
            continue
        entry_chain = ctx.chains.get(entry.oid)
        exit_chain = ctx.chains.get(exit_.oid)
        if entry_chain is None or exit_chain is None:
            continuity.append(f"{day}: fill chain missing for entry/exit oid")
            continue
        if entry_chain.start_position != 0:
            continuity.append(
                f"{day}: entry chain starts at {entry_chain.start_position} (expected 0)"
            )
        if entry_chain.end_position != exit_chain.start_position:
            continuity.append(
                f"{day}: entry ends at {entry_chain.end_position}, "
                f"exit starts at {exit_chain.start_position}"
            )
        if exit_chain.end_position != 0:
            continuity.append(f"{day}: exit chain ends at {exit_chain.end_position} (expected 0)")
    return _check(
        "position-chain",
        "startPosition 連鎖（建玉の連続性）",
        failures=len(breaks) + len(continuity),
        counts={
            "oids_checked": len(ctx.chains),
            "breaks": len(breaks),
            "continuity_errors": len(continuity),
        },
        details=(breaks + continuity)[:SAMPLE_LIMIT],
        tolerance="各 OID の鎖が全 fill を覆い、サイクル開始 0 → 終了 0 で連続する",
    )


@dataclass(frozen=True)
class _CycleFigures:
    day: str
    direction: str
    size: Decimal
    fill_count: int
    funding_rows: int
    exact_pnl: Decimal
    reported_pnl: Decimal
    fees: Decimal
    funding: Decimal
    delta_equity: Decimal
    fill_residual: Decimal
    reported_residual: Decimal
    quantization: Decimal | None
    attributed_residual: Decimal
    journal_net_residual: Decimal
    journal_gross_residual: Decimal


def _cycle_figures(ctx: _Context) -> tuple[list[_CycleFigures], list[str]]:
    figures: list[_CycleFigures] = []
    problems: list[str] = []
    if ctx.live is None:
        return figures, ["実 fill が無いためサイクル照合は不能"]
    for day, cycle in ctx.cycles_by_day.items():
        trade = ctx.trades_by_day.get(day)
        orders = ctx.orders_by_day.get(day, {})
        entry = orders.get("entry")
        exit_ = orders.get("exit")
        if trade is None or entry is None or exit_ is None:
            problems.append(f"{day}: trade/ORDER row missing")
            continue
        entry_fills = ctx.live_by_oid.get(entry.oid, [])
        exit_fills = ctx.live_by_oid.get(exit_.oid, [])
        if not entry_fills or not exit_fills:
            problems.append(f"{day}: entry/exit fills missing (oid {entry.oid}/{exit_.oid})")
            continue
        entry_size = sum((row.fill.sz for row in entry_fills), Decimal(0))
        exit_size = sum((row.fill.sz for row in exit_fills), Decimal(0))
        size = exit_size
        if entry_size != exit_size:
            problems.append(f"{day}: entry size {entry_size} != exit size {exit_size}")
        fills = [*entry_fills, *exit_fills]
        exact = exact_realized_pnl(
            entry_fills=entry_fills, exit_fills=exit_fills, direction=trade.direction
        )
        reported = sum((row.fill.closed_pnl for row in fills), Decimal(0))
        fees = sum((row.fill.fee for row in fills), Decimal(0))
        funding_rows = [
            row for row in ctx.live.funding if cycle.pre_ts_ms <= row.time_ms <= cycle.post_ts_ms
        ]
        funding = sum((row.delta for row in funding_rows), Decimal(0))
        entry_notional, entry_size = fill_notional_and_size(entry_fills)
        quantization = quantization_effect(
            entry_px_recorded=cycle.entry_position_px,
            entry_notional=entry_notional,
            entry_size=entry_size,
            direction=trade.direction,
        )
        fill_net = exact - fees + funding
        reported_net = reported - fees + funding
        fill_residual = cycle.delta_equity - fill_net
        reported_residual = cycle.delta_equity - reported_net
        journal_net_residual = (
            Decimal(0) if cycle.journal_net is None else cycle.delta_equity - cycle.journal_net
        )
        journal_gross_residual = (
            Decimal(0) if cycle.journal_gross is None else cycle.journal_gross - reported
        )
        figures.append(
            _CycleFigures(
                day=day,
                direction=trade.direction,
                size=size,
                fill_count=len(fills),
                funding_rows=len(funding_rows),
                exact_pnl=exact,
                reported_pnl=reported,
                fees=fees,
                funding=funding,
                delta_equity=cycle.delta_equity,
                fill_residual=fill_residual,
                reported_residual=reported_residual,
                quantization=quantization,
                attributed_residual=(reported_residual - (quantization or Decimal(0))),
                journal_net_residual=journal_net_residual,
                journal_gross_residual=journal_gross_residual,
            )
        )
    return figures, problems


def _cycle_rows(figures: Sequence[_CycleFigures]) -> dict[str, Any]:
    return {
        item.day: {
            "direction": item.direction,
            "size": dstr(item.size),
            "fills": item.fill_count,
            "exact_pnl_fills": dstr(item.exact_pnl),
            "reported_closed_pnl": dstr(item.reported_pnl),
            "fees": dstr(item.fees),
            "funding": dstr(item.funding),
            "delta_equity": dstr(item.delta_equity),
            "fill_derived_residual": dstr(item.fill_residual),
            "reported_residual": dstr(item.reported_residual),
            "entry_px_quantization": dstr(item.quantization),
            "attributed_residual": dstr(item.attributed_residual),
            "journal_net_residual": dstr(item.journal_net_residual),
            "journal_gross_residual": dstr(item.journal_gross_residual),
        }
        for item in figures
    }


def _check_cycle_equity(ctx: _Context, figures: list[_CycleFigures], problems: list[str]) -> Check:
    if ctx.live is None:
        return _check("cycle-equity", "サイクル Δequity 対 fill 由来 net", failures=0, blocked=True)
    failures = list(problems)
    for item in figures:
        tolerance = fee_tolerance(item.fill_count + item.funding_rows)
        if abs(item.fill_residual) > tolerance:
            failures.append(
                f"{item.day}: Δequity={dstr(item.delta_equity)} "
                f"net(fill)={dstr(item.exact_pnl - item.fees + item.funding)} "
                f"residual={dstr(item.fill_residual)} > tol={dstr(tolerance)}"
            )
    return _check(
        "cycle-equity",
        "サイクル Δequity 対 fill 由来 net（fee・funding 符号込み）",
        failures=len(failures),
        counts={
            "cycles": len(figures),
            "max_abs_residual": max_abs([item.fill_residual for item in figures]),
            "per_cycle": _cycle_rows(figures),
        },
        details=failures[:SAMPLE_LIMIT],
        tolerance="fill ごとの fee 1e-6 + 口座価値 1e-6 = (fill 数 + 1) × 1e-6 USDC",
    )


def _check_reported_closedpnl(
    ctx: _Context, figures: list[_CycleFigures], problems: list[str]
) -> Check:
    if ctx.live is None:
        return _check(
            "reported-closedpnl", "closedPnl と記録 entryPx の整合", failures=0, blocked=True
        )
    failures = list(problems)
    for item in figures:
        tolerance = fee_tolerance(item.fill_count)
        if abs(item.attributed_residual) > tolerance:
            failures.append(
                f"{item.day}: reported residual={dstr(item.reported_residual)} "
                f"- entryPx quantization={dstr(item.quantization)} "
                f"= {dstr(item.attributed_residual)} > tol={dstr(tolerance)}"
            )
    return _check(
        "reported-closedpnl",
        "closedPnl と記録 entryPx の整合（残差の原因分解）",
        failures=len(failures),
        counts={
            "max_abs_reported_residual": max_abs([item.reported_residual for item in figures]),
            "max_abs_quantization": max_abs(
                [item.quantization for item in figures if item.quantization is not None]
            ),
            "max_abs_attributed_residual": max_abs([item.attributed_residual for item in figures]),
        },
        details=failures[:SAMPLE_LIMIT],
        tolerance=(
            "closedPnl − (entryPx − exit 平均) × 数量 の差が (fill 数 + 1) × 1e-6 USDC 以内"
        ),
    )


def _check_journal_net(ctx: _Context, figures: list[_CycleFigures], problems: list[str]) -> Check:
    if ctx.live is None:
        return _check(
            "journal-net-precision",
            "ジャーナル記録 net の精度（Δequity との差）",
            failures=0,
            blocked=True,
        )
    failures = list(problems)
    for item in figures:
        tolerance = journal_net_tolerance(fill_count=item.fill_count, size=item.size)
        if abs(item.journal_net_residual) > tolerance:
            failures.append(
                f"{item.day}: Δequity − journal net = {dstr(item.journal_net_residual)} "
                f"> tol={dstr(tolerance)}"
            )
        gross_tolerance = Decimal(2) * PRICE_DISPLAY_QUANTUM * item.size
        if abs(item.journal_gross_residual) > gross_tolerance:
            failures.append(
                f"{item.day}: journal gross − closedPnl = "
                f"{dstr(item.journal_gross_residual)} > tol={dstr(gross_tolerance)}"
            )
    return _check(
        "journal-net-precision",
        "ジャーナル記録 net の精度（Δequity との差）",
        failures=len(failures),
        counts={
            "max_abs_journal_net_residual": max_abs(
                [item.journal_net_residual for item in figures]
            ),
            "max_abs_journal_gross_residual": max_abs(
                [item.journal_gross_residual for item in figures]
            ),
        },
        details=failures[:SAMPLE_LIMIT],
        tolerance=(
            "fee 量子化 + entry/exit の fill_px を 0.1 で切った影響 "
            "(fill 数+1)×1e-6 + 2×0.1×数量 USDC"
        ),
    )


def _check_funding(ctx: _Context, figures: list[_CycleFigures]) -> Check:
    if ctx.live is None:
        return _check("funding", "funding の実額と符号", failures=0, blocked=True)
    funding_rows = ctx.live.funding
    total = sum((row.delta for row in funding_rows), Decimal(0))
    expected_events = 0
    for day in ctx.cycles_by_day:
        orders = ctx.orders_by_day.get(day, {})
        entry = orders.get("entry")
        exit_ = orders.get("exit")
        if entry is None or exit_ is None:
            continue
        entry_rows = ctx.live_by_oid.get(entry.oid, [])
        exit_rows = ctx.live_by_oid.get(exit_.oid, [])
        if entry_rows and exit_rows:
            expected_events += len(hour_boundaries(entry_rows[0].time_ms, exit_rows[-1].time_ms))
    outside = [
        row.time_ms
        for row in funding_rows
        if not any(
            cycle.pre_ts_ms <= row.time_ms <= cycle.post_ts_ms
            for cycle in ctx.cycles_by_day.values()
        )
    ]
    cycle_funding = {
        day: sum(
            (
                row.delta
                for row in funding_rows
                if cycle.pre_ts_ms <= row.time_ms <= cycle.post_ts_ms
            ),
            Decimal(0),
        )
        for day, cycle in ctx.cycles_by_day.items()
    }
    failures: list[str] = []
    if outside:
        failures.append(f"サイクル窓外の funding: {_sample(outside)}")
    for ledger_day in ctx.bundle.ledger_days:
        expected = cycle_funding.get(ledger_day.day, Decimal(0))
        if abs(ledger_day.funding_balance - expected) > USDC_QUANTUM:
            failures.append(
                f"{ledger_day.day}: ledger funding_balance "
                f"{dstr(ledger_day.funding_balance)} != cycle funding {dstr(expected)}"
            )
    for trade in ctx.bundle.trades:
        expected = cycle_funding.get(trade.day, Decimal(0))
        if abs(trade.funding_usdc - expected) > USDC_QUANTUM:
            failures.append(
                f"{trade.day}: trades funding_usdc {dstr(trade.funding_usdc)} != "
                f"cycle funding {dstr(expected)}"
            )
    if sorted({row.token for row in funding_rows}) not in ([], ["USDC"]):
        failures.append(
            f"funding token が USDC 以外: {sorted({row.token for row in funding_rows})}"
        )
    return _check(
        "funding",
        "funding の実額と符号（受取=正 / 支払=負）",
        failures=len(failures),
        counts={
            "funding_rows": len(funding_rows),
            "funding_total": total,
            "expected_hourly_events": expected_events,
            "outside_cycle_window": len(outside),
            "cycles_with_funding": sum(1 for value in cycle_funding.values() if value != 0),
        },
        details=failures,
        tolerance=(
            "userFunding の実応答（件数 0 を含む）と台帳 funding_balance / "
            "trades funding_usdc が 1e-6 USDC 以内で一致"
        ),
    )


def _check_ledger_vs_raw(ctx: _Context) -> Check:
    failures: list[str] = []
    equity_deltas: list[Decimal] = []
    pnl_deltas: list[Decimal] = []
    for ledger in ctx.bundle.ledger_days:
        cycle = ctx.cycles_by_day.get(ledger.day)
        if cycle is None:
            failures.append(f"{ledger.day}: raw_events にサイクルが無い")
            continue
        equity_delta = ledger.equity - cycle.post_equity
        equity_deltas.append(equity_delta)
        if abs(equity_delta) > LEDGER_QUANTUM / 2:
            failures.append(
                f"{ledger.day}: ledger equity {dstr(ledger.equity)} != "
                f"post accountValue {dstr(cycle.post_equity)} (delta {dstr(equity_delta)})"
            )
        if cycle.journal_net is not None:
            pnl_delta = ledger.daily_pnl - cycle.journal_net
            pnl_deltas.append(pnl_delta)
            if abs(pnl_delta) > LEDGER_QUANTUM / 2:
                failures.append(
                    f"{ledger.day}: daily_pnl {dstr(ledger.daily_pnl)} != "
                    f"post net_pnl {dstr(cycle.journal_net)} (delta {dstr(pnl_delta)})"
                )
        cumulative_expected = sum(
            (day.daily_pnl for day in ctx.bundle.ledger_days if day.day <= ledger.day),
            Decimal(0),
        )
        if abs(ledger.cumulative_pnl - cumulative_expected) > 2 * LEDGER_QUANTUM:
            failures.append(
                f"{ledger.day}: cumulative_pnl {dstr(ledger.cumulative_pnl)} != "
                f"累積 daily_pnl {dstr(cumulative_expected)}"
            )
        if ledger.position != 0:
            failures.append(f"{ledger.day}: position {dstr(ledger.position)} != 0")
        cumulative_trades = sum(1 for trade in ctx.bundle.trades if trade.day <= ledger.day)
        if ledger.trades_count != cumulative_trades:
            failures.append(
                f"{ledger.day}: trades_count {ledger.trades_count} != "
                f"累積 trades {cumulative_trades}"
            )
    return _check(
        "ledger-vs-raw",
        "台帳 equity / daily_pnl ↔ raw_events の post_state",
        failures=len(failures),
        counts={
            "days": len(ctx.bundle.ledger_days),
            "max_abs_equity_delta": max_abs(equity_deltas),
            "max_abs_daily_pnl_delta": max_abs(pnl_deltas),
        },
        details=failures[:SAMPLE_LIMIT],
        tolerance="台帳は 4 桁表示のため |差| <= 5e-5 USDC",
    )


def _check_locks(ctx: _Context) -> Check:
    failures: list[str] = []
    for lock in ctx.bundle.raw.locks:
        pairs = {
            "killswitch": (lock.killswitch_acquired, lock.killswitch_released),
            "execution": (lock.execution_acquired, lock.execution_released),
        }
        for name, (acquired, released) in pairs.items():
            if (acquired, released) != (1, 1):
                failures.append(f"{lock.day}: {name} lock acquired={acquired} released={released}")
    for ledger in ctx.bundle.ledger_days:
        governance = ledger.governance
        if governance.get("position_returned_to_zero") is not True:
            failures.append(f"{ledger.day}: position_returned_to_zero is not true")
        if governance.get("killswitch_lock_logged") is not True:
            failures.append(f"{ledger.day}: killswitch_lock_logged is not true")
        if governance.get("unfilled_orders_remaining") != 0:
            failures.append(f"{ledger.day}: unfilled_orders_remaining != 0")
    return _check(
        "locks-and-governance",
        "キルスイッチ/実行ロックの取得・解放と governance フラグ",
        failures=len(failures),
        counts={
            "ledger_days": len(ctx.bundle.ledger_days),
            "days_with_lock_balance": sum(
                1
                for lock in ctx.bundle.raw.locks
                if (lock.killswitch_acquired, lock.killswitch_released) == (1, 1)
                and (lock.execution_acquired, lock.execution_released) == (1, 1)
            ),
        },
        details=failures[:SAMPLE_LIMIT],
        tolerance="各日 1 回取得・1 回解放、建玉 0・未約定 0",
    )


def _check_account_and_state(ctx: _Context) -> Check:
    if ctx.live is None or ctx.live.clearinghouse is None:
        return _check("account-and-state", "口座アドレスと現在状態", failures=0, blocked=True)
    failures: list[str] = []
    scorecard = ctx.bundle.scorecard_account
    if scorecard is None:
        failures.append("評価レポートから対象口座を読めない")
    elif ctx.account is not None and scorecard != ctx.account:
        failures.append(f"scorecard account {scorecard} != 指定 account {ctx.account}")
    if ctx.account is not None and ctx.live.account != ctx.account:
        failures.append(f"live snapshot account {ctx.live.account} != 指定 account {ctx.account}")
    last_equity = ctx.bundle.ledger_days[-1].equity
    margin = ctx.live.clearinghouse.get("marginSummary") or {}
    account_value = margin.get("accountValue")
    if account_value is None:
        failures.append("clearinghouseState.marginSummary.accountValue が無い")
    else:
        delta = Decimal(str(account_value)) - last_equity
        if abs(delta) > LEDGER_QUANTUM / 2:
            failures.append(
                f"現在 accountValue {account_value} != 最終 ledger {dstr(last_equity)} "
                f"(delta {dstr(delta)})"
            )
    if ctx.live.clearinghouse.get("assetPositions"):
        failures.append("現在も建玉が残っている")
    if ctx.live.open_orders:
        failures.append(f"未約定注文が {len(ctx.live.open_orders)} 件残っている")
    return _check(
        "account-and-state",
        "口座アドレスと現在状態（建玉 0・未約定 0）",
        failures=len(failures),
        counts={
            "scorecard_account": scorecard,
            "requested_account": ctx.account,
            "open_orders": len(ctx.live.open_orders),
            "account_value": account_value,
        },
        details=failures,
        tolerance="address 完全一致。equity は 4 桁台帳との差 <= 5e-5 USDC",
    )


def _check_trades_vs_journal(ctx: _Context) -> Check:
    failures: list[str] = []
    tolerance_price = USDC_QUANTUM
    for trade in ctx.bundle.trades:
        orders = ctx.orders_by_day.get(trade.day, {})
        for role, leg in (("entry", trade.entry), ("exit", trade.exit)):
            order = orders.get(role)
            if order is None:
                failures.append(f"{trade.day}: {role} ORDER row missing")
                continue
            if order.oid != leg.oid:
                failures.append(f"{trade.day} {role}: oid {order.oid} != trades {leg.oid}")
            if order.fill_sz != leg.size:
                failures.append(
                    f"{trade.day} {role}: size {dstr(order.fill_sz)} != {dstr(leg.size)}"
                )
            if abs(order.fill_px - leg.fill_px) > tolerance_price:
                failures.append(
                    f"{trade.day} {role}: px {dstr(order.fill_px)} != {dstr(leg.fill_px)}"
                )
            if abs(order.fee_usdc - leg.fee_usdc) > USDC_QUANTUM:
                failures.append(
                    f"{trade.day} {role}: fee {dstr(order.fee_usdc)} != {dstr(leg.fee_usdc)}"
                )
        expected_net = trade.gross_pnl_usdc - trade.fees_usdc + trade.funding_usdc
        if abs(trade.net_pnl_usdc - expected_net) > USDC_QUANTUM:
            failures.append(
                f"{trade.day}: net {dstr(trade.net_pnl_usdc)} != gross-fees+funding "
                f"{dstr(expected_net)}"
            )
    return _check(
        "trades-vs-journal",
        "trades.jsonl ↔ ジャーナル ORDER の一致",
        failures=len(failures),
        counts={"trades": len(ctx.bundle.trades)},
        details=failures[:SAMPLE_LIMIT],
        tolerance="OID・数量は完全一致、価格/fee は 1e-6 USDC 以内",
    )


# ---------------------------------------------------------------------- 集約
@dataclass(frozen=True)
class VerificationResult:
    verdict: str
    checks: tuple[Check, ...]
    metrics: Mapping[str, Any]
    inputs: tuple[SourceFile, ...]
    account: str | None
    live_provenance: Mapping[str, Any]
    problems: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return self.verdict == "PASS"

    def check(self, check_id: str) -> Check:
        for item in self.checks:
            if item.id == check_id:
                return item
        raise KeyError(check_id)

    def as_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "account": self.account,
            "checks": [check.as_dict() for check in self.checks],
            "metrics": jsonable(self.metrics),
            "inputs": [item.as_dict() for item in self.inputs],
            "live_provenance": jsonable(self.live_provenance),
            "problems": list(self.problems),
        }


def _verdict(checks: Sequence[Check]) -> str:
    statuses = {check.status for check in checks}
    if Status.FAIL in statuses:
        return "FAIL"
    if Status.BLOCKED in statuses:
        return "UNVERIFIABLE"
    if Status.WARN in statuses:
        return "PASS_WITH_WARNINGS"
    return "PASS"


def verify(
    bundle: SourceBundle,
    live: LiveSnapshot | None,
    *,
    account: str | None = None,
) -> VerificationResult:
    """保存済み台帳と実 fill を突き合わせる（読み取りのみ・fail-closed）。"""
    ctx = _build_context(bundle, live, account)
    figures, cycle_problems = _cycle_figures(ctx)
    if ctx.live is None:
        cycle_problems = ["実 fill が無いためサイクル照合は不能"]
    checks = (
        _check_journal_chain(ctx),
        _check_source_duplicates(ctx),
        _check_oid_journal_trades(ctx),
        _check_oid_live(ctx),
        _check_fill_aggregation(ctx),
        _check_partial_fills(ctx),
        _check_time_order(ctx),
        _check_position_chain(ctx),
        _check_cycle_equity(ctx, figures, cycle_problems),
        _check_reported_closedpnl(ctx, figures, cycle_problems),
        _check_journal_net(ctx, figures, cycle_problems),
        _check_funding(ctx, figures),
        _check_ledger_vs_raw(ctx),
        _check_locks(ctx),
        _check_account_and_state(ctx),
        _check_trades_vs_journal(ctx),
    )
    metrics = {
        "ledger_orders": len(bundle.orders),
        "trades": len(bundle.trades),
        "cycles": len(bundle.raw.cycles),
        "live_fills": 0 if live is None else len(live.fills),
        "live_oids": 0 if live is None else len(ctx.live_by_oid),
        "multi_fill_orders": sum(1 for agg in ctx.aggregates.values() if agg.fill_count > 1),
        "cycles_detail": _cycle_rows(figures),
        "journal_chain_lines": bundle.journal_chain.line_count,
    }
    return VerificationResult(
        verdict=_verdict(checks),
        checks=checks,
        metrics=metrics,
        inputs=bundle.files,
        account=account,
        live_provenance=(live.provenance if live is not None else {}),
        problems=tuple(cycle_problems),
    )
