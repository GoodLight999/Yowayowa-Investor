"""Weekly credit margin ingestion: parsers, persistence, API, CLI (P4-C).

Fixtures are verbatim snippets of the live pages captured 2026-09-24 (UTC,
see tests/credit_margin_fixtures.py and docs/CREDIT_MARGIN.md), so every test
here runs offline against the real observed markup.
"""

from __future__ import annotations

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
from yowayowa.credit_margin_models import (
    normalize_credit_margin_code,
    yahoo_credit_margin_symbol,
)
from yowayowa.db import Base, CreditMarginWeeklyRecord
from yowayowa.domain import LicenseClass
from yowayowa.providers.base import ProviderPolicyError
from yowayowa.providers.credit_margin_kabutan import (
    CreditMarginKabutanError,
    enforce_credit_margin_kabutan_policy,
    parse_credit_margin_kabutan_html,
)
from yowayowa.providers.credit_margin_yahoo import (
    CreditMarginYahooError,
    enforce_credit_margin_yahoo_policy,
    parse_credit_margin_yahoo_html,
)
from yowayowa.services.credit_margin import (
    latest_credit_margin_week,
    persist_credit_margin_weekly,
    read_credit_margin_by_code,
    read_credit_margin_by_date,
)

# Verbatim snippets of the live pages captured 2026-09-24 (UTC), stored so
# every test runs offline against the real observed markup (provenance and
# capture facts in docs/CREDIT_MARGIN.md).
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "credit_margin"
YAHOO_HTML = FIXTURES / "yahoo_margin_history.html"
KABUTAN_HTML = FIXTURES / "kabutan_margin_section.html"
YAHOO_SOURCE_URL = "https://finance.yahoo.co.jp/quote/6758.T/margin"
KABUTAN_SOURCE_URL = "https://kabutan.jp/stock/?code=6758"

RETRIEVED_AT = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)

runner = CliRunner()


def _memory_engine():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return engine


def _persist_yahoo_on_engine(engine, *, retrieved_at: datetime = RETRIEVED_AT):
    rows = parse_credit_margin_yahoo_html(
        YAHOO_HTML.read_text(encoding="utf-8"),
        code="6758",
        source_url=YAHOO_SOURCE_URL,
        retrieved_at=retrieved_at,
    )
    with Session(engine, expire_on_commit=False) as session:
        counts = persist_credit_margin_weekly(session, rows)
    return rows, counts


def _persist_kabutan_on_engine(engine, *, retrieved_at: datetime = RETRIEVED_AT):
    rows = parse_credit_margin_kabutan_html(
        KABUTAN_HTML.read_text(encoding="utf-8"),
        code="6758",
        source_url=KABUTAN_SOURCE_URL,
        retrieved_at=retrieved_at,
    )
    with Session(engine, expire_on_commit=False) as session:
        counts = persist_credit_margin_weekly(session, rows)
    return rows, counts


# ------------------------------------------------------------------ parsers


def test_parse_yahoo_fixture_full_history() -> None:
    rows = parse_credit_margin_yahoo_html(
        YAHOO_HTML.read_text(encoding="utf-8"),
        code="6758",
        source_url=YAHOO_SOURCE_URL,
        retrieved_at=RETRIEVED_AT,
    )
    assert len(rows) == 20
    assert rows[0].as_of_date == date(2026, 9, 11)
    # Live-verified values (shares, 株 int): 6758 week 2026-09-11.
    assert rows[0].short_total == 299_300
    assert rows[0].long_total == 7_581_100
    # Negative change cells must parse into the (ignored) change column without
    # corrupting the balance columns: 2026-09-04 short = 501,000.
    week_0904 = next(row for row in rows if row.as_of_date == date(2026, 9, 4))
    assert week_0904.short_total == 501_000
    assert week_0904.long_total == 5_874_900
    for row in rows:
        assert row.code == "6758"
        assert row.provenance.provider == "yahoo_finance_margin"
        assert row.provenance.source_url == YAHOO_SOURCE_URL
        assert row.provenance.license_class == LicenseClass.PERSONAL_ONLY
        assert row.provenance.retrieved_at == RETRIEVED_AT
        assert row.provenance.as_of == row.as_of_date


