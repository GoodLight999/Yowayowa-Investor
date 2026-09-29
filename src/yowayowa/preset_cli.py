from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import typer
from rich import print
from rich.table import Table

from yowayowa.cli import _client
from yowayowa.config import get_settings
from yowayowa.db import get_session
from yowayowa.services.strategy_presets import (
    KIYOHARA_GLOBAL_ID,
    get_builtin_strategy,
    list_builtin_strategies,
)
from yowayowa.services.strategy_snapshot_service import evaluate_and_record_builtin
from yowayowa.services.strategy_tracking import list_strategy_snapshots
from yowayowa.strategy_models import StrategyCandidateEvaluation

app = typer.Typer(no_args_is_help=True, help="Manage saved and built-in research presets.")

_IMPLEMENTED_STRATEGY_IDS = (KIYOHARA_GLOBAL_ID,)


def _kind(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in {"chart", "screener", "compare"}:
        raise typer.BadParameter("kind must be chart, screener, or compare")
    return normalized


def _first(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value is not None:
            return value
    return None


def _ensure_personal_mode() -> None:
    settings = get_settings()
    if settings.mode != "personal":
        raise typer.BadParameter(
            "Built-in strategy snapshots include personal-only screener data and "
            f"are not available in {settings.mode} mode"
        )


def _print_evaluation_table(evaluations: list[StrategyCandidateEvaluation]) -> None:
    table = Table(
        "Symbol",
        "Research priority",
        "Market cap",
        "P/E",
        "Yowayowa NCR",
        "Kiyohara NCR",
        "Cash-neutral P/E",
        "Revenue YoY",
        "FCF",
        "Basis",
    )
    for item in evaluations:
        conservative = item.yowayowa_conservative_net_cash_ratio
        conservative_text = "—" if conservative is None else f"{conservative:.2f}x"
        ratio = item.net_cash_ratio
        ratio_text = "—" if ratio is None else f"{ratio:.2f}x"
        if ratio is not None and item.net_cash_ratio_is_lower_bound:
            ratio_text = f">={ratio:.2f}x"
        cnpe = item.cash_neutral_pe
        cnpe_text = "—" if cnpe is None else f"{cnpe:.2f}x"
        if cnpe is not None and item.cash_neutral_pe_is_upper_bound:
            cnpe_text = f"<={cnpe:.2f}x"
        growth = item.revenue_growth_yoy
        fcf = item.free_cash_flow
        priority = item.research_priority
        priority_score = priority.score if priority is not None else None
        priority_text = "—" if priority_score is None else f"{priority_score:.0f}/100"
        table.add_row(
            item.symbol,
            priority_text,
            f"{item.market_cap:.6g}",
            "—" if item.pe_ratio is None else f"{item.pe_ratio:.2f}x",
            conservative_text,
            ratio_text,
            cnpe_text,
            "—" if growth is None else f"{growth:+.1%}",
            "—" if fcf is None else f"{fcf:.6g}",
            "floor" if item.net_cash_ratio_is_lower_bound else "formula",
        )
    print(table)


def _print_error_details(errors: dict[str, str], supplement_errors: dict[str, str]) -> None:
    if errors:
        print(f"Unavailable: {', '.join(errors)}")
        for symbol, message in errors.items():
            print(f"  {symbol}: {message}")
    if supplement_errors:
        print(f"EDINET supplement unavailable: {', '.join(supplement_errors)}")
        for symbol, message in supplement_errors.items():
            print(f"  {symbol}: {message}")


@app.command("list")
def list_presets(
    kind: str | None = typer.Option(None, help="chart, screener, or compare"),
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    params = {"kind": _kind(kind)} if kind else None
    with _client(base_url, token) as client:
        payload = client.get("/v1/research-presets", params=params).raise_for_status().json()
    table = Table("ID", "Kind", "Name", "Updated")
    for item in payload:
        table.add_row(str(item["id"]), item["kind"], item["name"], item["updated_at"])
    print(table)


@app.command("show")
def show_preset(
    preset_id: int,
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    with _client(base_url, token) as client:
        payload = client.get(f"/v1/research-presets/{preset_id}").raise_for_status().json()
    print(json.dumps(payload, ensure_ascii=False, indent=2))


@app.command("save")
def save_preset(
    name: str,
    kind: str = typer.Option(..., help="chart, screener, or compare"),
    file: Path = typer.Option(..., exists=True, dir_okay=False, readable=True),
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    payload = json.loads(file.read_text(encoding="utf-8"))
    request = {"name": name, "kind": _kind(kind), "payload": payload}
    with _client(base_url, token) as client:
        saved = client.post("/v1/research-presets", json=request).raise_for_status().json()
    print(f"Saved preset {saved['id']}: {saved['name']} ({saved['kind']})")


@app.command("update")
def update_preset(
    preset_id: int,
    name: str,
    kind: str = typer.Option(..., help="chart, screener, or compare"),
    file: Path = typer.Option(..., exists=True, dir_okay=False, readable=True),
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    payload = json.loads(file.read_text(encoding="utf-8"))
    request = {"name": name, "kind": _kind(kind), "payload": payload}
    with _client(base_url, token) as client:
        saved = (
            client.put(f"/v1/research-presets/{preset_id}", json=request).raise_for_status().json()
        )
    print(f"Updated preset {saved['id']}: {saved['name']} ({saved['kind']})")


@app.command("remove")
def remove_preset(
    preset_id: int,
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    with _client(base_url, token) as client:
        client.delete(f"/v1/research-presets/{preset_id}").raise_for_status()
    print(f"Removed preset {preset_id}")


@app.command("builtins")
def list_builtins(
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    with _client(base_url, token) as client:
        payload = client.get("/v1/strategy-presets").raise_for_status().json()
    table = Table("ID", "Name", "Default region")
    for item in payload:
        table.add_row(
            item["id"],
            item["name_en"],
            item["default_region"].upper(),
        )
    print(table)


def _run_builtin_local(
    strategy_id: str,
    region: str | None,
    size: int,
    edinet_key: str | None,
) -> None:
    _ensure_personal_mode()
    try:
        strategy = get_builtin_strategy(strategy_id)
    except LookupError as exc:
        raise typer.BadParameter(str(exc)) from exc
    resolved_region = (region or strategy.default_region).strip().lower()
    session = get_session()
    try:
        today_utc = datetime.now(UTC).date()
        recorded_today = {
            row.symbol
            for row in list_strategy_snapshots(
                session, strategy_id=strategy_id, region=resolved_region
            )
            if row.captured_at.date() == today_utc
        }
        outcome = evaluate_and_record_builtin(
            session,
            strategy_id,
            resolved_region,
            size,
            edinet_key=edinet_key,
        )
    finally:
        session.close()
    if not outcome.evaluations:
        print("No candidates with market-cap data were returned.")
    _print_evaluation_table(outcome.evaluations)
    expected_rows = sum(1 for item in outcome.evaluations if item.research_priority is not None)
    new_rows = sum(1 for item in outcome.snapshots if item.symbol not in recorded_today)
    duplicate_skips = len(outcome.snapshots) - new_rows
    not_recorded = expected_rows - len(outcome.snapshots)
    summary = f"Recorded {new_rows} new snapshot row(s) (duplicates skipped: {duplicate_skips}"
    if not_recorded:
        summary += f", not recorded (no research priority): {not_recorded}"
    summary += f") for {strategy_id} in {resolved_region}."
    print(summary)
    _print_error_details(outcome.errors, outcome.supplement_errors)


def _run_builtin_via_api(
    strategy_id: str,
    region: str | None,
    size: int,
    edinet_key: str | None,
    base_url: str,
    token: str | None,
) -> None:
    with _client(base_url, token) as client:
        strategy = client.get(f"/v1/strategy-presets/{strategy_id}").raise_for_status().json()
        resolved_region = (region or strategy["default_region"]).strip().lower()
        discovery = dict(strategy["discovery"])
        discovery["filters"] = [
            {"field": "region", "operator": "is-in", "value": [resolved_region]},
            *discovery.get("filters", []),
        ]
        discovery["size"] = size
        discovery["offset"] = 0
        screen = client.post("/v1/discover/screen", json=discovery).raise_for_status().json()

        candidates = []
        for row in screen.get("quotes", []):
            symbol = str(row.get("symbol") or "").strip()
            market_cap = _first(row, "marketCap", "intradaymarketcap")
            if not symbol or not isinstance(market_cap, (int, float)) or market_cap <= 0:
                continue
            pe_ratio = _first(row, "trailingPE", "peratio.lasttwelvemonths")
            candidates.append(
                {
                    "symbol": symbol,
                    "market_cap": float(market_cap),
                    "pe_ratio": float(pe_ratio) if isinstance(pe_ratio, (int, float)) else None,
                }
            )
        if not candidates:
            print("No candidates with market-cap data were returned.")
            raise typer.Exit(code=0)
        headers = {"X-Yowayowa-EDINET-Key": edinet_key} if edinet_key else None
        evaluation = (
            client.post(
                f"/v1/strategy-presets/{strategy_id}/evaluate",
                json={
                    "candidates": candidates,
                    "region": resolved_region,
                    "record": True,
                },
                headers=headers,
            )
            .raise_for_status()
            .json()
        )

    evaluations = [
        StrategyCandidateEvaluation.model_validate(item) for item in evaluation["evaluations"]
    ]
    _print_evaluation_table(evaluations)
    _print_error_details(
        evaluation.get("errors", {}),
        evaluation.get("supplement_errors", {}),
    )


@app.command("run-builtin")
def run_builtin(
    strategy_id: str,
    region: str | None = typer.Option(None, help="Yahoo screener region such as jp or us"),
    size: int = typer.Option(25, min=1, max=50),
    edinet_key: str | None = typer.Option(None, envvar="YOWAYOWA_EDINET_API_KEY", hidden=True),
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
    via_api: bool = typer.Option(
        False,
        "--via-api",
        help="Run through the HTTP API instead of the local database (legacy path).",
    ),
) -> None:
    """Evaluate one built-in strategy and record today's snapshots.

    The default path calls the shared service directly against the local
    database; no HTTP server is required. ``--via-api`` keeps the legacy HTTP
    route for backward compatibility.
    """
    if via_api:
        _run_builtin_via_api(strategy_id, region, size, edinet_key, base_url, token)
        return
    _run_builtin_local(strategy_id, region, size, edinet_key)


@app.command("snapshot-builtins")
def snapshot_builtins(
    region: str | None = typer.Option(None, help="Override each preset's default region"),
    size: int = typer.Option(25, min=1, max=50),
    edinet_key: str | None = typer.Option(None, envvar="YOWAYOWA_EDINET_API_KEY", hidden=True),
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
    via_api: bool = typer.Option(
        False,
        "--via-api",
        help="Run through the HTTP API instead of the local database (legacy path).",
    ),
) -> None:
    """Evaluate all supported built-in strategies and record today's snapshots.

    Intended as the repeatable entrypoint for an operator-managed scheduler;
    this command does not install or configure a scheduler. The default path
    calls the shared service directly against the local database; ``--via-api``
    keeps the legacy HTTP route for backward compatibility.
    """
    supported: list[dict[str, Any]]
    if via_api:
        with _client(base_url, token) as client:
            strategies = client.get("/v1/strategy-presets").raise_for_status().json()
        supported = [item for item in strategies if item["id"] in _IMPLEMENTED_STRATEGY_IDS]
    else:
        _ensure_personal_mode()
        supported = [
            {"id": item.id}
            for item in list_builtin_strategies()
            if item.id in _IMPLEMENTED_STRATEGY_IDS
        ]
    if not supported:
        raise typer.BadParameter("No implemented built-in strategy is available for snapshotting")
    print(f"Recording strategy snapshots for {len(supported)} implemented preset(s).")
    for strategy in supported:
        run_builtin(
            strategy_id=strategy["id"],
            region=region,
            size=size,
            edinet_key=edinet_key,
            base_url=base_url,
            token=token,
            via_api=via_api,
        )
