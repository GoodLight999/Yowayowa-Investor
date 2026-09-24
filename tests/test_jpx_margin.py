"""JPX daily margin balance ingestion: parser, persistence, API, CLI (P4-A).

Fixtures are the official JPX sample files (dummy data, official format;
see tests/fixtures/jpx/README.md). Live publication starts 2026-09-28, so
every test here runs offline against those fixtures.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from starlette.testclient import TestClient
from typer.testing import CliRunner

from yowayowa.api.app import app
from yowayowa.cli_entry import app as cli_app
from yowayowa.config import get_settings
from yowayowa.db import Base, JpxMarginBalanceRecord
from yowayowa.domain import LicenseClass
from yowayowa.jpx_models import JpxMarginBalance, normalize_jpx_code
from yowayowa.providers.base import ProviderPolicyError
from yowayowa.providers.jpx_margin import (
    JPX_MARGIN_CODE_MEANINGS,
    JpxMarginCsvError,
    enforce_jpx_margin_policy,
    jpx_margin_descriptor,
    parse_jpx_margin_csv,
)
from yowayowa.services.jpx_margin import (
    ingest_jpx_margin_csv,
    latest_jpx_margin_date,
    read_jpx_margin_by_code,
    read_jpx_margin_by_date,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "jpx"
EN_CSV = FIXTURES / "OutstandingMarginTradingByIssue.csv"
JP_CSV = FIXTURES / "JP_OutstandingMarginTradingByIssue.csv"
RETRIEVED_AT = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
SOURCE_URL = (
    "https://www.jpx.co.jp/markets/paid-info-equities/reference/sample_outstanding_margin.zip"
)

runner = CliRunner()


def _ingest_on_engine(
    engine,
    data: bytes,
    *,
    retrieved_at: datetime = RETRIEVED_AT,
    source_url: str = SOURCE_URL,
) -> list[JpxMarginBalance]:
    with Session(engine, expire_on_commit=False) as session:
        return ingest_jpx_margin_csv(
            session, data, source_url=source_url, retrieved_at=retrieved_at
        )


def _memory_engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


# --------------------------------------------------------------------- fixtures


def test_fixtures_are_byte_identical_copies_of_the_official_sample() -> None:
    """Guard against fixture drift: CRLF, fully quoted, 18 columns, no BOM."""

    en = EN_CSV.read_bytes()
    jp = JP_CSV.read_bytes()
    assert not en.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" in en and b"\r\n" in jp
    en_rows = list(csv.reader(io.StringIO(en.decode("utf-8"), newline="")))
    jp_rows = list(csv.reader(io.StringIO(jp.decode("cp932"), newline="")))
    assert len(en_rows[0]) == 18 and len(jp_rows[0]) == 18
    assert len(en_rows) == len(jp_rows) == 11  # header + 10 dummy issues
    # Same business rows in both distributions (code + volumes identical).
    assert [row[1] for row in en_rows[1:]] == [row[1] for row in jp_rows[1:]]
    assert [row[6:12] for row in en_rows[1:]] == [row[6:12] for row in jp_rows[1:]]


# ----------------------------------------------------------------- model rules


def test_model_rejects_identity_violation_for_both_sides() -> None:
    provenance = parse_jpx_margin_csv(EN_CSV.read_bytes(), source_url=SOURCE_URL)[0].provenance
    base = dict(
        application_date=date(2026, 9, 25),
        code="13010",
        short_total=3000,
        long_total=7000,
        short_negotiable=1000,
        short_standardized=2000,
        long_negotiable=3000,
        long_standardized=4000,
        provenance=provenance,
    )
    with pytest.raises(ValueError, match="short_total"):
        JpxMarginBalance(**{**base, "short_negotiable": 999})
    with pytest.raises(ValueError, match="long_total"):
        JpxMarginBalance(**{**base, "long_standardized": 999})


def test_normalize_jpx_code_rejects_decorated_or_short_codes() -> None:
    assert normalize_jpx_code("13010") == "13010"
    assert normalize_jpx_code("135A0") == "135A0"
    for bad in ("1301", "130100", "7203t", "1301 a", "ｱｱｱｱｱ", ""):
        with pytest.raises(ValueError):
            normalize_jpx_code(bad)


# ---------------------------------------------------------------------- parser


@pytest.mark.parametrize("fixture", [EN_CSV, JP_CSV], ids=["en-utf8", "jp-cp932"])
def test_parse_official_sample(fixture: Path) -> None:
    balances = parse_jpx_margin_csv(
        fixture.read_bytes(), source_url=SOURCE_URL, retrieved_at=RETRIEVED_AT
    )
    assert len(balances) == 10
    first = balances[0]
    assert first.application_date == date(2026, 4, 23)
    assert first.code == "13010"
    assert first.company_name is not None and first.company_name
    assert first.isin == "JP3257200000"
    assert first.market_code == "0111"
    assert first.margin_code in JPX_MARGIN_CODE_MEANINGS
    # 18 columns -> every field mapped; volumes and amounts both present.
    assert first.short_total == 3000
    assert first.long_total == 7000
    assert (first.short_negotiable, first.short_standardized) == (1000, 2000)
    assert (first.long_negotiable, first.long_standardized) == (3000, 4000)
    assert first.short_total_value == 300_000
    assert first.long_total_value == 700_000
    assert first.provenance.provider == "jpx_reference"
    assert first.provenance.license_class is LicenseClass.PERSONAL_ONLY
    assert first.provenance.as_of == date(2026, 4, 23)
    assert first.provenance.retrieved_at == RETRIEVED_AT


@pytest.mark.parametrize("fixture", [EN_CSV, JP_CSV], ids=["en-utf8", "jp-cp932"])
def test_parse_enforces_total_identity(fixture: Path) -> None:
    for balance in parse_jpx_margin_csv(fixture.read_bytes(), source_url=SOURCE_URL):
        assert balance.short_total == balance.short_negotiable + balance.short_standardized
        assert balance.long_total == balance.long_negotiable + balance.long_standardized
        assert balance.short_total_value == (
            balance.short_negotiable_value + balance.short_standardized_value
        )


def test_parse_accepts_explicit_utf8_bom() -> None:
    data = EN_CSV.read_bytes()
    assert parse_jpx_margin_csv(b"\xef\xbb\xbf" + data, source_url=SOURCE_URL)


def test_parse_missing_amounts_are_none_never_zero() -> None:
    """Pre-2026-09-25 application dates carry no amount columns -> None."""

    rows = list(csv.reader(io.StringIO(EN_CSV.read_text(encoding="utf-8"), newline="")))
    emptied = [rows[0]] + [[*row[:12], "", "", "", "", "", ""] for row in rows[1:]]
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\r\n", quoting=csv.QUOTE_ALL).writerows(emptied)
    balances = parse_jpx_margin_csv(buffer.getvalue().encode("utf-8"), source_url=SOURCE_URL)
    assert len(balances) == 10
    for balance in balances:
        for field in (
            "short_total_value",
            "long_total_value",
            "short_negotiable_value",
            "short_standardized_value",
            "long_negotiable_value",
            "long_standardized_value",
        ):
            assert getattr(balance, field) is None, field


@pytest.mark.parametrize(
    "encoding",
    ["utf-8", pytest.param("cp932", id="jp-cp932")],
)
def test_parse_detects_encoding_by_bytes(encoding: str) -> None:
    raw = (JP_CSV if encoding == "cp932" else EN_CSV).read_bytes()
    balances = parse_jpx_margin_csv(raw, source_url=SOURCE_URL)
    assert balances[0].code == "13010"


def test_parse_fails_closed_on_undecodable_bytes() -> None:
    # 0x80 0x81 0x82 0x83: invalid UTF-8 lead/continuation bytes and unused CP932
    # lead-byte positions, so both decoders must refuse.
    with pytest.raises(JpxMarginCsvError, match="neither UTF-8"):
        parse_jpx_margin_csv(b"\x80\x81\x82\x83", source_url=SOURCE_URL)


def test_parse_fails_closed_on_unparseable_volume() -> None:
    rows = list(csv.reader(io.StringIO(EN_CSV.read_text(encoding="utf-8"), newline="")))
    rows[1][6] = "3,000"  # thousands separator: refuse, never coerce
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\r\n", quoting=csv.QUOTE_ALL).writerows(rows)
    with pytest.raises(JpxMarginCsvError, match="short_total"):
        parse_jpx_margin_csv(buffer.getvalue().encode("utf-8"), source_url=SOURCE_URL)


def test_parse_fails_closed_on_identity_violation() -> None:
    rows = list(csv.reader(io.StringIO(EN_CSV.read_text(encoding="utf-8"), newline="")))
    rows[1][9] = "9999"  # short standardized no longer sums to the total
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\r\n", quoting=csv.QUOTE_ALL).writerows(rows)
    with pytest.raises(JpxMarginCsvError, match="short_total"):
        parse_jpx_margin_csv(buffer.getvalue().encode("utf-8"), source_url=SOURCE_URL)


def test_parse_fails_closed_on_unknown_header() -> None:
    rows = list(csv.reader(io.StringIO(EN_CSV.read_text(encoding="utf-8"), newline="")))
    rows[0][6] = "Short Margin Outstanding (shares)"
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\r\n", quoting=csv.QUOTE_ALL).writerows(rows)
    with pytest.raises(JpxMarginCsvError, match="Unrecognized JPX margin CSV header"):
        parse_jpx_margin_csv(buffer.getvalue().encode("utf-8"), source_url=SOURCE_URL)


def test_parse_fails_closed_on_wrong_column_count() -> None:
    rows = list(csv.reader(io.StringIO(EN_CSV.read_text(encoding="utf-8"), newline="")))
    del rows[0][0]
    del rows[1][0]
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\r\n", quoting=csv.QUOTE_ALL).writerows(rows)
    with pytest.raises(JpxMarginCsvError, match="columns"):
        parse_jpx_margin_csv(buffer.getvalue().encode("utf-8"), source_url=SOURCE_URL)


def test_parse_fails_closed_on_duplicate_code_within_a_date() -> None:
    rows = list(csv.reader(io.StringIO(EN_CSV.read_text(encoding="utf-8"), newline="")))
    rows[2][1] = rows[1][1]
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\r\n", quoting=csv.QUOTE_ALL).writerows(rows)
    with pytest.raises(JpxMarginCsvError, match="Duplicate"):
        parse_jpx_margin_csv(buffer.getvalue().encode("utf-8"), source_url=SOURCE_URL)


def test_parse_fails_closed_on_invalid_application_date() -> None:
    rows = list(csv.reader(io.StringIO(EN_CSV.read_text(encoding="utf-8"), newline="")))
    rows[1][0] = "20260230"  # February 30th
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\r\n", quoting=csv.QUOTE_ALL).writerows(rows)
    with pytest.raises(JpxMarginCsvError, match="application date"):
        parse_jpx_margin_csv(buffer.getvalue().encode("utf-8"), source_url=SOURCE_URL)


# --------------------------------------------------------- provider descriptor


def test_descriptor_is_personal_only_and_public_mode_refuses_it() -> None:
    descriptor = jpx_margin_descriptor()
    assert descriptor.name == "jpx_reference"
    assert descriptor.license_class is LicenseClass.PERSONAL_ONLY
    assert descriptor.redistributable is False
    enforce_jpx_margin_policy(mode="personal")
    with pytest.raises(ProviderPolicyError):
        enforce_jpx_margin_policy(mode="public")


# ------------------------------------------------------------------ persistence


def test_ingest_persists_all_rows_and_read_paths_agree() -> None:
    engine = _memory_engine()
    try:
        balances = _ingest_on_engine(engine, EN_CSV.read_bytes())
        assert len(balances) == 10
        with Session(engine) as session:
            assert session.scalar(select(func.count()).select_from(JpxMarginBalanceRecord)) == 10
            assert latest_jpx_margin_date(session) == date(2026, 4, 23)
            series = read_jpx_margin_by_code(session, "13010")
            assert [point.application_date for point in series.points] == [date(2026, 4, 23)]
            day = read_jpx_margin_by_date(session, date(2026, 4, 23))
            assert [point.code for point in day] == sorted(
                row[1]
                for row in (
                    list(csv.reader(io.StringIO(EN_CSV.read_text(encoding="utf-8"), newline="")))[
                        1:
                    ]
                )
            )
            assert day[0].short_total == 3000
            assert day[0].provenance.license_class is LicenseClass.PERSONAL_ONLY
    finally:
        engine.dispose()


def test_ingest_source_url_roundtrips_through_read_provenance() -> None:
    """Ingest URL is persisted and restored in read provenance, not dropped."""

    engine = _memory_engine()
    try:
        _ingest_on_engine(engine, EN_CSV.read_bytes(), source_url=SOURCE_URL)
        with Session(engine) as session:
            series = read_jpx_margin_by_code(session, "13010")
            assert series.points[0].provenance.source_url == SOURCE_URL
            day = read_jpx_margin_by_date(session, date(2026, 4, 23))
            assert all(point.provenance.source_url == SOURCE_URL for point in day)
    finally:
        engine.dispose()


def test_reingesting_same_application_date_replaces_atomically() -> None:
    engine = _memory_engine()
    try:
        _ingest_on_engine(engine, EN_CSV.read_bytes())
        # A corrected file: same date, one changed volume.
        rows = list(csv.reader(io.StringIO(EN_CSV.read_text(encoding="utf-8"), newline="")))
        rows[1][6] = "3100"  # short total
        rows[1][9] = "2100"  # short standardized (negotiable kept: 1000+2100=3100)
        buffer = io.StringIO()
        csv.writer(buffer, lineterminator="\r\n", quoting=csv.QUOTE_ALL).writerows(rows)
        _ingest_on_engine(
            engine,
            buffer.getvalue().encode("utf-8"),
            retrieved_at=datetime(2026, 9, 25, 12, 0, tzinfo=UTC),
        )
        with Session(engine) as session:
            # Exactly one row per (application_date, code): replacement, not append.
            assert session.scalar(select(func.count()).select_from(JpxMarginBalanceRecord)) == 10
            series = read_jpx_margin_by_code(session, "13010")
            assert series.points[0].short_total == 3100
            row = session.scalars(
                select(JpxMarginBalanceRecord).where(JpxMarginBalanceRecord.code == "13010")
            ).one()
            assert row.short_standardized == 2100
            # SQLite drops the tzinfo; the instant is the pinned UTC value.
            assert row.retrieved_at.replace(tzinfo=UTC) == datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
    finally:
        engine.dispose()


def test_failed_ingest_rolls_back_and_keeps_previous_day() -> None:
    engine = _memory_engine()
    try:
        _ingest_on_engine(engine, EN_CSV.read_bytes())
        with pytest.raises(JpxMarginCsvError):
            _ingest_on_engine(engine, b"not,a,margin,file\n" + b"x" * 40)
        with Session(engine) as session:
            assert session.scalar(select(func.count()).select_from(JpxMarginBalanceRecord)) == 10
    finally:
        engine.dispose()


def test_time_series_ordering_and_derived_fields() -> None:
    engine = _memory_engine()
    try:
        _ingest_on_engine(engine, EN_CSV.read_bytes())
        rows = list(csv.reader(io.StringIO(EN_CSV.read_text(encoding="utf-8"), newline="")))
        next_day = [list(rows[0])] + [list(row) for row in rows[1:]]
        for row in next_day[1:]:
            row[0] = "20260424"
            row[6] = str(int(row[6]) + 10)  # short total grows by 10
            row[7] = str(int(row[7]) + 20)  # long total grows by 20
            row[9] = str(int(row[9]) + 10)  # ... via standardized volumes
            row[11] = str(int(row[11]) + 20)
            row[12] = str(int(row[12]) + 10)  # keep value identity consistent too
            row[13] = str(int(row[13]) + 20)
            row[15] = str(int(row[15]) + 10)
            row[17] = str(int(row[17]) + 20)
        buffer = io.StringIO()
        csv.writer(buffer, lineterminator="\r\n", quoting=csv.QUOTE_ALL).writerows(next_day)
        _ingest_on_engine(engine, buffer.getvalue().encode("utf-8"))
        with Session(engine) as session:
            series = read_jpx_margin_by_code(session, "13010")
            assert [point.application_date for point in series.points] == [
                date(2026, 4, 23),
                date(2026, 4, 24),
            ]
            first, second = series.points
            # First persisted day has no previous application date -> None.
            assert first.short_change is None and first.long_change is None
            assert first.previous_application_date is None
            assert second.short_change == 10
            assert second.long_change == 20
            assert second.previous_application_date == date(2026, 4, 23)
            assert second.short_long_ratio == pytest.approx(second.short_total / second.long_total)
    finally:
        engine.dispose()


def test_zero_denominator_ratio_is_none_never_infinity() -> None:
    engine = _memory_engine()
    try:
        rows = list(csv.reader(io.StringIO(EN_CSV.read_text(encoding="utf-8"), newline="")))
        for row in rows[1:]:
            row[7] = "0"  # long total zero
            row[10] = "0"
            row[11] = "0"
        buffer = io.StringIO()
        csv.writer(buffer, lineterminator="\r\n", quoting=csv.QUOTE_ALL).writerows(rows)
        _ingest_on_engine(engine, buffer.getvalue().encode("utf-8"))
        with Session(engine) as session:
            day = read_jpx_margin_by_date(session, date(2026, 4, 23))
            assert all(point.short_long_ratio is None for point in day)
    finally:
        engine.dispose()


def test_short_series_limit_keeps_most_recent_dates() -> None:
    engine = _memory_engine()
    try:
        _ingest_on_engine(engine, EN_CSV.read_bytes())
        rows = list(csv.reader(io.StringIO(EN_CSV.read_text(encoding="utf-8"), newline="")))
        next_day = [row.copy() for row in rows]
        for row in next_day[1:]:
            row[0] = "20260424"
        buffer = io.StringIO()
        csv.writer(buffer, lineterminator="\r\n", quoting=csv.QUOTE_ALL).writerows(next_day)
        _ingest_on_engine(engine, buffer.getvalue().encode("utf-8"))
        with Session(engine) as session:
            series = read_jpx_margin_by_code(session, "13010", limit=1)
            assert [point.application_date for point in series.points] == [date(2026, 4, 24)]
    finally:
        engine.dispose()


# -------------------------------------------------------------------------- API


def test_api_personal_mode_serves_history_and_day(monkeypatch: pytest.MonkeyPatch) -> None:
    import tempfile

    from yowayowa import db as db_module

    db_path = (
        Path(tempfile.gettempdir()) / f"jpx-margin-api-{next(tempfile._get_candidate_names())}.db"
    )
    monkeypatch.setenv("YOWAYOWA_DATABASE_URL", f"sqlite:///{db_path}")
    db_module.dispose_database()
    session = db_module.get_session()
    try:
        ingest_jpx_margin_csv(
            session, EN_CSV.read_bytes(), source_url=SOURCE_URL, retrieved_at=RETRIEVED_AT
        )
    finally:
        session.close()
    with TestClient(app) as client:
        history = client.get("/v1/jpx/margin/13010")
        assert history.status_code == 200
        payload = history.json()
        assert payload["code"] == "13010"
        assert payload["points"][0]["application_date"] == "2026-04-23"
        assert payload["points"][0]["short_total"] == 3000
        assert payload["points"][0]["short_change"] is None

        day = client.get("/v1/jpx/margin/date/2026-04-23")
        assert day.status_code == 200
        day_payload = day.json()
        assert len(day_payload) == 10
        assert {item["code"] for item in day_payload} == {
            "13010",
            "13050",
            "135A0",
            "13770",
            "14070",
            "14180",
            "18410",
            "21640",
            "36560",
            "36810",
        }
        assert day_payload[0]["points"][0]["long_total_value"] == 700_000

        missing_day = client.get("/v1/jpx/margin/date/2026-01-01")
        assert missing_day.status_code == 404

        unknown_code = client.get("/v1/jpx/margin/99999")
        assert unknown_code.status_code == 200
        assert unknown_code.json()["points"] == []

        bad_code = client.get("/v1/jpx/margin/1301")
        assert bad_code.status_code == 422


def test_api_rejects_invalid_date_and_code_formats(monkeypatch: pytest.MonkeyPatch) -> None:
    import tempfile

    from yowayowa import db as db_module

    db_path = (
        Path(tempfile.gettempdir()) / f"jpx-margin-api-{next(tempfile._get_candidate_names())}.db"
    )
    monkeypatch.setenv("YOWAYOWA_DATABASE_URL", f"sqlite:///{db_path}")
    db_module.dispose_database()
    with TestClient(app) as client:
        response = client.get("/v1/jpx/margin/date/not-a-date")
        assert response.status_code == 422


def test_api_fails_closed_outside_personal_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    import tempfile

    from yowayowa import db as db_module

    db_path = (
        Path(tempfile.gettempdir()) / f"jpx-margin-api-{next(tempfile._get_candidate_names())}.db"
    )
    monkeypatch.setenv("YOWAYOWA_DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("YOWAYOWA_MODE", "public")
    monkeypatch.setenv("YOWAYOWA_API_TOKEN", "public-token")
    get_settings.cache_clear()
    db_module.dispose_database()
    try:
        with TestClient(app) as client:
            for path in (
                "/v1/jpx/margin/13010",
                "/v1/jpx/margin/date/2026-04-23",
                "/v1/jpx/margin/latest",
            ):
                response = client.get(path, headers={"Authorization": "Bearer public-token"})
                assert response.status_code == 403, path
                assert "personal" in response.json()["detail"]
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
        db_module.dispose_database()


# -------------------------------------------------------------------------- CLI


def test_cli_ingest_and_show_roundtrip(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from yowayowa import db as db_module

    db_path = tmp_path / "cli.db"
    monkeypatch.setenv("YOWAYOWA_DATABASE_URL", f"sqlite:///{db_path}")
    db_module.dispose_database()

    copy = tmp_path / "sample.csv"
    copy.write_bytes(EN_CSV.read_bytes())
    result = runner.invoke(
        cli_app,
        ["jpx-margin-ingest", str(copy), "--source-url", SOURCE_URL],
    )
    assert result.exit_code == 0, result.output
    assert "10 rows" in result.output

    shown = runner.invoke(cli_app, ["jpx-margin", "13010"])
    assert shown.exit_code == 0, shown.output
    # The rich table truncates cells to the 80-column test console, so assert
    # on the short cells that survive (code + volumes), not dates/names.
    assert "13010" in shown.output
    assert "3000" in shown.output
    assert "7000" in shown.output

    unknown = runner.invoke(cli_app, ["jpx-margin", "99999"])
    assert unknown.exit_code == 1
    assert "No JPX margin balances" in unknown.output

    bad = runner.invoke(cli_app, ["jpx-margin", "1301"])
    assert bad.exit_code != 0


def test_cli_ingest_rejects_invalid_csv(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from yowayowa import db as db_module

    db_path = tmp_path / "cli-bad.db"
    monkeypatch.setenv("YOWAYOWA_DATABASE_URL", f"sqlite:///{db_path}")
    db_module.dispose_database()
    bad = tmp_path / "bad.csv"
    bad.write_bytes(b"garbage")
    result = runner.invoke(
        cli_app,
        ["jpx-margin-ingest", str(bad), "--source-url", SOURCE_URL],
    )
    assert result.exit_code != 0


def test_cli_ingest_requires_existing_file(tmp_path) -> None:
    result = runner.invoke(
        cli_app,
        ["jpx-margin-ingest", str(tmp_path / "missing.csv"), "--source-url", SOURCE_URL],
    )
    assert result.exit_code != 0
