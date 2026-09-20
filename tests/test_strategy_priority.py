from datetime import UTC, datetime

from yowayowa.domain import LicenseClass, Provenance
from yowayowa.services.strategy_priority import research_priority
from yowayowa.strategy_models import StrategyCandidateEvaluation


def _evaluation(**updates: object) -> StrategyCandidateEvaluation:
    data: dict[str, object] = {
        "symbol": "TEST",
        "company_name": "Test Corp",
        "market_cap": 100.0,
        "pe_ratio": 15.0,
        "current_assets": 120.0,
        "liabilities": 40.0,
        "investment_securities": 20.0,
        "yowayowa_conservative_net_cash": 80.0,
        "yowayowa_conservative_net_cash_ratio": 0.8,
        "net_cash": 94.0,
        "net_cash_ratio": 0.8,
        "cash_neutral_pe": 3.0,
        "revenue_growth_yoy": 0.30,
        "net_income_growth_yoy": 0.40,
        "free_cash_flow": 10.0,
        "return_on_equity": 0.25,
        "deep_value_net_cash": False,
        "basis": "kiyohara_formula_with_investment_securities",
        "provenance": Provenance(
            provider="fixture",
            source="fixture",
            license_class=LicenseClass.OFFICIAL_PUBLIC,
            retrieved_at=datetime(2026, 9, 21, tzinfo=UTC),
        ),
    }
    data.update(updates)
    return StrategyCandidateEvaluation.model_validate(data)


def test_research_priority_exposes_factor_contributions_and_full_evidence() -> None:
    result = research_priority(_evaluation())

    assert result.score == 95.2
    assert result.confidence == 1.0
    assert {factor.key for factor in result.factors} == {
        "value",
        "growth",
        "quality",
        "evidence",
    }
    assert sum(factor.score for factor in result.factors) == result.score
    assert result.interpretation == "research_priority_not_return_forecast"


def test_research_priority_penalizes_weak_signals_without_hiding_rejection_flags() -> None:
    weak = _evaluation(
        investment_securities=None,
        net_cash_ratio=0.1,
        net_cash_ratio_is_lower_bound=True,
        cash_neutral_pe=18.0,
        cash_neutral_pe_is_upper_bound=True,
        revenue_growth_yoy=-0.10,
        net_income_growth_yoy=-0.20,
        free_cash_flow=-10.0,
        return_on_equity=-0.10,
        basis="conservative_floor_ex_investment_securities",
        missing=["investment_securities"],
    )
    result = research_priority(weak)

    assert result.score < 25
    assert "net_cash_ratio_lower_bound" in result.flags
    assert "cash_neutral_pe_upper_bound" in result.flags
    assert "revenue_contraction" in result.flags
    assert "net_income_contraction" in result.flags
    assert "negative_free_cash_flow" in result.flags
    assert "verify_exact_investment_securities_from_primary_filing" in result.next_checks
