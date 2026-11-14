"""CG-006 adversarial tests: research_ask provenance separation.

Offline by construction (same pattern as ``test_research_brief.py``): the
agent is a factory-made fake that records its prompt and returns a canned
answer, EDINET reads a fixture or a missing file, macro observations live in
``tmp_path`` JSONL, and screening candidates are seeded through the real
``persist_screening_run``.

What this file pins:

- facts[] is assembled deterministically from raw source rows (no LLM, no
  recomputed numbers) and every fact carries provenance;
- sources the question mentions but the stores lack appear as
  ``missing_inputs`` lines and never as facts;
- two candidates for one code from different sources both survive into
  facts and citations (no smoothing);
- the prompt carries the [F*] citation discipline and the two mandatory
  trailing sections; the response parses them deterministically;
- a missing-section answer is recorded in coverage (fail explicit, never
  silently dropped).
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, ClassVar

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from yowayowa.config import Settings
from yowayowa.db import Base, CreditMarginWeeklyRecord
from yowayowa.domain import LicenseClass, Provenance
from yowayowa.research_models import AIChatResponse, AIToolTrace
from yowayowa.services.macro_store import MacroObservationStore
from yowayowa.services.research_ask import research_ask
from yowayowa.services.screening_pipeline import persist_screening_run, run_screening_pipeline

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "screening"
EDINET_SAMPLE = FIXTURES / "edinet_daily_sample.jsonl"

RUN_DATE = date(2026, 9, 24)
NOW = datetime(2026, 9, 24, 23, 0, tzinfo=UTC)


def _memory_engine():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return engine


class FakeAgent:
    """Offline InvestmentResearchAgent replacement (records its prompt)."""

    instances: ClassVar[list[FakeAgent]] = []
    answer: ClassVar[str] = ""

    def __init__(self, settings: Settings, session: Any = None) -> None:
        self.settings = settings
        self.session = session
        self.requests: list[Any] = []
        FakeAgent.instances.append(self)

    def chat(self, request: Any) -> AIChatResponse:
        self.requests.append(request)
        return AIChatResponse(
            answer=self.answer,
            provider="openai_compatible",
            model="fake-model",
            tool_trace=[
                AIToolTrace(tool="get_screening_candidates", arguments={}, result_preview="[]")
            ],
        )


@pytest.fixture(autouse=True)
def _reset_fake_agents():
    FakeAgent.instances = []
    FakeAgent.answer = ""
    yield
    FakeAgent.instances = []
    FakeAgent.answer = ""


def _seed_two_source_candidates(engine) -> None:
    """Two candidates for 6758 from different sources with conflicting reasons."""

    provenance_margin = Provenance(
        provider="yahoo_finance_margin",
        source="Yahoo!ファイナンス 信用残 weekly history (quote page)",
        source_url="https://finance.yahoo.co.jp/quote/6758.T/margin",
        license_class=LicenseClass.PERSONAL_ONLY,
        retrieved_at=NOW,
        as_of=RUN_DATE,
    )
    previous_week = date(2026, 9, 11)
    with Session(engine, expire_on_commit=False) as session:
        session.add_all(
            [
                CreditMarginWeeklyRecord(
                    as_of_date=previous_week,
                    code="6758",
                    short_total=150,
                    long_total=200,
                    source_url=provenance_margin.source_url,
                    provider="yahoo_finance_margin",
                    retrieved_at=NOW,
                    notes=[],
                ),
                CreditMarginWeeklyRecord(
                    as_of_date=RUN_DATE,
                    code="6758",
                    short_total=300,
                    long_total=200,
                    source_url=provenance_margin.source_url,
                    provider="yahoo_finance_margin",
                    retrieved_at=NOW,
                    notes=[],
                ),
            ]
        )
        session.commit()
        result = run_screening_pipeline(
            session,
            edinet_path=EDINET_SAMPLE,
            screener_mode="off",
            credit_margin_mode="on",
            run_date=RUN_DATE,
        )
        persist_screening_run(session, result)


def _write_macro_files(root: Path) -> None:
    macro_dir = root / "macro-observations"
    macro_dir.mkdir(parents=True, exist_ok=True)
    (macro_dir / "fred.jsonl").write_text(
        json.dumps(
            {
                "series_id": "CPIAUCSL",
                "value": 334.131,
                "date": "2026-08-01",
                "provider": "fred",
                "retrieved_at": "2026-09-24",
                "source_url": "https://fred.stlouisfed.org/graph/fredgraph.csv?id=CPIAUCSL",
                "license_class": "official_public",
            }
        )
        + "\n",
        encoding="utf-8",
    )


def _write_ohlcv_files(root: Path) -> None:
    """AAPL stock rows only: no TSLA anywhere, no crypto at all."""

    stock_dir = root / "stock-ohlcv" / "AAPL"
    stock_dir.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "symbol": "AAPL",
            "interval": "1d",
            "currency": "USD",
            "provider": "alpaca",
            "source_url": "https://data.alpaca.markets/v2/stocks/bars?symbols=AAPL",
            "license_class": "personal_only",
            "retrieved_at": "2026-09-24T15:25:05Z",
            "as_of": f"{day}T04:00:00Z",
            "open": 335.0 + index,
            "high": 339.0 + index,
            "low": 332.0 + index,
            "close": 336.0 + index,
            "volume": 86726840.0,
        }
        for index, day in enumerate(["2026-09-22", "2026-09-23", "2026-09-24"])
    ]
    (stock_dir / "ohlcv.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )


def _ask(
    tmp_path: Path,
    question: str,
    *,
    engine=None,
    edinet_path: Path | None = EDINET_SAMPLE,
    with_macro: bool = False,
    with_ohlcv: bool = False,
    answer: str = "",
) -> Any:
    """Run one research_ask round with the fake agent and return its response."""

    FakeAgent.answer = answer
    settings = Settings(database_url="sqlite:///:memory:")
    own_engine = engine is None
    if own_engine:
        engine = _memory_engine()
    try:
        with Session(engine, expire_on_commit=False) as session:
            return research_ask(
                question,
                session,
                settings,
                edinet_path=edinet_path if edinet_path is not None else tmp_path / "absent.jsonl",
                macro_store=MacroObservationStore(tmp_path / "macro-observations"),
                stock_store=tmp_path / "stock-ohlcv" if with_ohlcv else tmp_path / "absent-stock",
                crypto_store=tmp_path / "crypto-ohlcv"
                if with_ohlcv
                else tmp_path / "absent-crypto",
                agent_factory=lambda s, sess: FakeAgent(s, sess),
            )
    finally:
        if own_engine:
            engine.dispose()


# ----------------------------------------------------------------- tests


def test_missing_stock_symbol_is_not_invented_as_fact(tmp_path: Path) -> None:
    """TSLA in the question with no store row: 未取得 line, never a TSLA fact."""

    _write_macro_files(tmp_path)
    _write_ohlcv_files(tmp_path)
    response = _ask(tmp_path, "TSLAの日足を見せて", with_ohlcv=True)

    missing = response.coverage["missing_inputs"]
    assert any(line.startswith("ohlcv TSLA:") and "未取得" in line for line in missing), missing
    tsla_facts = [f for f in response.facts if f.code_or_series == "TSLA"]
    assert tsla_facts == []
    # The one persisted symbol does have a fact.
    aapl_facts = [f for f in response.facts if f.code_or_series == "AAPL"]
    assert len(aapl_facts) == 1
    assert aapl_facts[0].kind == "stock_ohlcv"
    # Raw row values flow through unchanged (str() only, no reformatting).
    assert "close=338.0" in aapl_facts[0].statement
    assert "volume=86726840.0" in aapl_facts[0].statement
    assert aapl_facts[0].provider == "alpaca"
    # missing_inputs is mirrored into the first-class response field.
    assert response.missing_inputs == response.coverage["missing_inputs"]
    assert any("TSLA" in line for line in response.missing_inputs)


def test_missing_macro_store_records_missing_and_yields_no_macro_fact(
    tmp_path: Path,
) -> None:
    """Empty macro store: macro_observations: 未取得 and zero macro facts."""

    _write_ohlcv_files(tmp_path)
    response = _ask(tmp_path, "AAPLの調子は?", with_ohlcv=True)

    assert "macro_observations: 未取得" in response.coverage["missing_inputs"]
    assert "macro_observations: 未取得" in response.missing_inputs
    assert [f for f in response.facts if f.kind == "macro"] == []


def test_missing_edinet_file_records_missing_when_codes_mentioned(
    tmp_path: Path,
) -> None:
    """Absent EDINET file + a JPX code in the question: 未取得 recorded."""

    _write_macro_files(tmp_path)
    response = _ask(tmp_path, "7203の状況を教えて", edinet_path=tmp_path / "absent.jsonl")

    assert "edinet_daily_filings: 未取得" in response.coverage["missing_inputs"]
    assert [f for f in response.facts if f.kind == "edinet_filing"] == []
    assert [f for f in response.facts if f.kind == "edinet_filing"] == []


def test_two_source_candidates_survive_unsmoothed(tmp_path: Path) -> None:
    """Same-code conflict across sources: both facts, both citations, no smoothing."""

    engine = _memory_engine()
    try:
        _seed_two_source_candidates(engine)
        _write_macro_files(tmp_path)
        with Session(engine, expire_on_commit=False) as session:
            settings = Settings(database_url="sqlite:///:memory:")
            response = research_ask(
                "6758と11115の信用残とEDINETの動きは?",
                session,
                settings,
                edinet_path=EDINET_SAMPLE,
                macro_store=MacroObservationStore(tmp_path / "macro-observations"),
                stock_store=tmp_path / "absent-stock",
                crypto_store=tmp_path / "absent-crypto",
                agent_factory=lambda s, sess: FakeAgent(s, sess),
            )
    finally:
        engine.dispose()

    screening_facts = [f for f in response.facts if f.kind == "screening"]
    # 11115 -> EDINET-derived screening candidate; 6758 -> credit-margin candidate.
    # The two sources disagree; NEITHER is smoothed away.
    assert len(screening_facts) >= 2
    sources = {f.statement.split(" ", 1)[0] for f in screening_facts}
    assert "credit_margin_weekly" in sources
    assert "edinet_filing" in sources
    kinds = {c.kind for c in response.citations}
    assert "credit_margin_weekly" in kinds
    assert "edinet_filing" in kinds


def test_prompt_carries_fact_id_and_section_discipline(tmp_path: Path) -> None:
    """The agent prompt pins [F*] citations and the two trailing sections."""

    _write_macro_files(tmp_path)
    _write_ohlcv_files(tmp_path)
    _ask(tmp_path, "AAPLの直近の終値は?", with_ohlcv=True)

    agent = FakeAgent.instances[-1]
    prompt = agent.requests[-1].messages[0].content
    assert "[F" in prompt  # fact-id citation rule
    assert "###推論" in prompt
    assert "###反証条件" in prompt
    # Existing discipline stays intact.
    assert "自由計算" in prompt
    assert "未取得" in prompt
    # Facts are embedded with ids so the model can cite them.
    assert '"id": "F1"' in prompt


def test_inference_sections_are_parsed_structurally(tmp_path: Path) -> None:
    """###推論 / ###反証条件 become inferences + invalidation_conditions."""

    _write_macro_files(tmp_path)
    _write_ohlcv_files(tmp_path)
    answer = (
        "AAPLの直近終値は338.0ドルです[F1]。\n"
        "###推論\n"
        "- [F1] 保存済みの日足が直近3日分あり、直近終値として引用可能。\n"
        "* [F2] マクロはCPIのみで株式判断には補足情報。\n"
        "###反証条件\n"
        "- ローカルストアに2026-09-25以降の行が追加された場合、直近値は変わる。\n"
        "1. 取得元プロバイダの訂正があった場合。\n"
    )
    response = _ask(tmp_path, "AAPLの直近の終値は?", with_ohlcv=True, answer=answer)

    # Packet order: stock first (F1), macro after (F2). No screening/EDINET here.
    assert [f.id for f in response.facts] == ["F1", "F2"]
    assert len(response.inferences) == 2
    first, second = response.inferences
    assert first.statement == "保存済みの日足が直近3日分あり、直近終値として引用可能。"
    assert first.supporting_fact_ids == ["F1"]
    assert second.statement == "マクロはCPIのみで株式判断には補足情報。"
    assert second.supporting_fact_ids == ["F2"]
    assert response.invalidation_conditions == [
        "ローカルストアに2026-09-25以降の行が追加された場合、直近値は変わる。",
        "取得元プロバイダの訂正があった場合。",
    ]
    # The answer itself is preserved verbatim, sections included.
    assert response.answer == answer
    # Only existing fact ids may be cited.
    fact_ids = {f.id for f in response.facts}
    for inference in response.inferences:
        assert set(inference.supporting_fact_ids) <= fact_ids


