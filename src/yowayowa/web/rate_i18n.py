from __future__ import annotations

from collections.abc import Mapping

_MESSAGES: dict[str, dict[str, str]] = {
    "en": {
        "rates.nav": "Rates",
        "rates.title": "Treasury Yield Curve",
        "rates.subtitle": (
            "Official U.S. Treasury daily par yields, without a third-party market-data key."
        ),
        "rates.year": "Year",
        "rates.reload": "Load curve",
        "rates.curve": "Latest par yield curve",
        "rates.spreads": "Curve spreads",
        "rates.spread_10y_2y": "10Y - 2Y",
        "rates.spread_10y_3m": "10Y - 3M",
        "rates.history": "Spread history",
        "rates.maturity": "Maturity",
        "rates.yield": "Yield",
        "rates.loading": "Loading official Treasury curve…",
        "rates.points": "Published maturities",
        "rates.source_note": (
            "Missing Treasury maturities remain missing; they are not interpolated."
        ),
    },
    "ja": {
        "rates.nav": "金利",
        "rates.title": "米国債イールドカーブ",
        "rates.subtitle": (
            "第三者の市場データAPIキーを使わず、米財務省公式の日次Par Yieldを直接表示します。"
        ),
        "rates.year": "年",
        "rates.reload": "カーブを取得",
        "rates.curve": "最新Par Yield Curve",
        "rates.spreads": "カーブ・スプレッド",
        "rates.spread_10y_2y": "10年 - 2年",
        "rates.spread_10y_3m": "10年 - 3か月",
        "rates.history": "スプレッド履歴",
        "rates.maturity": "満期",
        "rates.yield": "利回り",
        "rates.loading": "米財務省公式カーブを取得中…",
        "rates.points": "公表満期",
        "rates.source_note": ("財務省が公表していない満期は欠損のまま扱い、補間しません。"),
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
    raise RuntimeError("Rate translation catalogs must have identical keys")
