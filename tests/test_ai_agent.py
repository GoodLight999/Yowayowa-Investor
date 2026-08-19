from __future__ import annotations

from unittest.mock import Mock

from yowayowa.config import Settings
from yowayowa.research_models import AIChatRequest, AIMessage, AIProviderConfig
from yowayowa.services import ai_agent
from yowayowa.services.ai_agent import InvestmentResearchAgent


class FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self.payload


def test_openai_compatible_agent_executes_tool_then_returns_answer(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    responses = iter(
        [
            FakeResponse(
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call-1",
                                        "type": "function",
                                        "function": {
                                            "name": "propose_compare",
                                            "arguments": '{"symbols":["RKLB","ASTS"]}',
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                }
            ),
            FakeResponse(
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "比較画面を準備しました。",
                            }
                        }
                    ]
                }
            ),
        ]
    )
    sent: list[dict[str, object]] = []

    def fake_post(url: str, **kwargs: object) -> FakeResponse:
        sent.append({"url": url, **kwargs})
        return next(responses)

    monkeypatch.setattr(ai_agent.httpx, "post", fake_post)
    agent = InvestmentResearchAgent(Settings(database_url="sqlite:///:memory:"), Mock())
    request = AIChatRequest(
        messages=[AIMessage(role="user", content="RKLBとASTSを比較して")],
        provider=AIProviderConfig(
            provider="openai_compatible",
            model="test-model",
            api_key="super-secret",
            base_url="https://provider.example/v1",
        ),
    )
    result = agent.chat(request)

    assert result.answer == "比較画面を準備しました。"
    assert result.tool_trace[0].tool == "propose_compare"
    assert result.proposed_operations[0].kind.value == "compare.symbols"
    assert result.proposed_operations[0].arguments["symbols"] == ["RKLB", "ASTS"]
    assert sent[0]["url"] == "https://provider.example/v1/chat/completions"
    assert "super-secret" not in result.model_dump_json()


def test_anthropic_agent_executes_native_tool_use(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    responses = iter(
        [
            FakeResponse(
                {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu-1",
                            "name": "propose_watchlist_change",
                            "input": {"action": "add", "symbols": ["RKLB"]},
                        }
                    ]
                }
            ),
            FakeResponse(
                {"content": [{"type": "text", "text": "ウォッチリスト追加を提案しました。"}]}
            ),
        ]
    )

    monkeypatch.setattr(ai_agent.httpx, "post", lambda *_args, **_kwargs: next(responses))
    agent = InvestmentResearchAgent(Settings(database_url="sqlite:///:memory:"), Mock())
    request = AIChatRequest(
        messages=[AIMessage(role="user", content="RKLBをウォッチリストに追加して")],
        provider=AIProviderConfig(
            provider="anthropic",
            model="claude-test",
            api_key="anthropic-secret",
        ),
    )
    result = agent.chat(request)

    assert result.answer == "ウォッチリスト追加を提案しました。"
    assert result.tool_trace[0].tool == "propose_watchlist_change"
    assert result.proposed_operations[0].kind.value == "watchlist.add"
    assert "anthropic-secret" not in result.model_dump_json()


def test_ai_status_never_returns_configured_keys() -> None:
    settings = Settings(
        database_url="sqlite:///:memory:",
        openai_compatible_api_key="openai-secret",
        openai_compatible_model="model-a",
        anthropic_api_key="anthropic-secret",
        anthropic_model="model-b",
    )
    status = InvestmentResearchAgent(settings, Mock()).status()

    assert status["configured"] == {"openai_compatible": True, "anthropic": True}
    assert "secret" not in str(status)
    assert "discover_stocks" in status["tools"]