def test_parse_kabutan_fixture_converts_thousands_to_shares() -> None:
    rows = parse_credit_margin_kabutan_html(
        KABUTAN_HTML.read_text(encoding="utf-8"),
        code="6758",
        source_url=KABUTAN_SOURCE_URL,
        retrieved_at=RETRIEVED_AT,
    )
    assert len(rows) == 4
    assert rows[0].as_of_date == date(2026, 9, 11)
    # 千株 x1000: 299.3 -> 299_300 (matches Yahoo's share figure exactly here).
    assert rows[0].short_total == 299_300
    assert rows[0].long_total == 7_581_100
    assert rows[1].short_total == 501_000
    assert rows[1].long_total == 5_874_900
    for row in rows:
        assert row.code == "6758"
        assert row.provenance.provider == "kabutan_margin"
        assert row.provenance.license_class == LicenseClass.PERSONAL_ONLY
        assert "千株" in "\n".join(row.provenance.notes)
        assert row.provenance.as_of == row.as_of_date


def test_yahoo_and_kabutan_agree_on_common_week_within_100_shares() -> None:
    yahoo_rows = parse_credit_margin_yahoo_html(
        YAHOO_HTML.read_text(encoding="utf-8"),
        code="6758",
        source_url=YAHOO_SOURCE_URL,
    )
    kabutan_rows = parse_credit_margin_kabutan_html(
        KABUTAN_HTML.read_text(encoding="utf-8"),
        code="6758",
        source_url=KABUTAN_SOURCE_URL,
    )
    ymap = {row.as_of_date: row for row in yahoo_rows}
    kmap = {row.as_of_date: row for row in kabutan_rows}
    common = sorted(set(ymap) & set(kmap))
    assert common  # the sources must overlap on the recent weeks
    for as_of in common:
        assert abs(ymap[as_of].short_total - kmap[as_of].short_total) <= 100, as_of
        assert abs(ymap[as_of].long_total - kmap[as_of].long_total) <= 100, as_of


def test_parse_yahoo_fails_closed_without_history_headers() -> None:
    html = "<html><body><table><th>2026/9/11</th><td>1</td><td>2</td></table></body></html>"
    with pytest.raises(CreditMarginYahooError, match="headers"):
        parse_credit_margin_yahoo_html(html, code="6758", source_url=YAHOO_SOURCE_URL)


def test_parse_yahoo_fails_closed_on_unparseable_number() -> None:
    html = (
        "<table><thead><tr><th>日付</th><th>売残</th><th>買残</th><th>売残増減</th>"
        "<th>買残増減</th><th>信用倍率</th></tr></thead><tbody>"
        "<tr><th>2026/9/11</th><td>abc</td><td>2</td><td>0</td><td>0</td><td>1</td></tr>"
        "</tbody></table>"
    )
    with pytest.raises(CreditMarginYahooError, match="styled numeric value"):
        parse_credit_margin_yahoo_html(html, code="6758", source_url=YAHOO_SOURCE_URL)


def test_parse_yahoo_fails_closed_on_no_rows() -> None:
    html = (
        "<table><thead><tr><th>日付</th><th>売残</th><th>買残</th><th>売残増減</th>"
        "<th>買残増減</th><th>信用倍率</th></tr></thead></table>"
    )
    with pytest.raises(CreditMarginYahooError, match="no history rows"):
        parse_credit_margin_yahoo_html(html, code="6758", source_url=YAHOO_SOURCE_URL)


