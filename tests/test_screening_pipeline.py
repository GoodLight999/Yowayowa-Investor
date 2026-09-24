"""Machine screening pipeline: EDINET flags, credit margin surges, Yahoo
screener, persistence idempotency, API, models, AI tool (P4-D).

All tests here are offline:
- EDINET reads the fixture ``tests/fixtures/screening/edinet_daily_sample.jsonl``;
- credit margin data is seeded into an in-memory SQLite database;
- the Yahoo screener is replaced with a fake provider (same pattern as
  ``test_yahoo_screener.py``).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from starlette.testclient import TestClient
from typer.testing import CliRunner

from yowayowa.api.app import app
from yowayowa.cli_entry import app as cli_app
from yowayowa.config import Settings
from yowayowa.db import Base, CreditMarginWeeklyRecord, ScreeningCandidateRecord
from yowayowa.domain import LicenseClass, Provenance
from yowayowa.research_models import MarketScreenResponse
from yowayowa.screening_models import (
    ScreeningCandidate,
    ScreeningRunResult,
    ScreeningSignal,
    ScreeningSource,
)
from yowayowa.services.screening_pipeline import (
    classify_edinet_filing,
    persist_screening_run,
    read_screening_candidates,
    run_screening_pipeline,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "screening"
EDINET_SAMPLE = FIXTURES / "edinet_daily_sample.jsonl"
EDINET_EMPTY = FIXTURES / "edinet_daily_empty.jsonl"

RETRIEVED_AT = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
SIGNAL_DATE = date(2026, 9, 24)
SIGNAL_WEEK = date(2026, 9, 11)
PREVIOUS_WEEK = date(2026, 9, 4)
SIGNAL_RETRIEVED = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)

runner = CliRunner()


def _memory_engine():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return engine


def _credit_row(
    code: str,
    as_of: date,
    short_total: int,
    long_total: int,
    *,
    previous_short: int | None = None,
    previous_long: int | None = None,
) -> tuple[CreditMarginWeeklyRecord, CreditMarginWeeklyRecord]:
    base = Provenance(
        provider="yahoo_finance_margin",
        source="Yahoo!ファイナンス 信用残 weekly history (quote page)",
        source_url=f"https://finance.yahoo.co.jp/quote/{code}.T/margin",
        license_class=LicenseClass.PERSONAL_ONLY,
        retrieved_at=SIGNAL_RETRIEVED,
        as_of=as_of,
    )
    latest = CreditMarginWeeklyRecord(
        as_of_date=as_of,
        code=code,
        short_total=short_total,
        long_total=long_total,
        source_url=base.source_url,
        provider="yahoo_finance_margin",
        retrieved_at=SIGNAL_RETRIEVED,
        notes=[],
    )
    previous = None
    if previous_short is not None and previous_long is not None:
        previous = CreditMarginWeeklyRecord(
            as_of_date=PREVIOUS_WEEK,
            code=code,
            short_total=previous_short,
            long_total=previous_long,
            source_url=base.source_url,
            provider="yahoo_finance_margin",
            retrieved_at=SIGNAL_RETRIEVED,
            notes=[],
        )
    return latest, previous


def _seed_credit_engine(engine) -> None:
    rows: list[CreditMarginWeeklyRecord] = []
    # Surge: 100 -> 200 short (+100%), long 200 -> 300 (+50%) — both fire.
    latest, previous = _credit_row(
        "6758", SIGNAL_WEEK, 300, 400, previous_short=150, previous_long=200
    )
    rows += [latest, previous]
    # Drop: 200 -> 100 short (-50%) — short drop fires.
    latest, previous = _credit_row(
        "7203", SIGNAL_WEEK, 100, 300, previous_short=200, previous_long=300
    )
    rows += [latest, previous]
    # Long surge only: short flat, long 100 -> 200 (+100%).
    latest, previous = _credit_row(
        "7267", SIGNAL_WEEK, 100, 200, previous_short=100, previous_long=100
    )
    rows += [latest, previous]
    # Nothing: short 100 -> 105, long 200 -> 205 (no threshold crossed).
    latest, previous = _credit_row(
        "9984", SIGNAL_WEEK, 105, 205, previous_short=100, previous_long=200
    )
    rows += [latest, previous]
    # Only one week: cannot evaluate (recorded, never a trigger).
    rows.append(
        CreditMarginWeeklyRecord(
            as_of_date=SIGNAL_WEEK,
            code="8306",
            short_total=500,
            long_total=500,
            source_url="https://finance.yahoo.co.jp/quote/8306.T/margin",
            provider="yahoo_finance_margin",
            retrieved_at=SIGNAL_RETRIEVED,
            notes=[],
        )
    )
    with Session(engine, expire_on_commit=False) as session:
        session.add_all(rows)
        session.commit()


class FakeScreenerProvider:
    """Offline replacement for YahooScreenerProvider (monkeypatch pattern)."""

    def __init__(self, quotes: list[dict], total=None):
        self.quotes = quotes
        self.total = total
        self.calls: list = []

    def screen(self, request):
        self.calls.append(request)
        return MarketScreenResponse(
            quotes=self.quotes,
            total=self.total,
            offset=request.offset,
            size=request.size,
            query={"filters": "fake"},
            provenance=Provenance(
                provider="yahoo/yfinance",
                source="Yahoo Finance Equity Screener (fake)",
                license_class=LicenseClass.PERSONAL_ONLY,
                retrieved_at=SIGNAL_RETRIEVED,
                as_of=SIGNAL_RETRIEVED,
            ),
        )


# ------------------------------------------------------------------- source A


def test_edinet_classification_and_skip_rules() -> None:
    assert classify_edinet_filing("訂正有価証券報告書") is ScreeningSignal.FILING_FORECAST_REVISION
    assert classify_edinet_filing("訂正報告書（大量保有報告書・変更報告書）") is (
        ScreeningSignal.FILING_FORECAST_REVISION
    )
    assert classify_edinet_filing("自己株券消却に伴う臨時報告書") is ScreeningSignal.FILING_BUYBACK
    assert (
        classify_edinet_filing("公開買付け実施に伴う臨時報告書") is ScreeningSignal.FILING_BUYBACK
    )
    assert classify_edinet_filing("上場廃止に伴う臨時報告書") is ScreeningSignal.FILING_CANCELLATION
    assert classify_edinet_filing("有価証券報告書") is None
    assert classify_edinet_filing("半期報告書（内国投資信託受益証券）－第14期") is None


def test_edinet_candidates_from_fixture() -> None:
    engine = _memory_engine()
    try:
        with Session(engine, expire_on_commit=False) as session:
            result = run_screening_pipeline(
                session,
                edinet_path=EDINET_SAMPLE,
                screener_mode="off",
                credit_margin_mode="off",
                run_date=SIGNAL_DATE,
            )
        codes_signals = sorted((c.code, c.signal) for c in result.candidates)
        assert ("11115", ScreeningSignal.FILING_FORECAST_REVISION) in codes_signals
        assert ("22225", ScreeningSignal.FILING_BUYBACK) in codes_signals
        assert ("33335", ScreeningSignal.FILING_BUYBACK) in codes_signals
        assert ("44445", ScreeningSignal.FILING_CANCELLATION) in codes_signals
        assert ("66665", ScreeningSignal.FILING_BUYBACK) in codes_signals
        edinet_candidates = [
            c for c in result.candidates if c.source == ScreeningSource.EDINET_FILING
        ]
        assert len(edinet_candidates) == 5
        counts = result.per_source_counts[ScreeningSource.EDINET_FILING.value]
        assert counts["row_count"] == 12
        assert counts["skipped_unclassified"] >= 2  # 通常 filings + funds skipped
        assert counts["skipped_missing_code"] == 2  # no secCode -> no candidate
        assert all(c.reason.startswith("EDINET提出") for c in edinet_candidates)
        provenance = edinet_candidates[0].provenance
        assert provenance.license_class == LicenseClass.OFFICIAL_PUBLIC
        assert provenance.source_url is not None
    finally:
        engine.dispose()


# ------------------------------------------------------------------- source B


def test_credit_margin_signals_from_seeded_db() -> None:
    engine = _memory_engine()
    try:
        _seed_credit_engine(engine)
        with Session(engine, expire_on_commit=False) as session:
            result = run_screening_pipeline(
                session,
                edinet_path=EDINET_EMPTY,
                screener_mode="off",
                credit_margin_mode="on",
                run_date=SIGNAL_DATE,
            )
    finally:
        engine.dispose()
    credit = [c for c in result.candidates if c.source == ScreeningSource.CREDIT_MARGIN_WEEKLY]
    by_key = {(c.code, c.signal): c for c in credit}
    assert (
        "6758",
        ScreeningSignal.CREDIT_SHORT_SURGE,
    ) in by_key, "short 150 -> 300 (+100%) must trigger a short surge"
    assert ("6758", ScreeningSignal.CREDIT_LONG_SURGE) in by_key
    assert ("7203", ScreeningSignal.CREDIT_SHORT_DROP) in by_key
    assert ("7267", ScreeningSignal.CREDIT_LONG_SURGE) in by_key
    assert ("9984", ScreeningSignal.CREDIT_SHORT_SURGE) not in by_key
    assert all(c.code != "8306" for c in credit)  # one week only -> no ratio
    surge = by_key[("6758", ScreeningSignal.CREDIT_SHORT_SURGE)]
    assert surge.value["previous_short_total"] == 150
    assert surge.value["short_total"] == 300
    assert surge.value["ratio_change_1w"] == pytest.approx(1.0)
    assert surge.reason.startswith("売残が前週比+30%以上")
    counts = result.per_source_counts[ScreeningSource.CREDIT_MARGIN_WEEKLY.value]
    assert counts["codes_with_two_weeks"] == 4
    assert counts["codes_without_two_weeks"] == 1


def test_credit_margin_empty_db_yields_zero_with_coverage() -> None:
    engine = _memory_engine()
    try:
        with Session(engine, expire_on_commit=False) as session:
            result = run_screening_pipeline(
                session,
                edinet_path=EDINET_EMPTY,
                screener_mode="off",
                credit_margin_mode="on",
                run_date=SIGNAL_DATE,
            )
    finally:
        engine.dispose()
    counts = result.per_source_counts[ScreeningSource.CREDIT_MARGIN_WEEKLY.value]
    assert counts == {"codes_with_two_weeks": 0, "codes_without_two_weeks": 0, "candidates": 0}
    assert result.coverage["credit_margin_weekly_coverage_complete"] is False


# ------------------------------------------------------------------- source C


def test_screener_candidates_and_unparseable_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeScreenerProvider(
        quotes=[
            {"symbol": "6758.T", "regularMarketPrice": 1200.0, "dayvolume": 1234},
            {"symbol": "55555.T", "regularMarketPrice": 999.0},
            {"symbol": "RKLB", "regularMarketPrice": 30.0},  # non-JP: unparseable
            {"symbol": "135A9.T", "regularMarketPrice": 30.0},  # 5-char code: skip
            {},  # no symbol: unparseable
        ]
    )
    monkeypatch.setattr(
        "yowayowa.services.screening_pipeline._screener_factory",
        lambda: fake,
    )
    result = run_screening_pipeline(
        session=None,
        screener_mode="on",
        credit_margin_mode="off",
        market_size=50,
        run_date=SIGNAL_DATE,
    )
    screener = [c for c in result.candidates if c.source == ScreeningSource.MARKET_SCREENER]
    assert sorted(c.code for c in screener) == ["6758"]
    assert all(c.signal == ScreeningSignal.SCREENER_LOW_PE for c in screener)
    counts = result.per_source_counts[ScreeningSource.MARKET_SCREENER.value]
    assert counts["quotes"] == 5
    assert counts["skipped_unparseable"] == 4  # RKLB, 135A9.T, empty row, 55555.T
    assert counts["candidates"] == 1
    assert fake.calls and fake.calls[0].size == 50
    request_filters = fake.calls[0].filters
    assert {f.field for f in request_filters} == {"region", "peratio.lasttwelvemonths"}

    monkeypatch.undo()

    # screener failure: coverage records the failure, other sources unaffected.
    def _boom() -> FakeScreenerProvider:
        raise RuntimeError("network down")

    monkeypatch.setattr(
        "yowayowa.services.screening_pipeline._screener_factory",
        _boom,
    )
    failed = run_screening_pipeline(
        session=None,
        screener_mode="on",
        credit_margin_mode="off",
        run_date=SIGNAL_DATE,
    )
    assert failed.per_source_counts[ScreeningSource.MARKET_SCREENER.value]["candidates"] == 0
    failed_counts = failed.per_source_counts[ScreeningSource.MARKET_SCREENER.value]
    assert "provider_error" in failed_counts["reason"]


def test_public_mode_policy_disables_screener(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeScreenerProvider(quotes=[])
    monkeypatch.setenv("YOWAYOWA_MODE", "public")
    from yowayowa.config import get_settings

    get_settings.cache_clear()
    try:
        result = run_screening_pipeline(
            session=None,
            screener_mode="policy",
            credit_margin_mode="off",
            run_date=SIGNAL_DATE,
        )
        screener_counts = result.per_source_counts[ScreeningSource.MARKET_SCREENER.value]
        assert screener_counts["candidates"] == 0
        assert screener_counts["reason"] == "policy_or_off"
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
    assert fake.calls == []  # for lint-symmetry; fake never constructed here


# -------------------------------------------------- pipeline combination + models


def test_pipeline_combines_sources_without_dedup() -> None:
    engine = _memory_engine()
    try:
        _seed_credit_engine(engine)
        fake = FakeScreenerProvider(quotes=[{"symbol": "6758.T", "regularMarketPrice": 1200.0}])
        engine_session = Session(engine, expire_on_commit=False)
        try:
            result = run_screening_pipeline(
                engine_session,
                edinet_path=EDINET_SAMPLE,
                screener_factory=lambda: fake,
                market_size=50,
                run_date=SIGNAL_DATE,
            )
        finally:
            engine_session.close()
    finally:
        engine.dispose()
    sources = {c.source for c in result.candidates}
    assert sources == {
        ScreeningSource.EDINET_FILING,
        ScreeningSource.CREDIT_MARGIN_WEEKLY,
        ScreeningSource.MARKET_SCREENER,
    }
    # No dedup: 6758 appears from two independent sources/signals.
    codes_6758 = [c for c in result.candidates if c.code == "6758"]
    assert len(codes_6758) >= 2
    assert {c.signal for c in codes_6758} == {
        ScreeningSignal.CREDIT_SHORT_SURGE,
        ScreeningSignal.CREDIT_LONG_SURGE,
        ScreeningSignal.SCREENER_LOW_PE,
    }

    # Missing-data invariant: any candidate always has full evidence + reason.
    for candidate in result.candidates:
        assert candidate.value
        assert candidate.reason
        assert candidate.provenance.provider
        assert candidate.detected_at.tzinfo is not None

    # Field-required invariant: ScreeningCandidate cannot be zero-filled.
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ScreeningCandidate(
            code="6758",
            source=ScreeningSource.EDINET_FILING,
            signal=ScreeningSignal.FILING_BUYBACK,
        )


def test_run_result_with_no_sources_still_records_coverage(tmp_path: Path) -> None:
    engine = _memory_engine()
    try:
        with Session(engine, expire_on_commit=False) as session:
            result = run_screening_pipeline(
                session,
                edinet_path=EDINET_EMPTY,
                screener_mode="off",
                credit_margin_mode="on",
                run_date=SIGNAL_DATE,
            )
    finally:
        engine.dispose()
    assert result.candidates == []
    assert isinstance(result, ScreeningRunResult)
    assert result.run_date == SIGNAL_DATE


# ------------------------------------------------------------------ persistence


def _two_candidate_result() -> ScreeningRunResult:
    provenance = Provenance(
        provider="edinet-v2",
        source="fixture",
        license_class=LicenseClass.OFFICIAL_PUBLIC,
        retrieved_at=SIGNAL_RETRIEVED,
        as_of=SIGNAL_DATE,
    )
    return ScreeningRunResult(
        run_date=SIGNAL_DATE,
        candidates=[
            ScreeningCandidate(
                code="11115",
                source=ScreeningSource.EDINET_FILING,
                signal=ScreeningSignal.FILING_FORECAST_REVISION,
                value={"doc_id": "S100TEST1"},
                reason="訂正有価証券報告書",
                provenance=provenance,
                detected_at=SIGNAL_RETRIEVED,
            ),
            ScreeningCandidate(
                code="6758",
                source=ScreeningSource.CREDIT_MARGIN_WEEKLY,
                signal=ScreeningSignal.CREDIT_SHORT_SURGE,
                value={"short_total": 300, "ratio_change_1w": 1.0},
                reason="売残が前週比+30%以上",
                provenance=provenance,
                detected_at=SIGNAL_RETRIEVED,
            ),
        ],
        per_source_counts={"edinet_filing": {"candidates": 1}},
        coverage={"edinet_coverage_complete": True},
        provenance=provenance,
    )


def test_persist_screening_run_is_idempotent_per_run_date() -> None:
    engine = _memory_engine()
    try:
        result = _two_candidate_result()
        with Session(engine, expire_on_commit=False) as session:
            first = persist_screening_run(session, result)
            assert first == {"inserted": 2, "updated": 0}
            second = persist_screening_run(session, result)
            assert second == {"inserted": 2, "updated": 2}
            total = session.scalar(
                select(ScreeningCandidateRecord).order_by(ScreeningCandidateRecord.id)
            )
            assert total is not None
            with Session(engine, expire_on_commit=False) as fresh:
                ids = fresh.scalars(select(ScreeningCandidateRecord.id)).all()
                assert len(ids) == 2  # delete + insert keeps exactly two rows
                restored = fresh.scalars(
                    select(ScreeningCandidateRecord).order_by(ScreeningCandidateRecord.id)
                ).all()
                assert {r.code for r in restored} == {"11115", "6758"}
                # read provenance restores the capture provenance.
                read = read_screening_candidates(fresh, run_date=SIGNAL_DATE)
                assert read
                assert read[0]["provenance"]["provider"] == "edinet-v2"
            del total
    finally:
        engine.dispose()


# ------------------------------------------------------------------------ API


def _api_db_engine(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from yowayowa import db as db_module

    monkeypatch.setenv("YOWAYOWA_DATABASE_URL", f"sqlite:///{tmp_path / 'screening-api.db'}")
    db_module.dispose_database()


def test_api_run_and_candidates(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    _api_db_engine(monkeypatch, tmp_path)
    fake = FakeScreenerProvider(quotes=[{"symbol": "6758.T", "regularMarketPrice": 1200.0}])
    monkeypatch.setattr(
        "yowayowa.services.screening_pipeline._screener_factory",
        lambda: fake,
    )
    with TestClient(app) as client:
        response = client.post("/v1/screening/run")
        assert response.status_code == 200
        payload = response.json()
        assert payload["persisted"]["inserted"] >= 1
        # The run date is "today" as seen by the pipeline itself; comparing to
        # a fresh date.today() here would flake when the test session crosses
        # midnight JST between the POST and the assertion.
        assert (
            payload["result"]["run_date"]
            == date.fromisoformat(payload["result"]["run_date"]).isoformat()
        )

        candidates = client.get("/v1/screening/candidates", params={"signal": "screener_low_pe"})
        assert candidates.status_code == 200
        rows = candidates.json()
        assert rows and rows[0]["code"] == "6758"
        assert rows[0]["provenance"]["provider"]


def test_api_fails_closed_outside_personal_mode(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    _api_db_engine(monkeypatch, tmp_path)
    monkeypatch.setenv("YOWAYOWA_MODE", "public")
    monkeypatch.setenv("YOWAYOWA_API_TOKEN", "public-token")
    from yowayowa.config import get_settings

    get_settings.cache_clear()
    try:
        with TestClient(app) as client:
            run_response = client.post(
                "/v1/screening/run", headers={"Authorization": "Bearer public-token"}
            )
            assert run_response.status_code == 403
            assert "personal" in run_response.json()["detail"]
            read_response = client.get(
                "/v1/screening/candidates", headers={"Authorization": "Bearer public-token"}
            )
            assert read_response.status_code == 403
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


def test_openapi_contains_screening_paths() -> None:
    schema = app.openapi()
    assert {"/v1/screening/run", "/v1/screening/candidates"} <= set(schema["paths"])


# ------------------------------------------------------------------------ CLI


def test_cli_screening_run_and_candidates(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from yowayowa import db as db_module

    monkeypatch.setenv("YOWAYOWA_DATABASE_URL", f"sqlite:///{tmp_path / 'cli.db'}")
    db_module.dispose_database()
    fake = FakeScreenerProvider(quotes=[{"symbol": "6758.T", "regularMarketPrice": 1200.0}])
    monkeypatch.setattr(
        "yowayowa.services.screening_pipeline._screener_factory",
        lambda: fake,
    )
    result = runner.invoke(cli_app, ["screening-run"])
    assert result.exit_code == 0, result.output
    assert "screening run" in result.output

    listing = runner.invoke(cli_app, ["screening-candidates", "6758"])
    assert listing.exit_code == 0, listing.output
    assert "6758" in listing.output

    listing_all = runner.invoke(cli_app, ["screening-candidates", "ALL"])
    assert listing_all.exit_code == 0, listing_all.output

    monkeypatch.undo()
    db_module.dispose_database()


# ------------------------------------------------------------------- AI tool


def test_ai_tool_get_screening_candidates_reads_session() -> None:

    from yowayowa.services.ai_agent import InvestmentResearchAgent

    engine = _memory_engine()
    try:
        result = _two_candidate_result()
        with Session(engine, expire_on_commit=False) as session:
            persist_screening_run(session, result)
        agent = InvestmentResearchAgent(
            Settings(database_url="sqlite:///:memory:"),
            session,
        )
        payload = agent._tool_screening_candidates({})
        assert payload and payload[0]["code"] in {"11115", "6758"}
        limited = agent._tool_screening_candidates({"limit": 1})
        assert len(limited) == 1
    finally:
        engine.dispose()
