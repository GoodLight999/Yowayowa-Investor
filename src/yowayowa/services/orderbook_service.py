from __future__ import annotations

import hashlib
import json
import math
import os
import re
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from click import ClickException
from fastapi import HTTPException

from yowayowa.acquisition.snapshots import reject_dot_dot
from yowayowa.domain import LicenseClass, Provenance
from yowayowa.orderbook_models import (
    DepthLevel,
    ExecutionImpactResult,
    OrderbookDepthLadder,
    OrderbookLevel,
    OrderbookMetrics,
    OrderbookSnapshot,
)

DEFAULT_ORDERBOOK_DIR = Path("data/orderbooks")

SYNTHETIC_PROVIDER = "synthetic-seed-orderbook"
SYNTHETIC_SOURCE = "Yowayowa synthetic seed orderbook (not an exchange feed)"
# Append-only ledger of persisted snapshots that could not be read. Kept next to the
# snapshots so a corruption is never observable only through a fabricated board.
CORRUPT_READ_LEDGER = "corrupt_reads.jsonl"

# One validation boundary for every symbol that reaches the snapshot store.
# Rejections are explicit and leak-free: the message never echoes the raw
# input, its bytes, or any filesystem path.
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")


class InvalidOrderbookSymbolError(HTTPException, ClickException, ValueError):
    """A symbol that cannot be mapped to a safe path inside the snapshot store.

    One exception, three surface mappings, no message re-formatting anywhere:

    * FastAPI routes: HTTPException handling -> 400 ``{"detail": ...}``;
    * click/typer CLI: ClickException handling -> one-line ``Error: ...`` and
      exit code 1;
    * AI-tool dispatch: the generic exception wrap -> ``{"error": ...}``.

    ``ValueError`` keeps direct service callers on the exception family they
    already handle. The message is a fixed string: it never contains the raw
    input, its bytes, or any filesystem path.
    """

    def __init__(self, reason: str) -> None:
        detail = (
            "Symbol cannot be empty."
            if reason == "empty"
            else f"Invalid orderbook symbol: {reason}."
        )
        HTTPException.__init__(self, status_code=400, detail=detail)
        ClickException.__init__(self, detail)

    def __str__(self) -> str:
        # starlette's HTTPException.__str__ prefixes the status code; every
        # surface here renders the bare fixed message.
        return str(self.detail)


def _validated_orderbook_symbol(symbol: str) -> str:
    """Validate one symbol for the snapshot store; return it stripped.

    Applies the same strip the API route already performs, keeps the existing
    normalization (``/``->``_``, ``:``->``_``, upper) for the storage path, and
    rejects anything that could escape or alias the store: dot-dot segments
    (the exact rule shared with acquisition/snapshots.py), ``.``/``..``
    segments, control characters (NUL included), path separators,
    absolute-path forms, separator-only names, and paths that resolve outside
    the store. Legitimate symbols are returned stripped and otherwise
    untouched, so storage paths and stored content do not change.
    """
    stripped = symbol.strip()
    if not stripped:
        raise InvalidOrderbookSymbolError("empty")
    if stripped.startswith(("/", "\\")) or _WINDOWS_DRIVE_RE.match(stripped):
        raise InvalidOrderbookSymbolError("absolute path form")
    try:
        reject_dot_dot(stripped)
    except ValueError:
        raise InvalidOrderbookSymbolError("unsafe path component") from None
    norm = stripped.replace("/", "_").replace(":", "_").upper()
    if not norm:  # defensive; strip/replace cannot empty a non-empty symbol
        raise InvalidOrderbookSymbolError("path separator only")
    if norm in {".", ".."}:
        raise InvalidOrderbookSymbolError("unsafe path component")
    if any(unicodedata.category(ch) == "Cc" for ch in norm):
        raise InvalidOrderbookSymbolError("control character")
    separators = {os.sep, "\\"}
    if os.altsep:
        separators.add(os.altsep)
    if any(ch in norm for ch in separators):
        raise InvalidOrderbookSymbolError("path separator")
    if set(norm) <= {"_"}:
        raise InvalidOrderbookSymbolError("path separator only")
    return stripped


