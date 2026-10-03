from __future__ import annotations

from typing import Any, cast

import httpx
import typer
from rich import print
from rich.table import Table

from yowayowa.cli import _client


def _get_json(
    client: httpx.Client,
    path: str,
    params: dict[str, str | int | float | bool] | None = None,
) -> Any:
    response = client.get(path, params=params)
    if response.status_code >= 400:
        try:
            detail = str(response.json().get("detail", ""))
        except Exception:
            detail = response.text[:200]
        print(f"HTTP {response.status_code}: {detail}")
        raise typer.Exit(code=1)
    return response.json()


def _pct(value: object) -> str:
    return "—" if value is None else f"{cast(float, value):+.2%}"


def _num(value: object) -> str:
    return "—" if value is None else str(value)


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
    params: dict[str, str | int | float | bool] = {"horizons": horizons, "limit": limit}
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
            f"[bold]{bucket['strategy_id']} / {bucket['scoring_version']} / "
            f"{bucket['horizon_trading_days']}[/bold]"
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
            print(f"{key}: {_pct(bucket.get(key))}")
        if bucket["ic_insufficient"]:
            print(f"IC: insufficient (n={bucket['ic_sample_count']})")
        else:
            print(f"rank_ic: {_num(bucket.get('rank_ic'))} (n={bucket['ic_sample_count']})")
        for name in ("score_deciles", "factor_deciles"):
            rows = bucket[name]
            if name == "score_deciles":
                table = Table(
                    "Decile", "Score min", "Score max", "Samples", "Median return", "Median excess"
                )
                for row in rows:
                    table.add_row(
                        _num(row.get("decile")),
                        _num(row.get("score_min")),
                        _num(row.get("score_max")),
                        _num(row.get("sample_count")),
                        _pct(row.get("median_total_return")),
                        _pct(row.get("median_excess_return")),
                    )
            else:
                table = Table(
                    "Decile", "Factor", "Range", "Samples", "Median return", "Median excess"
                )
                for row in rows:
                    table.add_row(
                        _num(row.get("decile")),
                        _num(row.get("factor_key")),
                        _num(row.get("factor_score_fraction_min"))
                        + "-"
                        + _num(row.get("factor_score_fraction_max")),
                        _num(row.get("sample_count")),
                        _pct(row.get("median_total_return")),
                        _pct(row.get("median_excess_return")),
                    )
            if rows:
                assert table.row_count == len(rows)
                print(table)
        pct_keys = (
            "oos_median_total_return",
            "oos_mean_total_return",
            "oos_median_excess_return",
            "oos_mean_excess_return",
            "oos_positive_excess_hit_rate",
            "oos_median_total_return_ci_low",
            "oos_median_total_return_ci_high",
            "oos_median_excess_return_ci_low",
            "oos_median_excess_return_ci_high",
        )
        is_pct_keys = (
            "is_median_total_return",
            "is_mean_total_return",
            "is_median_excess_return",
            "is_mean_excess_return",
            "is_positive_excess_hit_rate",
        )
        print("Out-of-sample:")
        for key in pct_keys:
            print(f"  {key}: {_pct(bucket.get(key))}")
        if bucket["oos_ic_insufficient"]:
            print(f"  Out-of-sample IC: insufficient (n={bucket['oos_ic_sample_count']})")
        else:
            print(
                f"  Out-of-sample IC: {_num(bucket.get('oos_rank_ic'))} "
                f"(n={bucket['oos_ic_sample_count']})"
            )
        print(f"  oos_sample_count: {_num(bucket.get('oos_sample_count'))}")
        print("In-sample:")
        for key in is_pct_keys:
            print(f"  {key}: {_pct(bucket.get(key))}")
        if bucket["is_ic_insufficient"]:
            print(f"  In-sample IC: insufficient (n={bucket['is_ic_sample_count']})")
        else:
            print(
                f"  In-sample IC: {_num(bucket.get('is_rank_ic'))} "
                f"(n={bucket['is_ic_sample_count']})"
            )
        print(f"  is_sample_count: {_num(bucket.get('is_sample_count'))}")
        print(
            "  Note: in-sample statistics describe the time-series front-half "
            "(pre-split) segment; they are not a fitted-model in-sample fit."
        )
        print("Split:")
        for key in ("purged_count", "exit_at_unknown_count", "oos_split_at"):
            print(f"  {key}: {_num(bucket.get(key))}")
        if bucket.get("oos_notes"):
            print("OOS notes: " + "; ".join(bucket["oos_notes"]))
        if bucket.get("notes"):
            print("Notes: " + "; ".join(bucket["notes"]))
    for note in report.get("notes", []):
        print(f"Note: {note}")
    for source in report.get("provenance", []):
        print(f"Provenance: {source}")
