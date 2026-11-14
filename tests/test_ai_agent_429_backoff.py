"""Bounded 429 (rate limit) behavior of the AI chat transport (t_ee4056a0).

A 429 is a scheduling signal, not a permanent failure: transient shapes
(z.ai 1302 concurrency / 1305 overload / opaque bodies) back off a bounded
number of times, while period walls (1308 five-hour window, 1311
plan-excluded model etc.) stop immediately with a short Japanese reason.
Real Z.AI bodies carry the error code as a numeric string, so both int and
digit-string codes are exercised here. The final failure keeps the
httpx.HTTPStatusError contract of the existing API/CLI boundaries, without
echoing the raw upstream body. Stubbed transport only — these tests never
touch a real provider and consume no quota.
"""

from __future__ import annotations

import json
from unittest.mock import Mock

import httpx
import pytest

from yowayowa.config import Settings
from yowayowa.research_models import AIChatRequest, AIMessage, AIProviderConfig
from yowayowa.services import ai_agent
from yowayowa.services.ai_agent import InvestmentResearchAgent

_URL = "https://provider.example/v1/chat/completions"


def _agent() -> InvestmentResearchAgent:
    return InvestmentResearchAgent(Settings(database_url="sqlite:///:memory:"), Mock())


def _request() -> AIChatRequest:
    return AIChatRequest(
        messages=[AIMessage(role="user", content="RKLBとASTSを比較して")],
        provider=AIProviderConfig(
            provider="openai_compatible",
            model="test-model",
            api_key="super-secret",
            base_url="https://provider.example/v1",
        ),
    )


class _FakeResponse:
    """Minimal stand-in for an httpx response (status/text/json only)."""

    def __init__(self, payload: dict[str, object], status: int = 200) -> None:
        self._payload = payload
        self.status_code = status
        self.text = json.dumps(payload)

    def json(self) -> dict[str, object]:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code == 200:
            return None
        raise httpx.HTTPStatusError(
            f"Client error '{self.status_code}'",
            request=httpx.Request("POST", _URL),
            response=self,  # type: ignore[arg-type]
        )


def _ok() -> _FakeResponse:
    return _FakeResponse(
        {"choices": [{"message": {"role": "assistant", "content": "比較画面を準備しました。"}}]}
    )


def _rate_limited(code: int | str | None = None, message: str = "rate limit") -> _FakeResponse:
    error: dict[str, object] = {"message": message}
    if code is not None:
        error["code"] = code
    return _FakeResponse({"error": error}, status=429)


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"error": {"code": 1308}}, 1308),
        ({"error": {"code": "1308"}}, 1308),
        ({"error": {"code": " 1305 "}}, 1305),
        ({"code": "1302"}, 1302),
        ({"error": {"code": "1302"}, "code": "1308"}, 1302),
        ({"error": {"code": True}}, None),
        ({"error": {"code": "12x8"}}, None),
        ({"error": {"message": "rate limit"}}, None),
        ({}, None),
    ],
)
def test_rate_limit_code_extraction(payload: dict[str, object], expected: int | None) -> None:  # type: ignore[no-untyped-def]
    assert ai_agent._rate_limit_code(_FakeResponse(payload)) == expected


