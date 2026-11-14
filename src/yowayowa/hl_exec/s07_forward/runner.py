# ruff: noqa: RUF002, RUF003 — 日本語の句読点（全角）は本文テキストであり誤字ではない
"""S-07 本番代替 runner（既定 dry-run・発注経路は fail-closed）。

契約は ``docs/s07_x3_contract.md``。旧 ``scripts/hl_testnet_s07/s07_daily_forward.py``
（停止標識）、本番台帳、cron には触れず、出力は隔離ディレクトリだけに書く。

構造上の安全策:

* 既定は dry-run。``enable_order_path`` が明示されない限り発注経路に入らない。
* 起動時に X1 真正性検証を呼び、``exit_code == 0`` かつ ``verdict == "PASS"`` 以外は
  決定・台帳・レポートのどれよりも前に停止する。
* CLI は sender を注入しない。したがって CLI の ``--enable-order-path`` は必ず
  fail-closed で停止し、実送信・署名・鍵・ブローカー接続へは到達しない。
* 発注経路の送信先は注入された ``unittest.mock`` のテストダブルだけ
  （:mod:`yowayowa.hl_exec.s07_forward.order_gate`）。
* 台帳 ``ledger.jsonl`` は追記専用・1 日 1 行。同日再実行では追記せず発注もしない。
* 不明な注文状態は再送しない。取消確認記録がある場合だけ、テストダブルへの
  再送を 1 回許す。送信直前に排他的マーカーを作り、二重送信を不可能にする。

CLI::

    PYTHONPATH=src python -m yowayowa.hl_exec.s07_forward.runner \\
        --day 2026-11-08 --market-json /abs/market.json --out /abs/out \\
        --data-dir /abs/data --account 0x... \\
        --fills-json /abs/userFills.json --funding-json /abs/userFunding.json \\
        --state-json /abs/clearinghouseState.json --orders-json /abs/openOrders.json

終了コード: 0=成功（dry-run 完了または同日記録済み）/ 2=不正入力・X1 失敗・発注遮断。
"""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import math
import os
import re
import stat
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from scripts.s07_paper.s07_strategy import Strategy
from yowayowa.hl_exec.s07_authenticity.runner import run as verify_authenticity
from yowayowa.hl_exec.s07_forward import order_gate
from yowayowa.hl_exec.s07_offline.manifest import load_batch

MARKER_NAME = ".s07-forward-isolated"

#: 受け付ける day 表記（これ以外は fail-closed で停止する）。
_DAY_FORMATS = (
    ("%Y-%m-%d", re.compile(r"\d{4}-\d{2}-\d{2}\Z")),
    ("%Y%m%d", re.compile(r"\d{8}\Z")),
)


def _canonical_day(day: object) -> str:
    """day キーを単一の正準形 ``YYYY-MM-DD`` に正規化する（fail-closed）。

    受け付ける表記は次の3つだけに明示的に限定する。

    * ``YYYY-MM-DD``（ゼロ埋め必須）
    * ``YYYYMMDD``
    * :class:`datetime.date`（``datetime.datetime`` は時刻の解釈が曖昧なので拒否）

    それ以外（``08/11/2026``・ISO 週日付 ``2026-W45-7``・非ゼロ埋め ``2026-1-1``・
    ``datetime``・``None``・数値など）は :class:`ValueError` で停止する。表記ゆれを
    暗黙に解釈すると同じ UTC 日が別キーになり、台帳2行・二重送信・``O_EXCL``
    すり抜けを招くため、解釈できないものは通さない。
    """
    if isinstance(day, dt.datetime):
        raise ValueError(
            "unsupported day representation: datetime is ambiguous; "
            "pass datetime.date, YYYY-MM-DD or YYYYMMDD"
        )
    if isinstance(day, dt.date):
        return day.isoformat()
    if isinstance(day, str):
        for fmt, pattern in _DAY_FORMATS:
            if pattern.match(day):
                try:
                    return dt.datetime.strptime(day, fmt).date().isoformat()
                except ValueError as exc:
                    raise ValueError(f"invalid day value: {day!r}") from exc
    raise ValueError(
        "unsupported day representation: expected YYYY-MM-DD, YYYYMMDD or datetime.date"
    )


#: 注文経路として扱うシグナル。HOLD では発注経路に入らない。
ACTIONABLE_SIGNALS = ("LONG", "SHORT")


def order_path(decision: Mapping[str, Any]) -> None:
    """実発注の入口。意図的に切断してある（署名器もトランスポートも無い）。"""
    raise NotImplementedError("order submission is not connected or approved")


def decide_and_maybe_order(decision: Mapping[str, Any], *, enable_order_path: bool = False) -> str:
    """dry-run の既定では ``order_path`` を呼ばない（単体で検証可能な入口）。"""
    if enable_order_path:
        order_path(decision)
    return str(decision["signal"])


