from __future__ import annotations

from datetime import UTC, date, datetime

from yowayowa.domain import MarketHistory, PriceBar, Provenance
from yowayowa.providers.base import MarketDataProvider
from yowayowa.strategy_models import (
    StrategyForwardOutcome,
    StrategyForwardOutcomeReport,
    StrategyResearchSnapshot,
)

REGION_BENCHMARKS: dict[str, str] = {
    "us": "^GSPC",
    "jp": "^N225",
    "gb": "^FTSE",
    "de": "^GDAXI",
    "fr": "^FCHI",
    "hk": "^HSI",
    "cn": "000001.SS",
    "tw": "^TWII",
    "kr": "^KS11",
    "ch": "^SSMI",
    "ca": "^GSPTSE",
    "au": "^AXJO",
    "sg": "^STI",
    "br": "^BVSP",
    "in": "^BSESN",
}


def _history_period(captured_at: datetime, now: datetime) -> str:
    age_days = max((now.date() - captured_at.date()).days, 0)
    if age_days <= 300:
        return "1y"
    if age_days <= 650:
        return "2y"
    if age_days <= 1800:
        return "5y"
    return "max"


def _bars_after(history: MarketHistory, captured_on: date) -> list[PriceBar]:
    return sorted(
        (bar for bar in history.bars if bar.timestamp.date() > captured_on),
        key=lambda bar: bar.timestamp,
    )


def _return_between(
    history: MarketHistory,
    start_date: date,
    end_date: date,
) -> float | None:
    bars = sorted(
        (bar for bar in history.bars if start_date <= bar.timestamp.date() <= end_date),
        key=lambda bar: bar.timestamp,
    )
    if len(bars) < 2 or bars[0].close == 0:
        return None
    return bars[-1].close / bars[0].close - 1.0


def forward_outcome_report(
    snapshots: list[StrategyResearchSnapshot],
    provider: MarketDataProvider,
    *,
    horizons: list[int] | None = None,
    benchmark_symbol: str | None = None,
    evaluated_at: datetime | None = None,
) -> StrategyForwardOutcomeReport:
    evaluated_at = evaluated_at or datetime.now(UTC)
    resolved_horizons = sorted(set(horizons or [20, 60, 120]))
    if not resolved_horizons or any(value <= 0 or value > 500 for value in resolved_horizons):
        raise ValueError("Outcome horizons must be between 1 and 500 trading days")

    history_cache: dict[tuple[str, str], MarketHistory | Exception] = {}
    provenance: list[Provenance] = []
    provenance_seen: set[str] = set()

    def history(symbol: str, period: str) -> MarketHistory:
        key = (symbol, period)
        cached = history_cache.get(key)
        if isinstance(cached, Exception):
            raise cached
        if cached is not None:
            return cached
        try:
            loaded = provider.history(symbol, period=period, interval="1d", indicators=[])
        except Exception as exc:
            history_cache[key] = exc
            raise
        history_cache[key] = loaded
        marker = loaded.provenance.model_dump_json()
        if marker not in provenance_seen:
            provenance_seen.add(marker)
            provenance.append(loaded.provenance)
        return loaded

    outcomes: list[StrategyForwardOutcome] = []
    for snapshot in snapshots:
        period = _history_period(snapshot.captured_at, evaluated_at)
        resolved_benchmark = benchmark_symbol or REGION_BENCHMARKS.get(snapshot.region)
        try:
            security_history = history(snapshot.symbol, period)
        except Exception:
            for horizon in resolved_horizons:
                outcomes.append(
                    StrategyForwardOutcome(
                        snapshot_id=snapshot.id,
                        strategy_id=snapshot.strategy_id,
                        scoring_version=snapshot.scoring_version,
                        region=snapshot.region,
                        symbol=snapshot.symbol,
                        research_priority_score=snapshot.score,
                        captured_at=snapshot.captured_at,
                        horizon_trading_days=horizon,
                        status="unavailable",
                        benchmark_symbol=resolved_benchmark,
                    )
                )
            continue

        future_bars = _bars_after(security_history, snapshot.captured_at.date())
        entry = future_bars[0] if future_bars else None
        benchmark_history: MarketHistory | None = None
        if resolved_benchmark:
            try:
                benchmark_history = history(resolved_benchmark, period)
            except Exception:
                benchmark_history = None

        for horizon in resolved_horizons:
            if entry is None:
                outcomes.append(
                    StrategyForwardOutcome(
                        snapshot_id=snapshot.id,
                        strategy_id=snapshot.strategy_id,
                        scoring_version=snapshot.scoring_version,
                        region=snapshot.region,
                        symbol=snapshot.symbol,
                        research_priority_score=snapshot.score,
                        captured_at=snapshot.captured_at,
                        horizon_trading_days=horizon,
                        status="pending",
                        benchmark_symbol=resolved_benchmark,
                    )
                )
                continue
            if len(future_bars) <= horizon:
                outcomes.append(
                    StrategyForwardOutcome(
                        snapshot_id=snapshot.id,
                        strategy_id=snapshot.strategy_id,
                        scoring_version=snapshot.scoring_version,
                        region=snapshot.region,
                        symbol=snapshot.symbol,
                        research_priority_score=snapshot.score,
                        captured_at=snapshot.captured_at,
                        horizon_trading_days=horizon,
                        status="pending",
                        entry_at=entry.timestamp,
                        entry_price=entry.close,
                        benchmark_symbol=resolved_benchmark,
                    )
                )
                continue

            exit_bar = future_bars[horizon]
            total_return = exit_bar.close / entry.close - 1.0 if entry.close else None
            benchmark_return = (
                _return_between(
                    benchmark_history,
                    entry.timestamp.date(),
                    exit_bar.timestamp.date(),
                )
                if benchmark_history is not None
                else None
            )
            excess_return = (
                total_return - benchmark_return
                if total_return is not None and benchmark_return is not None
                else None
            )
            outcomes.append(
                StrategyForwardOutcome(
                    snapshot_id=snapshot.id,
                    strategy_id=snapshot.strategy_id,
                    scoring_version=snapshot.scoring_version,
                    region=snapshot.region,
                    symbol=snapshot.symbol,
                    research_priority_score=snapshot.score,
                    captured_at=snapshot.captured_at,
                    horizon_trading_days=horizon,
                    status="available",
                    entry_at=entry.timestamp,
                    exit_at=exit_bar.timestamp,
                    entry_price=entry.close,
                    exit_price=exit_bar.close,
                    total_return=total_return,
                    benchmark_symbol=resolved_benchmark,
                    benchmark_return=benchmark_return,
                    excess_return=excess_return,
                )
            )

    return StrategyForwardOutcomeReport(
        outcomes=outcomes,
        provenance=provenance,
        notes=[
            (
                "Signals are evaluated from the first market close strictly after the snapshot "
                "date; the signal-day close is never used as an assumed executable entry."
            ),
            (
                "A horizon of N trading days compares that entry close with the close N security "
                "sessions later. Pending means the required future sessions do not yet exist."
            ),
            (
                "Benchmark return uses the first and last available benchmark closes within the "
                "same calendar window as the security outcome. Excess return is omitted if the "
                "benchmark is unavailable."
            ),
            (
                "These are forward-validation observations for score calibration, not a claim "
                "that historical results guarantee future returns."
            ),
        ],
        evaluated_at=evaluated_at,
    )