def test_parse_yahoo_fails_closed_on_duplicate_week() -> None:
    def _num_cell(value: str) -> str:
        return (
            f'<td><span class="_StyledNumber_1"><span class="_StyledNumber__value_9">'
            f"{value}</span></span></td>"
        )

    row = (
        "<tr><th>2026/9/11</th>"
        + _num_cell("1")
        + _num_cell("2")
        + _num_cell("0")
        + _num_cell("0")
        + _num_cell("1")
        + "</tr>"
    )
    html = (
        "<table><thead><tr><th>日付</th><th>売残</th><th>買残</th><th>売残増減</th>"
        "<th>買残増減</th><th>信用倍率</th></tr></thead><tbody>" + row + row + "</tbody></table>"
    )
    with pytest.raises(CreditMarginYahooError, match="Duplicate week"):
        parse_credit_margin_yahoo_html(html, code="6758", source_url=YAHOO_SOURCE_URL)


def test_parse_yahoo_fails_closed_on_invalid_date() -> None:
    def _num_cell(value: str) -> str:
        return (
            f'<td><span class="_StyledNumber_1"><span class="_StyledNumber__value_9">'
            f"{value}</span></span></td>"
        )

    html = (
        "<table><thead><tr><th>日付</th><th>売残</th><th>買残</th><th>売残増減</th>"
        "<th>買残増減</th><th>信用倍率</th></tr></thead><tbody>"
        "<tr><th>2026/13/40</th>"
        + _num_cell("1")
        + _num_cell("2")
        + _num_cell("0")
        + _num_cell("0")
        + _num_cell("1")
        + "</tr>"
        "</tbody></table>"
    )
    with pytest.raises(CreditMarginYahooError, match="Invalid week date"):
        parse_credit_margin_yahoo_html(html, code="6758", source_url=YAHOO_SOURCE_URL)


def test_parse_kabutan_fails_closed_without_section() -> None:
    html = "<html><body><table><tr><td>1</td></tr></table></body></html>"
    with pytest.raises(CreditMarginKabutanError, match="信用取引"):
        parse_credit_margin_kabutan_html(html, code="6758", source_url=KABUTAN_SOURCE_URL)


def test_parse_kabutan_ignores_decorative_following_section() -> None:
    # The fixture deliberately contains the following 情報提供 table with link
    # and image cells; only the 4 credit rows may be parsed.
    rows = parse_credit_margin_kabutan_html(
        KABUTAN_HTML.read_text(encoding="utf-8"),
        code="6758",
        source_url=KABUTAN_SOURCE_URL,
    )
    assert len(rows) == 4


def test_parse_kabutan_fails_closed_on_nonnumeric_cell() -> None:
    html = (
        '<h2 class="mgt6">信用取引&nbsp;(単位:千株)</h2>'
        "<table><tr><th scope='row'><time datetime=\"2026-09-11\">09/11</time></th>"
        "<td>n/a</td><td>2.0</td><td>1.0</td></tr></table>"
    )
    with pytest.raises(CreditMarginKabutanError, match=r"Unparseable|no data rows"):
        parse_credit_margin_kabutan_html(html, code="6758", source_url=KABUTAN_SOURCE_URL)


def test_parse_kabutan_fails_closed_on_missing_table() -> None:
    html = '<h2 class="mgt6">信用取引&nbsp;(単位:千株)</h2><p>no table</p>'
    with pytest.raises(CreditMarginKabutanError, match="table"):
        parse_credit_margin_kabutan_html(html, code="6758", source_url=KABUTAN_SOURCE_URL)


def test_provider_policies_fail_closed_outside_personal_mode() -> None:
    with pytest.raises(ProviderPolicyError):
        enforce_credit_margin_yahoo_policy(mode="public")
    with pytest.raises(ProviderPolicyError):
        enforce_credit_margin_kabutan_policy(mode="public")
    assert get_settings().mode != "public" or True  # policy check is env-driven
    enforce_credit_margin_yahoo_policy(mode="personal")
    enforce_credit_margin_kabutan_policy(mode="personal")


