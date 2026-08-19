from __future__ import annotations

from datetime import date

from yowayowa.domain import LicenseClass
from yowayowa.license_models import (
    LicenseCatalog,
    SourceAccess,
    SourceLicensePolicy,
)

SOURCE_POLICIES: tuple[SourceLicensePolicy, ...] = (
    SourceLicensePolicy(
        key="sec-edgar",
        source="U.S. Securities and Exchange Commission EDGAR",
        provider_patterns=["sec", "sec-edgar"],
        license_class=LicenseClass.OFFICIAL_PUBLIC,
        access=SourceAccess.PUBLIC,
        commercial_use=True,
        public_display=True,
        public_api=True,
        derived_analysis_public=True,
        attribution_required=False,
        attribution="Source: U.S. Securities and Exchange Commission EDGAR",
        terms_url="https://www.sec.gov/about/webmaster-frequently-asked-questions",
        reviewed_on=date(2026, 8, 18),
        notes=[
            "SEC states that Government-created sec.gov content and EDGAR public filing content "
            "are free to access and reuse.",
            "Respect SEC fair-access automation limits and identify automated clients.",
            "SEC seals, logos and trademarks are not covered by the data reuse permission.",
        ],
    ),
    SourceLicensePolicy(
        key="edinet",
        source="Financial Services Agency EDINET",
        provider_patterns=["edinet"],
        license_class=LicenseClass.OFFICIAL_PUBLIC,
        access=SourceAccess.REGISTERED_KEY,
        commercial_use=True,
        public_display=True,
        public_api=True,
        derived_analysis_public=True,
        attribution_required=True,
        attribution="Source: EDINET; processed by Yowayowa-Investor",
        terms_url="https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/WZEK0030.html",
        reviewed_on=date(2026, 8, 18),
        notes=[
            "EDINET content is available under Japan's Public Data License 1.0 subject to the "
            "site-specific terms and source attribution.",
            "When content is edited or transformed, identify that transformation and its author.",
            "EDINET taxonomy, logos and specifically excluded tools/assets are not covered by the "
            "general content permission.",
            "Use EDINET API for machine retrieval when the API provides the requested content.",
        ],
    ),
    SourceLicensePolicy(
        key="bls",
        source="U.S. Bureau of Labor Statistics",
        provider_patterns=["bls"],
        license_class=LicenseClass.OFFICIAL_PUBLIC,
        access=SourceAccess.PUBLIC,
        commercial_use=True,
        public_display=True,
        public_api=True,
        derived_analysis_public=True,
        attribution_required=True,
        attribution="Source: U.S. Bureau of Labor Statistics",
        terms_url="https://www.bls.gov/developers/termsOfService.htm",
        reviewed_on=date(2026, 8, 18),
        notes=[
            "BLS states that its published material is public domain except identified third-party "
            "photographs and illustrations.",
            "API-derived products should cite retrieval time and must not imply BLS endorsement.",
            "Yowayowa must identify its own transformations and cannot claim BLS vouches for them.",
        ],
    ),
    SourceLicensePolicy(
        key="bea",
        source="U.S. Bureau of Economic Analysis",
        provider_patterns=["bea"],
        license_class=LicenseClass.OFFICIAL_PUBLIC,
        access=SourceAccess.REGISTERED_KEY,
        commercial_use=True,
        public_display=True,
        public_api=True,
        derived_analysis_public=True,
        attribution_required=True,
        attribution="Source: U.S. Bureau of Economic Analysis",
        terms_url="https://www.bea.gov/about/policies-and-information/data-use-policies",
        reviewed_on=date(2026, 8, 18),
        notes=[
            "BEA-published data are public domain except content explicitly identified as "
            "third-party copyrighted material.",
            "API access uses a free registered key; that access credential is separate from the "
            "public-domain status of BEA data.",
            "Do not imply BEA or Department of Commerce endorsement of Yowayowa analysis.",
        ],
    ),
    SourceLicensePolicy(
        key="estat",
        source="Government of Japan e-Stat",
        provider_patterns=["estat"],
        license_class=LicenseClass.OFFICIAL_PUBLIC,
        access=SourceAccess.REGISTERED_KEY,
        commercial_use=True,
        public_display=True,
        public_api=True,
        derived_analysis_public=True,
        attribution_required=True,
        attribution="Source: Portal Site of Official Statistics of Japan (e-Stat)",
        terms_url="https://www.e-stat.go.jp/terms-of-use",
        reviewed_on=date(2026, 8, 18),
        notes=[
            "e-Stat permits commercial reuse of published content subject to attribution and "
            "identification of edits or transformations.",
            "Numerical data and simple tables/graphs are generally not copyright works under the "
            "site terms, while excluded third-party content and marks remain outside this policy.",
            "API access requires a registered application ID and published applications should "
            "display the requested e-Stat credit.",
        ],
    ),
    SourceLicensePolicy(
        key="us-treasury",
        source="U.S. Department of the Treasury",
        provider_patterns=["us-treasury"],
        license_class=LicenseClass.OFFICIAL_PUBLIC,
        access=SourceAccess.PUBLIC,
        commercial_use=True,
        public_display=True,
        public_api=True,
        derived_analysis_public=True,
        attribution_required=True,
        attribution="Source: U.S. Department of the Treasury",
        terms_url=("https://home.treasury.gov/resource-center/data-chart-center/interest-rates"),
        reviewed_on=date(2026, 8, 18),
        notes=[
            "Only the official Treasury rate data is treated as reusable here; third-party site "
            "assets, marks and unrelated content are outside this policy.",
        ],
    ),
    SourceLicensePolicy(
        key="fred",
        source="Federal Reserve Economic Data (FRED)",
        provider_patterns=["fred"],
        license_class=LicenseClass.USER_KEY,
        access=SourceAccess.REGISTERED_KEY,
        commercial_use=False,
        public_display=False,
        public_api=False,
        derived_analysis_public=False,
        attribution_required=True,
        attribution="Source: FRED / original series provider",
        terms_url="https://fred.stlouisfed.org/legal/terms/",
        reviewed_on=date(2026, 8, 18),
        notes=[
            "FRED aggregates series with different copyright statuses; a FRED API key does not "
            "grant redistribution rights to third-party series.",
            "Generic FRED discovery and series output are therefore personal-mode only until a "
            "series-specific rights registry is implemented.",
        ],
    ),
    SourceLicensePolicy(
        key="yahoo-personal",
        source="Yahoo Finance via yfinance",
        provider_patterns=["yahoo"],
        license_class=LicenseClass.PERSONAL_ONLY,
        access=SourceAccess.LOCAL_PERSONAL,
        commercial_use=False,
        public_display=False,
        public_api=False,
        derived_analysis_public=False,
        attribution_required=True,
        attribution="Source: Yahoo Finance",
        terms_url="https://legal.yahoo.com/us/en/yahoo/terms/product-atos/apiforydn/index.html",
        reviewed_on=date(2026, 8, 18),
        notes=[
            "Yowayowa intentionally treats Yahoo/yfinance data as personal-only and never as a "
            "public redistribution source.",
        ],
    ),
)


def source_policy(provider: str) -> SourceLicensePolicy | None:
    normalized = provider.strip().casefold()
    for policy in SOURCE_POLICIES:
        for pattern in policy.provider_patterns:
            token = pattern.casefold()
            if normalized == token or normalized.startswith(f"{token}-"):
                return policy
    return None


def _policy_public_api_allowed(policy: SourceLicensePolicy) -> bool:
    return (
        policy.commercial_use
        and policy.public_display
        and policy.public_api
        and policy.derived_analysis_public
    )


def public_api_allowed(provider: str) -> bool:
    policy = source_policy(provider)
    return bool(policy and _policy_public_api_allowed(policy))


def license_catalog(mode: str) -> LicenseCatalog:
    safe = [policy.key for policy in SOURCE_POLICIES if _policy_public_api_allowed(policy)]
    blocked = [policy.key for policy in SOURCE_POLICIES if not _policy_public_api_allowed(policy)]
    return LicenseCatalog(
        mode=mode,
        sources=list(SOURCE_POLICIES),
        public_safe_sources=safe,
        blocked_sources=blocked,
    )
