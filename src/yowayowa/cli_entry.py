from __future__ import annotations

from datetime import datetime, timedelta

import typer
from rich import print
from rich.table import Table

from yowayowa.chart_cli import app as chart_app
from yowayowa.cli import _client, _percent, app, portfolio_app
from yowayowa.edinet_cli import app as edinet_app
from yowayowa.event_cli import app as events_app
from yowayowa.institutional_cli import app as institutional_app
from yowayowa.license_cli import app as license_app
from yowayowa.macro_cli import app as macro_app
from yowayowa.preset_cli import app as preset_app
from yowayowa.rate_cli import app as rate_app

app.add_typer(events_app, name="events")
app.add_typer(chart_app, name="chart")
app.add_typer(rate_app, name="rates")
app.add_typer(institutional_app, name="13f")
app.add_typer(edinet_app, name="edinet")
app.add_typer(preset_app, name="preset")
app.add_typer(license_app, name="license")
app.add_typer(macro_app, name="macro")


@app.command("news-saved")
def news_saved(
    scope: str = typer.Option("all", help="all, watchlist, or portfolio"),
    scope_id: int | None = typer.Option(None, min=1),
    limit: int = typer.Option(60, min=1, max=200),
    per_symbol_limit: int = typer.Option(6, min=1, max=20),
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    normalized_scope = scope.strip().lower()
    if normalized_scope not in {"all", "watchlist", "portfolio"}:
        raise typer.BadParameter("scope must be all, watchlist, or portfolio")
    if normalized_scope == "all" and scope_id is not None:
        raise typer.BadParameter("--scope-id is not valid with --scope all")
    if normalized_scope != "all" and scope_id is None:
        raise typer.BadParameter("--scope-id is required for watchlist or portfolio scope")

    params: dict[str, str | int] = {
        "scope": normalized_scope,
        "limit": limit,
        "per_symbol_limit": per_symbol_limit,
    }
    if scope_id is not None:
        params["scope_id"] = scope_id
    with _client(base_url, token) as client:
        payload = client.get("/v1/news/saved", params=params).raise_for_status().json()

    table = Table("Published", "Symbols", "Publisher", "Headline")
    for item in payload["items"]:
        table.add_row(
            item.get("published_at") or "—",
            ", ".join(item["symbols"]),
            item.get("publisher") or "—",
            item["title"],
        )
    print(table)
    if payload["unavailable_symbols"]:
        print(f"Unavailable: {', '.join(payload['unavailable_symbols'])}")


@app.command("calendar-saved")
def calendar_saved(
    start: datetime = typer.Option(default_factory=datetime.now, formats=["%Y-%m-%d"]),
    end: datetime | None = typer.Option(None, formats=["%Y-%m-%d"]),
    scope: str = typer.Option("all", help="all, watchlist, or portfolio"),
    scope_id: int | None = typer.Option(None, min=1),
    types: str = typer.Option("earnings,dividend"),
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    normalized_scope = scope.strip().lower()
    if normalized_scope not in {"all", "watchlist", "portfolio"}:
        raise typer.BadParameter("scope must be all, watchlist, or portfolio")
    if normalized_scope == "all" and scope_id is not None:
        raise typer.BadParameter("--scope-id is not valid with --scope all")
    if normalized_scope != "all" and scope_id is None:
        raise typer.BadParameter("--scope-id is required for watchlist or portfolio scope")

    start_date = start.date()
    end_date = end.date() if end is not None else start_date + timedelta(days=14)
    params: dict[str, str | int] = {
        "start": start_date.isoformat(),
        "end": end_date.isoformat(),
        "scope": normalized_scope,
        "types": types,
    }
    if scope_id is not None:
        params["scope_id"] = scope_id
    with _client(base_url, token) as client:
        payload = client.get("/v1/calendar/tracked", params=params).raise_for_status().json()

    table = Table("When", "Type", "Symbol", "Event")
    for item in payload["events"]:
        when = item["starts_at"]
        if item.get("ends_at"):
            when = f"{when} -> {item['ends_at']}"
        table.add_row(when, item["event_type"], item["symbol"], item["title"])
    print(table)
    if payload["unavailable_symbols"]:
        print(f"Unavailable: {', '.join(payload['unavailable_symbols'])}")


@portfolio_app.command("risk")
def portfolio_risk(
    portfolio_id: int,
    benchmark: str = "^GSPC",
    period: str = "1y",
    risk_free_rate: float = typer.Option(0.0, help="Annual risk-free rate as a decimal"),
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    with _client(base_url, token) as client:
        payload = (
            client.get(
                f"/v1/portfolios/{portfolio_id}/risk",
                params={
                    "benchmark": benchmark,
                    "period": period,
                    "risk_free_rate": risk_free_rate,
                },
            )
            .raise_for_status()
            .json()
        )

    print(
        f"[bold]{payload['name']}[/bold] · {payload['period']} · "
        f"coverage {_percent(payload['covered_gross_weight'])} · "
        f"vol {_percent(payload.get('annualized_volatility'))} · "
        f"max DD {_percent(payload.get('max_drawdown'))} · "
        f"beta {payload.get('beta') if payload.get('beta') is not None else '—'}"
    )
    summary = Table("Metric", "Value")
    summary.add_row("Annualized return", _percent(payload.get("annualized_return")))
    summary.add_row("Annualized volatility", _percent(payload.get("annualized_volatility")))
    summary.add_row(
        "Sharpe ratio",
        "—" if payload.get("sharpe_ratio") is None else f"{payload['sharpe_ratio']:.3f}",
    )
    summary.add_row("Maximum drawdown", _percent(payload.get("max_drawdown")))
    summary.add_row("Historical VaR 95% (1D)", _percent(payload.get("value_at_risk_95")))
    summary.add_row(
        "Expected shortfall 95% (1D)",
        _percent(payload.get("expected_shortfall_95")),
    )
    summary.add_row("Benchmark", payload["benchmark"])
    summary.add_row(
        "Benchmark correlation",
        (
            "—"
            if payload.get("benchmark_correlation") is None
            else f"{payload['benchmark_correlation']:.3f}"
        ),
    )
    print(summary)

    positions = Table(
        "Symbol",
        "Signed weight",
        "Volatility",
        "Beta",
        "Correlation",
        "Risk contribution",
    )
    for item in payload["positions"]:
        positions.add_row(
            item["symbol"],
            _percent(item.get("signed_weight")),
            _percent(item.get("volatility_annualized")),
            "—" if item.get("beta") is None else f"{item['beta']:.3f}",
            (
                "—"
                if item.get("correlation_to_portfolio") is None
                else f"{item['correlation_to_portfolio']:.3f}"
            ),
            _percent(item.get("variance_contribution")),
        )
    print(positions)
    if payload["unavailable_symbols"]:
        print(f"Unavailable: {', '.join(payload['unavailable_symbols'])}")


__all__ = ["app"]
