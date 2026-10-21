"""Cross-sectional value x quality scoring for Japanese equities.

Implements the four-factor design of the master specification
(``docs/research/JAPAN_VALUE_QUALITY_STRATEGY_MASTER_ORIGINAL.md`` §2 and §3-B):

* Value      : B/M (inverse PBR) + net-cash ratio
* Quality    : strict operating profitability + simplified operating
               profitability + operating-income growth
* Safety     : equity ratio (equity / assets)
* Catalyst   : TSE capital-cost disclosure / total-payout increase / buyback
               authorisation -- **not available** from the snapshot sources, so
               it stays missing and never earns points (spec §3-B.3).

Selection is by cross-sectional percentile rank inside the universe, never by a
fixed threshold (spec §3-B.3).  Everything is computed from data that was
already disclosed at the decision date; a statement becomes eligible only once
``fy_end + DISCLOSURE_LAG_DAYS`` has passed (Japanese annual reports are due
within three months, with a possible one-month extension).

Nothing here fills a missing input with zero: a missing component makes its
dimension missing, and a missing dimension is either fatal for that name
(``strict`` policy) or dropped from the weighted average with the remaining
dimensions renormalised (``graceful`` policy).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Literal

#: Japanese annual reports (有価証券報告書) are due within three months of the
#: fiscal year end; a one-month extension is possible.  120 days is therefore a
#: conservative "this was public" boundary and never grants look-ahead.
DISCLOSURE_LAG_DAYS = 120

DIMENSIONS = ("value", "quality", "safety", "catalyst")
COMPONENTS = (
    "bm",
    "net_cash_ratio",
    "op_strict",
    "op_simple",
    "op_growth",
    "equity_ratio",
    "catalyst",
)

MissingPolicy = Literal["strict", "graceful"]
OpSimpleBasis = Literal["actual", "forecast"]


def disclosed_at(fy_end: date) -> date:
    """Conservative public-disclosure date for an annual statement."""

    return fy_end + timedelta(days=DISCLOSURE_LAG_DAYS)


@dataclass(frozen=True, slots=True)
class AnnualFundamental:
    """One fiscal year of reported figures (raw reported values, JPY)."""

    fy_end: date
    revenue: float | None = None
    operating_income: float | None = None
    net_income: float | None = None
    interest_expense: float | None = None
    eps_diluted: float | None = None
    equity: float | None = None
    assets: float | None = None
    current_assets: float | None = None
    current_liabilities: float | None = None
    liabilities: float | None = None
    shares_outstanding: float | None = None
    forecast_operating_income: float | None = None

    @property
    def disclosed_at(self) -> date:
        return disclosed_at(self.fy_end)


@dataclass(frozen=True, slots=True)
class SymbolFundamentals:
    symbol: str
    annual: tuple[AnnualFundamental, ...]

    def point_in_time(
        self, decision_date: date
    ) -> tuple[AnnualFundamental, AnnualFundamental] | None:
        """Latest two statements public strictly before ``decision_date``.

        Returns ``(latest, previous)`` or ``None`` when no statement is eligible.
        Nothing is imputed: a fiscal year that is not yet public is invisible.
        """

        eligible = sorted(
            (item for item in self.annual if item.disclosed_at < decision_date),
            key=lambda item: item.fy_end,
        )
        if not eligible:
            return None
        latest = eligible[-1]
        previous = eligible[-2] if len(eligible) >= 2 else None
        if previous is None:
            return (latest, AnnualFundamental(fy_end=date.min))
        return (latest, previous)


@dataclass(frozen=True, slots=True)
class FactorInputs:
    """Raw (unranked) factor inputs for one symbol at one decision date."""

    symbol: str
    fy_end: date
    disclosed_at: date
    market_cap: float | None
    bm: float | None
    net_cash_ratio: float | None
    op_strict: float | None
    op_simple: float | None
    op_growth: float | None
    equity_ratio: float | None
    roe: float | None
    pbr: float | None
    catalyst: float | None = None
    missing: tuple[str, ...] = ()

    def component(self, name: str) -> float | None:
        return {
            "bm": self.bm,
            "net_cash_ratio": self.net_cash_ratio,
            "op_strict": self.op_strict,
            "op_simple": self.op_simple,
            "op_growth": self.op_growth,
            "equity_ratio": self.equity_ratio,
            "catalyst": self.catalyst,
        }[name]


DIMENSION_COMPONENTS: dict[str, tuple[str, ...]] = {
    "value": ("bm", "net_cash_ratio"),
    "quality": ("op_strict", "op_simple", "op_growth"),
    "safety": ("equity_ratio",),
    "catalyst": ("catalyst",),
}


def _positive(value: float | None) -> float | None:
    if value is None or not math.isfinite(value) or value <= 0:
        return None
    return value


def compute_factor_inputs(
    fundamentals: SymbolFundamentals,
    decision_date: date,
    close: float | None,
    *,
    op_simple_basis: OpSimpleBasis = "actual",
) -> FactorInputs | None:
    """Build the raw factor inputs for ``decision_date``.

    ``close`` is the last close strictly before the decision date (no same-day
    price is used for scoring).  ``None`` means the symbol has no usable price
    and therefore no market-cap based factor.
    """

    pit = fundamentals.point_in_time(decision_date)
    if pit is None:
        return None
    latest, previous = pit
    missing: list[str] = []

    equity = _positive(latest.equity)
    assets = _positive(latest.assets)
    operating_income = latest.operating_income
    interest_expense = latest.interest_expense
    shares = _positive(latest.shares_outstanding)
    price = _positive(close)

    market_cap = price * shares if price is not None and shares is not None else None
    if market_cap is None:
        missing.append("market_cap")

    bm = None
    pbr = None
    if equity is not None and market_cap is not None:
        bm = equity / market_cap
        pbr = market_cap / equity
    else:
        missing.append("bm")

    net_cash = None
    current_assets = latest.current_assets
    total_liabilities = latest.liabilities
    if current_assets is not None and total_liabilities is not None:
        # Conservative floor: investment securities are not available from the
        # snapshot, so the Kiyohara formula's 0.7 x securities term is omitted
        # (same convention as ``evaluate_kiyohara_candidate``).
        net_cash = current_assets - total_liabilities
    net_cash_ratio = None
    if net_cash is not None and market_cap is not None:
        net_cash_ratio = net_cash / market_cap
    else:
        missing.append("net_cash_ratio")

    op_strict = None
    if (
        operating_income is not None
        and interest_expense is not None
        and equity is not None
    ):
        op_strict = (operating_income - interest_expense) / equity
    else:
        missing.append("op_strict")

    op_simple = None
    numerator = None
    if op_simple_basis == "forecast":
        numerator = latest.forecast_operating_income
        if numerator is None:
            missing.append("op_simple_forecast")
    else:
        numerator = operating_income
        if numerator is None:
            missing.append("op_simple")
    if numerator is not None and equity is not None:
        op_simple = numerator / equity
    elif "op_simple" not in missing and "op_simple_forecast" not in missing:
        missing.append("op_simple")

    op_growth = None
    if (
        operating_income is not None
        and previous.operating_income is not None
        and previous.operating_income != 0
    ):
        op_growth = (operating_income - previous.operating_income) / abs(
            previous.operating_income
        )
    else:
        missing.append("op_growth")

    equity_ratio = None
    if equity is not None and assets is not None:
        equity_ratio = equity / assets
    else:
        missing.append("equity_ratio")

    roe = None
    if latest.net_income is not None and equity is not None:
        roe = latest.net_income / equity

    return FactorInputs(
        symbol=fundamentals.symbol,
        fy_end=latest.fy_end,
        disclosed_at=latest.disclosed_at,
        market_cap=market_cap,
        bm=bm,
        net_cash_ratio=net_cash_ratio,
        op_strict=op_strict,
        op_simple=op_simple,
        op_growth=op_growth,
        equity_ratio=equity_ratio,
        roe=roe,
        pbr=pbr,
        catalyst=None,
        missing=tuple(dict.fromkeys(missing)),
    )


def percentile_ranks(values: dict[str, float | None]) -> dict[str, float]:
    """Percentile ranks in [0, 1]; ties share the average rank; higher is better."""

    usable = {
        symbol: value
        for symbol, value in values.items()
        if value is not None and math.isfinite(value)
    }
    count = len(usable)
    if count == 0:
        return {}
    if count == 1:
        return {next(iter(usable)): 0.5}
    ordered = sorted(usable.items(), key=lambda item: item[1])
    ranks: dict[str, float] = {}
    position = 0
    while position < count:
        end = position
        while end + 1 < count and ordered[end + 1][1] == ordered[position][1]:
            end += 1
        average_rank = (position + end) / 2
        percentile = average_rank / (count - 1)
        for index in range(position, end + 1):
            ranks[ordered[index][0]] = percentile
        position = end + 1
    return ranks


@dataclass(frozen=True, slots=True)
class ScoreWeights:
    value: float = 0.35
    quality: float = 0.30
    safety: float = 0.20
    catalyst: float = 0.15

    def items(self) -> tuple[tuple[str, float], ...]:
        return (
            ("value", self.value),
            ("quality", self.quality),
            ("safety", self.safety),
            ("catalyst", self.catalyst),
        )

    def normalised(self) -> dict[str, float]:
        total = sum(weight for _, weight in self.items())
        if total <= 0:
            raise ValueError("score weights must sum to a positive number")
        return {name: weight / total for name, weight in self.items()}

    @classmethod
    def equal(cls) -> ScoreWeights:
        return cls(0.25, 0.25, 0.25, 0.25)


@dataclass(frozen=True, slots=True)
class SymbolScore:
    symbol: str
    composite: float | None
    dimensions: dict[str, float | None]
    components: dict[str, float | None]
    missing_dimensions: tuple[str, ...]
    missing_components: tuple[str, ...]
    inputs: FactorInputs


@dataclass(frozen=True, slots=True)
class UniverseScore:
    decision_date: date
    scores: dict[str, SymbolScore]
    coverage: dict[str, Any] = field(default_factory=dict)

    def ranked(self) -> list[SymbolScore]:
        return sorted(
            (item for item in self.scores.values() if item.composite is not None),
            key=lambda item: (-item.composite, item.symbol),
        )


def score_universe(
    inputs: dict[str, FactorInputs],
    *,
    weights: ScoreWeights | None = None,
    missing_policy: MissingPolicy = "strict",
    decision_date: date | None = None,
) -> UniverseScore:
    """Rank the universe and compose the four dimension scores.

    ``strict``  : a dimension exists only if every component is present.
    ``graceful``: a dimension averages its available components (at least one).
    """

    weights = weights or ScoreWeights()
    component_ranks = {
        name: percentile_ranks(
            {symbol: item.component(name) for symbol, item in inputs.items()}
        )
        for name in COMPONENTS
    }

    scores: dict[str, SymbolScore] = {}
    for symbol, item in inputs.items():
        dimension_scores: dict[str, float | None] = {}
        for dimension, components in DIMENSION_COMPONENTS.items():
            available = [
                component_ranks[name][symbol]
                for name in components
                if symbol in component_ranks[name]
            ]
            if not available or (missing_policy == "strict" and len(available) < len(components)):
                dimension_scores[dimension] = None
            else:
                dimension_scores[dimension] = sum(available) / len(available)

        normalised = weights.normalised()
        weighted = [
            (normalised[dimension], value)
            for dimension, value in dimension_scores.items()
            if value is not None
        ]
        total_weight = sum(weight for weight, _ in weighted)
        composite = (
            sum(weight * value for weight, value in weighted) / total_weight
            if total_weight > 0
            else None
        )
        scores[symbol] = SymbolScore(
            symbol=symbol,
            composite=composite,
            dimensions=dimension_scores,
            components={
                name: component_ranks[name].get(symbol) for name in COMPONENTS
            },
            missing_dimensions=tuple(
                dimension for dimension, value in dimension_scores.items() if value is None
            ),
            missing_components=item.missing,
            inputs=item,
        )

    evaluated = [item for item in scores.values() if item.composite is not None]
    coverage: dict[str, object] = {
        "evaluated": len(scores),
        "scored": len(evaluated),
        "missing_by_dimension": {
            dimension: sum(
                1 for item in scores.values() if item.dimensions[dimension] is None
            )
            for dimension in DIMENSIONS
        },
        "missing_by_component": {
            component: sum(
                1 for symbol in scores if symbol not in component_ranks[component]
            )
            for component in COMPONENTS
        },
    }
    if scores:
        coverage["missing_rate_by_dimension"] = {
            dimension: count / len(scores)
            for dimension, count in coverage["missing_by_dimension"].items()  # type: ignore[union-attr]
        }
    return UniverseScore(
        decision_date=decision_date or date.min,
        scores=scores,
        coverage=coverage,
    )


@dataclass(frozen=True, slots=True)
class BaselineFilter:
    """Spec §3-A hard filter, used as the comparison baseline."""

    max_pbr: float = 1.0
    min_roe: float = 0.10
    min_equity_ratio: float = 0.40
    min_op_simple: float = 0.15

    def passes(self, item: FactorInputs) -> bool:
        pbr = item.pbr
        roe = item.roe
        equity_ratio = item.equity_ratio
        op_simple = item.op_simple
        if pbr is None or roe is None or equity_ratio is None or op_simple is None:
            return False
        return (
            pbr <= self.max_pbr
            and roe >= self.min_roe
            and equity_ratio >= self.min_equity_ratio
            and op_simple >= self.min_op_simple
        )
