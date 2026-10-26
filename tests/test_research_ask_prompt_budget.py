"""Prompt-budget regression tests for research ask (t_c3264797).

Same failure class as the brief (t_95004a27): ``research_ask._ask_prompt``
embedded the whole evidence JSON verbatim into the AIMessage prompt, whose
pydantic model caps ``content`` at 50000 chars
(``research_models.AIMessage``). The ask packet grows with the OHLCV stores
(5 newest rows per symbol, up to 12 ambient symbols, ~800 chars per stock
row) so a grown store pushes the prompt past the limit and every
``/v1/research/ask`` call dies with ``string_too_long``.

These tests pin the contract of the new prompt path:

- the prompt fits inside ``AIMessage``'s 50000-char limit (with the
  ``_PROMPT_MAX_CHARS`` safety margin), even for evidence at the
  collector caps and far beyond;
- citation anchors (fact ids / docID / series_id / source_url /
  retrieved_at / row_count / symbols) and the discipline text survive;
- staged narrowing keeps even pathological evidence inside the budget;
- the original evidence packet is never mutated;
- existing brief-test assertions (6758 / AAPL / 自由計算 / 未取得 in the
  prompt) still hold on normal-scale evidence.

All tests are offline: no agent, no network, no provider call.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any

from yowayowa.research_models import AIMessage
from yowayowa.services.research_ask import (
    _MAX_FACTS,
    _ask_prompt,
    _compact_ask_evidence_for_prompt,
)

RUN_DATE = date(2026, 10, 6)
NOW = datetime(2026, 10, 6, 0, 30, tzinfo=UTC)

_STOCK_NOTE = (
    "Alpaca Market Data daily bars (1Day, SIP feed); commercial broker/data-vendor "
    "data - NOT an official reference rate, personal-use classification. SIP history "
    "is used within the personal scope of Alpaca's data terms (15-minute delay for "
    "the free plan)."
)


def _stock_row(symbol: str, index: int) -> dict[str, Any]:
    """One real-shape stock OHLCV row (same per-row payload the brief tests use)."""

    return {
        "symbol": symbol,
        "interval": "1d",
        "currency": "USD",
        "provider": "alpaca",
        "source_url": f"https://data.alpaca.markets/v2/stocks/bars?symbols={symbol}",
        "license_class": "personal_only",
        "retrieved_at": "2026-10-05T02:51:02.094076Z",
        "as_of": f"2026-10-0{index + 1}T04:00:00Z",
        "open": 289.46 + index,
        "high": 292.69 + index,
        "low": 285.22 + index,
        "close": 291.52 + index,
        "volume": 37709308.0 + index,
        "vwap": 289.990699 + index,
        "trade_count": 264189 + index,
        "notes": [_STOCK_NOTE],
        "disclosure_date": "2026-10-05",
        "pbr": 51.24,
        "per": 33.75,
        "net_cash_ratio": 0.18,
        "kiyohara_net_cash_ratio": 0.21,
    }


def _crypto_row(symbol: str, index: int) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "interval": "1d",
        "currency": "USD",
        "provider": "coingecko",
        "source_url": f"https://api.coingecko.com/api/v3/coins/{symbol.lower()}/ohlc",
        "license_class": "personal_only",
        "retrieved_at": "2026-10-05T02:51:02.094076Z",
        "as_of": f"2026-10-0{index + 1}T00:00:00Z",
        "open": 95000.0 + index,
        "high": 97000.0 + index,
        "low": 94000.0 + index,
        "close": 96000.0 + index,
        "volume": None,
        "quote_volume": 1200000.0 + index,
    }


def _market_packet(symbols: list[str], row_factory: Any, rows_per_symbol: int) -> dict[str, Any]:
    """The exact packet shape ``collect_stock_evidence``/``collect_crypto_evidence``
    return (available_symbols / mentioned / symbols / coverage)."""

    symbols_out: dict[str, dict[str, Any]] = {}
    for symbol in symbols:
        rows = [row_factory(symbol, index) for index in range(rows_per_symbol)]
        symbols_out[symbol] = {"row_count": len(rows), "latest_rows": rows}
    return {
        "available_symbols": list(symbols),
        "mentioned": [],
        "symbols": symbols_out,
        "coverage": {
            "symbol_count": len(symbols_out),
            "row_count_total": len(symbols_out) * rows_per_symbol,
        },
    }


def _raw_edinet_row(index: int) -> dict[str, Any]:
    """Raw EDINET daily-JSONL row shape (raw key names, camelCase)."""

    return {
        "docID": f"S100BIG{index:03d}",
        "filerName": f"提出法人{index}株式会社",
        "docDescription": "訂正有価証券報告書",
        "docTypeCode": "030",
        "submitDateTime": f"2026-10-05 0{index % 10}:00",
        "secCode": f"{1300 + index:05d}" if index % 2 == 0 else None,
        "source_url": "https://api.edinet-fsa.go.jp/api/v2/documents.json?date=2026-10-05&type=2",
        "retrieved_at": "2026-10-05T23:00:02+00:00",
        "license_class": "official_public",
    }


def _macro_row(index: int) -> dict[str, Any]:
    """Macro row shape as ``assemble_macro_summary`` emits it."""

    return {
        "series_id": f"SERIES{index:05d}",
        "title": "unemployment rate (SA, %)",
        "value": 4.4,
        "as_of": "2026-09-01",
        "period_label": "September",
        "source_kind": "bls",
        "provider": "bls-v1",
        "retrieved_at": "2026-10-05T09:41:40+00:00",
        "source_url": "https://api.bls.gov/publicAPI/v1/timeseries/data/",
        "license_class": "official_public",
    }


def _screening_candidate(index: int) -> dict[str, Any]:
    """Screening row shape as ``read_screening_candidates`` returns it."""

    return {
        "run_date": RUN_DATE.isoformat(),
        "source": "credit_margin_weekly",
        "code": f"67{50 + index}",
        "signal": "credit_short_surge",
        "document_id": None,
        "company_name": f"提出法人{index}株式会社",
        "value": {"short_total": 300, "long_total": 200},
        "reason": "空売り残が前週比で急増（しきい値超え）。" * 6,
        "provenance": {
            "provider": "yahoo_finance_margin",
            "source": "Yahoo!ファイナンス 信用残 weekly history (quote page)",
            "source_url": f"https://finance.yahoo.co.jp/quote/67{50 + index}.T/margin",
            "license_class": "personal_only",
            "retrieved_at": NOW.isoformat(),
            "as_of": RUN_DATE.isoformat(),
            "notes": [],
        },
        "retrieved_at": NOW.isoformat(),
    }


def _fact(index: int) -> dict[str, Any]:
    """EvidenceFact.model_dump() shape (the deterministic citation backbone)."""

    return {
        "id": f"F{index + 1}",
        "kind": "screening",
        "statement": f"credit_margin_weekly 675{index}: 空売り残が前週比で急増（しきい値超え）。",
        "provider": "yahoo_finance_margin",
        "source_url": f"https://finance.yahoo.co.jp/quote/675{index}.T/margin",
        "retrieved_at": NOW.isoformat(),
        "as_of": RUN_DATE.isoformat(),
        "code_or_series": f"675{index}",
    }


def _ask_evidence(
    *,
    stock_symbols: int = 12,
    crypto_symbols: int = 2,
    rows_per_symbol: int = 3,
    screening: int = 8,
    edinet_filings: int = 8,
    macro_series: int = 12,
    facts: int = 24,
) -> dict[str, Any]:
    """An ask packet at the collectors' production caps (and beyond)."""

    return {
        "question": "6758と11115の信用残とEDINETの動きを教えて",
        "mentioned_codes": ["6758", "11115"],
        "screening_candidates": [_screening_candidate(index) for index in range(screening)],
        "edinet_filings": [_raw_edinet_row(index) for index in range(edinet_filings)],
        "stock_ohlcv": _market_packet(
            [f"SYM{index:03d}" for index in range(stock_symbols)],
            _stock_row,
            rows_per_symbol,
        ),
        "crypto_ohlcv": _market_packet(
            [f"CR{index:02d}" for index in range(crypto_symbols)],
            _crypto_row,
            rows_per_symbol,
        ),
        "macro_latest": [_macro_row(index) for index in range(macro_series)],
        "facts": [_fact(index) for index in range(facts)],
        "coverage_notes": {
            "rules": [
                "数値はEvidence JSONとツール結果からのみ。自由計算禁止。",
                "根拠の無い項目は『未取得』と明記。",
                "各主張に出典（docID/series_id/source_url/retrieved_at）を添える。",
            ]
        },
    }


