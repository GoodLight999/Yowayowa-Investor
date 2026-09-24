"""P5-A LLM research brief / ask: services, models, API, CLI, AI tool.

All tests here are offline:

- The agent is replaced with a fake ``InvestmentResearchAgent`` factory (the
  real provider loops are exercised in ``test_ai_agent.py``).
- EDINET reads the P4-D fixtures; macro observations are written to
  ``tmp_path`` JSONL files; screening candidates are seeded into an
  in-memory SQLite database via the real ``persist_screening_run``.
- Telegram delivery is tested with an injected sender and a monkeypatched
  ``subprocess.run`` — the real ``hermes`` CLI is never invoked.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, ClassVar

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from starlette.testclient import TestClient
from typer.testing import CliRunner

from yowayowa.api.app import app
from yowayowa.cli_entry import app as cli_app
from yowayowa.config import Settings
from yowayowa.db import Base, ResearchBriefRecord
from yowayowa.domain import LicenseClass, Provenance
from yowayowa.research_brief_models import ResearchBrief
from yowayowa.research_models import (
    AIChatResponse,
    AIToolTrace,
)
from yowayowa.services.macro_store import MacroObservationStore, default_macro_store
from yowayowa.services.research_ask import research_ask
from yowayowa.services.research_brief import (
    BRIEF_SECTIONS,
    MorningBriefService,
    persist_research_brief,
    read_latest_research_brief,
)
from yowayowa.services.screening_pipeline import (
    persist_screening_run,
    run_screening_pipeline,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "screening"
EDINET_SAMPLE = FIXTURES / "edinet_daily_sample.jsonl"
EDINET_EMPTY = FIXTURES / "edinet_daily_empty.jsonl"

RUN_DATE = date(2026, 9, 24)
NOW = datetime(2026, 9, 24, 23, 0, tzinfo=UTC)

runner = CliRunner()


def _memory_engine():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return engine


class FakeAgent:
    """Offline InvestmentResearchAgent replacement (records its prompt)."""

    instances: ClassVar[list[FakeAgent]] = []

    def __init__(self, settings: Settings, session: Any = None) -> None:
        self.settings = settings
        self.session = session
        self.requests: list[Any] = []
        self.answer = (
            "1. 本日の注力ポイント\n"
            "EDINETで訂正有価証券報告書（docID S100TEST1、retrieved_at "
            "2026-09-24T11:49:11+00:00）。信用残は6758の売残急増（前週比+100%、"
            "retrieved_at 2026-09-24T12:00:00+00:00）。\n"
            "4. マクロ更新\n"
            "CPIAUCSL 334.131（2026-08-01、本日発表ではありません）。\n"
            "5. 次の調査アクション\n調査起点として確認。"
        )
        self.provider = "openai_compatible"
        self.model = "fake-model"
        FakeAgent.instances.append(self)

    def chat(self, request: Any) -> AIChatResponse:
        self.requests.append(request)
        return AIChatResponse(
            answer=self.answer,
            provider=self.provider,
            model=self.model,
            tool_trace=[
                AIToolTrace(tool="get_screening_candidates", arguments={}, result_preview="[]")
            ],
        )


@pytest.fixture(autouse=True)
def _reset_fake_agents():
    FakeAgent.instances = []
    yield
    FakeAgent.instances = []


def _seed_screening(engine) -> None:
    """Seed the persisted screening table with EDINET + credit-margin rows."""

    from datetime import date as date_cls

    from yowayowa.credit_margin_models import normalize_credit_margin_code  # noqa: F401
    from yowayowa.db import CreditMarginWeeklyRecord
    from yowayowa.services.credit_margin import read_credit_margin_by_code  # noqa: F401

    provenance = Provenance(
        provider="yahoo_finance_margin",
        source="Yahoo!ファイナンス 信用残 weekly history (quote page)",
        source_url="https://finance.yahoo.co.jp/quote/6758.T/margin",
        license_class=LicenseClass.PERSONAL_ONLY,
        retrieved_at=NOW,
        as_of=RUN_DATE,
    )
    previous_week = date_cls(2026, 9, 11)
    with Session(engine, expire_on_commit=False) as session:
        # Two weeks for 6758: short 150 -> 300 (+100%) triggers CREDIT_SHORT_SURGE.
        session.add_all(
            [
                CreditMarginWeeklyRecord(
                    as_of_date=previous_week,
                    code="6758",
                    short_total=150,
                    long_total=200,
                    source_url=provenance.source_url,
                    provider="yahoo_finance_margin",
                    retrieved_at=NOW,
                    notes=[],
                ),
                CreditMarginWeeklyRecord(
                    as_of_date=RUN_DATE,
                    code="6758",
                    short_total=300,
                    long_total=200,
                    source_url=provenance.source_url,
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
    (macro_dir / "bls.jsonl").write_text(
        json.dumps(
            {
                "series_id": "LNS14000000",
                "title": "unemployment rate (SA, %)",
                "value": 4.4,
                "year": 2025,
                "period": "M12",
                "period_name": "December",
                "provider": "bls-v1",
                "retrieved_at": "2026-09-24T09:41:40+00:00",
                "source_url": "https://api.bls.gov/publicAPI/v1/timeseries/data/",
                "license_class": "official_public",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (macro_dir / "fred.jsonl").write_text(
        json.dumps(
            {
                "series_id": "CPIAUCSL,UNRATE",
                "value_row": {
                    "observation_date": "2026-08-01",
                    "CPIAUCSL": "334.131",
                    "UNRATE": "4.1",
                },
                "date": "2026-08-01",
                "provider": "fred",
                "retrieved_at": "2026-09-24",
                "source_url": "https://fred.stlouisfed.org/graph/fredgraph.csv?id=CPIAUCSL,UNRATE",
                "license_class": "official_public",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (macro_dir / "treasury.jsonl").write_text(
        json.dumps(
            {
                "series_id": "avg_interest_rates",
                "record_date": str(RUN_DATE),
                "value": 3.145,
                "provider": "treasury-fiscaldata",
                "retrieved_at": "2026-09-24T09:41:40+00:00",
                "source_url": "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v2/accounting/od/avg_interest_rates",
                "license_class": "official_public",
            }
        )
        + "\n",
        encoding="utf-8",
    )


def _service(
    engine,
    tmp_path: Path,
    *,
    edinet_path: Path = EDINET_SAMPLE,
    agent_factory: Any = FakeAgent,
) -> MorningBriefService:
    settings = Settings(database_url="sqlite:///:memory:")
    with Session(engine, expire_on_commit=False) as session:
        return MorningBriefService(
            settings,
            session,
            edinet_path=edinet_path,
            macro_store=default_macro_store(tmp_path),
            agent_factory=agent_factory,
        )


# ----------------------------------------------------------------- macro store


def test_macro_store_splits_combined_series_and_keeps_provenance(tmp_path: Path) -> None:
    _write_macro_files(tmp_path)
    store = default_macro_store(tmp_path)
    latest = store.latest_by_series()
    by_key = {(row["source_kind"], row["series_id"]): row for row in latest}
    assert ("fred", "CPIAUCSL") in by_key
    assert ("fred", "UNRATE") in by_key
    assert by_key[("fred", "CPIAUCSL")]["value"] == pytest.approx(334.131)
    assert by_key[("fred", "CPIAUCSL")]["as_of"] == "2026-08-01"
    assert by_key[("fred", "CPIAUCSL")]["retrieved_at"] == "2026-09-24"
    assert by_key[("bls", "LNS14000000")]["as_of"] == "2025-12-01"
    assert by_key[("treasury", "avg_interest_rates")]["as_of"] == RUN_DATE.isoformat()


def test_macro_store_missing_file_records_coverage_and_yields_nothing(tmp_path: Path) -> None:
    store = MacroObservationStore(tmp_path / "macro-observations")
    rows, coverage = store.read("bls")
    assert rows == []
    assert coverage["reason"] == "file_missing"
    assert store.latest_by_series() == []


def test_macro_store_skips_unparseable_values_without_zero_fill(tmp_path: Path) -> None:
    macro_dir = tmp_path / "macro-observations"
    macro_dir.mkdir(parents=True)
    (macro_dir / "bls.jsonl").write_text(
        json.dumps(
            {
                "series_id": "X1",
                "value": "not-a-number",
                "year": 2026,
                "period": "M01",
                "provider": "bls-v1",
                "retrieved_at": "2026-09-24T00:00:00+00:00",
            }
        )
        + "\n"
        + json.dumps(
            {
                "series_id": "X1",
                "value": 42.0,
                "year": 2026,
                "period": "M02",
                "provider": "bls-v1",
                "retrieved_at": "2026-09-24T00:00:00+00:00",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    rows, coverage = MacroObservationStore(macro_dir).read("bls")
    assert coverage["skipped_unparseable"] == 0  # row parsed; only the value is skipped
    assert [row["value"] for row in rows] == [pytest.approx(42.0)]


# ------------------------------------------------------------- EDINET summary


def test_assemble_edinet_summary_classifies_and_flags_large_filings(tmp_path: Path) -> None:
    engine = _memory_engine()
    try:
        service = _service(engine, tmp_path)
        summary = service.assemble_edinet_summary(RUN_DATE)
    finally:
        engine.dispose()
    assert summary["coverage"]["row_count"] == 12
    signals = {entry["doc_id"]: entry["signal"] for entry in summary["filings"]}
    assert signals["S100TEST1"] == "filing_forecast_revision"
    assert signals["S100TEST4"] == "filing_cancellation"
    # docTypeCode 030 (有価証券届出書) -> 大口提出 flag (A-3 input)
    large = {entry["doc_id"] for entry in summary["large_filings"]}
    assert "S100Z3EZ" in large
    assert all(entry["note"] == "大口提出（有価証券届出書）" for entry in summary["large_filings"])


def test_assemble_edinet_summary_missing_file_records_coverage(tmp_path: Path) -> None:
    engine = _memory_engine()
    try:
        service = _service(engine, tmp_path, edinet_path=tmp_path / "absent.jsonl")
        summary = service.assemble_edinet_summary(RUN_DATE)
    finally:
        engine.dispose()
    assert summary["filings"] == []
    assert summary["large_filings"] == []
    assert summary["coverage"]["reason"] == "file_missing"


# --------------------------------------------------------------------- macro


def test_assemble_macro_summary_flags_announced_today(tmp_path: Path) -> None:
    _write_macro_files(tmp_path)
    engine = _memory_engine()
    try:
        service = _service(engine, tmp_path)
        macro = service.assemble_macro_summary(RUN_DATE)
    finally:
        engine.dispose()
    by_series = {row["series_id"]: row for row in macro["series"]}
    assert by_series["avg_interest_rates"]["announced_today"] is True  # as_of == run date
    assert by_series["CPIAUCSL"]["announced_today"] is False
    assert macro["coverage"]["announced_today_count"] == 1


# -------------------------------------------------------------------- compose


def test_compose_brief_uses_strict_prompt_and_records_coverage(tmp_path: Path) -> None:
    _write_macro_files(tmp_path)
    engine = _memory_engine()
    try:
        _seed_screening(engine)
        service = _service(engine, tmp_path)
        brief = service.compose_brief(run_date=RUN_DATE, detected_at=NOW)
    finally:
        engine.dispose()

    assert isinstance(brief, ResearchBrief)
    assert brief.run_date == RUN_DATE
    assert brief.sections == list(BRIEF_SECTIONS)
    assert len(brief.sections) == 5
    assert brief.provider == "openai_compatible"
    assert brief.model == "fake-model"

    agent = FakeAgent.instances[-1]
    prompt = agent.requests[-1].messages[0].content
    # Strict prompt discipline: five sections, citation requirement,
    # fabrication ban, 未取得 rule.
    assert "5セクション" in prompt
    assert "捏造禁止" in prompt
    assert "未取得" in prompt
    assert "EVIDENCE JSON" in prompt
    assert "S100TEST1" in prompt  # evidence actually embedded
    assert "credit_short_surge" in prompt
    assert "CPIAUCSL" in prompt

    # Coverage records every family; EDINET+credit+macro all present here.
    coverage = brief.coverage
    assert coverage["edinet"]["row_count"] == 12
    assert coverage["credit_margin"]["credit_candidates"] >= 1
    assert coverage["macro"]["series_count"] >= 3
    assert coverage["missing_inputs"] == []

    # Citations carry provenance for every family.
    kinds = {citation.kind for citation in brief.citations}
    assert {"edinet_filing", "credit_margin", "macro"} <= kinds
    macro_citation = next(c for c in brief.citations if c.kind == "macro")
    assert macro_citation.source_url
    assert macro_citation.retrieved_at

    # Provenance: credit candidates are personal-only -> brief is personal-only.
    assert brief.provenance.license_class == LicenseClass.PERSONAL_ONLY
    assert brief.provenance.provider.startswith("yowayowa-research-brief/")
    assert brief.generated_at == NOW


def test_compose_brief_missing_inputs_rendered_as_coverage(tmp_path: Path) -> None:
    # No macro files, no screening rows, empty EDINET fixture: everything missing.
    engine = _memory_engine()
    try:
        service = _service(engine, tmp_path, edinet_path=EDINET_EMPTY)
        brief = service.compose_brief(run_date=RUN_DATE, detected_at=NOW)
    finally:
        engine.dispose()
    missing = "\n".join(brief.coverage["missing_inputs"])
    assert "edinet" in missing or "edinet_signal" in missing
    assert "credit_margin" in missing
    assert "macro" in missing
    assert brief.coverage["macro"]["reason"] == "no_macro_observation_files"
    assert brief.coverage["edinet"]["row_count"] == 0


# --------------------------------------------------------------- persistence


def test_persist_research_brief_is_idempotent_per_run_date(tmp_path: Path) -> None:
    engine = _memory_engine()
    try:
        service = _service(engine, tmp_path)
        brief = service.compose_brief(run_date=RUN_DATE, detected_at=NOW)
        with Session(engine, expire_on_commit=False) as session:
            first = persist_research_brief(session, brief)
            assert first == {"inserted": 1, "updated": 0}
            second = persist_research_brief(session, brief)
            assert second == {"inserted": 1, "updated": 1}
        with Session(engine, expire_on_commit=False) as fresh:
            rows = list(fresh.query(ResearchBriefRecord).all())
            assert len(rows) == 1
            restored = read_latest_research_brief(fresh)
            assert restored is not None
            assert restored.run_date == RUN_DATE
            assert restored.answer == brief.answer
            assert restored.provenance.provider == brief.provenance.provider
            assert restored.citations == brief.citations
    finally:
        engine.dispose()


def test_read_latest_research_brief_empty_db_returns_none(tmp_path: Path) -> None:
    engine = _memory_engine()
    try:
        with Session(engine, expire_on_commit=False) as session:
            assert read_latest_research_brief(session) is None
    finally:
        engine.dispose()


# ---------------------------------------------------------------------- send


def test_send_brief_injected_sender_receives_message(tmp_path: Path) -> None:
    engine = _memory_engine()
    try:
        service = _service(engine, tmp_path)
        brief = service.compose_brief(run_date=RUN_DATE, detected_at=NOW)
    finally:
        engine.dispose()
    sent: list[str] = []
    service.send_brief(brief, sender=sent.append)
    assert len(sent) == 1
    assert brief.answer in sent[0]
    assert brief.run_date.isoformat() in sent[0]


def test_cli_default_sender_runs_hermes_send(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The --send path's default sender calls ``hermes send`` (list argv, no shell)."""

    from yowayowa.services import research_brief as research_brief_module

    captured: dict[str, Any] = {}

    def _fake_run(argv: list[str], **kwargs: Any) -> Any:
        captured["argv"] = list(argv)
        captured["kwargs"] = kwargs

        class _Result:
            returncode = 0

        return _Result()

    monkeypatch.setattr(research_brief_module.subprocess, "run", _fake_run)
    sender = research_brief_module._default_sender
    sender("テストブリーフ本文")
    assert captured["argv"] == [
        "hermes",
        "send",
        "--to",
        "telegram",
        "テストブリーフ本文",
    ]
    # shell=True must never be used (arg quoting safety).
    assert captured["kwargs"].get("shell") in (None, False)
    assert captured["kwargs"]["check"] is True


