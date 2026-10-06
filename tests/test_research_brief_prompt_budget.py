"""Prompt-budget regression tests for the morning brief (t_95004a27).

The daily brief cron failed with pydantic ``string_too_long`` because
``MorningBriefService._brief_prompt`` embedded the whole evidence JSON, which
grows without bound (OHLCV rows alone reached ~41.5k chars). These tests pin
the contract of the prompt-only projection:

- the prompt fits inside ``AIMessage``'s 50000-char limit (with the
  ``_PROMPT_MAX_CHARS`` safety margin),
- citations anchors (source_url / retrieved_at / row_count / series_id /
  doc_id / symbols) and the discipline text survive compaction,
- staged narrowing keeps even pathological evidence inside the budget,
- the original evidence packet is never mutated.

All tests are offline: no agent, no network, no provider call.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, ClassVar

from yowayowa.config import Settings
from yowayowa.research_models import AIChatRequest, AIChatResponse, AIMessage, AIToolTrace
from yowayowa.services.macro_store import MacroObservationStore
from yowayowa.services.research_brief import (
    _PROMPT_MAX_CHARS,
    BRIEF_SECTIONS,
    MorningBriefService,
    _compact_evidence_for_prompt,
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
    """One ~793-char-class stock OHLCV row as the real store persists it:

    per-row provenance plus the cumulative valuation fields (notes,
    disclosure_date, pbr, per, net_cash_ratio, kiyohara_net_cash_ratio) that
    dominate the evidence JSON size.
    """

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
    }


def _market_packet(symbols: list[str], row_factory: Any, rows_per_symbol: int) -> dict[str, Any]:
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


def _edinet_entry(index: int) -> dict[str, Any]:
    return {
        "doc_id": f"S100BIG{index:03d}",
        "filer_name": f"提出法人{index}株式会社",
        "doc_description": "訂正有価証券報告書",
        "doc_type_code": "030",
        "submit_date_time": f"2026-10-05T0{index % 10}:00:00+09:00",
        "sec_code": f"{1300 + index:04d}" if index % 2 == 0 else None,
        "signal": "filing_forecast_revision",
        "source_url": "https://disclosure2.edinet-fsa.go.jp/week0001.json",
        "retrieved_at": "2026-10-05T23:00:02+00:00",
    }


def _macro_entry(index: int) -> dict[str, Any]:
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
        "announced_today": False,
    }


def _credit_candidate(index: int) -> dict[str, Any]:
    return {
        "signal": "credit_short_surge",
        "code": f"67{50 + index}",
        "run_date": RUN_DATE.isoformat(),
        "retrieved_at": NOW.isoformat(),
        "provenance": {
            "provider": "yahoo_finance_margin",
            "source": "Yahoo!ファイナンス 信用残 weekly history (quote page)",
            "source_url": f"https://finance.yahoo.co.jp/quote/67{50 + index}.T/margin",
            "license_class": "personal_only",
        },
    }


def _large_evidence(
    *,
    stock_symbols: int = 12,
    crypto_symbols: int = 2,
    rows_per_symbol: int = 3,
    edinet_filings: int = 12,
    edinet_large: int = 12,
    macro_series: int = 12,
) -> dict[str, Any]:
    """Synthetic evidence equivalent to the current production packet."""

    stocks = _market_packet(
        [f"SYM{index:03d}" for index in range(stock_symbols)],
        _stock_row,
        rows_per_symbol,
    )
    crypto = _market_packet(
        [f"CR{index:02d}" for index in range(crypto_symbols)],
        _crypto_row,
        rows_per_symbol,
    )
    return {
        "run_date": RUN_DATE.isoformat(),
        "edinet": {
            "filings": [_edinet_entry(index) for index in range(edinet_filings)],
            "large_filings": [_edinet_entry(index) for index in range(edinet_large)],
            "coverage": {"row_count": 1200, "classified": 24, "large_filing_count": edinet_large},
        },
        "credit_margin": {
            "candidates": [_credit_candidate(index) for index in range(2)],
            "coverage": {"run_date": RUN_DATE.isoformat(), "credit_candidates": 2},
        },
        "macro": {
            "series": [_macro_entry(index) for index in range(macro_series)],
            "coverage": {"series_count": macro_series, "announced_today_count": 0},
        },
        "prices": {
            "stocks": stocks,
            "crypto": crypto,
            "coverage": {
                "stock_symbols": stock_symbols,
                "stock_row_count": stock_symbols * rows_per_symbol,
                "crypto_symbols": crypto_symbols,
                "crypto_row_count": crypto_symbols * rows_per_symbol,
            },
        },
        "coverage_notes": {
            "missing_input_marker": "未取得",
            "rules": [
                "数値はEvidence JSONに存在する値のみ使用。推測・外挿・新規計算は禁止。",
                "Evidenceに無い入力は『未取得』と明記する。",
                "各主張に引用(docID/series_id/source_url/retrieved_at)を添える。",
            ],
        },
    }


class PromptBudgetFakeAgent:
    """Offline agent stand-in that records the AIChatRequest it receives."""

    instances: ClassVar[list[PromptBudgetFakeAgent]] = []

    def __init__(self, settings: Settings, session: Any = None) -> None:
        self.settings = settings
        self.session = session
        self.requests: list[AIChatRequest] = []
        PromptBudgetFakeAgent.instances.append(self)

    def chat(self, request: AIChatRequest) -> AIChatResponse:
        self.requests.append(request)
        return AIChatResponse(
            answer="1. 本日の注力ポイント\n調査起点として確認。",
            provider="openai_compatible",
            model="fake-model",
            tool_trace=[AIToolTrace(tool="noop", arguments={}, result_preview="[]")],
        )


def _budget_service(session: Any = None) -> MorningBriefService:
    """A service wired to absent stores: evidence comes from the caller only."""

    absent = Path("/nonexistent")
    agent_factory: Any = PromptBudgetFakeAgent
    return MorningBriefService(
        Settings(database_url="sqlite:///:memory:"),
        session,
        edinet_path=absent / "edinet-daily.jsonl",
        macro_store=MacroObservationStore(absent / "macro-observations"),
        stock_ohlcv_root=absent / "stock-ohlcv",
        crypto_ohlcv_root=absent / "crypto-ohlcv",
        agent_factory=agent_factory,
    )


class _SyntheticBriefService(MorningBriefService):
    """compose_brief over the synthetic packet (absent stores, fake agent)."""

    def __init__(self, evidence: dict[str, Any]) -> None:
        absent = Path("/nonexistent")
        agent_factory: Any = PromptBudgetFakeAgent
        super().__init__(
            Settings(database_url="sqlite:///:memory:"),
            None,
            edinet_path=absent / "edinet-daily.jsonl",
            macro_store=MacroObservationStore(absent / "macro-observations"),
            stock_ohlcv_root=absent / "stock-ohlcv",
            crypto_ohlcv_root=absent / "crypto-ohlcv",
            agent_factory=agent_factory,
        )
        self._evidence_override = evidence

    def assemble_edinet_summary(self, run_date: date) -> dict[str, Any]:
        return self._evidence_override["edinet"]

    def assemble_credit_summary(self, run_date: date) -> dict[str, Any]:
        return self._evidence_override["credit_margin"]

    def assemble_macro_summary(self, run_date: date) -> dict[str, Any]:
        return self._evidence_override["macro"]

    def assemble_price_summary(self) -> dict[str, Any]:
        return self._evidence_override["prices"]


def _reset_budget_fakes() -> None:
    PromptBudgetFakeAgent.instances = []


# ------------------------------------------------------------ regression: 50000


def test_current_scale_evidence_prompt_fits_aimessage_limit() -> None:
    """Regression for the six-day cron failure: the prompt must fit AIMessage.

    Failure reproduction: with the pre-fix implementation (raw evidence JSON
    embedded verbatim) this evidence yields a ~59k-char prompt and building
    ``AIMessage(role="user", content=prompt)`` raises
    ``pydantic.ValidationError: string_too_long`` (content max_length=50000).
    """

    evidence = _large_evidence()
    prompt = _budget_service()._brief_prompt(evidence)
    assert len(prompt) < 50000  # pre-fix this exceeded 50000 and crashed

    # The exact production failure mode: building the message must not raise.
    message = AIMessage(role="user", content=prompt)
    assert message.content == prompt


def test_current_scale_evidence_prompt_stays_under_safety_margin() -> None:
    evidence = _large_evidence()
    prompt = _budget_service()._brief_prompt(evidence)
    assert len(prompt) < _PROMPT_MAX_CHARS


# ------------------------------------------------------------ citations survive


def test_compact_prompt_keeps_citation_anchors_and_discipline() -> None:
    evidence = _large_evidence()
    service = _budget_service()
    prompt = service._brief_prompt(evidence)
    # Citation anchors survive compaction.
    assert "https://disclosure2.edinet-fsa.go.jp/week0001.json" in prompt  # source_url
    assert "2026-10-05T23:00:02+00:00" in prompt  # retrieved_at
    assert "row_count" in prompt
    assert "S100BIG000" in prompt  # doc_id
    assert "SERIES00000" in prompt  # series_id
    assert "SYM000" in prompt and "CR00" in prompt  # symbols
    # Discipline text and structure survive untouched.
    assert "捏造禁止" in prompt
    assert "未取得" in prompt
    assert "EVIDENCE JSON" in prompt
    assert f"{len(BRIEF_SECTIONS)}セクション" in prompt


# ------------------------------------------------- provenance values survive
# Regression for the CTO acceptance finding on 5d2c7c0: the per-symbol
# source block was built from the already-projected price-only rows, so
# provider/source_url/license_class/interval/currency/retrieved_at all came
# out null and every market-data citation URL was dropped from the prompt.
# The pre-existing ``assert "source_url" in prompt`` checks only the KEY
# name, which survives even when every VALUE is null — these assertions
# check the actual values instead. The two per-symbol tests FAIL on
# 5d2c7c0; the EDINET test is a defensive strengthening of the line-321
# assertion (EDINET provenance was never dropped, so it passes either way).


def test_prompt_keeps_per_symbol_source_url_values() -> None:
    """Every synthetic symbol's source_url VALUE reaches the prompt."""

    evidence = _large_evidence()
    prompt = _budget_service()._brief_prompt(evidence)
    newest_urls = [
        symbol_evidence["latest_rows"][0]["source_url"]
        for market_kind in ("stocks", "crypto")
        for symbol_evidence in evidence["prices"][market_kind]["symbols"].values()
    ]
    assert len(newest_urls) == 14
    for url in newest_urls:
        assert url in prompt  # fails at 5d2c7c0: value was null, URL dropped