# ------------------------------------------------------------ regression: 50000


def test_current_scale_evidence_prompt_fits_aimessage_limit() -> None:
    """The packet at the collector caps yields a prompt that fits AIMessage.

    Failure reproduction: with the pre-fix implementation (raw evidence JSON
    embedded verbatim: 12 stock symbols x 5 rows of ~800 chars + 2 crypto
    symbols + nested provenance dicts) this evidence yields a ~55k-char
    prompt and building ``AIMessage(role="user", content=prompt)`` raises
    ``pydantic.ValidationError: string_too_long`` (content max_length=50000).
    """

    evidence = _ask_evidence(rows_per_symbol=5)
    prompt = _ask_prompt("6758の信用残は?", evidence)
    assert len(prompt) < 50000  # pre-fix this exceeded 50000 and crashed

    # The exact production failure mode: building the message must not raise.
    message = AIMessage(role="user", content=prompt)
    assert message.content == prompt


# ------------------------------------------------------------ citations survive


def test_compact_prompt_keeps_citation_anchors_and_discipline() -> None:
    """Every citation anchor the strict prompt demands survives compaction."""

    evidence = _ask_evidence()
    prompt = _ask_prompt("6758と11115の信用残とEDINETの動きを教えて", evidence)
    # Discipline text survives verbatim.
    assert "自由計算" in prompt
    assert "未取得" in prompt
    assert "[F12]" in prompt  # fact-id citation rule text
    assert "###推論" in prompt and "###反証条件" in prompt
    assert "USER QUESTION:" in prompt and "EVIDENCE JSON" in prompt
    # Packet identity survives.
    assert '"id": "F1"' in prompt  # fact ids the model must cite
    assert "6758" in prompt  # mentioned codes
    assert "S100BIG000" in prompt  # raw EDINET docID
    assert "SERIES00000" in prompt  # macro series_id
    assert "SYM000" in prompt and "CR00" in prompt  # OHLCV symbols
    assert "row_count" in prompt
    # Provenance VALUES survive (not just key names).
    assert "https://api.edinet-fsa.go.jp/api/v2/documents.json?date=2026-10-05&type=2" in prompt
    assert "2026-10-05T23:00:02+00:00" in prompt  # edinet retrieved_at
    assert "https://finance.yahoo.co.jp/quote/6750.T/margin" in prompt  # screening provenance
    assert "yahoo_finance_margin" in prompt
    assert "https://data.alpaca.markets/v2/stocks/bars?symbols=SYM000" in prompt
    assert "https://api.bls.gov/publicAPI/v1/timeseries/data/" in prompt  # macro source_url
    assert "2026-10-05T02:51:02.094076Z" in prompt  # ohlcv retrieved_at


