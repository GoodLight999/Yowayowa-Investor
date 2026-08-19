from __future__ import annotations

from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from yowayowa.ai_network_policy import validate_ai_base_url
from yowayowa.api.deps import require_api_token
from yowayowa.config import Settings, get_settings
from yowayowa.research_models import AIProviderConfig

router = APIRouter(prefix="/v1/ai", dependencies=[Depends(require_api_token)])


class AIProviderCatalogItem(BaseModel):
    id: str
    label: str
    adapter: Literal["openai_compatible", "anthropic"]
    base_url: str
    auth_modes: list[Literal["api_key", "oauth_pkce", "none"]]
    model_discovery: bool = True
    category: Literal["cloud", "local", "custom"] = "cloud"
    docs_url: str | None = None
    note: str | None = None


class AIModelCatalogRequest(BaseModel):
    provider: AIProviderConfig
    limit: int = Field(default=500, ge=1, le=2000)


class AIModelCatalogResponse(BaseModel):
    models: list[str]
    endpoint: str


PROVIDERS: tuple[AIProviderCatalogItem, ...] = (
    AIProviderCatalogItem(
        id="openai",
        label="OpenAI",
        adapter="openai_compatible",
        base_url="https://api.openai.com/v1",
        auth_modes=["api_key"],
        docs_url="https://platform.openai.com/docs/api-reference",
    ),
    AIProviderCatalogItem(
        id="openrouter",
        label="OpenRouter",
        adapter="openai_compatible",
        base_url="https://openrouter.ai/api/v1",
        auth_modes=["api_key", "oauth_pkce"],
        docs_url="https://openrouter.ai/docs/guides/overview/auth/oauth",
        note="Official browser-friendly OAuth PKCE is supported by Yowayowa Settings.",
    ),
    AIProviderCatalogItem(
        id="anthropic",
        label="Anthropic",
        adapter="anthropic",
        base_url="https://api.anthropic.com",
        auth_modes=["api_key"],
        docs_url="https://docs.anthropic.com/en/api/overview",
    ),
    AIProviderCatalogItem(
        id="gemini",
        label="Google Gemini",
        adapter="openai_compatible",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        auth_modes=["api_key"],
        docs_url="https://ai.google.dev/gemini-api/docs/openai",
    ),
    AIProviderCatalogItem(
        id="xai",
        label="xAI",
        adapter="openai_compatible",
        base_url="https://api.x.ai/v1",
        auth_modes=["api_key"],
        docs_url="https://docs.x.ai/developers/rest-api-reference/inference",
    ),
    AIProviderCatalogItem(
        id="deepseek",
        label="DeepSeek",
        adapter="openai_compatible",
        base_url="https://api.deepseek.com",
        auth_modes=["api_key"],
        docs_url="https://api-docs.deepseek.com/",
    ),
    AIProviderCatalogItem(
        id="mistral",
        label="Mistral AI",
        adapter="openai_compatible",
        base_url="https://api.mistral.ai/v1",
        auth_modes=["api_key"],
        docs_url="https://docs.mistral.ai/api/",
    ),
    AIProviderCatalogItem(
        id="groq",
        label="Groq",
        adapter="openai_compatible",
        base_url="https://api.groq.com/openai/v1",
        auth_modes=["api_key"],
        docs_url="https://console.groq.com/docs/openai",
    ),
    AIProviderCatalogItem(
        id="together",
        label="Together AI",
        adapter="openai_compatible",
        base_url="https://api.together.xyz/v1",
        auth_modes=["api_key"],
        docs_url="https://docs.together.ai/docs/openai-api-compatibility",
    ),
    AIProviderCatalogItem(
        id="fireworks",
        label="Fireworks AI",
        adapter="openai_compatible",
        base_url="https://api.fireworks.ai/inference/v1",
        auth_modes=["api_key"],
        docs_url="https://docs.fireworks.ai/tools-sdks/openai-compatibility",
    ),
    AIProviderCatalogItem(
        id="cerebras",
        label="Cerebras",
        adapter="openai_compatible",
        base_url="https://api.cerebras.ai/v1",
        auth_modes=["api_key"],
        docs_url="https://inference-docs.cerebras.ai/api-reference/chat-completions",
    ),
    AIProviderCatalogItem(
        id="perplexity",
        label="Perplexity",
        adapter="openai_compatible",
        base_url="https://api.perplexity.ai",
        auth_modes=["api_key"],
        docs_url="https://docs.perplexity.ai/api-reference/chat-completions-post",
    ),
    AIProviderCatalogItem(
        id="nvidia",
        label="NVIDIA NIM",
        adapter="openai_compatible",
        base_url="https://integrate.api.nvidia.com/v1",
        auth_modes=["api_key"],
        docs_url="https://docs.api.nvidia.com/nim/reference/llm-apis",
    ),
    AIProviderCatalogItem(
        id="sambanova",
        label="SambaNova Cloud",
        adapter="openai_compatible",
        base_url="https://api.sambanova.ai/v1",
        auth_modes=["api_key"],
        docs_url="https://docs.sambanova.ai/cloud/docs/get-started/overview",
    ),
    AIProviderCatalogItem(
        id="ollama",
        label="Ollama",
        adapter="openai_compatible",
        base_url="http://127.0.0.1:11434/v1",
        auth_modes=["none"],
        category="local",
        docs_url="https://docs.ollama.com/openai",
        note=(
            "Local endpoint. A dummy API key can be used when a client requires a non-empty value."
        ),
    ),
    AIProviderCatalogItem(
        id="lmstudio",
        label="LM Studio",
        adapter="openai_compatible",
        base_url="http://127.0.0.1:1234/v1",
        auth_modes=["none"],
        category="local",
        docs_url="https://lmstudio.ai/docs/developer/openai-compat",
    ),
    AIProviderCatalogItem(
        id="vllm",
        label="vLLM",
        adapter="openai_compatible",
        base_url="http://127.0.0.1:8000/v1",
        auth_modes=["none"],
        category="local",
        docs_url="https://docs.vllm.ai/en/latest/serving/openai_compatible_server.html",
    ),
    AIProviderCatalogItem(
        id="custom",
        label="Custom OpenAI-compatible",
        adapter="openai_compatible",
        base_url="https://example.invalid/v1",
        auth_modes=["api_key"],
        category="custom",
        docs_url=None,
        note=(
            "Use any service that implements the chat/completions and models "
            "conventions required by Yowayowa."
        ),
    ),
)


