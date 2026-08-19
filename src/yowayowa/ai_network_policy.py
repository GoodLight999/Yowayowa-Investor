from __future__ import annotations

from urllib.parse import urlsplit


HOSTED_AI_HOSTS = frozenset(
    {
        "api.anthropic.com",
        "api.cerebras.ai",
        "api.deepseek.com",
        "api.fireworks.ai",
        "api.groq.com",
        "api.mistral.ai",
        "api.openai.com",
        "api.perplexity.ai",
        "api.sambanova.ai",
        "api.together.xyz",
        "api.x.ai",
        "generativelanguage.googleapis.com",
        "integrate.api.nvidia.com",
        "openrouter.ai",
    }
)


def validate_ai_base_url(base_url: str, *, allow_unlisted: bool) -> str:
    """Validate an AI provider base URL before a server-side outbound request.

    Self-hosted personal installations may deliberately target local/LAN or custom
    endpoints. Hosted deployments default to a small HTTPS host allowlist so a
    per-request BYOK base URL cannot become a generic SSRF primitive.
    """

    normalized = base_url.strip().rstrip("/")
    parsed = urlsplit(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("AI provider base URL must be an absolute HTTP(S) URL")
    if allow_unlisted:
        return normalized
    if parsed.scheme != "https" or parsed.hostname.casefold() not in HOSTED_AI_HOSTS:
        raise ValueError(
            "This hosted Yowayowa deployment only allows registered cloud AI endpoints; "
            "run Yowayowa locally to use LAN, localhost, or custom provider URLs"
        )
    return normalized
