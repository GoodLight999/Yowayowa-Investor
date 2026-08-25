from __future__ import annotations

from collections.abc import Mapping

_MESSAGES: dict[str, dict[str, str]] = {
    "en": {
        "chart.nav": "Composer",
        "chart.title": "Chart Composer",
        "chart.subtitle": "Put market prices, company fundamentals and FRED on one timeline.",
        "chart.sources": "Sources",
        "chart.formulas": "Derived series",
        "chart.add_source": "+ Add source",
        "chart.add_formula": "+ Add formula",
        "chart.run": "Compose chart",
        "chart.source_type": "Source",
        "chart.id": "ID",
        "chart.symbol_series": "Symbol / series",
        "chart.metric": "Metric",
        "chart.label": "Label",
        "chart.kind": "Formula",
        "chart.left": "Left",
        "chart.right": "Right",
        "chart.window": "Window",
        "chart.price": "Price",
        "chart.fundamental": "Fundamental",
        "chart.fred": "FRED",
        "chart.ratio": "Ratio",
        "chart.spread": "Spread",
        "chart.correlation": "Rolling correlation",
        "chart.result": "Composed timeline",
        "chart.provenance": "Series provenance",
        "chart.errors": "Unavailable series",
        "chart.no_errors": "All requested series loaded.",
        "chart.loading": "Composing cross-source chart…",
        "chart.empty": "Add at least one source.",
        "chart.price_hint": "e.g. AAPL",
        "chart.fred_hint": "e.g. CPIAUCSL",
        "chart.metric_hint": "e.g. revenue",
        "chart.asof_note": "Formulas use past-known values only; no backward fill/look-ahead.",
    },
    "ja": {
        "chart.nav": "チャート合成",
        "chart.title": "チャートコンポーザー",
        "chart.subtitle": "市場価格・企業財務・FREDを同じ時間軸で重ねて比較します。",
        "chart.sources": "データ系列",
        "chart.formulas": "派生系列",
        "chart.add_source": "+ 系列を追加",
        "chart.add_formula": "+ 数式を追加",
        "chart.run": "チャートを合成",
        "chart.source_type": "ソース",
        "chart.id": "ID",
        "chart.symbol_series": "銘柄 / 系列",
        "chart.metric": "指標",
        "chart.label": "表示名",
        "chart.kind": "数式",
        "chart.left": "左辺",
        "chart.right": "右辺",
        "chart.window": "期間",
        "chart.price": "価格",
        "chart.fundamental": "財務",
        "chart.fred": "FRED",
        "chart.ratio": "比率",
        "chart.spread": "スプレッド",
        "chart.correlation": "ローリング相関",
        "chart.result": "合成タイムライン",
        "chart.provenance": "系列ごとの出典",
        "chart.errors": "取得できなかった系列",
        "chart.no_errors": "要求した系列をすべて取得しました。",
        "chart.loading": "複数ソースのチャートを合成中…",
        "chart.empty": "データ系列を1つ以上追加してください。",
        "chart.price_hint": "例: AAPL",
        "chart.fred_hint": "例: CPIAUCSL",
        "chart.metric_hint": "例: revenue",
        "chart.asof_note": "数式はその時点までに観測済みの値だけを使い、未来値の逆流は行いません。",
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
    raise RuntimeError("Chart translation catalogs must have identical keys")
