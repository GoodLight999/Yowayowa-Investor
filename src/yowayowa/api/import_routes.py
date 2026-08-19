from __future__ import annotations

import csv
import io
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from yowayowa.api.deps import db_session, require_api_token
from yowayowa.domain import Portfolio, PositionBulkUpsert, PositionUpsert
from yowayowa.services.portfolios import bulk_upsert_positions

router = APIRouter(prefix="/v1", dependencies=[Depends(require_api_token)])

_MAX_CSV_BYTES = 1_000_000
_REQUIRED_COLUMNS = {"symbol", "quantity", "average_cost", "currency"}


@router.post("/portfolios/{portfolio_id}/import.csv", response_model=Portfolio)
async def import_portfolio_csv(
    portfolio_id: int,
    file: UploadFile = File(...),
    replace: bool = Query(default=False),
    session: Session = Depends(db_session),
) -> Portfolio:
    raw = await file.read(_MAX_CSV_BYTES + 1)
    if len(raw) > _MAX_CSV_BYTES:
        raise HTTPException(status_code=413, detail="Portfolio CSV is limited to 1 MB")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=422, detail="Portfolio CSV must be UTF-8") from exc

    reader = csv.DictReader(io.StringIO(text))
    fieldnames = [str(name).strip().lower() for name in (reader.fieldnames or [])]
    if not _REQUIRED_COLUMNS.issubset(fieldnames):
        raise HTTPException(
            status_code=422,
            detail="CSV must contain symbol, quantity, average_cost, currency columns",
        )

    positions: list[PositionUpsert] = []
    try:
        for raw_row in reader:
            row = {
                str(key).strip().lower(): (value or "").strip() for key, value in raw_row.items()
            }
            if not any(row.values()):
                continue
            quantity = Decimal(row["quantity"])
            average_cost = Decimal(row["average_cost"]) if row["average_cost"] else None
            positions.append(
                PositionUpsert(
                    symbol=row["symbol"],
                    quantity=quantity,
                    average_cost=average_cost,
                    currency=row["currency"],
                )
            )
            if len(positions) > 1000:
                raise HTTPException(
                    status_code=422,
                    detail="Portfolio CSV is limited to 1000 positions",
                )
    except (InvalidOperation, KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail="Portfolio CSV contains invalid position data",
        ) from exc

    if not positions:
        raise HTTPException(status_code=422, detail="Portfolio CSV contains no positions")
    try:
        return bulk_upsert_positions(
            session,
            portfolio_id,
            PositionBulkUpsert(positions=positions, replace=replace),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
