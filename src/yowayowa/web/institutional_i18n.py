from __future__ import annotations

from collections.abc import Mapping

_MESSAGES: dict[str, dict[str, str]] = {
    "en": {
        "institutional.nav": "US institutional holdings",
        "institutional.title": "US institutional holdings disclosures",
        "institutional.subtitle": (
            "Official SEC quarterly holdings research. Reported positions are delayed "
            "disclosures, not current portfolios."
        ),
        "institutional.cik": "Manager CIK",
        "institutional.quarters": "Quarters",
        "institutional.load": "Load holdings disclosure",
        "institutional.manager": "Manager",
        "institutional.latest": "Latest reported holdings",
        "institutional.changes": "Quarter-over-quarter changes",
        "institutional.report_date": "Report date",
        "institutional.filing_date": "Filed",
        "institutional.lag": "Disclosure lag",
        "institutional.days": "days",
        "institutional.issuer": "Issuer",
        "institutional.class": "Class",
        "institutional.cusip": "CUSIP",
        "institutional.value": "Reported value",
        "institutional.weight": "Weight",
        "institutional.shares": "Shares / principal",
        "institutional.status": "Change",
        "institutional.share_change": "Share change",
        "institutional.none": "No comparable prior filing is available.",
        "institutional.loading": "Loading official SEC holdings disclosures…",
        "institutional.delayed": (
            "Form 13F discloses quarter-end positions after the fact. Never interpret "
            "this table as real-time ownership."
        ),
        "institutional.new": "New",
        "institutional.increased": "Increased",
        "institutional.decreased": "Decreased",
        "institutional.exited": "Exited",
        "institutional.unchanged": "Unchanged",
    },
    "ja": {
        "institutional.nav": "米国機関投資家の保有開示",
        "institutional.title": "米国機関投資家の保有開示",
        "institutional.subtitle": (
            "SEC公式の四半期保有開示を調査します。報告ポジションは遅延開示であり、"
            "現在のポートフォリオではありません。"
        ),
        "institutional.cik": "運用者CIK",
        "institutional.quarters": "四半期数",
        "institutional.load": "保有開示を取得",
        "institutional.manager": "運用者",
        "institutional.latest": "最新の報告保有",
        "institutional.changes": "前四半期比",
        "institutional.report_date": "基準日",
        "institutional.filing_date": "提出日",
        "institutional.lag": "開示遅延",
        "institutional.days": "日",
        "institutional.issuer": "発行体",
        "institutional.class": "種類",
        "institutional.cusip": "CUSIP",
        "institutional.value": "報告価値",
        "institutional.weight": "構成比",
        "institutional.shares": "株数 / 元本",
        "institutional.status": "変化",
        "institutional.share_change": "数量変化",
        "institutional.none": "比較可能な前四半期データがありません。",
        "institutional.loading": "SEC公式の保有開示を取得中…",
        "institutional.delayed": (
            "Form 13Fは四半期末ポジションの事後開示です。リアルタイム保有として解釈しません。"
        ),
        "institutional.new": "新規",
        "institutional.increased": "増加",
        "institutional.decreased": "減少",
        "institutional.exited": "売却",
        "institutional.unchanged": "変化なし",
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
    raise RuntimeError("Institutional translation catalogs must have identical keys")
