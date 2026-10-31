"""P5: orderbook inputs must be finite positive numbers (t_35887f34).

``OrderbookLevel.price/size`` and ``ExecutionImpactRequest.quantity`` carried
only ``gt=0``, so ``+inf`` passed every validation boundary while ``NaN``
slipped through the service-level ``quantity <= 0`` check of
``simulate_execution_impact``. A board or order size of ``inf``/``NaN`` then
poisoned ``requested_quantity`` / fillable quantity / ``total_cost`` and the
depth metrics (AGENTS invariants 1/2/9; Private Operator roadmap P5).

Design pinned by the card (CTO-approved):

* The three existing ``Field`` definitions gain ``allow_inf_nan=False``;
  ``gt=0``, types, field names, defaults and normal numeric coercion are
  unchanged.
* ``simulate_execution_impact`` requires ``math.isfinite(quantity)`` and
  ``quantity > 0`` and rejects violations with fixed ``ValueError`` messages
  before any computation. The pre-existing strictly-positive branch keeps its
  exact message; no input value or path is ever embedded in an exception.
* The API keeps the existing Pydantic 422 path (no new error handler, no new
  API). An invalid request must never write a snapshot file and never 500.
* A persisted board containing ``Infinity`` becomes schema-invalid and flows
  through the existing degraded / corrupt-read-ledger path; the original bytes
  are preserved, never overwritten.

All tests run on isolated temporary stores with sockets blocked: no network
and no writes to the product data directory.
"""

from __future__ import annotations

import hashlib
import json
import math
import socket
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from yowayowa.api import orderbook_routes
from yowayowa.api.app import app
from yowayowa.domain import LicenseClass
from yowayowa.orderbook_models import (
    ExecutionImpactRequest,
    OrderbookLevel,
    OrderbookSnapshot,
)
from yowayowa.services.orderbook_service import (
    CORRUPT_READ_LEDGER,
    SYNTHETIC_PROVIDER,
    OrderbookService,
    compute_orderbook_metrics,
    simulate_execution_impact,
)

#: Fixed service-level rejection messages (no input value, no path).
NON_FINITE_QUANTITY_MSG = "Execution quantity must be a finite number."
STRICTLY_POSITIVE_QUANTITY_MSG = "Execution quantity must be strictly positive."

NON_FINITE: tuple[float, ...] = (math.inf, -math.inf, math.nan)
NON_POSITIVE: tuple[float, ...] = (0.0, -1.0)
JSON_STRING_REJECTS: tuple[str, ...] = ("Infinity", "-Infinity", "NaN", "0", "-1")


def _make_inet_guard(real_socket: Callable[..., socket.socket]) -> Callable[..., socket.socket]:
    """Build the socket factory installed by ``_no_network``.

    INET/INET6 must be refused in *every* call form: a positional family, the
    keyword ``family=...``, and the omitted argument (the stdlib default is
    ``AF_INET``). AF_UNIX passes through: the asyncio event loop that drives
    the in-process TestClient needs a ``socketpair`` self-pipe, which is
    local IPC, not network access.
    """

    def _guarded_socket(*args: object, **kwargs: object) -> socket.socket:
        family: object = args[0] if args else kwargs.get("family", socket.AF_INET)
        if family in (socket.AF_INET, socket.AF_INET6):
            raise AssertionError("network access is not allowed in these tests")
        return real_socket(*args, **kwargs)  # type: ignore[arg-type]

    return _guarded_socket


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Prove these tests never touch the network."""

    def _blocked_create_connection(*args: object, **kwargs: object):
        raise AssertionError("network access is not allowed in these tests")

    monkeypatch.setattr(socket, "socket", _make_inet_guard(socket.socket))
    monkeypatch.setattr(socket, "create_connection", _blocked_create_connection)
    yield


def test_no_network_refuses_inet_in_every_call_form() -> None:
    """Regression: positional, keyword, and omitted family are all INET.

    The first harness inspected only ``args[0]``, so ``socket.socket(family=
    AF_INET)``, ``socket.socket()`` (stdlib default AF_INET) and
    ``socket.socket(family=AF_INET6)`` slipped through to the real
    constructor. The guard now raises before delegating, so no socket is
    ever created in these calls.
    """

    with pytest.raises(AssertionError, match="network access is not allowed"):
        socket.socket(socket.AF_INET)
    with pytest.raises(AssertionError, match="network access is not allowed"):
        socket.socket(family=socket.AF_INET)
    with pytest.raises(AssertionError, match="network access is not allowed"):
        socket.socket()
    with pytest.raises(AssertionError, match="network access is not allowed"):
        socket.socket(family=socket.AF_INET6)
    with pytest.raises(AssertionError, match="network access is not allowed"):
        socket.create_connection(("localhost", 80))


def test_no_network_guard_blocks_before_constructor_with_fake_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No-communication roundtrip: the guard fires before any constructor.

    The constructor is first replaced by a recording fake that never creates
    a real socket, then the fixture's guard is layered on top of it,
    reproducing the fixture stack. Every INET/INET6 call form must be
    refused before reaching the fake; AF_UNIX must still pass through to it.
    """

    created: list[tuple[tuple[object, ...], dict[str, object]]] = []

    class _FakeNeverSocket:
        def __init__(self, *args: object, **kwargs: object) -> None:
            created.append((args, kwargs))

    monkeypatch.setattr(socket, "socket", _FakeNeverSocket)
    monkeypatch.setattr(socket, "socket", _make_inet_guard(socket.socket))

    with pytest.raises(AssertionError, match="network access is not allowed"):
        socket.socket(socket.AF_INET)
    with pytest.raises(AssertionError, match="network access is not allowed"):
        socket.socket(family=socket.AF_INET)
    with pytest.raises(AssertionError, match="network access is not allowed"):
        socket.socket()
    with pytest.raises(AssertionError, match="network access is not allowed"):
        socket.socket(family=socket.AF_INET6)

    # The guard refused every form before the constructor: zero sockets.
    assert created == []

    # AF_UNIX (TestClient self-pipe / local IPC) still passes through.
    socket.socket(socket.AF_UNIX)
    assert created == [((socket.AF_UNIX,), {})]