@router.get("/providers", response_model=list[AIProviderCatalogItem])
def provider_catalog() -> list[AIProviderCatalogItem]:
    return list(PROVIDERS)


def _model_endpoint(config: AIProviderConfig) -> tuple[str, dict[str, str]]:
    base = (config.base_url or "").rstrip("/")
    if config.provider == "anthropic":
        if not base:
            base = "https://api.anthropic.com"
        endpoint = f"{base}/v1/models" if not base.endswith("/v1") else f"{base}/models"
        return endpoint, {
            "x-api-key": config.api_key,
            "anthropic-version": "2023-06-01",
        }
    if not base:
        base = "https://api.openai.com/v1"
    return f"{base}/models", {"Authorization": f"Bearer {config.api_key}"}


@router.post("/models", response_model=AIModelCatalogResponse)
def provider_models(
    payload: AIModelCatalogRequest,
    settings: Settings = Depends(get_settings),
) -> AIModelCatalogResponse:
    provider = payload.provider
    if provider.base_url:
        try:
            provider = provider.model_copy(
                update={
                    "base_url": validate_ai_base_url(
                        provider.base_url,
                        allow_unlisted=settings.allow_unlisted_ai_endpoints,
                    )
                }
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    endpoint, headers = _model_endpoint(provider)
    try:
        with httpx.Client(timeout=20, follow_redirects=False) as client:
            response = client.get(endpoint, headers=headers)
            response.raise_for_status()
            body = response.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Provider model discovery returned HTTP {exc.response.status_code}",
        ) from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="Provider model discovery failed") from exc

    raw = body.get("data", body.get("models", [])) if isinstance(body, dict) else []
    models: list[str] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                model_id = item
            elif isinstance(item, dict):
                model_id = str(item.get("id") or item.get("name") or "")
            else:
                continue
            if model_id and model_id not in models:
                models.append(model_id)
            if len(models) >= payload.limit:
                break
    models.sort(key=str.casefold)
    return AIModelCatalogResponse(models=models, endpoint=endpoint)