def compute_orderbook_metrics(
    bids: list[OrderbookLevel],
    asks: list[OrderbookLevel],
) -> OrderbookMetrics:
    """Compute microstructure metrics from sorted bids (descending) and asks (ascending)."""
    sorted_bids = sorted(bids, key=lambda x: x.price, reverse=True)
    sorted_asks = sorted(asks, key=lambda x: x.price)

    best_bid = sorted_bids[0].price if sorted_bids else None
    best_ask = sorted_asks[0].price if sorted_asks else None

    spread: float | None = None
    mid_price: float | None = None
    spread_bps: float | None = None
    if best_bid is not None and best_ask is not None:
        spread = round(best_ask - best_bid, 6)
        mid_price = round((best_bid + best_ask) / 2.0, 6)
        if mid_price > 0:
            spread_bps = round((spread / mid_price) * 10000.0, 2)

    bid_depth = round(sum(b.size for b in sorted_bids), 4)
    ask_depth = round(sum(a.size for a in sorted_asks), 4)
    total_depth = round(bid_depth + ask_depth, 4)

    order_flow_imbalance: float | None = None
    if total_depth > 0:
        order_flow_imbalance = round((bid_depth - ask_depth) / total_depth, 4)

    micro_price: float | None = None
    if sorted_bids and sorted_asks and best_bid is not None and best_ask is not None:
        b1_size = sorted_bids[0].size
        a1_size = sorted_asks[0].size
        if b1_size + a1_size > 0:
            micro_price = round((best_bid * a1_size + best_ask * b1_size) / (b1_size + a1_size), 6)

    hft_indicator: Literal["HIGH", "MODERATE", "LOW", "UNKNOWN"] = "UNKNOWN"
    if spread_bps is not None and total_depth > 0:
        if spread_bps <= 2.5:
            hft_indicator = "HIGH"
        elif spread_bps <= 6.0:
            hft_indicator = "MODERATE"
        else:
            hft_indicator = "LOW"

    return OrderbookMetrics(
        best_bid=best_bid,
        best_ask=best_ask,
        mid_price=mid_price,
        spread=spread,
        spread_bps=spread_bps,
        bid_depth_total=bid_depth,
        ask_depth_total=ask_depth,
        total_depth=total_depth,
        order_flow_imbalance=order_flow_imbalance,
        micro_price=micro_price,
        hft_activity_indicator=hft_indicator,
    )


def compute_depth_ladder(
    snapshot: OrderbookSnapshot,
    max_levels: int = 20,
) -> OrderbookDepthLadder:
    """Generate a cumulative depth ladder for visualising market depth."""
    bids_cumulative: list[DepthLevel] = []
    running_bid = 0.0
    for level in snapshot.bids[:max_levels]:
        running_bid = round(running_bid + level.size, 4)
        bids_cumulative.append(
            DepthLevel(
                price=level.price,
                size=level.size,
                cumulative_size=running_bid,
            )
        )

    asks_cumulative: list[DepthLevel] = []
    running_ask = 0.0
    for level in snapshot.asks[:max_levels]:
        running_ask = round(running_ask + level.size, 4)
        asks_cumulative.append(
            DepthLevel(
                price=level.price,
                size=level.size,
                cumulative_size=running_ask,
            )
        )

    return OrderbookDepthLadder(
        symbol=snapshot.symbol,
        as_of=snapshot.as_of,
        mid_price=snapshot.metrics.mid_price,
        bids_cumulative=bids_cumulative,
        asks_cumulative=asks_cumulative,
        provenance=snapshot.provenance,
    )


