from __future__ import annotations

import math
from datetime import UTC, datetime

from yowayowa.domain import Fundamentals
from yowayowa.research_models import MarketScreenFilter, MarketScreenRequest
from yowayowa.services.screening import derived_metrics
from yowayowa.strategy_models import (
    StrategyCandidateEvaluation,
    StrategyCandidateInput,
    StrategyPresetDefinition,
    StrategySource,
)

KIYOHARA_GLOBAL_ID = "kiyohara_global_value_growth"

_KIYOHARA_GLOBAL = StrategyPresetDefinition(
    id=KIYOHARA_GLOBAL_ID,
    name_ja="清原達郎モード（非公式）",
    name_en="Tatsuro Kiyohara style (unofficial)",
    description_ja=(
        "公開されている『割安小型成長株』の考え方を世界の各市場へ移植した調査プリセット。"
        "地域ごとに低PERの小型株候補を拾い、ネットキャッシュ比率、成長性、FCFを追加確認します。"
        "PER上限20倍は候補数を抑えるためのYowayowa既定値であり、清原氏の固定ルールを意味しません。"
    ),
    description_en=(
        "An unofficial research preset adapting the published small-cap value-growth approach "
        "to regional equity markets. It discovers smaller low-P/E candidates within one region, "
        "then adds net-cash, growth and free-cash-flow checks. The 20x P/E ceiling is a Yowayowa "
        "discovery default, not a claimed fixed rule from Kiyohara."
    ),
    default_region="jp",
    region_required=True,
    discovery=MarketScreenRequest(
        filters=[
            MarketScreenFilter(
                field="peratio.lasttwelvemonths",
                operator="btwn",
                value=[0.01, 20.0],
            ),
            MarketScreenFilter(field="intradaymarketcap", operator="gt", value=0),
        ],
        sort_field="intradaymarketcap",
        sort_ascending=True,
        size=25,
    ),
    research_metrics=[
        "net_cash_ratio",
        "cash_neutral_pe",
        "revenue_growth_yoy",
        "net_income_growth_yoy",
        "free_cash_flow",
        "return_on_equity",
    ],
    qualitative_review_ja=[
        "数字だけで完結させず、事業内容と成長余地を調べる",
        "経営陣が成長へ資本を配分できるか確認する",
        "競争優位と市場の持続性を確認する",
        "不人気の理由が一時的な評価不足か、構造的な問題かを区別する",
    ],
    qualitative_review_en=[
        "Do not stop at the screen; investigate the business and runway for growth",
        "Assess whether management can allocate capital toward growth",
        "Check competitive durability and the persistence of the addressable market",
        "Separate temporary neglect from structural reasons for a low valuation",
    ],
    sources=[
        StrategySource(
            label="Kodansha — Waga Toshi-jutsu",
            url="https://www.kodansha.co.jp/book/products/0000387083",
            note=(
                "Publisher chapter listing: small-cap value-growth, cash-neutral P/E, "
                "valuation and contrarian investing."
            ),
        ),
        StrategySource(
            label="Toyo Keizai — Shikiho workflow",
            url="https://toyokeizai.net/articles/-/739831?page=2",
            note=(
                "Kiyohara describes screening by high net-cash ratio and low P/E, then reading "
                "Shikiho, researching the company and, where useful, meeting management."
            ),
        ),
        StrategySource(
            label="Diamond Online — 2026 net-cash methodology",
            url="https://diamond.jp/articles/-/386599",
            note=(
                "Published formula: current assets + investment securities × 70% − liabilities, "
                "divided by market capitalization."
            ),
        ),
        StrategySource(
            label="Toyo Keizai — 2026 small/mid-cap commentary",
            url="https://toyokeizai.net/articles/-/942685?display=b",
            note="Recent first-person commentary on the continuing opportunity in small/mid caps.",
        ),
    ],
)

BUILTIN_STRATEGIES = (_KIYOHARA_GLOBAL,)


def list_builtin_strategies() -> list[StrategyPresetDefinition]:
    return list(BUILTIN_STRATEGIES)


def get_builtin_strategy(strategy_id: str) -> StrategyPresetDefinition:
    for strategy in BUILTIN_STRATEGIES:
        if strategy.id == strategy_id:
            return strategy
    raise LookupError(f"Unknown built-in strategy preset: {strategy_id}")


def _finite_positive(value: float | None) -> float | None:
    if value is None or not math.isfinite(value) or value <= 0:
        return None
    return value


def evaluate_kiyohara_candidate(
    fundamentals: Fundamentals,
    candidate: StrategyCandidateInput,
) -> StrategyCandidateEvaluation:
    metrics = derived_metrics(fundamentals)
    current_assets = metrics.get("current_assets")
    liabilities = metrics.get("liabilities")
    missing: list[str] = []

    if current_assets is None:
        missing.append("current_assets")
    if liabilities is None:
        missing.append("liabilities")

    investment_securities = candidate.investment_securities
    exact_formula = investment_securities is not None
    if not exact_formula:
        missing.append("investment_securities")

    net_cash: float | None = None
    net_cash_ratio: float | None = None
    if current_assets is not None and liabilities is not None:
        investment_component = 0.7 * investment_securities if investment_securities is not None else 0.0
        net_cash = current_assets + investment_component - liabilities
        net_cash_ratio = net_cash / candidate.market_cap

    pe_ratio = _finite_positive(candidate.pe_ratio)
    cash_neutral_pe: float | None = None
    if pe_ratio is not None and net_cash_ratio is not None and net_cash_ratio < 1:
        cash_neutral_pe = pe_ratio * (1 - net_cash_ratio)

    return StrategyCandidateEvaluation(
        symbol=fundamentals.symbol,
        company_name=fundamentals.company_name,
        market_cap=candidate.market_cap,
        pe_ratio=pe_ratio,
        current_assets=current_assets,
        liabilities=liabilities,
        investment_securities=investment_securities,
        net_cash=net_cash,
        net_cash_ratio=net_cash_ratio,
        net_cash_ratio_is_lower_bound=not exact_formula and net_cash_ratio is not None,
        cash_neutral_pe=cash_neutral_pe,
        cash_neutral_pe_is_upper_bound=(
            not exact_formula and cash_neutral_pe is not None
        ),
        revenue_growth_yoy=metrics.get("revenue_growth_yoy"),
        net_income_growth_yoy=metrics.get("net_income_growth_yoy"),
        free_cash_flow=metrics.get("free_cash_flow"),
        return_on_equity=metrics.get("return_on_equity"),
        deep_value_net_cash=(net_cash_ratio >= 1 if net_cash_ratio is not None else None),
        basis=(
            "kiyohara_formula_with_investment_securities"
            if exact_formula
            else "conservative_floor_ex_investment_securities"
        ),
        missing=missing,
        provenance=fundamentals.provenance,
    )


def evaluated_at() -> datetime:
    return datetime.now(UTC)