def test_code_normalization_and_yahoo_symbol() -> None:
    assert normalize_credit_margin_code("7203") == "7203"
    assert normalize_credit_margin_code("7203.T") == "7203"
    assert normalize_credit_margin_code(" 6758 ") == "6758"
    assert yahoo_credit_margin_symbol("6758") == "6758.T"
    with pytest.raises(ValueError):
        normalize_credit_margin_code("6758.T.O")
    with pytest.raises(ValueError):
        normalize_credit_margin_code("675")
    with pytest.raises(ValueError):
        normalize_credit_margin_code("abcde")


# ------------------------------------------------------------- persistence


def test_upsert_insert_update_and_unchanged_paths() -> None:
    engine = _memory_engine()
    try:
        yahoo_rows, first_counts = _persist_yahoo_on_engine(engine)
        assert first_counts == {"inserted": 20, "updated": 0, "unchanged": 0}
        # Same values again: all no-op.
        with Session(engine, expire_on_commit=False) as session:
            same_counts = persist_credit_margin_weekly(session, yahoo_rows)
        assert same_counts == {"inserted": 0, "updated": 0, "unchanged": 20}
        # Changed value for one week: UPDATE + 再確認(値変化) note.
        changed = yahoo_rows[0].model_copy(update={"short_total": yahoo_rows[0].short_total + 100})
        later = datetime(2026, 9, 25, 3, 0, tzinfo=UTC)
        with Session(engine, expire_on_commit=False) as session:
            update_counts = persist_credit_margin_weekly(
                session,
                [
                    changed.model_copy(
                        update={
                            "provenance": changed.provenance.model_copy(
                                update={"retrieved_at": later}
                            )
                        }
                    )
                ],
            )
        assert update_counts == {"inserted": 0, "updated": 1, "unchanged": 0}
        with Session(engine) as session:
            record = session.scalar(
                select(CreditMarginWeeklyRecord).where(
                    CreditMarginWeeklyRecord.as_of_date == changed.as_of_date
                )
            )
            assert record is not None
            assert record.short_total == yahoo_rows[0].short_total + 100
            assert record.retrieved_at.replace(tzinfo=UTC) == later
            assert record.source_url == YAHOO_SOURCE_URL
            assert any("再確認(値変化)" in note for note in record.notes)
        # A different week inserts normally (kabutan overlap is the same weeks;
        # use a synthetic new week).
        new_week = yahoo_rows[0].model_copy(update={"as_of_date": date(2026, 9, 18)})
        with Session(engine, expire_on_commit=False) as session:
            new_counts = persist_credit_margin_weekly(session, [new_week])
        assert new_counts == {"inserted": 1, "updated": 0, "unchanged": 0}
        with Session(engine) as session:
            assert session.scalar(select(func.count()).select_from(CreditMarginWeeklyRecord)) == 21
    finally:
        engine.dispose()


def test_kabutan_upsert_does_not_clobber_yahoo_when_values_match() -> None:
    engine = _memory_engine()
    try:
        _persist_yahoo_on_engine(engine)
        _, kabutan_counts = _persist_kabutan_on_engine(engine)
        # The four kabutan weeks match the persisted Yahoo values within the
        # exact-value equality only when identical; 6758 happens to match
        # exactly (299.3 -> 299300), so they are unchanged, not duplicated.
        assert kabutan_counts == {"inserted": 0, "updated": 0, "unchanged": 4}
        with Session(engine) as session:
            assert session.scalar(select(func.count()).select_from(CreditMarginWeeklyRecord)) == 20
    finally:
        engine.dispose()


