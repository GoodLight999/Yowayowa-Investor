from __future__ import annotations

from typing import Any

import httpx
import typer
from rich import print
from rich.table import Table

from yowayowa.cli import _client

app = typer.Typer(no_args_is_help=True, help="Record and review investment hypotheses.")


def _request(client: httpx.Client, method: str, path: str, **kwargs: Any) -> Any:
    response = getattr(client, method)(path, **kwargs)
    if response.status_code >= 400:
        try:
            detail = str(response.json().get("detail", ""))
        except Exception:
            detail = response.text[:200]
        print(f"HTTP {response.status_code}: {detail}")
        raise typer.Exit(code=1)
    return response.json()


@app.command("add")
def add_hypothesis(
    hypothesis: str = typer.Option(..., help="Investment hypothesis to record"),
    criteria: list[str] = typer.Option(
        [], "--criteria", help="反証条件（繰り返し指定・1件以上必須）"
    ),
    evidence_url: list[str] = typer.Option(
        [], "--evidence-url", help="根拠URL（繰り返し指定・1件以上必須）"
    ),
    symbol: str | None = typer.Option(None),
    provider: str | None = typer.Option(None, help="全 evidence link に適用"),
    source: str | None = typer.Option(None, help="全 evidence link に適用"),
    retrieved_at: str | None = typer.Option(None, help="ISO8601。全 evidence link に適用"),
    as_of: str | None = typer.Option(None, help="全 evidence link に適用"),
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    """Record a hypothesis only; this command never executes or changes orders."""
    if not hypothesis.strip():
        raise typer.BadParameter("hypothesis must not be blank")
    if not criteria:
        raise typer.BadParameter("at least one --criteria is required")
    if not evidence_url:
        raise typer.BadParameter("at least one --evidence-url is required")
    links = []
    for url in evidence_url:
        link: dict[str, str] = {"source_url": url}
        for key, value in (
            ("provider", provider),
            ("source", source),
            ("retrieved_at", retrieved_at),
            ("as_of", as_of),
        ):
            if value is not None:
                link[key] = value
        links.append(link)
    body: dict[str, Any] = {
        "hypothesis": hypothesis,
        "falsification_criteria": criteria,
        "evidence_links": links,
    }
    if symbol is not None:
        body["symbol"] = symbol
    with _client(base_url, token) as client:
        result = _request(client, "post", "/v1/hypotheses", json=body)
    print(f"Saved hypothesis {result['id']}")


@app.command("list")
def list_hypotheses_cli(
    symbol: str | None = typer.Option(None),
    limit: int = typer.Option(50, min=1, max=200),
    offset: int = typer.Option(0, min=0, max=100000),
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if symbol is not None:
        params["symbol"] = symbol
    with _client(base_url, token) as client:
        rows = _request(client, "get", "/v1/hypotheses", params=params)
    if not rows:
        print("No hypotheses")
        return
    table = Table("ID", "Symbol", "Created", "Hypothesis")
    for row in rows:
        table.add_row(
            str(row["id"]),
            row.get("symbol") or "—",
            row.get("provenance", {}).get("created_at", "—"),
            row["hypothesis"],
        )
    print(table)


@app.command("show")
def show_hypothesis(
    hypothesis_id: int = typer.Argument(..., min=1),
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    with _client(base_url, token) as client:
        item = _request(client, "get", f"/v1/hypotheses/{hypothesis_id}")
    print(item["hypothesis"])
    print(f"Symbol: {item.get('symbol') or '—'}")
    print("Falsification criteria:")
    for criterion in item["falsification_criteria"]:
        print(f"- {criterion}")
    provenance = item["provenance"]
    print(f"Created: {provenance['created_at']}")
    table = Table("Source URL", "Provider", "Source", "Retrieved at", "As of")
    for link in provenance["evidence_links"]:
        table.add_row(
            *(
                str(link.get(key) or "—")
                for key in ("source_url", "provider", "source", "retrieved_at", "as_of")
            )
        )
    print(table)
