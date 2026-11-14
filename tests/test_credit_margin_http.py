"""Batch 2 gap coverage for the credit-margin providers.

Covers the remaining branches measured missing: URL construction, the
thousands/integer parse failure paths, HTML parse drift (missing section,
unterminated table, no data rows, duplicate weeks, bad cells), and the full
``fetch_*`` HTTP paths (success, non-200, transport error, owned-client
lifecycle) via pytest-httpx.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import httpx
import pytest

from yowayowa.providers.credit_margin_kabutan import (
    CREDIT_MARGIN_KABUTAN_URL_TEMPLATE,
    CreditMarginKabutanError,
    credit_margin_kabutan_url,
    fetch_credit_margin_kabutan,
    parse_credit_margin_kabutan_html,
)
from yowayowa.providers.credit_margin_yahoo import (
    CREDIT_MARGIN_YAHOO_URL_TEMPLATE,
    CreditMarginYahooError,
    credit_margin_yahoo_url,
    fetch_credit_margin_yahoo,
    parse_credit_margin_yahoo_html,
)

RETRIEVED_AT = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)

# Live-observed fixture markup (mirrors tests/fixtures/credit_margin snippets).
KABUTAN_SECTION_HEADER = "\u4fe1\u7528\u53d6\u5f15"  # shin-tori-hiki section title
KABUTAN_HTML = (
    "<html><body>"
    "<h2 class='mgt6'>" + KABUTAN_SECTION_HEADER + "&nbsp;(単位:千株)</h2>"
    "<table>"
    "<tr><th scope='row'><time datetime=\"2026-09-11\">09/11</time></th>"
    "<td>299.3</td><td>7,581.1</td><td>25.33</td></tr>"
    "<tr><th scope='row'><time datetime=\"2026-09-04\">09/04</time></th>"
    "<td>501.0</td><td>5,874.9</td><td>11.73</td></tr>"
    "</table>"
    "</body></html>"
)

YAHOO_HEADER_CELLS = (
    ">\u65e5\u4ed8</th>",  # date
    ">\u58f2\u6b8b</th>",  # short balance
    ">\u8cb7\u6b8b</th>",  # long balance
    ">\u58f2\u6b8b\u5897\u6e1b</th>",  # short change
    ">\u8cb7\u6b8b\u5897\u6e1b</th>",  # long change
    ">\u4fe1\u7528\u500d\u7387</th>",  # credit ratio
)

YAHOO_ROW = (
    "<tr><th>2026/9/11</th>"
    '<td><span class="_StyledNumber__value_1arhg_9">299,300</span></td>'
    '<td><span class="_StyledNumber__value_1arhg_9">7,581,100</span></td>'
    '<td><span class="_StyledNumber__value_1arhg_9">-201,700</span></td>'
    '<td><span class="_StyledNumber__value_1arhg_9">1,706,200</span></td>'
    '<td><span class="_StyledNumber__value_1arhg_9">25.33</span></td>'
    "</tr>"
)

YAHOO_HTML = (
    "<html><body><table>"
    "<thead><tr>" + "".join(YAHOO_HEADER_CELLS) + "</tr></thead>"
    "<tbody>" + YAHOO_ROW + "</tbody>"
    "</table></body></html>"
)


# ------------------------------------------------------------------ URL builders


def test_kabutan_url_quotes_normalized_code() -> None:
    assert credit_margin_kabutan_url("6758") == "https://kabutan.jp/stock/?code=6758"
    assert credit_margin_kabutan_url("6758.T") == "https://kabutan.jp/stock/?code=6758"
    assert CREDIT_MARGIN_KABUTAN_URL_TEMPLATE.endswith("{code}")


def test_yahoo_url_appends_t_suffix() -> None:
    assert credit_margin_yahoo_url("6758") == "https://finance.yahoo.co.jp/quote/6758.T/margin"
    assert CREDIT_MARGIN_YAHOO_URL_TEMPLATE.endswith("{symbol}/margin")


# ------------------------------------------------------- kabutan parse branches


def test_kabutan_parse_converts_thousands_and_orders_newest_first() -> None:
    rows = parse_credit_margin_kabutan_html(
        KABUTAN_HTML,
        code="6758",
        source_url="https://kabutan.jp/stock/?code=6758",
        retrieved_at=RETRIEVED_AT,
    )

    assert [row.as_of_date for row in rows] == [date(2026, 9, 11), date(2026, 9, 4)]
    assert rows[0].short_total == 299_300
    assert rows[0].long_total == 7_581_100
    assert rows[1].short_total == 501_000


def test_kabutan_parse_thousands_rejects_unparseable_cell() -> None:
    # "..." matches the row regex's numeric-cell class but not Decimal: the
    # parse must refuse to coerce it rather than guess.
    html = KABUTAN_HTML.replace("<td>299.3</td>", "<td>...</td>")

    with pytest.raises(
        CreditMarginKabutanError,
        match=r"Unparseable short_total value '\.\.\.' for 6758",
    ):
        parse_credit_margin_kabutan_html(
            html, code="6758", source_url="https://kabutan.jp/stock/?code=6758"
        )


def test_kabutan_parse_requires_section_heading() -> None:
    with pytest.raises(CreditMarginKabutanError, match="no " + KABUTAN_SECTION_HEADER):
        parse_credit_margin_kabutan_html(
            "<html>no section</html>", code="6758", source_url="https://kabutan.jp"
        )


def test_kabutan_parse_requires_table_after_section() -> None:
    html = "<h2>信用取引</h2><p>nothing follows</p>"

    with pytest.raises(CreditMarginKabutanError, match="not followed by a table"):
        parse_credit_margin_kabutan_html(html, code="6758", source_url="https://kabutan.jp")


def test_kabutan_parse_requires_terminated_table() -> None:
    head = "<h2>\u4fe1\u7528\u53d6\u5f15</h2><table><tr><th scope='row'>"
    tail = '<time datetime="2026-09-11">09/11</time></th><td>1</td><td>2</td><td>3</td></tr>'
    html = head + tail

    with pytest.raises(CreditMarginKabutanError, match="unterminated"):
        parse_credit_margin_kabutan_html(html, code="6758", source_url="https://kabutan.jp")


def test_kabutan_parse_rejects_invalid_week_date() -> None:
    html = (
        "<h2>\u4fe1\u7528\u53d6\u5f15</h2>"
        "<table>"
        "<tr><th scope='row'><time datetime=\"2026-13-40\">??</time></th>"
        "<td>1.0</td><td>2.0</td><td>3.0</td></tr>"
        "</table>"
    )

    with pytest.raises(CreditMarginKabutanError, match="Invalid week date 2026-13-40"):
        parse_credit_margin_kabutan_html(html, code="6758", source_url="https://kabutan.jp")


def test_kabutan_parse_rejects_duplicate_weeks() -> None:
    duplicate_rows = (
        "<tr><th scope='row'><time datetime=\"2026-09-11\">09/11</time></th>"
        "<td>1.0</td><td>2.0</td><td>3.0</td></tr>"
        "<tr><th scope='row'><time datetime=\"2026-09-11\">09/11</time></th>"
        "<td>4.0</td><td>5.0</td><td>6.0</td></tr>"
    )
    html = "<h2>\u4fe1\u7528\u53d6\u5f15</h2><table>" + duplicate_rows + "</table>"

    with pytest.raises(CreditMarginKabutanError, match="Duplicate week"):
        parse_credit_margin_kabutan_html(html, code="6758", source_url="https://kabutan.jp")


def test_kabutan_parse_rejects_table_without_data_rows() -> None:
    html = "<h2>信用取引</h2><table><tr><th>only headers</th></tr></table>"

    with pytest.raises(CreditMarginKabutanError, match="contains no data rows"):
        parse_credit_margin_kabutan_html(html, code="6758", source_url="https://kabutan.jp")


# -------------------------------------------------------- yahoo parse branches


def test_yahoo_parse_reads_styled_number_cells() -> None:
    rows = parse_credit_margin_yahoo_html(
        YAHOO_HTML,
        code="6758",
        source_url="https://finance.yahoo.co.jp/quote/6758.T/margin",
        retrieved_at=RETRIEVED_AT,
    )

    assert len(rows) == 1
    assert rows[0].as_of_date == date(2026, 9, 11)
    assert rows[0].short_total == 299_300
    assert rows[0].long_total == 7_581_100
    assert rows[0].provenance.source_url == "https://finance.yahoo.co.jp/quote/6758.T/margin"


def test_yahoo_parse_requires_expected_headers() -> None:
    missing_ratio = YAHOO_HTML.replace(YAHOO_HEADER_CELLS[-1], "")

    with pytest.raises(CreditMarginYahooError, match="missing: "):
        parse_credit_margin_yahoo_html(missing_ratio, code="6758", source_url="https://example.com")


def test_yahoo_parse_rejects_cell_without_single_styled_value() -> None:
    two_value_cell = (
        '<td><span class="_StyledNumber__value_1arhg_9">299,300</span>'
        '<span class="_StyledNumber__value_1arhg_9">999</span></td>'
    )
    broken_row = YAHOO_ROW.replace(
        '<td><span class="_StyledNumber__value_1arhg_9">299,300</span></td>',
        two_value_cell,
    )
    html = (
        "<html><body><table>"
        "<thead><tr>" + "".join(YAHOO_HEADER_CELLS) + "</tr></thead>"
        "<tbody>" + broken_row + "</tbody>"
        "</table></body></html>"
    )

    with pytest.raises(CreditMarginYahooError, match="exactly one styled numeric value"):
        parse_credit_margin_yahoo_html(html, code="6758", source_url="https://example.com")


def test_yahoo_parse_rejects_non_integer_cell_value() -> None:
    broken_row = YAHOO_ROW.replace(">299,300<", ">299.300<")
    html = (
        "<html><body><table>"
        "<thead><tr>" + "".join(YAHOO_HEADER_CELLS) + "</tr></thead>"
        "<tbody>" + broken_row + "</tbody>"
        "</table></body></html>"
    )

    with pytest.raises(CreditMarginYahooError, match="not an integer amount"):
        parse_credit_margin_yahoo_html(html, code="6758", source_url="https://example.com")


def test_yahoo_parse_rejects_unexpected_column_count() -> None:
    row_with_six_cells = YAHOO_ROW.replace(
        "</tr>",
        '<td><span class="_StyledNumber__value_1arhg_9">1.0</span></td></tr>',
    )
    html = (
        "<html><body><table>"
        "<thead><tr>" + "".join(YAHOO_HEADER_CELLS) + "</tr></thead>"
        "<tbody>" + row_with_six_cells + "</tbody>"
        "</table></body></html>"
    )

    with pytest.raises(CreditMarginYahooError, match="6 columns, expected 5"):
        parse_credit_margin_yahoo_html(html, code="6758", source_url="https://example.com")


def test_yahoo_parse_rejects_invalid_and_duplicate_week_dates() -> None:
    bad_date_row = (
        "<tr><th>2026/13/45</th>"
        '<td><span class="_StyledNumber__value_1arhg_9">1</span></td>'
        '<td><span class="_StyledNumber__value_1arhg_9">2</span></td>'
        '<td><span class="_StyledNumber__value_1arhg_9">3</span></td>'
        '<td><span class="_StyledNumber__value_1arhg_9">4</span></td>'
        '<td><span class="_StyledNumber__value_1arhg_9">5</span></td>'
        "</tr>"
    )
    html = (
        "<html><body><table>"
        "<thead><tr>" + "".join(YAHOO_HEADER_CELLS) + "</tr></thead>"
        "<tbody>" + bad_date_row + "</tbody>"
        "</table></body></html>"
    )
    with pytest.raises(CreditMarginYahooError, match="Invalid week date 2026/13/45"):
        parse_credit_margin_yahoo_html(html, code="6758", source_url="https://example.com")

    duplicate_row = YAHOO_ROW + YAHOO_ROW
    html_dup = (
        "<html><body><table>"
        "<thead><tr>" + "".join(YAHOO_HEADER_CELLS) + "</tr></thead>"
        "<tbody>" + duplicate_row + "</tbody>"
        "</table></body></html>"
    )
    with pytest.raises(CreditMarginYahooError, match="Duplicate week"):
        parse_credit_margin_yahoo_html(html_dup, code="6758", source_url="https://example.com")


def test_yahoo_parse_rejects_page_without_any_rows() -> None:
    html = (
        "<html><body><table>"
        "<thead><tr>" + "".join(YAHOO_HEADER_CELLS) + "</tr></thead>"
        "<tbody></tbody>"
        "</table></body></html>"
    )

    with pytest.raises(CreditMarginYahooError, match="contains no history rows"):
        parse_credit_margin_yahoo_html(html, code="6758", source_url="https://example.com")


# ------------------------------------------------------------------ HTTP paths


def test_kabutan_fetch_parses_recent_page(httpx_mock) -> None:  # type: ignore[no-untyped-def]
    httpx_mock.add_response(
        url="https://kabutan.jp/stock/?code=6758",
        text=KABUTAN_HTML,
    )

    rows = fetch_credit_margin_kabutan("6758", retrieved_at=RETRIEVED_AT)

    assert [row.as_of_date for row in rows] == [date(2026, 9, 11), date(2026, 9, 4)]
    request = httpx_mock.get_requests()[0]
    assert str(request.url) == "https://kabutan.jp/stock/?code=6758"
    assert request.headers["User-Agent"].startswith("Mozilla/5.0")


def test_kabutan_fetch_rejects_non_200(httpx_mock) -> None:  # type: ignore[no-untyped-def]
    httpx_mock.add_response(url="https://kabutan.jp/stock/?code=6758", status_code=404)

    with pytest.raises(CreditMarginKabutanError, match="returned HTTP 404"):
        fetch_credit_margin_kabutan("6758")


def test_kabutan_fetch_wraps_transport_errors(httpx_mock) -> None:  # type: ignore[no-untyped-def]
    import httpx

    httpx_mock.add_exception(httpx.ConnectError("boom"))

    with pytest.raises(CreditMarginKabutanError, match="fetch failed for 6758"):
        fetch_credit_margin_kabutan("6758")


def test_kabutan_fetch_propagates_parse_failures(httpx_mock) -> None:  # type: ignore[no-untyped-def]
    httpx_mock.add_response(
        url="https://kabutan.jp/stock/?code=6758",
        text="<html>no section</html>",
    )

    with pytest.raises(CreditMarginKabutanError, match="has no " + KABUTAN_SECTION_HEADER):
        fetch_credit_margin_kabutan("6758")


def test_kabutan_fetch_reuses_injected_client(httpx_mock) -> None:  # type: ignore[no-untyped-def]
    httpx_mock.add_response(url="https://kabutan.jp/stock/?code=6758", text=KABUTAN_HTML)

    with httpx.Client() as client:
        rows = fetch_credit_margin_kabutan("6758", client=client)

    assert rows[0].short_total == 299_300


def test_yahoo_fetch_parses_recent_page(httpx_mock) -> None:  # type: ignore[no-untyped-def]
    httpx_mock.add_response(
        url="https://finance.yahoo.co.jp/quote/6758.T/margin",
        text=YAHOO_HTML,
    )

    rows = fetch_credit_margin_yahoo("6758", retrieved_at=RETRIEVED_AT)

    assert rows[0].short_total == 299_300
    assert rows[0].long_total == 7_581_100
    request = httpx_mock.get_requests()[0]
    assert str(request.url) == "https://finance.yahoo.co.jp/quote/6758.T/margin"
    assert request.headers["User-Agent"].startswith("Mozilla/5.0")


def test_yahoo_fetch_rejects_non_200(httpx_mock) -> None:  # type: ignore[no-untyped-def]
    httpx_mock.add_response(
        url="https://finance.yahoo.co.jp/quote/6758.T/margin",
        status_code=503,
    )

    with pytest.raises(CreditMarginYahooError, match="returned HTTP 503"):
        fetch_credit_margin_yahoo("6758")


def test_yahoo_fetch_wraps_transport_errors(httpx_mock) -> None:  # type: ignore[no-untyped-def]
    import httpx

    httpx_mock.add_exception(httpx.ConnectTimeout("slow"))

    with pytest.raises(CreditMarginYahooError, match="fetch failed for 6758"):
        fetch_credit_margin_yahoo("6758")


def test_yahoo_fetch_propagates_parse_failures(httpx_mock) -> None:  # type: ignore[no-untyped-def]
    httpx_mock.add_response(
        url="https://finance.yahoo.co.jp/quote/6758.T/margin",
        text="<html>empty page</html>",
    )

    with pytest.raises(CreditMarginYahooError, match="format drift"):
        fetch_credit_margin_yahoo("6758")


def test_yahoo_fetch_reuses_injected_client(httpx_mock) -> None:  # type: ignore[no-untyped-def]
    httpx_mock.add_response(
        url="https://finance.yahoo.co.jp/quote/6758.T/margin",
        text=YAHOO_HTML,
    )

    with httpx.Client() as client:
        rows = fetch_credit_margin_yahoo("6758", client=client)

    assert rows[0].short_total == 299_300
