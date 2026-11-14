from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from yowayowa.backtest_models import BacktestStrategyDefinition

_STRATEGY_DEFINITIONS = Path(__file__).resolve().parents[1] / "backtest_strategies.json"


@lru_cache(maxsize=1)
def list_strategies() -> list[BacktestStrategyDefinition]:
    payload = json.loads(_STRATEGY_DEFINITIONS.read_text(encoding="utf-8"))
    return [BacktestStrategyDefinition.model_validate(item) for item in payload]


def get_strategy(strategy_id: str) -> BacktestStrategyDefinition:
    for strategy in list_strategies():
        if strategy.id == strategy_id:
            return strategy
    raise LookupError(f"Unknown backtest strategy: {strategy_id}")
