from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import typer
from rich import print
from rich.table import Table

app = typer.Typer(help="Yowayowa-Investor CLI: every meaningful GUI operation has an API/CLI path.")
watchlist_app = typer.Typer(help="Manage watchlists")
portfolio_app = typer.Typer(help="Manage portfolios")
alerts_app = typer.Typer(help="Manage price alerts")
app.add_typer(watchlist_app, name="watchlist")
app.add_typer(portfolio_app, name="portfolio")
app.add_typer(alerts_app, name="alerts")


def _client(base_url: str, token: str | None) -> httpx.Client:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx.Client(base_url=base_url.rstrip("/"), headers=headers, timeout=30)


def _percent(value: float | None) -> str:
    return "—" if value is None else f"{value:+.2%}"


@app.command()
def search(
    query: str,
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    with _client(base_url, token) as client:
        rows = client.get("/v1/instruments/search", params={"q": query}).raise_for_status().json()
    table = Table("Symbol", "Name", "Type", "Exchange", "Currency", "CIK")
    for row in rows:
        table.add_row(
            row["symbol"],
            row["name"],
            row.get("instrument_type") or "",
            row.get("exchange") or "",
            row.get("currency") or "",
            row.get("cik") or "",
        )
    print(table)


@app.command()
def fundamentals(
    symbol: str,
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    with _client(base_url, token) as client:
        payload = client.get(f"/v1/fundamentals/{symbol}").raise_for_status().json()
    table = Table("Metric", "Latest", "Period")
    for metric in payload["metrics"].values():
        latest = metric["points"][-1]
        table.add_row(metric["label"], str(latest["value"]), latest["period_end"])
    print(table)


@app.command()
def valuation(
    symbol: str,
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    with _client(base_url, token) as client:
        payload = client.get(f"/v1/valuation/{symbol}").raise_for_status().json()
    table = Table("Metric", "Value")
    table.add_row("Price", f"{payload['price']:.6g}")
    market_cap = payload.get("market_cap")
    table.add_row("Market cap", "—" if market_cap is None else f"{market_cap:.6g}")
    for key, value in payload["metrics"].items():
        table.add_row(key, "—" if value is None else f"{value:.6g}")
    print(table)
    print(f"Annual basis: {payload.get('annual_period_end') or 'unavailable'}")


@app.command()
def history(
    symbol: str,
    period: str = "1y",
    interval: str = "1d",
    indicators: str = "sma20,rsi14",
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    with _client(base_url, token) as client:
        payload = (
            client.get(
                f"/v1/markets/{symbol}/history",
                params={"period": period, "interval": interval, "indicators": indicators},
            )
            .raise_for_status()
            .json()
        )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


@app.command()
def market(
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    with _client(base_url, token) as client:
        payload = client.get("/v1/markets/overview").raise_for_status().json()
    table = Table("Asset", "Value", "1D", "1M", "3M", "1Y")
    for item in payload["items"]:
        table.add_row(
            f"{item['label']} ({item['symbol']})",
            f"{item['value']:.4g} {item['unit']}",
            _percent(item.get("change_1d")),
            _percent(item.get("change_1m")),
            _percent(item.get("change_3m")),
            _percent(item.get("change_1y")),
        )
    print(table)
    if payload["unavailable_symbols"]:
        print(f"Unavailable: {', '.join(payload['unavailable_symbols'])}")


@app.command()
def quotes(
    symbols: list[str],
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    if not symbols:
        raise typer.BadParameter("quotes requires at least one symbol")
    with _client(base_url, token) as client:
        payload = (
            client.get("/v1/markets/quotes", params={"symbols": ",".join(symbols)})
            .raise_for_status()
            .json()
        )
    table = Table("Symbol", "Price", "Previous close", "1D", "As of")
    for raw in symbols:
        symbol = raw.upper()
        quote = payload["quotes"].get(symbol)
        if quote is None:
            table.add_row(symbol, "—", "—", "—", "—")
            continue
        previous = quote.get("previous_close")
        change = None if previous in (None, 0) else quote["price"] / previous - 1
        table.add_row(
            symbol,
            f"{quote['price']:.6g}",
            "—" if previous is None else f"{previous:.6g}",
            _percent(change),
            quote["as_of"],
        )
    print(table)
    if payload["unavailable_symbols"]:
        print(f"Unavailable: {', '.join(payload['unavailable_symbols'])}")


@app.command("news")
def news_command(
    query: str,
    limit: int = 12,
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    with _client(base_url, token) as client:
        payload = client.get(f"/v1/news/{query}", params={"limit": limit}).raise_for_status().json()
    table = Table("Published", "Publisher", "Title", "URL")
    for item in payload["items"]:
        table.add_row(
            item.get("published_at") or "—",
            item.get("publisher") or "—",
            item["title"],
            item.get("url") or "—",
        )
    print(table)


@app.command("calendar")
def calendar_command(
    start: datetime = typer.Option(default_factory=datetime.now, formats=["%Y-%m-%d"]),
    end: datetime | None = typer.Option(None, formats=["%Y-%m-%d"]),
    types: str = "earnings,economic,ipo,split",
    symbol: str | None = None,
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    start_date = start.date()
    resolved_end = end.date() if end is not None else start_date + timedelta(days=7)
    params = {"start": start_date.isoformat(), "end": resolved_end.isoformat(), "types": types}
    if symbol:
        params["symbol"] = symbol.upper()
    with _client(base_url, token) as client:
        payload = client.get("/v1/calendar", params=params).raise_for_status().json()
    table = Table("When", "Type", "Symbol", "Event")
    for item in payload["events"]:
        table.add_row(
            str(item.get("starts_at") or "—"),
            item["event_type"],
            item.get("symbol") or "—",
            item["title"],
        )
    print(table)


@watchlist_app.command("list")
def watchlist_list(
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    with _client(base_url, token) as client:
        print(client.get("/v1/watchlists").raise_for_status().json())


@watchlist_app.command("create")
def watchlist_create(
    name: str,
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    with _client(base_url, token) as client:
        print(client.post("/v1/watchlists", json={"name": name}).raise_for_status().json())


@watchlist_app.command("add")
def watchlist_add(
    watchlist_id: int,
    symbols: list[str],
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    with _client(base_url, token) as client:
        payload = (
            client.post(f"/v1/watchlists/{watchlist_id}/symbols", json=symbols)
            .raise_for_status()
            .json()
        )
    print(payload)


@watchlist_app.command("remove")
def watchlist_remove(
    watchlist_id: int,
    symbols: list[str],
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    with _client(base_url, token) as client:
        payload = None
        for symbol in symbols:
            payload = (
                client.delete(f"/v1/watchlists/{watchlist_id}/symbols/{symbol}")
                .raise_for_status()
                .json()
            )
    print(payload)


@portfolio_app.command("list")
def portfolio_list(
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    with _client(base_url, token) as client:
        print(client.get("/v1/portfolios").raise_for_status().json())


@portfolio_app.command("create")
def portfolio_create(
    name: str,
    base_currency: str = "USD",
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    with _client(base_url, token) as client:
        print(
            client.post(
                "/v1/portfolios",
                json={"name": name, "base_currency": base_currency.upper()},
            )
            .raise_for_status()
            .json()
        )


@portfolio_app.command("position")
def portfolio_position(
    portfolio_id: int,
    symbol: str,
    quantity: str,
    average_cost: str | None = None,
    currency: str = "USD",
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    body = {
        "symbol": symbol.upper(),
        "quantity": quantity,
        "average_cost": average_cost,
        "currency": currency.upper(),
    }
    with _client(base_url, token) as client:
        print(
            client.put(f"/v1/portfolios/{portfolio_id}/positions", json=body)
            .raise_for_status()
            .json()
        )


@portfolio_app.command("remove")
def portfolio_remove(
    portfolio_id: int,
    symbol: str,
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    with _client(base_url, token) as client:
        print(
            client.delete(f"/v1/portfolios/{portfolio_id}/positions/{symbol.upper()}")
            .raise_for_status()
            .json()
        )


@portfolio_app.command("value")
def portfolio_value(
    portfolio_id: int,
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    with _client(base_url, token) as client:
        payload = client.get(f"/v1/portfolios/{portfolio_id}/analytics").raise_for_status().json()
    currency = payload["base_currency"]
    print(
        f"[bold]{payload['name']}[/bold] · net {payload['net_market_value']:.2f} {currency} · "
        f"gross {payload['gross_market_value']:.2f} {currency} · "
        f"day {payload['day_pnl']:+.2f} {currency} · "
        f"unrealized {payload['unrealized_pnl']:+.2f} {currency}"
    )
    table = Table("Symbol", "Value", "Weight", "Day P/L", "Unrealized P/L", "FX")
    for item in payload["positions"]:
        table.add_row(
            item["symbol"],
            f"{item['market_value_base']:.2f}",
            f"{item['weight']:.2%}",
            "—" if item["day_pnl_base"] is None else f"{item['day_pnl_base']:+.2f}",
            ("—" if item["unrealized_pnl_base"] is None else f"{item['unrealized_pnl_base']:+.2f}"),
            f"{item['fx_to_base']:.6g}",
        )
    print(table)
    if payload["unavailable_symbols"]:
        print(f"Unavailable: {', '.join(payload['unavailable_symbols'])}")


@portfolio_app.command("history")
def portfolio_history(
    portfolio_id: int,
    limit: int = 365,
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    with _client(base_url, token) as client:
        payload = (
            client.get(f"/v1/portfolios/{portfolio_id}/snapshots", params={"limit": limit})
            .raise_for_status()
            .json()
        )
    table = Table("Captured", "Net", "Gross", "Day P/L", "Unrealized P/L")
    for row in payload:
        table.add_row(
            row["captured_at"],
            f"{row['net_market_value']:.2f}",
            f"{row['gross_market_value']:.2f}",
            f"{row['day_pnl']:+.2f}",
            f"{row['unrealized_pnl']:+.2f}",
        )
    print(table)


@portfolio_app.command("import-csv")
def portfolio_import_csv(
    portfolio_id: int,
    path: Path,
    replace: bool = False,
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    positions = []
    for row in rows:
        try:
            symbol = row["symbol"].strip().upper()
            quantity = row["quantity"].strip()
            currency = row["currency"].strip().upper()
        except (KeyError, AttributeError) as exc:
            raise typer.BadParameter(
                "CSV must contain symbol, quantity, average_cost, currency columns"
            ) from exc
        average_cost = (row.get("average_cost") or "").strip() or None
        positions.append(
            {
                "symbol": symbol,
                "quantity": quantity,
                "average_cost": average_cost,
                "currency": currency,
            }
        )
    if not positions:
        raise typer.BadParameter("CSV contains no positions")
    with _client(base_url, token) as client:
        payload = (
            client.put(
                f"/v1/portfolios/{portfolio_id}/positions/bulk",
                json={"positions": positions, "replace": replace},
            )
            .raise_for_status()
            .json()
        )
    print(payload)


@portfolio_app.command("export-csv")
def portfolio_export_csv(
    portfolio_id: int,
    output: Path,
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    with _client(base_url, token) as client:
        response = client.get(f"/v1/portfolios/{portfolio_id}/export.csv").raise_for_status()
    output.write_text(response.text, encoding="utf-8")
    print(f"Wrote {output}")


@alerts_app.command("list")
def alerts_list(
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    with _client(base_url, token) as client:
        print(client.get("/v1/alerts").raise_for_status().json())


@alerts_app.command("add")
def alerts_add(
    symbol: str,
    target: str,
    operator: str = "above",
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    with _client(base_url, token) as client:
        print(
            client.post(
                "/v1/alerts",
                json={"symbol": symbol.upper(), "target": target, "operator": operator},
            )
            .raise_for_status()
            .json()
        )


@alerts_app.command("remove")
def alerts_remove(
    alert_id: int,
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    with _client(base_url, token) as client:
        client.delete(f"/v1/alerts/{alert_id}").raise_for_status()
    print(f"Removed alert {alert_id}")


@alerts_app.command("check")
def alerts_check(
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    with _client(base_url, token) as client:
        payload = client.post("/v1/alerts/evaluate").raise_for_status().json()
    print(payload)


@app.command()
def screen(
    symbols: list[str],
    filter: list[str] = typer.Option(
        [],
        "--filter",
        help="metric:operator:value, e.g. operating_margin:gt:0",
    ),
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    rules = []
    for raw in filter:
        try:
            metric, operator, value = raw.split(":", 2)
            rules.append({"metric": metric, "operator": operator, "value": float(value)})
        except ValueError as exc:
            raise typer.BadParameter(
                f"Invalid --filter {raw!r}; expected metric:operator:value"
            ) from exc
    with _client(base_url, token) as client:
        payload = (
            client.post(
                "/v1/screen",
                json={"symbols": [symbol.upper() for symbol in symbols], "filters": rules},
            )
            .raise_for_status()
            .json()
        )
    table = Table("Symbol", "Matched", "Failures", "Metrics")
    for row in payload["rows"]:
        table.add_row(
            row["symbol"],
            "yes" if row["matched"] else "no",
            ", ".join(row["failures"]),
            json.dumps(row["metrics"], ensure_ascii=False),
        )
    print(table)


@app.command()
def compare(
    symbols: list[str],
    metric: list[str] = typer.Option([], "--metric", help="Comparison metric key"),
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    if len(symbols) < 2:
        raise typer.BadParameter("compare requires at least two symbols")
    with _client(base_url, token) as client:
        payload = (
            client.post(
                "/v1/compare",
                json={"symbols": [symbol.upper() for symbol in symbols], "metrics": metric},
            )
            .raise_for_status()
            .json()
        )
    table = Table("Metric", *[row["symbol"] for row in payload["rows"]])
    for descriptor in payload["metrics"]:
        key = descriptor["key"]
        table.add_row(
            descriptor["label"],
            *[
                "—" if row["metrics"].get(key) is None else f"{row['metrics'][key]:.4g}"
                for row in payload["rows"]
            ],
        )
    print(table)


@app.command()
def plan(
    text: str,
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    with _client(base_url, token) as client:
        payload = (
            client.post("/v1/operations/plan", params={"text": text}).raise_for_status().json()
        )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


@app.command()
def export_openapi(output: Path = Path("openapi.json")) -> None:
    from yowayowa.api.app import app as api_app

    output.write_text(
        json.dumps(api_app.openapi(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"Wrote {output}")
