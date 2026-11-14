from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, Protocol, cast

import numpy as np
import pandas as pd

from yowayowa.chart_models import (
    ChartComposeRequest,
    ChartComposeResponse,
    ChartPoint,
    ChartSourceSpec,
    ChartTransformSpec,
    ComposedChartSeries,
)
from yowayowa.domain import Fundamentals, MarketHistory, Provenance
from yowayowa.symbols import normalize_symbol


class PriceHistoryProvider(Protocol):
    def history(
        self,
        symbol: str,
        period: str = "1y",
        interval: str = "1d",
        indicators: list[str] | None = None,
    ) -> MarketHistory: ...


class FundamentalsProvider(Protocol):
    def company_facts(self, symbol: str) -> Fundamentals: ...


class FredSeriesProvider(Protocol):
    def series(
        self,
        series_id: str,
        limit: int = 5000,
        observation_start: date | None = None,
        observation_end: date | None = None,
        units: str | None = None,
        frequency: str | None = None,
        aggregation_method: str | None = None,
    ) -> dict[str, object]: ...


def _points(series: pd.Series) -> list[ChartPoint]:
    output: list[ChartPoint] = []
    clean = series.replace([np.inf, -np.inf], np.nan).dropna()
    for index, value in clean.items():
        output.append(
            ChartPoint(
                date=pd.Timestamp(cast(Any, index)).date(),
                value=float(value),
            )
        )
    return output


def _source_series(model: ComposedChartSeries) -> pd.Series:
    if not model.points:
        return pd.Series(dtype=float)
    series = pd.Series(
        [point.value for point in model.points],
        index=pd.DatetimeIndex([point.date for point in model.points]),
        dtype=float,
    )
    return series[~series.index.duplicated(keep="last")].sort_index()


def _price_source(spec: ChartSourceSpec, provider: PriceHistoryProvider) -> ComposedChartSeries:
    assert spec.symbol is not None
    symbol = normalize_symbol(spec.symbol)
    history = provider.history(symbol, spec.period, "1d", [])
    values = pd.Series(
        [bar.close for bar in history.bars],
        index=pd.DatetimeIndex([bar.timestamp.date() for bar in history.bars]),
        dtype=float,
    )
    return ComposedChartSeries(
        id=spec.id,
        label=spec.label or f"{symbol} price",
        kind="source",
        source="price",
        unit=None,
        points=_points(values),
        provenance=[history.provenance],
    )


def _fundamental_source(
    spec: ChartSourceSpec,
    provider: FundamentalsProvider,
) -> ComposedChartSeries:
    assert spec.symbol is not None and spec.metric is not None
    symbol = normalize_symbol(spec.symbol)
    facts = provider.company_facts(symbol)
    metric = facts.metrics.get(spec.metric)
    if metric is None or not metric.points:
        raise LookupError(f"Fundamental metric {spec.metric} is unavailable for {symbol}")
    values = pd.Series(
        [float(point.value) for point in metric.points],
        index=pd.DatetimeIndex([point.period_end for point in metric.points]),
        dtype=float,
    )
    unit = metric.points[-1].unit if metric.points else None
    return ComposedChartSeries(
        id=spec.id,
        label=spec.label or f"{symbol} · {metric.label}",
        kind="source",
        source="fundamental",
        unit=unit,
        points=_points(values),
        provenance=[facts.provenance],
    )


def _fred_source(spec: ChartSourceSpec, provider: FredSeriesProvider) -> ComposedChartSeries:
    assert spec.series_id is not None
    series_id = spec.series_id.strip().upper()
    payload = provider.series(series_id)
    observations = payload.get("observations")
    if not isinstance(observations, list):
        raise LookupError(f"No FRED observations returned for {series_id}")
    dates: list[date] = []
    values: list[float] = []
    for row in observations:
        if not isinstance(row, dict):
            continue
        raw_date = row.get("date")
        raw_value = row.get("value")
        if not isinstance(raw_date, str) or raw_value is None or raw_value == ".":
            continue
        try:
            value = float(str(raw_value))
            observation_date = date.fromisoformat(raw_date)
        except ValueError:
            continue
        dates.append(observation_date)
        values.append(value)
    if not values:
        raise LookupError(f"No numeric FRED observations returned for {series_id}")

    metadata = payload.get("metadata")
    title = series_id
    unit: str | None = None
    if isinstance(metadata, dict):
        title = str(metadata.get("title") or title)
        raw_unit = metadata.get("units")
        unit = str(raw_unit) if raw_unit else None
    raw_provenance = payload.get("provenance")
    provenance: list[Provenance] = []
    if isinstance(raw_provenance, dict):
        provenance.append(Provenance.model_validate(raw_provenance))
    values_series = pd.Series(values, index=pd.DatetimeIndex(dates), dtype=float)
    return ComposedChartSeries(
        id=spec.id,
        label=spec.label or title,
        kind="source",
        source="fred",
        unit=unit,
        points=_points(values_series),
        provenance=provenance,
    )


