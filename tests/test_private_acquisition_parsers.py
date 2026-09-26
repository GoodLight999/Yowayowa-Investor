import pytest

from yowayowa.acquisition.parsers import (
    PARSER_REGISTRY,
    TableHtmlParser,
    TextHtmlParser,
    lookup_parser,
)
from yowayowa.acquisition.transport import PrivateAcquisitionError

_TABLE_HTML = """
<html><body>
<table>
  <tr><th>Symbol</th><th>Quantity</th></tr>
  <tr><td> 7203 </td><td>100</td></tr>
  <tr><td>6758</td><td>200</td></tr>
</table>
<table>
  <tr><td>Cash</td><td>JPY</td></tr>
  <tr><td>1,200,000</td><td>Deposit</td></tr>
</table>
</body></html>
"""


def test_table_parser_extracts_headers_and_rows_with_whitespace_stripped() -> None:
    payload = TableHtmlParser().parse(_TABLE_HTML)
    tables = payload["tables"]
    assert len(tables) == 2
    assert tables[0]["headers"] == ["Symbol", "Quantity"]
    assert tables[0]["rows"] == [
        {"Symbol": "7203", "Quantity": "100"},
        {"Symbol": "6758", "Quantity": "200"},
    ]
    # Second table's first tr doubles as headers.
    assert tables[1]["headers"] == ["Cash", "JPY"]
    assert tables[1]["rows"] == [{"Cash": "1,200,000", "JPY": "Deposit"}]


def test_table_parser_empty_html_returns_no_tables() -> None:
    payload = TableHtmlParser().parse("<html><body></body></html>")
    assert payload == {"tables": []}


def test_table_parser_nested_tags_in_cells() -> None:
    html = "<table><tr><th>名前</th></tr><tr><td><b>トヨタ</b> <i>自動車</i></td></tr></table>"
    payload = TableHtmlParser().parse(html)
    assert payload["tables"][0]["rows"] == [{"名前": "トヨタ 自動車"}]


def test_table_parser_versions() -> None:
    parser = TableHtmlParser()
    assert parser.parser_version == "table-v1"
    assert parser.schema_version == "tables-v1"


def test_text_parser_title_and_length() -> None:
    html = "<html><head><title>口座情報</title></head><body>data</body></html>"
    payload = TextHtmlParser().parse(html)
    assert payload["title"] == "口座情報"
    assert payload["text_length"] == len(html)
    assert TextHtmlParser().parser_version == "text-v1"


def test_text_parser_no_title_is_none() -> None:
    payload = TextHtmlParser().parse("<p>no title</p>")
    assert payload["title"] is None


def test_parser_registry_contains_builtin_adapters() -> None:
    assert set(PARSER_REGISTRY) == {"tables", "text"}


def test_lookup_unknown_parser_raises_failed() -> None:
    with pytest.raises(PrivateAcquisitionError) as excinfo:
        lookup_parser("xml")
    assert excinfo.value.state.value == "failed"
    assert "unknown parser" in excinfo.value.reason
