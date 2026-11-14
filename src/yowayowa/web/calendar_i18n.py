from __future__ import annotations

from collections.abc import Mapping

_MESSAGES: dict[str, dict[str, str]] = {
    "en": {
        "calendar.scope": "Universe",
        "calendar.scope.market": "Market-wide",
        "calendar.scope.saved": "All saved symbols",
        "calendar.scope.watchlist": "Watchlist · {name}",
        "calendar.scope.portfolio": "Portfolio · {name}",
        "calendar.dividend": "Dividend",
        "calendar.tracked_types": "Tracked event types",
        "calendar.tracked_unavailable": "Ticker calendar unavailable: {symbols}",
        "calendar.tracked_empty": "No saved symbols in this universe.",
        "calendar.subtype.earnings": "Earnings date",
        "calendar.subtype.ex_dividend": "Ex-dividend date",
        "calendar.subtype.dividend_payment": "Dividend payment date",
        "calendar.subtype.other": "Ticker event",
    },
    "ja": {
        "calendar.scope": "対象",
        "calendar.scope.market": "市場全体",
        "calendar.scope.saved": "保存済み銘柄すべて",
        "calendar.scope.watchlist": "ウォッチリスト · {name}",
        "calendar.scope.portfolio": "ポートフォリオ · {name}",
        "calendar.dividend": "配当",
        "calendar.tracked_types": "保有・監視銘柄のイベント種別",
        "calendar.tracked_unavailable": "銘柄カレンダー取得不可: {symbols}",
        "calendar.tracked_empty": "この対象には保存済み銘柄がありません。",
        "calendar.subtype.earnings": "決算予定日",
        "calendar.subtype.ex_dividend": "権利落ち日",
        "calendar.subtype.dividend_payment": "配当支払日",
        "calendar.subtype.other": "銘柄イベント",
    },
}


def messages(locale: str) -> Mapping[str, str]:
    return _MESSAGES.get(locale, _MESSAGES["en"])


def translate(locale: str, key: str, **values: object) -> str:
    text = messages(locale).get(key, key)
    return text.format(**values) if values else text


if set(_MESSAGES["ja"]) != set(_MESSAGES["en"]):
    raise RuntimeError("Calendar translation catalogs must have identical keys")
