"""Aggregate forward outcomes into calibration evidence.

Pure read-time aggregation: calibration consumes point-in-time strategy
snapshots plus an already-computed ``StrategyForwardOutcomeReport`` and never
writes to the database, so the point-in-time property of the underlying
snapshots is preserved.

Missing data is never treated as zero. Pending/unavailable outcomes are
excluded from return statistics but always reported via ``sample_pending`` /
``sample_unavailable``, and per-metric ``None`` fields (for example a missing
benchmark return) are excluded from that metric only.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from statistics import fmean, median

from yowayowa.strategy_models import (
    StrategyCalibrationBucket,
    StrategyCalibrationDecileMinimumSample,
    StrategyCalibrationMinimumSampleThreshold,
    StrategyCalibrationReport,
    StrategyFactorDecileSummary,
    StrategyForwardOutcome,
    StrategyForwardOutcomeReport,
    StrategyOutcomeDecileStatistics,
    StrategyPriorityFactor,
    StrategyResearchSnapshot,
    StrategyScoreDecileSummary,
)

# (score, total_return, excess_return)
ScoreRow = tuple[float, float | None, float | None]
# (factor_key, score/max_score fraction, total_return, excess_return)
FactorRow = tuple[str, float, float | None, float | None]


def _mean(values: list[float]) -> float:
    return fmean(values)


def _rank(values: Sequence[float]) -> list[float]:
    """Average ranks (1-based) with ties resolved by averaging."""
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    position = 0
    while position < len(order):
        tie_end = position
        while tie_end + 1 < len(order) and values[order[tie_end + 1]] == values[order[position]]:
            tie_end += 1
        average_rank = (position + tie_end + 2) / 2
        for tied in order[position : tie_end + 1]:
            ranks[tied] = average_rank
        position = tie_end + 1
    return ranks


def _pearson(first: Sequence[float], second: Sequence[float]) -> float:
    count = len(first)
    mean_first = _mean(list(first[:count]))
    mean_second = _mean(list(second[:count]))
    covariance = sum(
        (a - mean_first) * (b - mean_second) for a, b in zip(first, second, strict=True)
    )
    variance_first = sum((a - mean_first) ** 2 for a in first)
    variance_second = sum((b - mean_second) ** 2 for b in second)
    denominator = (variance_first * variance_second) ** 0.5
    if denominator == 0:
        raise ValueError("Pearson correlation is undefined for constant input")
    return float(covariance / denominator)


def spearman_rank_correlation(first: Sequence[float], second: Sequence[float]) -> float:
    """Spearman rank correlation implemented as Pearson correlation of ranks."""
    if len(first) != len(second):
        raise ValueError("Spearman correlation requires equal-length inputs")
    if len(first) < 2:
        raise ValueError("Spearman correlation requires at least 2 samples")
    return _pearson(_rank(first), _rank(second))


def _decile_groups(values: Sequence[float]) -> dict[int, list[int]]:
    """Group indices by equal-count decile label (1 = lowest rank, 10 = highest)."""
    count = len(values)
    groups: dict[int, list[int]] = defaultdict(list)
    for rank_position, original_index in enumerate(
        sorted(range(count), key=lambda index: values[index])
    ):
        decile = min((rank_position * 10) // count + 1, 10)
        groups[decile].append(original_index)
    return dict(groups)


def _statistics(
    rows: Sequence[tuple[float | None, float | None]],
) -> StrategyOutcomeDecileStatistics:
    total_values = [total for total, _ in rows if total is not None]
    excess_values = [excess for _, excess in rows if excess is not None]
    positive_excess = [value for value in excess_values if value > 0]
    return StrategyOutcomeDecileStatistics(
        sample_count=len(rows),
        median_total_return=median(total_values) if total_values else None,
        mean_total_return=_mean(total_values) if total_values else None,
        median_excess_return=median(excess_values) if excess_values else None,
        mean_excess_return=_mean(excess_values) if excess_values else None,
        positive_excess_hit_rate=(
            len(positive_excess) / len(excess_values) if excess_values else None
        ),
    )


def _score_deciles(rows: list[ScoreRow]) -> list[StrategyScoreDecileSummary]:
    summaries: list[StrategyScoreDecileSummary] = []
    groups = _decile_groups([row[0] for row in rows])
    for decile in sorted(groups):
        members = [rows[index] for index in groups[decile]]
        scores = [member[0] for member in members]
        stats = _statistics([(member[1], member[2]) for member in members])
        summaries.append(
            StrategyScoreDecileSummary(
                decile=decile,
                score_min=min(scores),
                score_max=max(scores),
                **stats.model_dump(),
            )
        )
    return summaries


def _factor_deciles(rows: list[FactorRow]) -> list[StrategyFactorDecileSummary]:
    grouped: dict[str, list[FactorRow]] = defaultdict(list)
    for row in rows:
        grouped[row[0]].append(row)

    summaries: list[StrategyFactorDecileSummary] = []
    for factor_key in sorted(grouped):
        members = grouped[factor_key]
        groups = _decile_groups([member[1] for member in members])
        for decile in sorted(groups):
            decile_members = [members[index] for index in groups[decile]]
            fractions = [member[1] for member in decile_members]
            stats = _statistics([(member[2], member[3]) for member in decile_members])
            summaries.append(
                StrategyFactorDecileSummary(
                    factor_key=factor_key,  # type: ignore[arg-type]
                    decile=decile,
                    factor_score_fraction_min=min(fractions),
                    factor_score_fraction_max=max(fractions),
                    **stats.model_dump(),
                )
            )
    return summaries


def _factor_rows(
    snapshots: list[StrategyResearchSnapshot],
    outcomes: list[StrategyForwardOutcome],
) -> tuple[list[FactorRow], int]:
    """Build normalized factor rows for available outcomes; count missing factor payloads."""
    factors_by_snapshot: dict[int, list[StrategyPriorityFactor]] = {
        snapshot.id: (
            snapshot.evaluation.research_priority.factors
            if snapshot.evaluation.research_priority is not None
            else []
        )
        for snapshot in snapshots
    }
    rows: list[FactorRow] = []
    missing = 0
    for outcome in outcomes:
        factors = factors_by_snapshot.get(outcome.snapshot_id, [])
        if not factors:
            missing += 1
            continue
        for factor in factors:
            rows.append(
                (
                    factor.key,
                    factor.score / factor.max_score,
                    outcome.total_return,
                    outcome.excess_return,
                )
            )
    return rows, missing


def calibration_report(
    snapshots: list[StrategyResearchSnapshot],
    outcome_report: StrategyForwardOutcomeReport,
    *,
    evaluated_at: datetime | None = None,
) -> StrategyCalibrationReport:
    """Aggregate a forward-outcome report into per-(strategy, version, horizon) buckets."""
    evaluated_at = evaluated_at or datetime.now(UTC)

    grouped: dict[tuple[str, str, int], list[StrategyForwardOutcome]] = defaultdict(list)
    for outcome in outcome_report.outcomes:
        grouped[
            (outcome.strategy_id, outcome.scoring_version, outcome.horizon_trading_days)
        ].append(outcome)

    buckets: list[StrategyCalibrationBucket] = []
    for (strategy_id, scoring_version, horizon), members in sorted(grouped.items()):
        available = [outcome for outcome in members if outcome.status == "available"]
        pending = sum(1 for outcome in members if outcome.status == "pending")
        unavailable = sum(1 for outcome in members if outcome.status == "unavailable")

        score_rows: list[ScoreRow] = [
            (
                outcome.research_priority_score,
                outcome.total_return,
                outcome.excess_return,
            )
            for outcome in available
        ]
        excess_pairs = [(row[0], row[2]) for row in score_rows if row[2] is not None]

        bucket_notes: list[str] = [
            (
                "Decile and bucket sample_count counts available outcomes; total-return "
                "statistics use outcomes with a non-None total_return and excess-return "
                "statistics use outcomes with a non-None excess_return."
            ),
            (
                "Overlapping snapshot windows are treated as independent point-in-time "
                "observations, which can overstate the effective sample size of the rank "
                "information coefficient; walk-forward / out-of-sample evaluation is not "
                "implemented."
            ),
            (
                "The research-priority score is an attention-allocation score, never an "
                "expected-return forecast."
            ),
        ]
        missing_benchmark = sum(1 for outcome in available if outcome.excess_return is None)
        if missing_benchmark:
            bucket_notes.append(
                f"{missing_benchmark} available outcome(s) lack a benchmark excess return "
                "and are excluded from excess-return statistics only."
            )

        statistics = _statistics([(row[1], row[2]) for row in score_rows])

        score_deciles: list[StrategyScoreDecileSummary] = []
        factor_deciles: list[StrategyFactorDecileSummary] = []
        if len(available) < StrategyCalibrationDecileMinimumSample:
            bucket_notes.append(
                "Score and factor deciles omitted: fewer than "
                f"{StrategyCalibrationDecileMinimumSample} available outcomes "
                f"({len(available)})."
            )
        else:
            score_deciles = _score_deciles(score_rows)
            factor_rows, factor_missing = _factor_rows(snapshots, available)
            if factor_rows:
                factor_deciles = _factor_deciles(factor_rows)
            if factor_missing:
                bucket_notes.append(
                    f"{factor_missing} available outcome(s) carry no factor scores and "
                    "are excluded from factor deciles."
                )

        rank_ic: float | None = None
        ic_sample_count = 0
        ic_insufficient = True
        if len(excess_pairs) >= StrategyCalibrationDecileMinimumSample:
            try:
                rank_ic = spearman_rank_correlation(
                    [pair[0] for pair in excess_pairs],
                    [pair[1] for pair in excess_pairs],
                )
                ic_sample_count = len(excess_pairs)
                ic_insufficient = False
            except ValueError:
                ic_sample_count = len(excess_pairs)
                bucket_notes.append(
                    "Rank IC undefined: the excess-return sample has zero variance in "
                    "scores or returns."
                )
        elif excess_pairs:
            bucket_notes.append(
                "Rank IC omitted: fewer than "
                f"{StrategyCalibrationDecileMinimumSample} outcomes with a benchmark "
                f"excess return ({len(excess_pairs)})."
            )
        else:
            bucket_notes.append(
                "Rank IC omitted: no available outcome has a benchmark excess return."
            )

        buckets.append(
            StrategyCalibrationBucket(
                strategy_id=strategy_id,
                scoring_version=scoring_version,
                horizon_trading_days=horizon,
                sample_total=len(members),
                sample_available=len(available),
                sample_pending=pending,
                sample_unavailable=unavailable,
                minimum_sample_warning=(len(available) < StrategyCalibrationMinimumSampleThreshold),
                score_deciles=score_deciles,
                factor_deciles=factor_deciles,
                median_total_return=statistics.median_total_return,
                mean_total_return=statistics.mean_total_return,
                median_excess_return=statistics.median_excess_return,
                mean_excess_return=statistics.mean_excess_return,
                positive_excess_hit_rate=statistics.positive_excess_hit_rate,
                rank_ic=rank_ic,
                ic_sample_count=ic_sample_count,
                ic_insufficient=ic_insufficient,
                notes=bucket_notes,
            )
        )

    return StrategyCalibrationReport(
        buckets=buckets,
        provenance=outcome_report.provenance,
        notes=[
            (
                "Calibration aggregates point-in-time snapshot outcomes at read time; "
                "nothing is written back and history is never rewritten."
            ),
            (
                "Pending and unavailable outcomes are excluded from return statistics and "
                "reported separately; missing data is never treated as zero."
            ),
        ],
        evaluated_at=evaluated_at,
    )
