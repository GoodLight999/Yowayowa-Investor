from __future__ import annotations

from datetime import date

import httpx
import pytest

from yowayowa.config import Settings
from yowayowa.providers import alpha_vantage as av_module
from yowayowa.providers.alpha_vantage import (
    AlphaVantageClient,
    AlphaVantageQuotaExhausted,
)
from yowayowa.providers.base import ProviderPolicyError

GLOBAL_QUOTE_PAYLOAD = {
    "Global Quote": {
        "01. symbol": "IBM",
        "02. open": "248.0000",
        "03. high": "252.8800",
        "04. low": "247.4400",
        "05. price": "251.4600",
        "06. volume": "10027803",
        "07. latest trading day": "2026-10-21",
        "08. previous close": "241.9300",
        "09. change": "9.5300",
        "10. change percent": "3.9435%",
    }
}


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": "sqlite:///:memory:",
        "alpha_vantage_api_key": "test-key",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def _client(**overrides: object) -> AlphaVantageClient:
    kwargs: dict[str, object] = {}
    if "usage_path" in overrides:
        kwargs["usage_path"] = overrides.pop("usage_path")
    return AlphaVantageClient(_settings(**overrides), **kwargs)  # type: ignore[arg-type]


def test_settings_field_reads_prefixed_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YOWAYOWA_ALPHA_VANTAGE_API_KEY", "env-key")
    assert Settings(database_url="sqlite:///:memory:").alpha_vantage_api_key == "env-key"


def test_client_fails_closed_in_public_mode() -> None:
    with pytest.raises(ProviderPolicyError):
        AlphaVantageClient(_settings(mode="public", api_token="token", alpha_vantage_api_key="k"))


def test_key_fails_closed_without_api_key(tmp_path) -> None:  # type: ignore[no-untyped-def]
    usage = tmp_path / "usage.json"
    usage.write_text('{"date": "2026-10-22", "used": 0, "limit": 25}', encoding="utf-8")
    client = _client(alpha_vantage_api_key=None, usage_path=usage)

    with pytest.raises(RuntimeError, match="YOWAYOWA_ALPHA_VANTAGE_API_KEY"):
        client.global_quote("IBM")

    # The key guard runs before the budget reserve: nothing was charged.
    assert usage.read_text(encoding="utf-8").startswith('{"date": "2026-10-22", "used": 0')


def test_global_quote_builds_request_and_provenance(httpx_mock, tmp_path) -> None:  # type: ignore[no-untyped-def]
    httpx_mock.add_response(json=GLOBAL_QUOTE_PAYLOAD)
    client = _client(usage_path=tmp_path / "usage.json")

    result = client.global_quote("IBM")

    assert result["symbol"] == "IBM"
    assert result["quote"]["05. price"] == "251.4600"
    provenance = result["provenance"]
    assert provenance["provider"] == "alpha-vantage"
    assert provenance["license_class"] == "personal_only"
    assert provenance["source_url"].endswith("symbol=IBM")

    request = httpx_mock.get_requests()[0]
    assert request.url.host == "www.alphavantage.co"
    assert request.url.path == "/query"
    params = request.url.params
    assert params["function"] == "GLOBAL_QUOTE"
    assert params["symbol"] == "IBM"
    assert params["apikey"] == "test-key"


def test_global_quote_yields_none_quote_on_unknown_payload(httpx_mock, tmp_path) -> None:  # type: ignore[no-untyped-def]
    httpx_mock.add_response(json={})
    result = _client(usage_path=tmp_path / "usage.json").global_quote("IBM")
    assert result["quote"] is None


@pytest.mark.parametrize("flag", ["Error Message", "Information", "Note"])
def test_api_message_bodies_raise_after_counting_the_attempt(
    httpx_mock,
    flag: str,
    tmp_path,  # type: ignore[no-untyped-def]
) -> None:
    httpx_mock.add_response(json={flag: "rate limited"})
    usage = tmp_path / "usage.json"
    client = _client(usage_path=usage)

    with pytest.raises(RuntimeError, match=flag):
        client.global_quote("IBM")

    # The attempt is charged even though the call failed: undercounting the
    # budget is the failure mode the guard must never allow.
    assert '"used": 1' in usage.read_text(encoding="utf-8")


def test_http_error_counts_against_budget(httpx_mock, tmp_path) -> None:  # type: ignore[no-untyped-def]
    httpx_mock.add_response(status_code=500)
    usage = tmp_path / "usage.json"
    client = _client(usage_path=usage)

    with pytest.raises(httpx.HTTPStatusError):
        client.global_quote("IBM")

    assert '"used": 1' in usage.read_text(encoding="utf-8")


def test_daily_budget_fails_closed_at_limit(httpx_mock, tmp_path) -> None:  # type: ignore[no-untyped-def]
    usage = tmp_path / "usage.json"
    client = _client(usage_path=usage)
    client.daily_request_limit = 2
    httpx_mock.add_response(json=GLOBAL_QUOTE_PAYLOAD)
    httpx_mock.add_response(json=GLOBAL_QUOTE_PAYLOAD)

    assert client.global_quote("IBM")["quote"] is not None
    assert client.global_quote("IBM")["quote"] is not None

    with pytest.raises(AlphaVantageQuotaExhausted, match="2/2"):
        client.global_quote("IBM")

    # Fail-closed: the refused call never reached the network.
    assert len(httpx_mock.get_requests()) == 2


def test_daily_budget_is_shared_across_clients(httpx_mock, tmp_path) -> None:  # type: ignore[no-untyped-def]
    usage = tmp_path / "usage.json"
    httpx_mock.add_response(json=GLOBAL_QUOTE_PAYLOAD)
    first = _client(usage_path=usage)
    first.daily_request_limit = 1
    second = _client(usage_path=usage)
    second.daily_request_limit = 1

    assert first.global_quote("IBM")["quote"] is not None

    with pytest.raises(AlphaVantageQuotaExhausted):
        second.global_quote("IBM")

    # The second client was blocked before any further HTTP request.
    assert len(httpx_mock.get_requests()) == 1


def test_daily_budget_resets_on_new_day(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    usage = tmp_path / "usage.json"
    usage.write_text('{"date": "2026-10-20", "used": 25, "limit": 25}', encoding="utf-8")
    monkeypatch.setattr(
        "yowayowa.providers.alpha_vantage._DailyBudget._today",
        staticmethod(lambda: date(2026, 10, 21)),
    )
    budget = av_module._DailyBudget(usage, limit=25)

    assert budget.reserve() == 1
    assert '"used": 1' in usage.read_text(encoding="utf-8")


def test_corrupt_budget_state_fails_closed_for_the_day(tmp_path) -> None:  # type: ignore[no-untyped-def]
    usage = tmp_path / "usage.json"
    usage.write_text("not json", encoding="utf-8")
    budget = av_module._DailyBudget(usage, limit=25)

    with pytest.raises(AlphaVantageQuotaExhausted):
        budget.reserve()
