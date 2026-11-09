# ruff: noqa: RUF002, RUF003 — 日本語の句読点（全角）は本文テキストであり誤字ではない

"""S-07 X1 実データ真正性検証の別入口（読み取り専用・実注文 0）。

公開アドレス経路の ``POST /info`` だけを使い、鍵の読み出し・署名・``/exchange``
への送信は行わない。出力は ``--out`` 配下のレポートと取得応答のスナップショット
だけで、入力の ``--data-dir`` へは書かない（配下を出力先に指定したら拒否する）。

CLI（実データ・ライブ照会）::

    python -m yowayowa.hl_exec.s07_authenticity.runner \\
        --data-dir /path/to/data/hl_testnet_s07 \\
        --account 0x... \\
        --out /tmp/s07-authenticity-run

CLI（保存済み応答のオフライン再検証）::

    python -m yowayowa.hl_exec.s07_authenticity.runner \\
        --data-dir /path/to/data/hl_testnet_s07 \\
        --out /tmp/s07-authenticity-offline \\
        --offline \\
        --fills-json /tmp/userFills.json \\
        --funding-json /tmp/userFunding.json \\
        --state-json /tmp/clearinghouseState.json \\
        --orders-json /tmp/openOrders.json

終了コード: 0=PASS / 4=PASS_WITH_WARNINGS / 3=FAIL / 2=UNVERIFIABLE。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from yowayowa.hl_exec.client import INFO_URL_TESTNET, HyperliquidInfoClient
from yowayowa.hl_exec.s07_authenticity.models import AuthenticityError, LiveSnapshot
from yowayowa.hl_exec.s07_authenticity.report import build_report, render_markdown
from yowayowa.hl_exec.s07_authenticity.sources import (
    build_live_snapshot,
    load_bundle,
    load_live_from_files,
    object_sha256,
)
from yowayowa.hl_exec.s07_authenticity.verifier import VerificationResult, verify

EXIT_OK = 0
EXIT_UNVERIFIABLE = 2
EXIT_FAIL = 3
EXIT_WARN = 4

EXIT_BY_VERDICT = {
    "PASS": EXIT_OK,
    "PASS_WITH_WARNINGS": EXIT_WARN,
    "FAIL": EXIT_FAIL,
    "UNVERIFIABLE": EXIT_UNVERIFIABLE,
}

MS_PER_DAY = 86_400_000
Clock = Callable[[], _dt.datetime]


@dataclass(frozen=True)
class RunOutcome:
    exit_code: int
    result: VerificationResult
    report: Mapping[str, Any]
    out_dir: str


def _default_clock() -> _dt.datetime:
    return _dt.datetime.now(_dt.UTC)


def guard_output_dir(out_dir: str | Path, data_dir: str | Path) -> Path:
    """出力先が入力データディレクトリの中（または同一）なら拒否する。"""
    out = Path(out_dir).resolve()
    data = Path(data_dir).resolve()
    if out == data or data in out.parents:
        raise AuthenticityError(
            f"refusing to write inside the read-only data directory: out={out} data={data}"
        )
    return out


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, payload: Any) -> None:
    _write_text(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _fetch_live(
    account: str,
    *,
    start_time_ms: int,
    clock: Clock,
    client: HyperliquidInfoClient | None = None,
    raw_dir: Path | None = None,
) -> LiveSnapshot:
    """公開アドレスだけで 4 応答を取得する（署名・鍵・送信なし）。"""
    reader = client if client is not None else HyperliquidInfoClient(base_url=INFO_URL_TESTNET)
    fills = reader.user_fills(account)
    funding = reader.user_funding(account, start_time_ms=start_time_ms)
    clearinghouse = reader.clearinghouse_state(account)
    open_orders = reader.open_orders(account)
    fetched_at = clock().isoformat()
    provenance = {
        "mode": "live_public_info",
        "endpoint": INFO_URL_TESTNET,
        "account": account,
        "fetched_at": fetched_at,
        "requests": [
            {"type": "userFills", "user": account},
            {"type": "userFunding", "user": account, "startTime": start_time_ms},
            {"type": "clearinghouseState", "user": account},
            {"type": "openOrders", "user": account},
        ],
        "response_sha256": {
            "userFills": object_sha256(fills),
            "userFunding": object_sha256(funding),
            "clearinghouseState": object_sha256(clearinghouse),
            "openOrders": object_sha256(open_orders),
        },
        "response_counts": {
            "userFills": len(fills),
            "userFunding": len(funding),
            "openOrders": len(open_orders),
        },
    }
    if raw_dir is not None:
        _write_json(raw_dir / "userFills.json", fills)
        _write_json(raw_dir / "userFunding.json", funding)
        _write_json(raw_dir / "clearinghouseState.json", clearinghouse)
        _write_json(raw_dir / "openOrders.json", open_orders)
    return build_live_snapshot(
        account=account,
        fills=fills,
        funding=funding,
        clearinghouse=clearinghouse,
        open_orders=open_orders,
        provenance=provenance,
    )


def _offline_snapshot(
    account: str,
    *,
    fills_json: str | Path,
    funding_json: str | Path,
    state_json: str | Path,
    orders_json: str | Path,
) -> LiveSnapshot:
    return load_live_from_files(
        account=account,
        fills_path=fills_json,
        funding_path=funding_json,
        state_path=state_json,
        orders_path=orders_json,
    )


def run(
    *,
    data_dir: str | Path,
    out_dir: str | Path,
    account: str | None = None,
    run_id: str | None = None,
    offline: bool = False,
    fills_json: str | Path | None = None,
    funding_json: str | Path | None = None,
    state_json: str | Path | None = None,
    orders_json: str | Path | None = None,
    command: str = "",
    clock: Clock | None = None,
) -> RunOutcome:
    """1 回の真正性検証を実行し、レポートを書く（実注文は存在しない）。"""
    clock = clock if clock is not None else _default_clock
    bundle = load_bundle(data_dir)
    resolved_account = account if account is not None else bundle.scorecard_account
    if not resolved_account:
        raise AuthenticityError("account is unknown; pass --account or fix the scorecard")
    out = guard_output_dir(out_dir, data_dir)
    raw_dir = out / "raw"

    if offline:
        if fills_json is None or funding_json is None or state_json is None or orders_json is None:
            missing = [
                name
                for name, value in (
                    ("--fills-json", fills_json),
                    ("--funding-json", funding_json),
                    ("--state-json", state_json),
                    ("--orders-json", orders_json),
                )
                if value is None
            ]
            raise AuthenticityError(f"offline mode requires {missing}")
        live = _offline_snapshot(
            resolved_account,
            fills_json=fills_json,
            funding_json=funding_json,
            state_json=state_json,
            orders_json=orders_json,
        )
    else:
        start_time_ms = min(cycle.pre_ts_ms for cycle in bundle.raw.cycles) - MS_PER_DAY
        live = _fetch_live(
            resolved_account, start_time_ms=start_time_ms, clock=clock, raw_dir=raw_dir
        )

    result = verify(bundle, live, account=resolved_account)
    generated_at = clock().isoformat()
    effective_run_id = run_id or f"s07x1-{generated_at.replace(':', '').replace('+', 'Z')}"
    report = build_report(
        result=result,
        data_kind="real",
        run={
            "run_id": effective_run_id,
            "generated_at": generated_at,
            "command": command,
            "account": resolved_account,
            "network": "testnet",
            "data_dir": str(Path(data_dir).resolve()),
            "source_mode": "offline_snapshot" if offline else "live_public_info",
        },
    )
    _write_json(out / "report.json", report)
    _write_text(out / "report.md", render_markdown(report))
    if command:
        _write_text(out / "command.txt", command + "\n")
    return RunOutcome(
        exit_code=EXIT_BY_VERDICT.get(result.verdict, EXIT_FAIL),
        result=result,
        report=report,
        out_dir=str(out),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m yowayowa.hl_exec.s07_authenticity.runner",
        description="S-07 X1 real-data authenticity verifier (read-only, no orders).",
    )
    parser.add_argument("--data-dir", required=True, help="data/hl_testnet_s07 (read-only)")
    parser.add_argument("--out", required=True, help="output directory (never inside data-dir)")
    parser.add_argument("--account", default=None, help="public account address (no key)")
    parser.add_argument("--run-id", default=None)
    parser.add_argument(
        "--offline",
        action="store_true",
        help="verify saved /info responses instead of calling the endpoint",
    )
    parser.add_argument("--fills-json", default=None)
    parser.add_argument("--funding-json", default=None)
    parser.add_argument("--state-json", default=None)
    parser.add_argument("--orders-json", default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    command = "python -m yowayowa.hl_exec.s07_authenticity.runner " + " ".join(
        argv if argv is not None else sys.argv[1:]
    )
    try:
        outcome = run(
            data_dir=args.data_dir,
            out_dir=args.out,
            account=args.account,
            run_id=args.run_id,
            offline=args.offline,
            fills_json=args.fills_json,
            funding_json=args.funding_json,
            state_json=args.state_json,
            orders_json=args.orders_json,
            command=command,
        )
    except AuthenticityError as exc:
        print(f"S-07 X1 verifier error: {exc}", file=sys.stderr)
        return EXIT_UNVERIFIABLE
    print(
        f"verdict={outcome.result.verdict} exit={outcome.exit_code} out={outcome.out_dir}",
        file=sys.stderr,
    )
    return outcome.exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