def test_transient_429_backs_off_bounded_then_recovers(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    responses = iter([_rate_limited("1302"), _rate_limited("1305"), _ok()])
    monkeypatch.setattr(ai_agent.httpx, "post", lambda *_a, **_k: next(responses))
    sleeps: list[float] = []
    monkeypatch.setattr(ai_agent.time, "sleep", sleeps.append)

    result = _agent().chat(_request())

    assert result.answer == "比較画面を準備しました。"
    assert sleeps == list(ai_agent.RATE_LIMIT_BACKOFF_SECONDS)


def test_period_wall_429_fails_fast_with_japanese_reason(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    responses = iter([_rate_limited("1308", "Usage limit reached for 5 hour")])
    monkeypatch.setattr(ai_agent.httpx, "post", lambda *_a, **_k: next(responses))
    sleeps: list[float] = []
    monkeypatch.setattr(ai_agent.time, "sleep", sleeps.append)

    with pytest.raises(httpx.HTTPStatusError) as excinfo:
        _agent().chat(_request())

    message = str(excinfo.value)
    assert "5時間利用枠" in message
    assert "[code=1308]" in message
    assert "body=" not in message
    assert "Usage limit reached" not in message
    assert "super-secret" not in message
    assert sleeps == []
    assert isinstance(excinfo.value.__cause__, httpx.HTTPStatusError)
    assert excinfo.value.response.status_code == 429


def test_plan_excluded_model_429_1311_fails_fast(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    responses = iter([_rate_limited("1311", "model not available in plan")])
    monkeypatch.setattr(ai_agent.httpx, "post", lambda *_a, **_k: next(responses))
    sleeps: list[float] = []
    monkeypatch.setattr(ai_agent.time, "sleep", sleeps.append)

    with pytest.raises(httpx.HTTPStatusError) as excinfo:
        _agent().chat(_request())

    message = str(excinfo.value)
    assert "プラン対象外モデル" in message
    assert "[code=1311]" in message
    assert sleeps == []


def test_top_level_code_429_fails_fast(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    responses = iter([_FakeResponse({"code": "1308", "message": "usage limit"}, status=429)])
    monkeypatch.setattr(ai_agent.httpx, "post", lambda *_a, **_k: next(responses))
    sleeps: list[float] = []
    monkeypatch.setattr(ai_agent.time, "sleep", sleeps.append)

    with pytest.raises(httpx.HTTPStatusError) as excinfo:
        _agent().chat(_request())

    message = str(excinfo.value)
    assert "5時間利用枠" in message
    assert "[code=1308]" in message
    assert sleeps == []


def test_persistent_429_stops_at_bounded_backoff_with_japanese_reason(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    responses = iter([_rate_limited(1302) for _ in range(10)])
    posts: list[int] = []

    def fake_post(*_a: object, **_k: object) -> _FakeResponse:
        posts.append(1)
        return next(responses)

    monkeypatch.setattr(ai_agent.httpx, "post", fake_post)
    sleeps: list[float] = []
    monkeypatch.setattr(ai_agent.time, "sleep", sleeps.append)

    with pytest.raises(httpx.HTTPStatusError) as excinfo:
        _agent().chat(_request())

    assert len(posts) == 1 + len(ai_agent.RATE_LIMIT_BACKOFF_SECONDS)
    assert sleeps == list(ai_agent.RATE_LIMIT_BACKOFF_SECONDS)
    message = str(excinfo.value)
    assert "同時実行数" in message
    assert "[code=1302]" in message
    assert "body=" not in message
    assert "super-secret" not in message


def test_opaque_429_without_code_still_backs_off_bounded(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    responses = iter([_rate_limited(None), _ok()])
    monkeypatch.setattr(ai_agent.httpx, "post", lambda *_a, **_k: next(responses))
    sleeps: list[float] = []
    monkeypatch.setattr(ai_agent.time, "sleep", sleeps.append)

    result = _agent().chat(_request())

    assert result.answer == "比較画面を準備しました。"
    assert sleeps == [ai_agent.RATE_LIMIT_BACKOFF_SECONDS[0]]


def test_bool_code_treated_as_opaque(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    responses = iter([_FakeResponse({"error": {"code": True}}, status=429), _ok()])
    monkeypatch.setattr(ai_agent.httpx, "post", lambda *_a, **_k: next(responses))
    sleeps: list[float] = []
    monkeypatch.setattr(ai_agent.time, "sleep", sleeps.append)

    result = _agent().chat(_request())

    assert result.answer == "比較画面を準備しました。"
    assert sleeps == [ai_agent.RATE_LIMIT_BACKOFF_SECONDS[0]]


def test_non_429_http_error_passthrough_unchanged(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    responses = iter([_FakeResponse({"error": {"message": "boom"}}, status=500)])
    monkeypatch.setattr(ai_agent.httpx, "post", lambda *_a, **_k: next(responses))
    sleeps: list[float] = []
    monkeypatch.setattr(ai_agent.time, "sleep", sleeps.append)

    with pytest.raises(httpx.HTTPStatusError):
        _agent().chat(_request())

    assert sleeps == []
