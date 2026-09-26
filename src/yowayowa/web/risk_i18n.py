from __future__ import annotations

from collections.abc import Mapping

_MESSAGES: dict[str, dict[str, str]] = {
    "en": {
        "risk.title": "Historical risk",
        "risk.subtitle": "Current-weight risk from adjusted daily returns",
        "risk.period": "History",
        "risk.benchmark": "Benchmark",
        "risk.risk_free": "Risk-free rate (annual decimal)",
        "risk.analyze": "Analyze risk",
        "risk.loading": "Calculating historical risk…",
        "risk.empty": "Run risk analysis for the selected portfolio.",
        "risk.annual_return": "Annualized return",
        "risk.volatility": "Annualized volatility",
        "risk.sharpe": "Sharpe ratio",
        "risk.max_drawdown": "Maximum drawdown",
        "risk.var95": "Historical VaR 95% · 1D",
        "risk.es95": "Expected shortfall 95% · 1D",
        "risk.beta": "Beta",
        "risk.benchmark_corr": "Benchmark correlation",
        "risk.coverage": "Historical coverage",
        "risk.observations": "Observations",
        "risk.position_risk": "Position risk",
        "risk.signed_weight": "Signed weight",
        "risk.correlation": "Portfolio corr.",
        "risk.contribution": "Variance contribution",
        "risk.correlation_matrix": "Pairwise correlations",
        "risk.left": "Left",
        "risk.right": "Right",
        "risk.notes": "Method notes",
        "risk.no_pairs": "At least two covered positions are required for pairwise correlations.",
        "risk.unavailable": "Historical coverage unavailable: {symbols}",
        "risk.note.weights": (
            "Returns use current position weights held constant over the sampled period."
        ),
        "risk.note.gross": (
            "Long/short portfolio return uses gross exposure; short weights are signed."
        ),
        "risk.note.tail": (
            "VaR 95% and expected shortfall 95% are one-day historical loss fractions."
        ),
        "risk.note.history": (
            "Historical statistics describe the sampled period and are not forecasts."
        ),
        "risk.note.partial": (
            "Aggregate statistics describe only the historically covered sleeve; "
            "its reported gross weight is normalized to 100% for the calculation."
        ),
    },
    "ja": {
        "risk.title": "ヒストリカルリスク",
        "risk.subtitle": "現在ウェイトと調整後日次リターンによるリスク分析",
        "risk.period": "分析期間",
        "risk.benchmark": "ベンチマーク",
        "risk.risk_free": "無リスク金利 - 年率・小数",
        "risk.analyze": "リスクを分析",
        "risk.loading": "ヒストリカルリスクを計算中…",
        "risk.empty": "選択中のポートフォリオでリスク分析を実行してください。",
        "risk.annual_return": "年率リターン",
        "risk.volatility": "年率ボラティリティ",
        "risk.sharpe": "シャープレシオ",
        "risk.max_drawdown": "最大ドローダウン",
        "risk.var95": "ヒストリカルVaR 95% · 1日",
        "risk.es95": "期待ショートフォール 95% · 1日",
        "risk.beta": "ベータ",
        "risk.benchmark_corr": "ベンチマーク相関",
        "risk.coverage": "履歴カバー率",
        "risk.observations": "観測数",
        "risk.position_risk": "銘柄別リスク",
        "risk.signed_weight": "符号付き比率",
        "risk.correlation": "ポートフォリオ相関",
        "risk.contribution": "分散寄与度",
        "risk.correlation_matrix": "銘柄間相関",
        "risk.left": "銘柄1",
        "risk.right": "銘柄2",
        "risk.notes": "計算方法",
        "risk.no_pairs": "銘柄間相関には履歴を取得できる銘柄が2つ以上必要です。",
        "risk.unavailable": "履歴取得不可: {symbols}",
        "risk.note.weights": (
            "分析期間を通じ、現在の各ポジション比率を一定としてリターンを計算します。"
        ),
        "risk.note.gross": (
            "ロング・ショートはグロスエクスポージャー基準で計算し、"
            "ショート比率は負値として扱います。"
        ),
        "risk.note.tail": (
            "VaR 95%と期待ショートフォール95%は、過去の日次リターンに基づく1日損失率です。"
        ),
        "risk.note.history": (
            "ヒストリカル統計は分析期間の実績を示すものであり、将来予測ではありません。"
        ),
        "risk.note.partial": (
            "履歴を取得できない銘柄がある場合、集計値は取得可能部分だけを対象とし、"
            "表示したグロス比率を100%へ正規化して計算します。"
        ),
    },
}


def messages(locale: str) -> Mapping[str, str]:
    return _MESSAGES.get(locale, _MESSAGES["en"])


def translate(locale: str, key: str, **values: object) -> str:
    text = messages(locale).get(key, key)
    for name, value in values.items():
        text = text.replace("{" + name + "}", str(value))
    return text


if set(_MESSAGES["ja"]) != set(_MESSAGES["en"]):
    raise RuntimeError("Risk translation catalogs must have identical keys")
