from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, cast

import pandas as pd
import pandas_ta_classic as ta

from yowayowa.domain import IndicatorSeries

IndicatorKind = Literal[
    "sma",
    "ema",
    "rsi",
    "bbands",
    "macd",
    "atr",
    "stoch",
    "adx",
    "willr",
    "obv",
    "supertrend",
]

_SIMPLE_TOKEN = re.compile(r"^(sma|ema|rsi|atr|bb|bbands|stoch|adx|willr|supertrend)(\d{1,3})$")
_MACD_TOKEN = re.compile(r"^macd(?:(\d{1,3})-(\d{1,3})-(\d{1,3}))?$")
_MAX_REQUESTED_INDICATORS = 12


@dataclass(frozen=True, slots=True)
class IndicatorSpec:
    kind: IndicatorKind
    length: int | None = None
    fast: int | None = None
    slow: int | None = None
    signal: int | None = None


def _bounded_length(value: int, *, label: str) -> int:
    if not 2 <= value <= 500:
        raise ValueError(f"{label} length must be between 2 and 500")
    return value


def parse_indicator(token: str) -> IndicatorSpec:
    normalized = token.strip().lower()
    if normalized == "obv":
        return IndicatorSpec(kind="obv")

    simple = _SIMPLE_TOKEN.fullmatch(normalized)
    if simple:
        raw_kind, raw_length = simple.groups()
        normalized_kind = "bbands" if raw_kind in {"bb", "bbands"} else raw_kind
        kind = cast(IndicatorKind, normalized_kind)
        return IndicatorSpec(
            kind=kind,
            length=_bounded_length(int(raw_length), label=kind.upper()),
        )

    macd = _MACD_TOKEN.fullmatch(normalized)
    if macd:
        raw_fast, raw_slow, raw_signal = macd.groups()
        fast = _bounded_length(int(raw_fast or 12), label="MACD fast")
        slow = _bounded_length(int(raw_slow or 26), label="MACD slow")
        signal = _bounded_length(int(raw_signal or 9), label="MACD signal")
        if fast >= slow:
            raise ValueError("MACD fast length must be smaller than slow length")
        return IndicatorSpec(kind="macd", fast=fast, slow=slow, signal=signal)

    raise ValueError(
        f"Unknown indicator '{token}'. Supported forms: sma20, ema20, rsi14, bb20, "
        "atr14, stoch14, adx14, willr14, obv, supertrend10, macd, macd12-26-9"
    )


def parse_indicators(tokens: list[str]) -> list[IndicatorSpec]:
    normalized = list(dict.fromkeys(token.strip().lower() for token in tokens if token.strip()))
    if len(normalized) > _MAX_REQUESTED_INDICATORS:
        raise ValueError(f"At most {_MAX_REQUESTED_INDICATORS} indicators may be requested")
    return [parse_indicator(token) for token in normalized]


def _utc_timestamp(value: Any) -> datetime:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    return timestamp.to_pydatetime()


def _series(
    name: str,
    parameters: dict[str, int | float],
    values: pd.Series | None,
    *,
    pane: str = "price",
    render: Literal["line", "histogram"] = "line",
    reference_lines: list[float] | None = None,
) -> IndicatorSeries:
    points: list[tuple[datetime, float | None]] = []
    if values is not None:
        for index, value in values.items():
            points.append((_utc_timestamp(index), float(value) if pd.notna(value) else None))
    return IndicatorSeries(
        name=name,
        parameters=parameters,
        points=points,
        pane=pane,
        render=render,
        reference_lines=reference_lines or [],
    )


def _column(frame: pd.DataFrame | None, prefix: str) -> pd.Series | None:
    if frame is None or frame.empty:
        return None
    column = next((name for name in frame.columns if str(name).startswith(prefix)), None)
    if column is None:
        return None
    values = frame[column]
    return values if isinstance(values, pd.Series) else values.iloc[:, 0]


