from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

from yowayowa.config import Settings
from yowayowa.domain import Fundamentals, LicenseClass, MetricPoint, MetricSeries, Provenance
from yowayowa.research_models import (
    AIChatRequest,
    AIMessage,
    AIPromptPacketRequest,
    AIProviderConfig,
    MarketScreenResponse,
)
from yowayowa.services import ai_agent
from yowayowa.services.ai_agent import InvestmentResearchAgent
from yowayowa.services.codex_cli import CodexStructuredResult


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
    assert "triage_strategy" in status["tools"]
    assert "get_strategy_history" in status["tools"]
    assert "get_strategy_outcomes" in status["tools"]


def test_ai_strategy_triage_returns_interpretable_priority(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    provenance = Provenance(
        provider="fixture",
        source="fixture",
        license_class=LicenseClass.OFFICIAL_PUBLIC,
        retrieved_at=datetime(2026, 9, 21, tzinfo=UTC),
        as_of=date(2025, 12, 31),
    )

    def series(key: str, value: float) -> MetricSeries:
        return MetricSeries(
            key=key,
            label=key,
            points=[
                MetricPoint(
                    period_end=date(2025, 12, 31),
                    fiscal_period="FY",
                    value=Decimal(str(value)),
                    unit="USD",
                )
            ],
        )

    facts = Fundamentals(
        symbol="TEST",
        cik="",
        company_name="Test Corp",
        metrics={
            "current_assets": series("current_assets", 120),
            "liabilities": series("liabilities", 40),
            "operating_cash_flow": series("operating_cash_flow", 15),
            "capex": series("capex", 5),
        },
        provenance=provenance,
    )
    screen = MarketScreenResponse(
        quotes=[
            {
                "symbol": "TEST",
                "marketCap": 100.0,
                "trailingPE": 10.0,
            }
        ],
        total=1,
        offset=0,
        size=1,
        provenance=provenance,
    )
    monkeypatch.setattr(
        ai_agent,
        "yahoo_screener_provider",
        lambda: SimpleNamespace(screen=lambda _payload: screen),
    )
    monkeypatch.setattr(
        ai_agent,
        "sec_client",
        lambda: SimpleNamespace(company_facts=lambda _symbol: facts),
    )
    monkeypatch.setattr(
        ai_agent,
        "record_strategy_snapshots",
        lambda *_args, **_kwargs: [SimpleNamespace(id=123)],
    )

    agent = InvestmentResearchAgent(Settings(database_url="sqlite:///:memory:"), Mock())
    result = agent._tool_triage_strategy(
        {
            "strategy_id": "kiyohara_global_value_growth",
            "region": "us",
            "size": 5,
        }
    )

    assert result["strategy_id"] == "kiyohara_global_value_growth"
    assert result["region"] == "us"
    assert result["evaluations"][0]["symbol"] == "TEST"
    assert result["snapshot_ids"] == [123]
    priority = result["evaluations"][0]["research_priority"]
    assert priority["score"] > 0
    assert priority["interpretation"] == "research_priority_not_return_forecast"
    assert {item["key"] for item in priority["factors"]} == {
        "value",
        "growth",
        "quality",
        "evidence",
    }


def test_codex_chat_uses_same_yowayowa_tool_loop(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    replies = iter(
        [
            {
                "answer": None,
                "tool_calls": [
                    {
                        "name": "get_alerts",
                        "arguments": {},
                    }
                ],
            },
            {
                "answer": "Codex answer",
                "tool_calls": [],
            },
        ]
    )
    monkeypatch.setattr(
        ai_agent,
        "run_codex_structured",
        lambda *_args, **_kwargs: CodexStructuredResult(result=next(replies)),
    )
    monkeypatch.setattr(ai_agent, "list_alerts", lambda _session: [])

    agent = InvestmentResearchAgent(
        Settings(database_url="sqlite:///:memory:", codex_cli_enabled=True),
        Mock(),
    )
    result = agent.chat(
        AIChatRequest(
            messages=[AIMessage(role="user", content="状況を調べて")],
            provider=AIProviderConfig(
                provider="codex_cli",
                model="default",
                api_key="local",
            ),
        )
    )

    assert result.answer == "Codex answer"
    assert result.provider == "codex_cli"
    assert result.model == "default"
    assert [item.tool for item in result.tool_trace] == ["get_alerts"]


def test_external_prompt_packet_works_without_any_ai_provider() -> None:
    agent = InvestmentResearchAgent(
        Settings(
            database_url="sqlite:///:memory:",
            codex_cli_enabled=False,
        ),
        Mock(),
    )

    packet = agent.prompt_packet(
        AIPromptPacketRequest(
            user_prompt="この材料を投資判断用に分析して",
            context={"page": "/ai"},
        )
    )

    assert "YOWAYOWA DATA PACKET JSON" in packet.prompt
    assert "この材料を投資判断用に分析して" in packet.prompt
    assert packet.included_tools == []
    assert packet.characters == len(packet.prompt)



def test_hosted_codex_refreshes_browser_credential_between_tool_rounds(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    seen_credentials: list[str | None] = []
    replies = iter(
        [
            CodexStructuredResult(
                result={
                    "answer": None,
                    "tool_calls": [{"name": "get_alerts", "arguments": {}}],
                },
                credential="sealed-two",
            ),
            CodexStructuredResult(
                result={"answer": "done", "tool_calls": []},
                credential="sealed-three",
            ),
        ]
    )

    def fake_run(*_args, **kwargs):  # type: ignore[no-untyped-def]
        seen_credentials.append(kwargs.get("credential"))
        return next(replies)

    monkeypatch.setattr(ai_agent, "run_codex_structured", fake_run)
    monkeypatch.setattr(ai_agent, "list_alerts", lambda _session: [])

    agent = InvestmentResearchAgent(
        Settings(
            database_url="sqlite:///:memory:",
            codex_cli_enabled=False,
            codex_bridge_url="https://codex.internal",
        ),
        Mock(),
        codex_session_id="browser-session-0123456789",
    )
    result = agent.chat(
        AIChatRequest(
            messages=[AIMessage(role="user", content="調べて")],
            provider=AIProviderConfig(
                provider="codex_cli",
                model="default",
                api_key="local",
                credential="sealed-one",
            ),
        )
    )

    assert seen_credentials == ["sealed-one", "sealed-two"]
    assert result.answer == "done"
    assert result.provider_credential == "sealed-three"
