from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from yowayowa.config import get_settings
from yowayowa.providers.edinet import EdinetClient
from yowayowa.providers.registry import fundamentals_provider
from yowayowa.providers.yahoo_screener import YahooScreenerProvider
from yowayowa.research_models import MarketScreenFilter
from yowayowa.services.strategy_edinet import balance_sheet_supplement as edinet_supplement
from yowayowa.services.strategy_edinet import tokyo_security_code
from yowayowa.services.strategy_presets import (
    KIYOHARA_GLOBAL_ID,
    evaluate_kiyohara_candidate,
    get_builtin_strategy,
)
from yowayowa.services.strategy_sec import balance_sheet_supplement as sec_supplement
from yowayowa.services.strategy_tracking import record_strategy_snapshots
from yowayowa.services.strategy_yahoo import balance_sheet_supplement as yahoo_supplement
from yowayowa.strategy_models import (
    StrategyCandidateEvaluation,
    StrategyCandidateInput,
    StrategyResearchSnapshot,
)


@dataclass
class BuiltinSnapshotOutcome:
    evaluations: list[StrategyCandidateEvaluation] = field(default_factory=list)
    snapshots: list[StrategyResearchSnapshot] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    supplement_errors: dict[str, str] = field(default_factory=dict)


def evaluate_and_record_builtin(
    session: Session,
    strategy_id: str,
    region: str,
    size: int,
    *,
    edinet_key: str | None = None,
    captured_at: datetime | None = None,
) -> BuiltinSnapshotOutcome:
    strategy = get_builtin_strategy(strategy_id)
    if strategy_id != KIYOHARA_GLOBAL_ID:
        raise NotImplementedError(f"Strategy evaluator not implemented: {strategy_id}")
    settings = get_settings()
    normalized_region = region.strip().lower()
    discovery = strategy.discovery.model_copy(deep=True)
    discovery.filters = [
        MarketScreenFilter(field="region", operator="is-in", value=[normalized_region]),
        *discovery.filters,
    ]
    discovery.size = size
    discovery.offset = 0
    quotes = YahooScreenerProvider(settings).screen(discovery).quotes
    outcome = BuiltinSnapshotOutcome()
    if edinet_key:
        settings = settings.model_copy(update={"edinet_api_key": edinet_key})
    for row in quotes:
        symbol = str(row.get("symbol") or "").strip()
        market_cap = _first(row, "marketCap", "intradaymarketcap")
        if not symbol:
            outcome.errors["<unknown>"] = "ValueError: quote missing symbol"
            continue
        if not isinstance(market_cap, (int, float)) or market_cap <= 0:
            outcome.errors[symbol] = "ValueError: quote missing positive market capitalization"
            continue
        pe_ratio = _first(row, "trailingPE", "peratio.lasttwelvemonths")
        candidate = StrategyCandidateInput(
            symbol=symbol,
            market_cap=float(market_cap),
            pe_ratio=float(pe_ratio) if isinstance(pe_ratio, (int, float)) else None,
        )
        try:
            facts = fundamentals_provider().company_facts(symbol)
        except Exception as exc:
            outcome.errors[symbol] = f"{type(exc).__name__}: {exc}"
            continue
        supplement = None
        try:
            if tokyo_security_code(symbol) is not None and settings.edinet_api_key:
                supplement = edinet_supplement(session, EdinetClient(settings), symbol)
            if supplement is None:
                supplement = sec_supplement(facts) or yahoo_supplement(facts)
        except Exception as exc:
            outcome.supplement_errors[symbol] = f"{type(exc).__name__}: {exc}"
        outcome.evaluations.append(evaluate_kiyohara_candidate(facts, candidate, supplement))
    outcome.snapshots = record_strategy_snapshots(
        session, strategy_id, normalized_region, outcome.evaluations, captured_at=captured_at
    )
    return outcome


def _first(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value is not None:
            return value
    return None
