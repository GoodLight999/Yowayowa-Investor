"""Orderbook store symbol path-safety boundary (kanban t_9ad92d9b).

Network-free unit tests only. Pins:

* every service entry point (``get_snapshot`` / ``record_snapshot`` /
  ``get_depth_ladder`` / ``get_metrics`` / ``estimate_impact``) rejects
  traversal and degenerate symbols through ONE explicit ValueError-derived
  exception, without touching disk and without echoing the raw input or any
  filesystem path;
* legitimate symbols keep byte-identical storage semantics versus the
  pre-change implementation (inline legacy oracle copied from the hash
  recorded on the card);
* a request symbol that resolves to a board saved under a different symbol
  is surfaced explicitly (provenance note), never silently;
* the API route maps the rejection to 400 (not 500), the CLI to a one-line
  error plus exit code 1, and the AI-tool dispatch to ``{"error": ...}``;
* the dot-dot rejection rule stays the one shared with
  ``acquisition/snapshots.py::_safe_component``.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime, tzinfo
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner
from typer.testing import Result as CliResult

from yowayowa.api import orderbook_routes
from yowayowa.api.app import app
from yowayowa.cli_entry import app as cli_app
from yowayowa.domain import LicenseClass, Provenance
from yowayowa.orderbook_models import (
    OrderbookLevel,
    OrderbookSnapshot,
)
from yowayowa.services import orderbook_service
from yowayowa.services.orderbook_service import (
    OrderbookService,
    compute_orderbook_metrics,
    generate_seed_orderbook,
)

runner = CliRunner()

_EMPTY_DETAIL = "Symbol cannot be empty."
_UNSAFE_DETAIL = "Invalid orderbook symbol: unsafe path component."
_CONTROL_DETAIL = "Invalid orderbook symbol: control character."
_ABSOLUTE_DETAIL = "Invalid orderbook symbol: absolute path form."
_SEPARATOR_DETAIL = "Invalid orderbook symbol: path separator."


# (raw symbol, exact expected rejection message) — no input echo, no path.
_REJECT_CASES: list[tuple[str, str]] = [
    ("", _EMPTY_DETAIL),
    ("   ", _EMPTY_DETAIL),
    ("\t", _EMPTY_DETAIL),
    (".", _UNSAFE_DETAIL),
    ("..", _UNSAFE_DETAIL),
    ("...", _UNSAFE_DETAIL),
    ("../..", _UNSAFE_DETAIL),
    ("a/../..", _UNSAFE_DETAIL),
    ("7203.T\x00x", _CONTROL_DETAIL),
    ("BTC\x07USD", _CONTROL_DETAIL),
    ("X\x1fY", _CONTROL_DETAIL),
    ("a\nb", _CONTROL_DETAIL),
    ("/etc/passwd", _ABSOLUTE_DETAIL),
    ("/", _ABSOLUTE_DETAIL),
    ("//", _ABSOLUTE_DETAIL),
    ("\\\\srv\\share", _ABSOLUTE_DETAIL),
    ("C:\\tmp\\x", _ABSOLUTE_DETAIL),
    ("A\\B", _SEPARATOR_DETAIL),
    (":", "Invalid orderbook symbol: path separator only."),
]

# Symbols whose storage path / bytes / returned fields must stay identical to
# the pre-change implementation. Includes both halves of the known
# BTC/USD <-> BTC:USD shared-path design (kept as-is by decision).
_LEGACY_SYMBOLS = ["7203.T", "7203.t", "BTC/USD", "BTC:USD", "トヨタ", "7203"]

_LEGACY_RELPATH = {
    "7203.T": "7203.T/latest.json",
    "7203.t": "7203.T/latest.json",
    "BTC/USD": "BTC_USD/latest.json",
    "BTC:USD": "BTC_USD/latest.json",
    "トヨタ": "トヨタ/latest.json",
    "7203": "7203/latest.json",
}


def _invalid_symbol_error() -> type[Exception]:
    """Lazy import so the RED run reports behavior failures, not collection."""
    from yowayowa.services.orderbook_service import InvalidOrderbookSymbolError

    return InvalidOrderbookSymbolError


_FIXED_MOMENT = datetime(2026, 10, 24, 9, 0, 0, tzinfo=UTC)


def _fixed_snapshot(symbol: str) -> OrderbookSnapshot:
    """A deterministic board so new/old storage bytes can be compared exactly."""
    bids = [
        OrderbookLevel(price=100.0, size=10.0, order_count=3),
        OrderbookLevel(price=99.5, size=20.0, order_count=4),
    ]
    asks = [
        OrderbookLevel(price=100.5, size=12.0, order_count=2),
        OrderbookLevel(price=101.0, size=18.0, order_count=5),
    ]
    return OrderbookSnapshot(
        symbol=symbol,
        as_of=_FIXED_MOMENT,
        bids=bids,
        asks=asks,
        metrics=compute_orderbook_metrics(bids, asks),
        provenance=Provenance(
            provider="test-feed",
            source="unit-test fixture",
            license_class=LicenseClass.SYNTHETIC,
            retrieved_at=_FIXED_MOMENT,
        ),
    )


class _FrozenDatetime(datetime):
    """datetime stand-in so generated boards carry identical timestamps."""

    @classmethod
    def now(cls, tz: tzinfo | None = None) -> datetime:
        return datetime(2026, 10, 24, 12, 0, 0, tzinfo=UTC)


class _LegacyOrderbookStore:
    """Verbatim pre-change semantics (happy path), copied for equivalence.

    Source: src/yowayowa/services/orderbook_service.py at
    sha256 741f0cfd4a9aedde0127e7f82ac0a7a5dcc448bddb289301b22dd65eb8be6a42
    (the hash recorded on the card at start): ``_symbol_path``,
    ``record_snapshot`` and the missing-file bootstrap branch of
    ``get_snapshot``.
    """

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = Path(base_dir)

    def _symbol_path(self, symbol: str) -> Path:
        norm = symbol.replace("/", "_").replace(":", "_").upper()
        return self.base_dir / norm / "latest.json"

    def record_snapshot(self, snapshot: OrderbookSnapshot) -> OrderbookSnapshot:
        sorted_bids = sorted(snapshot.bids, key=lambda x: x.price, reverse=True)
        sorted_asks = sorted(snapshot.asks, key=lambda x: x.price)
        metrics = compute_orderbook_metrics(sorted_bids, sorted_asks)
        normalized_snapshot = OrderbookSnapshot(
            symbol=snapshot.symbol,
            as_of=snapshot.as_of,
            bids=sorted_bids,
            asks=sorted_asks,
            metrics=metrics,
            provenance=snapshot.provenance,
        )
        out_path = self._symbol_path(snapshot.symbol)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(normalized_snapshot.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return normalized_snapshot

    def get_snapshot(self, symbol: str) -> OrderbookSnapshot:
        path = self._symbol_path(symbol)
        if not path.exists():
            return self.record_snapshot(generate_seed_orderbook(symbol))
        return OrderbookSnapshot.model_validate(json.loads(path.read_bytes().decode("utf-8")))


def _store_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file()) if root.exists() else []


def _cli_text(result: CliResult) -> str:
    """click mixes stderr into ``output`` (<8.2) or splits it (>=8.2)."""
    text = result.output
    with contextlib.suppress(ValueError, AttributeError):
        text += result.stderr
    return text


# ---------------------------------------------------------------- rejections


@pytest.mark.parametrize(("symbol", "expected_detail"), _REJECT_CASES)
def test_rejections_are_explicit_silent_and_nonescaping(
    tmp_path: Path, symbol: str, expected_detail: str
) -> None:
    root = tmp_path / "store"
    service = OrderbookService(root)
    error = _invalid_symbol_error()

    with pytest.raises(error) as exc_info:
        service.get_snapshot(symbol)
    assert str(exc_info.value) == expected_detail

    with pytest.raises(error):
        service.get_metrics(symbol)
    with pytest.raises(error):
        service.get_depth_ladder(symbol)
    with pytest.raises(error):
        service.estimate_impact(symbol, side="buy", quantity=1.0)
    with pytest.raises(error):
        service.record_snapshot(_fixed_snapshot(symbol))

    # Nothing was written: neither inside the store nor beside it (the ".."
    # defect used to create <base_dir>/../latest.json).
    assert _store_files(root) == []
    assert not (tmp_path / "latest.json").exists()


def test_nul_byte_is_rejected_before_the_os_path(tmp_path: Path) -> None:
    """The measured defect: NUL used to leak ValueError: embedded null byte."""
    service = OrderbookService(tmp_path)

    with pytest.raises(_invalid_symbol_error()) as exc_info:
        service.get_snapshot("7203.T\x00x")

    assert "null byte" not in str(exc_info.value)
    assert isinstance(exc_info.value, ValueError)  # stays in the ValueError family
    assert _store_files(tmp_path) == []


def test_whitespace_padding_is_normalized_like_the_api_route(tmp_path: Path) -> None:
    """The API route already strips; service/CLI/AI direct calls now match."""
    service = OrderbookService(tmp_path)
    service.record_snapshot(_fixed_snapshot("7203.T"))

    padded = service.get_snapshot("  7203.T  ")

    assert padded.symbol == "7203.T"
    assert (tmp_path / "7203.T" / "latest.json").exists()
    assert _store_files(tmp_path) == [tmp_path / "7203.T" / "latest.json"]


# ------------------------------------------------- legacy-equivalence (green)


@pytest.mark.parametrize("symbol", _LEGACY_SYMBOLS)
def test_legitimate_symbols_keep_legacy_storage_and_fields(
    tmp_path: Path, symbol: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(orderbook_service, "datetime", _FrozenDatetime)

    new_root, old_root = tmp_path / "new", tmp_path / "old"
    new_service = OrderbookService(new_root)
    legacy = _LegacyOrderbookStore(old_root)
    snapshot = _fixed_snapshot(symbol)

    recorded_new = new_service.record_snapshot(snapshot)
    recorded_old = legacy.record_snapshot(snapshot)

    new_path = next(path for path in new_root.rglob("latest.json"))
    old_path = next(path for path in old_root.rglob("latest.json"))
    assert new_path.relative_to(new_root).as_posix() == _LEGACY_RELPATH[symbol]
    assert old_path.relative_to(old_root).as_posix() == _LEGACY_RELPATH[symbol]
    assert (
        hashlib.sha256(new_path.read_bytes()).digest()
        == hashlib.sha256(old_path.read_bytes()).digest()
    )
    assert recorded_new == recorded_old  # full-field deep equal
    assert recorded_new.model_dump() == recorded_old.model_dump()

    # Bootstrap read path on fresh stores (synthetic seed board persisted).
    boot_new = OrderbookService(tmp_path / "boot-new").get_snapshot(symbol)
    boot_old = _LegacyOrderbookStore(tmp_path / "boot-old").get_snapshot(symbol)
    assert boot_new.model_dump() == boot_old.model_dump()
    assert (tmp_path / "boot-new" / _LEGACY_RELPATH[symbol]).read_bytes() == (
        tmp_path / "boot-old" / _LEGACY_RELPATH[symbol]
    ).read_bytes()

    # Reading back a recorded board returns it unchanged — no extra notes.
    reread = new_service.get_snapshot(symbol)
    assert reread == recorded_new


def test_dot_dot_rule_is_shared_with_snapshots_safe_component() -> None:
    """One dot-dot rule: the orderbook boundary reuses snapshots' predicate."""
    from yowayowa.acquisition.snapshots import _safe_component
    from yowayowa.services.orderbook_service import _validated_orderbook_symbol

    for value in ("..", "../..", "a/../..", "...", "..x", "x.."):
        with pytest.raises(ValueError):
            _safe_component(value)
        with pytest.raises(ValueError):
            _validated_orderbook_symbol(value)

    # snapshots' behavior stays byte-identical after the helper promotion.
    assert _safe_component("7203_T-x") == "7203_T-x"
    assert _safe_component("api/v2/x") == "api-v2-x"
    with pytest.raises(ValueError) as exc_info:
        _safe_component("..")
    assert str(exc_info.value) == "unsafe path component: '..'"


