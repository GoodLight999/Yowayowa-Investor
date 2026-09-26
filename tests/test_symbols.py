import pytest

from yowayowa.symbols import InputValidationError, normalize_currency, normalize_symbol


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("rklb", "RKLB"),
        ("7203.t", "7203.T"),
        ("btc-usd", "BTC-USD"),
        ("^gspc", "^GSPC"),
        ("eurusd=x", "EURUSD=X"),
        ("brk/b", "BRK/B"),
    ],
)
def test_normalize_symbol_accepts_supported_market_forms(raw: str, expected: str) -> None:
    assert normalize_symbol(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "<script>", "RK LB", "x" * 33])
def test_normalize_symbol_rejects_invalid_market_forms(raw: str) -> None:
    with pytest.raises(InputValidationError):
        normalize_symbol(raw)


def test_normalize_currency_requires_three_ascii_letters() -> None:
    assert normalize_currency(" usd ") == "USD"
    with pytest.raises(InputValidationError):
        normalize_currency("US")
    with pytest.raises(InputValidationError):
        normalize_currency("USDX")
    with pytest.raises(InputValidationError):
        normalize_currency("U$D")
