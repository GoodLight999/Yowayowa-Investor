"""Read-only OHLCV evidence collectors for research ask / morning brief (P5-A).

Wraps the persisted per-symbol JSONL stores
(``stock_acquisition.StockOhlcvStore`` under ``data/stock-ohlcv``,
``crypto_acquisition.CryptoOhlcvStore`` under ``data/crypto-ohlcv``) so the
LLM research surfaces can cite saved daily bars as evidence:

- ``available_symbols`` lists the store's per-symbol directories (sorted).
- ``mentioned_symbols`` scans the question for those symbols, case
  insensitive, on word boundaries (the neighbours of the match are not
  alphanumeric), returned in question order.
- ``collect_stock_evidence`` / ``collect_crypto_evidence`` build the packet:
  per-symbol row counts plus the newest rows, with explicit coverage.
  A missing/empty store root is a normal empty packet — never an error and
  never zero-filled.

The collectors are strictly read-only: they never touch the network and
never write to the stores.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from yowayowa.crypto_acquisition import CryptoOhlcvStore
from yowayowa.stock_acquisition import _TIMELINE_MAX_READ, StockOhlcvStore

__all__ = [
    "available_symbols",
    "collect_crypto_evidence",
    "collect_stock_evidence",
    "mentioned_symbols",
    "question_ticker_tokens",
]

_DEFAULT_ROWS_PER_SYMBOL = 5
_DEFAULT_MAX_SYMBOLS = 12

# Ticker-like tokens in a question, for missing-symbol detection: 2-5 ASCII
# UPPERCASE letters (optional class-share suffix), word-bounded. Uppercase in
# the ORIGINAL text is required so English prose words ("what", "is") never
# register; Japanese sentences carry tickers verbatim (AAPL, TSLA, BRK.B).
_QUESTION_TOKEN = re.compile(r"(?<![A-Za-z0-9])([A-Z]{2,5}(?:\.[A-Z])?)(?![A-Za-z0-9])")

# Currency mentions that would otherwise look like unknown tickers.
_CURRENCY_TOKENS = frozenset({"USD", "JPY", "EUR"})


def question_ticker_tokens(question: str) -> list[str]:
    """Ordered unique ticker-like tokens written in the question.

    Unlike :func:`mentioned_symbols` this does NOT require the symbol to be
    persisted: it is the input for 未取得 marking of asked-but-unsaved
    symbols (e.g. TSLA with an AAPL/MSFT/NVDA store). Currency codes (USD /
    JPY / EUR) are excluded.
    """

    tokens: list[str] = []
    for match in _QUESTION_TOKEN.finditer(question):
        token = match.group(1)
        if token in _CURRENCY_TOKENS:
            continue
        if token not in tokens:
            tokens.append(token)
    return tokens


def available_symbols(root: Path) -> list[str]:
    """Sorted per-symbol directory names under the store root ([] if absent)."""

    if not root.is_dir():
        return []
    return sorted(entry.name for entry in root.iterdir() if entry.is_dir())


def mentioned_symbols(question: str, available: list[str]) -> list[str]:
    """Available symbols that appear in the question, in question order.

    Matching is case-insensitive with ASCII word boundaries: the characters
    immediately before and after a match must not be an ASCII letter or
    digit. CJK characters (「AAPLの…」) count as boundaries so Japanese
    sentences match without surrounding spaces, while ``AAPLX`` never
    matches ``AAPL``. Symbols are never repaired or expanded — only exact
    stored symbols are reported.
    """

    lowered = question.lower()
    hits: list[tuple[int, str]] = []
    for symbol in available:
        token = symbol.lower()
        if not token:
            continue
        position = lowered.find(token)
        while position != -1:
            before_ok = position == 0 or not _is_ascii_word_char(lowered[position - 1])
            end = position + len(token)
            after_ok = end >= len(lowered) or not _is_ascii_word_char(lowered[end])
            if before_ok and after_ok:
                hits.append((position, symbol))
                break
            position = lowered.find(token, position + 1)
    hits.sort(key=lambda hit: hit[0])
    return [symbol for _, symbol in hits]


def _is_ascii_word_char(character: str) -> bool:
    """ASCII letter/digit check: CJK neighbours are boundaries, not part of a symbol."""

    return character.isascii() and character.isalnum()


def _collect_market_evidence(
    root: Path,
    question: str,
    store: StockOhlcvStore | CryptoOhlcvStore,
    *,
    rows_per_symbol: int,
    max_symbols: int,
) -> dict[str, Any]:
    """Shared collector over one store; see ``collect_stock_evidence``."""

    available = available_symbols(root)
    mentioned = mentioned_symbols(question, available)
    # Mentioned symbols always make the cut; remaining slots fill from the
    # head of the sorted available list (ambient context).
    selected = mentioned[:max_symbols]
    for symbol in available:
        if len(selected) >= max_symbols:
            break
        if symbol not in selected:
            selected.append(symbol)
    symbols: dict[str, dict[str, Any]] = {}
    row_count_total = 0
    for symbol in selected:
        try:
            # Same read window the stores expose (both store modules define
            # the identical _TIMELINE_MAX_READ = 10_000 cap).
            rows = store.read(symbol, limit=_TIMELINE_MAX_READ)
        except ValueError:
            continue  # a directory that is not a valid symbol is skipped, not invented
        row_count = len(rows)
        symbols[symbol] = {
            "row_count": row_count,
            "latest_rows": rows[: max(0, rows_per_symbol)],
        }
        row_count_total += row_count
    return {
        "available_symbols": available,
        "mentioned": mentioned,
        "symbols": symbols,
        "coverage": {"symbol_count": len(symbols), "row_count_total": row_count_total},
    }


def collect_stock_evidence(
    root: Path,
    question: str,
    *,
    rows_per_symbol: int = _DEFAULT_ROWS_PER_SYMBOL,
    max_symbols: int = _DEFAULT_MAX_SYMBOLS,
) -> dict[str, Any]:
    """Evidence packet from the persisted US-stock daily OHLCV store."""

    return _collect_market_evidence(
        root,
        question,
        StockOhlcvStore(root),
        rows_per_symbol=rows_per_symbol,
        max_symbols=max_symbols,
    )


def collect_crypto_evidence(
    root: Path,
    question: str,
    *,
    rows_per_symbol: int = _DEFAULT_ROWS_PER_SYMBOL,
    max_symbols: int = _DEFAULT_MAX_SYMBOLS,
) -> dict[str, Any]:
    """Evidence packet from the persisted crypto daily OHLCV store."""

    return _collect_market_evidence(
        root,
        question,
        CryptoOhlcvStore(root),
        rows_per_symbol=rows_per_symbol,
        max_symbols=max_symbols,
    )