def _overlap(a: Path, b: Path) -> bool:
    return a == b or a in b.parents or b in a.parents


def _safe_root(root: str | Path, inputs: Sequence[str | Path]) -> Path:
    """出力先が既存の絶対ディレクトリで、入力・リポジトリ data と重ならないことを要求する。"""
    candidate = Path(root)
    if not candidate.is_absolute() or not candidate.is_dir():
        raise ValueError("output must be an existing absolute directory")
    for part in (candidate, *candidate.parents):
        if part.is_symlink():
            raise ValueError("symlink in output path")
    resolved = candidate.resolve(strict=True)
    repo_data = Path(__file__).resolve().parents[4] / "data"
    for item in [repo_data, *inputs]:
        other = Path(item).resolve()
        if _overlap(resolved, other):
            raise ValueError("output overlaps protected input or repository data")
    return resolved


def _regular(path: Path) -> None:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError(f"unsafe file: {path}")


def _write_all(fd: int, data: bytes) -> None:
    if os.write(fd, data) != len(data):
        raise OSError("short write; manual reconciliation required")
    os.fsync(fd)


def _encode(payload: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n"
    ).encode()


def _ensure_dir(path: Path) -> Path:
    if path.is_symlink():
        raise ValueError(f"unsafe directory: {path}")
    path.mkdir(mode=0o700, exist_ok=True)
    if not path.is_dir():
        raise ValueError(f"unsafe directory: {path}")
    return path


