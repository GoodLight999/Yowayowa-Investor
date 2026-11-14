from __future__ import annotations

import pytest
from starlette.requests import Request

from yowayowa.api.deps import request_data_source_settings
from yowayowa.config import Settings


@pytest.mark.parametrize(
    ("source", "header", "field"),
    [
        ("edinet", "x-yowayowa-edinet-key", "edinet_api_key"),
        ("estat", "x-yowayowa-estat-key", "estat_app_id"),
        ("fred", "x-yowayowa-fred-key", "fred_api_key"),
        ("bea", "x-yowayowa-bea-key", "bea_api_key"),
        ("bls", "x-yowayowa-bls-key", "bls_api_key"),
    ],
)
def test_browser_data_source_key_is_request_scoped(
    source: str,
    header: str,
    field: str,
) -> None:
    settings = Settings()
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/v1/test",
            "headers": [(header.encode(), b"browser-secret")],
        }
    )

    scoped = request_data_source_settings(request, settings, source)  # type: ignore[arg-type]

    assert getattr(settings, field) is None
    assert getattr(scoped, field) == "browser-secret"
    assert scoped is not settings


def test_missing_browser_data_source_key_reuses_server_settings() -> None:
    settings = Settings(fred_api_key="server-key")
    request = Request({"type": "http", "method": "GET", "path": "/v1/test", "headers": []})

    scoped = request_data_source_settings(request, settings, "fred")

    assert scoped is settings
    assert scoped.fred_api_key == "server-key"
