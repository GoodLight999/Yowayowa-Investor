from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, Field

from yowayowa.domain import LicenseClass


class SourceAccess(StrEnum):
    PUBLIC = "public"
    REGISTERED_KEY = "registered_key"
    CONTRACT = "contract"
    LOCAL_PERSONAL = "local_personal"


class SourceLicensePolicy(BaseModel):
    key: str
    source: str
    provider_patterns: list[str]
    license_class: LicenseClass
    access: SourceAccess
    commercial_use: bool
    public_display: bool
    public_api: bool
    derived_analysis_public: bool
    attribution_required: bool = False
    attribution: str | None = None
    terms_url: str
    reviewed_on: date
    notes: list[str] = Field(default_factory=list)


class LicenseCatalog(BaseModel):
    mode: str
    sources: list[SourceLicensePolicy]
    public_safe_sources: list[str]
    blocked_sources: list[str]
