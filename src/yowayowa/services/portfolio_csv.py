from __future__ import annotations

import csv
import io
from decimal import Decimal, InvalidOperation

from yowayowa.domain import PositionUpsert

REQUIRED_COLUMNS = {"symbol", "quantity", "average_cost", "currency"}
MAX_POSITIONS = 1000


def parse_portfolio_csv(text: str) -> list[PositionUpsert]:
    reader = csv.DictReader(io.StringIO(text.removeprefix("\ufeff")))
    fieldnames = [str(name).strip().lower() for name in (reader.fieldnames or [])]
    if not REQUIRED_COLUMNS.issubset(fieldnames):
        raise ValueError("CSV must contain symbol, quantity, average_cost, currency columns")

    positions: list[PositionUpsert] = []
    for raw_row in reader:
        row = {str(key).strip().lower(): (value or "").strip() for key, value in raw_row.items()}
        if not any(row.values()):
            continue
        try:
            quantity = Decimal(row["quantity"])
            average_cost = Decimal(row["average_cost"]) if row["average_cost"] else None
        except (InvalidOperation, KeyError) as exc:
            raise ValueError("Portfolio CSV contains an invalid decimal value") from exc
        positions.append(
            PositionUpsert(
                symbol=row["symbol"],
                quantity=quantity,
                average_cost=average_cost,
                currency=row["currency"],
            )
        )
        if len(positions) > MAX_POSITIONS:
            raise ValueError(f"Portfolio CSV is limited to {MAX_POSITIONS} positions")
    if not positions:
        raise ValueError("Portfolio CSV contains no positions")
    return positions
