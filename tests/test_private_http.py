import httpx
import pytest

from yowayowa.private_connectors import (
    PrivateAcquisitionMethod,
    PrivateConnectorDescriptor,
)
from yowayowa.services.private_http import (
    AuthenticatedPrivateHttpClient,
    PrivateProtocolViolation,
)


def _descriptor(method: PrivateAcquisitionMethod) -> PrivateConnectorDescriptor:
    return PrivateConnectorDescriptor(
        id="fixture",
        provider="fixture-broker",
        method=method,
        authenticated=True,
        redistributable=False,
        parser_version="1",
    )


def test_private_http_uses_injected_authenticated_session_and_same_origin() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-session"] == "authorized"
        return httpx.Response(200, json={"cash": 123})

    raw = httpx.Client(
        transport=httpx.MockTransport(handler),
        headers={"x-session": "authorized"},
    )
    connector = AuthenticatedPrivateHttpClient(
        descriptor=_descriptor(PrivateAcquisitionMethod.PRIVATE_HTTP),
        base_url="https://broker.example/account/",
        client=raw,
    )

    result = connector.get_json("positions", params={"market": "jp"})

    assert result.payload == {"cash": 123}
    assert result.source_url == "https://broker.example/account/positions"


def test_private_http_rejects_absolute_cross_origin_path() -> None:
    connector = AuthenticatedPrivateHttpClient(
        descriptor=_descriptor(PrivateAcquisitionMethod.PRIVATE_HTTP),
        base_url="https://broker.example/",
        client=httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200))),
    )

    with pytest.raises(PrivateProtocolViolation, match="relative"):
        connector.get_json("https://evil.example/steal")


def test_private_http_rejects_cross_origin_redirect() -> None:
    connector = AuthenticatedPrivateHttpClient(
        descriptor=_descriptor(PrivateAcquisitionMethod.PRIVATE_HTTP),
        base_url="https://broker.example/",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    302,
                    headers={"location": "https://evil.example/login"},
                )
            )
        ),
    )

    with pytest.raises(PrivateProtocolViolation, match="cross-origin"):
        connector.get_json("orders")


def test_authenticated_html_scraper_returns_only_structured_parser_output() -> None:
    raw = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, text="<span id='cash'>123</span>")
        )
    )
    connector = AuthenticatedPrivateHttpClient(
        descriptor=_descriptor(PrivateAcquisitionMethod.HTML_SCRAPE),
        base_url="https://broker.example/",
        client=raw,
    )

    result = connector.scrape_html(
        "account",
        lambda html: {"cash": 123 if "cash" in html else None},
    )

    assert result.payload == {"cash": 123}
    assert "html" not in result.payload
