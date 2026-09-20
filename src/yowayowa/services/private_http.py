from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from yowayowa.private_connectors import (
    PrivateAcquisitionMethod,
    PrivateConnectorDescriptor,
    PrivateConnectorResult,
)


class PrivateProtocolViolation(RuntimeError):
    pass


def _provenance_url(url: httpx.URL) -> str:
    return str(url.copy_with(query=None, fragment=None))


def _same_origin(base_url: str, candidate: str) -> bool:
    base = urlsplit(base_url)
    target = urlsplit(candidate)
    return (
        base.scheme.lower(),
        base.hostname,
        base.port,
    ) == (
        target.scheme.lower(),
        target.hostname,
        target.port,
    )


class AuthenticatedPrivateHttpClient:
    """Same-origin client for an operator's legitimately authenticated session."""

    def __init__(
        self,
        *,
        descriptor: PrivateConnectorDescriptor,
        base_url: str,
        client: httpx.Client,
        max_response_bytes: int = 10_000_000,
    ) -> None:
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname:
            raise ValueError("base_url must be an absolute HTTP(S) origin")
        if descriptor.method not in {
            PrivateAcquisitionMethod.PRIVATE_HTTP,
            PrivateAcquisitionMethod.STRUCTURED_SCRAPE,
            PrivateAcquisitionMethod.HTML_SCRAPE,
        }:
            raise ValueError("descriptor method is not HTTP based")
        self.descriptor = descriptor
        self.base_url = base_url.rstrip("/") + "/"
        self.client = client
        self.max_response_bytes = max_response_bytes

    def _url(self, path: str) -> str:
        candidate = urlsplit(path)
        if candidate.scheme or candidate.netloc:
            raise PrivateProtocolViolation("private connector paths must be relative")
        url = urljoin(self.base_url, path.lstrip("/"))
        if not _same_origin(self.base_url, url):
            raise PrivateProtocolViolation("private connector request escaped configured origin")
        return url

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, object] | None = None,
        json_body: object | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        response = self.client.request(
            method,
            self._url(path),
            params=params,
            json=json_body,
            headers=headers,
            follow_redirects=False,
        )
        if response.is_redirect:
            location = response.headers.get("location")
            if location:
                target = urljoin(str(response.request.url), location)
                if not _same_origin(self.base_url, target):
                    raise PrivateProtocolViolation(
                        "private connector refused a cross-origin redirect"
                    )
            raise PrivateProtocolViolation(
                "private connector does not follow redirects automatically"
            )
        response.raise_for_status()
        content_length = response.headers.get("content-length")
        if content_length is not None:
            try:
                declared = int(content_length)
            except ValueError:
                declared = 0
            if declared > self.max_response_bytes:
                raise PrivateProtocolViolation("private connector response is too large")
        if len(response.content) > self.max_response_bytes:
            raise PrivateProtocolViolation("private connector response is too large")
        return response

    def get_json(
        self,
        path: str,
        *,
        params: Mapping[str, object] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> PrivateConnectorResult:
        response = self._request("GET", path, params=params, headers=headers)
        payload = response.json()
        if not isinstance(payload, dict):
            raise PrivateProtocolViolation("expected a JSON object response")
        return PrivateConnectorResult(
            descriptor=self.descriptor,
            source_url=_provenance_url(response.request.url),
            retrieved_at=datetime.now(UTC),
            payload=payload,
        )

    def post_json(
        self,
        path: str,
        *,
        json_body: object,
        headers: Mapping[str, str] | None = None,
    ) -> PrivateConnectorResult:
        response = self._request("POST", path, json_body=json_body, headers=headers)
        payload = response.json()
        if not isinstance(payload, dict):
            raise PrivateProtocolViolation("expected a JSON object response")
        return PrivateConnectorResult(
            descriptor=self.descriptor,
            source_url=_provenance_url(response.request.url),
            retrieved_at=datetime.now(UTC),
            payload=payload,
        )

    def scrape_html(
        self,
        path: str,
        parser: Callable[[str], dict[str, Any]],
        *,
        params: Mapping[str, object] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> PrivateConnectorResult:
        response = self._request("GET", path, params=params, headers=headers)
        payload = parser(response.text)
        return PrivateConnectorResult(
            descriptor=self.descriptor,
            source_url=_provenance_url(response.request.url),
            retrieved_at=datetime.now(UTC),
            payload=payload,
        )