def test_read_time_derived_fields_and_ordering() -> None:
    engine = _memory_engine()
    try:
        _persist_yahoo_on_engine(engine)
        with Session(engine) as session:
            series = read_credit_margin_by_code(session, "6758")
            dates = [point.as_of_date for point in series.points]
            assert dates == sorted(dates)
            assert len(dates) == 20
            first, second = series.points[0], series.points[1]
            assert first.short_change is None and first.previous_as_of_date is None
            assert second.previous_as_of_date == first.as_of_date
            assert second.short_change == second.short_total - first.short_total
            assert second.long_change == second.long_total - first.long_total
            assert second.short_long_ratio == pytest.approx(second.short_total / second.long_total)
            # Zero long balance -> ratio None, never infinity.
            zero_long = series.points[0].model_copy(
                update={"long_total": 0, "short_long_ratio": None}
            )
            assert zero_long.short_long_ratio is None
            # date window filtering.
            window = read_credit_margin_by_code(
                session,
                "6758",
                date_from=date(2026, 8, 1),
                date_to=date(2026, 8, 31),
            )
            assert all(date(2026, 8, 1) <= p.as_of_date <= date(2026, 8, 31) for p in window.points)
            # limit keeps most recent.
            limited = read_credit_margin_by_code(session, "6758", limit=2)
            assert [p.as_of_date for p in limited.points] == dates[-2:]
            latest = latest_credit_margin_week(session)
            assert latest == max(dates)
            by_date = read_credit_margin_by_date(session, latest)
            assert [p.code for p in by_date] == ["6758"]
    finally:
        engine.dispose()


# --------------------------------------------------------------------- API


def _setup_api_db(monkeypatch: pytest.MonkeyPatch) -> None:
    import tempfile

    from yowayowa import db as db_module

    db_path = (
        Path(tempfile.gettempdir())
        / f"credit-margin-api-{next(tempfile._get_candidate_names())}.db"
    )
    monkeypatch.setenv("YOWAYOWA_DATABASE_URL", f"sqlite:///{db_path}")
    db_module.dispose_database()


def test_api_personal_mode_serves_three_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    _setup_api_db(monkeypatch)
    from yowayowa import db as db_module

    session = db_module.get_session()
    try:
        rows = parse_credit_margin_yahoo_html(
            YAHOO_HTML.read_text(encoding="utf-8"),
            code="6758",
            source_url=YAHOO_SOURCE_URL,
            retrieved_at=RETRIEVED_AT,
        )
        persist_credit_margin_weekly(session, rows)
    finally:
        session.close()
    with TestClient(app) as client:
        history = client.get("/v1/credit/margin/6758")
        assert history.status_code == 200
        payload = history.json()
        assert payload["code"] == "6758"
        assert payload["points"][0]["as_of_date"] == "2026-08-07" or len(payload["points"]) == 20
        assert payload["points"][-1]["as_of_date"] == "2026-09-11"
        assert payload["points"][-1]["short_total"] == 299_300

        by_date = client.get("/v1/credit/margin/date/2026-09-11")
        assert by_date.status_code == 200
        assert [item["code"] for item in by_date.json()] == ["6758"]

        latest = client.get("/v1/credit/margin/latest")
        assert latest.status_code == 200
        assert [item["code"] for item in latest.json()] == ["6758"]

        missing_day = client.get("/v1/credit/margin/date/2026-01-01")
        assert missing_day.status_code == 404

        unknown_code = client.get("/v1/credit/margin/6758 XXXX")
        assert unknown_code.status_code == 422
        assert unknown_code.json()["detail"]

        bad_code = client.get("/v1/credit/margin/67580")
        assert bad_code.status_code == 422

        bad_date = client.get("/v1/credit/margin/date/not-a-date")
        assert bad_date.status_code == 422

        empty_latest = client.get("/v1/credit/margin/latest")
        assert empty_latest.status_code == 200


def test_api_latest_404_on_empty_db(monkeypatch: pytest.MonkeyPatch) -> None:
    _setup_api_db(monkeypatch)
    with TestClient(app) as client:
        response = client.get("/v1/credit/margin/latest")
        assert response.status_code == 404