def simulate_execution_impact(
    snapshot: OrderbookSnapshot,
    side: Literal["buy", "sell"],
    quantity: float,
) -> ExecutionImpactResult:
    """Simulate a market order walking the book to compute slippage and market impact."""
    # Reject non-finite order sizes before any computation: NaN/+inf would
    # poison requested_quantity, fillable_quantity and total_cost. The
    # messages are fixed constants — no input value is ever embedded.
    if not math.isfinite(quantity):
        raise ValueError("Execution quantity must be a finite number.")
    if quantity <= 0:
        raise ValueError("Execution quantity must be strictly positive.")

    levels = snapshot.asks if side == "buy" else snapshot.bids
    if not levels:
        return ExecutionImpactResult(
            symbol=snapshot.symbol,
            side=side,
            requested_quantity=quantity,
            fillable_quantity=0.0,
            fully_filled=False,
            warning="No orderbook liquidity available on the requested side.",
            provenance=snapshot.provenance,
        )

    best_quote = levels[0].price
    remaining = float(quantity)
    filled = 0.0
    cost = 0.0
    levels_swept = 0

    for level in levels:
        if remaining <= 1e-9:
            break
        fill_size = min(remaining, level.size)
        cost += fill_size * level.price
        filled += fill_size
        remaining -= fill_size
        levels_swept += 1

    filled = round(filled, 6)
    cost = round(cost, 4)
    avg_price = round(cost / filled, 6) if filled > 0 else None

    slippage: float | None = None
    slippage_bps: float | None = None
    if avg_price is not None and best_quote > 0:
        if side == "buy":
            slippage = round(avg_price - best_quote, 6)
        else:
            slippage = round(best_quote - avg_price, 6)
        slippage_bps = round((slippage / best_quote) * 10000.0, 2)

    fully_filled = remaining <= 1e-9
    warning: str | None = None
    if not fully_filled:
        warning = (
            f"Liquidity exhausted: only {filled}/{quantity} units could be filled "
            f"across {levels_swept} book level(s)."
        )

    return ExecutionImpactResult(
        symbol=snapshot.symbol,
        side=side,
        requested_quantity=quantity,
        fillable_quantity=filled,
        fully_filled=fully_filled,
        average_price=avg_price,
        best_quote_price=best_quote,
        slippage=slippage,
        slippage_bps=slippage_bps,
        total_cost=cost,
        levels_swept=levels_swept,
        warning=warning,
        provenance=snapshot.provenance,
    )


def generate_seed_orderbook(symbol: str) -> OrderbookSnapshot:
    """Generate a synthetic Level-2 orderbook for simulation and testing.

    The board is fabricated locally: it is *not* sourced from any exchange. Its provenance
    carries ``provider="synthetic-seed-orderbook"`` and ``license_class=SYNTHETIC`` so that
    API / CLI / LLM consumers can never mistake it for real market data.
    """
    sym = symbol.upper()
    now = datetime.now(UTC)

    # Price scales below are synthetic scaffolding, not quotes from a real venue.
    if sym in {"7203", "7203.T", "TOYOTA"}:
        base_price = 2850.0
        tick = 1.0
        lot = 1000.0
    elif sym in {"BTC/USD", "BTCUSDT", "BTC"}:
        base_price = 65200.0
        tick = 5.0
        lot = 0.45
    elif sym in {"ETH/USD", "ETHUSDT", "ETH"}:
        base_price = 3450.0
        tick = 0.5
        lot = 3.2
    elif sym in {"NK225M", "N225MINI", "NI225"}:
        base_price = 38900.0
        tick = 5.0
        lot = 25.0
    else:
        base_price = 1500.0
        tick = 1.0
        lot = 500.0

    bids: list[OrderbookLevel] = []
    asks: list[OrderbookLevel] = []

    # 10 levels deep on each side
    for i in range(1, 11):
        bid_p = round(base_price - (i * tick), 2)
        ask_p = round(base_price + (i * tick), 2)
        bid_sz = round(lot * (1.0 + (i * 0.25)), 4)
        ask_sz = round(lot * (0.9 + (i * 0.3)), 4)
        bids.append(OrderbookLevel(price=bid_p, size=bid_sz, order_count=3 + i))
        asks.append(OrderbookLevel(price=ask_p, size=ask_sz, order_count=2 + i))

    metrics = compute_orderbook_metrics(bids, asks)
    return OrderbookSnapshot(
        symbol=symbol,
        as_of=now,
        bids=bids,
        asks=asks,
        metrics=metrics,
        provenance=Provenance(
            provider=SYNTHETIC_PROVIDER,
            source=SYNTHETIC_SOURCE,
            license_class=LicenseClass.SYNTHETIC,
            retrieved_at=now,
            notes=["Synthetic multi-level depth generated locally; not live exchange data."],
        ),
    )


