from __future__ import annotations

from collections.abc import Mapping

_MESSAGES: dict[str, dict[str, str]] = {
    "en": {
        "edinet.nav": "EDINET",
        "edinet.title": "Japan filings / EDINET",
        "edinet.subtitle": (
            "Official FSA filing history and normalized financial facts from EDINET's own "
            "XBRL-to-CSV conversion."
        ),
        "edinet.history": "Company filing history",
        "edinet.history_note": (
            "Search the locally indexed official filing list by company. Index coverage is "
            "reported explicitly."
        ),
        "edinet.start_date": "From",
        "edinet.end_date": "To",
        "edinet.history_search": "Search history",
        "edinet.history_loading": "Searching indexed EDINET filing history…",
        "edinet.history_count": "{matched} filings · coverage {indexed}/{expected} days",
        "edinet.history_complete": "Complete coverage for the requested dates",
        "edinet.history_partial": "Partial coverage — unsynchronized dates may contain filings",
        "edinet.index_range": "Indexed range: {start} → {end}",
        "edinet.no_history": "No matching filing is present in the indexed coverage.",
        "edinet.daily_lookup": "Daily official lookup",
        "edinet.daily_note": "Query EDINET directly for one filing date.",
        "edinet.date": "Filing date",
        "edinet.security_code": "Security code",
        "edinet.security_hint": "4 or 5 digits",
        "edinet.csv_only": "CSV/XBRL only",
        "edinet.load": "Find filings",
        "edinet.key_missing": (
            "The server has no EDINET API key. Register a free key and configure "
            "YOWAYOWA_EDINET_API_KEY to use live EDINET access. Indexed history remains "
            "searchable if it has already been populated."
        ),
        "edinet.filings": "Filings",
        "edinet.result_count": "{matched} matched / {total} submitted",
        "edinet.no_filings": "No matching filing was found for this date.",
        "edinet.doc_id": "Doc ID",
        "edinet.filer": "Filer",
        "edinet.type": "Type",
        "edinet.period": "Period",
        "edinet.submitted": "Submitted",
        "edinet.csv": "CSV",
        "edinet.open": "Financials",
        "edinet.loading_filings": "Loading official EDINET filing list…",
        "edinet.loading_financials": "Loading EDINET XBRL-to-CSV facts…",
        "edinet.financials": "Normalized financials",
        "edinet.company": "Company",
        "edinet.code": "Security",
        "edinet.accounting": "Accounting",
        "edinet.fact_count": "Parsed facts",
        "edinet.metric": "Metric",
        "edinet.value": "Value",
        "edinet.unit": "Unit",
        "edinet.scope": "Scope / context",
        "edinet.element": "Source element",
        "edinet.contexts": "{count} contexts",
        "edinet.no_metrics": "No supported canonical financial metric was found in this filing.",
        "edinet.unavailable": "Unavailable canonical metrics",
        "edinet.parse_notes": "Parse notes",
        "edinet.raw_facts": "Raw fact search",
        "edinet.fact_query": "Element, label, context or value",
        "edinet.search": "Search facts",
        "edinet.fact_matches": "{matched} matched / {total} facts",
        "edinet.metric.revenue": "Revenue / net sales",
        "edinet.metric.gross_profit": "Gross profit",
        "edinet.metric.operating_income": "Operating income",
        "edinet.metric.ordinary_income": "Ordinary income",
        "edinet.metric.net_income": "Net income",
        "edinet.metric.assets": "Assets",
        "edinet.metric.current_assets": "Current assets",
        "edinet.metric.liabilities": "Liabilities",
        "edinet.metric.current_liabilities": "Current liabilities",
        "edinet.metric.equity": "Equity / net assets",
        "edinet.metric.cash": "Cash",
        "edinet.metric.operating_cash_flow": "Operating cash flow",
        "edinet.metric.investing_cash_flow": "Investing cash flow",
        "edinet.metric.financing_cash_flow": "Financing cash flow",
        "edinet.metric.eps_basic": "Basic EPS",
        "edinet.metric.eps_diluted": "Diluted EPS",
    },
    "ja": {
        "edinet.nav": "EDINET",
        "edinet.title": "日本企業開示 / EDINET",
        "edinet.subtitle": (
            "金融庁EDINETの提出履歴を会社起点で検索し、EDINET自身のXBRL→CSV変換から"
            "財務数値を正規化して調査します。"
        ),
        "edinet.history": "会社別の提出履歴",
        "edinet.history_note": (
            "公式提出一覧を蓄積したローカルインデックスから会社別に検索します。"
            "未同期期間は必ず明示します。"
        ),
        "edinet.start_date": "開始日",
        "edinet.end_date": "終了日",
        "edinet.history_search": "履歴を検索",
        "edinet.history_loading": "EDINET提出履歴インデックスを検索中…",
        "edinet.history_count": "{matched}件 · 対象期間の同期 {indexed}/{expected}日",
        "edinet.history_complete": "指定期間は全日同期済み",
        "edinet.history_partial": "一部未同期 — 未同期日に提出書類が存在する可能性があります",
        "edinet.index_range": "インデックス収録範囲: {start} → {end}",
        "edinet.no_history": "同期済み範囲には条件に一致する提出書類がありません。",
        "edinet.daily_lookup": "提出日を指定して直接検索",
        "edinet.daily_note": "1日分の提出一覧をEDINET APIへ直接問い合わせます。",
        "edinet.date": "提出日",
        "edinet.security_code": "証券コード",
        "edinet.security_hint": "4桁または5桁",
        "edinet.csv_only": "CSV/XBRLありのみ",
        "edinet.load": "提出書類を検索",
        "edinet.key_missing": (
            "サーバーにEDINET APIキーが設定されていません。無料のAPIキーを発行し、"
            "YOWAYOWA_EDINET_API_KEYを設定するとEDINETへの直接アクセスを利用できます。"
            "既に蓄積済みの履歴インデックスは検索できます。"
        ),
        "edinet.filings": "提出書類",
        "edinet.result_count": "{matched}件該当 / 提出{total}件",
        "edinet.no_filings": "この提出日では条件に一致する書類がありません。",
        "edinet.doc_id": "書類ID",
        "edinet.filer": "提出者",
        "edinet.type": "書類種別",
        "edinet.period": "対象期間",
        "edinet.submitted": "提出日時",
        "edinet.csv": "CSV",
        "edinet.open": "財務を見る",
        "edinet.loading_filings": "EDINET公式の提出書類一覧を取得中…",
        "edinet.loading_financials": "EDINET XBRL→CSVの財務Factを取得中…",
        "edinet.financials": "正規化財務",
        "edinet.company": "会社",
        "edinet.code": "証券コード",
        "edinet.accounting": "会計基準",
        "edinet.fact_count": "解析Fact数",
        "edinet.metric": "指標",
        "edinet.value": "値",
        "edinet.unit": "単位",
        "edinet.scope": "範囲 / コンテキスト",
        "edinet.element": "出典要素",
        "edinet.contexts": "{count}コンテキスト",
        "edinet.no_metrics": "対応する標準財務指標をこの書類から取得できませんでした。",
        "edinet.unavailable": "取得できなかった標準指標",
        "edinet.parse_notes": "解析注記",
        "edinet.raw_facts": "生Fact検索",
        "edinet.fact_query": "要素ID・項目名・コンテキスト・値",
        "edinet.search": "Factを検索",
        "edinet.fact_matches": "{matched}件該当 / 全{total}Fact",
        "edinet.metric.revenue": "売上高 / 営業収益",
        "edinet.metric.gross_profit": "売上総利益",
        "edinet.metric.operating_income": "営業利益",
        "edinet.metric.ordinary_income": "経常利益",
        "edinet.metric.net_income": "当期純利益",
        "edinet.metric.assets": "総資産",
        "edinet.metric.current_assets": "流動資産",
        "edinet.metric.liabilities": "負債",
        "edinet.metric.current_liabilities": "流動負債",
        "edinet.metric.equity": "純資産 / 資本",
        "edinet.metric.cash": "現金及び現金同等物",
        "edinet.metric.operating_cash_flow": "営業キャッシュフロー",
        "edinet.metric.investing_cash_flow": "投資キャッシュフロー",
        "edinet.metric.financing_cash_flow": "財務キャッシュフロー",
        "edinet.metric.eps_basic": "基本EPS",
        "edinet.metric.eps_diluted": "希薄化後EPS",
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
    raise RuntimeError("EDINET translation catalogs must have identical keys")
