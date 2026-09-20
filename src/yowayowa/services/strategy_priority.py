from __future__ import annotations

from yowayowa.strategy_models import (
    StrategyCandidateEvaluation,
    StrategyPriorityFactor,
    StrategyResearchPriority,
)


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _rounded(value: float) -> float:
    return round(value, 2)


def research_priority(evaluation: StrategyCandidateEvaluation) -> StrategyResearchPriority:
    """Build a transparent research-priority score from normalized strategy evidence.

    The score is intentionally not an expected-return forecast. It only decides where
    scarce research attention should go first, while exposing every factor contribution.
    """

    net_cash_ratio = evaluation.net_cash_ratio
    value_ncr = 0.0
    if net_cash_ratio is not None:
        value_ncr = 24.0 * _clamp(net_cash_ratio)

    value_pe = 0.0
    if evaluation.deep_value_net_cash:
        value_pe = 16.0
    elif evaluation.cash_neutral_pe is not None:
        value_pe = 16.0 * _clamp((20.0 - evaluation.cash_neutral_pe) / 16.0)
    elif evaluation.pe_ratio is not None:
        value_pe = 8.0 * _clamp((20.0 - evaluation.pe_ratio) / 15.0)

    value_score = value_ncr + value_pe

    revenue_growth = evaluation.revenue_growth_yoy
    net_income_growth = evaluation.net_income_growth_yoy
    growth_revenue = (
        15.0 * _clamp(revenue_growth / 0.30) if revenue_growth is not None else 0.0
    )
    growth_income = (
        10.0 * _clamp(net_income_growth / 0.40)
        if net_income_growth is not None
        else 0.0
    )
    growth_score = growth_revenue + growth_income

    roe = evaluation.return_on_equity
    quality_roe = 12.0 * _clamp(roe / 0.25) if roe is not None else 0.0
    quality_fcf = 8.0 if evaluation.free_cash_flow is not None and evaluation.free_cash_flow > 0 else 0.0
    quality_score = quality_roe + quality_fcf

    evidence_score = 0.0
    if evaluation.current_assets is not None and evaluation.liabilities is not None:
        evidence_score += 3.0
    if evaluation.investment_securities is not None:
        evidence_score += 3.0
    elif evaluation.net_cash_ratio is not None:
        evidence_score += 1.0
    if revenue_growth is not None:
        evidence_score += 2.0
    if net_income_growth is not None:
        evidence_score += 2.0
    if evaluation.free_cash_flow is not None:
        evidence_score += 2.0
    if roe is not None:
        evidence_score += 2.0
    if evaluation.pe_ratio is not None:
        evidence_score += 1.0

    flags: list[str] = []
    next_checks: list[str] = []
    if evaluation.net_cash_ratio_is_lower_bound:
        flags.append("net_cash_ratio_lower_bound")
        next_checks.append("verify_exact_investment_securities_from_primary_filing")
    if evaluation.cash_neutral_pe_is_upper_bound:
        flags.append("cash_neutral_pe_upper_bound")
    if revenue_growth is not None and revenue_growth < 0:
        flags.append("revenue_contraction")
        next_checks.append("explain_revenue_contraction_and_recovery_conditions")
    if net_income_growth is not None and net_income_growth < 0:
        flags.append("net_income_contraction")
        next_checks.append("separate_margin_pressure_from_one_off_items")
    if evaluation.free_cash_flow is not None and evaluation.free_cash_flow < 0:
        flags.append("negative_free_cash_flow")
        next_checks.append("explain_cash_burn_capex_and_funding_runway")
    if evaluation.deep_value_net_cash:
        flags.append("net_cash_exceeds_market_cap")
        next_checks.append("verify_asset_realisability_liabilities_and_governance")
    if evidence_score < 9:
        flags.append("low_evidence_coverage")
        next_checks.append("fill_missing_financial_evidence_before_conviction")

    next_checks.extend(
        [
            "test_business_durability_and_competitive_advantage",
            "review_capital_allocation_management_and_near_term_catalysts",
            "identify_first_falsifiable_rejection_condition",
        ]
    )
    next_checks = list(dict.fromkeys(next_checks))

    factors = [
        StrategyPriorityFactor(
            key="value",
            score=_rounded(value_score),
            max_score=40,
            signals={
                "net_cash_ratio": net_cash_ratio,
                "cash_neutral_pe": evaluation.cash_neutral_pe,
                "pe_ratio": evaluation.pe_ratio,
                "net_cash_ratio_is_lower_bound": evaluation.net_cash_ratio_is_lower_bound,
            },
        ),
        StrategyPriorityFactor(
            key="growth",
            score=_rounded(growth_score),
            max_score=25,
            signals={
                "revenue_growth_yoy": revenue_growth,
                "net_income_growth_yoy": net_income_growth,
            },
        ),
        StrategyPriorityFactor(
            key="quality",
            score=_rounded(quality_score),
            max_score=20,
            signals={
                "return_on_equity": roe,
                "free_cash_flow": evaluation.free_cash_flow,
            },
        ),
        StrategyPriorityFactor(
            key="evidence",
            score=_rounded(evidence_score),
            max_score=15,
            signals={
                "exact_investment_securities": evaluation.investment_securities is not None,
                "missing_count": float(len(evaluation.missing)),
            },
        ),
    ]
    total = sum(item.score for item in factors)
    return StrategyResearchPriority(
        score=_rounded(total),
        confidence=_rounded(evidence_score / 15.0),
        factors=factors,
        flags=flags,
        next_checks=next_checks,
    )