# ---------------------------------------------------------------- research_ask


def test_research_ask_collects_deterministic_evidence(tmp_path: Path) -> None:
    _write_macro_files(tmp_path)
    engine = _memory_engine()
    try:
        _seed_screening(engine)
        settings = Settings(database_url="sqlite:///:memory:")

        def factory(s: Settings, sess: Any) -> FakeAgent:
            return FakeAgent(s, sess)

        with Session(engine, expire_on_commit=False) as session:
            response = research_ask(
                "6758と11115の信用残とEDINETの動きを教えて",
                session,
                settings,
                edinet_path=EDINET_SAMPLE,
                macro_store=default_macro_store(tmp_path),
                agent_factory=factory,
            )
    finally:
        engine.dispose()

    assert response.provider == "openai_compatible"
    assert "6758" in response.coverage["mentioned_codes"]
    assert response.coverage["screening_candidates_matched"] >= 1
    # 11115 appears in the EDINET fixture (S100TEST1 訂正有価証券報告書).
    assert response.coverage["edinet_filings_matched"] >= 1
    assert response.coverage["macro_series"] >= 3

    # Deterministic tool trace runs before the agent call.
    tools = [entry["tool"] for entry in response.tool_trace[:4]]
    assert tools[0] == "read_screening_candidates"
    assert "edinet_daily_lookup" in tools
    assert "macro_latest_by_series" in tools
    by_tool = {entry["tool"]: entry for entry in response.tool_trace}
    assert by_tool["edinet_daily_lookup"]["matched"] >= 1
    assert by_tool["macro_latest_by_series"]["matched"] >= 3

    # Evidence packet the agent saw mentions the code's rows.
    agent = FakeAgent.instances[-1]
    prompt = agent.requests[-1].messages[0].content
    assert "6758" in prompt
    assert "自由計算" in prompt  # arithmetic ban discipline
    assert "未取得" in prompt

    # Citations include the matched EDINET row and the screening candidate.
    kinds = {citation.kind for citation in response.citations}
    assert "edinet_filing" in kinds
    assert "edinet_filing" in kinds and "macro" in kinds


