"""CLI date-contract tests for ``screening-candidates`` (P5, 2026-10-23).

The candidates table must let the operator tell the pipeline run date apart
from the data's own as-of date and the fetch timestamp: ``Run date`` shows the
pipeline execution date, ``As of`` only ``provenance.as_of`` and ``Retrieved``
only ``provenance.retrieved_at``. Missing/legacy/unparseable provenance dates
render as ``unavailable`` and are never backfilled from the run date, the
current time or any other field. Persisted ISO strings are echoed verbatim, so
timezone information (or the absence of one) is preserved exactly.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from yowayowa.cli_entry import app as entry_app
from yowayowa.db import Base, ScreeningCandidateRecord

runner = CliRunner()

RUN_DATE = date(2026, 10, 23)
RETRIEVED = datetime(2026, 10, 22, 17, 30, tzinfo=UTC)


def _seed_engine(rows: list[dict[str, Any]]):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        for row in rows:
            session.add(ScreeningCandidateRecord(**row))
        session.commit()
    return engine


def _candidate(
    code: str,
    *,
    provenance: Any,
    run_date: date = RUN_DATE,
    source: str = "edinet_filing",
    signal: str = "filing_forecast_revision",
) -> dict[str, Any]:
    return {
        "run_date": run_date,
        "source": source,
        "code": code,
        "signal": signal,
        "document_id": f"doc-{code}",
        "company_name": None,
        "value": {},
        "reason": "EDINET提出: 訂正臨時報告書",
        "provenance": provenance,
        "retrieved_at": RETRIEVED,
    }


def _invoke(monkeypatch: pytest.MonkeyPatch, engine, args: list[str]) -> str:
    from yowayowa import screening_cli

    def _fake_get_session() -> Session:
        return Session(engine)

    monkeypatch.setattr(screening_cli, "get_session", _fake_get_session)
    # Wide console: the seven-column table must carry the full persisted date
    # strings; at the CliRunner default 80 columns rich crops every cell with
    # an ellipsis, which would test rich's squeezing instead of the contract.
    result = runner.invoke(entry_app, ["screening-candidates", *args], env={"COLUMNS": "220"})
    assert result.exit_code == 0, result.output
    return result.output


def test_run_date_as_of_and_retrieved_are_distinct(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run date, as-of and retrieval are visible as three different dates."""

    engine = _seed_engine(
        [
            _candidate(
                "29010",
                provenance={
                    "provider": "edinet-v2",
                    "source": "EDINET API Version 2 daily document list (type=2)",
                    "license_class": "official_public",
                    "retrieved_at": "2026-09-24T11:49:11.304750Z",
                    "as_of": "2026-09-24",
                    "notes": [],
                },
            ),
            _candidate(
                "72030",
                provenance={
                    "provider": "yahoo-screener",
                    "source": "Yahoo Finance screener",
                    "license_class": "personal_only",
                    "retrieved_at": "2026-10-11T14:03:43.786773+09:00",
                    "as_of": "2026-10-11T00:00:00+09:00",
                    "notes": [],
                },
            ),
        ]
    )
    try:
        output = _invoke(monkeypatch, engine, ["ALL", "--limit", "5"])
    finally:
        engine.dispose()

    assert "Run date" in output
    assert "As of" in output
    assert "Retrieved" in output
    assert "2026-10-23" in output  # run date
    assert "2026-09-24" in output  # older as-of: EDINET filing data is not run-day data
    # Persisted strings echo verbatim: the Z and the +09:00 offsets survive.
    assert "2026-09-24T11:49:11.304750Z" in output
    assert "2026-10-11T14:03:43.786773+09:00" in output
    # Existing columns keep their content.
    assert "29010" in output and "72030" in output
    assert "edinet_filing" in output and "filing_forecast_revision" in output
    assert "unavailable" not in output


def test_missing_legacy_or_invalid_provenance_shows_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """None, legacy, non-dict and unparseable provenance degrade to unavailable."""

    engine = _seed_engine(
        [
            _candidate("11110", provenance=None),  # legacy row: no provenance payload
            _candidate("22220", provenance={}),  # dict without the date keys
            _candidate(
                "33330",
                provenance={"provider": "x", "as_of": None, "retrieved_at": None},
            ),
            _candidate("44440", provenance="not-a-dict"),  # corrupt payload type
            _candidate(
                "55550",
                provenance={"as_of": "24/09/2026", "retrieved_at": "soon"},
            ),
        ]
    )
    try:
        output = _invoke(monkeypatch, engine, ["ALL"])
    finally:
        engine.dispose()

    # 5 rows x 2 provenance date columns, all unavailable: never backfilled
    # with the run date, the current time or the sibling column value.
    assert output.count("unavailable") == 10
    assert "24/09/2026" not in output and "soon" not in output
    assert "not-a-dict" not in output


def test_naive_retrieved_stays_naive_and_as_of_date_only_is_kept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A timezone-naive persisted timestamp must not be relabelled as UTC."""

    engine = _seed_engine(
        [
            _candidate(
                "67580",
                provenance={
                    "provider": "edinet-v2",
                    "source": "s",
                    "license_class": "official_public",
                    "retrieved_at": "2026-10-22T09:30:00",
                    "as_of": "2026-10-22",
                    "notes": [],
                },
            )
        ]
    )
    try:
        output = _invoke(monkeypatch, engine, ["ALL"])
    finally:
        engine.dispose()

    assert "2026-10-22T09:30:00" in output
    assert "2026-10-22T09:30:00+00:00" not in output
    assert "2026-10-22T09:30:00Z" not in output
    assert "2026-10-22" in output  # as-of stays a bare date


def test_empty_result_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    from yowayowa import screening_cli

    engine = _seed_engine([])
    try:
        monkeypatch.setattr(screening_cli, "get_session", lambda: Session(engine))
        result = runner.invoke(
            entry_app, ["screening-candidates", "99999"], env={"COLUMNS": "220"}
        )
        assert result.exit_code == 1
        assert "No screening candidates persisted for 99999" in result.output
    finally:
        engine.dispose()


def test_code_filter_and_limit_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _seed_engine(
        [
            _candidate("14140", provenance={"as_of": "2026-09-24"}),
            _candidate("67580", provenance={"as_of": "2026-10-05"}),
            _candidate(
                "67580",
                provenance={"as_of": "2026-10-05"},
                source="market_screener",
                signal="price_drop",
            ),
        ]
    )
    try:
        filtered = _invoke(monkeypatch, engine, ["14140"])
        assert "14140" in filtered
        assert "67580" not in filtered

        limited = _invoke(monkeypatch, engine, ["ALL", "--limit", "2"])
        assert limited.count("unavailable") == 2  # only 2 of the 3 rows listed
    finally:
        engine.dispose()