def test_prompt_keeps_edinet_source_url_value() -> None:
    """The EDINET source_url value (not just the key name) reaches the prompt."""

    evidence = _large_evidence()
    prompt = _budget_service()._brief_prompt(evidence)
    edinet_urls = {
        entry["source_url"]
        for field in ("filings", "large_filings")
        for entry in evidence["edinet"][field]
    }
    assert edinet_urls
    for url in edinet_urls:
        assert url in prompt


def test_compact_evidence_source_block_matches_original_row() -> None:
    """Compact per-symbol source blocks carry the ORIGINAL row's provenance.

    Builds a small explicit packet (AAPL / BTC, matching the real-data
    repro) and compares the compacted ``source`` block against
    ``latest_rows[0]`` of the untouched evidence. Fails on 5d2c7c0, where
    the block was built from the projected price-only rows (all null).
    """

    aapl_row = _stock_row("AAPL", 0)
    btc_row = _crypto_row("BTC", 0)
    evidence: dict[str, Any] = {
        "prices": {
            "stocks": {
                "symbols": {
                    "AAPL": {"row_count": 1, "latest_rows": [aapl_row]},
                },
            },
            "crypto": {
                "symbols": {
                    "BTC": {"row_count": 1, "latest_rows": [btc_row]},
                },
            },
        },
    }
    compacted = _compact_evidence_for_prompt(evidence)
    for market_kind, symbol, row in (
        ("stocks", "AAPL", aapl_row),
        ("crypto", "BTC", btc_row),
    ):
        source = compacted["prices"][market_kind]["symbols"][symbol]["source"]
        assert source["source_url"] == row["source_url"]  # fails at 5d2c7c0 (None)
        assert source["provider"] == row["provider"]  # fails at 5d2c7c0 (None)
        assert source["license_class"] == row["license_class"]
        assert source["interval"] == row["interval"]
        assert source["currency"] == row["currency"]
        assert source["retrieved_at"] == row["retrieved_at"]

    # A key absent from the original row must not be fabricated as None.
    partial_row = {key: value for key, value in aapl_row.items() if key != "interval"}
    partial_evidence: dict[str, Any] = {
        "prices": {
            "stocks": {
                "symbols": {"AAPL": {"row_count": 1, "latest_rows": [partial_row]}},
            },
        },
    }
    partial_compact = _compact_evidence_for_prompt(partial_evidence)
    partial_source = partial_compact["prices"]["stocks"]["symbols"]["AAPL"]["source"]
    assert "interval" not in partial_source
    assert partial_source["source_url"] == partial_row["source_url"]


