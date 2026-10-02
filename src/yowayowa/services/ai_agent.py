from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

import httpx
from sqlalchemy.orm import Session

from yowayowa.acquisition.models import AcquisitionFetchState, AuthState
from yowayowa.broker.execution.models import OrderProposal
from yowayowa.broker.execution.service import OrderExecutionPreview
from yowayowa.config import Settings
from yowayowa.domain import Operation, OperationKind
from yowayowa.providers.registry import (
    fred_client,
    sec_client,
    yahoo_deep_research_provider,
    yahoo_market_provider,
    yahoo_screener_provider,
)
from yowayowa.providers.yahoo_research import YahooResearchProvider
from yowayowa.providers.yahoo_search import YahooSearchProvider
from yowayowa.research_models import (
    AIChatRequest,
    AIChatResponse,
    AIPromptPacketRequest,
    AIPromptPacketResponse,
    AIProviderConfig,
    AIToolTrace,
    MarketScreenFilter,
    MarketScreenRequest,
    ResearchSection,
)
from yowayowa.services.alerts import list_alerts
from yowayowa.services.broker_read_service import BrokerReadOutcome
from yowayowa.services.codex_cli import codex_cli_status, run_codex_structured
from yowayowa.services.comparison import compare
from yowayowa.services.portfolio_sizing import portfolio_sizing_proposals
from yowayowa.services.portfolios import get_portfolio, list_portfolios, portfolio_analytics
from yowayowa.services.screening import derived_metrics
from yowayowa.services.screening_pipeline import read_screening_candidates
from yowayowa.services.strategy_calibration import calibration_report
from yowayowa.services.strategy_outcomes import forward_outcome_report
from yowayowa.services.strategy_presets import (
    KIYOHARA_GLOBAL_ID,
    evaluate_kiyohara_candidate,
    get_builtin_strategy,
)
from yowayowa.services.strategy_sec import balance_sheet_supplement as sec_strategy_supplement
from yowayowa.services.strategy_tracking import (
    list_strategy_snapshots,
    record_strategy_snapshots,
)
from yowayowa.services.strategy_yahoo import balance_sheet_supplement as yahoo_strategy_supplement
from yowayowa.services.technical_context import read_technical_context
from yowayowa.services.valuation import valuation_snapshot
from yowayowa.services.watchlists import list_watchlists
from yowayowa.sizing_models import PortfolioSizingRequest
from yowayowa.strategy_models import StrategyCandidateInput
from yowayowa.symbols import normalize_symbol

ToolHandler = Callable[[dict[str, Any]], Any]


class _BrokerReadProvider(Protocol):
    def fetch(
        self,
        resource: str,
        market: str,
        *,
        force_refresh: bool = False,
    ) -> BrokerReadOutcome: ...


class _BrokerExecutionProvider(Protocol):
    def propose(self, **fields: object) -> OrderProposal: ...

    def preview(self, proposal: OrderProposal) -> OrderExecutionPreview: ...


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler
    mutating: bool = False


@dataclass(frozen=True)
class ResolvedAIProvider:
    provider: str
    model: str
    api_key: str
    base_url: str
    credential: str | None = None


