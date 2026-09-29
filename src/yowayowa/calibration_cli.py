from __future__ import annotations

import httpx
import typer
from rich import print
from rich.table import Table

from yowayowa.cli import _client


def _get_json(client: httpx.Client, path: str, params: dict[str, object] | None = None) -> object:
    response = client.get(path, params=params)
    if response.status_code >= 400:
        try:
            detail = str(response.json().get("detail", ""))
        except Exception:
            detail = response.text[:200]
        print(f"HTTP {response.status_code}: {detail}")
        raise typer.Exit(code=1)
    return response.json()


def _fmt(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, float) and abs(value) <= 1:
        return f"{value:.2%}"
    return str(value)


def strategy_calibration(
    strategy_id: str | None = typer.Option(None),
    region: str | None = typer.Option(None),
    symbol: str | None = typer.Option(None),
    horizons: str = typer.Option("20,60,120"),
    benchmark: str | None = typer.Option(None),
    limit: int = typer.Option(50, min=1, max=200),
    oos_min_sample: int | None = typer.Option(None, min=1, max=1000),
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    params: dict[str, object] = {"horizons": horizons, "limit": limit}
    for key, value in (
        ("strategy_id", strategy_id),
        ("region", region),
        ("symbol", symbol),
        ("benchmark", benchmark),
        ("oos_min_sample", oos_min_sample),
    ):
        if value is not None:
            params[key] = value
    with _client(base_url, token) as client:
        report = _get_json(client, "/v1/strategy-research/calibration", params)
    buckets = report["buckets"]
    if not buckets:
        print("No calibration buckets")
    for bucket in buckets:
        print(
            f"[bold]{bucket['strategy_id']} / {bucket['scoring_version']} / {bucket['horizon_trading_days']}[/bold]"
        )
        print(
            "Samples: "
            + " / ".join(
                f"{k}={bucket[k]}"
                for k in (
                    "sample_total",
                    "sample_available",
                    "sample_pending",
                    "sample_unavailable",
                )
            )
        )
        if bucket["minimum_sample_warning"]:
            print("WARNING: minimum sample")
        for key in (
            "median_total_return",
            "mean_total_return",
            "median_excess_return",
            "mean_excess_return",
            "positive_excess_hit_rate",
        ):
            print(f"{key}: {_fmt(bucket.get(key))}")
        if bucket["ic_insufficient"]:
            print(f"IC: insufficient (n={bucket['ic_sample_count']})")
        else:
            print(f"rank_ic: {_fmt(bucket.get('rank_ic'))} (n={bucket['ic_sample_count']})")
        for name in ("score_deciles", "factor_deciles"):
            rows = bucket[name]
            table = Table(
                name, "Decile", "Factor", "Range", "Samples", "Median return", "Median excess"
            )
            for row in rows:
                table.add_row(
                    str(row.get("decile")),
                    str(row.get("factor_key", "—")),
                    str(row.get("score_min", row.get("factor_score_fraction_min", "—")))
                    + "–"
                    + str(row.get("score_max", row.get("factor_score_fraction_max", "—"))),
                    str(row["sample_count"]),
                    _fmt(row.get("median_total_return")),
                    _fmt(row.get("median_excess_return")),
                )
            if rows:
                print(table)
        for key in (
            "oos_rank_ic",
            "oos_ic_sample_count",
            "oos_ic_insufficient",
            "oos_median_total_return",
            "oos_mean_total_return",
            "oos_median_excess_return",
            "oos_mean_excess_return",
            "oos_positive_excess_hit_rate",
            "oos_sample_count",
            "is_rank_ic",
            "is_ic_sample_count",
            "is_ic_insufficient",
            "is_median_total_return",
            "is_mean_total_return",
            "is_median_excess_return",
            "is_mean_excess_return",
            "is_positive_excess_hit_rate",
            "is_sample_count",
            "purged_count",
            "exit_at_unknown_count",
            "oos_split_at",
            "oos_median_total_return_ci_low",
            "oos_median_total_return_ci_high",
            "oos_median_excess_return_ci_low",
            "oos_median_excess_return_ci_high",
        ):
            print(f"{key}: {_fmt(bucket.get(key))}")
        if bucket.get("oos_notes"):
            print("OOS notes: " + "; ".join(bucket["oos_notes"]))
        if bucket.get("notes"):
            print("Notes: " + "; ".join(bucket["notes"]))
    for note in report.get("notes", []):
        print(f"Note: {note}")
    for source in report.get("provenance", []):
        print(f"Provenance: {source}")
