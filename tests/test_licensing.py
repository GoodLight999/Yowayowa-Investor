import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from starlette.requests import Request
from starlette.testclient import TestClient

from yowayowa.api.deps import require_api_token
from yowayowa.config import Settings, get_settings
from yowayowa.domain import LicenseClass
from yowayowa.providers.base import (
    ProviderDescriptor,
    ProviderPolicyError,
    enforce_provider_policy,
    enforce_source_policy,
)
from yowayowa.services.licensing import license_catalog, public_api_allowed


def _request(method: str, path: str) -> Request:
    return Request({"type": "http", "method": method, "path": path, "headers": []})


def test_license_catalog_fails_closed_and_separates_restricted_sources() -> None:
    catalog = license_catalog("public")

    assert {"sec-edgar", "edinet", "bls", "bea", "estat", "us-treasury"} <= set(
        catalog.public_safe_sources
    )
    assert {"fred", "yahoo-personal"} <= set(catalog.blocked_sources)
    assert public_api_allowed("sec-edgar") is True
    assert public_api_allowed("edinet-v2") is True
    assert public_api_allowed("bls") is True
    assert public_api_allowed("bea") is True
    assert public_api_allowed("estat") is True
    assert public_api_allowed("fred") is False
    assert public_api_allowed("yahoo") is False
    assert public_api_allowed("unknown-provider") is False


def test_public_source_policy_rejects_unknown_and_restricted_sources() -> None:
    enforce_source_policy("sec-edgar", mode="public")
    enforce_source_policy("bea", mode="public")
    enforce_source_policy("estat", mode="public")
    with pytest.raises(ProviderPolicyError):
        enforce_source_policy("fred", mode="public")
    with pytest.raises(ProviderPolicyError):
        enforce_source_policy("unregistered-source", mode="public")


def test_legacy_personal_provider_override_cannot_bypass_public_policy() -> None:
    descriptor = ProviderDescriptor(
        "yahoo",
        LicenseClass.PERSONAL_ONLY,
        False,
        "fixture",
    )
    with pytest.raises(ProviderPolicyError):
        enforce_provider_policy(
            descriptor,
            mode="public",
            allow_personal_in_public=True,
        )


def test_public_settings_reject_personal_provider_and_local_enrichment_overrides() -> None:
    with pytest.raises(ValidationError):
        Settings(
            mode="public",
            api_token="token",
            allow_personal_provider_in_public=True,
        )
    with pytest.raises(ValidationError):
        Settings(
            mode="public",
            api_token="token",
            local_enrichment_enabled=True,
        )


def test_public_anonymous_auth_allows_only_explicit_research_routes() -> None:
    settings = Settings(mode="public", api_token="secret")

    require_api_token(_request("GET", "/v1/licensing/sources"), settings=settings)
    require_api_token(_request("GET", "/v1/macro/bls/catalog"), settings=settings)
    require_api_token(
        _request("GET", "/v1/macro/bea/nipa/catalog"),
        settings=settings,
    )
    require_api_token(_request("GET", "/v1/macro/estat/tables"), settings=settings)
    require_api_token(_request("GET", "/v1/macro/estat/123/meta"), settings=settings)
    require_api_token(_request("GET", "/v1/macro/estat/123/data"), settings=settings)
    require_api_token(_request("POST", "/v1/compare"), settings=settings)

    with pytest.raises(HTTPException) as private_error:
        require_api_token(_request("GET", "/v1/portfolios"), settings=settings)
    assert private_error.value.status_code == 401

    with pytest.raises(HTTPException) as restricted_error:
        require_api_token(_request("GET", "/v1/markets/quotes"), settings=settings)
    assert restricted_error.value.status_code == 401


def test_public_api_exposes_safe_sources_but_not_private_or_restricted_data(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("YOWAYOWA_MODE", "public")
    monkeypatch.setenv("YOWAYOWA_API_TOKEN", "secret")
    monkeypatch.setenv("YOWAYOWA_DATABASE_URL", f"sqlite:///{tmp_path / 'public.db'}")
    monkeypatch.delenv("YOWAYOWA_ESTAT_APP_ID", raising=False)
    get_settings.cache_clear()

    from yowayowa.api.app import app

    with TestClient(app) as client:
        catalog = client.get("/v1/licensing/sources")
        assert catalog.status_code == 200
        assert {"bls", "bea", "estat"} <= set(catalog.json()["public_safe_sources"])

        bls_catalog = client.get("/v1/macro/bls/catalog")
        assert bls_catalog.status_code == 200
        assert {item["series_id"] for item in bls_catalog.json()["series"]} >= {
            "CUSR0000SA0",
            "LNS14000000",
        }

        bea_catalog = client.get("/v1/macro/bea/nipa/catalog")
        assert bea_catalog.status_code == 200
        assert {item["table_name"] for item in bea_catalog.json()["tables"]} >= {
            "T10101",
            "T20305",
        }
        bea_data_without_server_key = client.get(
            "/v1/macro/bea/nipa/T10101",
            params={"years": 2025, "line_number": 1},
        )
        assert bea_data_without_server_key.status_code == 424

        estat_without_server_id = client.get(
            "/v1/macro/estat/tables",
            params={"q": "consumer prices"},
        )
        assert estat_without_server_id.status_code == 424

        private = client.get("/v1/portfolios")
        assert private.status_code == 401

        restricted_without_auth = client.get("/v1/macro/fred/search", params={"q": "CPI"})
        assert restricted_without_auth.status_code == 401

        restricted_with_auth = client.get(
            "/v1/macro/fred/search",
            params={"q": "CPI"},
            headers={"Authorization": "Bearer secret"},
        )
        assert restricted_with_auth.status_code == 403

        root = client.get("/", follow_redirects=False)
        assert root.status_code == 307
        assert root.headers["location"] == "/macro"
        market_page = client.get("/markets", follow_redirects=False)
        assert market_page.status_code == 307
        assert market_page.headers["location"] == "/macro"

        compare_page = client.get("/compare?lang=en")
        assert compare_page.status_code == 200
        assert "PUBLIC · LIVE" in compare_page.text
        assert 'window.YOWAYOWA_MODE = "public"' in compare_page.text
        assert 'id="compare-preset-form"' not in compare_page.text
        assert 'id="use-main-watchlist"' not in compare_page.text
        assert 'href="/portfolio"' not in compare_page.text
        assert 'href="/licenses"' in compare_page.text

        screener_page = client.get("/screener?lang=en")
        assert screener_page.status_code == 200
        assert 'id="screen-preset-select"' not in screener_page.text
        assert 'id="screen-use-watchlist"' not in screener_page.text

        macro_page = client.get("/macro?lang=en")
        assert macro_page.status_code == 200
        assert "Official public macro" in macro_page.text
        assert "Japan e-Stat official statistics" in macro_page.text
        assert "BEA access requires a free registered API key" in macro_page.text
        assert 'id="estat-search-form"' in macro_page.text
        assert 'id="macro-search-form"' not in macro_page.text

    get_settings.cache_clear()
