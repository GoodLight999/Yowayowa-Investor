"""Cross-evidence research question answering (P5-A).

``research_ask`` is a two-stage pipeline:

1. Deterministic evidence collection — no LLM involved. The question's
   mentioned codes (4/5-digit JPX-looking tokens) are matched against the
   persisted screening candidates and the local EDINET daily JSONL, and the
   latest macro observations join the packet as ambient context.
2. One strict :class:`InvestmentResearchAgent` chat round over that packet.
   The prompt forbids arithmetic and invented numbers: every numeric claim
   must come from the packet (or a ``get_*`` tool result).

The response separates the model answer from the deterministic
``tool_trace`` (which evidence lookups ran) and per-family coverage so a
missing source renders as 未取得 instead of an empty string.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from yowayowa.config import Settings
from yowayowa.fx_models import SUPPORTED_CRYPTO_ASSETS
from yowayowa.research_brief_models import BriefCitation, ResearchAskResponse
from yowayowa.research_models import AIChatRequest, AIMessage, AIProviderConfig
from yowayowa.services.ai_agent import InvestmentResearchAgent
from yowayowa.services.macro_store import MacroObservationStore
from yowayowa.services.ohlcv_evidence import (
    collect_crypto_evidence,
    collect_stock_evidence,
    question_ticker_tokens,
)
from yowayowa.services.research_brief import MorningBriefService
from yowayowa.services.screening_pipeline import _DEFAULT_EDINET_PATH, read_screening_candidates

__all__ = ["research_ask"]

_CODE_TOKEN = re.compile(r"(?<!\d)(\d{4,5})(?!\d)")

_MAX_CANDIDATES_PER_CODE = 8
_MAX_MACRO_SERIES = 12
_MAX_QUESTION_CHARS = 2000
_DEFAULT_STOCK_OHLCV_ROOT = Path("./data/stock-ohlcv")
_DEFAULT_CRYPTO_OHLCV_ROOT = Path("./data/crypto-ohlcv")


def _mentioned_codes(question: str) -> list[str]:
    """4/5-digit tokens in the question (never repaired, never expanded)."""

    codes: list[str] = []
    for match in _CODE_TOKEN.finditer(question):
        token = match.group(1)
        if token not in codes:
            codes.append(token)
    return codes


def _edinet_rows_for_codes(
    edinet_path: Path,
    codes: list[str],
) -> list[dict[str, Any]]:
    """EDINET daily rows whose secCode matches a mentioned code."""

    if not codes or not edinet_path.is_file():
        return []
    wanted = set(codes)
    rows: list[dict[str, Any]] = []
    with edinet_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                row = json.loads(stripped)
            except ValueError:
                continue
            if not isinstance(row, dict):
                continue
            sec_code = row.get("secCode")
            if not isinstance(sec_code, str):
                continue
            if sec_code in wanted or (sec_code[:4] if len(sec_code) == 5 else "") in wanted:
                rows.append(row)
    return rows


def _ask_prompt(question: str, evidence: dict[str, Any]) -> str:
    return "\n\n".join(
        [
            (
                "あなたはYowayowa-InvestorのリサーチQ&Aエージェントです。"
                "以下のEvidence JSONだけを根拠に質問へ回答してください（日本語）。"
                "Evidenceに無い数字を生成したり、自由計算（合計・増減率・比較の新規算術）を"
                "してはいけません。数値はEvidenceまたはツール結果からそのまま引用し、"
                "出典（docID/series_id/source_url/retrieved_at）を添えてください。"
                "根拠が無い部分は『未取得』と明記してください。"
            ),
            f"USER QUESTION:\n{question}",
            "EVIDENCE JSON:\n" + json.dumps(evidence, ensure_ascii=False, default=str, indent=2),
        ]
    )


def research_ask(
    question: str,
    session: Session | None,
    settings: Settings,
    *,
    provider_config: AIProviderConfig | None = None,
    edinet_path: str | Path | None = None,
    macro_store: MacroObservationStore | None = None,
    stock_store: Path | None = None,
    crypto_store: Path | None = None,
    agent_factory: Any = None,
) -> ResearchAskResponse:
    """Answer one research question from local evidence + one agent round."""

    clean_question = question.strip()[:_MAX_QUESTION_CHARS]
    now = datetime.now(UTC)
    resolved_edinet = Path(edinet_path) if edinet_path is not None else _DEFAULT_EDINET_PATH
    resolved_stock_root = stock_store if stock_store is not None else _DEFAULT_STOCK_OHLCV_ROOT
    resolved_crypto_root = crypto_store if crypto_store is not None else _DEFAULT_CRYPTO_OHLCV_ROOT
    codes = _mentioned_codes(clean_question)

    tool_trace: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    if session is not None:
        for code in codes:
            found = read_screening_candidates(
                session,
                code=code,
                limit=_MAX_CANDIDATES_PER_CODE,
            )
            candidates.extend(found)
            tool_trace.append(
                {
                    "tool": "read_screening_candidates",
                    "arguments": {"code": code},
                    "matched": len(found),
                }
            )
    edinet_rows = _edinet_rows_for_codes(resolved_edinet, codes)
    tool_trace.append(
        {
            "tool": "edinet_daily_lookup",
            "arguments": {"codes": codes, "path": str(resolved_edinet)},
            "matched": len(edinet_rows),
        }
    )

    stock_evidence = collect_stock_evidence(resolved_stock_root, clean_question)
    tool_trace.append(
        {
            "tool": "stock_ohlcv_lookup",
            "arguments": {"mentioned": stock_evidence["mentioned"]},
            "matched": stock_evidence["coverage"]["row_count_total"],
        }
    )
    crypto_evidence = collect_crypto_evidence(resolved_crypto_root, clean_question)
    tool_trace.append(
        {
            "tool": "crypto_ohlcv_lookup",
            "arguments": {"mentioned": crypto_evidence["mentioned"]},
            "matched": crypto_evidence["coverage"]["row_count_total"],
        }
    )
    # Ticker-like tokens the question wrote but neither store has are the
    # OHLCV missing-symbol candidates (e.g. TSLA with an AAPL/MSFT store).
    asked_tickers = question_ticker_tokens(clean_question)
    ohlcv_mentioned = sorted(
        set(stock_evidence["mentioned"]) | set(crypto_evidence["mentioned"]) | set(asked_tickers)
    )

    brief_service = MorningBriefService(
        settings,
        session,
        edinet_path=resolved_edinet,
        macro_store=macro_store,
        provider_config=provider_config,
    )
    macro = brief_service.assemble_macro_summary(now.date())
    tool_trace.append(
        {
            "tool": "macro_latest_by_series",
            "arguments": {},
            "matched": len(macro["series"]),
        }
    )

    evidence = {
        "question": clean_question,
        "mentioned_codes": codes,
        "screening_candidates": candidates,
        "edinet_filings": edinet_rows[:_MAX_CANDIDATES_PER_CODE],
        "stock_ohlcv": stock_evidence,
        "crypto_ohlcv": crypto_evidence,
        "macro_latest": macro["series"][:_MAX_MACRO_SERIES],
        "coverage_notes": {
            "rules": [
                "数値はEvidence JSONとツール結果からのみ。自由計算禁止。",
                "根拠の無い項目は『未取得』と明記。",
                "各主張に出典（docID/series_id/source_url/retrieved_at）を添える。",
            ]
        },
    }

    if agent_factory is not None:
        agent = agent_factory(settings, session)
    elif session is not None:
        agent = InvestmentResearchAgent(settings, session)
    else:
        raise RuntimeError(
            "research_ask requires a database session for the AI agent "
            "(screening/strategy tools read persisted state)"
        )
    request = AIChatRequest(
        messages=[AIMessage(role="user", content=_ask_prompt(clean_question, evidence))],
        provider=provider_config,
        context={"surface": "research_ask"},
        max_tool_rounds=3,
    )
    response = agent.chat(request)

    citations = _collect_ask_citations(
        candidates,
        edinet_rows,
        macro["series"],
        stock_evidence,
        crypto_evidence,
    )
    coverage: dict[str, Any] = {
        "mentioned_codes": codes,
        "screening_candidates_matched": len(candidates),
        "edinet_filings_matched": len(edinet_rows),
        "stock_ohlcv_rows": stock_evidence["coverage"]["row_count_total"],
        "crypto_ohlcv_rows": crypto_evidence["coverage"]["row_count_total"],
        "macro_series": len(macro["series"]),
        "missing_inputs": [],
    }
    if codes and not candidates:
        coverage["missing_inputs"].append("screening_candidates: 未取得")
    if codes and not edinet_rows:
        coverage["missing_inputs"].append("edinet_daily_filings: 未取得")
    stock_available = set(stock_evidence["available_symbols"])
    crypto_available = set(crypto_evidence["available_symbols"])
    stock_mentions = set(stock_evidence["mentioned"])
    crypto_mentions = set(crypto_evidence["mentioned"])
    for symbol in ohlcv_mentioned:
        # Classify missing symbols only when there is evidence for the asset
        # class. Never silently call an unknown token a stock: BTC/ETH are
        # known crypto assets even when their local store is empty, and an
        # otherwise unknown token can inherit a single unambiguous market
        # context from another symbol in the same question.
        stock_known = symbol in stock_available
        crypto_known = symbol in crypto_available or symbol in SUPPORTED_CRYPTO_ASSETS
        if not stock_known and not crypto_known:
            if stock_mentions and not crypto_mentions:
                stock_known = True
            elif crypto_mentions and not stock_mentions:
                crypto_known = True

        if stock_known:
            stock_rows = stock_evidence["symbols"].get(symbol, {}).get("row_count", 0)
            if stock_rows == 0:
                coverage["missing_inputs"].append(f"stock_ohlcv {symbol}: 未取得")
        if crypto_known:
            crypto_rows = crypto_evidence["symbols"].get(symbol, {}).get("row_count", 0)
            if crypto_rows == 0:
                coverage["missing_inputs"].append(f"crypto_ohlcv {symbol}: 未取得")
        if not stock_known and not crypto_known:
            coverage["missing_inputs"].append(f"ohlcv {symbol}: 未取得")
    if not macro["series"]:
        coverage["missing_inputs"].append("macro_observations: 未取得")

    return ResearchAskResponse(
        question=clean_question,
        answer=response.answer,
        citations=citations,
        tool_trace=[*tool_trace, *[trace.model_dump(mode="json") for trace in response.tool_trace]],
        coverage=coverage,
        provider=response.provider,
        model=response.model,
        generated_at=now,
    )


def _collect_ask_citations(
    candidates: list[dict[str, Any]],
    edinet_rows: list[dict[str, Any]],
    macro_series: list[dict[str, Any]],
    stock_evidence: dict[str, Any] | None = None,
    crypto_evidence: dict[str, Any] | None = None,
) -> list[BriefCitation]:
    citations: list[BriefCitation] = []
    for row in candidates:
        provenance = row.get("provenance") or {}
        citations.append(
            BriefCitation(
                provider=str(provenance.get("provider") or "screening"),
                source=provenance.get("source"),
                source_url=provenance.get("source_url"),
                retrieved_at=row.get("retrieved_at"),
                as_of=str(row.get("run_date")),
                kind=str(row.get("source") or "screening"),
                code_or_series=row.get("code"),
            )
        )
    for row in edinet_rows:
        citations.append(
            BriefCitation(
                provider="edinet-v2",
                source="EDINET API Version 2 daily document list (type=2)",
                source_url=row.get("source_url"),
                retrieved_at=row.get("retrieved_at"),
                as_of=(row.get("submitDateTime") or "")[:10] or None,
                kind="edinet_filing",
                code_or_series=row.get("docID"),
            )
        )
    for row in macro_series:
        citations.append(
            BriefCitation(
                provider=str(row.get("provider") or row.get("source_kind")),
                source=f"macro-observations/{row.get('source_kind')}.jsonl",
                source_url=row.get("source_url"),
                retrieved_at=row.get("retrieved_at"),
                as_of=row.get("as_of"),
                kind="macro",
                code_or_series=row.get("series_id"),
            )
        )
    for evidence, kind in (
        (stock_evidence, "stock_ohlcv"),
        (crypto_evidence, "crypto_ohlcv"),
    ):
        if not evidence:
            continue
        for symbol, symbol_evidence in evidence.get("symbols", {}).items():
            latest_rows = symbol_evidence.get("latest_rows") or []
            if not latest_rows:
                continue  # a symbol with no persisted rows gets 未取得, not a citation
            row = latest_rows[0]
            citations.append(
                BriefCitation(
                    provider=str(row.get("provider") or kind),
                    source=row.get("source_url"),
                    source_url=row.get("source_url"),
                    retrieved_at=row.get("retrieved_at"),
                    as_of=row.get("as_of"),
                    kind=kind,
                    code_or_series=symbol,
                )
            )
    return citations
