"""Morning research brief service (P5-A A-1/A-2/A-3).

Contract:

- :meth:`MorningBriefService.assemble_edinet_summary` reads the local EDINET
  daily JSONL (``data/edinet-daily.jsonl``) and classifies filings with the
  same keyword table the P4-D screening pipeline uses
  (:func:`classify_edinet_filing`). docTypeCode ``030`` (有価証券届出書)
  rows are additionally flagged 大口提出 — the A-3 large-filing input.
- Credit margin surges come from the persisted screening candidates
  (:func:`read_screening_candidates`, signals with the ``credit_`` prefix).
- Macro updates come from the local macro observation JSONL files
  (``data/macro-observations/{bls,fred,treasury}.jsonl``), latest value per
  series with retrieved_at; a series whose as-of equals the run date gets
  「本日発表」 (the A-3 macro announcement input).
- :meth:`MorningBriefService.compose_brief` sends the assembled evidence
  packet through :class:`InvestmentResearchAgent`.chat under a strict
  prompt: five fixed sections, per-claim citations
  (docID/source_url/retrieved_at), no invented numbers, and every input
  that was not obtained must be stated as 未取得 and recorded in coverage.

Nothing here zero-fills: a missing EDINET file, an empty candidates table
and a missing macro file each render as 未取得 plus a coverage entry, and
the brief still composes from whatever evidence exists.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from yowayowa.config import Settings
from yowayowa.domain import LicenseClass, Provenance
from yowayowa.research_brief_models import BriefCitation, ResearchBrief
from yowayowa.research_models import AIChatRequest, AIChatResponse, AIMessage, AIProviderConfig
from yowayowa.services.ai_agent import InvestmentResearchAgent
from yowayowa.services.macro_store import MacroObservationStore
from yowayowa.services.ohlcv_evidence import (
    collect_crypto_evidence,
    collect_stock_evidence,
)
from yowayowa.services.screening_pipeline import (
    _DEFAULT_EDINET_PATH,
    classify_edinet_filing,
    read_screening_candidates,
)

__all__ = [
    "BRIEF_SECTIONS",
    "DEFAULT_NOTIFY_TARGET",
    "MorningBriefService",
    "persist_research_brief",
    "read_latest_research_brief",
]

BRIEF_SECTIONS: tuple[str, ...] = (
    "1. 本日の注力ポイント（エグゼクティブサマリー）",
    "2. EDINET提出状況（訂正・自己株式・公開買付・上場廃止・大口提出）",
    "3. 信用残急変銘柄",
    "4. マクロ更新（本日発表を明記）",
    "5. 次の調査アクション",
    "6. 市場データ（米株・暗号資産）",
)

DEFAULT_NOTIFY_TARGET = "telegram"

_HERMES_SEND_TIMEOUT_SECONDS = 60

_EDINET_LARGE_FILING_DOC_TYPE = "030"  # 有価証券届出書

_MAX_EDINET_LARGE_FILINGS = 12
_MAX_EDINET_SIGNALS = 12
_MAX_CREDIT_CANDIDATES = 12
_MAX_MACRO_SERIES = 12
_PRICE_ROWS_PER_SYMBOL = 3

# Prompt budget (P5-A regression fix, 2026-10): the evidence JSON is embedded
# verbatim into the AIMessage prompt, whose pydantic model caps ``content`` at
# 50000 chars (``research_models.AIMessage``). The compacted projection plus a
# deterministic staged-narrowing safety net keep the prompt inside
# ``_PROMPT_MAX_CHARS`` (a 5000-char margin below the model limit). The
# evidence packet itself, ``coverage`` and :meth:`collect_citations` never see
# this projection — citations keep their own per-row provenance.
_PROMPT_MAX_CHARS = 45000
_MAX_FILER_NAME_CHARS = 200
_MAX_DOC_DESCRIPTION_CHARS = 300
_MAX_MACRO_TITLE_CHARS = 200
_MAX_AVAILABLE_SYMBOLS_IN_PROMPT = 24
_NARROWED_EDINET_FILINGS = 6
_NARROWED_MACRO_SERIES = 6
_PRICES_DETAIL_OMITTED_NOTE = "prices_detail_omitted_for_prompt_budget"
_EVIDENCE_TRUNCATED_NOTE = "evidence_truncated_for_prompt_budget"


def _truncate_text(value: Any, max_chars: int) -> Any:
    """Return a possibly shortened copy of a string field (value-preserving)."""

    if not isinstance(value, str) or len(value) <= max_chars:
        return value
    return value[:max_chars]


def _compact_edinet_for_prompt(edinet: dict[str, Any]) -> dict[str, Any]:
    """Project EDINET filings to the prompt-relevant fields (pure copy).

    Per-filing provenance (``source_url`` / ``retrieved_at``) is shared across
    the whole evidence packet, so it is hoisted to one ``source`` block taken
    from the first row; per-filing identity (``doc_id`` etc.) stays on every
    entry.
    """

    filings = edinet.get("filings")
    large_filings = edinet.get("large_filings")
    if not isinstance(filings, list) and not isinstance(large_filings, list):
        return deepcopy(edinet)
    compacted: dict[str, Any] = deepcopy(edinet)

    def _project(entry: dict[str, Any]) -> dict[str, Any]:
        row: dict[str, Any] = {
            "doc_id": entry.get("doc_id"),
            "filer_name": _truncate_text(entry.get("filer_name"), _MAX_FILER_NAME_CHARS),
            "doc_description": _truncate_text(
                entry.get("doc_description"), _MAX_DOC_DESCRIPTION_CHARS
            ),
            "doc_type_code": entry.get("doc_type_code"),
            "submit_date_time": entry.get("submit_date_time"),
            "sec_code": entry.get("sec_code"),
            "signal": entry.get("signal"),
        }
        if "note" in entry:
            row["note"] = entry["note"]
        return row

    source: dict[str, Any] = {}
    if isinstance(filings, list):
        compacted["filings"] = [_project(entry) for entry in filings if isinstance(entry, dict)]
        for entry in filings:
            if isinstance(entry, dict) and entry.get("source_url") is not None:
                source = {
                    "source_url": entry.get("source_url"),
                    "retrieved_at": entry.get("retrieved_at"),
                }
                break
    if isinstance(large_filings, list):
        compacted["large_filings"] = [
            _project(entry) for entry in large_filings if isinstance(entry, dict)
        ]
        if not source:
            for entry in large_filings:
                if isinstance(entry, dict) and entry.get("source_url") is not None:
                    source = {
                        "source_url": entry.get("source_url"),
                        "retrieved_at": entry.get("retrieved_at"),
                    }
                    break
    if source:
        compacted["source"] = source
    return compacted


def _compact_macro_for_prompt(macro: dict[str, Any]) -> dict[str, Any]:
    """Project macro series to per-observation facts + per-source provenance."""

    series = macro.get("series")
    if not isinstance(series, list):
        return deepcopy(macro)
    compacted: dict[str, Any] = deepcopy(macro)
    sources: dict[str, dict[str, Any]] = {}
    projected: list[dict[str, Any]] = []
    for entry in series:
        if not isinstance(entry, dict):
            continue
        source_kind = entry.get("source_kind")
        projected.append(
            {
                "series_id": entry.get("series_id"),
                "title": _truncate_text(entry.get("title"), _MAX_MACRO_TITLE_CHARS),
                "value": entry.get("value"),
                "as_of": entry.get("as_of"),
                "period_label": entry.get("period_label"),
                "source_kind": source_kind,
                "announced_today": entry.get("announced_today"),
            }
        )
        key = str(source_kind)
        if key not in sources and entry.get("source_url") is not None:
            sources[key] = {
                "provider": entry.get("provider"),
                "source_url": entry.get("source_url"),
                "license_class": entry.get("license_class"),
                "retrieved_at": entry.get("retrieved_at"),
            }
    compacted["series"] = projected
    if sources:
        compacted["sources"] = sources
    return compacted


def _compact_market_for_prompt(market: dict[str, Any], *, market_kind: str) -> dict[str, Any]:
    """Project one OHLCV market packet: shared provenance + price-only rows.

    ``latest_rows`` keep only the per-bar price fields; the per-row provenance
    and cumulative valuation fields (symbol/notes/disclosure_date/pbr/per/
    net_cash_ratio/kiyohara_net_cash_ratio) are hoisted into one ``source``
    block per symbol, taken from the newest ORIGINAL row (``latest_rows[0]``)
    — never from the projected price-only rows, which carry no provenance.
    """

    row_fields: tuple[str, ...]
    if market_kind == "crypto":
        row_fields = ("as_of", "open", "high", "low", "close", "volume", "quote_volume")
    else:
        row_fields = ("as_of", "open", "high", "low", "close", "volume", "vwap", "trade_count")
    source_fields = ("provider", "source_url", "license_class", "interval", "currency")
    symbols = market.get("symbols")
    if not isinstance(symbols, dict):
        return deepcopy(market)
    compacted: dict[str, Any] = deepcopy(market)
    projected_symbols: dict[str, dict[str, Any]] = {}
    for symbol, symbol_evidence in symbols.items():
        if not isinstance(symbol_evidence, dict):
            projected_symbols[str(symbol)] = symbol_evidence
            continue
        # Hoist the per-symbol provenance from the ORIGINAL newest row
        # (``latest_rows[0]``), not from ``rows_out``: the projected rows keep
        # only price fields, so a source block built from them would be all
        # None and every market-data citation URL would silently vanish from
        # the prompt. Missing keys are simply omitted (no None fabrication).
        latest_rows = symbol_evidence.get("latest_rows")
        rows_out: list[dict[str, Any]] = []
        newest_original: dict[str, Any] | None = None
        if isinstance(latest_rows, list):
            for row in latest_rows:
                if isinstance(row, dict):
                    if newest_original is None:
                        newest_original = row
                    rows_out.append({field: row.get(field) for field in row_fields})
                else:
                    rows_out.append(row)
        symbol_out: dict[str, Any] = {
            "row_count": symbol_evidence.get("row_count"),
            "latest_rows": rows_out,
        }
        if newest_original is not None:
            source: dict[str, Any] = {
                field: newest_original[field]
                for field in (*source_fields, "retrieved_at")
                if field in newest_original
            }
            if source:
                symbol_out["source"] = source
        projected_symbols[str(symbol)] = symbol_out
    compacted["symbols"] = projected_symbols
    return compacted


def _compact_prices_for_prompt(prices: dict[str, Any]) -> dict[str, Any]:
    """Project the prices packet: per-symbol source blocks + price-only rows."""

    compacted = deepcopy(prices)
    for market_kind in ("stocks", "crypto"):
        market = compacted.get(market_kind)
        if isinstance(market, dict):
            compacted[market_kind] = _compact_market_for_prompt(market, market_kind=market_kind)
    return compacted


def _compact_evidence_for_prompt(evidence: dict[str, Any]) -> dict[str, Any]:
    """Prompt-only projection of the evidence packet (pure; non-destructive).

    The packet returned by :meth:`_evidence_packet` feeds both ``coverage``
    and :meth:`collect_citations`, so it must never be mutated. This builds an
    independent copy with the same facts in a compact shape: repeated
    per-row provenance and cumulative valuation fields are hoisted into
    per-symbol / per-source blocks, values themselves are passed through
    unchanged (no rounding, no new arithmetic).
    """

    compacted = deepcopy(evidence)
    edinet = compacted.get("edinet")
    if isinstance(edinet, dict):
        compacted["edinet"] = _compact_edinet_for_prompt(edinet)
    macro = compacted.get("macro")
    if isinstance(macro, dict):
        compacted["macro"] = _compact_macro_for_prompt(macro)
    prices = compacted.get("prices")
    if isinstance(prices, dict):
        compacted["prices"] = _compact_prices_for_prompt(prices)
    # credit_margin is small (bounded at 12 candidates): shallow copy suffices.
    return compacted


def _narrow_price_rows_for_prompt(data: dict[str, Any]) -> dict[str, Any]:
    """Stage 1: keep only the newest row per symbol."""

    prices = data.get("prices")
    if isinstance(prices, dict):
        for market_kind in ("stocks", "crypto"):
            market = prices.get(market_kind)
            if not isinstance(market, dict):
                continue
            symbols = market.get("symbols")
            if isinstance(symbols, dict):
                for symbol_evidence in symbols.values():
                    if isinstance(symbol_evidence, dict) and isinstance(
                        symbol_evidence.get("latest_rows"), list
                    ):
                        symbol_evidence["latest_rows"] = symbol_evidence["latest_rows"][:1]
    return data


def _cap_available_symbols_for_prompt(data: dict[str, Any]) -> dict[str, Any]:
    """Stage 2: cap the available-symbols lists."""

    prices = data.get("prices")
    if isinstance(prices, dict):
        for market_kind in ("stocks", "crypto"):
            market = prices.get(market_kind)
            if not isinstance(market, dict):
                continue
            available = market.get("available_symbols")
            if isinstance(available, list):
                market["available_symbols"] = available[:_MAX_AVAILABLE_SYMBOLS_IN_PROMPT]
    return data


def _drop_prices_detail(data: dict[str, Any]) -> dict[str, Any]:
    """Stage 3: drop per-symbol price detail, keep coverage and symbols lists."""

    prices = data.get("prices")
    if isinstance(prices, dict):
        for market_kind in ("stocks", "crypto"):
            market = prices.get(market_kind)
            if isinstance(market, dict):
                market.pop("symbols", None)
        notes = data.get("coverage_notes")
        if isinstance(notes, dict):
            rules = notes.get("rules")
            if isinstance(rules, list):
                rules.append(_PRICES_DETAIL_OMITTED_NOTE)
    return data


def _narrow_edinet_and_macro(data: dict[str, Any]) -> dict[str, Any]:
    """Final stage: head-truncate EDINET filings and macro series."""

    edinet = data.get("edinet")
    if isinstance(edinet, dict):
        for field in ("filings", "large_filings"):
            entries = edinet.get(field)
            if isinstance(entries, list):
                edinet[field] = entries[:_NARROWED_EDINET_FILINGS]
    macro = data.get("macro")
    if isinstance(macro, dict):
        series = macro.get("series")
        if isinstance(series, list):
            macro["series"] = series[:_NARROWED_MACRO_SERIES]
    notes = data.get("coverage_notes")
    if isinstance(notes, dict):
        rules = notes.get("rules")
        if isinstance(rules, list):
            rules.append(_EVIDENCE_TRUNCATED_NOTE)
    return data


def _render_brief_prompt(data: dict[str, Any]) -> str:
    """The fixed strict-prompt text around one (already compacted) JSON blob."""

    sections = "\n".join(BRIEF_SECTIONS)
    return "\n\n".join(
        [
            (
                "あなたはYowayowa-Investorの朝のリサーチブリーフ作成エージェントです。"
                "以下のEvidence JSONだけを根拠に、日本語でブリーフを書いてください。"
                f"構成は必ず次の{len(BRIEF_SECTIONS)}セクション順に従ってください:\n{sections}"
            ),
            "EVIDENCE JSON:\n" + json.dumps(data, ensure_ascii=False, default=str, indent=2),
            (
                "絶対規律:\n"
                "- Evidence JSONに無い数字を一切生成しない（捏造禁止）。"
                "算術・合計・増減率の新規計算もしない。\n"
                "- 入力が無い項目は『未取得』と明記する。\n"
                "- 各主張に docID / series_id / source_url / retrieved_at "
                "のいずれかを引用として添える。\n"
                "- スクリーニング候補は推奨売買ではなく調査起点であると明記する。\n"
                "- 簡潔に。ダミー文・一般論の羅列を書かない。"
            ),
        ]
    )


def _default_sender(message: str) -> None:
    """Send via the hermes CLI (no shell, no quoting pitfalls)."""

    subprocess.run(
        ["hermes", "send", "--to", DEFAULT_NOTIFY_TARGET, message],
        check=True,
        capture_output=True,
        timeout=_HERMES_SEND_TIMEOUT_SECONDS,
    )


class MorningBriefService:
    """Assembles deterministic evidence and composes one LLM morning brief."""

    def __init__(
        self,
        settings: Settings,
        session: Session | None,
        *,
        edinet_path: str | Path | None = None,
        macro_store: MacroObservationStore | None = None,
        stock_ohlcv_root: Path | None = None,
        crypto_ohlcv_root: Path | None = None,
        provider_config: AIProviderConfig | None = None,
        agent_factory: Callable[[Settings, Session | None], InvestmentResearchAgent] | None = None,
    ) -> None:
        self.settings = settings
        self.session = session
        self.edinet_path = Path(edinet_path) if edinet_path is not None else _DEFAULT_EDINET_PATH
        self.macro_store = macro_store
        self.stock_ohlcv_root = (
            stock_ohlcv_root if stock_ohlcv_root is not None else Path("./data/stock-ohlcv")
        )
        self.crypto_ohlcv_root = (
            crypto_ohlcv_root if crypto_ohlcv_root is not None else Path("./data/crypto-ohlcv")
        )
        self.provider_config = provider_config
        self._agent_factory = agent_factory

    # ------------------------------------------------------------- evidence

    def _macro_store_or_default(self) -> MacroObservationStore:
        if self.macro_store is not None:
            return self.macro_store
        from yowayowa.services.macro_store import default_macro_store

        return default_macro_store()

    def assemble_edinet_summary(self, run_date: date) -> dict[str, Any]:
        """Classified EDINET daily-list summary (deterministic, provenanced).

        Returns ``{"filings": [...], "large_filings": [...], "coverage": {...}}``.
        A missing file yields empty lists plus ``file_missing`` coverage —
        never an error, never fabricated rows.
        """

        coverage: dict[str, Any] = {
            "path": str(self.edinet_path),
            "row_count": 0,
            "classified": 0,
            "large_filing_count": 0,
        }
        if not self.edinet_path.is_file():
            coverage["reason"] = "file_missing"
            return {"filings": [], "large_filings": [], "coverage": coverage}
        filings: list[dict[str, Any]] = []
        large_filings: list[dict[str, Any]] = []
        classified = 0
        with self.edinet_path.open("r", encoding="utf-8") as handle:
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
                coverage["row_count"] += 1
                description_raw = row.get("docDescription")
                description = description_raw.strip() if isinstance(description_raw, str) else ""
                signal = classify_edinet_filing(description) if description else None
                is_large = row.get("docTypeCode") == _EDINET_LARGE_FILING_DOC_TYPE
                if signal is None and not is_large:
                    continue
                classified += 1
                entry: dict[str, Any] = {
                    "doc_id": row.get("docID"),
                    "filer_name": row.get("filerName"),
                    "doc_description": description,
                    "doc_type_code": row.get("docTypeCode"),
                    "submit_date_time": row.get("submitDateTime"),
                    "sec_code": row.get("secCode") or None,
                    "signal": signal.value if signal is not None else None,
                    "source_url": row.get("source_url"),
                    "retrieved_at": row.get("retrieved_at"),
                }
                if signal is not None:
                    filings.append(entry)
                if is_large:
                    note = "大口提出（有価証券届出書）"
                    large_entry = {**entry, "note": note}
                    large_filings.append(large_entry)
        coverage["classified"] = classified
        coverage["large_filing_count"] = len(large_filings)
        return {
            "filings": filings[:_MAX_EDINET_SIGNALS],
            "large_filings": large_filings[:_MAX_EDINET_LARGE_FILINGS],
            "coverage": coverage,
        }

    def assemble_credit_summary(self, run_date: date) -> dict[str, Any]:
        """Persisted credit-margin surge candidates (signal prefix ``credit_``)."""

        rows: list[dict[str, Any]] = []
        if self.session is not None:
            persisted = read_screening_candidates(self.session, run_date=run_date, limit=100)
        else:
            persisted = []
        for row in persisted:
            if not str(row.get("signal") or "").startswith("credit_"):
                continue
            rows.append(row)
        coverage: dict[str, Any] = {
            "run_date": run_date.isoformat(),
            "credit_candidates": len(rows),
        }
        if not rows:
            coverage["reason"] = "no_persisted_credit_candidates_for_run_date"
        return {"candidates": rows[:_MAX_CREDIT_CANDIDATES], "coverage": coverage}

    def assemble_macro_summary(self, run_date: date) -> dict[str, Any]:
        """Latest macro observation per series + 本日発表 flags (A-3 input)."""

        store = self._macro_store_or_default()
        latest = store.latest_by_series()
        series_out: list[dict[str, Any]] = []
        for observation in latest:
            as_of = observation.get("as_of")
            announced_today = as_of == run_date.isoformat()
            series_out.append({**observation, "announced_today": announced_today})
        coverage: dict[str, Any] = {
            "series_count": len(series_out),
            "announced_today_count": sum(1 for row in series_out if row["announced_today"]),
        }
        if not series_out:
            coverage["reason"] = "no_macro_observation_files"
        return {"series": series_out[:_MAX_MACRO_SERIES], "coverage": coverage}

    def assemble_price_summary(self) -> dict[str, Any]:
        """Saved daily OHLCV for stocks + crypto (section 6 input).

        Ambient collection over the whole store (``question=""`` so nothing
        counts as "mentioned"): newest three rows per symbol. A missing or
        empty store is an explicit coverage entry — never zero-fill.
        """

        stocks = collect_stock_evidence(
            self.stock_ohlcv_root, "", rows_per_symbol=_PRICE_ROWS_PER_SYMBOL
        )
        crypto = collect_crypto_evidence(
            self.crypto_ohlcv_root, "", rows_per_symbol=_PRICE_ROWS_PER_SYMBOL
        )
        return {
            "stocks": stocks,
            "crypto": crypto,
            "coverage": {
                "stock_symbols": stocks["coverage"]["symbol_count"],
                "stock_row_count": stocks["coverage"]["row_count_total"],
                "crypto_symbols": crypto["coverage"]["symbol_count"],
                "crypto_row_count": crypto["coverage"]["row_count_total"],
            },
        }

    def collect_citations(
        self,
        edinet: dict[str, Any],
        credit: dict[str, Any],
        macro: dict[str, Any],
        prices: dict[str, Any] | None = None,
    ) -> list[BriefCitation]:
        citations: list[BriefCitation] = []
        for entry in [*edinet["filings"], *edinet["large_filings"]]:
            citations.append(
                BriefCitation(
                    provider="edinet-v2",
                    source="EDINET API Version 2 daily document list (type=2)",
                    source_url=entry.get("source_url"),
                    retrieved_at=entry.get("retrieved_at"),
                    as_of=(entry.get("submit_date_time") or "")[:10] or None,
                    kind="edinet_filing",
                    code_or_series=entry.get("doc_id"),
                    note=entry.get("note"),
                )
            )
        for row in credit["candidates"]:
            provenance = row.get("provenance") or {}
            citations.append(
                BriefCitation(
                    provider=str(provenance.get("provider") or "credit_margin"),
                    source=provenance.get("source"),
                    source_url=provenance.get("source_url"),
                    retrieved_at=row.get("retrieved_at"),
                    as_of=str(row.get("run_date")),
                    kind="credit_margin",
                    code_or_series=row.get("code"),
                )
            )
        for row in macro["series"]:
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
        if prices:
            for market, kind in (("stocks", "stock_ohlcv"), ("crypto", "crypto_ohlcv")):
                market_evidence = prices.get(market) or {}
                for symbol, symbol_evidence in market_evidence.get("symbols", {}).items():
                    latest_rows = symbol_evidence.get("latest_rows") or []
                    if not latest_rows:
                        continue  # no persisted rows -> 未取得 in coverage, not a citation
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

    # -------------------------------------------------------------- prompt

    def _evidence_packet(
        self,
        run_date: date,
        edinet: dict[str, Any],
        credit: dict[str, Any],
        macro: dict[str, Any],
        prices: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        packet: dict[str, Any] = {
            "run_date": run_date.isoformat(),
            "edinet": edinet,
            "credit_margin": credit,
            "macro": macro,
            "coverage_notes": {
                "missing_input_marker": "未取得",
                "rules": [
                    "数値はEvidence JSONに存在する値のみ使用。推測・外挿・新規計算は禁止。",
                    "Evidenceに無い入力は『未取得』と明記する。",
                    "各主張に引用（docID/series_id/source_url/retrieved_at）を添える。",
                ],
            },
        }
        if prices is not None:
            packet["prices"] = prices
        return packet

    def _brief_prompt(self, evidence: dict[str, Any]) -> str:
        """Build the strict prompt inside ``AIMessage``'s 50000-char limit.

        The evidence is first projected through
        :func:`_compact_evidence_for_prompt` (prompt-only; the packet itself
        is never touched). A deterministic staged narrowing then enforces
        ``_PROMPT_MAX_CHARS``: each stage re-renders and re-measures, and the
        discipline text and section list are never shortened.
        """

        data = _compact_evidence_for_prompt(evidence)
        prompt = _render_brief_prompt(data)
        if len(prompt) < _PROMPT_MAX_CHARS:
            return prompt
        # Stage 1: one row per symbol.
        prompt = _render_brief_prompt(_narrow_price_rows_for_prompt(data))
        if len(prompt) < _PROMPT_MAX_CHARS:
            return prompt
        # Stage 2: cap the available-symbols lists.
        prompt = _render_brief_prompt(_cap_available_symbols_for_prompt(data))
        if len(prompt) < _PROMPT_MAX_CHARS:
            return prompt
        # Stage 3: drop per-symbol price detail, keep coverage + symbol lists.
        prompt = _render_brief_prompt(_drop_prices_detail(data))
        if len(prompt) < _PROMPT_MAX_CHARS:
            return prompt
        # Final stage: head-truncate EDINET filings and macro series.
        return _render_brief_prompt(_narrow_edinet_and_macro(data))

    # -------------------------------------------------------------- compose

    def _agent(self) -> InvestmentResearchAgent:
        if self._agent_factory is not None:
            return self._agent_factory(self.settings, self.session)
        session = self.session
        if session is None:
            raise RuntimeError(
                "MorningBriefService requires a database session for the AI agent "
                "(screening/strategy tools read persisted state)"
            )
        return InvestmentResearchAgent(self.settings, session)

    def compose_brief(
        self,
        run_date: date | None = None,
        *,
        detected_at: datetime | None = None,
    ) -> ResearchBrief:
        """Assemble evidence, run one strict-prompt agent chat, return the brief."""

        resolved_date = run_date or datetime.now(UTC).date()
        now = detected_at or datetime.now(UTC)
        edinet = self.assemble_edinet_summary(resolved_date)
        credit = self.assemble_credit_summary(resolved_date)
        macro = self.assemble_macro_summary(resolved_date)
        prices = self.assemble_price_summary()
        evidence = self._evidence_packet(resolved_date, edinet, credit, macro, prices)
        citations = self.collect_citations(edinet, credit, macro, prices)

        coverage: dict[str, Any] = {
            "edinet": edinet["coverage"],
            "credit_margin": credit["coverage"],
            "macro": macro["coverage"],
            "prices": prices["coverage"],
            "missing_inputs": [],
        }
        if edinet["coverage"].get("reason") == "file_missing":
            coverage["missing_inputs"].append("edinet_daily_jsonl: 未取得")
        if not edinet["filings"] and not edinet["large_filings"]:
            coverage["missing_inputs"].append("edinet_signal_filings: 0件")
        if not credit["candidates"]:
            coverage["missing_inputs"].append("credit_margin_surges: 未取得")
        if not macro["series"]:
            coverage["missing_inputs"].append("macro_observations: 未取得")
        if not prices["stocks"]["coverage"]["row_count_total"]:
            coverage["missing_inputs"].append("stock_ohlcv: 未取得")
        if not prices["crypto"]["coverage"]["row_count_total"]:
            coverage["missing_inputs"].append("crypto_ohlcv: 未取得")

        request = AIChatRequest(
            messages=[AIMessage(role="user", content=self._brief_prompt(evidence))],
            provider=self.provider_config,
            context={"surface": "research_brief", "run_date": resolved_date.isoformat()},
        )
        response = self._agent().chat(request)
        return ResearchBrief(
            run_date=resolved_date,
            sections=list(BRIEF_SECTIONS),
            answer=response.answer,
            citations=citations,
            coverage=coverage,
            provenance=self._brief_provenance(response, now),
            generated_at=now,
            provider=response.provider,
            model=response.model,
        )

    def _brief_provenance(self, response: AIChatResponse, now: datetime) -> Provenance:
        notes = [
            "LLM-generated summary over locally captured evidence; "
            "deterministic coverage in ResearchBrief.coverage.",
            f"evidence providers: edinet-v2, credit_margin_weekly, "
            f"macro-observations JSONL, stock-ohlcv JSONL, crypto-ohlcv JSONL; "
            f"model: {response.model}",
        ]
        tool_sources = sorted({trace.tool for trace in response.tool_trace})
        if tool_sources:
            notes.append("tools used: " + ",".join(tool_sources))
        return Provenance(
            provider=f"yowayowa-research-brief/{response.provider}",
            source="Morning brief assembled from EDINET daily list, credit margin "
            "screening candidates, macro observation JSONL and the persisted "
            "stock/crypto daily OHLCV stores",
            license_class=LicenseClass.OFFICIAL_PUBLIC
            if self._all_sources_official()
            else LicenseClass.PERSONAL_ONLY,
            retrieved_at=now,
            as_of=now,
            notes=notes,
        )

    def _all_sources_official(self) -> bool:
        # Credit margin candidates carry PERSONAL_ONLY scraped provenance; when
        # any is present the combined brief inherits the stricter class.
        if self.session is None:
            return False
        rows = read_screening_candidates(self.session, limit=100)
        return not any(str(row.get("signal") or "").startswith("credit_") for row in rows)

    # ---------------------------------------------------------------- send

    def send_brief(
        self,
        brief: ResearchBrief,
        *,
        sender: Callable[[str], None] | None = None,
    ) -> None:
        """Deliver the brief through ``hermes send`` (Telegram home channel).

        ``sender`` is injectable for tests; the default is a subprocess
        list-argv ``hermes send --to telegram`` (shell is never used).
        """

        deliver = sender if sender is not None else _default_sender
        deliver(self.render_message(brief))

    @staticmethod
    def render_message(brief: ResearchBrief) -> str:
        return (
            f"朝のリサーチブリーフ {brief.run_date.isoformat()} "
            f"(provider={brief.provider}/{brief.model})\n\n{brief.answer}"
        )


# --------------------------------------------------------------- persistence


def persist_research_brief(session: Session, brief: ResearchBrief) -> dict[str, int]:
    """Replace the run date's row atomically (delete + insert, one commit).

    Re-generating for the same run date is idempotent, mirroring
    ``persist_screening_run``. Returns ``{"inserted": n, "updated": n}`` where
    ``updated`` counts the rows replaced by this write.
    """

    from sqlalchemy import delete

    from yowayowa.db import ResearchBriefRecord

    try:
        replaced_result = session.execute(
            delete(ResearchBriefRecord).where(ResearchBriefRecord.run_date == brief.run_date)
        )
        replaced_rowcount: int | None = replaced_result.rowcount  # type: ignore[attr-defined]
        replaced_count = int(replaced_rowcount) if replaced_rowcount is not None else 0
        session.add(
            ResearchBriefRecord(
                run_date=brief.run_date,
                payload=brief.model_dump(mode="json", exclude={"provenance"}),
                provenance=brief.provenance.model_dump(mode="json"),
                generated_at=brief.generated_at,
            )
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return {"inserted": 1, "updated": replaced_count}


def read_latest_research_brief(session: Session) -> ResearchBrief | None:
    """The most recently generated persisted brief, or ``None``.

    ``provenance`` is restored from its own column; a legacy/degenerate row
    without a provenance payload degrades to ``None`` rather than being
    fabricated.
    """

    from sqlalchemy import select

    from yowayowa.db import ResearchBriefRecord

    row = session.scalar(
        select(ResearchBriefRecord).order_by(ResearchBriefRecord.generated_at.desc()).limit(1)
    )
    if row is None:
        return None
    payload = dict(row.payload)
    if not isinstance(payload.get("provenance"), dict):
        payload["provenance"] = row.provenance
    return ResearchBrief.model_validate(payload)


def read_research_brief(session: Session, run_date: date) -> ResearchBrief | None:
    """The persisted brief for one run date, or ``None``."""

    from sqlalchemy import select

    from yowayowa.db import ResearchBriefRecord

    row = session.scalar(
        select(ResearchBriefRecord).where(ResearchBriefRecord.run_date == run_date).limit(1)
    )
    if row is None:
        return None
    payload = dict(row.payload)
    if not isinstance(payload.get("provenance"), dict):
        payload["provenance"] = row.provenance
    return ResearchBrief.model_validate(payload)
