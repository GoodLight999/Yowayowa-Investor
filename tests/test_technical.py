import pandas as pd
import pytest

from yowayowa.technical import compute_indicators, parse_indicator, parse_indicators


def _frame() -> pd.DataFrame:
    index = pd.date_range("2026-01-02", periods=120, freq="B", tz="UTC")
    close = pd.Series(
        [100 + offset * 0.4 + (offset % 7) * 0.2 for offset in range(120)],
        index=index,
    )
    return pd.DataFrame(
        {
            "open": close - 0.3,
            "high": close + 1.1,
            "low": close - 1.0,
            "close": close,
            "volume": [1_000_000 + offset * 1000 for offset in range(120)],
        },
        index=index,
    )


def test_indicator_parser_supports_defaults_and_extended_indicators() -> None:
    default_macd = parse_indicator("macd")
    custom_macd = parse_indicator("MACD8-21-5")
    bollinger = parse_indicator("bb20")
    stochastic = parse_indicator("STOCH14")
    supertrend = parse_indicator("supertrend10")
    obv = parse_indicator("OBV")

    assert (default_macd.fast, default_macd.slow, default_macd.signal) == (12, 26, 9)
    assert (custom_macd.fast, custom_macd.slow, custom_macd.signal) == (8, 21, 5)
    assert bollinger.kind == "bbands"
    assert bollinger.length == 20
    assert stochastic.kind == "stoch"
    assert stochastic.length == 14
    assert supertrend.kind == "supertrend"
    assert supertrend.length == 10
    assert obv.kind == "obv"
    assert obv.length is None


def test_indicator_parser_rejects_unknown_and_invalid_parameters() -> None:
    with pytest.raises(ValueError, match="Unknown indicator"):
        parse_indicator("magic14")
    with pytest.raises(ValueError, match="smaller than slow"):
        parse_indicator("macd30-20-9")
    with pytest.raises(ValueError, match="between 2 and 500"):
        parse_indicator("rsi1")
    with pytest.raises(ValueError, match="At most 12"):
        parse_indicators([f"sma{length}" for length in range(2, 15)])


def test_compute_major_technical_indicators_with_render_metadata() -> None:
    indicators = compute_indicators(
        _frame(),
        [
            "sma20",
            "ema20",
            "bb20",
            "rsi14",
            "macd",
            "atr14",
            "stoch14",
            "adx14",
            "willr14",
            "obv",
            "supertrend10",
        ],
    )
    by_name = {item.name: item for item in indicators}

    assert by_name["SMA 20"].pane == "price"
    assert by_name["EMA 20"].pane == "price"
    assert by_name["Bollinger Lower 20"].pane == "price"
    assert by_name["Bollinger Mid 20"].pane == "price"
    assert by_name["Bollinger Upper 20"].pane == "price"
    assert by_name["Supertrend 10x3"].pane == "price"

    rsi = by_name["RSI 14"]
    assert rsi.pane == "rsi"
    assert rsi.reference_lines == [30.0, 70.0]

    macd = by_name["MACD 12/26/9"]
    signal = by_name["MACD Signal 12/26/9"]
    histogram = by_name["MACD Histogram 12/26/9"]
    assert macd.pane == signal.pane == histogram.pane == "macd"
    assert macd.reference_lines == [0.0]
    assert histogram.render == "histogram"

    atr = by_name["ATR 14"]
    assert atr.pane == "atr"
    assert atr.render == "line"

    stochastic_k = by_name["Stochastic %K 14"]
    stochastic_d = by_name["Stochastic %D 14"]
    assert stochastic_k.pane == stochastic_d.pane == "stoch"
    assert stochastic_k.reference_lines == [20.0, 80.0]

    adx = by_name["ADX 14"]
    plus_di = by_name["+DI 14"]
    minus_di = by_name["-DI 14"]
    assert adx.pane == plus_di.pane == minus_di.pane == "adx"
    assert adx.reference_lines == [20.0, 25.0]

    williams = by_name["Williams %R 14"]
    assert williams.pane == "willr"
    assert williams.reference_lines == [-80.0, -20.0]

    assert by_name["OBV"].pane == "obv"

    for item in indicators:
        assert any(value is not None for _, value in item.points)