def _decide(day: str, market: Mapping[str, Any]) -> dict[str, Any]:
    """マーケットスナップショットから当日のシグナルを決める（純粋計算）。"""
    batch = load_batch(market)
    if batch.provenance.coin != "BTC" or not batch.complete:
        raise ValueError("market data incomplete or wrong coin")
    end = dt.datetime.combine(dt.date.fromisoformat(day) + dt.timedelta(days=1), dt.time(), dt.UTC)
    end_ms = int(end.timestamp() * 1000)
    selected = [bar for bar in batch.accepted if end_ms > bar.T]
    if not selected or selected[-1].t != end_ms - 86_400_000 or len(selected) < 50:
        raise ValueError("missing daily candle or warmup")
    funding = market.get("funding")
    if not isinstance(funding, list):
        raise ValueError("funding missing")
    expected = set(range(end_ms - 86_400_000, end_ms, 3_600_000))
    parsed = []
    for row in funding:
        stamp, rate = row["time"], float(row["rate"])
        if isinstance(stamp, bool) or not isinstance(stamp, int) or not math.isfinite(rate):
            raise ValueError("invalid funding")
        if end_ms - 86_400_000 <= stamp < end_ms:
            parsed.append({"time": stamp, "rate": rate})
    if {item["time"] // 3_600_000 * 3_600_000 for item in parsed} != expected or len(parsed) != 24:
        raise ValueError("funding window must contain each hour exactly once")
    candles = [
        {"t": bar.t, "h": float(bar.high), "l": float(bar.low), "c": float(bar.close)}
        for bar in selected
    ]
    decision = Strategy(candles, parsed).decide(len(candles) - 1)
    if not decision.warm:
        raise ValueError("strategy is not warmed up")
    signal = (
        "LONG"
        if decision.long_signal and not decision.block_long
        else "SHORT"
        if decision.short_signal and not decision.block_short
        else "HOLD"
    )
    return {
        "day": day,
        "signal": signal,
        "indicators": decision.as_dict(),
        "data_kind": batch.provenance.data_kind,
        "network": "testnet",
        "mode": "dry_run",
        "official_result": False,
        "source": batch.provenance.as_dict(),
        "account_state": "UNKNOWN",
        "retry_allowed": False,
    }


def _authenticity_check(authenticity: Mapping[str, Any], out_dir: Path) -> None:
    """X1 を実行し、PASS 以外は決定前に停止する。"""
    kwargs = dict(authenticity)
    try:
        outcome = verify_authenticity(out_dir=out_dir, **kwargs)
        exit_code = getattr(outcome, "exit_code", None)
        result = getattr(outcome, "result", None)
        verdict = getattr(result, "verdict", None)
    except Exception as exc:  # 検証器の失敗はすべて停止理由にする
        raise ValueError(f"authenticity prerequisite errored: {type(exc).__name__}: {exc}") from exc
    if exit_code != 0 or verdict != "PASS":
        raise ValueError(
            f"authenticity prerequisite did not PASS: exit_code={exit_code!r} verdict={verdict!r}"
        )


def _read_ledger(path: Path) -> list[dict[str, Any]]:
    """台帳を読む。空行・非 dict・day 欠落・重複 day・非正準 day は破損として拒否する。

    旧実装が書いた ``20261108`` のような非正準 day が残っている場合、そのまま
    照合すると同じ UTC 日を別キーとして追記してしまう。入力の正規化だけでは
    既存ファイルの汚染を防げないため、台帳内の day が正準形でない時点で停止する
    （運用者が台帳を確認するまで fail-closed）。
    """
    if not path.exists() and not path.is_symlink():
        return []
    _regular(path)
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for lineno, line in enumerate(handle, 1):
            if not line.strip():
                raise ValueError(f"blank line in ledger at {lineno}")
            row = json.loads(line)
            if not isinstance(row, dict) or not isinstance(row.get("day"), str):
                raise ValueError(f"invalid ledger row at {lineno}")
            if _canonical_day(row["day"]) != row["day"]:
                raise ValueError(
                    f"non-canonical day in ledger at {lineno}: {row['day']!r}; "
                    "refusing to reconcile against a polluted ledger"
                )
            records.append(row)
    days = [row["day"] for row in records]
    if len(days) != len(set(days)):
        raise ValueError("duplicate days in ledger")
    return records


def _reject_noncanonical_order_artifacts(orders_dir: Path) -> None:
    """旧実装が残した非正準名の送信マーカー・イベントログを検出して停止する。

    ``20261108.submitted`` が残っていると、正準形 ``2026-11-08.submitted`` の
    ``O_EXCL`` は衝突せず二重送信が成立する。名前の正準性を確認できない限り
    発注経路へ入らない。
    """
    if not orders_dir.exists():
        return
    if orders_dir.is_symlink() or not orders_dir.is_dir():
        raise ValueError(f"unsafe order journal directory: {orders_dir}")
    for path in sorted(orders_dir.iterdir()):
        name = path.name
        for suffix in (".submitted", ".events.jsonl"):
            if not name.endswith(suffix):
                continue
            stem = name[: -len(suffix)]
            if _canonical_day(stem) != stem:
                raise ValueError(
                    f"non-canonical day in order artifact: {name!r}; "
                    "refusing to enter the order path"
                )


def _append_ledger(path: Path, record: Mapping[str, Any]) -> None:
    if path.is_symlink():
        raise ValueError(f"unsafe ledger: {path}")
    data = _encode(record)
    fd = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        _write_all(fd, data)
    finally:
        os.close(fd)


def _report_path(root: Path, day: str) -> Path:
    reports = _ensure_dir(root / "reports")
    return reports / f"{day}.json"


def _write_report(root: Path, day: str, record: Mapping[str, Any]) -> Path:
    """当日レポートを新規作成する（既存なら何もしない呼び出し側の責務）。"""
    path = _report_path(root, day)
    data = _encode(record)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        _write_all(fd, data)
    finally:
        os.close(fd)
    return path


def _ensure_report(root: Path, day: str, record: Mapping[str, Any]) -> Path:
    """記録済みの日のレポート欠損を復旧する。内容不一致は破損として停止する。"""
    path = _report_path(root, day)
    if path.exists() or path.is_symlink():
        _regular(path)
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing != dict(record):
            raise ValueError("report does not match ledger record")
        return path
    return _write_report(root, day, record)


def _run_order_path(
    root: Path,
    decision: Mapping[str, Any],
    *,
    sender: Callable[..., Any],
    order_status: object | None,
) -> dict[str, Any]:
    """テストダブルへの再送可否を判定し、許された場合だけ 1 回だけ送る。"""
    day = str(decision["day"])
    journal = root / "orders"
    signal = decision.get("signal")
    if signal not in ACTIONABLE_SIGNALS:
        blocked = {
            "authorized": False,
            "reason": "signal_not_actionable",
            "evidence": {"kind": "signal", "verified": False, "signal": signal},
        }
        order_gate.record_event(journal, day, {"event": "resend_blocked", **blocked})
        return blocked

    gate = order_gate.authorize(order_status)
    if not gate["authorized"]:
        order_gate.record_event(journal, day, {"event": "resend_blocked", **gate})
        return gate

    try:
        order_gate.claim_submission(
            journal,
            day,
            {"event": "resend_invoked", "signal": signal, "evidence": gate["evidence"]},
        )
    except order_gate.OrderGateError as exc:
        blocked = {
            "authorized": False,
            "reason": "submission_already_claimed",
            "evidence": {"kind": "intent_marker", "verified": False, "detail": str(exc)},
        }
        order_gate.record_event(journal, day, {"event": "resend_blocked", **blocked})
        return blocked

    sender(
        {
            "day": day,
            "signal": signal,
            "action": "resend",
            "evidence": gate["evidence"],
        }
    )
    order_gate.record_event(journal, day, {"event": "resend_completed", "signal": signal})
    return gate


def run_day(
    *,
    day: str | dt.date,
    market: Mapping[str, Any],
    out_dir: str | Path,
    authenticity: Mapping[str, Any],
    enable_order_path: bool = False,
    sender: object | None = None,
    order_status: object | None = None,
) -> dict[str, Any]:
    """1 日分を決定し、台帳追記とレポート生成まで通す（既定は dry-run）。

    ``day`` は入口で :func:`_canonical_day` により ``YYYY-MM-DD`` へ正規化する。
    台帳キー・冪等判定・レポート名・``orders/<day>.submitted`` はすべて正準形から
    作るため、同じ UTC 日の別表記は同一キーになる。正準化できない表記は、出力先を
    一切触る前に :class:`ValueError` で停止する。

    ``enable_order_path`` を明示しても、テストダブルの ``sender`` が無ければ
    :class:`NotImplementedError` で停止し、台帳にも何も書かない。CLI は sender を
    注入しないため、CLI から発注経路へ入る方法は存在しない。
    """
    day = _canonical_day(day)
    inputs = [authenticity["data_dir"]]
    inputs += [
        authenticity[key]
        for key in ("fills_json", "funding_json", "state_json", "orders_json")
        if authenticity.get(key)
    ]
    root = _safe_root(out_dir, inputs)

    marker = root / MARKER_NAME
    if not marker.exists():
        if any(root.iterdir()):
            raise ValueError("output directory must initially be empty")
        fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        os.close(fd)
    _regular(marker)

    lock = root / "lock"
    if lock.exists() or lock.is_symlink():
        _regular(lock)
    lock_fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)

        auth_out = root / "authenticity"
        if auth_out.is_symlink():
            raise ValueError("unsafe authenticity output")
        if authenticity.get("offline") is not True:
            raise ValueError("authenticity verification must run offline (offline=True)")
        _authenticity_check(authenticity, auth_out)

        # 明示フラグでも sender が無ければ、決定・台帳・発注のすべてより前に停止する。
        if enable_order_path:
            if sender is None:
                raise NotImplementedError(
                    "order path requires an injected test sender; "
                    "the CLI never provides one and no live sender exists"
                )
            sender_callable = order_gate.require_test_double(sender)
        else:
            sender_callable = None

        ledger = root / "ledger.jsonl"
        records = _read_ledger(ledger)
        _reject_noncanonical_order_artifacts(root / "orders")
        existing = next((row for row in records if row["day"] == day), None)
        if existing is not None:
            _ensure_report(root, day, existing)
            return {"status": "already_recorded", "decision": existing}

        decision = _decide(day, market)
        if decision.get("day") != day:
            raise ValueError("decision day does not match canonical day")

        order_result: dict[str, Any] | None = None
        if enable_order_path and sender_callable is not None:
            order_result = _run_order_path(
                root, decision, sender=sender_callable, order_status=order_status
            )

        _append_ledger(ledger, decision)
        _write_report(root, day, decision)

        result: dict[str, Any] = {"status": "appended", "decision": decision}
        if order_result is not None:
            result["order"] = order_result
            if not order_result["authorized"]:
                result["status"] = "order_blocked"
        return result
    finally:
        os.close(lock_fd)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--day",
        required=True,
        help="UTC day: YYYY-MM-DD or YYYYMMDD (any other notation is rejected)",
    )
    parser.add_argument("--market-json", required=True)
    parser.add_argument("--out", required=True, help="existing empty isolated output directory")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--account", required=True)
    parser.add_argument("--fills-json", required=True)
    parser.add_argument("--funding-json", required=True)
    parser.add_argument("--state-json", required=True)
    parser.add_argument("--orders-json", required=True)
    parser.add_argument(
        "--enable-order-path",
        action="store_true",
        help="request the test-only order path; the CLI injects no sender and fails closed",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 入口。sender を注入する手段は無く、いかなる実送信経路も持たない。"""
    args = _parser().parse_args(argv)
    try:
        authenticity = {
            "data_dir": args.data_dir,
            "account": args.account,
            "offline": True,
            "fills_json": args.fills_json,
            "funding_json": args.funding_json,
            "state_json": args.state_json,
            "orders_json": args.orders_json,
        }
        if _overlap(Path(args.out).resolve(), Path(args.market_json).resolve()):
            raise ValueError("market input overlaps output")
        market = json.loads(Path(args.market_json).read_text(encoding="utf-8"))
        if not isinstance(market, dict):
            raise ValueError("market json must be an object")
        result = run_day(
            day=args.day,
            market=market,
            out_dir=args.out,
            authenticity=authenticity,
            enable_order_path=args.enable_order_path,
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        if result.get("status") == "order_blocked":
            print(
                "S07_FORWARD_ABORTED: order path blocked; no submission performed",
                file=sys.stderr,
            )
            return 2
        return 0
    except (ValueError, OSError, KeyError, TypeError, NotImplementedError) as exc:
        print(f"S07_FORWARD_ABORTED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
