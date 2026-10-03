from __future__ import annotations

import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

from yowayowa.acquisition.models import AcquisitionFetchState, NetworkExchange
from yowayowa.private_connectors import (
    PrivateAcquisitionMethod,
    PrivateConnectorDescriptor,
)


class PrivateAcquisitionError(RuntimeError):
    """Acquisition failure with an explicit machine-readable state."""

    def __init__(self, state: AcquisitionFetchState, reason: str) -> None:
        super().__init__(reason)
        self.state = state
        self.reason = reason


class TransportResponse:
    """Transport-normalized HTTP response; url is the final URL without query."""

    def __init__(
        self,
        *,
        status_code: int,
        url: str,
        content_type: str | None,
        text: str,
        content: bytes,
        elapsed_ms: float,
    ) -> None:
        self.status_code = status_code
        self.url = url
        self.content_type = content_type
        self.text = text
        self.content = content
        self.elapsed_ms = elapsed_ms


class SessionTransport(Protocol):
    def fetch(
        self,
        method: str,
        resource: str,
        *,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        data: object | None = None,
    ) -> TransportResponse: ...


def provenance_url(url: str) -> str:
    parts = urlsplit(url)
    return parts._replace(query="", fragment="").geturl()


def record_exchange(
    method: str,
    url: str,
    status_code: int | None,
    *,
    content_type: str | None,
    size_bytes: int | None,
    duration_ms: float | None,
    occurred_at: datetime | None = None,
) -> NetworkExchange:
    """Build the provenance-safe exchange record. Never records headers."""
    return NetworkExchange(
        method=method.upper(),
        url=provenance_url(url),
        status_code=status_code,
        content_type=content_type,
        size_bytes=size_bytes,
        duration_ms=duration_ms,
        occurred_at=occurred_at or datetime.now(UTC),
    )


class HttpxSessionTransport:
    """Same-origin private HTTP transport on top of AuthenticatedPrivateHttpClient."""

    def __init__(
        self,
        *,
        descriptor: PrivateConnectorDescriptor,
        base_url: str,
        client: httpx.Client,
        max_response_bytes: int = 10_000_000,
    ) -> None:
        # Imported here to keep this module importable without the private_http
        # module's heavier dependency closure being required at import time.
        from yowayowa.services.private_http import AuthenticatedPrivateHttpClient

        self._client = AuthenticatedPrivateHttpClient(
            descriptor=descriptor,
            base_url=base_url,
            client=client,
            max_response_bytes=max_response_bytes,
        )

    def fetch(
        self,
        method: str,
        resource: str,
        *,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        data: object | None = None,
    ) -> TransportResponse:
        started = time.perf_counter()
        try:
            response = self._client._request(
                method,
                resource,
                params=params,
                json_body=data if isinstance(data, (dict, list)) else None,
                headers=headers,
            )
        except httpx.HTTPError as exc:
            elapsed = (time.perf_counter() - started) * 1000
            raise PrivateAcquisitionError(
                AcquisitionFetchState.FAILED,
                f"transport error: {type(exc).__name__}",
            ) from exc
        except ValueError as exc:
            raise PrivateAcquisitionError(
                AcquisitionFetchState.FAILED, f"invalid request: {exc}"
            ) from exc
        except RuntimeError as exc:  # PrivateProtocolViolation
            raise PrivateAcquisitionError(AcquisitionFetchState.FAILED, str(exc)) from exc
        elapsed = (time.perf_counter() - started) * 1000
        url = str(response.request.url)
        return TransportResponse(
            status_code=response.status_code,
            url=provenance_url(url),
            content_type=response.headers.get("content-type"),
            text=response.text,
            content=response.content,
            elapsed_ms=elapsed,
        )


class PlaywrightSessionTransport:
    """Transport over a PersistentBrokerWebSession (operator browser session).

    The web_session module imports playwright at module top level, so it is
    imported lazily here (inside __init__), never at this module's import time.
    """

    def __init__(self, session: Any) -> None:
        from yowayowa.operator_bridge.web_session import BrokerWebSessionError

        self._session = session
        self._error_type: type[Exception] = BrokerWebSessionError

    def fetch(
        self,
        method: str,
        resource: str,
        *,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        data: object | None = None,
    ) -> TransportResponse:
        if urlsplit(resource).scheme or urlsplit(resource).netloc:
            raise PrivateAcquisitionError(
                AcquisitionFetchState.FAILED,
                "browser-session resources must be relative paths",
            )
        if params is not None and any(
            value is None or not isinstance(value, str) for value in params.values()
        ):
            raise PrivateAcquisitionError(
                AcquisitionFetchState.FAILED,
                "browser-session params must be plain strings",
            )
        started = time.perf_counter()
        try:
            response = self._session.request(
                method,
                resource,
                headers=dict(headers) if headers else None,
                params=dict(params) if params else None,
                data=data,
            )
        except self._error_type as exc:
            raise PrivateAcquisitionError(AcquisitionFetchState.FAILED, str(exc)) from exc
        elapsed = (time.perf_counter() - started) * 1000
        try:
            body = response.body()
        except Exception as exc:  # pragma: no cover - transport-specific
            raise PrivateAcquisitionError(
                AcquisitionFetchState.FAILED,
                f"failed to read browser-session body: {type(exc).__name__}",
            ) from exc
        url = response.url
        status = response.status
        headers_map: dict[str, str] = dict(response.headers)
        content_type = headers_map.get("content-type") or headers_map.get("Content-Type")
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            text = body.decode("utf-8", errors="replace")
        return TransportResponse(
            status_code=status,
            url=provenance_url(url),
            content_type=content_type,
            text=text,
            content=body,
            elapsed_ms=elapsed,
        )


HTTP_METHODS = frozenset(
    {
        PrivateAcquisitionMethod.PRIVATE_HTTP,
        PrivateAcquisitionMethod.STRUCTURED_SCRAPE,
        PrivateAcquisitionMethod.HTML_SCRAPE,
    }
)
