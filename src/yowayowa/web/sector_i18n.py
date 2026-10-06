from __future__ import annotations

from collections.abc import Mapping

_MESSAGES: dict[str, dict[str, str]] = {
    "en": {
        "sector.title": "Sector relative strength",
        "sector.subtitle": (
            "Equal-area view of the 11 Select Sector SPDR ETFs. "
            "Color is return, not market-cap weight."
        ),
        "sector.horizon": "Horizon",
        "sector.1d": "1D",
        "sector.1m": "1M",
        "sector.3m": "3M",
        "sector.1y": "1Y",
        "sector.loading": "Loading sector relative strength…",
        "sector.unavailable": "Unavailable sectors",
        "sector.XLK": "Technology",
        "sector.XLC": "Communication Services",
        "sector.XLY": "Consumer Discretionary",
        "sector.XLP": "Consumer Staples",
        "sector.XLE": "Energy",
        "sector.XLF": "Financials",
        "sector.XLV": "Health Care",
        "sector.XLI": "Industrials",
        "sector.XLB": "Materials",
        "sector.XLRE": "Real Estate",
        "sector.XLU": "Utilities",
    },
    "ja": {
        "sector.title": "セクター相対強度",
        "sector.subtitle": (
            "Select Sector SPDR 11業種を等面積で比較。"
            "色は騰落率であり、時価総額比率ではありません。"
        ),
        "sector.horizon": "期間",
        "sector.1d": "1日",
        "sector.1m": "1か月",
        "sector.3m": "3か月",
        "sector.1y": "1年",
        "sector.loading": "セクター相対強度を取得中…",
        "sector.unavailable": "取得できないセクター",
        "sector.XLK": "情報技術",
        "sector.XLC": "コミュニケーション",
        "sector.XLY": "一般消費財",
        "sector.XLP": "生活必需品",
        "sector.XLE": "エネルギー",
        "sector.XLF": "金融",
        "sector.XLV": "ヘルスケア",
        "sector.XLI": "資本財",
        "sector.XLB": "素材",
        "sector.XLRE": "不動産",
        "sector.XLU": "公益",
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
    raise RuntimeError("Sector translation catalogs must have identical keys")
