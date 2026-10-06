from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

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
    assert "get_strategy_calibration" in status["tools"]


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


def test_ai_tool_strategy_calibration_aggregates_with_fixture_provider(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from yowayowa.domain import MarketHistory, PriceBar
    from yowayowa.providers.base import ProviderDescriptor

    def bar(day: int, value: float) -> PriceBar:
        return PriceBar(
            timestamp=datetime(2026, 1, day, tzinfo=UTC),
            open=value,
            high=value,
            low=value,
            close=value,
            volume=1,
        )

    def history(symbol: str, values: list[float]) -> MarketHistory:
        return MarketHistory(
            symbol=symbol,
            interval="1d",
            bars=[bar(day + 1, value) for day, value in enumerate(values)],
            provenance=Provenance(
                provider="fixture",
                source=f"history:{symbol}",
                license_class=LicenseClass.PERSONAL_ONLY,
                retrieved_at=datetime(2026, 2, 1, tzinfo=UTC),
            ),
        )

    class FakeMarketProvider:
        descriptor = ProviderDescriptor(
            name="fixture",
            license_class=LicenseClass.PERSONAL_ONLY,
            redistributable=False,
            description="fixture",
        )

        def __init__(self) -> None:
            self.histories = {
                "CAL": history("CAL", [100, 100, 110, 121]),
                "^GSPC": history("^GSPC", [200, 200, 202, 204]),
            }

        def history(
            self,
            symbol: str,
            period: str,
            interval: str,
            indicators: list[str],
        ) -> MarketHistory:
            return self.histories[symbol]

    monkeypatch.setattr(ai_agent, "yahoo_market_provider", lambda: FakeMarketProvider())

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from yowayowa.db import Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        agent = InvestmentResearchAgent(Settings(database_url="sqlite:///:memory:"), Mock())
        agent.session = session
        result = agent._tool_strategy_calibration(
            {
                "strategy_id": "kiyohara_global_value_growth",
                "horizons": [2],
                "limit": 30,
            }
        )
    engine.dispose()

    # No snapshots recorded in this empty fixture database -> no buckets, no error.
    assert result["buckets"] == []
    assert isinstance(result["notes"], list)
    assert result["provenance"] == []

    # A prompt packet that references a strategy must include calibration evidence.
    packet = agent.prompt_packet(
        AIPromptPacketRequest(context={"strategy": "kiyohara_global_value_growth"})
    )
    assert "get_strategy_calibration" in packet.included_tools


def test_ai_tool_strategy_calibration_reports_oos_fields(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """OOS fields from the purged walk-forward split surface through the AI tool."""
    from datetime import timedelta

    from yowayowa.domain import MarketHistory, PriceBar
    from yowayowa.providers.base import ProviderDescriptor

    base_day = datetime(2026, 1, 1, tzinfo=UTC)

    def bar(offset: int, value: float) -> PriceBar:
        return PriceBar(
            timestamp=base_day + timedelta(days=offset),
            open=value,
            high=value,
            low=value,
            close=value,
            volume=1,
        )

    def history(symbol: str, values: list[float]) -> MarketHistory:
        return MarketHistory(
            symbol=symbol,
            interval="1d",
            bars=[bar(offset, value) for offset, value in enumerate(values)],
            provenance=Provenance(
                provider="fixture",
                source=f"history:{symbol}",
                license_class=LicenseClass.PERSONAL_ONLY,
                retrieved_at=datetime(2026, 2, 1, tzinfo=UTC),
            ),
        )

    class FakeMarketProvider:
        descriptor = ProviderDescriptor(
            name="fixture",
            license_class=LicenseClass.PERSONAL_ONLY,
            redistributable=False,
            description="fixture",
        )

        def __init__(self, histories: dict[str, MarketHistory]) -> None:
            self.histories = histories

        def history(
            self,
            symbol: str,
            period: str,
            interval: str,
            indicators: list[str],
        ) -> MarketHistory:
            return self.histories[symbol]

    # 11 snapshots at 10-day spacing with a 2-day horizon (windows never
    # overlap) and strictly increasing forward returns -> in-sample 1 /
    # out-of-sample 10 with a perfect OOS rank IC. Per-day drift rises with the
    # index so each snapshot's 2-day forward return is strictly larger than the
    # previous one; ~117 daily bars cover every capture + horizon.
    histories: dict[str, MarketHistory] = {
        "^GSPC": history("^GSPC", [200.0] * 130),
    }
    snapshots = []
    for index in range(11):
        symbol = f"OO{index}"
        drift = 0.001 + 0.0005 * index
        closes: list[float] = []
        value = 100.0
        for _day in range(130):
            value = value * (1 + drift)
            closes.append(round(value, 6))
        histories[symbol] = history(symbol, [100.0, *closes])
        snapshots.append(
            SimpleNamespace(
                id=index + 1,
                strategy_id="kiyohara_global_value_growth",
                scoring_version="kiyohara_priority_v1",
                region="us",
                symbol=symbol,
                score=50 + 5 * index,
                confidence=0.9,
                captured_at=base_day + timedelta(days=10 * index, hours=12),
                evaluation=SimpleNamespace(research_priority=None),
            )
        )

    monkeypatch.setattr(
        ai_agent,
        "list_strategy_snapshots",
        lambda *_args, **_kwargs: snapshots,
    )
    monkeypatch.setattr(
        ai_agent,
        "yahoo_market_provider",
        lambda: FakeMarketProvider(histories),
    )

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from yowayowa.db import Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        agent = InvestmentResearchAgent(Settings(database_url="sqlite:///:memory:"), Mock())
        agent.session = session
        result = agent._tool_strategy_calibration(
            {
                "strategy_id": "kiyohara_global_value_growth",
                "horizons": [2],
                "limit": 30,
            }
        )
    engine.dispose()

    assert len(result["buckets"]) == 1
    bucket = result["buckets"][0]
    assert bucket["sample_available"] == 11
    assert bucket["oos_sample_count"] == 10
    assert bucket["is_sample_count"] == 1
    assert bucket["purged_count"] == 0
    assert bucket["oos_split_at"] is not None
    # Strictly increasing returns -> perfect positive OOS rank IC.
    assert bucket["oos_rank_ic"] == pytest.approx(1.0)
    assert bucket["oos_ic_insufficient"] is False
    assert bucket["is_ic_insufficient"] is True  # 1 in-sample outcome
    # The is_*-are-not-a-fitted-fit disclaimer always ships.
    assert any("never fitted" in note for note in bucket["oos_notes"])
    # Explicit oos_min_sample passes through the tool arguments.
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        agent = InvestmentResearchAgent(Settings(database_url="sqlite:///:memory:"), Mock())
        agent.session = session
        clamped = agent._tool_strategy_calibration(
            {
                "strategy_id": "kiyohara_global_value_growth",
                "horizons": [2],
                "limit": 30,
                "oos_min_sample": 3,
            }
        )
    engine.dispose()
    bucket = clamped["buckets"][0]
    assert bucket["oos_sample_count"] == 3
    assert bucket["is_sample_count"] == 8


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


def test_hosted_codex_refreshes_browser_credential(monkeypatch) -> None:  # type: ignore[no-untyped-def]
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


def test_get_ohlcv_tool_reads_persisted_stores(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The get_ohlcv tool returns saved rows verbatim via the evidence collector."""

    stock_dir = tmp_path / "stock-ohlcv" / "AAPL"
    stock_dir.mkdir(parents=True)
    row = {
        "symbol": "AAPL",
        "currency": "USD",
        "provider": "alpaca",
        "as_of": "2026-09-24T04:00:00Z",
        "open": 1.0,
        "high": 2.0,
        "low": 0.5,
        "close": 1.5,
        "volume": 100.0,
        "source_url": "https://data.alpaca.markets/v2/stocks/bars?symbols=AAPL",
        "retrieved_at": "2026-09-24T15:25:05Z",
    }
    (stock_dir / "ohlcv.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")

    agent = InvestmentResearchAgent(Settings(database_url="sqlite:///:memory:"), Mock())
    assert "get_ohlcv" in agent.tools

    import yowayowa.services.ohlcv_evidence as ohlcv_module

    real_stock = ohlcv_module.collect_stock_evidence
    real_crypto = ohlcv_module.collect_crypto_evidence

    monkeypatch.setattr(
        ohlcv_module,
        "collect_stock_evidence",
        lambda root, question, **kwargs: real_stock(tmp_path / "stock-ohlcv", question, **kwargs),
    )
    monkeypatch.setattr(
        ohlcv_module,
        "collect_crypto_evidence",
        lambda root, question, **kwargs: real_crypto(tmp_path / "crypto-ohlcv", question, **kwargs),
    )

    result = agent._tool_ohlcv({"market": "stock", "symbol": "aapl", "limit": 5})
    assert result["symbol"] == "AAPL"
    assert result["market"] == "stock"
    assert result["row_count"] == 1
    assert result["rows"][0]["close"] == 1.5  # verbatim, never recomputed

    # limit is clamped into 1..60 and defaults to 10.
    clamped = agent._tool_ohlcv({"market": "stock", "symbol": "AAPL", "limit": 999})
    assert clamped["row_count"] == 1
    assert agent._tool_ohlcv({"market": "stock", "symbol": "AAPL"})["rows"] == result["rows"]

    # Unknown market fails closed with an error dict.
    assert "error" in agent._tool_ohlcv({"market": "fx", "symbol": "AAPL"})

    # Missing symbol: empty rows, never invented.
    missing = agent._tool_ohlcv({"market": "crypto", "symbol": "DOGE"})
    assert missing["row_count"] == 0
    assert missing["rows"] == []


def test_get_ohlcv_tool_honors_limit_and_finds_symbol_beyond_ambient_cap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Requested symbols must not be hidden by the evidence collector's ambient cap."""

    stock_root = tmp_path / "stock-ohlcv"
    # More than the collector's ambient 12-symbol cap. Under the old tool
    # implementation (question=""), ZZZZ was never selected and appeared missing.
    for suffix in "ABCDEFGHIJKLM":
        (stock_root / f"AA{suffix}").mkdir(parents=True)

    target = stock_root / "ZZZZ"
    target.mkdir(parents=True)
    rows = [
        {
            "symbol": "ZZZZ",
            "currency": "USD",
            "provider": "alpaca",
            "as_of": f"2026-09-{day:02d}T04:00:00Z",
            "open": float(day),
            "high": float(day) + 1,
            "low": float(day) - 1,
            "close": float(day) + 0.5,
            "volume": 100.0 + day,
            "source_url": "https://data.alpaca.markets/v2/stocks/bars?symbols=ZZZZ",
            "retrieved_at": "2026-09-25T00:00:00Z",
        }
        for day in range(1, 11)
    ]
    (target / "ohlcv.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )

    import yowayowa.services.ohlcv_evidence as ohlcv_module

    real_stock = ohlcv_module.collect_stock_evidence
    monkeypatch.setattr(
        ohlcv_module,
        "collect_stock_evidence",
        lambda root, question, **kwargs: real_stock(stock_root, question, **kwargs),
    )

    agent = InvestmentResearchAgent(Settings(database_url="sqlite:///:memory:"), Mock())
    result = agent._tool_ohlcv({"market": "stock", "symbol": "ZZZZ", "limit": 10})

    assert result["row_count"] == 10
    assert len(result["rows"]) == 10
    assert result["rows"][0]["as_of"] == "2026-09-10T04:00:00Z"
    assert result["rows"][-1]["as_of"] == "2026-09-01T04:00:00Z"


def test_ir_timeline_and_kpi_tools_read_local_data_with_provenance(tmp_path: Path) -> None:
    from yowayowa.services.ir_monitor_service import IrMonitorService

    def unused_transport(_source):  # type: ignore[no-untyped-def]
        raise AssertionError("read-only IR tools must never fetch source URLs")

    service = IrMonitorService(data_dir=tmp_path, transport_factory=unused_transport)
    source_url = "https://ir.example.test/release.pdf"
    provenance = {
        "provider": "ir.example.test",
        "source_url": source_url,
        "license_class": "official_public",
        "retrieved_at": "2026-09-28T08:00:00+00:00",
        "as_of": None,
    }
    service._timeline.append(
        "7203.T",
        {
            "kind": "document",
            "symbol": "7203.T",
            "recorded_at": "2026-09-28T08:00:00+00:00",
            "provenance": provenance,
            "payload": {
                "label": "Quarterly results",
                "kpis": [{"kpi": "revenue", "value": 120.0}],
            },
            "notes": [],
        },
    )
    service._kpi_history.append(
        source_url,
        [{"kpi": "revenue", "value": 100.0}],
        provenance=provenance,
    )
    service._kpi_history.append(
        source_url,
        [{"kpi": "revenue", "value": 120.0}],
        provenance=provenance,
    )

    agent = InvestmentResearchAgent(
        Settings(
            database_url="sqlite:///:memory:",
            private_acquisition_data_dir=str(tmp_path),
            mode="personal",
            private_connectors_enabled=True,
        ),
        Mock(),
    )
    timeline = agent._tool_ir_timeline({"symbol": "7203.T", "limit": 20})
    assert timeline["entry_count"] == 1
    assert timeline["entries"][0]["provenance"]["source_url"] == source_url
    assert timeline["entries"][0]["provenance"]["license_class"] == "official_public"

    history = agent._tool_ir_kpi_history({"url": source_url, "kpi": "revenue", "limit": 1})
    assert history["entry_count"] == 1
    assert history["entries"][0]["kpis"] == [{"kpi": "revenue", "value": 120.0}]
    assert history["entries"][0]["provenance"]["provider"] == "ir.example.test"


def test_ir_tools_fail_closed_when_private_connectors_are_disabled(tmp_path: Path) -> None:
    agent = InvestmentResearchAgent(
        Settings(
            database_url="sqlite:///:memory:",
            api_token="test-token",
            private_acquisition_data_dir=str(tmp_path),
            mode="public",
            private_connectors_enabled=False,
        ),
        Mock(),
    )

    assert "error" in agent._tool_ir_timeline({"symbol": "7203.T"})
    assert "error" in agent._tool_ir_kpi_history({"url": "https://example.test/doc.pdf"})


def test_edinet_tool_reads_index_with_coverage_and_provenance(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from yowayowa.services import edinet_index

    seen: dict[str, object] = {}
    response = {
        "security_code": "72030",
        "coverage_complete": False,
        "matched_count": 1,
        "provenance": {
            "provider": "edinet-v2-index",
            "source_url": "https://disclosure2.edinet-fsa.go.jp/",
        },
        "documents": [{"doc_id": "S100TEST"}],
    }

    class _History:
        def model_dump(self, *, mode: str) -> dict[str, object]:
            assert mode == "json"
            return response

    def fake_history(session, start, end, *, security_code, limit):  # type: ignore[no-untyped-def]
        seen.update(
            session=session,
            start=start,
            end=end,
            security_code=security_code,
            limit=limit,
        )
        return _History()

    monkeypatch.setattr(edinet_index, "filing_history", fake_history)
    session = Mock()
    agent = InvestmentResearchAgent(Settings(database_url="sqlite:///:memory:"), session)
    result = agent._tool_edinet_filing_history(
        {
            "symbol": "7203.T",
            "start_date": "2026-09-01",
            "end_date": "2026-09-20",
            "limit": 5,
        }
    )

    assert result == response
    assert seen == {
        "session": session,
        "start": date(2026, 9, 1),
        "end": date(2026, 9, 20),
        "security_code": "72030",
        "limit": 5,
    }

    invalid = agent._tool_edinet_filing_history({"symbol": "AAPL"})
    assert "error" in invalid


def test_ai_propose_broker_order_creates_audited_proposal_only(tmp_path: Path) -> None:
    from yowayowa.acquisition.models import AcquisitionFetchState, AuthState
    from yowayowa.broker.execution.service import BrokerExecutionDomainService
    from yowayowa.broker_models import BrokerPosition
    from yowayowa.services.broker_read_service import BrokerReadOutcome

    class FakeBrokerRead:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, bool]] = []

        def fetch(self, resource: str, market: str, *, force_refresh: bool = False):  # type: ignore[no-untyped-def]
            self.calls.append((resource, market, force_refresh))
            return BrokerReadOutcome(
                connector_id="rakuten-web",
                resource=resource,
                market=market,
                fetch_state=AcquisitionFetchState.OK,
                auth_state=AuthState.AUTHENTICATED,
                positions=[
                    BrokerPosition(
                        broker="rakuten-securities",
                        symbol="7203.T",
                        quantity=Decimal("12"),
                        currency="JPY",
                    )
                ],
                detail={"verified": True},
                source_url="https://broker.example.test/positions",
                retrieved_at="2026-09-28T10:00:00+09:00",
                as_of="2026-09-28T09:59:00+09:00",
            )

    read_service = FakeBrokerRead()
    settings = Settings(
        database_url="sqlite:///:memory:",
        mode="personal",
        private_connectors_enabled=True,
    )
    execution_service = BrokerExecutionDomainService(
        settings=settings,
        audit_dir=tmp_path / "broker-audit",
    )
    agent = InvestmentResearchAgent(
        settings,
        Mock(),
        broker_read_service=read_service,
        broker_execution_service=execution_service,
    )

    result = agent._execute_tool(
        "propose_broker_order",
        {
            "client_order_id": "ai-proposal-1",
            "symbol": "7203.T",
            "market": "jp",
            "side": "sell",
            "quantity": 5,
            "order_type": "limit",
            "limit_price": "100",
            "reference_price": "101.5",
            "currency": "JPY",
            "motivation": "Source-backed thesis review",
            "source_research_link": "/research/7203.T",
            "research_retrieved_at": "2026-09-28T10:01:00+09:00",
        },
    )

    assert result["status"] == "proposed"
    assert result["proposal"]["quantity"] == 5
    assert result["proposal"]["source_research_link"] == "/research/7203.T"
    assert result["proposal"]["provenance"]["research_retrieved_at"] == (
        "2026-09-28T01:01:00+00:00"
    )
    assert result["preview"]["estimated_notional"] == "500"
    assert result["proposal"]["provenance"]["quantity_unit"] == "shares"
    assert result["provenance"]["broker_positions_retrieved_at"] == ("2026-09-28T01:00:00+00:00")
    assert result["submitted"] is False
    assert result["cancelled"] is False
    assert "position_context" not in result
    assert "held_quantity" not in result
    assert read_service.calls == [("positions", "jp", True)]
    assert [entry.kind for entry in execution_service.audit_entries()] == ["intent"]
    assert agent.trace[-1].tool == "propose_broker_order"
    assert agent.trace[-1].mutating is True
    assert not {"submit_broker_order", "cancel_broker_order"} & set(agent.tools)


def test_ai_propose_broker_order_fails_closed_on_stale_positions(tmp_path: Path) -> None:
    from yowayowa.acquisition.models import AcquisitionFetchState, AuthState
    from yowayowa.broker.execution.service import BrokerExecutionDomainService
    from yowayowa.services.broker_read_service import BrokerReadOutcome

    read_service = Mock()
    read_service.fetch.return_value = BrokerReadOutcome(
        connector_id="rakuten-web",
        resource="positions",
        market="jp",
        fetch_state=AcquisitionFetchState.STALE,
        auth_state=AuthState.AUTHENTICATED,
        source_url="https://broker.example.test/positions",
        retrieved_at="2026-09-28T10:00:00+09:00",
        detail={"verified": True},
    )
    settings = Settings(
        database_url="sqlite:///:memory:",
        mode="personal",
        private_connectors_enabled=True,
    )
    execution_service = BrokerExecutionDomainService(
        settings=settings,
        audit_dir=tmp_path / "broker-audit",
    )
    agent = InvestmentResearchAgent(
        settings,
        Mock(),
        broker_read_service=read_service,
        broker_execution_service=execution_service,
    )
    result = agent._tool_propose_broker_order(
        {
            "client_order_id": "stale-proposal",
            "symbol": "7203.T",
            "market": "jp",
            "side": "buy",
            "quantity": 1,
            "order_type": "market",
            "reference_price": "100",
            "currency": "JPY",
            "motivation": "Stale data must block",
            "source_research_link": "/research/7203.T",
            "research_retrieved_at": "2026-09-28T10:01:00+09:00",
        }
    )

    assert "error" in result
    assert "no proposal was created" in result["error"]
    assert execution_service.audit_entries() == []


def test_ai_propose_broker_order_waits_for_verified_broker_catalog(tmp_path: Path) -> None:
    from yowayowa.acquisition.models import AcquisitionFetchState, AuthState
    from yowayowa.broker.execution.service import BrokerExecutionDomainService
    from yowayowa.services.broker_read_service import BrokerReadOutcome

    read_service = Mock()
    read_service.fetch.return_value = BrokerReadOutcome(
        connector_id="rakuten-web",
        resource="positions",
        market="jp",
        fetch_state=AcquisitionFetchState.OK,
        auth_state=AuthState.AUTHENTICATED,
        detail={"verified": False},
        source_url="https://broker.example.test/positions",
        retrieved_at="2026-09-28T10:00:00+09:00",
    )
    settings = Settings(
        database_url="sqlite:///:memory:",
        mode="personal",
        private_connectors_enabled=True,
    )
    execution_service = BrokerExecutionDomainService(
        settings=settings,
        audit_dir=tmp_path / "broker-audit",
    )
    agent = InvestmentResearchAgent(
        settings,
        Mock(),
        broker_read_service=read_service,
        broker_execution_service=execution_service,
    )
    result = agent._tool_propose_broker_order(
        {
            "client_order_id": "unverified-catalog",
            "symbol": "7203.T",
            "market": "jp",
            "side": "buy",
            "quantity": 1,
            "order_type": "market",
            "reference_price": "100",
            "currency": "JPY",
            "motivation": "Unverified positions must block",
            "source_research_link": "/research/7203.T",
            "research_retrieved_at": "2026-09-28T10:01:00+09:00",
        }
    )

    assert "verified" in result["error"]
    assert execution_service.audit_entries() == []


def test_ai_propose_broker_order_does_not_default_missing_quantity_to_zero(tmp_path: Path) -> None:
    from yowayowa.broker.execution.service import BrokerExecutionDomainService

    read_service = Mock()
    settings = Settings(
        database_url="sqlite:///:memory:",
        mode="personal",
        private_connectors_enabled=True,
    )
    execution_service = BrokerExecutionDomainService(
        settings=settings,
        audit_dir=tmp_path / "broker-audit",
    )
    agent = InvestmentResearchAgent(
        settings,
        Mock(),
        broker_read_service=read_service,
        broker_execution_service=execution_service,
    )
    result = agent._tool_propose_broker_order(
        {
            "client_order_id": "missing-quantity",
            "symbol": "7203.T",
            "market": "jp",
            "side": "buy",
            "order_type": "limit",
            "limit_price": "100",
            "currency": "JPY",
            "motivation": "A missing quantity must not be imputed",
            "source_research_link": "/research/7203.T",
            "research_retrieved_at": "2026-09-28T10:01:00+09:00",
        }
    )

    assert "error" in result
    assert "positive integer" in result["error"]
    read_service.fetch.assert_not_called()
    assert execution_service.audit_entries() == []