def _level_kwargs(field: str, value: object) -> dict[str, object]:
    """Both OrderbookLevel fields are required; fill the other one validly."""

    return {"price": 100.0, "size": 10.0} | {field: value}


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


# ---------------------------------------------------------------------------
# Model boundary: OrderbookLevel.price / OrderbookLevel.size
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field", ["price", "size"])
@pytest.mark.parametrize("value", [*NON_FINITE, *NON_POSITIVE])
def test_orderbook_level_rejects_non_finite_and_non_positive(field: str, value: float) -> None:
    with pytest.raises(ValidationError):
        OrderbookLevel(**_level_kwargs(field, value))  # type: ignore[arg-type]


@pytest.mark.parametrize("field", ["price", "size"])
@pytest.mark.parametrize("value", JSON_STRING_REJECTS)
def test_orderbook_level_rejects_json_string_forms(field: str, value: str) -> None:
    """The JSON string spellings ("Infinity" et al.) must not become a board."""

    with pytest.raises(ValidationError):
        OrderbookLevel(**_level_kwargs(field, value))  # type: ignore[arg-type]


def test_orderbook_level_normal_values_are_unchanged() -> None:
    """Normal integers/decimals coerce exactly as before (deep-equal dump)."""

    assert OrderbookLevel(price=100, size=10).model_dump() == {
        "price": 100.0,
        "size": 10.0,
        "order_count": None,
    }
    assert OrderbookLevel(price=100.5, size=0.25, order_count=3).model_dump() == {
        "price": 100.5,
        "size": 0.25,
        "order_count": 3,
    }


# ---------------------------------------------------------------------------
# Model boundary: ExecutionImpactRequest.quantity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [*NON_FINITE, *NON_POSITIVE])
def test_execution_impact_request_rejects_non_finite_and_non_positive(
    value: float,
) -> None:
    with pytest.raises(ValidationError):
        ExecutionImpactRequest(side="buy", quantity=value)


@pytest.mark.parametrize("value", JSON_STRING_REJECTS)
def test_execution_impact_request_rejects_json_string_forms(value: str) -> None:
    with pytest.raises(ValidationError):
        ExecutionImpactRequest(side="buy", quantity=value)


def test_execution_impact_request_normal_values_are_unchanged() -> None:
    assert list(ExecutionImpactRequest.model_fields) == ["side", "quantity"]
    assert ExecutionImpactRequest(side="sell", quantity=5).model_dump() == {
        "side": "sell",
        "quantity": 5.0,
    }
    with pytest.raises(ValidationError):
        ExecutionImpactRequest(side="hold", quantity=1.0)


