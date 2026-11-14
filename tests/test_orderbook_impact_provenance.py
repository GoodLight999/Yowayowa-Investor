"""P5: execution-impact responses must carry the board's provenance.

``ExecutionImpactResult`` used to expose twelve bare numbers with no record of
which board they came from, while the same snapshot served through
``get_depth_ladder`` kept its ``provenance``. A synthetic or degraded board
(``license_class=synthetic``, ``provider=synthetic-seed-orderbook``, degraded
notes) could therefore be read by API / CLI / LLM consumers as if the numbers
were sourced from a real feed (AGENTS invariants 1/3/9; Private Operator
roadmap P1C/P5).

Design pinned by the card (t_5686873e, CTO-approved):

* ``provenance: Provenance`` is added to ``ExecutionImpactResult`` additively:
  the twelve pre-card fields keep their names, types, defaults, values and
  order; ``provenance`` mirrors ``snapshot.provenance`` as-is.
* Both the no-liquidity early return and the normal path must carry it.
* Every number (fill quantities, prices, slippage, costs, warnings) must be
  bit-for-bit identical to the pre-card implementation.

The reference implementation below (``_reference_impact_v1``) is a verbatim
copy of the pre-card ``simulate_execution_impact`` body
(orderbook_service.py sha256 ``3d3ebbc2b0f04c6c0c86ee75326ee9eb0bb3cd322b77ca42e9b8364645c521cf``)
with the final ``ExecutionImpactResult(...)`` constructions swapped for
``dict(...)`` so the twelve pre-card values can be compared without depending
on the new field. All tests run on isolated temporary stores; no network and
no writes to the product data directory.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import pytest
from fastapi.testclient import TestClient

from yowayowa.api import orderbook_routes
from yowayowa.api.app import app
from yowayowa.domain import LicenseClass, Provenance
from yowayowa.orderbook_models import (
    ExecutionImpactResult,
    OrderbookLevel,
    OrderbookSnapshot,
)
from yowayowa.services.orderbook_service import (
    SYNTHETIC_PROVIDER,
    OrderbookService,
    compute_orderbook_metrics,
    generate_seed_orderbook,
    simulate_execution_impact,
)

#: The twelve pre-card output fields, in their pinned order.
PRE_CARD_FIELDS: tuple[str, ...] = (
    "symbol",
    "side",
    "requested_quantity",
    "fillable_quantity",
    "fully_filled",
    "average_price",
    "best_quote_price",
    "slippage",
    "slippage_bps",
    "total_cost",
    "levels_swept",
    "warning",
)


def _reference_impact_v1(
    snapshot: OrderbookSnapshot,
    side: Literal["buy", "sell"],
    quantity: float,
) -> dict[str, object]:
    """Verbatim pre-card ``simulate_execution_impact`` (container swap only)."""

    if quantity <= 0:
        raise ValueError("Execution quantity must be strictly positive.")

    levels = snapshot.asks if side == "buy" else snapshot.bids
    if not levels:
        return dict(
            symbol=snapshot.symbol,
            side=side,
            requested_quantity=quantity,
            fillable_quantity=0.0,
            fully_filled=False,
            warning="No orderbook liquidity available on the requested side.",
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

    return dict(
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
    )


def _three_level_book(symbol: str = "TEST") -> OrderbookSnapshot:
    bids = [
        OrderbookLevel(price=100.0, size=10.0, order_count=3),
        OrderbookLevel(price=99.0, size=20.0, order_count=5),
        OrderbookLevel(price=98.0, size=50.0, order_count=7),
    ]
    asks = [
        OrderbookLevel(price=101.0, size=10.0, order_count=2),
        OrderbookLevel(price=102.0, size=20.0, order_count=4),
        OrderbookLevel(price=103.0, size=50.0, order_count=6),
    ]
    return OrderbookSnapshot(
        symbol=symbol,
        bids=bids,
        asks=asks,
        metrics=compute_orderbook_metrics(bids, asks),
    )


#: Pre-card ``ExecutionImpactResult`` defaults. The pre-card early-return
#: construction omitted these keys, so the old model supplied them; the
#: comparator applies them where the verbatim reference dict is sparse.
PRE_CARD_DEFAULTS: dict[str, object] = {
    "average_price": None,
    "best_quote_price": None,
    "slippage": None,
    "slippage_bps": None,
    "total_cost": None,
    "levels_swept": 0,
    "warning": None,
}


def _assert_pre_card_numbers_equal(
    result: ExecutionImpactResult,
    snapshot: OrderbookSnapshot,
    side: Literal["buy", "sell"],
    quantity: float,
) -> None:
    """The twelve pre-card outputs must equal the verbatim reference and the
    result must expose exactly those fields plus ``provenance``."""

    dump = result.model_dump()
    assert set(dump) == set(PRE_CARD_FIELDS) | {"provenance"}
    reference = _reference_impact_v1(snapshot, side, quantity)
    for field in PRE_CARD_FIELDS:
        expected = reference[field] if field in reference else PRE_CARD_DEFAULTS[field]
        assert dump[field] == expected, field


# ---------------------------------------------------------------------------
# Contract shape: additive field, pre-card order untouched
# ---------------------------------------------------------------------------


def test_execution_impact_result_fields_are_additive() -> None:
    """The twelve pre-card fields keep their order; provenance is appended."""

    assert list(ExecutionImpactResult.model_fields) == [*PRE_CARD_FIELDS, "provenance"]


# ---------------------------------------------------------------------------
# System 1: synthetic board (seed orderbook)
# ---------------------------------------------------------------------------


def test_synthetic_board_provenance_is_carried() -> None:
    snapshot = generate_seed_orderbook("BTC/USD")
    assert snapshot.provenance.provider == SYNTHETIC_PROVIDER
    assert snapshot.provenance.license_class is LicenseClass.SYNTHETIC
    assert snapshot.provenance.notes  # the synthetic disclaimer is on the board

    for side, quantity in (("buy", 2.5), ("sell", 1.0)):
        result = simulate_execution_impact(snapshot, side=side, quantity=quantity)
        assert result.provenance == snapshot.provenance
        _assert_pre_card_numbers_equal(result, snapshot, side, quantity)


# ---------------------------------------------------------------------------
# System 2: real provenance board (operator-captured feed)
# ---------------------------------------------------------------------------


def test_real_provenance_board_provenance_is_carried(tmp_path: Path) -> None:
    service = OrderbookService(tmp_path)
    recorded = service.record_snapshot(
        OrderbookSnapshot(
            symbol="ORIG",
            bids=[OrderbookLevel(price=100.0, size=10.0)],
            asks=[OrderbookLevel(price=101.0, size=10.0)],
            metrics=compute_orderbook_metrics(
                [OrderbookLevel(price=100.0, size=10.0)],
                [OrderbookLevel(price=101.0, size=10.0)],
            ),
            provenance=Provenance(
                provider="jpx-tse-feed",
                source="captured feed",
                source_url="https://example.test/feed/ORIG",
                license_class=LicenseClass.OFFICIAL_PUBLIC,
                retrieved_at=datetime(2026, 10, 24, 5, 0, tzinfo=UTC),
                notes=["operator capture"],
            ),
        )
    )
    loaded = service.get_snapshot("ORIG")

    result_model = simulate_execution_impact(loaded, side="buy", quantity=10.0)
    assert result_model.provenance == loaded.provenance
    assert result_model.provenance == recorded.provenance
    assert result_model.provenance.provider == "jpx-tse-feed"
    assert result_model.provenance.license_class is LicenseClass.OFFICIAL_PUBLIC
    assert result_model.provenance.notes == ["operator capture"]

    result_service = service.estimate_impact("ORIG", side="sell", quantity=5.0)
    assert result_service.provenance == loaded.provenance
    _assert_pre_card_numbers_equal(result_service, loaded, "sell", 5.0)


# ---------------------------------------------------------------------------
# System 3: degraded board (persisted snapshot unreadable)
# ---------------------------------------------------------------------------


def test_degraded_board_provenance_is_carried(tmp_path: Path) -> None:
    service = OrderbookService(tmp_path)
    path = tmp_path / "7203" / "latest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    corrupt_bytes = b'{"symbol": "7203", "bids": [{"price": 2850.0, "size": 1000.0}], "as'
    path.write_bytes(corrupt_bytes)
    original_sha = hashlib.sha256(corrupt_bytes).hexdigest()

    degraded = service.get_snapshot("7203")
    assert degraded.provenance.provider == SYNTHETIC_PROVIDER
    assert any(note.startswith("degraded:") for note in degraded.provenance.notes)

    result = simulate_execution_impact(degraded, side="buy", quantity=100.0)
    assert result.provenance == degraded.provenance
    assert any(note.startswith("degraded:") for note in result.provenance.notes)
    _assert_pre_card_numbers_equal(result, degraded, "buy", 100.0)

    via_service = service.estimate_impact("7203", side="sell", quantity=100.0)
    assert via_service.provenance.provider == SYNTHETIC_PROVIDER
    assert via_service.provenance.license_class is LicenseClass.SYNTHETIC
    assert any(note.startswith("degraded:") for note in via_service.provenance.notes)
    # the damaged file itself is still untouched
    assert hashlib.sha256(path.read_bytes()).hexdigest() == original_sha


# ---------------------------------------------------------------------------
# System 4: no-liquidity early return
# ---------------------------------------------------------------------------


def test_no_liquidity_early_return_carries_provenance() -> None:
    snapshot = _three_level_book("EMPTYBIDS")
    snapshot = snapshot.model_copy(update={"bids": []})
    assert snapshot.bids == []

    result = simulate_execution_impact(snapshot, side="sell", quantity=10.0)

    assert result.fully_filled is False
    assert result.fillable_quantity == 0.0
    assert result.warning == "No orderbook liquidity available on the requested side."
    assert result.provenance == snapshot.provenance
    _assert_pre_card_numbers_equal(result, snapshot, "sell", 10.0)


# ---------------------------------------------------------------------------
# Numbers: verbatim parity with the pre-card implementation, determinism
# ---------------------------------------------------------------------------


def test_numbers_bit_for_bit_equal_to_pre_card_reference() -> None:
    book = _three_level_book()
    exact_book = OrderbookSnapshot(
        symbol="EXACT",
        bids=[OrderbookLevel(price=100.0, size=10.0)],
        asks=[OrderbookLevel(price=101.0, size=10.0)],
        metrics=compute_orderbook_metrics(
            [OrderbookLevel(price=100.0, size=10.0)],
            [OrderbookLevel(price=101.0, size=10.0)],
        ),
    )
    asks_only = OrderbookSnapshot(
        symbol="ASKSONLY",
        bids=[],
        asks=[OrderbookLevel(price=101.0, size=10.0)],
        metrics=compute_orderbook_metrics([], [OrderbookLevel(price=101.0, size=10.0)]),
    )
    cases: list[tuple[OrderbookSnapshot, Literal["buy", "sell"], float]] = [
        (book, "buy", 15.0),  # full fill across two levels
        (book, "sell", 25.0),  # full fill across two levels
        (book, "buy", 200.0),  # liquidity exhausted (80.0 fillable)
        (book, "sell", 5.0),  # single-level fill
        (book, "buy", 12.5),  # fractional fill across two levels
        (exact_book, "buy", 10.0),  # exact-fill boundary
        (asks_only, "buy", 4.0),  # one-sided book, normal path
        (asks_only, "sell", 4.0),  # early return on the empty side
    ]
    for snapshot, side, quantity in cases:
        result = simulate_execution_impact(snapshot, side=side, quantity=quantity)
        _assert_pre_card_numbers_equal(result, snapshot, side, quantity)


def test_same_input_reexecution_is_identical(tmp_path: Path) -> None:
    service = OrderbookService(tmp_path)
    first = service.estimate_impact("BTC/USD", side="buy", quantity=3.0)
    second = service.estimate_impact("BTC/USD", side="buy", quantity=3.0)
    assert first == second
    assert first.model_dump() == second.model_dump()


# ---------------------------------------------------------------------------
# API surface: the one new key, existing keys untouched
# ---------------------------------------------------------------------------


def test_api_impact_response_carries_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Point the router at a scratch orderbook dir (no writes to ./data);
    # monkeypatch restores the module default afterwards.
    monkeypatch.setattr(orderbook_routes, "_service", OrderbookService(tmp_path))
    client = TestClient(app)
    snapshot_json = client.get("/v1/orderbook/APITEST").json()

    resp = client.post(
        "/v1/orderbook/APITEST/impact",
        json={"side": "buy", "quantity": 300.0},
    )

    assert resp.status_code == 200
    data = resp.json()
    # additive contract: the twelve pre-card keys are untouched, one key added
    assert set(data) == set(PRE_CARD_FIELDS) | {"provenance"}
    # provenance mirrors the served board exactly (deep-equal in JSON)
    assert data["provenance"] == snapshot_json["provenance"]
    # the synthetic board is readable as synthetic
    assert data["provenance"]["provider"] == SYNTHETIC_PROVIDER
    assert data["provenance"]["license_class"] == "synthetic"
    assert any("not live exchange data" in note for note in data["provenance"]["notes"])
    # numbers stay pinned to the pre-card implementation
    served_snapshot = OrderbookSnapshot.model_validate(snapshot_json)
    result = ExecutionImpactResult.model_validate(data)
    _assert_pre_card_numbers_equal(result, served_snapshot, "buy", 300.0)
    assert json.dumps(data["provenance"], sort_keys=True) == json.dumps(
        served_snapshot.provenance.model_dump(mode="json"), sort_keys=True
    )