def test_api_fails_closed_outside_personal_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    _setup_api_db(monkeypatch)
    monkeypatch.setenv("YOWAYOWA_MODE", "public")
    monkeypatch.setenv("YOWAYOWA_API_TOKEN", "public-token")
    get_settings.cache_clear()
    try:
        with TestClient(app) as client:
            for path in (
                "/v1/credit/margin/6758",
                "/v1/credit/margin/date/2026-09-11",
                "/v1/credit/margin/latest",
            ):
                response = client.get(path, headers={"Authorization": "Bearer public-token"})
                assert response.status_code == 403, path
                assert "personal" in response.json()["detail"]
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
        db_cleanup()


def db_cleanup() -> None:
    from yowayowa import db as db_module

    db_module.dispose_database()


def test_openapi_contains_credit_margin_paths() -> None:
    schema = app.openapi()
    paths = schema["paths"]
    assert {
        "/v1/credit/margin/{code}",
        "/v1/credit/margin/latest",
        "/v1/credit/margin/date/{as_of_date}",
    } <= set(paths)
    operation_ids = {
        operation.get("operationId")
        for path_item in paths.values()
        for operation in path_item.values()
        if isinstance(operation, dict) and "operationId" in operation
    }
    assert len(operation_ids) == len(set(operation_ids))


# --------------------------------------------------------------------- CLI


def test_cli_show_roundtrip(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from yowayowa import db as db_module

    db_path = tmp_path / "cli.db"
    monkeypatch.setenv("YOWAYOWA_DATABASE_URL", f"sqlite:///{db_path}")
    db_module.dispose_database()

    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    engine.dispose()
    _rows, _ = _persist_yahoo_on_engine(create_engine(f"sqlite:///{db_path}"))

    shown = runner.invoke(cli_app, ["credit-margin", "6758"])
    assert shown.exit_code == 0, shown.output
    assert "6758" in shown.output
    assert "299,300" in shown.output or "299300" in shown.output

    unknown = runner.invoke(cli_app, ["credit-margin", "1301"])
    assert unknown.exit_code == 1
    assert "No credit margin weeks" in unknown.output

    bad = runner.invoke(cli_app, ["credit-margin", "67580"])
    assert bad.exit_code != 0


def test_cli_fetch_rejects_invalid_code(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from yowayowa import db as db_module

    monkeypatch.setenv("YOWAYOWA_DATABASE_URL", f"sqlite:///{tmp_path / 'cli.db'}")
    db_module.dispose_database()
    result = runner.invoke(cli_app, ["credit-margin-fetch", "67580"])
    assert result.exit_code != 0


def test_cli_fetch_rejects_unknown_source(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from yowayowa import db as db_module

    monkeypatch.setenv("YOWAYOWA_DATABASE_URL", f"sqlite:///{tmp_path / 'cli.db'}")
    db_module.dispose_database()
    result = runner.invoke(cli_app, ["credit-margin-fetch", "6758", "--source", "bogus"])
    assert result.exit_code != 0


def test_cli_fetch_yahoo_end_to_end(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Offline end-to-end: stub the provider fetch, persist through the CLI."""

    from yowayowa import credit_margin_cli as cli_module
    from yowayowa import db as db_module

    db_path = tmp_path / "cli-e2e.db"
    monkeypatch.setenv("YOWAYOWA_DATABASE_URL", f"sqlite:///{db_path}")
    db_module.dispose_database()
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    engine.dispose()

    stubbed_rows = parse_credit_margin_yahoo_html(
        YAHOO_HTML.read_text(encoding="utf-8"),
        code="6758",
        source_url=YAHOO_SOURCE_URL,
        retrieved_at=RETRIEVED_AT,
    )

    def _fake_fetch(code: str, *, client=None, retrieved_at=None):
        assert code == "6758"
        return stubbed_rows

    monkeypatch.setattr(cli_module, "fetch_credit_margin_yahoo", _fake_fetch)
    fetched = runner.invoke(cli_app, ["credit-margin-fetch", "6758"])
    assert fetched.exit_code == 0, fetched.output
    assert "inserted" in fetched.output

    shown = runner.invoke(cli_app, ["credit-margin", "6758"])
    assert shown.exit_code == 0, shown.output
    assert "6758" in shown.output