class OrderbookService:
    """Service to persist, retrieve, and analyze Level-2 orderbook data."""

    def __init__(self, base_dir: Path | str | None = None) -> None:
        self.base_dir = Path(base_dir) if base_dir is not None else DEFAULT_ORDERBOOK_DIR

    def _symbol_path(self, symbol: str) -> Path:
        norm = _validated_orderbook_symbol(symbol).replace("/", "_").replace(":", "_").upper()
        path = self.base_dir / norm / "latest.json"
        # Defense in depth: the string rules above already rule out traversal,
        # but the resolved path must stay inside the store to be addressable.
        try:
            path.resolve().relative_to(self.base_dir.resolve())
        except (OSError, ValueError):
            raise InvalidOrderbookSymbolError("escapes the orderbook store") from None
        return path

    def _with_symbol_mismatch_note(
        self, snapshot: OrderbookSnapshot, requested: str
    ) -> OrderbookSnapshot:
        """Surface a request symbol that resolves to a board saved under a
        different symbol, instead of silently returning the other board. The
        normalization ``BTC:USD`` -> ``BTC_USD`` is the existing shared-path
        design and is kept; the mismatch itself is made explicit."""
        if snapshot.symbol == requested:
            return snapshot
        note = (
            "symbol mismatch: the requested symbol resolves to the store path of a "
            f"board saved as {snapshot.symbol!r}; this is that stored board, "
            f"not a board captured for {requested!r}."
        )
        provenance = snapshot.provenance.model_copy(
            update={"notes": [*snapshot.provenance.notes, note]}
        )
        return snapshot.model_copy(update={"provenance": provenance})

    def record_snapshot(self, snapshot: OrderbookSnapshot) -> OrderbookSnapshot:
        """Recalculate metrics, ensure sorted order, and persist snapshot."""
        clean = _validated_orderbook_symbol(snapshot.symbol)
        # Ensure sorted order: bids descending, asks ascending
        sorted_bids = sorted(snapshot.bids, key=lambda x: x.price, reverse=True)
        sorted_asks = sorted(snapshot.asks, key=lambda x: x.price)
        metrics = compute_orderbook_metrics(sorted_bids, sorted_asks)

        normalized_snapshot = OrderbookSnapshot(
            symbol=clean,
            as_of=snapshot.as_of,
            bids=sorted_bids,
            asks=sorted_asks,
            metrics=metrics,
            provenance=snapshot.provenance,
        )

        out_path = self._symbol_path(clean)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(normalized_snapshot.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return normalized_snapshot

    def _record_corrupt_read(
        self,
        *,
        symbol: str,
        path: Path,
        exc: BaseException,
        payload: bytes | None,
    ) -> dict[str, Any]:
        """Append a durable record of a corrupted persisted snapshot.

        Records the bytes actually on disk (size + sha256) so the operator can tell which
        damaged file was involved, then returns the record for the caller to surface.
        """
        sha256 = hashlib.sha256(payload).hexdigest() if payload is not None else None
        record: dict[str, Any] = {
            "recorded_at": datetime.now(UTC).isoformat(),
            "symbol": symbol,
            "path": str(path),
            "error_type": type(exc).__name__,
            "error": str(exc)[:500],
            "bytes": len(payload) if payload is not None else None,
            "sha256": sha256,
            "fallback_provider": SYNTHETIC_PROVIDER,
            "action": "original file preserved; synthetic seed board returned but not persisted",
        }

        ledger = self.base_dir / CORRUPT_READ_LEDGER
        try:
            previous = ledger.read_text(encoding="utf-8").splitlines() if ledger.exists() else []
            if previous:
                last = json.loads(previous[-1])
                if (
                    last.get("symbol") == symbol
                    and last.get("sha256") == sha256
                    and last.get("error_type") == record["error_type"]
                ):
                    # Same damage, already recorded: keep one durable entry instead of
                    # growing the ledger on every failing read.
                    return record
        except (OSError, ValueError) as read_exc:
            record["ledger_read_error"] = f"{type(read_exc).__name__}: {read_exc}"

        try:
            ledger.parent.mkdir(parents=True, exist_ok=True)
            with ledger.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as write_exc:
            record["ledger_error"] = f"{type(write_exc).__name__}: {write_exc}"
        return record

    def _degraded_seed_snapshot(
        self,
        symbol: str,
        exc: BaseException,
        record: dict[str, Any],
    ) -> OrderbookSnapshot:
        """Build the synthetic board returned when a persisted snapshot is unreadable."""
        seed = generate_seed_orderbook(symbol)
        notes = list(seed.provenance.notes)
        notes.append(
            "degraded: the persisted snapshot for this symbol could not be read "
            f"({type(exc).__name__}: {str(exc)[:200]}); the stored file was preserved and "
            "this board is synthetic, not a captured feed."
        )
        if record.get("ledger_error"):
            notes.append(f"corruption ledger write failed: {record['ledger_error']}")
        if record.get("ledger_read_error"):
            notes.append(f"corruption ledger read failed: {record['ledger_read_error']}")
        provenance = seed.provenance.model_copy(update={"notes": notes})
        return seed.model_copy(update={"provenance": provenance})

    def get_snapshot(self, symbol: str) -> OrderbookSnapshot:
        """Return the persisted snapshot, or a clearly synthetic board when none exists.

        A persisted snapshot that is present but unreadable (truncated write, hand edit, old
        schema) is never overwritten and never silently replaced by fabricated data: the
        original bytes stay on disk, the failure lands in ``orderbooks/corrupt_reads.jsonl``,
        and the returned board advertises synthetic provenance plus a degraded note.

        Every symbol passes the single validation boundary first: traversal and
        degenerate symbols are rejected explicitly without touching the store.
        """
        clean = _validated_orderbook_symbol(symbol)
        path = self._symbol_path(clean)
        if not path.exists():
            # Nothing captured yet: bootstrap with a synthetic board and persist it.
            return self.record_snapshot(generate_seed_orderbook(clean))

        payload: bytes | None = None
        try:
            payload = path.read_bytes()
            data = json.loads(payload.decode("utf-8"))
            snapshot = OrderbookSnapshot.model_validate(data)
        except (OSError, ValueError) as exc:
            # json.JSONDecodeError, UnicodeDecodeError and pydantic ValidationError are all
            # ValueError subclasses; OSError covers I/O failures on the persisted file.
            record = self._record_corrupt_read(symbol=clean, path=path, exc=exc, payload=payload)
            return self._degraded_seed_snapshot(clean, exc, record)
        return self._with_symbol_mismatch_note(snapshot, clean)

    def get_depth_ladder(self, symbol: str, max_levels: int = 20) -> OrderbookDepthLadder:
        snapshot = self.get_snapshot(symbol)
        return compute_depth_ladder(snapshot, max_levels=max_levels)

    def get_metrics(self, symbol: str) -> OrderbookMetrics:
        snapshot = self.get_snapshot(symbol)
        return snapshot.metrics

    def estimate_impact(
        self,
        symbol: str,
        side: Literal["buy", "sell"],
        quantity: float,
    ) -> ExecutionImpactResult:
        snapshot = self.get_snapshot(symbol)
        return simulate_execution_impact(snapshot, side=side, quantity=quantity)
