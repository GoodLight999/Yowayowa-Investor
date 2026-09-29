from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

import pytest
from playwright.sync_api import Page, Route


@pytest.fixture
def app_server(tmp_path: Path) -> Iterator[str]:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    base_url = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env.update(
        {
            "YOWAYOWA_MODE": "personal",
            "YOWAYOWA_PRIVATE_CONNECTORS_ENABLED": "true",
            "YOWAYOWA_PRIVATE_ACQUISITION_DATA_DIR": str(tmp_path / "private-data"),
            "YOWAYOWA_BROKER_EXECUTION_AUDIT_DIR": str(tmp_path / "audit"),
            "YOWAYOWA_BROKER_RAKUTEN_WEB_PROFILE_DIR": str(tmp_path / "browser-profile"),
            "DATABASE_URL": f"sqlite:///{tmp_path / 'browser.sqlite'}",
        }
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "yowayowa.api.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if process.poll() is not None:
                pytest.fail("uvicorn exited before the browser test server became ready")
            try:
                with urlopen(f"{base_url}/v1/health", timeout=1):
                    break
            except (OSError, URLError):
                time.sleep(0.1)
        else:
            pytest.fail("uvicorn did not become ready for the browser test")
        yield base_url
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def _install_api_stubs(
    page: Page, response_map: Mapping[str, tuple[int, Any]]
) -> list[dict[str, str]]:
    calls: list[dict[str, str]] = []

    def route_api(route: Route) -> None:
        request = route.request
        path = request.url.split("/v1/", 1)[-1].split("?", 1)[0]
        calls.append({"method": request.method, "path": path})
        if path == "health":
            route.fulfill(status=200, content_type="application/json", body='{"mode":"personal"}')
            return
        if path == "alert-inbox":
            route.fulfill(status=200, content_type="application/json", body="[]")
            return
        status, payload = response_map.get(path, (200, {}))
        route.fulfill(status=status, content_type="application/json", body=json.dumps(payload))

    page.route("**/v1/**", route_api)
    page.route("https://unpkg.com/**", lambda route: route.abort())
    return calls


def _audit_payload() -> dict[str, Any]:
    return {
        "intact": True,
        "total_entries": 2,
        "verify_problems": [],
        "entries": [
            {
                "seq": 1,
                "ts": "2026-09-29T03:00:00+00:00",
                "kind": "intent",
                "client_order_id": "browser-proposal-1",
                "payload": {
                    "proposal": {
                        "client_order_id": "browser-proposal-1",
                        "symbol": "AAPL",
                        "market": "us",
                        "side": "buy",
                        "quantity": 2,
                        "order_type": "limit",
                        "limit_price": "190.25",
                        "reference_price": None,
                        "currency": "USD",
                        "motivation": "Test proposal",
                    }
                },
            },
            {
                "seq": 2,
                "ts": "2026-09-29T03:01:00+00:00",
                "kind": "state",
                "client_order_id": "browser-proposal-1",
                "payload": {
                    "stage": "evaluate",
                    "allowed": False,
                    "armed": False,
                    "reasons": ["gate closed"],
                },
            },
        ],
    }


def _orders_payload(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "broker": "rakuten-securities",
        "market": "us",
        "generated_at": "2026-09-29T03:02:00+00:00",
        "fetch_state_open": "ok",
        "fetch_state_history": "ok",
        "auth_state": "authenticated",
        "items": items,
        "notes": [],
        "intact_audit": True,
        "source_urls": ["https://broker.example/orders"],
    }


def _fills_payload(executions: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "connector_id": "rakuten-web",
        "resource": "executions",
        "market": "us",
        "fetch_state": "ok",
        "auth_state": "authenticated",
        "executions": executions,
        "detail": {"verified": True},
        "source_url": "https://broker.example/executions",
        "retrieved_at": "2026-09-29T03:02:00+00:00",
        "as_of": "2026-09-29T03:00:00+00:00",
        "parser_version": "fake-v1",
        "schema_version": "fake-v1",
        "snapshot": {"snapshot_id": "snapshot-1", "payload_sha256": "a" * 64},
    }


def test_order_proposal_and_fill_rows_render_in_real_browser(page: Page, app_server: str) -> None:
    response_map = {
        "broker-execution/audit": (200, _audit_payload()),
        "broker-execution/orders": (
            200,
            _orders_payload(
                [
                    {
                        "order": {
                            "broker": "rakuten-securities",
                            "broker_order_id": "broker-order-1",
                            "symbol": "AAPL",
                            "side": "buy",
                            "quantity": 2,
                            "status": "accepted",
                        },
                        "match": "audit_matched",
                        "client_order_id": "browser-proposal-1",
                        "proposal_hash": "b" * 64,
                    }
                ]
            ),
        ),
        "broker-execution/executions": (
            200,
            _fills_payload(
                [
                    {
                        "broker": "rakuten-securities",
                        "execution_id": "fill-1",
                        "broker_order_id": "broker-order-1",
                        "symbol": "AAPL",
                        "side": "buy",
                        "quantity": "2",
                        "price": "190.25",
                        "currency": "USD",
                        "executed_at": "2026-09-29T03:00:00+00:00",
                    }
                ]
            ),
        ),
    }
    calls = _install_api_stubs(page, response_map)
    page.goto(f"{app_server}/broker-execution?lang=en", wait_until="domcontentloaded")

    page.get_by_role("heading", name="Orders & fills").wait_for()
    page.locator("#fills-table").get_by_text("AAPL", exact=True).wait_for()
    page.get_by_text("190.25", exact=True).wait_for()
    page.get_by_text("Gate evaluation: blocked").wait_for()
    page.get_by_text("Audit matched").wait_for()
    broker_calls = [call for call in calls if call["path"].startswith("broker-execution/")]
    assert {call["method"] for call in broker_calls} == {"GET"}
    assert {call["path"] for call in broker_calls} == {
        "broker-execution/audit",
        "broker-execution/orders",
        "broker-execution/executions",
    }


def test_empty_broker_read_shows_distinct_empty_states(page: Page, app_server: str) -> None:
    response_map = {
        "broker-execution/audit": (
            200,
            {"intact": True, "total_entries": 0, "verify_problems": [], "entries": []},
        ),
        "broker-execution/orders": (200, _orders_payload([])),
        "broker-execution/executions": (200, _fills_payload([])),
    }
    _install_api_stubs(page, response_map)
    page.goto(f"{app_server}/broker-execution?lang=en", wait_until="domcontentloaded")

    page.get_by_text("No proposal records.").wait_for()
    page.get_by_text("No order rows were returned in this read.").wait_for()
    page.get_by_text(
        "No fill rows were returned in this read. Feed coverage is unconfirmed."
    ).wait_for()


def test_broker_api_errors_are_visible_without_fake_empty_state(
    page: Page, app_server: str
) -> None:
    response_map = {
        "broker-execution/audit": (503, {"detail": "unavailable"}),
        "broker-execution/orders": (503, {"detail": "unavailable"}),
        "broker-execution/executions": (503, {"detail": "unavailable"}),
    }
    _install_api_stubs(page, response_map)
    page.goto(f"{app_server}/broker-execution?lang=en", wait_until="domcontentloaded")

    errors = page.locator(".broker-error")
    errors.first.wait_for()
    assert errors.count() == 3
    assert page.get_by_text("The service is temporarily unavailable.").count() == 3
    assert page.get_by_text("No order rows were returned in this read.").count() == 0
    assert (
        page.get_by_text(
            "No fill rows were returned in this read. Feed coverage is unconfirmed."
        ).count()
        == 0
    )
