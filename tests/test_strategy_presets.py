from datetime import UTC, date, datetime
from decimal import Decimal

from starlette.testclient import TestClient

from yowayowa.domain import (
    Fundamentals,
    LicenseClass,
    MetricPoint,
    MetricSeries,
    Provenance,
)
from yowayowa.services.strategy_edinet import StrategyBalanceSheetSupplement
from yowayowa.services.strategy_presets import (
    KIYOHARA_GLOBAL_ID,
    evaluate_kiyohara_candidate,
    get_builtin_strategy,
)
from yowayowa.strategy_models import StrategyCandidateInput


def _provenance(source: str = "fixture statements") -> Provenance:
    return Provenance(
        provider="fixture",
        source=source,
        license_class=LicenseClass.OFFICIAL_PUBLIC,
        retrieved_at=datetime(2026, 8, 25, tzinfo=UTC),
        as_of=date(2025, 12, 31),
    )


def _fundamentals(symbol: str = "TEST") -> Fundamentals:
    provenance = _provenance()

    def series(key: str, value: float) -> MetricSeries:
        return MetricSeries(
            key=key,
            label=key,
            points=[
                MetricPoint(
                    period_start=date(2025, 1, 1),
                    period_end=date(2025, 12, 31),
                    fiscal_year=2025,
                    fiscal_period="FY",
                    value=Decimal(str(value)),
                    unit="USD",
                )
            ],
        )

    return Fundamentals(
        symbol=symbol,
        cik="0000000001",
        company_name=f"{symbol} Corp",
        metrics={
            "current_assets": series("current_assets", 120),
            "liabilities": series("liabilities", 40),
            "revenue": series("revenue", 100),
            "net_income": series("net_income", 10),
            "operating_cash_flow": series("operating_cash_flow", 15),
            "capex": series("capex", 5),
            "equity": series("equity", 60),
        },
        provenance=provenance,
    )


def test_kiyohara_builtin_is_global_regional_workflow_not_fixed_jpy_cap() -> None:
    strategy = get_builtin_strategy(KIYOHARA_GLOBAL_ID)

    assert strategy.name_ja == "清原達郎モード"
    assert strategy.default_region == "jp"
    assert strategy.region_required is True
    assert strategy.discovery.sort_field == "intradaymarketcap"
    assert strategy.discovery.sort_ascending is True
    assert not any(item.field == "region" for item in strategy.discovery.filters)
    assert not any(
        item.field == "intradaymarketcap" and item.operator in {"lt", "lte"}
        for item in strategy.discovery.filters
    )
    assert "yowayowa_conservative_net_cash_ratio" in strategy.research_metrics
    assert any("net-cash" in source.note.lower() for source in strategy.sources)


def test_kiyohara_formula_uses_seventy_percent_of_investment_securities() -> None:
    result = evaluate_kiyohara_candidate(
        _fundamentals(),
        StrategyCandidateInput(
            symbol="TEST",
            market_cap=100,
            pe_ratio=10,
            investment_securities=30,
        ),
    )

    assert result.yowayowa_conservative_net_cash == 80
    assert result.yowayowa_conservative_net_cash_ratio == 0.8
    assert result.net_cash == 101
    assert result.net_cash_ratio == 1.01
    assert result.deep_value_net_cash is True
    assert result.cash_neutral_pe is None
    assert result.net_cash_ratio_is_lower_bound is False
    assert result.basis == "kiyohara_formula_with_investment_securities"


def test_edinet_supplement_replaces_all_three_balance_sheet_inputs_together() -> None:
    supplement = StrategyBalanceSheetSupplement(
        current_assets=200,
        liabilities=50,
        investment_securities=50,
        provenance=_provenance("EDINET annual filing"),
    )
    result = evaluate_kiyohara_candidate(
        _fundamentals("7203.T"),
        StrategyCandidateInput(symbol="7203.T", market_cap=250, pe_ratio=10),
        supplement,
    )

    assert result.current_assets == 200
    assert result.liabilities == 50
    assert result.investment_securities == 50
    assert result.yowayowa_conservative_net_cash_ratio == 0.6
    assert result.net_cash_ratio == 0.74
    assert result.cash_neutral_pe == 2.6
    assert result.net_cash_ratio_is_lower_bound is False
    assert result.supplemental_provenance[0].source == "EDINET annual filing"


def test_missing_investment_securities_produces_conservative_bounds() -> None:
    result = evaluate_kiyohara_candidate(
        _fundamentals(),
        StrategyCandidateInput(symbol="TEST", market_cap=100, pe_ratio=10),
    )

    assert result.yowayowa_conservative_net_cash == 80
    assert result.yowayowa_conservative_net_cash_ratio == 0.8
    assert result.net_cash == 80
    assert result.net_cash_ratio == 0.8
    assert result.net_cash_ratio_is_lower_bound is True
    assert result.cash_neutral_pe == 2
    assert result.cash_neutral_pe_is_upper_bound is True
    assert result.basis == "conservative_floor_ex_investment_securities"
    assert "investment_securities" in result.missing


def test_strategy_api_returns_partial_results_and_preserves_errors(monkeypatch) -> None:
    from yowayowa.api import fundamentals_routes
    from yowayowa.api.app import app

    def fake_fundamentals(symbol: str) -> Fundamentals:
        if symbol == "MISS":
            raise LookupError("fixture unavailable")
        return _fundamentals(symbol)

    monkeypatch.setattr(fundamentals_routes, "_fundamentals", fake_fundamentals)

    with TestClient(app) as client:
        catalog = client.get("/v1/strategy-presets")
        assert catalog.status_code == 200
        assert catalog.json()[0]["id"] == KIYOHARA_GLOBAL_ID

        response = client.post(
            f"/v1/strategy-presets/{KIYOHARA_GLOBAL_ID}/evaluate",
            json={
                "candidates": [
                    {"symbol": "GOOD", "market_cap": 100, "pe_ratio": 10},
                    {"symbol": "MISS", "market_cap": 100, "pe_ratio": 10},
                ]
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert [item["symbol"] for item in payload["evaluations"]] == ["GOOD"]
    assert payload["evaluations"][0]["yowayowa_conservative_net_cash_ratio"] == 0.8
    assert "MISS" in payload["errors"]
