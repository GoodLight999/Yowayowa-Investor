from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import httpx
from sqlalchemy.orm import Session

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
    AIProviderConfig,
    AIToolTrace,
    MarketScreenRequest,
    ResearchSection,
)
from yowayowa.services.alerts import list_alerts
from yowayowa.services.comparison import compare
from yowayowa.services.portfolios import get_portfolio, list_portfolios, portfolio_analytics
from yowayowa.services.screening import derived_metrics
from yowayowa.services.valuation import valuation_snapshot
from yowayowa.services.watchlists import list_watchlists
from yowayowa.symbols import normalize_symbol

ToolHandler = Callable[[dict[str, Any]], Any]


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


class InvestmentResearchAgent:
    """Multi-provider BYOK agent over real Yowayowa research boundaries."""

    def __init__(self, settings: Settings, session: Session) -> None:
        self.settings = settings
        self.session = session
        self.trace: list[AIToolTrace] = []
        self.proposals: list[Operation] = []
        self.tools = self._build_tools()

    def chat(self, request: AIChatRequest) -> AIChatResponse:
        provider = self._resolve_provider(request.provider)
        self.trace = []
        self.proposals = []
        if provider.provider == "anthropic":
            answer = self._anthropic_loop(provider, request)
        else:
            answer = self._openai_loop(provider, request)
        return AIChatResponse(
            answer=answer.strip() or "No answer returned.",
            provider=provider.provider,
            model=provider.model,
            tool_trace=self.trace,
            proposed_operations=self.proposals,
        )

    def status(self) -> dict[str, Any]:
        anthropic_ready = bool(self.settings.anthropic_api_key and self.settings.anthropic_model)
        openai_ready = bool(
            self.settings.openai_compatible_api_key and self.settings.openai_compatible_model
        )
        return {
            "configured": {
                "openai_compatible": openai_ready,
                "anthropic": anthropic_ready,
            },
            "tools": list(self.tools),
            "byok_per_request": True,
            "keys_persisted_by_server": False,
        }

    def _resolve_provider(
        self,
        supplied: AIProviderConfig | None,
    ) -> ResolvedAIProvider:
        if supplied is not None:
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
            "Prefer compact, decision-relevant comparisons over generic prose. "
            "For workspace changes, use propose_* tools; never silently mutate state. "
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

    def _build_tools(self) -> dict[str, ToolSpec]:
        symbol = self._object_schema({"symbol": {"type": "string"}}, ["symbol"])
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
                "get_alerts",
                "Read configured price alerts.",
                self._object_schema({}),
                self._tool_alerts,
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

    def _tool_portfolios(self, args: dict[str, Any]) -> Any:
        portfolio_id = args.get("portfolio_id")
        if portfolio_id is None:
            return [item.model_dump(mode="json") for item in list_portfolios(self.session)]
        portfolio = get_portfolio(self.session, int(portfolio_id))
        result = portfolio_analytics(portfolio, yahoo_market_provider())
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