# ---------------------------------------------------------------------------
# Service boundary: simulate_execution_impact direct calls
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("quantity", NON_FINITE)
def test_service_rejects_non_finite_quantity_with_fixed_message(
    quantity: float,
) -> None:
    book = _three_level_book()

    with pytest.raises(ValueError) as excinfo:
        simulate_execution_impact(book, side="buy", quantity=quantity)

    assert str(excinfo.value) == NON_FINITE_QUANTITY_MSG


@pytest.mark.parametrize("quantity", NON_POSITIVE)
def test_service_keeps_strictly_positive_message(quantity: float) -> None:
    """The pre-existing <=0 branch keeps its exact message (no behavior change)."""

    book = _three_level_book()

    with pytest.raises(ValueError) as excinfo:
        simulate_execution_impact(book, side="sell", quantity=quantity)

    assert str(excinfo.value) == STRICTLY_POSITIVE_QUANTITY_MSG


def test_service_normal_numbers_unchanged() -> None:
    """Normal buy/sell, insufficient liquidity, and rounding stay as before."""

    book = _three_level_book()
    res_buy = simulate_execution_impact(book, side="buy", quantity=15.0)
    assert res_buy.fully_filled is True
    assert res_buy.fillable_quantity == 15.0
    assert res_buy.levels_swept == 2
    assert res_buy.best_quote_price == 101.0
    assert res_buy.average_price == 101.333333
    assert res_buy.slippage == 0.333333
    assert res_buy.total_cost == round(10 * 101.0 + 5 * 102.0, 4)

    res_sell = simulate_execution_impact(book, side="sell", quantity=25.0)
    assert res_sell.fully_filled is True
    assert res_sell.fillable_quantity == 25.0
    assert res_sell.average_price == 99.4
    assert res_sell.slippage == 0.6

    tiny = OrderbookSnapshot(
        symbol="TINY",
        bids=[OrderbookLevel(price=100.0, size=10.0)],
        asks=[OrderbookLevel(price=101.0, size=5.0)],
        metrics=compute_orderbook_metrics(
            [OrderbookLevel(price=100.0, size=10.0)],
            [OrderbookLevel(price=101.0, size=5.0)],
        ),
    )
    res_small = simulate_execution_impact(tiny, side="buy", quantity=20.0)
    assert res_small.fully_filled is False
    assert res_small.fillable_quantity == 5.0
    assert res_small.warning == (
        "Liquidity exhausted: only 5.0/20.0 units could be filled across 1 book level(s)."
    )

    no_bids = book.model_copy(update={"bids": []})
    res_empty = simulate_execution_impact(no_bids, side="sell", quantity=10.0)
    assert res_empty.fully_filled is False
    assert res_empty.fillable_quantity == 0.0
    assert res_empty.warning == "No orderbook liquidity available on the requested side."


# ---------------------------------------------------------------------------
# API boundary: existing Pydantic 422 path, no 500, no store write
# ---------------------------------------------------------------------------


