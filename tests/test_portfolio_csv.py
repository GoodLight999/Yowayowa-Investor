import pytest

from yowayowa.services.portfolio_csv import parse_portfolio_csv


def test_portfolio_csv_handles_bom_quotes_and_optional_cost() -> None:
    csv_text = (
        "\ufeffsymbol,quantity,average_cost,currency,note\n"
        '"RKLB","10","20.5","USD","core, long"\n'
        "ASTS,5,,USD,watch\n"
    )
    rows = parse_portfolio_csv(csv_text)
    assert [row.symbol for row in rows] == ["RKLB", "ASTS"]
    assert str(rows[0].quantity) == "10"
    assert str(rows[0].average_cost) == "20.5"
    assert rows[1].average_cost is None


def test_portfolio_csv_requires_canonical_columns() -> None:
    with pytest.raises(ValueError, match="symbol, quantity"):
        parse_portfolio_csv("ticker,shares,currency\nRKLB,10,USD\n")
