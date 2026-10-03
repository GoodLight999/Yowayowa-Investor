from typing import Any, ClassVar

import httpx
import pytest

from yowayowa.acquisition.transport import (
    HttpxSessionTransport,
    PlaywrightSessionTransport,
    PrivateAcquisitionError,
    TransportResponse,
    record_exchange,
)
from yowayowa.private_connectors import PrivateAcquisitionMethod, PrivateConnectorDescriptor


def _descriptor() -> PrivateConnectorDescriptor:
    return PrivateConnectorDescriptor(
        id="fixture",
        provider="fixture-broker",
        method=PrivateAcquisitionMethod.PRIVATE_HTTP,
        parser_version="1",
    )


def test_httpx_transport_success_records_exchange_fields_without_query() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    transport = HttpxSessionTransport(
        descriptor=_descriptor(),
        base_url="https://broker.example/api/",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    response = transport.fetch("GET", "positions", params={"market": "jp"})
    assert response.status_code == 200
    assert response.url == "https://broker.example/api/positions"
    assert "?" not in response.url
    assert response.content_type is not None and "json" in response.content_type
    assert response.elapsed_ms >= 0

    exchange = record_exchange(
        "GET",
        response.url,
        response.status_code,
        content_type=response.content_type,
        size_bytes=len(response.content),
        duration_ms=response.elapsed_ms,
    )
    assert exchange.method == "GET"
    assert exchange.url == "https://broker.example/api/positions"
    assert exchange.size_bytes == len(response.content)
    assert exchange.status_code == 200


def test_httpx_transport_cross_origin_redirect_fails() -> None:
    transport = HttpxSessionTransport(
        descriptor=_descriptor(),
        base_url="https://broker.example/",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(302, headers={"location": "https://evil.example/login"})
            )
        ),
    )
    with pytest.raises(PrivateAcquisitionError) as excinfo:
        transport.fetch("GET", "orders")
    assert excinfo.value.state.value == "failed"
    assert "cross-origin" in excinfo.value.reason


def test_httpx_transport_oversize_content_length_fails() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-length": str(20_000_000)}, content=b"{}")

    transport = HttpxSessionTransport(
        descriptor=_descriptor(),
        base_url="https://broker.example/",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(PrivateAcquisitionError, match="too large"):
        transport.fetch("GET", "big")


def test_playwright_transport_rejects_absolute_resource_paths() -> None:
    class StubSession:
        def request(self, *_: object, **__: object) -> object:
            raise AssertionError("must not be called for absolute paths")

    transport = PlaywrightSessionTransport(session=StubSession())
    with pytest.raises(PrivateAcquisitionError, match="relative"):
        transport.fetch("GET", "https://evil.example/steal")


def test_playwright_transport_normalizes_url_and_measures_elapsed() -> None:
    class StubResponse:
        url = "https://broker.example/positions?session=secret"
        status = 200
        headers: ClassVar[dict[str, str]] = {"content-type": "application/json"}

        def body(self) -> bytes:
            return b'{"rows": []}'

    class StubSession:
        def request(self, method: str, path: str, **kwargs: object) -> StubResponse:
            assert method.upper() == "GET"
            assert path == "positions"
            assert "headers" not in kwargs or kwargs.get("headers") is None
            return StubResponse()

    transport = PlaywrightSessionTransport(session=StubSession())
    response = transport.fetch("GET", "positions")
    assert isinstance(response, TransportResponse)
    assert response.url == "https://broker.example/positions"
    assert "?" not in response.url
    assert response.status_code == 200
    assert response.content_type == "application/json"
    assert response.elapsed_ms >= 0
    assert b"rows" in response.content


def test_playwright_transport_wraps_session_errors() -> None:
    from yowayowa.operator_bridge.web_session import BrokerWebSessionError

    class BrokenSession:
        def request(self, *_: Any, **__: Any) -> object:
            raise BrokerWebSessionError("session not started")

    transport = PlaywrightSessionTransport(session=BrokenSession())
    with pytest.raises(PrivateAcquisitionError, match="session not started"):
        transport.fetch("GET", "positions")