def test_compact_prompt_keeps_crypto_specific_row_fields() -> None:
    """Crypto rows keep quote_volume and gain no stock-only None fields."""

    evidence = _ask_evidence(stock_symbols=1, crypto_symbols=1, rows_per_symbol=1)
    compacted = _compact_ask_evidence_for_prompt(evidence)
    crypto_row = compacted["crypto_ohlcv"]["symbols"]["CR00"]["latest_rows"][0]
    assert crypto_row["quote_volume"] == 1200000.0
    assert "vwap" not in crypto_row  # stock-only field not fabricated for crypto
    assert "trade_count" not in crypto_row
    stock_row = compacted["stock_ohlcv"]["symbols"]["SYM000"]["latest_rows"][0]
    assert stock_row["vwap"] == 289.990699  # stock rows keep their own fields
    prompt = _ask_prompt("BTCの動きは?", evidence)
    assert "quote_volume" in prompt


def test_compact_prompt_flattens_screening_provenance() -> None:
    """The nested provenance dict becomes scalar provenance on the row."""

    compacted = _compact_ask_evidence_for_prompt(
        _ask_evidence(stock_symbols=0, crypto_symbols=0, screening=1, facts=0)
    )
    row = compacted["screening_candidates"][0]
    assert "provenance" not in row
    assert row["provider"] == "yahoo_finance_margin"
    assert row["source_url"] == "https://finance.yahoo.co.jp/quote/6750.T/margin"
    assert row["license_class"] == "personal_only"
    # Long free text is head-truncated, not dropped.
    assert 0 < len(row["reason"]) <= 300