class InvestmentResearchAgent:
    """Multi-provider BYOK agent over real Yowayowa research boundaries."""

    def __init__(
        self,
        settings: Settings,
        session: Session,
        *,
        codex_session_id: str | None = None,
        broker_read_service: _BrokerReadProvider | None = None,
        broker_execution_service: _BrokerExecutionProvider | None = None,
    ) -> None:
        self.settings = settings
        self.session = session
        self.codex_session_id = codex_session_id
        self._broker_read_service = broker_read_service
        self._broker_execution_service = broker_execution_service
        self.provider_credential: str | None = None
        self.trace: list[AIToolTrace] = []
        self.proposals: list[Operation] = []
        self.tools = self._build_tools()

    def chat(self, request: AIChatRequest) -> AIChatResponse:
        provider = self._resolve_provider(request.provider)
        self.trace = []
        self.proposals = []
        self.provider_credential = provider.credential
        if provider.provider == "anthropic":
            answer = self._anthropic_loop(provider, request)
        elif provider.provider == "codex_cli":
            answer = self._codex_loop(provider, request)
        else:
            answer = self._openai_loop(provider, request)
        return AIChatResponse(
            answer=answer.strip() or "No answer returned.",
            provider=provider.provider,
            model=provider.model,
            tool_trace=self.trace,
            proposed_operations=self.proposals,
            provider_credential=self.provider_credential,
        )

    def status(self) -> dict[str, Any]:
        anthropic_ready = bool(self.settings.anthropic_api_key and self.settings.anthropic_model)
        openai_ready = bool(
            self.settings.openai_compatible_api_key and self.settings.openai_compatible_model
        )
        codex = codex_cli_status(self.settings)
        return {
            "configured": {
                "openai_compatible": openai_ready,
                "anthropic": anthropic_ready,
            },
            "codex_cli": codex.model_dump(mode="json"),
            "tools": list(self.tools),
            "byok_per_request": True,
            "keys_persisted_by_server": False,
        }

    def _resolve_provider(
        self,
        supplied: AIProviderConfig | None,
    ) -> ResolvedAIProvider:
        if supplied is not None:
            if supplied.provider == "codex_cli":
                if not self.settings.codex_cli_enabled and not self.settings.codex_bridge_url:
                    raise RuntimeError("Codex is disabled on this deployment")
                return ResolvedAIProvider(
                    provider="codex_cli",
                    model=supplied.model,
                    api_key="",
                    base_url="",
                    credential=supplied.credential,
                )
            if supplied.provider == "anthropic":
                return ResolvedAIProvider(
                    provider="anthropic",
                    model=supplied.model,
                    api_key=supplied.api_key,
                    base_url=(supplied.base_url or "https://api.anthropic.com").rstrip("/"),
                )
            return ResolvedAIProvider(
                provider="openai_compatible",
                model=supplied.model,
                api_key=supplied.api_key,
                base_url=(supplied.base_url or "https://api.openai.com/v1").rstrip("/"),
            )
        if self.settings.openai_compatible_api_key and self.settings.openai_compatible_model:
            base_url = self.settings.openai_compatible_base_url or "https://api.openai.com/v1"
            return ResolvedAIProvider(
                provider="openai_compatible",
                model=self.settings.openai_compatible_model,
                api_key=self.settings.openai_compatible_api_key,
                base_url=base_url.rstrip("/"),
            )
        if self.settings.anthropic_api_key and self.settings.anthropic_model:
            return ResolvedAIProvider(
                provider="anthropic",
                model=self.settings.anthropic_model,
                api_key=self.settings.anthropic_api_key,
                base_url=self.settings.anthropic_base_url.rstrip("/"),
            )
        raise RuntimeError("No BYOK AI provider is configured")

    def _system_prompt(self, request: AIChatRequest) -> str:
        context = json.dumps(request.context, ensure_ascii=False, default=str)
        if len(context) > 8000:
            context = context[:8000] + "…"
        return (
            "You are the research and operation agent inside Yowayowa-Investor. "
            "Use tools for current market, company, portfolio, news, macro, analyst, "
            "ownership, insider, ESG, option, calendar, and screener facts. "
            "Never invent unavailable data. Separate sourced facts from interpretation. "
            "When the user asks to find or prioritize investment candidates rather than analyze "
            "named symbols, prefer the deterministic triage_strategy tool first, then investigate "
            "the strongest candidates with primary facts, news, events and research tools. "
            "Treat research-priority scores as attention-allocation scores, never as expected "
            "returns or autonomous buy/sell decisions. When prior snapshots exist, use "
            "get_strategy_outcomes to inspect forward results rather than assuming the scoring "
            "rules work, and check get_strategy_calibration for score-calibration evidence "
            "(deciles, hit rates, rank IC, sample-size warnings) before claiming a scoring "
            "rule works. Explain factor contributions, evidence "
            "coverage, first rejection conditions and what evidence would change the view. "
            "Prefer compact, decision-relevant comparisons over generic prose. "
            "Machine-discovered screening candidates come from the "
            "get_screening_candidates tool and are research starting points, "
            "never buy/sell recommendations. "
            "Arithmetic discipline: every number you state must come verbatim "
            "from a tool result or the supplied evidence; do not perform free-form "
            "calculations (sums, ratios, comparisons) that the tools did not "
            "already compute — quote the tool value and cite its source instead. "
            "For any requested share quantity, use propose_portfolio_sizing and quote its "
            "deterministic proposal; never calculate the share count yourself. Its result "
            "is not an order and must never be represented as one. "
            "For workspace changes, use propose_* tools; never silently mutate state. "
            "For broker orders, propose_broker_order is proposal-only: never submit or cancel. "
            "Do not invent order quantities, prices, research sources, or retrieval timestamps; "
            "if explicit sourced inputs are unavailable, do not create a proposal. "
            "State uncertainty and data basis. Answer in the user's language. "
            f"Server date: {date.today().isoformat()}. UI context: {context}"
        )

    def _openai_loop(
        self,
        provider: ResolvedAIProvider,
        request: AIChatRequest,
    ) -> str:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._system_prompt(request)},
            *[item.model_dump(mode="json") for item in request.messages],
        ]
        tools = [self._openai_tool(spec) for spec in self.tools.values()]
        headers = {"Authorization": f"Bearer {provider.api_key}"}
        endpoint = f"{provider.base_url}/chat/completions"
        for _ in range(request.max_tool_rounds):
            payload = self._post_json(
                endpoint,
                headers=headers,
                body={
                    "model": provider.model,
                    "messages": messages,
                    "tools": tools,
                    "tool_choice": "auto",
                    "temperature": 0.1,
                },
            )
            message = self._openai_message(payload)
            calls = message.get("tool_calls")
            if not isinstance(calls, list) or not calls:
                return self._string_content(message.get("content"))
            messages.append(message)
            for call in calls:
                if not isinstance(call, dict):
                    continue
                call_id = str(call.get("id") or "tool")
                function = call.get("function")
                result: Any
                if isinstance(function, dict):
                    name = str(function.get("name") or "")
                    args = self._parse_arguments(function.get("arguments"))
                    result = self._execute_tool(name, args)
                else:
                    result = {"error": "Malformed tool call"}
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": self._tool_content(result),
                    }
                )
        payload = self._post_json(
            endpoint,
            headers=headers,
            body={
                "model": provider.model,
                "messages": messages,
                "temperature": 0.1,
            },
        )
        return self._string_content(self._openai_message(payload).get("content"))

    def _codex_loop(
        self,
        provider: ResolvedAIProvider,
        request: AIChatRequest,
    ) -> str:
        observations: list[dict[str, Any]] = []
        credential = provider.credential
        for _ in range(request.max_tool_rounds):
            execution = run_codex_structured(
                self.settings,
                prompt=self._codex_prompt(request, observations, allow_tools=True),
                schema=self._codex_response_schema(),
                model=provider.model,
                credential=credential,
                session_id=self.codex_session_id,
            )
            payload = execution.result
            credential = execution.credential or credential
            self.provider_credential = credential
            raw_calls = payload.get("tool_calls")
            calls = raw_calls if isinstance(raw_calls, list) else []
            if not calls:
                answer = payload.get("answer")
                return str(answer or "")
            for raw_call in calls[:8]:
                if not isinstance(raw_call, dict):
                    continue
                name = str(raw_call.get("name") or "")
                raw_arguments = raw_call.get("arguments")
                arguments = raw_arguments if isinstance(raw_arguments, dict) else {}
                result = self._execute_tool(name, arguments)
                content = self._tool_content(result)
                if len(content) > 20000:
                    content = content[:20000] + "…"
                observations.append(
                    {
                        "tool": name,
                        "arguments": arguments,
                        "result": content,
                    }
                )
        execution = run_codex_structured(
            self.settings,
            prompt=self._codex_prompt(request, observations, allow_tools=False),
            schema=self._codex_response_schema(),
            model=provider.model,
            credential=credential,
            session_id=self.codex_session_id,
        )
        self.provider_credential = execution.credential or credential
        return str(execution.result.get("answer") or "")

    def _codex_prompt(
        self,
        request: AIChatRequest,
        observations: list[dict[str, Any]],
        *,
        allow_tools: bool,
    ) -> str:
        tools = [
            {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.parameters,
            }
            for spec in self.tools.values()
        ]
        conversation = [item.model_dump(mode="json") for item in request.messages]
        instruction = (
            "Choose zero or more Yowayowa tools needed for the next research step. "
            "If more evidence is needed, return tool_calls and set answer to null. "
            "If the evidence is sufficient, return no tool_calls and write the final answer. "
        )
        if not allow_tools:
            instruction = (
                "Do not request more tools. Synthesize the final answer from the supplied "
                "conversation and tool observations."
            )
        return "\n\n".join(
            [
                self._system_prompt(request),
                instruction,
                "You are not allowed to use shell commands, files, web search, or external tools. "
                "The only admissible fresh evidence is returned through the Yowayowa tools "
                "described below.",
                "CONVERSATION JSON:\n" + json.dumps(conversation, ensure_ascii=False, default=str),
                "AVAILABLE YOWAYOWA TOOLS JSON:\n"
                + json.dumps(tools, ensure_ascii=False, default=str),
                "TOOL OBSERVATIONS JSON:\n"
                + json.dumps(observations, ensure_ascii=False, default=str),
                (
                    "Return only the structured response requested by the output schema. "
                    "Never fabricate tool results."
                ),
            ]
        )

    def _codex_response_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "answer": {"type": ["string", "null"]},
                "tool_calls": {
                    "type": "array",
                    "maxItems": 8,
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "enum": list(self.tools),
                            },
                            "arguments": {
                                "type": "object",
                                "additionalProperties": True,
                            },
                        },
                        "required": ["name", "arguments"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["answer", "tool_calls"],
            "additionalProperties": False,
        }

    def _anthropic_loop(
        self,
        provider: ResolvedAIProvider,
        request: AIChatRequest,
    ) -> str:
        messages = [item.model_dump(mode="json") for item in request.messages]
        tools = [self._anthropic_tool(spec) for spec in self.tools.values()]
        headers = {
            "x-api-key": provider.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        endpoint = f"{provider.base_url}/v1/messages"
        for _ in range(request.max_tool_rounds):
            payload = self._post_json(
                endpoint,
                headers=headers,
                body={
                    "model": provider.model,
                    "max_tokens": 4096,
                    "system": self._system_prompt(request),
                    "messages": messages,
                    "tools": tools,
                    "temperature": 0.1,
                },
            )
            raw_content = payload.get("content")
            blocks = raw_content if isinstance(raw_content, list) else []
            tool_blocks = [
                block
                for block in blocks
                if isinstance(block, dict) and block.get("type") == "tool_use"
            ]
            if not tool_blocks:
                return self._anthropic_text(blocks)
            messages.append({"role": "assistant", "content": blocks})
            results: list[dict[str, Any]] = []
            for block in tool_blocks:
                name = str(block.get("name") or "")
                raw_input = block.get("input")
                args = raw_input if isinstance(raw_input, dict) else {}
                result = self._execute_tool(name, args)
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": str(block.get("id") or "tool"),
                        "content": self._tool_content(result),
                    }
                )
            messages.append({"role": "user", "content": results})
        payload = self._post_json(
            endpoint,
            headers=headers,
            body={
                "model": provider.model,
                "max_tokens": 4096,
                "system": self._system_prompt(request),
                "messages": messages,
                "temperature": 0.1,
            },
        )
        raw_content = payload.get("content")
        blocks = raw_content if isinstance(raw_content, list) else []
        return self._anthropic_text(blocks)

    def prompt_packet(self, request: AIPromptPacketRequest) -> AIPromptPacketResponse:
        self.trace = []
        self.proposals = []
        context = request.context
        included: list[str] = []
        evidence: dict[str, Any] = {}

        def capture(name: str, arguments: dict[str, Any]) -> Any:
            try:
                result = self._execute_tool(name, arguments)
            except Exception as exc:
                result = {"error": f"{type(exc).__name__}: {exc}"}
            evidence.setdefault(name, []).append(
                {
                    "arguments": arguments,
                    "result": result,
                }
            )
            if name not in included:
                included.append(name)
            return result

        symbols: list[str] = []
        raw_symbols = context.get("symbols")
        if isinstance(raw_symbols, list):
            symbols.extend(normalize_symbol(str(item)) for item in raw_symbols if str(item).strip())
        raw_symbol = context.get("symbol")
        if raw_symbol:
            symbols.append(normalize_symbol(str(raw_symbol)))

        strategy_id = str(context.get("strategy") or "").strip()
        region = str(context.get("region") or "").strip().lower()
        if strategy_id:
            triage = capture(
                "triage_strategy",
                {
                    "strategy_id": strategy_id,
                    "region": region or "jp",
                    "size": 12,
                },
            )
            if isinstance(triage, dict):
                evaluations = triage.get("evaluations")
                if isinstance(evaluations, list):
                    symbols.extend(
                        normalize_symbol(str(item.get("symbol")))
                        for item in evaluations[:5]
                        if isinstance(item, dict) and item.get("symbol")
                    )
            capture(
                "get_strategy_history",
                {
                    "strategy_id": strategy_id,
                    "region": region or None,
                    "limit": 30,
                },
            )
            capture(
                "get_strategy_outcomes",
                {
                    "strategy_id": strategy_id,
                    "region": region or None,
                    "horizons": [20, 60, 120],
                    "limit": 30,
                },
            )
            capture(
                "get_strategy_calibration",
                {
                    "strategy_id": strategy_id,
                    "region": region or None,
                    "horizons": [20, 60, 120],
                    "limit": 30,
                },
            )

        symbols = list(dict.fromkeys(symbols))[:6]
        if symbols:
            capture("get_quotes", {"symbols": symbols})
        for symbol in symbols:
            capture("get_fundamentals", {"symbol": symbol})
            capture("get_valuation", {"symbol": symbol})
            capture(
                "get_company_research",
                {
                    "symbol": symbol,
                    "sections": ["analyst", "ownership", "insiders", "actions"],
                },
            )
            capture("search_news", {"query": symbol, "limit": 8})
            capture(
                "get_calendar",
                {
                    "start": date.today().isoformat(),
                    "end": (date.today() + timedelta(days=45)).isoformat(),
                    "symbol": symbol,
                },
            )

        if context.get("page") == "/portfolio":
            capture("get_portfolios", {})

        conversation = [item.model_dump(mode="json") for item in request.messages]
        if request.user_prompt:
            conversation.append({"role": "user", "content": request.user_prompt})
        packet = {
            "generated_at": datetime.now(UTC).isoformat(),
            "ui_context": context,
            "conversation": conversation,
            "evidence": evidence,
        }
        task = request.user_prompt or (
            conversation[-1]["content"]
            if conversation
            else "Analyze the supplied investment evidence."
        )
        prompt = "\n\n".join(
            [
                "You are analyzing a Yowayowa-Investor research packet.",
                (
                    "Goal: produce decision-relevant investment research. Separate sourced facts, "
                    "deterministic calculations, and interpretation. Do not invent missing data. "
                    "Use provenance fields to identify the basis of claims. Treat "
                    "research-priority "
                    "scores as attention-allocation scores, not expected returns. State the first "
                    "falsifiable rejection condition and what new evidence would change the view."
                ),
                f"USER TASK:\n{task}",
                "YOWAYOWA DATA PACKET JSON:\n"
                + json.dumps(packet, ensure_ascii=False, default=str, indent=2),
                (
                    "Return a concise thesis, strongest evidence, strongest counterevidence, "
                    "valuation/quality/growth interpretation, catalysts, rejection conditions, "
                    "missing evidence, and next research actions. When multiple securities are "
                    "present, compare them explicitly."
                ),
            ]
        )
        if len(prompt) > 300000:
            prompt = prompt[:300000] + "\n\n[Packet truncated at 300,000 characters.]"
        generated_at = packet["generated_at"]
        return AIPromptPacketResponse(
            prompt=prompt,
            included_tools=included,
            generated_at=str(generated_at),
            characters=len(prompt),
        )

    def _build_tools(self) -> dict[str, ToolSpec]:
        symbol = self._object_schema({"symbol": {"type": "string"}}, ["symbol"])
        sizing_schema = PortfolioSizingRequest.model_json_schema()
        sizing_schema["properties"]["portfolio_id"] = {"type": "integer", "minimum": 1}
        sizing_schema["required"] = ["portfolio_id", *sizing_schema.get("required", [])]
        sizing_schema["additionalProperties"] = False
        specs = [
            ToolSpec(
                "search_instruments",
                "Search tickers, companies, ETFs, funds, indexes and FX.",
                self._object_schema(
                    {
                        "query": {"type": "string"},
                        "limit": {"type": "integer", "default": 12},
                    },
                    ["query"],
                ),
                self._tool_search_instruments,
            ),
            ToolSpec(
                "get_quotes",
                "Get current quotes and previous closes for up to 50 symbols.",
                self._object_schema(
                    {
                        "symbols": {
                            "type": "array",
                            "items": {"type": "string"},
                            "maxItems": 50,
                        }
                    },
                    ["symbols"],
                ),
                self._tool_quotes,
            ),
            ToolSpec(
                "get_fundamentals",
                "Get normalized statements and derived financial metrics.",
                symbol,
                self._tool_fundamentals,
            ),
            ToolSpec(
                "get_valuation",
                "Get price multiples and valuation yields with provenance.",
                symbol,
                self._tool_valuation,
            ),
            ToolSpec(
                "get_company_research",
                "Get analyst, ownership, insider, ESG, action and fund research.",
                self._object_schema(
                    {
                        "symbol": {"type": "string"},
                        "sections": {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "enum": [item.value for item in ResearchSection],
                            },
                        },
                    },
                    ["symbol"],
                ),
                self._tool_company_research,
            ),
            ToolSpec(
                "get_options",
                "Get option expirations and an optional calls/puts chain.",
                self._object_schema(
                    {
                        "symbol": {"type": "string"},
                        "expiration": {"type": "string"},
                    },
                    ["symbol"],
                ),
                self._tool_options,
            ),
            ToolSpec(
                "discover_stocks",
                "Screen global equities by valuation, growth, quality and positioning.",
                MarketScreenRequest.model_json_schema(),
                self._tool_discover,
            ),
            ToolSpec(
                "triage_strategy",
                "Run a built-in investment strategy and rank candidates with deterministic, "
                "factor-level research-priority contributions. Use before deep research when "
                "the user asks the AI to find opportunities or decide what deserves attention.",
                self._object_schema(
                    {
                        "strategy_id": {
                            "type": "string",
                            "enum": [KIYOHARA_GLOBAL_ID],
                        },
                        "region": {"type": "string"},
                        "size": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 25,
                            "default": 12,
                        },
                    },
                    ["strategy_id"],
                ),
                self._tool_triage_strategy,
            ),
            ToolSpec(
                "get_strategy_history",
                "Read point-in-time strategy research snapshots. Use this to compare today's "
                "candidate evidence with earlier AI/Discover triage runs without rewriting "
                "history.",
                self._object_schema(
                    {
                        "strategy_id": {"type": "string"},
                        "region": {"type": "string"},
                        "symbol": {"type": "string"},
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 200,
                            "default": 50,
                        },
                    }
                ),
                self._tool_strategy_history,
            ),
            ToolSpec(
                "get_strategy_outcomes",
                "Evaluate matured point-in-time strategy snapshots over forward trading-day "
                "horizons. Use to test whether research-priority scores have actually been "
                "associated with subsequent returns without using future data in the signal.",
                self._object_schema(
                    {
                        "strategy_id": {"type": "string"},
                        "region": {"type": "string"},
                        "symbol": {"type": "string"},
                        "horizons": {
                            "type": "array",
                            "items": {"type": "integer", "minimum": 1, "maximum": 500},
                            "maxItems": 6,
                        },
                        "benchmark": {"type": "string"},
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 100,
                            "default": 30,
                        },
                    }
                ),
                self._tool_strategy_outcomes,
            ),
            ToolSpec(
                "get_strategy_calibration",
                "Aggregate prior strategy outcome evidence into calibration buckets per "
                "scoring version and horizon: score/factor deciles, median/mean total and "
                "benchmark-excess returns, positive-excess hit rate, rank information "
                "coefficient, and minimum-sample warnings. Inspect this calibration "
                "evidence before claiming a scoring rule works.",
                self._object_schema(
                    {
                        "strategy_id": {"type": "string"},
                        "region": {"type": "string"},
                        "symbol": {"type": "string"},
                        "horizons": {
                            "type": "array",
                            "items": {"type": "integer", "minimum": 1, "maximum": 500},
                            "maxItems": 6,
                        },
                        "benchmark": {"type": "string"},
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 100,
                            "default": 30,
                        },
                        "oos_min_sample": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 1000,
                            "description": (
                                "Minimum number of decision-time-ordered outcomes with a "
                                "known exit_at required to run the purged walk-forward "
                                "out-of-sample evaluation (default 10)."
                            ),
                        },
                    }
                ),
                self._tool_strategy_calibration,
            ),
            ToolSpec(
                "compare_symbols",
                "Compare normalized fundamentals for 2-20 symbols.",
                self._object_schema(
                    {
                        "symbols": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 2,
                            "maxItems": 20,
                        },
                        "metrics": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    ["symbols"],
                ),
                self._tool_compare,
            ),
            ToolSpec(
                "search_news",
                "Search current financial news by ticker, company or topic.",
                self._object_schema(
                    {
                        "query": {"type": "string"},
                        "limit": {"type": "integer", "default": 10},
                    },
                    ["query"],
                ),
                self._tool_news,
            ),
            ToolSpec(
                "get_calendar",
                "Get earnings, economic, IPO, split and ticker events.",
                self._object_schema(
                    {
                        "start": {"type": "string", "format": "date"},
                        "end": {"type": "string", "format": "date"},
                        "symbol": {"type": "string"},
                    }
                ),
                self._tool_calendar,
            ),
            ToolSpec(
                "fred_search",
                "Search FRED macro series. Requires the configured FRED key.",
                self._object_schema(
                    {
                        "query": {"type": "string"},
                        "limit": {"type": "integer", "default": 20},
                    },
                    ["query"],
                ),
                self._tool_fred_search,
            ),
            ToolSpec(
                "fred_series",
                "Fetch FRED series metadata and observations.",
                self._object_schema(
                    {
                        "series_id": {"type": "string"},
                        "observation_start": {
                            "type": "string",
                            "format": "date",
                        },
                        "observation_end": {
                            "type": "string",
                            "format": "date",
                        },
                    },
                    ["series_id"],
                ),
                self._tool_fred_series,
            ),
            ToolSpec(
                "get_watchlists",
                "Read the user's watchlists.",
                self._object_schema({}),
                self._tool_watchlists,
            ),
            ToolSpec(
                "get_portfolios",
                "Read portfolios and optionally calculate live analytics.",
                self._object_schema({"portfolio_id": {"type": "integer"}}),
                self._tool_portfolios,
            ),
            ToolSpec(
                "propose_portfolio_sizing",
                "Deterministically calculate non-executable share-quantity candidates for a "
                "portfolio. Requires explicit entry/stop prices, lot sizes, currencies, and "
                "price provenance; cross-currency ideas fail closed. The supplied portfolio "
                "risk budget is split equally across ideas. This tool never creates or sends "
                "orders; do not calculate quantities outside this tool.",
                sizing_schema,
                self._tool_propose_portfolio_sizing,
            ),
            ToolSpec(
                "get_alerts",
                "Read configured price alerts.",
                self._object_schema({}),
                self._tool_alerts,
            ),
            ToolSpec(
                "propose_broker_order",
                "Create an audited broker order proposal only. This tool never submits or "
                "cancels an order. Quantity is explicit and is not calculated here. It requires "
                "a fresh, authenticated, verified broker positions snapshot for provenance, "
                "but does not expose or use holdings to calculate quantity. The operator must "
                "supply and review quantity. Include the research source and its retrieval time.",
                self._object_schema(
                    {
                        "client_order_id": {"type": "string", "minLength": 1, "maxLength": 128},
                        "symbol": {"type": "string", "minLength": 1, "maxLength": 32},
                        "market": {"type": "string", "enum": ["jp", "us"]},
                        "side": {"type": "string", "enum": ["buy", "sell"]},
                        "quantity": {"type": "integer", "minimum": 1},
                        "order_type": {"type": "string", "enum": ["market", "limit"]},
                        "limit_price": {
                            "type": "string",
                            "pattern": "^[0-9]+(?:\\.[0-9]+)?$",
                        },
                        "reference_price": {
                            "type": "string",
                            "pattern": "^[0-9]+(?:\\.[0-9]+)?$",
                        },
                        "currency": {"type": "string", "enum": ["JPY", "USD"]},
                        "motivation": {"type": "string", "minLength": 1, "maxLength": 2000},
                        "source_research_link": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 2048,
                        },
                        "research_retrieved_at": {
                            "type": "string",
                            "format": "date-time",
                        },
                    },
                    [
                        "client_order_id",
                        "symbol",
                        "market",
                        "side",
                        "quantity",
                        "order_type",
                        "currency",
                        "motivation",
                        "source_research_link",
                        "research_retrieved_at",
                    ],
                ),
                self._tool_propose_broker_order,
                mutating=True,
            ),
            ToolSpec(
                "propose_watchlist_change",
                "Propose a transparent watchlist add/remove operation.",
                self._object_schema(
                    {
                        "action": {
                            "type": "string",
                            "enum": ["add", "remove"],
                        },
                        "symbols": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    ["action", "symbols"],
                ),
                self._tool_propose_watchlist,
                mutating=True,
            ),
            ToolSpec(
                "propose_compare",
                "Propose opening comparison for selected symbols.",
                self._object_schema(
                    {
                        "symbols": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 2,
                        }
                    },
                    ["symbols"],
                ),
                self._tool_propose_compare,
                mutating=True,
            ),
            ToolSpec(
                "propose_screen_filters",
                "Propose transparent filters for the Yowayowa screener.",
                self._object_schema(
                    {
                        "symbols": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "filters": {
                            "type": "array",
                            "items": {"type": "object"},
                        },
                    },
                    ["symbols", "filters"],
                ),
                self._tool_propose_screen,
                mutating=True,
            ),
            ToolSpec(
                "get_screening_candidates",
                "Get today's machine-discovered screening candidates with reasons and provenance.",
                self._object_schema(
                    {
                        "run_date": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                ),
                self._tool_screening_candidates,
            ),
            ToolSpec(
                "get_macro_series",
                "Read the locally persisted macro observations "
                "(BLS / FRED / Treasury JSONL). Returns the latest value per "
                "series with retrieved_at; quote values verbatim, never "
                "recompute them.",
                self._object_schema(
                    {
                        "source": {
                            "type": "string",
                            "enum": ["bls", "fred", "treasury"],
                            "description": "Omit to read every source's latest values.",
                        },
                        "series_id": {"type": "string"},
                    },
                ),
                self._tool_macro_series,
            ),
            ToolSpec(
                "get_technical_context",
                "Read calculated indicator series from the supported market-history "
                "provider (technical.py), with provider/as-of provenance. Returns indicator "
                "values only; do not recompute indicators from raw OHLCV. Missing values "
                "remain null and unavailable history is marked missing.",
                self._object_schema(
                    {
                        "symbol": {"type": "string"},
                        "period": {
                            "type": "string",
                            "pattern": "^(?:[0-9]+(?:d|mo|y)|max)$",
                            "default": "1y",
                        },
                        "interval": {"type": "string", "maxLength": 10, "default": "1d"},
                        "indicators": {
                            "type": "array",
                            "items": {"type": "string"},
                            "maxItems": 12,
                            "description": "technical.py tokens; default: sma20,rsi14.",
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 60,
                            "default": 10,
                            "description": "Newest indicator points to return per series.",
                        },
                    },
                    ["symbol"],
                ),
                self._tool_technical_context,
            ),
            ToolSpec(
                "get_ohlcv",
                "Read the locally persisted daily OHLCV JSONL stores "
                "(US stocks under data/stock-ohlcv, crypto under "
                "data/crypto-ohlcv). Returns saved daily bars (open/high/low/"
                "close/volume) with provider and as-of provenance for one "
                "symbol; quote values verbatim, never recompute them. If a "
                "symbol has no persisted rows the answer is 未取得 — do not "
                "invent prices.",
                self._object_schema(
                    {
                        "market": {
                            "type": "string",
                            "enum": ["stock", "crypto"],
                        },
                        "symbol": {"type": "string"},
                        "limit": {
                            "type": "integer",
                            "description": "Newest-first rows to return (1-60, default 10).",
                        },
                    },
                    ["market", "symbol"],
                ),
                self._tool_ohlcv,
            ),
            ToolSpec(
                "get_ir_timeline",
                "Read locally monitored IR documents for one instrument. Entries include "
                "source URL, provider, license class, retrieval time, and extracted KPI changes. "
                "This is read-only and never triggers an IR fetch.",
                self._object_schema(
                    {
                        "symbol": {"type": "string"},
                        "kind": {"type": "string"},
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 200,
                            "default": 50,
                        },
                    },
                    ["symbol"],
                ),
                self._tool_ir_timeline,
            ),
            ToolSpec(
                "get_ir_kpi_history",
                "Read locally recorded KPI revisions for an IR document URL. Each observation "
                "preserves source URL and, when recorded, provider/license/retrieval provenance. "
                "This is read-only and never fetches the URL.",
                self._object_schema(
                    {
                        "url": {"type": "string", "minLength": 1, "maxLength": 2048},
                        "kpi": {"type": "string", "maxLength": 64},
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 200,
                            "default": 10,
                        },
                    },
                    ["url"],
                ),
                self._tool_ir_kpi_history,
            ),
            ToolSpec(
                "get_edinet_filing_history",
                "Read the locally indexed official EDINET filing history for a Japanese "
                "security code or .T symbol. Returns explicit index coverage and EDINET "
                "provenance; it does not call EDINET or synchronize data.",
                self._object_schema(
                    {
                        "symbol": {"type": "string"},
                        "start_date": {"type": "string", "format": "date"},
                        "end_date": {"type": "string", "format": "date"},
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 100,
                            "default": 20,
                        },
                    },
                    ["symbol"],
                ),
                self._tool_edinet_filing_history,
            ),
            ToolSpec(
                "get_orderbook",
                "Read the Level-2 orderbook (market depth ladder, top bids/asks, "
                "spread, order flow imbalance, and HFT activity indicator) for a given symbol.",
                self._object_schema(
                    {
                        "symbol": {"type": "string"},
                        "depth": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 50,
                            "default": 10,
                        },
                    },
                    ["symbol"],
                ),
                self._tool_get_orderbook,
            ),
            ToolSpec(
                "estimate_orderbook_impact",
                "Simulate a market order walking the Level-2 orderbook to estimate "
                "fill price, slippage, and market impact bps.",
                self._object_schema(
                    {
                        "symbol": {"type": "string"},
                        "side": {"type": "string", "enum": ["buy", "sell"]},
                        "quantity": {"type": "number", "minimum": 0.0001},
                    },
                    ["symbol", "side", "quantity"],
                ),
                self._tool_estimate_orderbook_impact,
            ),
        ]
        return {item.name: item for item in specs}

    def _execute_tool(self, name: str, args: dict[str, Any]) -> Any:
        spec = self.tools.get(name)
        if spec is None:
            result: Any = {"error": f"Unknown tool: {name}"}
            self._record_trace(name, args, result, False)
            return result
        try:
            result = spec.handler(args)
        except Exception as exc:
            result = {"error": f"{type(exc).__name__}: {exc}"}
        self._record_trace(name, args, result, spec.mutating)
        return result

    def _record_trace(
        self,
        name: str,
        args: dict[str, Any],
        result: Any,
        mutating: bool,
    ) -> None:
        preview = self._tool_content(result)
        if len(preview) > 1200:
            preview = preview[:1200] + "…"
        self.trace.append(
            AIToolTrace(
                tool=name,
                arguments=args,
                result_preview=preview,
                mutating=mutating,
            )
        )

    def _tool_search_instruments(self, args: dict[str, Any]) -> Any:
        query = str(args.get("query") or "").strip()
        limit = min(max(int(args.get("limit") or 12), 1), 30)
        yahoo = YahooSearchProvider(self.settings).search(query, limit)
        official = []
        with suppress(Exception):
            official = sec_client().search(query, limit)
        merged = {item.symbol: item for item in yahoo}
        for item in official:
            merged[item.symbol] = item
        return [item.model_dump(mode="json") for item in list(merged.values())[:limit]]

    def _tool_quotes(self, args: dict[str, Any]) -> Any:
        symbols = [normalize_symbol(str(item)) for item in args.get("symbols", [])][:50]
        return yahoo_market_provider().quotes(symbols).model_dump(mode="json")

    def _tool_fundamentals(self, args: dict[str, Any]) -> Any:
        symbol = normalize_symbol(str(args.get("symbol") or ""))
        facts = sec_client().company_facts(symbol)
        recent: dict[str, Any] = {}
        for key, metric in facts.metrics.items():
            points = sorted(metric.points, key=lambda item: item.period_end)[-4:]
            recent[key] = [item.model_dump(mode="json") for item in points]
        return {
            "symbol": facts.symbol,
            "company_name": facts.company_name,
            "derived_metrics": derived_metrics(facts),
            "recent_series": recent,
            "provenance": facts.provenance.model_dump(mode="json"),
        }

    def _tool_valuation(self, args: dict[str, Any]) -> Any:
        symbol = normalize_symbol(str(args.get("symbol") or ""))
        facts = sec_client().company_facts(symbol)
        quotes = yahoo_market_provider().quotes([symbol])
        return valuation_snapshot(facts, quotes).model_dump(mode="json")

    def _tool_company_research(self, args: dict[str, Any]) -> Any:
        symbol = normalize_symbol(str(args.get("symbol") or ""))
        raw_sections = args.get("sections")
        sections = None
        if isinstance(raw_sections, list) and raw_sections:
            sections = [ResearchSection(str(item)) for item in raw_sections]
        result = yahoo_deep_research_provider().research(symbol, sections)
        return result.model_dump(mode="json")

    def _tool_options(self, args: dict[str, Any]) -> Any:
        symbol = normalize_symbol(str(args.get("symbol") or ""))
        raw_expiration = args.get("expiration")
        expiration = str(raw_expiration) if raw_expiration else None
        result = yahoo_deep_research_provider().option_chain(symbol, expiration)
        payload = result.model_dump(mode="json")
        payload["calls"] = payload.get("calls", [])[:60]
        payload["puts"] = payload.get("puts", [])[:60]
        return payload

    def _tool_discover(self, args: dict[str, Any]) -> Any:
        payload = MarketScreenRequest.model_validate(args)
        if payload.size > 50:
            payload = payload.model_copy(update={"size": 50})
        result = yahoo_screener_provider().screen(payload)
        return result.model_dump(mode="json")

    def _tool_triage_strategy(self, args: dict[str, Any]) -> Any:
        strategy_id = str(args.get("strategy_id") or "").strip()
        strategy = get_builtin_strategy(strategy_id)
        region = str(args.get("region") or strategy.default_region).strip().lower()
        size = min(max(int(args.get("size") or 12), 1), 25)
        filters = list(strategy.discovery.filters)
        if strategy.region_required:
            filters = [
                MarketScreenFilter(field="region", operator="is-in", value=[region]),
                *filters,
            ]
        screen_request = strategy.discovery.model_copy(
            update={
                "filters": filters,
                "size": size,
                "offset": 0,
            }
        )
        screen = yahoo_screener_provider().screen(screen_request)

        def numeric(row: dict[str, Any], *keys: str) -> float | None:
            for key in keys:
                raw = row.get(key)
                if isinstance(raw, (int, float)):
                    return float(raw)
            return None

        evaluations = []
        errors: dict[str, str] = {}
        for row in screen.quotes:
            symbol = normalize_symbol(str(row.get("symbol") or ""))
            market_cap = numeric(row, "marketCap", "intradaymarketcap")
            if not symbol or market_cap is None or market_cap <= 0:
                continue
            pe_ratio = numeric(row, "trailingPE", "peratio.lasttwelvemonths")
            if pe_ratio is not None and pe_ratio <= 0:
                pe_ratio = None
            try:
                facts = sec_client().company_facts(symbol)
                supplement = sec_strategy_supplement(facts) or yahoo_strategy_supplement(facts)
                evaluation = evaluate_kiyohara_candidate(
                    facts,
                    StrategyCandidateInput(
                        symbol=symbol,
                        market_cap=market_cap,
                        pe_ratio=pe_ratio,
                    ),
                    supplement,
                )
                evaluations.append(evaluation)
            except Exception as exc:
                errors[symbol] = f"{type(exc).__name__}: {exc}"

        evaluations.sort(
            key=lambda item: (
                -(item.research_priority.score if item.research_priority is not None else -1.0),
                item.symbol,
            )
        )
        snapshots = record_strategy_snapshots(
            self.session,
            strategy.id,
            region,
            evaluations,
        )
        return {
            "strategy_id": strategy.id,
            "strategy_name": strategy.name_en,
            "region": region,
            "evaluations": [item.model_dump(mode="json") for item in evaluations],
            "errors": errors,
            "snapshot_ids": [item.id for item in snapshots],
            "screen_provenance": screen.provenance.model_dump(mode="json"),
            "notes": [
                "Research-priority score is deterministic and interpretable; it is not an "
                "expected-return forecast.",
                "The AI should deep-research only the strongest candidates and actively test "
                "their first rejection conditions.",
                "This AI tool uses SEC exact enrichment when available and otherwise preserves "
                "the conservative Yahoo lower-bound semantics.",
            ],
        }

    def _tool_strategy_history(self, args: dict[str, Any]) -> Any:
        strategy_id = str(args.get("strategy_id") or "").strip() or None
        region = str(args.get("region") or "").strip() or None
        symbol = str(args.get("symbol") or "").strip() or None
        limit = min(max(int(args.get("limit") or 50), 1), 200)
        return [
            item.model_dump(mode="json")
            for item in list_strategy_snapshots(
                self.session,
                strategy_id=strategy_id,
                region=region,
                symbol=symbol,
                limit=limit,
            )
        ]

    def _tool_strategy_outcomes(self, args: dict[str, Any]) -> Any:
        strategy_id = str(args.get("strategy_id") or "").strip() or None
        region = str(args.get("region") or "").strip() or None
        symbol = str(args.get("symbol") or "").strip() or None
        raw_horizons = args.get("horizons")
        horizons = (
            [int(item) for item in raw_horizons]
            if isinstance(raw_horizons, list) and raw_horizons
            else [20, 60, 120]
        )
        raw_benchmark = str(args.get("benchmark") or "").strip()
        benchmark = normalize_symbol(raw_benchmark) if raw_benchmark else None
        limit = min(max(int(args.get("limit") or 30), 1), 100)
        snapshots = list_strategy_snapshots(
            self.session,
            strategy_id=strategy_id,
            region=region,
            symbol=symbol,
            limit=limit,
        )
        return forward_outcome_report(
            snapshots,
            yahoo_market_provider(),
            horizons=horizons,
            benchmark_symbol=benchmark,
        ).model_dump(mode="json")

    def _tool_strategy_calibration(self, args: dict[str, Any]) -> Any:
        strategy_id = str(args.get("strategy_id") or "").strip() or None
        region = str(args.get("region") or "").strip() or None
        symbol = str(args.get("symbol") or "").strip() or None
        raw_horizons = args.get("horizons")
        horizons = (
            [int(item) for item in raw_horizons]
            if isinstance(raw_horizons, list) and raw_horizons
            else [20, 60, 120]
        )
        raw_benchmark = str(args.get("benchmark") or "").strip()
        benchmark = normalize_symbol(raw_benchmark) if raw_benchmark else None
        limit = min(max(int(args.get("limit") or 30), 1), 100)
        raw_oos_min_sample = args.get("oos_min_sample")
        oos_min_sample = (
            min(max(int(raw_oos_min_sample), 1), 1000) if raw_oos_min_sample is not None else None
        )
        snapshots = list_strategy_snapshots(
            self.session,
            strategy_id=strategy_id,
            region=region,
            symbol=symbol,
            limit=limit,
        )
        report = forward_outcome_report(
            snapshots,
            yahoo_market_provider(),
            horizons=horizons,
            benchmark_symbol=benchmark,
        )
        return calibration_report(
            snapshots,
            report,
            oos_min_sample=oos_min_sample,
        ).model_dump(mode="json")

    def _tool_compare(self, args: dict[str, Any]) -> Any:
        symbols = [normalize_symbol(str(item)) for item in args.get("symbols", [])][:20]
        raw_metrics = args.get("metrics")
        metrics = [str(item) for item in raw_metrics] if isinstance(raw_metrics, list) else None
        facts = [sec_client().company_facts(symbol) for symbol in symbols]
        return compare(facts, metrics).model_dump(mode="json")

    def _tool_news(self, args: dict[str, Any]) -> Any:
        query = str(args.get("query") or "").strip()
        limit = min(max(int(args.get("limit") or 10), 1), 20)
        result = YahooResearchProvider(self.settings).news(query, limit)
        return result.model_dump(mode="json")

    def _tool_calendar(self, args: dict[str, Any]) -> Any:
        start = self._date_arg(args.get("start")) or date.today()
        end = self._date_arg(args.get("end")) or (start + timedelta(days=14))
        if (end - start).days > 93:
            end = start + timedelta(days=93)
        raw_symbol = args.get("symbol")
        symbol = normalize_symbol(str(raw_symbol)) if raw_symbol else None
        result = YahooResearchProvider(self.settings).calendar(
            start,
            end,
            symbol=symbol,
            limit=100,
        )
        return result.model_dump(mode="json")

    def _tool_fred_search(self, args: dict[str, Any]) -> Any:
        query = str(args.get("query") or "").strip()
        limit = min(max(int(args.get("limit") or 20), 1), 50)
        return fred_client().search(query, limit)

    def _tool_fred_series(self, args: dict[str, Any]) -> Any:
        series_id = str(args.get("series_id") or "").strip().upper()
        start = self._date_arg(args.get("observation_start"))
        end = self._date_arg(args.get("observation_end"))
        return fred_client().series(
            series_id,
            limit=5000,
            observation_start=start,
            observation_end=end,
        )

    def _tool_watchlists(self, _: dict[str, Any]) -> Any:
        return [item.model_dump(mode="json") for item in list_watchlists(self.session)]

    def _tool_screening_candidates(self, args: dict[str, Any]) -> Any:
        run_date = self._date_arg(args.get("run_date"))
        raw_limit = args.get("limit")
        limit = min(max(int(raw_limit), 1), 100) if raw_limit is not None else 50
        return read_screening_candidates(
            self.session,
            run_date=run_date,
            limit=limit,
        )

    def _tool_macro_series(self, args: dict[str, Any]) -> Any:
        from yowayowa.services.macro_store import MACRO_SOURCE_KINDS, default_macro_store

        raw_source = str(args.get("source") or "").strip().lower()
        raw_series = str(args.get("series_id") or "").strip()
        store = default_macro_store()
        if raw_source:
            if raw_source not in MACRO_SOURCE_KINDS:
                return {"error": f"Unknown macro source: {raw_source}"}
            rows, coverage = store.read(raw_source, series_id=raw_series or None)
            return {"observations": rows, "coverage": coverage}
        latest = store.latest_by_series()
        if raw_series:
            latest = [row for row in latest if row.get("series_id") == raw_series]
        return {
            "latest_by_series": latest,
            "coverage": {"series_count": len(latest), "sources": list(MACRO_SOURCE_KINDS)},
        }

    def _tool_ohlcv(self, args: dict[str, Any]) -> Any:
        from pathlib import Path

        from yowayowa.services.ohlcv_evidence import collect_crypto_evidence

        market = str(args.get("market") or "").strip().lower()
        if market not in ("stock", "crypto"):
            return {"error": f"Unknown market: {market} (expected 'stock' or 'crypto')"}
        symbol = str(args.get("symbol") or "").strip().upper()
        raw_limit = args.get("limit")
        try:
            limit = min(max(int(raw_limit), 1), 60) if raw_limit is not None else 10
        except (TypeError, ValueError):
            return {"error": f"Invalid limit: {raw_limit!r}"}
        if market == "stock":
            from yowayowa.services.ohlcv_evidence import collect_stock_evidence

            evidence = collect_stock_evidence(
                Path("./data/stock-ohlcv"),
                symbol,
                rows_per_symbol=limit,
                max_symbols=1,
            )
        else:
            evidence = collect_crypto_evidence(
                Path("./data/crypto-ohlcv"),
                symbol,
                rows_per_symbol=limit,
                max_symbols=1,
            )
        symbol_evidence = evidence["symbols"].get(symbol)
        if symbol_evidence is None:
            return {
                "symbol": symbol,
                "market": market,
                "row_count": 0,
                "rows": [],
                "available_symbols": evidence["available_symbols"],
            }
        return {
            "symbol": symbol,
            "market": market,
            "row_count": symbol_evidence["row_count"],
            "rows": symbol_evidence["latest_rows"][:limit],
        }

    def _tool_ir_timeline(self, args: dict[str, Any]) -> Any:
        symbol = normalize_symbol(str(args.get("symbol") or ""))
        if self.settings.mode != "personal" or not self.settings.private_connectors_enabled:
            return {
                "error": "IR monitoring data is unavailable while private connectors are disabled."
            }
        from pathlib import Path

        from yowayowa.services.ir_monitor_service import (
            IrMonitorService,
            _default_http_transport,
        )

        service = IrMonitorService(
            data_dir=Path(self.settings.private_acquisition_data_dir),
            transport_factory=_default_http_transport,
        )
        kind = str(args.get("kind") or "").strip() or None
        raw_limit = args.get("limit")
        try:
            limit = min(max(int(raw_limit), 1), 200) if raw_limit is not None else 50
        except (TypeError, ValueError):
            return {"error": f"Invalid limit: {raw_limit!r}"}
        entries = service.timeline(symbol, kind=kind, limit=limit)
        return {"symbol": symbol, "entry_count": len(entries), "entries": entries}

    def _tool_ir_kpi_history(self, args: dict[str, Any]) -> Any:
        if self.settings.mode != "personal" or not self.settings.private_connectors_enabled:
            return {
                "error": "IR monitoring data is unavailable while private connectors are disabled."
            }
        url = str(args.get("url") or "").strip()
        if not url:
            return {"error": "A document URL is required."}
        from pathlib import Path

        from yowayowa.services.ir_monitor_service import (
            IrMonitorService,
            _default_http_transport,
        )

        service = IrMonitorService(
            data_dir=Path(self.settings.private_acquisition_data_dir),
            transport_factory=_default_http_transport,
        )
        kpi = str(args.get("kpi") or "").strip() or None
        raw_limit = args.get("limit")
        try:
            limit = min(max(int(raw_limit), 1), 200) if raw_limit is not None else 10
        except (TypeError, ValueError):
            return {"error": f"Invalid limit: {raw_limit!r}"}
        entries = service.document_kpi_history(url, kpi=kpi, limit=limit)
        return {"url": url, "entry_count": len(entries), "entries": entries}

    def _tool_edinet_filing_history(self, args: dict[str, Any]) -> Any:
        from yowayowa.services.edinet import normalize_security_code
        from yowayowa.services.edinet_index import filing_history

        raw_symbol = str(args.get("symbol") or "").strip().upper()
        security_code = raw_symbol[:-2] if raw_symbol.endswith(".T") else raw_symbol
        try:
            security_code = normalize_security_code(security_code)
        except ValueError as exc:
            return {"error": str(exc)}

        raw_start = args.get("start_date")
        raw_end = args.get("end_date")
        parsed_start = self._date_arg(raw_start)
        parsed_end = self._date_arg(raw_end)
        if raw_start and parsed_start is None:
            return {"error": "start_date must be an ISO date."}
        if raw_end and parsed_end is None:
            return {"error": "end_date must be an ISO date."}
        end = parsed_end or datetime.now(UTC).date()
        start = parsed_start or (end - timedelta(days=730))
        raw_limit = args.get("limit")
        try:
            limit = min(max(int(raw_limit), 1), 100) if raw_limit is not None else 20
        except (TypeError, ValueError):
            return {"error": f"Invalid limit: {raw_limit!r}"}
        result = filing_history(
            self.session,
            start,
            end,
            security_code=security_code,
            limit=limit,
        )
        return result.model_dump(mode="json")

    def _tool_technical_context(self, args: dict[str, Any]) -> Any:
        raw_indicators = args.get("indicators")
        indicators = (
            [str(token) for token in raw_indicators] if isinstance(raw_indicators, list) else None
        )
        raw_limit = args.get("limit", 10)
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid technical-context limit: {raw_limit!r}") from exc
        return read_technical_context(
            yahoo_market_provider(),
            symbol=str(args.get("symbol") or ""),
            period=str(args.get("period") or "1y"),
            interval=str(args.get("interval") or "1d"),
            indicators=indicators,
            limit=limit,
        )

    def _tool_portfolios(self, args: dict[str, Any]) -> Any:
        portfolio_id = args.get("portfolio_id")
        if portfolio_id is None:
            return [item.model_dump(mode="json") for item in list_portfolios(self.session)]
        portfolio = get_portfolio(self.session, int(portfolio_id))
        result = portfolio_analytics(portfolio, yahoo_market_provider())
        return result.model_dump(mode="json")

    def _tool_propose_portfolio_sizing(self, args: dict[str, Any]) -> Any:
        portfolio_id = int(args["portfolio_id"])
        request = PortfolioSizingRequest.model_validate(
            {
                "ideas": args.get("ideas"),
                "risk_budget_pct": args.get("risk_budget_pct"),
                "max_position_pct": args.get("max_position_pct"),
            }
        )
        portfolio = get_portfolio(self.session, portfolio_id)
        valuation = portfolio_analytics(portfolio, yahoo_market_provider())
        result = portfolio_sizing_proposals(portfolio, valuation, request)
        return result.model_dump(mode="json")

    def _tool_alerts(self, _: dict[str, Any]) -> Any:
        return [item.model_dump(mode="json") for item in list_alerts(self.session)]

    def _tool_propose_watchlist(self, args: dict[str, Any]) -> Any:
        action = str(args.get("action") or "add")
        symbols = [normalize_symbol(str(item)) for item in args.get("symbols", [])]
        kind = OperationKind.WATCHLIST_REMOVE if action == "remove" else OperationKind.WATCHLIST_ADD
        operation = Operation(kind=kind, arguments={"symbols": symbols})
        return self._proposal(operation)

    def _tool_propose_compare(self, args: dict[str, Any]) -> Any:
        symbols = [normalize_symbol(str(item)) for item in args.get("symbols", [])]
        operation = Operation(
            kind=OperationKind.COMPARE_SYMBOLS,
            arguments={"symbols": symbols},
        )
        return self._proposal(operation)

    def _tool_propose_screen(self, args: dict[str, Any]) -> Any:
        symbols = [normalize_symbol(str(item)) for item in args.get("symbols", [])]
        raw_filters = args.get("filters")
        filters = raw_filters if isinstance(raw_filters, list) else []
        operation = Operation(
            kind=OperationKind.SCREEN_SET_FILTERS,
            arguments={"symbols": symbols, "filters": filters},
        )
        return self._proposal(operation)

    def _tool_propose_broker_order(self, args: dict[str, Any]) -> Any:
        """Persist a proposal after fail-closed read-only provenance checks."""

        if self.settings.mode != "personal" or not self.settings.private_connectors_enabled:
            return {
                "error": ("Broker proposals are unavailable while private connectors are disabled.")
            }

        market = str(args.get("market") or "").strip().lower()
        if market not in {"jp", "us"}:
            return {"error": "market must be 'jp' or 'us'."}
        currency = str(args.get("currency") or "").strip().upper()
        expected_currency = "JPY" if market == "jp" else "USD"
        if currency != expected_currency:
            return {"error": f"currency must be {expected_currency} for the {market} market."}

        symbol_raw = str(args.get("symbol") or "").strip()
        try:
            symbol = normalize_symbol(symbol_raw)
        except ValueError as exc:
            return {"error": str(exc)}

        client_order_id = str(args.get("client_order_id") or "").strip()
        motivation = str(args.get("motivation") or "").strip()
        source_research_link = str(args.get("source_research_link") or "").strip()
        if not client_order_id or not motivation or not source_research_link:
            return {"error": "client_order_id, motivation, and source_research_link are required."}
        if len(client_order_id) > 128 or len(motivation) > 2000 or len(source_research_link) > 2048:
            return {"error": "A proposal field exceeds its supported maximum length."}
        if any(ord(character) < 32 for character in source_research_link):
            return {"error": "source_research_link contains control characters."}

        research_retrieved_at = self._aware_timestamp(args.get("research_retrieved_at"))
        if research_retrieved_at is None:
            return {"error": "research_retrieved_at must be an ISO timestamp with a timezone."}

        raw_quantity = args.get("quantity")
        if isinstance(raw_quantity, bool) or not isinstance(raw_quantity, int) or raw_quantity <= 0:
            return {"error": "quantity must be a positive integer; missing quantity is not zero."}

        side = str(args.get("side") or "").strip().lower()
        order_type = str(args.get("order_type") or "").strip().lower()
        if side not in {"buy", "sell"} or order_type not in {"market", "limit"}:
            return {"error": "side must be buy/sell and order_type must be market/limit."}

        prices: dict[str, Decimal | None] = {}
        for field in ("limit_price", "reference_price"):
            raw_price = args.get(field)
            if raw_price is None:
                prices[field] = None
                continue
            if not isinstance(raw_price, str) or not raw_price.strip():
                return {"error": f"{field} must be an exact positive decimal string."}
            try:
                price = Decimal(raw_price)
            except InvalidOperation:
                return {"error": f"{field} must be an exact positive decimal string."}
            if not price.is_finite() or price <= 0:
                return {"error": f"{field} must be a finite positive amount."}
            prices[field] = price
        limit_price = prices["limit_price"]
        reference_price = prices["reference_price"]
        if order_type == "limit" and limit_price is None:
            return {"error": "limit_price is required for limit orders."}
        if order_type == "market" and reference_price is None:
            return {"error": "reference_price is required for market-order notional preview."}

        outcome = self._get_broker_read_service().fetch("positions", market, force_refresh=True)
        if outcome.resource != "positions" or outcome.market != market:
            return {
                "error": (
                    "Broker positions response does not match the requested market; "
                    "no proposal was created."
                )
            }
        if outcome.fetch_state != AcquisitionFetchState.OK:
            return {
                "error": (
                    "Broker positions could not be freshly verified; no proposal was created."
                )
            }
        if outcome.auth_state != AuthState.AUTHENTICATED:
            return {
                "error": "Broker positions could not be freshly verified; no proposal was created."
            }
        if outcome.detail is None or outcome.detail.get("verified") is not True:
            return {"error": "Broker positions catalog is not verified; no proposal was created."}
        if outcome.notes:
            return {
                "error": (
                    "Broker positions contain parser or coverage notes; no proposal was created."
                )
            }
        broker_retrieved_at = self._aware_timestamp(outcome.retrieved_at)
        if not outcome.source_url or broker_retrieved_at is None:
            return {"error": "Broker positions provenance is incomplete; no proposal was created."}
        broker_as_of = self._aware_timestamp(outcome.as_of, optional=True)
        if outcome.as_of is not None and broker_as_of is None:
            return {
                "error": ("Broker positions as_of timestamp is invalid; no proposal was created.")
            }

        provenance = {
            "quantity_unit": "shares",
            "research_retrieved_at": research_retrieved_at.isoformat(),
            "broker_positions_source_url": outcome.source_url,
            "broker_positions_retrieved_at": broker_retrieved_at.isoformat(),
            "broker_positions_market": market,
        }
        if broker_as_of is not None:
            provenance["broker_positions_as_of"] = broker_as_of.isoformat()
        execution_service = self._get_broker_execution_service()
        proposal = execution_service.propose(
            client_order_id=client_order_id,
            symbol=symbol,
            market=market,
            side=side,
            quantity=raw_quantity,
            order_type=order_type,
            limit_price=limit_price,
            reference_price=reference_price,
            currency=currency,
            motivation=motivation,
            source_research_link=source_research_link,
            provenance=provenance,
        )
        return {
            "status": "proposed",
            "proposal": proposal.model_dump(mode="json"),
            "proposal_hash": proposal.proposal_hash(),
            "preview": execution_service.preview(proposal).model_dump(mode="json"),
            "provenance": {
                "quantity_unit": "shares",
                "source_research_link": source_research_link,
                "research_retrieved_at": research_retrieved_at.isoformat(),
                "broker_positions_source_url": outcome.source_url,
                "broker_positions_retrieved_at": broker_retrieved_at.isoformat(),
                "broker_positions_as_of": broker_as_of.isoformat() if broker_as_of else None,
            },
            "submitted": False,
            "cancelled": False,
            "notes": [
                "Quantity is operator-supplied and not sized or checked against holdings.",
                "Review the full broker state before any later evaluation or execution.",
            ],
        }

    def _get_broker_read_service(self) -> _BrokerReadProvider:
        if self._broker_read_service is None:
            from yowayowa.api.deps import get_broker_read_service

            self._broker_read_service = get_broker_read_service()
        return self._broker_read_service

    def _get_broker_execution_service(self) -> _BrokerExecutionProvider:
        if self._broker_execution_service is None:
            from yowayowa.api.deps import get_broker_execution_service

            self._broker_execution_service = get_broker_execution_service()
        return self._broker_execution_service

    @staticmethod
    def _aware_timestamp(value: Any, *, optional: bool = False) -> datetime | None:
        if value is None and optional:
            return None
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
        else:
            return None
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(UTC)

    def _proposal(self, operation: Operation) -> dict[str, Any]:
        self.proposals.append(operation)
        return {
            "proposed": operation.model_dump(mode="json"),
            "executed": False,
        }

    @staticmethod
    def _object_schema(
        properties: dict[str, Any],
        required: list[str] | None = None,
    ) -> dict[str, Any]:
        schema: dict[str, Any] = {
            "type": "object",
            "properties": properties,
            "additionalProperties": False,
        }
        if required:
            schema["required"] = required
        return schema

    @staticmethod
    def _openai_tool(spec: ToolSpec) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.parameters,
            },
        }

    @staticmethod
    def _anthropic_tool(spec: ToolSpec) -> dict[str, Any]:
        return {
            "name": spec.name,
            "description": spec.description,
            "input_schema": spec.parameters,
        }

    def _post_json(
        self,
        url: str,
        *,
        headers: dict[str, str],
        body: dict[str, Any],
    ) -> dict[str, Any]:
        response = httpx.post(
            url,
            headers=headers,
            json=body,
            timeout=self.settings.request_timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("AI provider returned a non-object response")
        return payload

    @staticmethod
    def _openai_message(payload: dict[str, Any]) -> dict[str, Any]:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise RuntimeError("OpenAI-compatible provider returned no choices")
        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise RuntimeError("OpenAI-compatible provider returned no message")
        return message

    @staticmethod
    def _parse_arguments(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        if not isinstance(value, str) or not value.strip():
            return {}
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {"_raw": value}
        return parsed if isinstance(parsed, dict) else {"_value": parsed}

    @staticmethod
    def _string_content(value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            parts = [
                str(item.get("text"))
                for item in value
                if isinstance(item, dict)
                and item.get("type") == "text"
                and isinstance(item.get("text"), str)
            ]
            return "\n".join(parts)
        return ""

    @staticmethod
    def _anthropic_text(blocks: list[Any]) -> str:
        parts = [
            str(block.get("text"))
            for block in blocks
            if isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        ]
        return "\n".join(parts)

    @staticmethod
    def _tool_content(result: Any) -> str:
        return json.dumps(
            result,
            ensure_ascii=False,
            default=str,
            separators=(",", ":"),
        )

    @staticmethod
    def _date_arg(value: Any) -> date | None:
        if not value:
            return None
        try:
            return date.fromisoformat(str(value))
        except ValueError:
            return None

    def _tool_get_orderbook(self, args: dict[str, Any]) -> dict[str, Any]:
        from yowayowa.services.orderbook_service import OrderbookService

        symbol = str(args.get("symbol") or "").strip()
        if not symbol:
            return {"error": "symbol is required"}
        try:
            depth = max(1, min(int(args.get("depth", 10)), 50))
        except (ValueError, TypeError):
            depth = 10

        service = OrderbookService()
        snapshot = service.get_snapshot(symbol)
        m = snapshot.metrics
        return {
            "symbol": snapshot.symbol,
            "as_of": snapshot.as_of.isoformat(),
            "best_bid": m.best_bid,
            "best_ask": m.best_ask,
            "mid_price": m.mid_price,
            "spread": m.spread,
            "spread_bps": m.spread_bps,
            "order_flow_imbalance": m.order_flow_imbalance,
            "micro_price": m.micro_price,
            "hft_activity_indicator": m.hft_activity_indicator,
            "bids": [{"price": b.price, "size": b.size} for b in snapshot.bids[:depth]],
            "asks": [{"price": a.price, "size": a.size} for a in snapshot.asks[:depth]],
            "provenance": snapshot.provenance.model_dump(mode="json"),
        }

    def _tool_estimate_orderbook_impact(self, args: dict[str, Any]) -> dict[str, Any]:
        from yowayowa.services.orderbook_service import OrderbookService

        symbol = str(args.get("symbol") or "").strip()
        if not symbol:
            return {"error": "symbol is required"}
        side = str(args.get("side") or "buy").lower().strip()
        if side not in {"buy", "sell"}:
            return {"error": "side must be 'buy' or 'sell'"}
        try:
            qty = float(args.get("quantity", 100.0))
            if qty <= 0:
                return {"error": "quantity must be positive"}
        except (ValueError, TypeError):
            return {"error": "invalid quantity"}

        service = OrderbookService()
        result = service.estimate_impact(symbol, side=side, quantity=qty)  # type: ignore[arg-type]
        return result.model_dump(mode="json")
