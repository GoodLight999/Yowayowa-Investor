# ruff: noqa: RUF002, RUF003 — 日本語の句読点（全角）は本文テキストであり誤字ではない

"""保存済み実データと ``/info`` 公開応答の読み取り（書き込みなし）。

入力は ``data/hl_testnet_s07/`` の 4 種（raw_events / execution_journal /
trades / daily_ledger）と、公開アドレスで取得した ``userFills`` /
``userFunding`` / ``clearinghouseState`` / ``openOrders``。すべて **読み取り
専用** で開き、検証器は入力ディレクトリへ一切書かない（runner が出力先を
入力ディレクトリ配下から拒否する）。
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from yowayowa.hl_exec.journal import VerifyResult, entries, verify_chain
from yowayowa.hl_exec.s07_authenticity.models import (
    AuthenticityError,
    CycleState,
    FundingRow,
    LedgerDay,
    LiveFill,
    LiveSnapshot,
    LockCounts,
    OrderRecord,
    SourceFile,
    TradeLeg,
    TradeRecord,
    iso_to_ms,
    to_decimal,
    to_int,
)
from yowayowa.hl_exec.s07_offline.fills import FillIntegrityError, parse_fill

JOURNAL_FILE = "execution_journal.jsonl"
TRADES_FILE = "trades.jsonl"
RAW_EVENTS_FILE = "raw_events.jsonl"
LEDGER_FILE = "daily_ledger.json"
SCORECARD_FILE = "reports/S07_TESTNET_SCORECARD.md"

_ADDRESS_RE = re.compile(r"0x[0-9a-fA-F]{40}")
_SCORECARD_ACCOUNT_RE = re.compile(r"対象口座\*\*:\s*`(0x[0-9a-fA-F]{40})`")


def file_digest(path: str | Path, *, rows: int | None = None) -> SourceFile:
    """ファイルの sha256 とサイズを読む（読み取りのみ）。"""
    resolved = Path(path)
    digest = hashlib.sha256()
    with open(resolved, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return SourceFile(
        path=str(resolved),
        sha256=digest.hexdigest(),
        size=resolved.stat().st_size,
        rows=rows,
    )


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    """JSONL を順序どおり読む。空行は無視し、不正 JSON は行番号付きで拒否。"""
    rows: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                row = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise AuthenticityError(f"{path}: line {line_no} is not valid JSON: {exc}") from exc
            if not isinstance(row, dict):
                raise AuthenticityError(f"{path}: line {line_no} is not an object")
            rows.append(row)
    return rows


def read_json(path: str | Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise AuthenticityError(f"{path}: top-level JSON must be an object")
    return payload


def read_json_value(path: str | Path) -> Any:
    """トップレベルが配列でも読む（``userFills`` の保存応答など）。"""
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def object_sha256(payload: Any) -> str:
    """応答 JSON の正準化 sha256（再現・改変検出用）。"""
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def canonical(obj: Mapping[str, Any]) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def duplicate_rows(rows: Sequence[Mapping[str, Any]]) -> list[int]:
    """同一内容の行が 2 回以上現れたとき、2 回目以降の行番号（1 始まり）を返す。"""
    seen: dict[str, int] = {}
    duplicates: list[int] = []
    for index, row in enumerate(rows, start=1):
        key = canonical(row)
        if key in seen:
            duplicates.append(index)
        else:
            seen[key] = index
    return duplicates


def parse_orders(journal_entries: Iterable[Any]) -> list[OrderRecord]:
    """ジャーナル ORDER 段を台帳の注文記録として取り出す。"""
    orders: list[OrderRecord] = []
    for entry in journal_entries:
        if entry.stage != "ORDER":
            continue
        payload = entry.payload
        oid = to_int(payload.get("oid"), what=f"{entry.event_id}.oid")
        ts_ms = iso_to_ms(entry.ts, what=f"{entry.event_id}.ts")
        substage = str(payload.get("substage", ""))
        if substage not in ("entry_fill", "exit_fill"):
            raise AuthenticityError(f"{entry.event_id}: unknown ORDER substage {substage!r}")
        orders.append(
            OrderRecord(
                day=entry.ts[:10],
                event_id=entry.event_id,
                ts_ms=ts_ms,
                substage=substage,
                oid=oid,
                fill_px=to_decimal(payload.get("fill_px"), what="ORDER.fill_px"),
                fill_sz=to_decimal(payload.get("fill_sz"), what="ORDER.fill_sz"),
                fee_usdc=to_decimal(payload.get("fee_usdc"), what="ORDER.fee_usdc"),
                duration_ms=to_int(payload.get("duration_ms", 0), what="ORDER.duration_ms"),
                slippage_bp=(
                    None
                    if payload.get("slippage_bp") is None
                    else to_decimal(payload["slippage_bp"], what="ORDER.slippage_bp")
                ),
            )
        )
    return orders


def _trade_leg(raw: Mapping[str, Any], *, what: str) -> TradeLeg:
    return TradeLeg(
        oid=to_int(raw.get("oid"), what=f"{what}.oid"),
        fill_px=to_decimal(raw.get("fill_px"), what=f"{what}.fill_px"),
        size=to_decimal(raw.get("size"), what=f"{what}.size"),
        fee_usdc=to_decimal(raw.get("fee_usdc"), what=f"{what}.fee_usdc"),
        partial=bool(raw.get("partial", False)),
    )


def parse_trades(rows: Sequence[Mapping[str, Any]]) -> list[TradeRecord]:
    trades: list[TradeRecord] = []
    for index, row in enumerate(rows):
        entry = row.get("entry")
        exit_ = row.get("exit")
        if not isinstance(entry, Mapping) or not isinstance(exit_, Mapping):
            raise AuthenticityError(f"trade[{index}] lacks entry/exit objects")
        trades.append(
            TradeRecord(
                day=str(row.get("day", "")),
                direction=str(row.get("direction", "")),
                coin=str(row.get("coin", "")),
                entry=_trade_leg(entry, what=f"trade[{index}].entry"),
                exit=_trade_leg(exit_, what=f"trade[{index}].exit"),
                gross_pnl_usdc=to_decimal(row.get("gross_pnl_usdc"), what="trade.gross_pnl_usdc"),
                fees_usdc=to_decimal(row.get("fees_usdc"), what="trade.fees_usdc"),
                funding_usdc=to_decimal(row.get("funding_usdc"), what="trade.funding_usdc"),
                net_pnl_usdc=to_decimal(row.get("net_pnl_usdc"), what="trade.net_pnl_usdc"),
            )
        )
    return trades


@dataclass(frozen=True)
class RawScan:
    cycles: tuple[CycleState, ...]
    locks: tuple[LockCounts, ...]


def scan_raw_events(rows: Sequence[Mapping[str, Any]]) -> RawScan:
    """``raw_events`` から 1 日 1 サイクルの pre/post とロック回数を再構成する。"""
    day = ""
    locks: dict[str, dict[str, int]] = {}
    cycles: list[CycleState] = []
    open_cycle: dict[str, Any] | None = None
    entry_px: Decimal | None = None

    for index, row in enumerate(rows):
        stage = str(row.get("stage", ""))
        if row.get("day"):
            day = str(row["day"])
        if not day:
            raise AuthenticityError(f"raw_events[{index}] appears before any day label")
        bucket = locks.setdefault(
            day,
            {
                "killswitch_acquired": 0,
                "killswitch_released": 0,
                "execution_acquired": 0,
                "execution_released": 0,
            },
        )
        if stage == "killswitch_lock_acquired":
            bucket["killswitch_acquired"] += 1
        elif stage == "killswitch_lock_released":
            bucket["killswitch_released"] += 1
        elif stage == "execution_lock_acquired":
            bucket["execution_acquired"] += 1
        elif stage == "execution_lock_released":
            bucket["execution_released"] += 1
        elif stage == "pre_state":
            if open_cycle is not None:
                raise AuthenticityError(f"{day}: pre_state repeated before post_state")
            open_cycle = {
                "day": day,
                "pre_equity": to_decimal(row.get("accountValue"), what="pre.accountValue"),
                "pre_ts_ms": iso_to_ms(str(row.get("ts")), what="pre.ts"),
            }
            entry_px = None
        elif stage == "in_trade_positions":
            positions = row.get("positions") or []
            if positions:
                first = positions[0]
                position = first.get("position") if isinstance(first, Mapping) else None
                if isinstance(position, Mapping) and position.get("entryPx") is not None:
                    entry_px = to_decimal(position["entryPx"], what="position.entryPx")
        elif stage == "post_state":
            if open_cycle is None:
                raise AuthenticityError(f"{day}: post_state without pre_state")
            cycles.append(
                CycleState(
                    day=day,
                    pre_equity=open_cycle["pre_equity"],
                    post_equity=to_decimal(row.get("accountValue"), what="post.accountValue"),
                    pre_ts_ms=open_cycle["pre_ts_ms"],
                    post_ts_ms=iso_to_ms(str(row.get("ts")), what="post.ts"),
                    journal_gross=(
                        None
                        if row.get("gross_pnl") is None
                        else to_decimal(row["gross_pnl"], what="post.gross_pnl")
                    ),
                    journal_net=(
                        None
                        if row.get("net_pnl") is None
                        else to_decimal(row["net_pnl"], what="post.net_pnl")
                    ),
                    entry_position_px=entry_px,
                )
            )
            open_cycle = None
            entry_px = None

    if open_cycle is not None:
        raise AuthenticityError(f"{open_cycle['day']}: pre_state without post_state")
    seen_days = [cycle.day for cycle in cycles]
    if len(seen_days) != len(set(seen_days)):
        raise AuthenticityError(f"duplicate cycle days: {seen_days}")
    lock_rows = tuple(LockCounts(day=key, **value) for key, value in sorted(locks.items()))
    return RawScan(cycles=tuple(cycles), locks=lock_rows)


def parse_ledger(payload: Mapping[str, Any]) -> list[LedgerDay]:
    days = payload.get("days")
    if not isinstance(days, list) or not days:
        raise AuthenticityError("daily_ledger.days must be a non-empty list")
    result: list[LedgerDay] = []
    for index, row in enumerate(days):
        if not isinstance(row, Mapping):
            raise AuthenticityError(f"daily_ledger.days[{index}] must be an object")
        result.append(
            LedgerDay(
                day=str(row.get("day", "")),
                equity=to_decimal(row.get("equity"), what="ledger.equity"),
                daily_pnl=to_decimal(row.get("daily_pnl"), what="ledger.daily_pnl"),
                cumulative_pnl=to_decimal(row.get("cumulative_pnl"), what="ledger.cumulative_pnl"),
                funding_balance=to_decimal(
                    row.get("funding_balance"), what="ledger.funding_balance"
                ),
                trades_count=to_int(row.get("trades_count", 0), what="ledger.trades_count"),
                position=to_decimal(row.get("position", 0), what="ledger.position"),
                governance=dict(row.get("governance") or {}),
            )
        )
    return result


def parse_scorecard_account(text: str) -> str | None:
    """評価レポートの「対象口座」を読む（無ければ先頭の 0x アドレス）。"""
    match = _SCORECARD_ACCOUNT_RE.search(text)
    if match:
        return match.group(1)
    match = _ADDRESS_RE.search(text)
    return match.group(0) if match else None


def parse_live_fills(rows: Sequence[Mapping[str, Any]], *, account: str) -> tuple[LiveFill, ...]:
    fills: list[LiveFill] = []
    for index, row in enumerate(rows):
        try:
            fill = parse_fill(row, network="testnet")
        except FillIntegrityError as exc:
            raise AuthenticityError(f"userFills[{index}]: {exc}") from exc
        start_position = to_decimal(
            row.get("startPosition"), what=f"userFills[{index}].startPosition"
        )
        fills.append(
            LiveFill(
                fill=fill,
                start_position=start_position,
                direction=str(row.get("dir", "")),
                tx_hash=str(row.get("hash", "")),
            )
        )
    if not fills:
        raise AuthenticityError(f"userFills for {account} is empty")
    return tuple(fills)


def parse_funding(rows: Sequence[Mapping[str, Any]]) -> tuple[FundingRow, ...]:
    result: list[FundingRow] = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise AuthenticityError(f"userFunding[{index}] must be an object")
        # 公式応答は {"time":..., "delta": {...}} の入れ子形式を取り得る。
        time_raw = row.get("time")
        delta_raw = row.get("delta")
        token = row.get("token")
        coin = row.get("coin")
        if isinstance(delta_raw, Mapping):
            token = delta_raw.get("token", token)
            coin = delta_raw.get("coin", coin)
            delta_raw = delta_raw.get("usdc", delta_raw.get("delta"))
        if time_raw is None or delta_raw is None:
            raise AuthenticityError(f"userFunding[{index}] must carry time/delta: {sorted(row)}")
        # Hyperliquid の funding は USDC 建て。token 省略時は USDC と明示する。
        if token is None:
            token = "USDC"
        result.append(
            FundingRow(
                time_ms=to_int(time_raw, what=f"userFunding[{index}].time"),
                delta=to_decimal(delta_raw, what=f"userFunding[{index}].delta"),
                token=str(token),
                coin=None if coin is None else str(coin),
            )
        )
    return tuple(result)


@dataclass(frozen=True)
class SourceBundle:
    """保存済み台帳 4 種の読み取り結果と各入力の sha256。"""

    data_dir: str
    files: tuple[SourceFile, ...]
    journal_chain: VerifyResult
    orders: tuple[OrderRecord, ...]
    trades: tuple[TradeRecord, ...]
    raw: RawScan
    ledger_days: tuple[LedgerDay, ...]
    scorecard_account: str | None
    duplicate_lines: Mapping[str, tuple[int, ...]]


def load_bundle(data_dir: str | Path) -> SourceBundle:
    """``data/hl_testnet_s07`` 相当のディレクトリを読み取り専用で読む。"""
    base = Path(data_dir)
    journal_path = base / JOURNAL_FILE
    trades_path = base / TRADES_FILE
    raw_path = base / RAW_EVENTS_FILE
    ledger_path = base / LEDGER_FILE
    scorecard_path = base / SCORECARD_FILE
    missing = [
        str(path)
        for path in (journal_path, trades_path, raw_path, ledger_path, scorecard_path)
        if not path.is_file()
    ]
    if missing:
        raise AuthenticityError(f"required input files are missing: {missing}")

    journal_rows = read_jsonl(journal_path)
    trade_rows = read_jsonl(trades_path)
    raw_rows = read_jsonl(raw_path)
    journal_entries = entries(journal_path)
    chain = verify_chain(journal_path)
    scorecard_text = scorecard_path.read_text(encoding="utf-8")

    files = (
        file_digest(journal_path, rows=len(journal_rows)),
        file_digest(trades_path, rows=len(trade_rows)),
        file_digest(raw_path, rows=len(raw_rows)),
        file_digest(ledger_path),
        file_digest(scorecard_path),
    )
    duplicates = {
        JOURNAL_FILE: tuple(duplicate_rows(journal_rows)),
        TRADES_FILE: tuple(duplicate_rows(trade_rows)),
        RAW_EVENTS_FILE: tuple(duplicate_rows(raw_rows)),
    }
    return SourceBundle(
        data_dir=str(base),
        files=files,
        journal_chain=chain,
        orders=tuple(parse_orders(journal_entries)),
        trades=tuple(parse_trades(trade_rows)),
        raw=scan_raw_events(raw_rows),
        ledger_days=tuple(parse_ledger(read_json(ledger_path))),
        scorecard_account=parse_scorecard_account(scorecard_text),
        duplicate_lines=duplicates,
    )


def build_live_snapshot(
    *,
    account: str,
    fills: Sequence[Mapping[str, Any]],
    funding: Sequence[Mapping[str, Any]],
    clearinghouse: Mapping[str, Any] | None,
    open_orders: Sequence[Mapping[str, Any]] | None,
    provenance: Mapping[str, Any],
) -> LiveSnapshot:
    """取得済み応答から :class:`LiveSnapshot` を組み立てる（送信なし）。"""
    return LiveSnapshot(
        account=account,
        fills=parse_live_fills(fills, account=account),
        funding=parse_funding(funding),
        clearinghouse=clearinghouse,
        open_orders=tuple(open_orders or ()),
        provenance=dict(provenance),
    )


def load_live_from_files(
    *,
    account: str,
    fills_path: str | Path,
    funding_path: str | Path | None = None,
    state_path: str | Path | None = None,
    orders_path: str | Path | None = None,
) -> LiveSnapshot:
    """保存済みの公開応答（オフライン再検証用）を読む。"""
    fills_raw = read_json_value(fills_path)
    if not isinstance(fills_raw, list):
        raise AuthenticityError(f"{fills_path}: userFills snapshot must be a list")
    funding_raw: Any = []
    if funding_path is not None:
        funding_raw = read_json_value(funding_path)
        if not isinstance(funding_raw, list):
            raise AuthenticityError(f"{funding_path}: userFunding snapshot must be a list")
    state_raw = read_json(state_path) if state_path is not None else None
    orders_raw: Any = []
    if orders_path is not None:
        orders_raw = read_json_value(orders_path)
        if not isinstance(orders_raw, list):
            raise AuthenticityError(f"{orders_path}: openOrders snapshot must be a list")
    files = [file_digest(fills_path)]
    if funding_path is not None:
        files.append(file_digest(funding_path))
    if state_path is not None:
        files.append(file_digest(state_path))
    if orders_path is not None:
        files.append(file_digest(orders_path))
    provenance = {
        "mode": "offline_snapshot",
        "account": account,
        "files": [item.as_dict() for item in files],
        "response_sha256": {
            "userFills": object_sha256(fills_raw),
            "userFunding": object_sha256(funding_raw),
            "clearinghouseState": object_sha256(state_raw) if state_raw is not None else None,
            "openOrders": object_sha256(orders_raw),
        },
    }
    return build_live_snapshot(
        account=account,
        fills=fills_raw,
        funding=funding_raw,
        clearinghouse=state_raw,
        open_orders=orders_raw,
        provenance=provenance,
    )