# ------------------------------------------------------------ budget enforcement


def test_extreme_evidence_prompt_enforces_budget_via_staged_narrowing() -> None:
    evidence = _large_evidence(
        stock_symbols=100,
        crypto_symbols=10,
        rows_per_symbol=10,
        edinet_filings=24,
        edinet_large=24,
        macro_series=12,
    )
    service = _budget_service()
    prompt = service._brief_prompt(evidence)
    assert len(prompt) < _PROMPT_MAX_CHARS
    # Even after narrowing, the anchors the model must cite remain present.
    assert "source_url" in prompt
    assert "retrieved_at" in prompt
    assert "row_count" in prompt
    assert "S100BIG000" in prompt
    assert "SERIES00000" in prompt
    assert "捏造禁止" in prompt
    assert "6セクション" in prompt


def test_compact_evidence_for_prompt_is_non_destructive() -> None:
    evidence = _large_evidence()
    before = json.dumps(evidence, ensure_ascii=False, sort_keys=True, default=str)
    _compact_evidence_for_prompt(evidence)
    after = json.dumps(evidence, ensure_ascii=False, sort_keys=True, default=str)
    assert before == after


def test_compose_brief_builds_aichat_request_without_string_too_long() -> None:
    """Full compose_brief run: AIChatRequest builds without string_too_long.

    The fake agent records the request during compose_brief; pydantic would
    raise at AIChatRequest construction if the prompt still exceeded 50000
    chars, so reaching the assertion below is itself the no-regression proof.
    """

    _reset_budget_fakes()
    seen: list[AIChatRequest] = []

    class _RecordingAgent(PromptBudgetFakeAgent):
        def chat(self, request: AIChatRequest) -> AIChatResponse:
            seen.append(request)
            return super().chat(request)

    service = _SyntheticBriefService(_large_evidence())
    service._agent_factory = lambda settings, session: _RecordingAgent(settings, session)
    brief = service.compose_brief(run_date=RUN_DATE, detected_at=NOW)
    assert seen, "fake agent must have received one chat request"
    request = seen[-1]
    assert len(request.messages[0].content) < 50000
    assert brief.sections == list(BRIEF_SECTIONS)
    # The citation layer is built from the untouched evidence, not the prompt
    # projection: stock citations still carry their own per-row provenance.
    stock_citations = [c for c in brief.citations if c.kind == "stock_ohlcv"]
    assert stock_citations
    assert all(citation.source_url for citation in stock_citations)
