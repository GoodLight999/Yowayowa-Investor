from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from yowayowa.db import Base
from yowayowa.domain import Fundamentals, LicenseClass, MetricPoint, MetricSeries, Provenance
from yowayowa.services import strategy_snapshot_service
from yowayowa.services.strategy_snapshot_service import (
    KIYOHARA_GLOBAL_ID,
    BuiltinSnapshotOutcome,
    evaluate_and_record_builtin,
)
from yowayowa.services.strategy_tracking import list_strategy_snapshots

CAPTURED_AT = datetime(2026, 9, 30, tzinfo=UTC)


@contextmanager
def _session() -> Iterator[Session]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            yield session
    finally:
        engine.dispose()


def _provenance() -> Provenance:
    return Provenance(
        provider="fixture",
        source="fixture statements",
        license_class=LicenseClass.OFFICIAL_PUBLIC,
        retrieved_at=datetime(2026, 8, 25, tzinfo=UTC),
        as_of=date(2025, 12, 31),
    )


def _fundamentals(symbol: str) -> Fundamentals:
    provenance = _provenance()

    def series(key: str, value: float) -> MetricSeries:
        return MetricSeries(
            key=key,
            label=key,
            points=[
                MetricPoint(
                    period_start=date(2025, 1, 1),
                    period_end=date(2025, 12, 31),
                    fiscal_year=2025,
                    fiscal_period="FY",
                    value=Decimal(str(value)),
                    unit="USD",
                )
            ],
        )

    return Fundamentals(
        symbol=symbol,
        cik="0000000001",
        company_name=f"{symbol} Corp",
        metrics={
            "current_assets": series("current_assets", 120),
            "liabilities": series("liabilities", 40),
        },
        provenance=provenance,
    )


class _FakeScreener:
    quotes_by_call: ClassVar[list[list[dict[str, Any]]]] = []
    calls: ClassVar[list[Any]] = []

    def __init__(self, settings: Any) -> None:
        self.settings = settings

    def screen(self, request: Any) -> Any:
        type(self).calls.append(request)
        quotes = type(self).quotes_by_call.pop(0)
        return SimpleNamespace(quotes=quotes)


class _FakeFundamentalsProvider:
    def company_facts(self, symbol: str) -> Fundamentals:
        return _fundamentals(symbol)


@pytest.fixture(autouse=True)
def _stub_providers(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    _FakeScreener.quotes_by_call = []
    _FakeScreener.calls = []
    monkeypatch.setattr(strategy_snapshot_service, "YahooScreenerProvider", _FakeScreener)
    monkeypatch.setattr(
        strategy_snapshot_service,
        "fundamentals_provider",
        lambda: _FakeFundamentalsProvider(),
    )
    yield
    _FakeScreener.quotes_by_call = []
    _FakeScreener.calls = []


def _quote(symbol: str, market_cap: float | None, pe_ratio: float | None = 10.0) -> dict[str, Any]:
    row: dict[str, Any] = {"symbol": symbol}
    if market_cap is not None:
        row["marketCap"] = market_cap
    if pe_ratio is not None:
        row["trailingPE"] = pe_ratio
    return row


def test_evaluate_and_record_builtin_records_point_in_time_snapshots() -> None:
    _FakeScreener.quotes_by_call = [[_quote("GOOD", 100.0, 10.0), _quote("BEST", 200.0, 5.0)]]
    with _session() as session:
        outcome = evaluate_and_record_builtin(
            session,
            KIYOHARA_GLOBAL_ID,
            "jp",
            25,
            captured_at=CAPTURED_AT,
        )

        assert isinstance(outcome, BuiltinSnapshotOutcome)
        assert outcome.errors == {}
        assert outcome.supplement_errors == {}
        assert [item.symbol for item in outcome.evaluations] == ["GOOD", "BEST"]
        assert len(outcome.snapshots) == 2
        rows = list_strategy_snapshots(session)
        assert sorted(row.symbol for row in rows) == ["BEST", "GOOD"]
        assert {row.region for row in rows} == {"jp"}
        assert {row.scoring_version for row in rows} == {"kiyohara_priority_v1"}
        assert all(row.evaluation.research_priority is not None for row in rows)


def test_evaluate_and_record_builtin_excludes_quotes_without_market_cap() -> None:
    _FakeScreener.quotes_by_call = [
        [_quote("MISSING", None), _quote("NEGATIVE", -5.0), _quote("GOOD", 100.0)]
    ]
    with _session() as session:
        outcome = evaluate_and_record_builtin(
            session,
            KIYOHARA_GLOBAL_ID,
            "jp",
            25,
            captured_at=CAPTURED_AT,
        )

        assert [item.symbol for item in outcome.evaluations] == ["GOOD"]
        assert outcome.errors["MISSING"].startswith("ValueError:")
        assert outcome.errors["NEGATIVE"].startswith("ValueError:")
        assert set(outcome.errors) == {"MISSING", "NEGATIVE"}


def test_evaluate_and_record_builtin_rerun_same_day_is_deduplicated() -> None:
    _FakeScreener.quotes_by_call = [[_quote("GOOD", 100.0)], [_quote("GOOD", 100.0)]]
    with _session() as session:
        first = evaluate_and_record_builtin(
            session,
            KIYOHARA_GLOBAL_ID,
            "jp",
            25,
            captured_at=CAPTURED_AT,
        )
        second = evaluate_and_record_builtin(
            session,
            KIYOHARA_GLOBAL_ID,
            "jp",
            25,
            captured_at=CAPTURED_AT,
        )

        assert len(first.snapshots) == 1
        assert len(second.snapshots) == 1
        assert second.snapshots[0].id == first.snapshots[0].id
        assert len(list_strategy_snapshots(session)) == 1


def test_evaluate_and_record_builtin_normalizes_region() -> None:
    _FakeScreener.quotes_by_call = [[_quote("GOOD", 100.0)]]
    with _session() as session:
        outcome = evaluate_and_record_builtin(
            session,
            KIYOHARA_GLOBAL_ID,
            " JP ",
            25,
            captured_at=CAPTURED_AT,
        )

        request = _FakeScreener.calls[0]
        region_filter = next(item for item in request.filters if item.field == "region")
        assert region_filter.value == ["jp"]
        assert outcome.snapshots[0].region == "jp"
        assert all(row.region == "jp" for row in list_strategy_snapshots(session))


def test_evaluate_and_record_builtin_reports_fundamentals_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FailingProvider:
        def company_facts(self, symbol: str) -> Fundamentals:
            if symbol == "BAD":
                raise LookupError("fixture unavailable")
            return _fundamentals(symbol)

    monkeypatch.setattr(
        strategy_snapshot_service,
        "fundamentals_provider",
        lambda: _FailingProvider(),
    )

    _FakeScreener.quotes_by_call = [[_quote("BAD", 100.0), _quote("GOOD", 100.0)]]
    with _session() as session:
        outcome = evaluate_and_record_builtin(
            session,
            KIYOHARA_GLOBAL_ID,
            "jp",
            25,
            captured_at=CAPTURED_AT,
        )

    assert outcome.errors["BAD"] == "LookupError: fixture unavailable"
    assert [item.symbol for item in outcome.evaluations] == ["GOOD"]
