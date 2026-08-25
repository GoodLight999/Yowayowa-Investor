from __future__ import annotations

from typing import Any
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from yowayowa.ai_network_policy import validate_ai_base_url
from yowayowa.api import ai_integration_routes, ai_routes
from yowayowa.api.ai_integration_routes import (
    AIModelCatalogRequest,
    provider_catalog,
    provider_models,
)
from yowayowa.config import Settings
from yowayowa.research_models import AIChatRequest, AIMessage, AIProviderConfig


def test_ai_provider_catalog_exposes_shared_and_local_adapters() -> None:
    catalog = {item.id: item for item in provider_catalog()}

    assert catalog["openrouter"].adapter == "openai_compatible"
    assert "oauth_pkce" in catalog["openrouter"].auth_modes
    assert catalog["anthropic"].adapter == "anthropic"
    assert catalog["ollama"].category == "local"
    assert "none" in catalog["ollama"].auth_modes
    assert catalog["custom"].category == "custom"


def test_hosted_ai_policy_allows_catalogued_cloud_and_rejects_local_or_custom() -> None:
    assert (
        validate_ai_base_url("https://openrouter.ai/api/v1/", allow_unlisted=False)
        == "https://openrouter.ai/api/v1"
    )
    with pytest.raises(ValueError, match="registered cloud AI endpoints"):
        validate_ai_base_url("http://127.0.0.1:11434/v1", allow_unlisted=False)
    with pytest.raises(ValueError, match="registered cloud AI endpoints"):
        validate_ai_base_url("https://private-gateway.example/v1", allow_unlisted=False)


def test_self_host_ai_policy_allows_local_and_custom_endpoints() -> None:
    assert (
        validate_ai_base_url("http://127.0.0.1:11434/v1/", allow_unlisted=True)
        == "http://127.0.0.1:11434/v1"
    )
    assert (
        validate_ai_base_url("https://private-gateway.example/v1", allow_unlisted=True)
        == "https://private-gateway.example/v1"
    )


class FakeResponse:
    status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return {"data": [{"id": "zeta"}, {"id": "alpha"}, {"id": "alpha"}]}


class FakeClient:
    last_url: str | None = None
    last_headers: dict[str, str] | None = None

    def __init__(self, **_: object) -> None:
        pass

    def __enter__(self) -> FakeClient:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def get(self, url: str, *, headers: dict[str, str]) -> FakeResponse:
        type(self).last_url = url
        type(self).last_headers = headers
        return FakeResponse()


def test_model_discovery_uses_provider_models_endpoint_without_persisting_key(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(ai_integration_routes.httpx, "Client", FakeClient)
    request = AIModelCatalogRequest(
        provider=AIProviderConfig(
            provider="openai_compatible",
            model="placeholder",
            api_key="secret-test-key",
            base_url="https://provider.example/v1",
        ),
        limit=50,
    )

    result = provider_models(
        request,
        Settings(database_url="sqlite:///:memory:", allow_unlisted_ai_endpoints=True),
    )

    assert result.models == ["alpha", "zeta"]
    assert result.endpoint == "https://provider.example/v1/models"
    assert FakeClient.last_url == result.endpoint
    assert FakeClient.last_headers == {"Authorization": "Bearer secret-test-key"}
    assert "secret-test-key" not in result.model_dump_json()


def test_model_discovery_rejects_unlisted_endpoint_before_http(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def fail_client(**_: object) -> None:
        raise AssertionError("HTTP must not be attempted for a rejected endpoint")

    monkeypatch.setattr(ai_integration_routes.httpx, "Client", fail_client)
    request = AIModelCatalogRequest(
        provider=AIProviderConfig(
            provider="openai_compatible",
            model="placeholder",
            api_key="secret-test-key",
            base_url="http://127.0.0.1:11434/v1",
        )
    )

    with pytest.raises(HTTPException) as exc:
        provider_models(
            request,
            Settings(database_url="sqlite:///:memory:", allow_unlisted_ai_endpoints=False),
        )
    assert exc.value.status_code == 422


def test_ai_chat_rejects_unlisted_endpoint_before_agent(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    class ForbiddenAgent:
        def __init__(self, *_: object, **__: object) -> None:
            raise AssertionError("agent must not be constructed for a rejected endpoint")

    monkeypatch.setattr(ai_routes, "InvestmentResearchAgent", ForbiddenAgent)
    request = AIChatRequest(
        messages=[AIMessage(role="user", content="hello")],
        provider=AIProviderConfig(
            provider="openai_compatible",
            model="local-model",
            api_key="local",
            base_url="http://127.0.0.1:11434/v1",
        ),
    )

    with pytest.raises(HTTPException) as exc:
        ai_routes.ai_chat(
            request,
            Settings(database_url="sqlite:///:memory:", allow_unlisted_ai_endpoints=False),
            Mock(),
        )
    assert exc.value.status_code == 422
