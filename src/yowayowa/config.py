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


def _default_codex_cli_enabled() -> bool:
    return not bool(os.getenv("VERCEL"))


def _default_codex_bridge_url() -> str | None:
    return os.getenv("CODEX_BRIDGE_URL")


def _default_full_operator_capability() -> bool:
    return not bool(os.getenv("VERCEL"))


def _default_allow_unlisted_ai_endpoints() -> bool:
    # Local/self-hosted personal installs intentionally support Ollama, LM Studio,
    # vLLM and custom gateways. Hosted deployments must opt in explicitly rather
    # than turning a user-supplied base URL into a generic server-side fetch target.
    return not bool(os.getenv("VERCEL"))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="YOWAYOWA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    mode: Literal["personal", "public"] = "personal"
    database_url: str = Field(default_factory=_default_database_url)
    private_acquisition_data_dir: str = "./data/private-acquisition"
    broker_rakuten_web_profile_dir: str = "./data/broker-profiles/rakuten"
    broker_session_notify_state_path: str = "./data/broker-session/notify-state.json"
    broker_rakuten_web_user_agent: str | None = None
    sec_user_agent: str = "Yowayowa-Investor/0.1 admin@example.invalid"
    sec_requests_per_second: float = Field(default=8.0, gt=0, le=10)
    fred_api_key: str | None = None
    edinet_api_key: str | None = None
    edinet_index_lookback_days: int = Field(default=550, ge=31, le=3660)
    edinet_index_backfill_days_per_run: int = Field(default=31, ge=1, le=31)
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
    private_connectors_enabled: bool = Field(default_factory=_default_full_operator_capability)
    scraping_enabled: bool = Field(default_factory=_default_full_operator_capability)
    broker_control_enabled: bool = Field(default_factory=_default_full_operator_capability)
    broker_live_orders_enabled: bool = False
    broker_risk_currency: str = Field(default="JPY", min_length=3, max_length=3)
    broker_max_single_order_notional: float | None = Field(default=None, gt=0)
    broker_max_orders_per_day: int | None = Field(default=None, ge=1, le=10000)
    broker_execution_audit_dir: str = "./data/broker-execution/audit"
    allow_unlisted_ai_endpoints: bool = Field(default_factory=_default_allow_unlisted_ai_endpoints)
    codex_cli_enabled: bool = Field(default_factory=_default_codex_cli_enabled)
    codex_bridge_url: str | None = Field(default_factory=_default_codex_bridge_url)
    codex_cli_timeout_seconds: float = Field(default=180.0, ge=10, le=900)
    openai_compatible_base_url: str | None = None
    openai_compatible_api_key: str | None = None
    openai_compatible_model: str | None = None
    anthropic_api_key: str | None = None
    anthropic_model: str | None = None
    anthropic_base_url: str = "https://api.anthropic.com"
    # Authorized private mailbox access (P1D): the operator's own mail account,
    # read through the read-only `gog` CLI. The keyring password file is a
    # local path only; its value is never read into settings, logged, or
    # returned by any surface.
    mailbox_command: str = "gog"
    mailbox_keyring_password_file: str | None = None

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
        if self.mode == "public":
            self.private_connectors_enabled = False
            self.scraping_enabled = False
            self.broker_control_enabled = False
            self.broker_live_orders_enabled = False
            self.allow_unlisted_ai_endpoints = False
            self.codex_cli_enabled = False
        if self.broker_live_orders_enabled:
            if self.mode != "personal" or not self.broker_control_enabled:
                raise ValueError(
                    "Live broker orders require personal mode with broker control enabled"
                )
            if self.broker_max_single_order_notional is None:
                raise ValueError(
                    "YOWAYOWA_BROKER_MAX_SINGLE_ORDER_NOTIONAL is required for live broker orders"
                )
            if self.broker_max_orders_per_day is None:
                raise ValueError(
                    "YOWAYOWA_BROKER_MAX_ORDERS_PER_DAY is required for live broker orders"
                )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
