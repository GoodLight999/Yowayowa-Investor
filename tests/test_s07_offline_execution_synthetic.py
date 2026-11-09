# ruff: noqa: RUF002, RUF003 — 日本語の句読点（全角）は本文テキストであり誤字ではない
"""S-07-A02/A03/A05/A06/A07 のオフライン受入試験（合成データ）。

PASS は試験アサーションの PASS であり、運用成績・実約定の証明ではない。
送信・署名・鍵生成・取消の経路は呼ばない（fake 注文 object のみ）。
"""

from __future__ import annotations

import ast
import datetime as dt
import importlib.util
import json
import os
import socket
import stat
import sys
import urllib.request
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from yowayowa.hl_exec.guards import OrderReconciliation, UnknownOrderStateError, send_order_once
from yowayowa.hl_exec.s07_offline.evidence import EvidenceWriteError, sanitize
from yowayowa.hl_exec.s07_offline.fills import (
    Correction,
    FillIntegrityError,
    FillPage,
    account_net,
    apply_corrections,
    assess_funding_coverage,
    assess_page_completeness,
    completed_round_trip,
    ingest_fills,
    parse_fill,
    parse_funding,
    replay_order,
)
from yowayowa.hl_exec.s07_offline.isolation import (
    IsolatedRunRoot,
    IsolationError,
    guard_input_file,
    tree_snapshot,
)
from yowayowa.hl_exec.s07_offline.runner import EXIT_FAILED_EVIDENCE_SAVED, main, run_cycle

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "s07_offline_synthetic"
RUN_FIXTURE = FIXTURES / "run_synthetic_A02_A07_A08.json"
FILL_FIXTURE = FIXTURES / "fills_synthetic_A03_A05_A06.json"
WINDOW_START = 1793750400000
WINDOW_END = 1796774400000
CLOCK = lambda: dt.datetime(2026, 11, 9, tzinfo=dt.UTC)  # noqa: E731
SDK_AVAILABLE = importlib.util.find_spec("hyperliquid") is not None

_MONITOR: list[tuple[str, str]] | None = None


def _audit(event: str, args: tuple[Any, ...]) -> None:
    if _MONITOR is None or event != "open":
        return
    path, mode = args[0], args[1]
    if isinstance(path, (str, bytes, os.PathLike)):
        _MONITOR.append((os.path.realpath(os.fsdecode(path)), str(mode)))


sys.addaudithook(_audit)


@pytest.fixture(autouse=True)
def _forbid_real_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("network access attempted during an offline S-07 test")

    monkeypatch.setattr(socket.socket, "connect", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom)
    monkeypatch.setattr(urllib.request, "urlopen", _boom)


@pytest.fixture
def open_monitor():
    global _MONITOR
    _MONITOR = []
    try:
        yield _MONITOR
    finally:
        _MONITOR = None


def _payload() -> dict[str, Any]:
    return json.loads(RUN_FIXTURE.read_text(encoding="utf-8"))


def _fills() -> dict[str, Any]:
    return json.loads(FILL_FIXTURE.read_text(encoding="utf-8"))


def _ingest(bundle: dict[str, Any]):
    return ingest_fills(bundle["rows"], window_start_ms=WINDOW_START, window_end_ms=WINDOW_END)


# --------------------------------------------------------------------------- A02


def test_a02_dry_run_outputs_are_physically_separate_from_mock_ledger(
    tmp_path: Path, open_monitor
) -> None:
    ledger = tmp_path / "mock_real_ledger"
    ledger.mkdir()
    (ledger / "state.json").write_text('{"synthetic": "mock ledger copy"}', encoding="utf-8")
    (ledger / "trades.jsonl").write_text('{"synthetic": true}\n', encoding="utf-8")
    before = tree_snapshot(ledger)
    mark = len(open_monitor)

    out = tmp_path / "dry_run_base"
    out.mkdir()
    result = run_cycle(
        _payload(),
        out_base=out,
        run_id="a02-synthetic",
        clock=CLOCK,
        forbidden_roots=[ledger],
    )
    assert result.exit_code == 0

    ledger_real = str(ledger.resolve())
    touched = [
        path
        for path, _mode in open_monitor[mark:]
        if path == ledger_real or path.startswith(ledger_real + os.sep)
    ]
    assert touched == [], f"real-ledger mock was opened during dry-run: {touched}"
    after = tree_snapshot(ledger)
    assert before == after  # inode / size / mtime / sha256 all unchanged

    root = out / "dry-run" / "a02-synthetic"
    assert sorted(path.name for path in root.iterdir()) == [
        "events.jsonl",
        "report.json",
        "report.md",
        "state.json",
    ]
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    for name in ("events.jsonl", "report.json", "report.md", "state.json"):
        assert stat.S_IMODE((root / name).stat().st_mode) == 0o600
        assert root.resolve() in (root / name).resolve().parents


