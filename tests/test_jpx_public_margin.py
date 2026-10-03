"""Free JPX daily margin publications: parsers, persistence and signals."""

from __future__ import annotations

from datetime import UTC, date, datetime
from io import BytesIO

import pytest
from openpyxl import Workbook
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from yowayowa.db import Base, JpxMarginAuxRecord, JpxMarginBalanceRecord
from yowayowa.providers import jpx_public_balance as balance_parser
from yowayowa.providers import jpx_public_flow as flow_parser
from yowayowa.providers.jpx_public_margin import (
    parse_jpx_margin_flow_pdf,
    parse_jpx_margin_watch_xlsx,
    parse_jpx_premium_xlsx,
    parse_jpx_public_balance_pdf,
    safe_jpx_code_from_premium,
)
from yowayowa.services.jpx_margin import read_jpx_margin_by_code
from yowayowa.services.jpx_margin_signals import scan_jpx_margin_signals
from yowayowa.services.jpx_public_margin import (
    discover_jpx_artifact_url,
    ingest_jpx_margin_flow_pdf,
    ingest_jpx_premium_xlsx,
    ingest_jpx_public_balance_pdf,
)

SOURCE = "https://www.jpx.co.jp/example"
RETRIEVED = datetime(2026, 10, 2, 8, 0, tzinfo=UTC)


def _balance_lines(
    *,
    code: str = "72030",
    short_ratio: str = "3.0%",
    long_change: str = "▲ 20",
) -> list[str]:
    return [
        "2026/10/2",
        "2026/10/1 申込み現在",
        "B",
        "テスト株式会社　普通株式",
        "プライム",
        "貸",
        code,
        "JP0000000001 株数 Shs.",
        "100",
        "10",
        short_ratio,
        "200",
        long_change,
        "5.0%",
        "40",
        "5",
        "60",
        "5",
        "120",
        "▲ 10",
        "80",
        "▲ 10",
        code,
        "JP0000000001 金額 Val.",
        "1000",
        "100",
        "-",
        "2000",
        "▲ 200",
        "-",
        "400",
        "40",
        "600",
        "60",
        "1200",
        "▲ 100",
        "800",
        "▲ 100",
        "1 銘柄",
        "株数 Shs.",
        "総合計",
        "1 銘柄",
        "株数 Shs.",
        "プライム 小計",
        "0 銘柄",
        "株数 Shs.",
        "スタンダード 小計",
        "0 銘柄",
        "株数 Shs.",
        "グロース 小計",
        "0 銘柄",
        "株数 Shs.",
        "投信等 小計",
        "1 銘柄",
        "株数 Shs.",
        "貸借銘柄",
        "0 銘柄",
        "株数 Shs.",
        "制度信用銘柄",
        "0 銘柄",
        "株数 Shs.",
        "その他",
    ]


def _workbook_bytes(rows: list[list[object]]) -> bytes:
    book = Workbook()
    sheet = book.active
    for row in rows:
        sheet.append(row)
    buffer = BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def _premium_bytes(premium: object) -> bytes:
    return _workbook_bytes(
        [
            ["header"],
            ["header"],
            ["header"],
            [20261001, 7203, "テスト株式会社", "東証", 1000, 8.0, premium],
        ]
    )


def _flow_lines(
    purchase: str,
    *,
    code: str = "72030",
) -> list[str]:
    return [
        "2026年10月2日売買分",
        "2026年10月1日売買分",
        "2026年9月30日売買分",
        "日",
        "テスト株式会社　普通株式",
        "プライム",
        "貸",
        code,
        "10.0%",
        purchase,
        "9.0%",
        "35.0%",
        "8.0%",
        "30.0%",
    ]


def test_balance_parser_keeps_exact_nonzero_fifth_character(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        balance_parser,
        "extract_jpx_pdf_lines",
        lambda _: _balance_lines(code="25935", long_change="-"),
    )
    batch = parse_jpx_public_balance_pdf(
        b"synthetic-pdf",
        source_url=SOURCE,
        retrieved_at=RETRIEVED,
    )
    assert batch.declared_issue_count == 1
    assert batch.balances[0].code == "25935"
    assert batch.details[0].code == "25935"
    assert batch.details[0].long_source_change is None
    assert batch.balances[0].short_total == 100
    assert batch.balances[0].short_negotiable + batch.balances[0].short_standardized == 100
    assert batch.section_counts == {
        "プライム": 1,
        "スタンダード": 0,
        "グロース": 0,
        "投信等": 0,
    }


def test_balance_parser_fails_closed_if_declared_count_does_not_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines = _balance_lines()
    lines[lines.index("総合計") - 2] = "2 銘柄"
    monkeypatch.setattr(balance_parser, "extract_jpx_pdf_lines", lambda _: lines)
    with pytest.raises(ValueError, match="count mismatch"):
        parse_jpx_public_balance_pdf(b"synthetic-pdf", source_url=SOURCE)