# --------------------------------------------------- cross-normalization read


def test_cross_normalization_read_is_surfaced_not_silent(tmp_path: Path) -> None:
    service = OrderbookService(tmp_path)
    service.record_snapshot(_fixed_snapshot("BTC/USD"))

    cross = service.get_snapshot("BTC:USD")

    # The stored board is returned, but the mismatch is explicit.
    assert cross.symbol == "BTC/USD"
    joined = "\n".join(cross.provenance.notes)
    assert "mismatch" in joined.lower()
    assert "BTC:USD" in joined
    assert "BTC/USD" in joined

    # The cross read is read-only: stored bytes untouched.
    stored = json.loads((tmp_path / "BTC_USD" / "latest.json").read_text(encoding="utf-8"))
    assert stored["symbol"] == "BTC/USD"

    # The depth ladder funnels through the same snapshot and carries the note.
    ladder = service.get_depth_ladder("BTC:USD")
    assert any("mismatch" in note.lower() for note in ladder.provenance.notes)

    # Same-symbol reads stay exactly the stored board (no note added).
    direct = service.get_snapshot("BTC/USD")
    assert direct.provenance.notes == []
    assert direct.symbol == "BTC/USD"


# ---------------------------------------------------------------- API surface


def test_api_maps_symbol_rejection_to_400_not_500(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(orderbook_routes, "_service", OrderbookService(tmp_path))
    client = TestClient(app)

    for encoded in ("%2E%2E", "%2E"):
        resp = client.get(f"/v1/orderbook/{encoded}")
        assert resp.status_code == 400, resp.text
        assert resp.json()["detail"] == _UNSAFE_DETAIL
        assert ".." not in resp.text
        assert "%2E" not in resp.text

    # The route's own empty-after-strip wording is untouched.
    resp_space = client.get("/v1/orderbook/%20")
    assert resp_space.status_code == 400
    assert resp_space.json()["detail"] == _EMPTY_DETAIL

    # Impact (POST) maps the same way.
    resp_impact = client.post(
        "/v1/orderbook/%2E%2E/impact",
        json={"side": "buy", "quantity": 1.0},
    )
    assert resp_impact.status_code == 400
    assert resp_impact.json()["detail"] == _UNSAFE_DETAIL

    # Legitimate symbols are unchanged: 200 and the stored symbol.
    OrderbookService(tmp_path).record_snapshot(_fixed_snapshot("7203.T"))
    resp_ok = client.get("/v1/orderbook/7203.T")
    assert resp_ok.status_code == 200
    assert resp_ok.json()["symbol"] == "7203.T"


# ----------------------------------------------------------------- CLI surface

_CONSOLE_SCRIPT = Path(sys.executable).parent / "yowayowa"


def test_cli_rejection_is_one_stderr_line_with_the_fixed_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The CLI owns the rejection mapping (t_c3565178): exactly one stderr
    line with the fixed message, empty stdout, exit code 1, and no service
    exception escaping the callback (typer would otherwise render its rich
    multi-line traceback panel with absolute source paths)."""
    monkeypatch.chdir(tmp_path)  # the CLI store is CWD-relative; keep it here

    for args, expected_detail in (
        (["orderbook", "show", ".."], _UNSAFE_DETAIL),
        (["orderbook", "show", "A\x1fB"], _CONTROL_DETAIL),
        (["orderbook", "metrics", ".."], _UNSAFE_DETAIL),
        (["orderbook", "metrics", "/etc/passwd"], _ABSOLUTE_DETAIL),
        (["orderbook", "impact", "..", "--side", "buy", "--qty", "1"], _UNSAFE_DETAIL),
        (["orderbook", "impact", "/etc/passwd", "--side", "buy", "--qty", "1"], _ABSOLUTE_DETAIL),
    ):
        result = runner.invoke(cli_app, args)
        assert result.exit_code == 1, args
        assert result.stderr == f"Error: {expected_detail}\n", args  # exactly one line
        assert result.stdout == "", args  # no table/panel rendered either
        # The rejection is handled by the CLI itself; the raw service
        # exception no longer escapes to the runner.
        assert not isinstance(result.exception, _invalid_symbol_error()), args
        assert args[2] not in result.stderr, args  # raw input never echoed
        assert "\x1f" not in result.stderr and "\x00" not in result.stderr, args
    assert not Path("data").exists()  # nothing written


def test_cli_rejection_renders_no_rich_panel_and_keeps_the_happy_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No rich traceback panel (multi-line, absolute source paths) on the
    rejection path, and the narrow catch keeps legitimate symbols working."""
    monkeypatch.chdir(tmp_path)

    for command in ("show", "metrics", "impact"):
        args = ["orderbook", command, ".."]
        if command == "impact":
            args += ["--side", "buy", "--qty", "1"]
        result = runner.invoke(cli_app, args)
        assert result.exit_code == 1, args
        assert result.stderr.count("\n") == 1, args  # one line, nothing more
        assert "Traceback" not in result.stderr, args
        assert "╭" not in result.stderr, args  # rich panel border
        assert "/root" not in result.stderr, args  # no absolute source path

    # The catch is narrow: a legitimate symbol still renders, rc=0, no stderr.
    for args in (
        ["orderbook", "show", "7203"],
        ["orderbook", "metrics", "7203"],
        ["orderbook", "impact", "7203", "--side", "buy", "--qty", "1"],
    ):
        result = runner.invoke(cli_app, args)
        assert result.exit_code == 0, args
        assert result.stderr == "", args


@pytest.mark.skipif(
    not _CONSOLE_SCRIPT.exists(),
    reason="yowayowa console script is not installed in this venv",
)
def test_real_cli_entry_rejection_stderr_is_one_leak_free_line(tmp_path: Path) -> None:
    """Measured through the real entrypoint (same venv, no network).

    NUL cannot be delivered through argv at all (fork/exec rejects it), so the
    real-entry check uses the dot-dot case; NUL is covered at the service,
    API and AI-tool boundaries which do receive it.
    """
    result = subprocess.run(
        [str(_CONSOLE_SCRIPT), "orderbook", "show", ".."],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=120,
        check=False,
    )
    assert result.returncode == 1
    # The whole rejection is exactly one stderr line with the fixed message:
    # no rich traceback panel, no absolute source path, no input echo.
    assert result.stderr == "Error: Invalid orderbook symbol: unsafe path component.\n"
    assert len(result.stderr.splitlines()) == 1
    assert "/root" not in result.stderr
    # the old failure text never surfaces; the store was never touched
    assert "embedded null byte" not in result.stderr
    assert list(tmp_path.iterdir()) == []  # nothing written beside the run


# -------------------------------------------------------------- AI-tool surface


def _ai_agent() -> Any:
    from yowayowa.config import Settings
    from yowayowa.services.ai_agent import InvestmentResearchAgent

    return InvestmentResearchAgent(Settings(database_url="sqlite:///:memory:"), Mock())


def test_ai_tool_dispatch_surfaces_rejection_as_error_dict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)  # the AI tool store is CWD-relative; keep it here
    agent = _ai_agent()

    result = agent._execute_tool("get_orderbook", {"symbol": ".."})
    assert set(result) == {"error"}
    assert result["error"].startswith("InvalidOrderbookSymbolError: ")
    assert "unsafe path component" in result["error"]
    assert ".." not in result["error"]

    result = agent._execute_tool(
        "estimate_orderbook_impact",
        {"symbol": "A\x1fB", "side": "buy", "quantity": 1.0},
    )
    assert set(result) == {"error"}
    assert "control character" in result["error"]
    assert "\x1f" not in result["error"]
