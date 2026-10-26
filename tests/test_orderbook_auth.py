"""Authentication boundary for the ``/v1/orderbook`` router (audit P1).

``docs/OPENAPI_DRIFT_20261023.md`` §4 P1 / §9-1 measured these five operations
as the only ``/v1`` surface without the router-level ``require_api_token``
boundary that ``routes.py``, ``screening_routes.py`` and
``portfolio_sizing_routes.py`` apply. These tests pin the boundary in both
directions and pin the deps.py behaviour this card did *not* change:

* no token / wrong token            -> 401, and no orderbook file is written
* valid token                       -> 200
* ``api_token`` unset in settings   -> 200 (deps.py fail-open, unchanged)
* other ``/v1`` routers             -> boundary untouched
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from yowayowa.api import orderbook_routes
from yowayowa.api.app import app
from yowayowa.config import Settings, get_settings
from yowayowa.services.orderbook_service import OrderbookService

TOKEN = "orderbook-auth-test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
WRONG_AUTH = {"Authorization": "Bearer not-the-token"}

IMPACT_BODY: dict[str, object] = {"side": "buy", "quantity": 10.0}


def snapshot_body(symbol: str = "7203") -> dict[str, object]:
    """A minimal valid OrderbookSnapshot payload (the POST /snapshots body)."""
    return {
        "symbol": symbol,
        "as_of": "2026-10-23T00:00:00Z",
        "bids": [{"price": 100.0, "size": 10.0}],
        "asks": [{"price": 101.0, "size": 10.0}],
        "provenance": {
            "provider": "jpx-tse-feed",
            "source": "captured feed",
            "license_class": "official_public",
            "retrieved_at": "2026-10-23T00:00:00Z",
        },
    }


def orderbook_requests() -> list[tuple[str, str, dict[str, object] | None]]:
    """The five operations the audit measured as unauthenticated."""
    return [
        ("GET", "/v1/orderbook/7203", None),
        ("GET", "/v1/orderbook/7203/depth?max_levels=5", None),
        ("GET", "/v1/orderbook/7203/metrics", None),
        ("POST", "/v1/orderbook/7203/impact", IMPACT_BODY),
        ("POST", "/v1/orderbook/7203/snapshots", snapshot_body()),
    ]


@pytest.fixture()
def orderbook_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the router at a scratch orderbook dir (no writes to ./data)."""
    base = tmp_path / "orderbooks"
    monkeypatch.setattr(orderbook_routes, "_service", OrderbookService(base))
    return base


@pytest.fixture()
def token_client() -> Iterator[TestClient]:
    """A client whose app sees ``api_token`` configured (personal mode)."""
    app.dependency_overrides[get_settings] = lambda: Settings(mode="personal", api_token=TOKEN)
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_settings, None)


def test_orderbook_routes_reject_missing_and_wrong_token(
    token_client: TestClient, orderbook_dir: Path
) -> None:
    for method, url, body in orderbook_requests():
        missing = token_client.request(method, url, json=body)
        assert missing.status_code == 401, f"{method} {url} without token"
        assert missing.json()["detail"] == "Invalid API token"

        wrong = token_client.request(method, url, json=body, headers=WRONG_AUTH)
        assert wrong.status_code == 401, f"{method} {url} with a wrong token"

    # The 401 must come before request validation: an unauthenticated caller
    # must not be able to probe the payload/query schema (no 422 leak).
    for method, url in (
        ("POST", "/v1/orderbook/7203/impact"),
        ("POST", "/v1/orderbook/7203/snapshots"),
        ("GET", "/v1/orderbook/7203/depth?max_levels=999"),
    ):
        resp = token_client.request(method, url, json=None)
        assert resp.status_code == 401, f"{method} {url} with an invalid request"

    # Fail closed also means side effects stop: POST /snapshots writes
    # latest.json on disk, and get_snapshot seeds one. Nothing may be written.
    assert not orderbook_dir.exists() or not list(orderbook_dir.iterdir())


def test_orderbook_routes_accept_valid_token(token_client: TestClient, orderbook_dir: Path) -> None:
    for method, url, body in orderbook_requests():
        resp = token_client.request(method, url, json=body, headers=AUTH)
        assert resp.status_code == 200, f"{method} {url} with a valid token"

    # The authenticated POST did persist, so the assertion above is not vacuous.
    assert (orderbook_dir / "7203" / "latest.json").is_file()


def test_orderbook_routes_stay_open_when_api_token_is_unset(
    orderbook_dir: Path,
) -> None:
    """deps.py ``require_api_token`` is a no-op without a configured token.

    This is pre-existing behaviour; the card deliberately does not change it,
    so it is pinned here rather than left implicit.
    """
    client = TestClient(app)
    for method, url, body in orderbook_requests():
        resp = client.request(method, url, json=body)
        assert resp.status_code == 200, f"{method} {url} with no token configured"


def test_other_v1_routers_keep_their_auth_boundary(token_client: TestClient) -> None:
    """Adding the router dependency to orderbook must not leak into other routers."""
    unauthenticated = token_client.get("/v1/portfolios")
    assert unauthenticated.status_code == 401

    authenticated = token_client.get("/v1/portfolios", headers=AUTH)
    assert authenticated.status_code == 200

    screening = token_client.post("/v1/screening/run")
    assert screening.status_code == 401


def test_openapi_contract_pins_and_auth_parameter() -> None:
    """Operation ids stay stable; the five ops expose the auth header like the rest."""
    schema = app.openapi()
    paths = schema["paths"]

    expected_operations = {
        "/v1/orderbook/{symbol}": ("get", "get_orderbook_snapshot"),
        "/v1/orderbook/{symbol}/depth": ("get", "get_orderbook_depth_ladder"),
        "/v1/orderbook/{symbol}/metrics": ("get", "get_orderbook_metrics"),
        "/v1/orderbook/{symbol}/impact": ("post", "estimate_orderbook_execution_impact"),
        "/v1/orderbook/{symbol}/snapshots": ("post", "record_orderbook_snapshot_endpoint"),
    }
    for path, (method, operation_id) in expected_operations.items():
        operation = paths[path][method]
        assert operation["operationId"] == operation_id
        parameter_names = {param["name"] for param in operation.get("parameters", [])}
        # Same optional header parameter the other guarded /v1 routers expose.
        assert "authorization" in parameter_names, path

    # Pins used by tests/test_openapi_contract.py stay satisfied.
    assert {
        "/v1/backtest/run",
        "/v1/ai/chat",
        "/v1/screening/run",
        "/v1/screening/candidates",
        "/v1/portfolios/{portfolio_id}/sizing-proposals",
    } <= set(paths)