def test_watch_parser_preserves_missing_change_and_real_zero() -> None:
    data = _workbook_bytes(
        [
            ["2026/10/1", "申込み現在", "2026/10/2"],
            [],
            [
                "B",
                "日",
                "喚",
                "テスト株式会社",
                "プライム",
                "貸",
                "72030",
                "JP0000000001",
                100,
                "-",
                3.0,
                200,
                0,
                5.0,
                50.0,
                40,
                "-",
                60,
                0,
                120,
                -10,
                80,
                10,
            ],
        ]
    )
    rows = parse_jpx_margin_watch_xlsx(
        data,
        source_url=SOURCE,
        retrieved_at=RETRIEVED,
    )
    assert len(rows) == 1
    assert rows[0].short_change is None
    assert rows[0].long_change == 0
    assert rows[0].short_standardized_change == 0


def test_premium_parser_distinguishes_asterisk_missing_from_numeric_zero() -> None:
    missing = parse_jpx_premium_xlsx(
        _premium_bytes("*****"),
        source_url=SOURCE,
        retrieved_at=RETRIEVED,
    )[0]
    zero = parse_jpx_premium_xlsx(
        _premium_bytes(0),
        source_url=SOURCE,
        retrieved_at=RETRIEVED,
    )[0]
    assert missing.premium_charge is None
    assert zero.premium_charge == 0.0


def test_premium_code_resolution_never_guesses_class_security() -> None:
    assert safe_jpx_code_from_premium("2593", {"25935"}) is None
    assert safe_jpx_code_from_premium("2593", {"25930", "25935"}) == "25930"


def test_flow_parser_separates_status_marker_and_preserves_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        flow_parser,
        "extract_jpx_pdf_lines",
        lambda _: _flow_lines("-"),
    )
    rows = parse_jpx_margin_flow_pdf(
        b"synthetic-flow",
        source_url=SOURCE,
        retrieved_at=RETRIEVED,
    )
    assert len(rows) == 3
    latest = rows[0]
    assert latest.status_marker == "日"
    assert latest.company_name == "テスト株式会社　普通株式"
    assert latest.new_purchase_ratio_pct is None
    assert latest.trade_date == date(2026, 10, 2)
    assert latest.published_at.isoformat() == "2026-10-02T16:30:00+09:00"
    assert rows[1].published_at.isoformat() == "2026-10-01T16:30:00+09:00"
    assert rows[2].published_at.isoformat() == "2026-09-30T16:30:00+09:00"


def _flow_lines_shifted(
    *,
    latest_purchase: str = "45.0%",
    prior_purchase: str = "40.0%",
    oldest_purchase: str = "35.0%",
) -> list[str]:
    return [
        "2026年10月3日売買分",
        "2026年10月2日売買分",
        "2026年10月1日売買分",
        "日",
        "テスト株式会社　普通株式",
        "プライム",
        "貸",
        "72030",
        "11.0%",
        latest_purchase,
        "10.0%",
        prior_purchase,
        "9.0%",
        oldest_purchase,
    ]


def test_flow_reingest_preserves_overlapping_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    later = datetime(2026, 10, 3, 8, 0, tzinfo=UTC)
    try:
        with Session(engine) as session:
            monkeypatch.setattr(
                flow_parser,
                "extract_jpx_pdf_lines",
                lambda _: _flow_lines("40.0%"),
            )
            ingest_jpx_margin_flow_pdf(
                session,
                b"flow-first",
                source_url=SOURCE,
                retrieved_at=RETRIEVED,
            )
            original = session.scalar(
                select(JpxMarginAuxRecord).where(
                    JpxMarginAuxRecord.kind == "flow",
                    JpxMarginAuxRecord.as_of_date == date(2026, 10, 2),
                    JpxMarginAuxRecord.code == "72030",
                )
            )
            assert original is not None
            original_sha = original.source_sha256
            original_retrieved = original.retrieved_at
            original_published = original.published_at

            monkeypatch.setattr(
                flow_parser,
                "extract_jpx_pdf_lines",
                lambda _: _flow_lines_shifted(),
            )
            ingest_jpx_margin_flow_pdf(
                session,
                b"flow-second",
                source_url=SOURCE,
                retrieved_at=later,
            )

            preserved = session.scalar(
                select(JpxMarginAuxRecord).where(
                    JpxMarginAuxRecord.kind == "flow",
                    JpxMarginAuxRecord.as_of_date == date(2026, 10, 2),
                    JpxMarginAuxRecord.code == "72030",
                )
            )
            newest = session.scalar(
                select(JpxMarginAuxRecord).where(
                    JpxMarginAuxRecord.kind == "flow",
                    JpxMarginAuxRecord.as_of_date == date(2026, 10, 3),
                    JpxMarginAuxRecord.code == "72030",
                )
            )
            assert preserved is not None
            assert newest is not None
            assert preserved.source_sha256 == original_sha
            assert preserved.retrieved_at == original_retrieved
            assert preserved.published_at == original_published
            assert newest.retrieved_at != original_retrieved
            assert newest.published_at is not None
            assert newest.published_at.date() == date(2026, 10, 3)
            assert (newest.published_at.hour, newest.published_at.minute) == (16, 30)
    finally:
        engine.dispose()