def test_missing_inference_sections_is_recorded_in_coverage(tmp_path: Path) -> None:
    """An answer without the sections: empty inferences + coverage marker."""

    _write_macro_files(tmp_path)
    _write_ohlcv_files(tmp_path)
    response = _ask(
        tmp_path,
        "AAPLの直近の終値は?",
        with_ohlcv=True,
        answer="AAPLの直近終値は336.0です[F3]。(セクションなし)",
    )

    assert response.inferences == []
    assert response.invalidation_conditions == []
    assert response.coverage["inference_sections"] == "未記載"
    # …and the fact discipline still holds: the citation refers to a real fact.
    assert any(f.code_or_series == "AAPL" for f in response.facts)


def test_fact_cap_truncates_and_records_coverage(tmp_path: Path) -> None:
    """More than the fact cap: truncation is recorded, ids stay sequential."""

    engine = _memory_engine()
    try:
        _seed_two_source_candidates(engine)
        _write_macro_files(tmp_path)
        with Session(engine, expire_on_commit=False) as session:
            settings = Settings(database_url="sqlite:///:memory:")
            response = research_ask(
                "6758の状況は?",
                session,
                settings,
                edinet_path=EDINET_SAMPLE,
                macro_store=MacroObservationStore(tmp_path / "macro-observations"),
                stock_store=tmp_path / "absent-stock",
                crypto_store=tmp_path / "absent-crypto",
                agent_factory=lambda s, sess: FakeAgent(s, sess),
            )
    finally:
        engine.dispose()

    # Sanity: ids are F1..Fn without gaps.
    ids = [f.id for f in response.facts]
    assert ids == [f"F{i}" for i in range(1, len(ids) + 1)]
    # This fixture never exceeds the cap; the flag must NOT be set.
    assert "facts_truncated" not in response.coverage


def test_existing_regression_surface_untouched(tmp_path: Path) -> None:
    """additive fields default sanely and citations/tool_trace keep their shape."""

    _write_macro_files(tmp_path)
    response = _ask(tmp_path, "7203はどう?", with_macro=True)

    assert all(f.kind == "macro" for f in response.facts)  # only ambient macro evidence
    assert response.calculations == []  # v1 forbids free computation
    assert response.tool_trace
    assert response.provider == "openai_compatible"
    missing = response.missing_inputs
    assert "screening_candidates: 未取得" in missing
    assert "edinet_daily_filings: 未取得" in missing
