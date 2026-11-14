# ruff: noqa: RUF001
from __future__ import annotations

import math
from datetime import UTC, datetime

from yowayowa.domain import Fundamentals
from yowayowa.research_models import MarketScreenFilter, MarketScreenRequest
from yowayowa.services.screening import derived_metrics
from yowayowa.services.strategy_priority import research_priority
from yowayowa.strategy_models import (
    StrategyBalanceSheetSupplement,
    StrategyCandidateEvaluation,
    StrategyCandidateInput,
    StrategyPresetDefinition,
    StrategySource,
)

KIYOHARA_GLOBAL_ID = "kiyohara_global_value_growth"
JAPAN_VALUE_QUALITY_ID = "japan_value_quality_repricing"

_KIYOHARA_GLOBAL = StrategyPresetDefinition(
    id=KIYOHARA_GLOBAL_ID,
    name_ja="清原達郎モード",
    name_en="Tatsuro Kiyohara mode",
    description_ja=(
        "公開されている『割安小型成長株』の考え方を世界の各市場へ移植した調査プリセット。"
        "地域ごとに低PERの小型株候補を拾い、ネットキャッシュ比率、成長性、FCFを追加確認します。"
    ),
    description_en=(
        "A research preset adapting the published small-cap value-growth approach to regional "
        "equity markets. It discovers smaller low-P/E candidates within one region, then adds "
        "net-cash, growth and free-cash-flow checks."
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
        "yowayowa_conservative_net_cash_ratio",
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
                "Published formula: current assets + investment securities x 70% - liabilities, "
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

_JAPAN_VALUE_QUALITY = StrategyPresetDefinition(
    id=JAPAN_VALUE_QUALITY_ID,
    name_ja="日本株バリュー×クオリティ（慢性割安の再評価）",
    name_en="Japan value x quality (chronic-undervaluation repricing)",
    description_ja=(
        "東証プライム・スタンダード（金融業を除く）を対象に、割安度（B/M・ネットキャッシュ比率）、"
        "本業の収益力（厳密OP・簡易OP・営業利益成長率）、財務安全性（自己資本比率）を"
        "クロスセクションのパーセンタイル順位で合成して候補を並べる調査プリセット。"
        "固定閾値で足切りせず、判断日に開示済みの決算のみを使います。"
        "資本コスト開示などのカタリストはデータが揃わない限り欠損として扱い、加点しません。"
    ),
    description_en=(
        "A research preset that ranks Japanese Prime/Standard non-financial issuers by a "
        "cross-sectional percentile composite of value (B/M, net-cash ratio), quality "
        "(strict and simplified operating profitability, operating-income growth) and "
        "financial safety (equity ratio) instead of fixed screening thresholds. Only "
        "statements already disclosed at the decision date are used, and unavailable "
        "catalyst data is treated as missing rather than imputed."
    ),
    default_region="jp",
    region_required=True,
    discovery=MarketScreenRequest(
        filters=[
            MarketScreenFilter(field="intradaymarketcap", operator="gt", value=0),
        ],
        sort_field="intradaymarketcap",
        sort_ascending=False,
        size=100,
    ),
    research_metrics=[
        "yowayowa_conservative_net_cash_ratio",
        "net_cash_ratio",
        "return_on_equity",
        "operating_income_growth_yoy",
        "equity_to_assets",
        "operating_margin",
    ],
    qualitative_review_ja=[
        "割安の理由が一時的な評価不足か、構造的な収益力低下かを区別する",
        "OP_strict と OP_simple の差が支払利息や特別要因で説明できるか確認する",
        "自己資本比率とネットキャッシュが同時に厚いかを確認する",
        "資本コスト開示・総還元性向・自社株買い枠の有無を開示資料で確認する（データ欠損は加点しない）",
    ],
    qualitative_review_en=[
        "Separate temporary neglect from a structural decline in profitability",
        "Explain any gap between strict and simplified operating profitability",
        "Check that a high equity ratio comes with genuine net cash",
        "Read filings for capital-cost disclosure, payout policy and buyback authorisation "
        "(missing data earns no points)",
    ],
    sources=[
        StrategySource(
            label="Chou, Ko & Rhee (2023) — Comparing competing factor and characteristics "
            "models: Evidence in Japan",
            url="https://doi.org/10.1016/j.pacfin.2023.102179",
            note=(
                "Characteristics beat factor loadings for Japanese equities; book-to-market "
                "and operating profitability carry the significant explanatory power."
            ),
        ),
        StrategySource(
            label="D'Ercole, Wagner & Yamada (2026) — Investor repricing of chronic "
            "undervaluation: Evidence from the Tokyo Stock Exchange capital efficiency initiative",
            url="https://doi.org/10.1016/j.jcorpfin.2026.103009",
            note=(
                "Low-PBR x high-ROE issuers repriced after the TSE capital-efficiency push; "
                "disclosure of capital-efficiency plans was rewarded and non-disclosure penalised."
            ),
        ),
        StrategySource(
            label="Tokyo Stock Exchange — 資本コストや株価を意識した経営の実現に向けた対応",
            url="https://www.jpx.co.jp/equities/improvements/follow-up/",
            note="Official list of issuers disclosing capital-cost/share-price initiatives.",
        ),
    ],
)

BUILTIN_STRATEGIES = (_KIYOHARA_GLOBAL, _JAPAN_VALUE_QUALITY)


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
    supplement: StrategyBalanceSheetSupplement | None = None,
) -> StrategyCandidateEvaluation:
    metrics = derived_metrics(fundamentals)
    current_assets = (
        supplement.current_assets if supplement is not None else metrics.get("current_assets")
    )
    liabilities = supplement.liabilities if supplement is not None else metrics.get("liabilities")
    missing: list[str] = []

    if current_assets is None:
        missing.append("current_assets")
    if liabilities is None:
        missing.append("liabilities")

    yowayowa_conservative_net_cash: float | None = None
    yowayowa_conservative_net_cash_ratio: float | None = None
    if current_assets is not None and liabilities is not None:
        yowayowa_conservative_net_cash = current_assets - liabilities
        yowayowa_conservative_net_cash_ratio = yowayowa_conservative_net_cash / candidate.market_cap

    investment_securities = (
        supplement.investment_securities
        if supplement is not None
        else candidate.investment_securities
    )
    exact_formula = investment_securities is not None
    if not exact_formula:
        missing.append("investment_securities")

    net_cash: float | None = None
    net_cash_ratio: float | None = None
    if yowayowa_conservative_net_cash is not None:
        investment_component = (
            0.7 * investment_securities if investment_securities is not None else 0.0
        )
        net_cash = yowayowa_conservative_net_cash + investment_component
        net_cash_ratio = net_cash / candidate.market_cap

    pe_ratio = _finite_positive(candidate.pe_ratio)
    cash_neutral_pe: float | None = None
    if pe_ratio is not None and net_cash_ratio is not None and net_cash_ratio < 1:
        cash_neutral_pe = pe_ratio * (1 - net_cash_ratio)

    result = StrategyCandidateEvaluation(
        symbol=fundamentals.symbol,
        company_name=fundamentals.company_name,
        market_cap=candidate.market_cap,
        pe_ratio=pe_ratio,
        current_assets=current_assets,
        liabilities=liabilities,
        investment_securities=investment_securities,
        yowayowa_conservative_net_cash=yowayowa_conservative_net_cash,
        yowayowa_conservative_net_cash_ratio=yowayowa_conservative_net_cash_ratio,
        net_cash=net_cash,
        net_cash_ratio=net_cash_ratio,
        net_cash_ratio_is_lower_bound=not exact_formula and net_cash_ratio is not None,
        cash_neutral_pe=cash_neutral_pe,
        cash_neutral_pe_is_upper_bound=(not exact_formula and cash_neutral_pe is not None),
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
        supplemental_provenance=[supplement.provenance] if supplement is not None else [],
    )
    return result.model_copy(update={"research_priority": research_priority(result)})


def evaluated_at() -> datetime:
    return datetime.now(UTC)
