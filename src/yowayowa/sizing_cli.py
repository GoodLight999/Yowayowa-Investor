"""Operator CLI for quote-backed sizing ideas (P5 R5).

``yowayowa sizing-idea SYMBOL --stop-price VALUE --lot-size N`` fetches the
latest provider quote through ``POST /v1/sizing/quote-idea`` and prints the
resulting ``PortfolioSizingIdea`` as exactly one JSON line on stdout. Decimal
precision is preserved by passing ``--stop-price`` through as a string.

HTTP failures escape as ``httpx.HTTPError``: the ``cli_shim`` boundary (the
existing safe path) converts them into one fixed operator-safe sentence with
a non-zero exit, and no success-shaped JSON is printed.
"""

from __future__ import annotations

import json

import httpx
import typer


def _api_client(base_url: str, token: str | None) -> httpx.Client:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx.Client(base_url=base_url.rstrip("/"), headers=headers, timeout=30)


def sizing_idea(
    symbol: str = typer.Argument(..., help="Market symbol, e.g. 7203.T or AAPL."),
    stop_price: str = typer.Option(
        ...,
        "--stop-price",
        help="Caller-supplied stop assumption, decimal string (never recommended here).",
    ),
    lot_size: int = typer.Option(
        ...,
        "--lot-size",
        min=1,
        max=1_000_000,
        help="Caller-supplied whole-unit lot size (never inferred here).",
    ),
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    """Build one sizing idea from the latest provider quote. No orders."""

    payload = {"symbol": symbol, "stop_price": stop_price, "lot_size": lot_size}
    with _api_client(base_url, token) as client:
        response = client.post("/v1/sizing/quote-idea", json=payload)
        response.raise_for_status()  # failures: fixed cli_shim sentence, exit 1
        idea = response.json()
    typer.echo(json.dumps(idea, ensure_ascii=False))