def test_research_ask_missing_evidence_records_coverage(tmp_path: Path) -> None:
    engine = _memory_engine()
    try:
        settings = Settings(database_url="sqlite:///:memory:")
        with Session(engine, expire_on_commit=False) as session:
            response = research_ask(
                "7203について教えて",
                session,
                settings,
                edinet_path=EDINET_EMPTY,
                macro_store=default_macro_store(tmp_path),
                agent_factory=lambda s, sess: FakeAgent(s, sess),
            )
    finally:
        engine.dispose()
    assert response.coverage["screening_candidates_matched"] == 0
    assert "screening_candidates: 未取得" in response.coverage["missing_inputs"]
    assert "edinet_daily_filings: 未取得" in response.coverage["missing_inputs"]
    assert "macro_observations: 未取得" in response.coverage["missing_inputs"]


# ------------------------------------------------------------------------- API


def _api_db_engine(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from yowayowa import db as db_module

    monkeypatch.setenv("YOWAYOWA_DATABASE_URL", f"sqlite:///{tmp_path / 'research-api.db'}")
    db_module.dispose_database()


def _patch_fake_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    import yowayowa.api.research_llm_routes as routes

    monkeypatch.setattr(routes, "MorningBriefService", _api_service_factory)
    monkeypatch.setattr(routes, "research_ask", _api_research_ask_factory)


def _api_service_factory(settings: Any, session: Any, **kwargs: Any) -> Any:
    kwargs.setdefault("agent_factory", lambda s, sess: FakeAgent(s, sess))
    service = MorningBriefService(settings, session, **kwargs)

    original_compose = service.compose_brief

    def compose(run_date: Any = None, **kw: Any) -> Any:
        kw.setdefault("detected_at", NOW)
        return original_compose(run_date=run_date, **kw)

    service.compose_brief = compose  # type: ignore[method-assign]
    return service


def _api_research_ask_factory(*args: Any, **kwargs: Any) -> Any:
    # Inject the fake agent factory for the API path too.
    kwargs.setdefault("agent_factory", lambda s, sess: FakeAgent(s, sess))
    return research_ask(*args, **kwargs)


def test_api_ask_and_brief_roundtrip(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _api_db_engine(monkeypatch, tmp_path)
    _write_macro_files(tmp_path / "api-data")
    _patch_fake_agent(monkeypatch)
    # Point the default EDINET path and macro store at fixture/tmp data.
    monkeypatch.setattr("yowayowa.services.research_brief._DEFAULT_EDINET_PATH", EDINET_SAMPLE)
    from yowayowa.services import macro_store as macro_store_module

    monkeypatch.setattr(
        macro_store_module,
        "default_macro_store",
        lambda data_dir="./data": MacroObservationStore(
            tmp_path / "api-data" / "macro-observations"
        ),
    )
    with TestClient(app) as client:
        ask = client.post("/v1/research/ask", json={"question": "6758の状況は"})
        assert ask.status_code == 200, ask.text
        ask_payload = ask.json()
        assert ask_payload["answer"]
        assert ask_payload["provider"] == "openai_compatible"
        assert any(entry["tool"] == "edinet_daily_lookup" for entry in ask_payload["tool_trace"])

        brief_response = client.post("/v1/research/brief", json={"run_date": RUN_DATE.isoformat()})
        assert brief_response.status_code == 200, brief_response.text
        brief_payload = brief_response.json()
        assert brief_payload["persisted"] == {"inserted": 1, "updated": 0}
        assert len(brief_payload["brief"]["sections"]) == 5
        assert brief_payload["brief"]["run_date"] == RUN_DATE.isoformat()

        latest = client.get("/v1/research/brief")
        assert latest.status_code == 200
        assert latest.json()["run_date"] == RUN_DATE.isoformat()

        by_date = client.get("/v1/research/brief", params={"run_date": "2020-01-01"})
        assert by_date.status_code == 404


def test_api_research_fails_closed_outside_personal_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _api_db_engine(monkeypatch, tmp_path)
    monkeypatch.setenv("YOWAYOWA_MODE", "public")
    monkeypatch.setenv("YOWAYOWA_API_TOKEN", "public-token")
    from yowayowa.config import get_settings

    get_settings.cache_clear()
    try:
        with TestClient(app) as client:
            ask = client.post(
                "/v1/research/ask",
                json={"question": "test"},
                headers={"Authorization": "Bearer public-token"},
            )
            assert ask.status_code == 403
            assert "personal" in ask.json()["detail"]
            brief = client.post(
                "/v1/research/brief",
                json={},
                headers={"Authorization": "Bearer public-token"},
            )
            assert brief.status_code == 403
            latest = client.get(
                "/v1/research/brief", headers={"Authorization": "Bearer public-token"}
            )
            assert latest.status_code == 403
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


def test_openapi_contains_research_llm_paths() -> None:
    schema = app.openapi()
    assert {"/v1/research/ask", "/v1/research/brief"} <= set(schema["paths"])


# ------------------------------------------------------------------------- CLI


def test_cli_research_ask_prints_answer_and_trace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from yowayowa import db as db_module
    from yowayowa.services import ai_agent as ai_agent_module
    from yowayowa.services import macro_store as macro_store_module

    monkeypatch.setenv("YOWAYOWA_DATABASE_URL", f"sqlite:///{tmp_path / 'cli-research.db'}")
    db_module.dispose_database()
    monkeypatch.setattr("yowayowa.services.research_brief._DEFAULT_EDINET_PATH", EDINET_SAMPLE)
    monkeypatch.setattr(
        macro_store_module,
        "default_macro_store",
        lambda data_dir="./data": MacroObservationStore(tmp_path / "cli-macro"),
    )
    monkeypatch.setattr(ai_agent_module, "InvestmentResearchAgent", FakeAgent)
    monkeypatch.setattr(
        "yowayowa.services.research_ask.InvestmentResearchAgent",
        FakeAgent,
    )
    result = runner.invoke(cli_app, ["research-ask", "6758の信用残は"])
    assert result.exit_code == 0, result.output
    assert "Q:" in result.output
    assert "tool_trace" in result.output
    assert FakeAgent.instances, "fake agent must have been called"
    db_module.dispose_database()


def test_cli_research_brief_generates_and_persists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from yowayowa import db as db_module
    from yowayowa.services import ai_agent as ai_agent_module
    from yowayowa.services import macro_store as macro_store_module

    monkeypatch.setenv("YOWAYOWA_DATABASE_URL", f"sqlite:///{tmp_path / 'cli-brief.db'}")
    db_module.dispose_database()
    monkeypatch.setattr("yowayowa.services.research_brief._DEFAULT_EDINET_PATH", EDINET_SAMPLE)
    monkeypatch.setattr(
        macro_store_module,
        "default_macro_store",
        lambda data_dir="./data": MacroObservationStore(tmp_path / "cli-brief-macro"),
    )
    monkeypatch.setattr(ai_agent_module, "InvestmentResearchAgent", FakeAgent)
    monkeypatch.setattr(
        "yowayowa.services.research_ask.InvestmentResearchAgent",
        FakeAgent,
    )
    monkeypatch.setattr(
        "yowayowa.services.research_brief.InvestmentResearchAgent",
        FakeAgent,
    )
    result = runner.invoke(cli_app, ["research-brief"])
    assert result.exit_code == 0, result.output
    assert "research brief" in result.output
    assert FakeAgent.instances, "fake agent must have been called"
    db_module.dispose_database()