@pytest.fixture()
def orderbook_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the router at a scratch orderbook dir (no writes to ./data)."""

    base = tmp_path / "orderbooks"
    monkeypatch.setattr(orderbook_routes, "_service", OrderbookService(base))
    return base


client = TestClient(app)


def _snapshot_body(price: object = 100.0, size: object = 10.0) -> dict[str, object]:
    return {
        "symbol": "7203",
        "as_of": "2026-10-23T00:00:00Z",
        "bids": [{"price": price, "size": size}],
        "asks": [{"price": 101.0, "size": 10.0}],
    }


@pytest.mark.parametrize("value", JSON_STRING_REJECTS)
def test_api_impact_rejects_json_string_quantity(value: str, orderbook_dir: Path) -> None:
    resp = client.post(
        "/v1/orderbook/7203/impact",
        json={"side": "buy", "quantity": value},
    )

    assert resp.status_code == 422
    assert "quantity" in json.dumps(resp.json())
    assert not (orderbook_dir / "7203").exists()


def test_api_impact_rejects_nonstandard_json_literals(orderbook_dir: Path) -> None:
    """NaN/Infinity literals must never persist and never look successful.

    Note (measured 2026-10-25, recorded in
    docs/P5_ORDERBOOK_FINITE_INPUTS_20261024.md §5): pydantic rejects the
    value, but FastAPI's *default* 422 handler then fails to render the error
    because starlette's ``JSONResponse`` serializes with ``allow_nan=False``
    and the pydantic error detail embeds the raw ``inf``/``nan`` input. The
    card's CTO design forbids adding a custom error handler, so only the
    no-persistence property is pinned here; the render failure itself is an
    upstream-behavior observation, not a contract this card introduces.
    """

    for literal in ("Infinity", "-Infinity", "NaN"):
        with pytest.raises(Exception):  # noqa: B017 - upstream render failure
            client.post(
                "/v1/orderbook/7203/impact",
                content=f'{{"side": "buy", "quantity": {literal}}}'.encode(),
                headers={"content-type": "application/json"},
            )
    assert not (orderbook_dir / "7203").exists()


@pytest.mark.parametrize("value", JSON_STRING_REJECTS)
@pytest.mark.parametrize("field", ["price", "size"])
def test_api_snapshot_rejects_non_finite_level_without_writing(
    field: str, value: str, orderbook_dir: Path
) -> None:
    body = _snapshot_body(**{field: value})  # type: ignore[arg-type]
    resp = client.post("/v1/orderbook/7203/snapshots", json=body)

    assert resp.status_code == 422
    assert not (orderbook_dir / "7203" / "latest.json").exists()


def test_api_snapshot_rejects_nonstandard_json_literal_without_writing(
    orderbook_dir: Path,
) -> None:
    """An inf-carrying snapshot must not be persisted.

    See the module note on non-standard JSON literals (measured 2026-10-25):
    the default 422 render fails upstream, so only the no-write property is
    pinned here. Pre-card this same request returned 200 AND wrote an
    inf-carrying latest.json — that hole is closed.
    """

    raw = (
        b'{"symbol": "7203", "as_of": "2026-10-23T00:00:00Z", '
        b'"bids": [{"price": Infinity, "size": 10.0}], '
        b'"asks": [{"price": 101.0, "size": 10.0}]}'
    )
    with pytest.raises(Exception):  # noqa: B017 - upstream render failure
        client.post(
            "/v1/orderbook/7203/snapshots",
            content=raw,
            headers={"content-type": "application/json"},
        )
    assert not (orderbook_dir / "7203" / "latest.json").exists()


def test_api_normal_impact_and_snapshot_unchanged(orderbook_dir: Path) -> None:
    resp = client.post(
        "/v1/orderbook/7203/impact",
        json={"side": "buy", "quantity": 300.0},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["fully_filled"] is True
    assert data["requested_quantity"] == 300.0

    resp = client.post("/v1/orderbook/7203/snapshots", json=_snapshot_body())
    assert resp.status_code == 200
    persisted = json.loads((orderbook_dir / "7203" / "latest.json").read_text(encoding="utf-8"))
    assert persisted["bids"][0]["price"] == 100.0
    assert persisted["asks"][0]["size"] == 10.0


# ---------------------------------------------------------------------------
# Persisted damage: an +inf board flows through the degraded/ledger path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "label"),
    [
        (
            b'{"symbol": "7203", "bids": [{"price": Infinity, "size": 10.0}], "asks": []}',
            "price=+inf",
        ),
        (
            b'{"symbol": "7203", "bids": [], "asks": [{"price": 101.0, "size": Infinity}]}',
            "size=+inf",
        ),
    ],
)
def test_persisted_infinity_board_is_degraded_with_original_preserved(
    tmp_path: Path, raw: bytes, label: str
) -> None:
    del label  # pytest id only
    path = tmp_path / "7203" / "latest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    original_sha = hashlib.sha256(raw).hexdigest()

    service = OrderbookService(tmp_path)
    snapshot = service.get_snapshot("7203")

    # (a) the returned board is advertised as synthetic and degraded
    assert snapshot.provenance.provider == SYNTHETIC_PROVIDER
    assert snapshot.provenance.license_class is LicenseClass.SYNTHETIC
    assert any(note.startswith("degraded:") for note in snapshot.provenance.notes)
    # (b) the original bytes are never overwritten
    assert hashlib.sha256(path.read_bytes()).hexdigest() == original_sha
    # (c) the failure lands in the existing corrupt-read ledger
    ledger = tmp_path / CORRUPT_READ_LEDGER
    records = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 1
    assert records[0]["symbol"] == "7203"
    assert records[0]["error_type"] == "ValidationError"
    assert records[0]["sha256"] == original_sha
    # (d) impact computed on the degraded board carries the degraded provenance
    result = service.estimate_impact("7203", side="buy", quantity=10.0)
    assert result.provenance.license_class is LicenseClass.SYNTHETIC
    assert any(note.startswith("degraded:") for note in result.provenance.notes)