def test_flow_reingest_rejects_changed_historical_observation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    later = datetime(2026, 10, 3, 8, 0, tzinfo=UTC)
    try:
        with Session(engine) as session:
            monkeypatch.setattr(
                flow_parser,
                "extract_jpx_pdf_lines",
                lambda _: _flow_lines("40.0%"),
            )
            ingest_jpx_margin_flow_pdf(
                session,
                b"flow-first",
                source_url=SOURCE,
                retrieved_at=RETRIEVED,
            )

            monkeypatch.setattr(
                flow_parser,
                "extract_jpx_pdf_lines",
                lambda _: _flow_lines_shifted(prior_purchase="41.0%"),
            )
            with pytest.raises(ValueError, match="historical flow observation changed"):
                ingest_jpx_margin_flow_pdf(
                    session,
                    b"flow-mutated-history",
                    source_url=SOURCE,
                    retrieved_at=later,
                )

            newest = session.scalar(
                select(JpxMarginAuxRecord).where(
                    JpxMarginAuxRecord.kind == "flow",
                    JpxMarginAuxRecord.as_of_date == date(2026, 10, 3),
                    JpxMarginAuxRecord.code == "72030",
                )
            )
            preserved = session.scalar(
                select(JpxMarginAuxRecord).where(
                    JpxMarginAuxRecord.kind == "flow",
                    JpxMarginAuxRecord.as_of_date == date(2026, 10, 2),
                    JpxMarginAuxRecord.code == "72030",
                )
            )
            assert newest is None
            assert preserved is not None
            assert preserved.payload["new_purchase_ratio_pct"] == 40.0
    finally:
        engine.dispose()


def test_discovery_rejects_cross_host_and_uses_matching_artifact() -> None:
    html = """
    <a href="https://evil.example/file.pdf">bad</a>
    <a href="/files/latest.xlsx">xlsx</a>
    <a href="/files/latest.pdf">pdf</a>
    """
    assert (
        discover_jpx_artifact_url(
            html,
            page_url="https://www.jpx.co.jp/markets/x.html",
            extension=".pdf",
        )
        == "https://www.jpx.co.jp/files/latest.pdf"
    )


def test_limit_one_still_uses_true_previous_persisted_day() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            common = dict(
                code="72030",
                company_name="Test",
                isin="JP0000000001",
                market_code=None,
                margin_code="2",
                short_negotiable=40,
                short_standardized=60,
                long_negotiable=120,
                long_standardized=80,
                short_total_value=1000,
                long_total_value=2000,
                short_negotiable_value=400,
                short_standardized_value=600,
                long_negotiable_value=1200,
                long_standardized_value=800,
                source_url=SOURCE,
                retrieved_at=RETRIEVED,
            )
            session.add(
                JpxMarginBalanceRecord(
                    application_date=date(2026, 9, 30),
                    short_total=90,
                    long_total=220,
                    **common,
                )
            )
            session.add(
                JpxMarginBalanceRecord(
                    application_date=date(2026, 10, 1),
                    short_total=100,
                    long_total=200,
                    **common,
                )
            )
            session.commit()
            series = read_jpx_margin_by_code(session, "72030", limit=1)
            assert len(series.points) == 1
            assert series.points[0].short_change == 10
            assert series.points[0].long_change == -20
            assert series.points[0].previous_application_date == date(2026, 9, 30)
    finally:
        engine.dispose()


def test_squeeze_watch_requires_observed_non_null_buy_flow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    try:
        monkeypatch.setattr(
            balance_parser,
            "extract_jpx_pdf_lines",
            lambda _: _balance_lines(),
        )
        with Session(engine) as session:
            ingest_jpx_public_balance_pdf(
                session,
                b"balance",
                source_url=SOURCE,
                retrieved_at=RETRIEVED,
            )
            ingest_jpx_premium_xlsx(
                session,
                _premium_bytes(2.0),
                source_url=SOURCE,
                retrieved_at=RETRIEVED,
            )

            monkeypatch.setattr(
                flow_parser,
                "extract_jpx_pdf_lines",
                lambda _: _flow_lines("-"),
            )
            ingest_jpx_margin_flow_pdf(
                session,
                b"flow-missing",
                source_url=SOURCE,
                retrieved_at=RETRIEVED,
            )
            assert scan_jpx_margin_signals(session, "squeeze-watch") == []

            monkeypatch.setattr(
                flow_parser,
                "extract_jpx_pdf_lines",
                lambda _: _flow_lines("55.0%"),
            )
            ingest_jpx_margin_flow_pdf(
                session,
                b"flow-present",
                source_url=SOURCE,
                retrieved_at=RETRIEVED,
            )
            hits = scan_jpx_margin_signals(session, "squeeze-watch")
            assert [hit.code for hit in hits] == ["72030"]
            assert hits[0].metrics["new_purchase_ratio_pct"] == 55.0
    finally:
        engine.dispose()
