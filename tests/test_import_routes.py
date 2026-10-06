from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from yowayowa.api.deps import db_session
from yowayowa.db import Base


def test_portfolio_positions_and_csv_import_routes_remain_available(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    from yowayowa.config import get_settings

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'lifespan.sqlite'}")
    get_settings.cache_clear()
    engine = create_engine(f"sqlite:///{tmp_path / 'import-routes.sqlite'}")
    Base.metadata.create_all(engine)

    def session_override() -> Iterator[Session]:
        with Session(engine, expire_on_commit=False) as session:
            yield session

    from yowayowa.api.app import app

    app.dependency_overrides[db_session] = session_override
    try:
        with TestClient(app) as client:
            created = client.post(
                "/v1/portfolios",
                json={"name": "CSV route regression", "base_currency": "USD"},
            )
            assert created.status_code == 201, created.text
            portfolio_id = created.json()["id"]

            positioned = client.put(
                f"/v1/portfolios/{portfolio_id}/positions",
                json={"symbol": "AAPL", "quantity": "3", "average_cost": "10", "currency": "USD"},
            )
            assert positioned.status_code == 200, positioned.text

            imported = client.post(
                f"/v1/portfolios/{portfolio_id}/import.csv?replace=false",
                files={
                    "file": (
                        "positions.csv",
                        "symbol,quantity,average_cost,currency\nMSFT,4,20,USD\n",
                        "text/csv",
                    )
                },
            )
            assert imported.status_code == 200, imported.text
            holdings = {item["symbol"]: item for item in imported.json()["positions"]}
            assert set(holdings) == {"AAPL", "MSFT"}
            assert Decimal(holdings["AAPL"]["quantity"]) == Decimal("3")
            assert Decimal(holdings["MSFT"]["quantity"]) == Decimal("4")

            bulk = client.put(
                f"/v1/portfolios/{portfolio_id}/positions/bulk",
                json={
                    "positions": [
                        {"symbol": "AAPL", "quantity": "5", "average_cost": "11", "currency": "USD"}
                    ],
                    "replace": False,
                },
            )
            assert bulk.status_code == 200, bulk.text
            assert {item["symbol"] for item in bulk.json()["positions"]} == {"AAPL", "MSFT"}
            assert Decimal(
                next(item for item in bulk.json()["positions"] if item["symbol"] == "AAPL")[
                    "quantity"
                ]
            ) == Decimal("5")
    finally:
        app.dependency_overrides.pop(db_session, None)
        engine.dispose()
        get_settings.cache_clear()
