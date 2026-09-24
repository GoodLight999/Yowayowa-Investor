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
)

DEFAULT_NOTIFY_TARGET = "telegram"

_HERMES_SEND_TIMEOUT_SECONDS = 60

_EDINET_LARGE_FILING_DOC_TYPE = "030"  # 有価証券届出書

_MAX_EDINET_LARGE_FILINGS = 12
_MAX_EDINET_SIGNALS = 12
_MAX_CREDIT_CANDIDATES = 12
_MAX_MACRO_SERIES = 12


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
        provider_config: AIProviderConfig | None = None,
        agent_factory: Callable[[Settings, Session | None], InvestmentResearchAgent] | None = None,
    ) -> None:
        self.settings = settings
        self.session = session
        self.edinet_path = Path(edinet_path) if edinet_path is not None else _DEFAULT_EDINET_PATH
        self.macro_store = macro_store
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

    def collect_citations(
        self, edinet: dict[str, Any], credit: dict[str, Any], macro: dict[str, Any]
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
        return citations

    # -------------------------------------------------------------- prompt

    def _evidence_packet(
        self,
        run_date: date,
        edinet: dict[str, Any],
        credit: dict[str, Any],
        macro: dict[str, Any],
    ) -> dict[str, Any]:
        return {
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

    def _brief_prompt(self, evidence: dict[str, Any]) -> str:
        sections = "\n".join(BRIEF_SECTIONS)
        return "\n\n".join(
            [
                (
                    "あなたはYowayowa-Investorの朝のリサーチブリーフ作成エージェントです。"
                    "以下のEvidence JSONだけを根拠に、日本語でブリーフを書いてください。"
                    f"構成は必ず次の5セクション順に従ってください:\n{sections}"
                ),
                "EVIDENCE JSON:\n"
                + json.dumps(evidence, ensure_ascii=False, default=str, indent=2),
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
        evidence = self._evidence_packet(resolved_date, edinet, credit, macro)
        citations = self.collect_citations(edinet, credit, macro)

        coverage: dict[str, Any] = {
            "edinet": edinet["coverage"],
            "credit_margin": credit["coverage"],
            "macro": macro["coverage"],
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
            f"macro-observations JSONL; model: {response.model}",
        ]
        tool_sources = sorted({trace.tool for trace in response.tool_trace})
        if tool_sources:
            notes.append("tools used: " + ",".join(tool_sources))
        return Provenance(
            provider=f"yowayowa-research-brief/{response.provider}",
            source="Morning brief assembled from EDINET daily list, credit margin "
            "screening candidates and macro observation JSONL",
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
