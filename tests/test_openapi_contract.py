from yowayowa.api.app import app


def test_fred_series_schema_keeps_rich_date_filtered_contract() -> None:
    schema = app.openapi()
    operation = schema["paths"]["/v1/macro/fred/{series_id}"]["get"]
    parameter_names = {parameter["name"] for parameter in operation["parameters"]}

    assert {"observation_start", "observation_end"} <= parameter_names
    assert {"units", "frequency", "aggregation_method", "limit"} <= parameter_names


def test_openapi_operation_ids_are_unique() -> None:
    schema = app.openapi()
    operation_ids: list[str] = []
    for path_item in schema["paths"].values():
        for operation in path_item.values():
            if isinstance(operation, dict) and "operationId" in operation:
                operation_ids.append(operation["operationId"])

    assert len(operation_ids) == len(set(operation_ids))


def test_agent_facing_openapi_contract_remains_machine_discoverable() -> None:
    schema = app.openapi()
    paths = schema["paths"]

    required_paths = {
        "/v1/ai/chat",
        "/v1/ai/status",
        "/v1/ai/prompt-packet",
        "/v1/ai/codex/status",
        "/v1/ai/codex/device-auth",
        "/v1/ai/codex/session-status",
        "/v1/strategy-research/snapshots",
        "/v1/strategy-research/outcomes",
        "/v1/strategy-research/calibration",
        "/v1/strategy-presets/{strategy_id}/evaluate",
        "/v1/operations/plan",
    }
    assert required_paths <= set(paths)
    assert paths["/v1/ai/chat"]["post"]["operationId"] == "ai_chat"


REQUIRED_CORE_PATHS = {
    # AI surfaces (existing pins)
    "/v1/ai/chat",
    "/v1/ai/status",
    "/v1/ai/prompt-packet",
    "/v1/ai/codex/status",
    "/v1/ai/codex/device-auth",
    "/v1/ai/codex/session-status",
    "/v1/strategy-research/snapshots",
    "/v1/strategy-research/outcomes",
    "/v1/strategy-research/calibration",
    "/v1/strategy-presets/{strategy_id}/evaluate",
    "/v1/operations/plan",
    # research LLM surfaces (P5-A; GET brief also exists)
    "/v1/research/ask",
    "/v1/research/brief",
    # screening pipeline (P4-D)
    "/v1/screening/run",
    "/v1/screening/candidates",
    # persisted OHLCV stores
    "/v1/stocks/{symbol}/bars",
    "/v1/stocks/{symbol}/bars/latest",
    "/v1/crypto/ohlcv",
    "/v1/crypto/ohlcv/{symbol}",
    # broker read / execution (P2)
    "/v1/broker-read/connectors",
    "/v1/broker-read/connectors/{connector_id}/auth-check",
    "/v1/broker-execution/proposals",
    "/v1/broker-execution/orders",
}


def test_core_machine_paths_remain_discoverable() -> None:
    """CG-003 regression pin: losing a core machine path must fail CI.

    Additions are free; removals are intentional contract changes only.
    """

    schema = app.openapi()
    assert set(schema["paths"]) >= REQUIRED_CORE_PATHS


# The 25 tool names of InvestmentResearchAgent._build_tools (CG-003). Loss of any
# one must fail CI; additions are free, removals are intentional changes only.
REQUIRED_AGENT_TOOLS = frozenset(
    {
        "search_instruments",
        "get_quotes",
        "get_fundamentals",
        "get_valuation",
        "get_company_research",
        "get_options",
        "discover_stocks",
        "triage_strategy",
        "get_strategy_history",
        "get_strategy_outcomes",
        "get_strategy_calibration",
        "compare_symbols",
        "search_news",
        "get_calendar",
        "fred_search",
        "fred_series",
        "get_watchlists",
        "get_portfolios",
        "get_alerts",
        "propose_watchlist_change",
        "propose_compare",
        "propose_screen_filters",
        "get_screening_candidates",
        "get_macro_series",
        "get_ohlcv",
    }
)


def test_agent_tool_catalog_pins_core_read_tools() -> None:
    """CG-003 regression pin: the agent tool catalog keeps its core reads.

    Follows the existing InvestmentResearchAgent construction pattern of
    tests/test_ai_agent.py (in-memory settings + Mock session). Losing any
    pinned tool fails CI; additions are free, removals are intentional.
    """

    from unittest.mock import Mock

    from yowayowa.config import Settings
    from yowayowa.services.ai_agent import InvestmentResearchAgent

    agent = InvestmentResearchAgent(Settings(database_url="sqlite:///:memory:"), Mock())
    assert set(agent.tools) >= REQUIRED_AGENT_TOOLS
