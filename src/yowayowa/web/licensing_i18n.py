from __future__ import annotations

from collections.abc import Mapping

_MESSAGES: dict[str, dict[str, str]] = {
    "en": {
        "macro.public_title": "Official public macro",
        "macro.public_note": (
            "License-clear original statistical agencies are preferred for public analysis. "
            "Japan e-Stat, BLS and BEA remain traceable to their official sources."
        ),
        "macro.bls": "BLS public-domain series",
        "macro.bea": "BEA National Accounts",
        "macro.bea_note": (
            "GDP and consumption data direct from the original U.S. statistical agency."
        ),
        "macro.bea_key": "BEA access requires a free registered API key on the server.",
        "macro.estat": "Japan e-Stat official statistics",
        "macro.estat_note": (
            "Search official Japanese statistics by meaning, then inspect table dimensions before "
            "requesting facts. This avoids hard-coding table IDs that can change at base revisions."
        ),
        "macro.estat_query": "Search official Japanese statistics",
        "macro.estat_placeholder": "Consumer Price Index, unemployment rate, wages…",
        "macro.estat_search": "Search e-Stat",
        "macro.estat_key": "e-Stat access requires a free registered application ID on the server.",
        "macro.estat_results": "e-Stat tables",
        "macro.estat_dimensions": "Dimensions / filters",
        "macro.estat_data": "Official facts",
        "macro.estat_load": "Load facts",
        "macro.estat_filter_hint": (
            "Leave a dimension blank for all values; comma-separated codes are allowed."
        ),
        "macro.estat_count": "{count} table(s)",
        "macro.estat_facts": "{shown} shown / {total} fact(s)",
        "macro.fred_personal": "FRED · personal / BYOK",
        "macro.fred_rights_note": (
            "Series rights vary by original provider. Generic FRED output stays personal-only."
        ),
        "licenses.title": "Data sources & licenses",
        "licenses.subtitle": (
            "Auditable source policy for public display, API redistribution and derived analysis"
        ),
        "licenses.mode": "Runtime mode",
        "licenses.public_safe": "Public-safe",
        "licenses.restricted": "Restricted",
        "licenses.source": "Source",
        "licenses.access": "Access",
        "licenses.display": "Public display",
        "licenses.api": "Public API",
        "licenses.derived": "Derived analysis",
        "licenses.attribution": "Attribution",
        "licenses.reviewed": "Reviewed",
        "licenses.yes": "Yes",
        "licenses.no": "No",
        "licenses.none": "Not required",
        "licenses.policy_note": (
            "Public mode fails closed: unknown or unapproved sources cannot become public API data."
        ),
        "licenses.local_note": (
            "Personal local enrichment is isolated from public mode and must retain "
            "source provenance."
        ),
        "licenses.footer": "Data licenses",
    },
    "ja": {
        "macro.public_title": "公式・公開可能マクロ",
        "macro.public_note": (
            "一般公開できる分析では、権利関係が明確な原統計機関を優先します。"
            "日本のe-Stat・BLS・BEAはいずれも公式ソースまで追跡できます。"
        ),
        "macro.bls": "BLS パブリックドメイン系列",
        "macro.bea": "BEA 国民経済計算",
        "macro.bea_note": "GDP・消費などを米国の原統計機関から直接取得します。",
        "macro.bea_key": "BEAデータ取得にはサーバー側の無料登録APIキーが必要です。",
        "macro.estat": "日本 e-Stat 政府統計",
        "macro.estat_note": (
            "日本の公式統計を意味から検索し、統計表の分類軸を確認してからFactを取得します。"
            "基準改定で統計表IDが変わっても、固定ID依存を避けられます。"
        ),
        "macro.estat_query": "日本の公式統計を検索",
        "macro.estat_placeholder": "消費者物価指数、完全失業率、賃金…",
        "macro.estat_search": "e-Statを検索",
        "macro.estat_key": "e-Statデータ取得にはサーバー側の無料登録アプリケーションIDが必要です。",
        "macro.estat_results": "e-Stat 統計表",
        "macro.estat_dimensions": "分類軸 / フィルタ",
        "macro.estat_data": "公式Fact",
        "macro.estat_load": "Factを取得",
        "macro.estat_filter_hint": (
            "空欄の分類軸は全件対象です。コードはカンマ区切りで複数指定できます。"
        ),
        "macro.estat_count": "{count}表",
        "macro.estat_facts": "表示{shown}件 / 全{total}Fact",
        "macro.fred_personal": "FRED · 個人利用 / BYOK",
        "macro.fred_rights_note": (
            "系列ごとに原提供者の権利条件が異なるため、汎用FRED出力は個人モード限定です。"
        ),
        "licenses.title": "データ源とライセンス",
        "licenses.subtitle": "公開表示・API再配布・派生分析の可否を監査可能な形で管理",
        "licenses.mode": "実行モード",
        "licenses.public_safe": "公開可能",
        "licenses.restricted": "制限あり",
        "licenses.source": "データ源",
        "licenses.access": "取得条件",
        "licenses.display": "公開表示",
        "licenses.api": "公開API",
        "licenses.derived": "派生分析",
        "licenses.attribution": "出典表示",
        "licenses.reviewed": "確認日",
        "licenses.yes": "可",
        "licenses.no": "不可",
        "licenses.none": "不要",
        "licenses.policy_note": (
            "公開モードはfail-closedです。未登録・未承認のデータ源は公開APIへ流せません。"
        ),
        "licenses.local_note": (
            "個人ローカル拡張は公開モードから隔離し、必ず出典情報を保持します。"
        ),
        "licenses.footer": "データライセンス",
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
    raise RuntimeError("Licensing locale catalogs must have matching keys")
