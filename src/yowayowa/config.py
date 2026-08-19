from __future__ import annotations

import os
from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_database_url() -> str:
    if database_url := os.getenv("DATABASE_URL"):
        return database_url
    if os.getenv("VERCEL"):
        return "sqlite:////tmp/yowayowa.db"
    return "sqlite:///./data/yowayowa.db"


def _default_cron_secret() -> str | None:
    return os.getenv("CRON_SECRET")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="YOWAYOWA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    mode: Literal["personal", "public"] = "personal"
    database_url: str = Field(default_factory=_default_database_url)
    sec_user_agent: str = "Yowayowa-Investor/0.1 admin@example.invalid"
    sec_requests_per_second: float = Field(default=8.0, gt=0, le=10)
    fred_api_key: str | None = None
    edinet_api_key: str | None = None
    bls_api_key: str | None = None
    bea_api_key: str | None = None
    estat_app_id: str | None = None
    market_provider: Literal["yahoo"] = "yahoo"
    request_timeout_seconds: float = Field(default=20.0, gt=0)
    cache_ttl_seconds: int = Field(default=900, ge=0)
    api_token: str | None = None
    cron_secret: str | None = Field(default_factory=_default_cron_secret)
    allow_personal_provider_in_public: bool = False
    local_enrichment_enabled: bool = False
    openai_compatible_base_url: str | None = None
    openai_compatible_api_key: str | None = None
    openai_compatible_model: str | None = None
    anthropic_api_key: str | None = None
    anthropic_model: str | None = None
    anthropic_base_url: str = "https://api.anthropic.com"

    @model_validator(mode="after")
    def enforce_public_safeguards(self) -> Settings:
        if self.mode == "public" and not self.api_token:
            raise ValueError("YOWAYOWA_API_TOKEN is required in public mode")
        if self.mode == "public" and self.allow_personal_provider_in_public:
            raise ValueError(
                "Personal-only provider overrides are forbidden in public mode; use personal mode"
            )
        if self.mode == "public" and self.local_enrichment_enabled:
            raise ValueError("Local enrichment is personal-mode only and cannot run in public mode")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