def test_a02_negative_controls_are_rejected(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    (ledger / "state.json").write_text("{}", encoding="utf-8")
    base = tmp_path / "out"
    base.mkdir()

    # 実台帳の子を base にする
    inside = ledger / "out"
    inside.mkdir()
    with pytest.raises(IsolationError, match="overlaps the forbidden"):
        IsolatedRunRoot.create(inside, "r1", forbidden_roots=[ledger])

    # 実台帳と同じ path
    with pytest.raises(IsolationError, match="overlaps the forbidden"):
        IsolatedRunRoot.create(ledger, "r2", forbidden_roots=[ledger])

    # 相対 path
    with pytest.raises(IsolationError, match="must be absolute"):
        IsolatedRunRoot.create("relative/out", "r3", forbidden_roots=[ledger])

    # run_id の脱出
    for bad in ("../escape", "a/b", "", ".", ".."):
        with pytest.raises(IsolationError, match="run_id"):
            IsolatedRunRoot.create(base, bad, forbidden_roots=[ledger])

    # base の symlink
    link = tmp_path / "link_to_ledger"
    link.symlink_to(ledger, target_is_directory=True)
    with pytest.raises(IsolationError, match="symlink component"):
        IsolatedRunRoot.create(link, "r4", forbidden_roots=[ledger])

    # dry-run コンテナの symlink
    link_base = tmp_path / "linked_base"
    link_base.mkdir()
    (link_base / "dry-run").symlink_to(ledger, target_is_directory=True)
    with pytest.raises(IsolationError, match="not a real directory"):
        IsolatedRunRoot.create(link_base, "r5", forbidden_roots=[])

    # 既存 run root の再利用
    first = IsolatedRunRoot.create(base, "same", forbidden_roots=[ledger])
    assert first.root.exists()
    with pytest.raises(IsolationError, match="fresh root"):
        IsolatedRunRoot.create(base, "same", forbidden_roots=[ledger])

    # hardlink された出力ファイル
    hardlink_root = IsolatedRunRoot.create(base, "hardlink", forbidden_roots=[ledger])
    os.link(ledger / "state.json", hardlink_root.root / "state.json")
    with pytest.raises(IsolationError, match="hardlinked"):
        hardlink_root.path_for("state.json")

    # 出力名のパス混入
    safe_root = IsolatedRunRoot.create(base, "names", forbidden_roots=[ledger])
    with pytest.raises(IsolationError, match="output name"):
        safe_root.path_for("../state.json")
    with pytest.raises(IsolationError, match="output name"):
        safe_root.path_for("sub/state.json")

    # 実台帳ファイルを入力に使う
    with pytest.raises(IsolationError, match="overlaps the forbidden"):
        guard_input_file(ledger / "state.json", forbidden_roots=[ledger])


def test_a02_no_network_and_no_send_path_in_package() -> None:
    with pytest.raises(AssertionError, match="network access attempted"):
        socket.create_connection(("127.0.0.1", 9))

    package = Path(__file__).resolve().parents[1] / "src" / "yowayowa" / "hl_exec" / "s07_offline"
    forbidden_modules = {"socket", "urllib", "http", "hyperliquid", "eth_account", "requests"}
    for path in package.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = {alias.name.split(".")[0] for alias in node.names}
                assert not (names & forbidden_modules), f"{path.name} imports {names}"
            elif isinstance(node, ast.ImportFrom) and node.module:
                root = node.module.split(".")[0]
                assert root not in forbidden_modules, f"{path.name} imports {node.module}"
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert (
                    node.func.attr
                    not in {
                        "submit_order",
                        "cancel_orders",
                        "order",
                        "sign",
                    }
                    or not isinstance(node.func.value, ast.Name)
                    or node.func.value.id
                    not in {
                        "exchange",
                        "client",
                    }
                ), f"send-like call in {path.name}:{node.lineno}"


# --------------------------------------------------------------------------- A03


def test_a03_fees_and_funding_are_applied_exactly_once() -> None:
    bundle = _fills()
    ingest = _ingest(bundle)
    assert len(ingest.accepted) == 5
    funding = [parse_funding(row) for row in bundle["funding"]]
    net = account_net(fills=ingest.accepted, funding=funding)
    assert net.complete is True
    assert str(net.fees) == "0.43"  # 0.08 + 0.12 + 0.09 + 0.09 + 0.05, once
    assert str(net.funding) == "-0.05"
    assert str(net.gross_pnl) == "9.95"
    assert str(net.net) == "9.47"


def test_a03_rebate_keeps_its_sign_and_funding_sign_is_preserved() -> None:
    row = {
        "tid": 1,
        "oid": 1,
        "time": WINDOW_START + 1,
        "coin": "BTC",
        "side": "B",
        "sz": "0.01",
        "px": "100",
        "fee": "-0.02",
        "feeToken": "USDC",
        "closedPnl": "1.00",
    }
    fill = parse_fill(row, network="testnet")
    positive = parse_funding({"time": WINDOW_START + 2, "delta": "0.01", "token": "USDC"})
    negative = parse_funding({"time": WINDOW_START + 3, "delta": "-0.03", "token": "USDC"})
    assert str(account_net(fills=[fill], funding=[positive]).net) == "1.03"
    assert str(account_net(fills=[fill], funding=[negative]).net) == "0.99"


def test_a03_currency_mismatch_is_inconclusive_not_zero() -> None:
    bundle = _fills()
    ingest = _ingest(bundle)
    funding = [parse_funding({"time": WINDOW_START + 10, "delta": "-0.05", "token": "USDT"})]
    net = account_net(fills=ingest.accepted, funding=funding)
    assert net.complete is False
    assert any("multiple fee/funding currencies" in problem for problem in net.problems)


def test_a03_missing_fee_is_rejected() -> None:
    row = {
        "tid": 1,
        "oid": 1,
        "time": WINDOW_START + 1,
        "coin": "BTC",
        "side": "B",
        "sz": "0.01",
        "px": "100",
        "feeToken": "USDC",
        "closedPnl": "0",
    }
    with pytest.raises(FillIntegrityError, match="missing required keys"):
        parse_fill(row, network="testnet")


# --------------------------------------------------------------------------- A05


def test_a05_duplicates_are_applied_once_and_collisions_rejected() -> None:
    bundle = _fills()
    ingest = _ingest(bundle)
    assert ingest.duplicate_count == 1
    assert ingest.applied_count == len(ingest.accepted) == 5

    colliding = dict(bundle["rows"][0])
    colliding["px"] = "99999"
    with pytest.raises(FillIntegrityError, match="conflicting content"):
        _ingest({**bundle, "rows": [bundle["rows"][0], colliding]})


def test_a05_out_of_window_fill_is_quarantined_and_kept_in_evidence() -> None:
    ingest = _ingest(_fills())
    assert len(ingest.quarantined) == 1
    item = ingest.quarantined[0]
    assert item.tid == 9
    assert item.reason == "before window start"
    assert len(item.raw_hash) == 64
    assert all(fill.tid != 9 for fill in ingest.accepted)


def test_a05_page_saturation_cannot_prove_completeness() -> None:
    bundle = _fills()
    saturated = FillPage(index=0, rows=tuple(bundle["rows"][:2]), limit=2)
    report = assess_page_completeness([saturated])
    assert report.complete is False
    assert any("limit" in reason for reason in report.reasons)

    contiguous = FillPage(index=0, rows=tuple(bundle["rows"][:4]), limit=2000)
    assert assess_page_completeness([contiguous]).complete is True

    next_page = FillPage(index=1, rows=tuple(bundle["rows"][4:5]), limit=2000)
    overlapping = assess_page_completeness([contiguous, next_page])
    if contiguous.last_time_ms == next_page.rows[0].get("time"):
        assert overlapping.complete is False


def test_a05_missing_funding_payment_is_detected() -> None:
    bundle = _fills()
    funding = [parse_funding(row) for row in bundle["funding"]]
    expected = [WINDOW_START, funding[0].time_ms]
    coverage = assess_funding_coverage(funding, expected_times_ms=expected)
    assert coverage.complete is False
    assert coverage.missing == (WINDOW_START,)


def test_a05_corrections_are_appended_and_never_overwrite_the_original() -> None:
    ingest = _ingest(_fills())
    original = next(fill for fill in ingest.accepted if fill.tid == 1)
    original_hash = original.content_hash()
    corrected, trail = apply_corrections(
        ingest.accepted,
        [
            Correction(
                tid=1,
                field="fee",
                value="0.10",
                source="synthetic exchange statement line 7",
                reason="synthetic fee transcription correction",
                recorded_at_ms=WINDOW_START + 10,
            )
        ],
    )
    assert original.fee == Decimal("0.08")  # 原本は不変
    assert original.content_hash() == original_hash
    updated = next(fill for fill in corrected if fill.tid == 1)
    assert updated.fee == Decimal("0.10")
    assert trail[0]["original_hash"] == original_hash
    assert trail[0]["applied_once"] is True

    with pytest.raises(FillIntegrityError, match="non-empty source and reason"):
        apply_corrections(
            ingest.accepted,
            [
                Correction(
                    tid=1,
                    field="fee",
                    value="0.10",
                    source="",
                    reason="",
                    recorded_at_ms=WINDOW_START + 10,
                )
            ],
        )
    with pytest.raises(FillIntegrityError, match="conflicting corrections"):
        apply_corrections(
            ingest.accepted,
            [
                Correction(1, "fee", "0.10", "s", "r", 1),
                Correction(1, "fee", "0.11", "s", "r", 2),
            ],
        )


# --------------------------------------------------------------------------- A06


class _TimeoutExchange:
    def __init__(self) -> None:
        self.calls = 0

    def order(self, *_args: Any, **_kwargs: Any) -> Any:
        self.calls += 1
        raise TimeoutError("synthetic timeout; no network was used")


def _send(exchange: _TimeoutExchange, *, open_orders, order_status):
    return send_order_once(
        exchange,
        coin="BTC",
        is_buy=True,
        sz=0.01,
        px=78000.0,
        order_type={"limit": {"tif": "Ioc"}},
        cloid="0x" + "a" * 32,
        open_orders=open_orders,
        order_status=order_status,
    )


def test_a06_partial_fills_quantity_fee_and_state_transitions() -> None:
    bundle = _fills()
    ingest = _ingest(bundle)
    by_tid = {fill.tid: fill for fill in ingest.accepted}
    first = replay_order(cloid="0x" + "a" * 32, requested_sz="0.010", fills=[by_tid[1]])
    assert first.status.value == "PARTIAL"
    assert str(first.filled_sz) == "0.004"
    assert str(first.remaining_sz) == "0.006"
    assert str(first.fees) == "0.08"

    complete = replay_order(
        cloid="0x" + "a" * 32, requested_sz="0.010", fills=[by_tid[1], by_tid[2]]
    )
    assert complete.status.value == "FILLED"
    assert str(complete.filled_sz) == "0.010"
    assert str(complete.remaining_sz) == "0"
    assert str(complete.fees) == "0.20"
    assert str(complete.average_px) == "78030"


def test_a06_overfill_is_rejected() -> None:
    ingest = _ingest(_fills())
    by_tid = {fill.tid: fill for fill in ingest.accepted}
    with pytest.raises(FillIntegrityError, match="exceed requested"):
        replay_order(cloid="0x" + "a" * 32, requested_sz="0.005", fills=[by_tid[1], by_tid[2]])


def test_a06_cancel_preserves_partial_fills_and_remaining() -> None:
    ingest = _ingest(_fills())
    fill = next(fill for fill in ingest.accepted if fill.tid == 5)
    replay = replay_order(
        cloid="0x" + "c" * 32,
        requested_sz="0.020",
        fills=[fill],
        cancel_after_ms=fill.time_ms + 1,
    )
    assert replay.status.value == "CANCELED"
    assert str(replay.filled_sz) == "0.007"
    assert str(replay.remaining_sz) == "0.013"
    assert str(replay.fees) == "0.05"


def test_a06_round_trip_counts_only_when_flat_and_reconciled() -> None:
    bundle = _fills()
    ingest = _ingest(bundle)
    by_tid = {fill.tid: fill for fill in ingest.accepted}
    funding = [parse_funding(row) for row in bundle["funding"]]
    trip = completed_round_trip(
        coin="BTC",
        entry_fills=[by_tid[1], by_tid[2]],
        exit_fills=[by_tid[3], by_tid[4]],
        funding=funding,
    )
    assert trip.complete is True
    assert str(trip.net) == "9.52"

    not_flat = completed_round_trip(
        coin="BTC",
        entry_fills=[by_tid[1], by_tid[2]],
        exit_fills=[by_tid[3], by_tid[4]],
        position_sz="0.001",
    )
    assert not_flat.complete is False
    assert "not flat" in not_flat.reason

    unresolved = completed_round_trip(
        coin="BTC",
        entry_fills=[by_tid[1], by_tid[2]],
        exit_fills=[by_tid[3], by_tid[4]],
        open_order_count=1,
    )
    assert unresolved.complete is False


@pytest.mark.skipif(
    not SDK_AVAILABLE,
    reason=(
        "hyperliquid SDK not installed; the guards non-resend case needs the official Cloid type"
    ),
)
def test_a06_unknown_order_is_never_resent() -> None:
    exchange = _TimeoutExchange()
    absent = _send(
        exchange,
        open_orders=lambda: [],
        order_status=lambda: {"status": "unknownCloid"},
    )
    assert absent.status is OrderReconciliation.ABSENT
    assert exchange.calls == 1

    exchange = _TimeoutExchange()

    def _raise() -> Any:
        raise RuntimeError("lookup unavailable")

    with pytest.raises(UnknownOrderStateError):
        _send(exchange, open_orders=_raise, order_status=_raise)
    assert exchange.calls == 1  # timeout 後に再送していない


def test_a06_unknown_order_in_pipeline_holds_retry(tmp_path: Path) -> None:
    payload = _payload()
    base = tmp_path / "out"
    base.mkdir()
    result = run_cycle(payload, out_base=base, run_id="a06-unknown", clock=CLOCK)
    assert result.exit_code == 0
    report = result.report
    assert report is not None
    assert report["reconciliation_status"] == "unresolved"
    assert report["retryable"] is False
    assert report["unresolved_order_ids"]


# --------------------------------------------------------------------------- A07


def test_a07_timeout_failure_is_recorded_and_retry_is_bounded(tmp_path: Path) -> None:
    payload = _payload()
    payload["inject"] = {"stage": "fills", "error": "timeout"}
    out = tmp_path / "out"
    out.mkdir()
    result = run_cycle(payload, out_base=out, run_id="a07-timeout", clock=CLOCK)
    assert result.exit_code == EXIT_FAILED_EVIDENCE_SAVED
    assert result.status == "FAILED"
    root = out / "dry-run" / "a07-timeout"
    report = json.loads((root / "report.json").read_text(encoding="utf-8"))
    assert report["error_class"] == "TimeoutError"
    assert report["retryable"] is True
    assert report["stage"] == "offline"
    assert "synthetic timeout" in report["error_safe_message"]
    events = (root / "events.jsonl").read_text(encoding="utf-8")
    assert '"kind": "stage_failed"' in events
    assert "COMPLETED" not in report["status"]


def test_a07_malformed_input_is_recorded_without_claiming_success(tmp_path: Path) -> None:
    payload = _payload()
    payload["fills"]["rows"] = [{"tid": "not-an-integer"}]
    out = tmp_path / "out"
    out.mkdir()
    result = run_cycle(payload, out_base=out, run_id="a07-malformed", clock=CLOCK)
    assert result.exit_code == EXIT_FAILED_EVIDENCE_SAVED
    report = json.loads((out / "dry-run" / "a07-malformed" / "report.json").read_text())
    assert report["error_class"] == "FillIntegrityError"
    assert report["retryable"] is False
    assert report["status"] == "FAILED"


def test_a07_evidence_write_failure_is_nonzero_and_never_claims_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out"
    out.mkdir()
    original = IsolatedRunRoot.open_exclusive

    def _fail_on_report(self: IsolatedRunRoot, name: str, *, mode: str = "w"):
        if name == "report.json":
            raise OSError("synthetic write refusal")
        return original(self, name, mode=mode)

    monkeypatch.setattr(IsolatedRunRoot, "open_exclusive", _fail_on_report)
    code = main(
        [
            "--input",
            str(RUN_FIXTURE),
            "--out",
            str(out),
            "--run-id",
            "a07-write-refused",
            "--repo-root",
            str(Path(__file__).resolve().parents[1]),
        ]
    )
    captured = capsys.readouterr()
    assert code == 2
    assert "S07_OFFLINE_EVIDENCE_WRITE_FAILED" in captured.err
    root = out / "dry-run" / "a07-write-refused"
    assert (root / "events.jsonl").exists()
    assert not (root / "report.json").exists()  # 保存成功を装わない


def test_a07_successful_cli_writes_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out"
    out.mkdir()
    code = main(
        [
            "--input",
            str(RUN_FIXTURE),
            "--out",
            str(out),
            "--run-id",
            "a07-success",
            "--repo-root",
            str(Path(__file__).resolve().parents[1]),
        ]
    )
    assert code == 0
    report = json.loads((out / "dry-run" / "a07-success" / "report.json").read_text())
    assert report["status"] == "COMPLETED"
    assert report["data_kind"] == "synthetic"
    assert report["official_result"] is False
    assert "SYNTHETIC" in (out / "dry-run" / "a07-success" / "report.md").read_text()


def test_a07_secrets_are_redacted_in_evidence() -> None:
    secret = "0x" + "f" * 64
    payload = sanitize({"private_key": secret, "note": f"key={secret}"})
    assert payload["private_key"] == "[REDACTED]"
    assert secret not in payload["note"]


def test_a07_evidence_write_failure_type_is_stable() -> None:
    assert issubclass(EvidenceWriteError, RuntimeError)