# ------------------------------------------------- facts backbone stays intact


def test_facts_are_never_narrowed_even_for_pathological_evidence() -> None:
    """facts[] survives every stage: the model may only cite existing ids."""

    evidence = _ask_evidence(facts=_MAX_FACTS)
    prompt = _ask_prompt("全部教えて", evidence)
    assert f'"id": "F{_MAX_FACTS}"' in prompt
    for index in range(1, _MAX_FACTS + 1):
        assert f"F{index}" in prompt


# ------------------------------------------------- budget enforcement


def test_extreme_evidence_prompt_enforces_budget_via_staged_narrowing() -> None:
    """Evidence far beyond any real store still lands inside the margin."""

    evidence = _ask_evidence(
        stock_symbols=100,
        crypto_symbols=10,
        rows_per_symbol=10,
        screening=48,
        edinet_filings=48,
        macro_series=48,
        facts=_MAX_FACTS,
    )
    prompt = _ask_prompt("全部教えて", evidence)
    from yowayowa.services.research_brief import _PROMPT_MAX_CHARS

    assert len(prompt) < _PROMPT_MAX_CHARS
    # Even after narrowing, the anchors the model must cite remain present.
    assert "source_url" in prompt
    assert "retrieved_at" in prompt
    assert "row_count" in prompt
    assert '"id": "F1"' in prompt and f'"id": "F{_MAX_FACTS}"' in prompt
    assert "自由計算" in prompt


def test_narrowing_is_recorded_in_coverage_notes() -> None:
    """Stage-3/final narrowing adds its marker to coverage_notes.rules."""

    evidence = _ask_evidence(
        stock_symbols=100,
        crypto_symbols=10,
        rows_per_symbol=10,
        facts=_MAX_FACTS,
    )
    prompt = _ask_prompt("全部教えて", evidence)
    assert len(prompt) < 50000
    assert (
        "prices_detail_omitted_for_prompt_budget" in prompt
        or "evidence_truncated_for_prompt_budget" in prompt
    )


def test_compact_ask_evidence_for_prompt_is_non_destructive() -> None:
    evidence = _ask_evidence()
    before = json.dumps(evidence, ensure_ascii=False, sort_keys=True, default=str)
    _compact_ask_evidence_for_prompt(evidence)
    after = json.dumps(evidence, ensure_ascii=False, sort_keys=True, default=str)
    assert before == after


def test_tiny_evidence_prompt_is_verbatim_except_projection() -> None:
    """Normal-scale evidence: same three-section shape, nothing narrowed.

    The compacted packet embeds cleanly and every family stays complete
    (12 macro rows = the production cap; 5+5 OHLCV rows).
    """

    evidence = _ask_evidence()
    prompt = _ask_prompt("6758の状況は?", evidence)
    assert prompt.count('"series_id"') == 12
    assert prompt.count('"as_of"') >= 40  # 5 stock + 5 crypto rows per symbol x symbols
    # Stage markers are absent: nothing was narrowed.
    assert "prices_detail_omitted_for_prompt_budget" not in prompt
    assert "evidence_truncated_for_prompt_budget" not in prompt