def _unique_provenance(*groups: list[Provenance]) -> list[Provenance]:
    output: list[Provenance] = []
    seen: set[tuple[str, str | None, datetime]] = set()
    for group in groups:
        for item in group:
            key = (item.provider, item.source_url, item.retrieved_at)
            if key in seen:
                continue
            seen.add(key)
            output.append(item)
    return output


def _aligned(left: pd.Series, right: pd.Series) -> pd.DataFrame:
    frame = pd.concat({"left": left, "right": right}, axis=1).sort_index().ffill()
    return frame.dropna(how="any")


def _derived_series(
    spec: ChartTransformSpec,
    left_model: ComposedChartSeries,
    right_model: ComposedChartSeries,
) -> ComposedChartSeries:
    aligned = _aligned(_source_series(left_model), _source_series(right_model))
    if aligned.empty:
        raise LookupError(f"No overlapping as-of observations for transform {spec.id}")
    unit: str | None
    if spec.kind == "ratio":
        denominator = aligned["right"].replace(0, np.nan)
        values = aligned["left"] / denominator
        unit = "ratio"
        default_label = f"{left_model.label} / {right_model.label}"
    elif spec.kind == "spread":
        values = aligned["left"] - aligned["right"]
        unit = left_model.unit if left_model.unit == right_model.unit else None
        default_label = f"{left_model.label} - {right_model.label}"
    else:
        rolling = aligned["left"].rolling(spec.window, min_periods=spec.window)
        values = rolling.corr(aligned["right"])
        unit = "correlation"
        default_label = f"{left_model.label} vs {right_model.label} ({spec.window})"
    points = _points(values)
    if not points:
        raise LookupError(f"Transform {spec.id} produced no finite observations")
    return ComposedChartSeries(
        id=spec.id,
        label=spec.label or default_label,
        kind="derived",
        transform=spec.kind,
        unit=unit,
        points=points,
        provenance=_unique_provenance(left_model.provenance, right_model.provenance),
    )


def compose_chart(
    request: ChartComposeRequest,
    market: PriceHistoryProvider,
    fundamentals: FundamentalsProvider,
    fred: FredSeriesProvider,
) -> ChartComposeResponse:
    series_by_id: dict[str, ComposedChartSeries] = {}
    errors: dict[str, str] = {}
    for source_spec in request.sources:
        try:
            if source_spec.source == "price":
                model = _price_source(source_spec, market)
            elif source_spec.source == "fundamental":
                model = _fundamental_source(source_spec, fundamentals)
            else:
                model = _fred_source(source_spec, fred)
            series_by_id[source_spec.id] = model
        except Exception as exc:
            errors[source_spec.id] = str(exc)

    derived: list[ComposedChartSeries] = []
    for transform_spec in request.transforms:
        left = series_by_id.get(transform_spec.left)
        right = series_by_id.get(transform_spec.right)
        if left is None or right is None:
            errors[transform_spec.id] = "A required source is unavailable"
            continue
        try:
            model = _derived_series(transform_spec, left, right)
            derived.append(model)
        except Exception as exc:
            errors[transform_spec.id] = str(exc)

    return ChartComposeResponse(
        series=[*series_by_id.values(), *derived],
        errors=errors,
        composed_at=datetime.now(UTC),
        notes=[
            "Derived transforms align source observations on the union timeline and forward-fill "
            "past observations only; no backfill/look-ahead is used.",
            "Sparse fundamental and FRED observations remain sparse in their source series.",
        ],
    )