def _compute_one(frame: pd.DataFrame, spec: IndicatorSpec) -> list[IndicatorSeries]:
    close = frame["close"]
    high = frame["high"]
    low = frame["low"]

    if spec.kind == "sma":
        assert spec.length is not None
        values = ta.sma(close, length=spec.length)
        return [_series(f"SMA {spec.length}", {"length": spec.length}, values)]
    if spec.kind == "ema":
        assert spec.length is not None
        values = ta.ema(close, length=spec.length)
        return [_series(f"EMA {spec.length}", {"length": spec.length}, values)]
    if spec.kind == "rsi":
        assert spec.length is not None
        return [
            _series(
                f"RSI {spec.length}",
                {"length": spec.length},
                ta.rsi(close, length=spec.length),
                pane="rsi",
                reference_lines=[30, 70],
            )
        ]
    if spec.kind == "atr":
        assert spec.length is not None
        return [
            _series(
                f"ATR {spec.length}",
                {"length": spec.length},
                ta.atr(high, low, close, length=spec.length),
                pane="atr",
            )
        ]
    if spec.kind == "bbands":
        assert spec.length is not None
        deviation = 2.0
        values = ta.bbands(close, length=spec.length, std=deviation)
        parameters: dict[str, int | float] = {"length": spec.length, "std": deviation}
        return [
            _series(
                f"Bollinger Lower {spec.length}",
                parameters,
                _column(values, "BBL_"),
            ),
            _series(
                f"Bollinger Mid {spec.length}",
                parameters,
                _column(values, "BBM_"),
            ),
            _series(
                f"Bollinger Upper {spec.length}",
                parameters,
                _column(values, "BBU_"),
            ),
        ]
    if spec.kind == "stoch":
        assert spec.length is not None
        d = 3
        smooth_k = 3
        values = ta.stoch(high, low, close, k=spec.length, d=d, smooth_k=smooth_k)
        parameters = {"k": spec.length, "d": d, "smooth_k": smooth_k}
        return [
            _series(
                f"Stochastic %K {spec.length}",
                parameters,
                _column(values, "STOCHk_"),
                pane="stoch",
                reference_lines=[20, 80],
            ),
            _series(
                f"Stochastic %D {spec.length}",
                parameters,
                _column(values, "STOCHd_"),
                pane="stoch",
            ),
        ]
    if spec.kind == "adx":
        assert spec.length is not None
        values = ta.adx(high, low, close, length=spec.length)
        parameters = {"length": spec.length}
        return [
            _series(
                f"ADX {spec.length}",
                parameters,
                _column(values, "ADX_"),
                pane="adx",
                reference_lines=[20, 25],
            ),
            _series(
                f"+DI {spec.length}",
                parameters,
                _column(values, "DMP_"),
                pane="adx",
            ),
            _series(
                f"-DI {spec.length}",
                parameters,
                _column(values, "DMN_"),
                pane="adx",
            ),
        ]
    if spec.kind == "willr":
        assert spec.length is not None
        return [
            _series(
                f"Williams %R {spec.length}",
                {"length": spec.length},
                ta.willr(high, low, close, length=spec.length),
                pane="willr",
                reference_lines=[-80, -20],
            )
        ]
    if spec.kind == "obv":
        return [
            _series(
                "OBV",
                {},
                ta.obv(close, frame["volume"]),
                pane="obv",
            )
        ]
    if spec.kind == "supertrend":
        assert spec.length is not None
        multiplier = 3.0
        values = ta.supertrend(high, low, close, length=spec.length, multiplier=multiplier)
        return [
            _series(
                f"Supertrend {spec.length}x3",
                {"length": spec.length, "multiplier": multiplier},
                _column(values, "SUPERT_"),
            )
        ]

    assert spec.fast is not None and spec.slow is not None and spec.signal is not None
    values = ta.macd(close, fast=spec.fast, slow=spec.slow, signal=spec.signal)
    parameters = {"fast": spec.fast, "slow": spec.slow, "signal": spec.signal}
    label = f"{spec.fast}/{spec.slow}/{spec.signal}"
    return [
        _series(
            f"MACD {label}",
            parameters,
            _column(values, "MACD_"),
            pane="macd",
            reference_lines=[0],
        ),
        _series(
            f"MACD Signal {label}",
            parameters,
            _column(values, "MACDs_"),
            pane="macd",
        ),
        _series(
            f"MACD Histogram {label}",
            parameters,
            _column(values, "MACDh_"),
            pane="macd",
            render="histogram",
        ),
    ]


def compute_indicators(frame: pd.DataFrame, tokens: list[str]) -> list[IndicatorSeries]:
    output: list[IndicatorSeries] = []
    for spec in parse_indicators(tokens):
        output.extend(_compute_one(frame, spec))
    return output
