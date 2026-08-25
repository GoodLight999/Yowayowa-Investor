from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from yowayowa.domain import Provenance
from yowayowa.edinet_index_db import EdinetFilingRecord
from yowayowa.edinet_models import EdinetFinancials, EdinetMetricObservation
from yowayowa.providers.edinet import EdinetClient
from yowayowa.services.edinet import financials, normalize_security_code


@dataclass(frozen=True)
class StrategyBalanceSheetSupplement:
    current_assets: float
    liabilities: float
    investment_securities: float
    provenance: Provenance


def tokyo_security_code(symbol: str) -> str | None:
    normalized = symbol.strip().upper()
    if not normalized.endswith(".T"):
        return None
    raw = normalized[:-2]
    if len(raw) != 4 or not raw.isascii() or not raw.isdigit():
        return None
    return normalize_security_code(raw)


def latest_indexed_annual_report(
    session: Session,
    symbol: str,
) -> EdinetFilingRecord | None:
    security_code = tokyo_security_code(symbol)
    if security_code is None:
        return None
    statement = (
        select(EdinetFilingRecord)
        .where(
            EdinetFilingRecord.security_code == security_code,
            EdinetFilingRecord.doc_type_code == "120",
            EdinetFilingRecord.csv_available.is_(True),
        )
        .order_by(
            EdinetFilingRecord.period_end.desc(),
            EdinetFilingRecord.submitted_at.desc(),
            EdinetFilingRecord.doc_id.desc(),
        )
        .limit(1)
    )
    return session.scalar(statement)


def _first_numeric(
    data: EdinetFinancials,
    metric: str,
) -> EdinetMetricObservation | None:
    for observation in data.metrics.get(metric, []):
        if observation.numeric_value is not None:
            return observation
    return None


def balance_sheet_supplement(
    session: Session,
    client: EdinetClient,
    symbol: str,
) -> StrategyBalanceSheetSupplement | None:
    filing = latest_indexed_annual_report(session, symbol)
    if filing is None:
        return None
    data = financials(client, filing.doc_id)
    observations = {
        key: _first_numeric(data, key)
        for key in ("current_assets", "liabilities", "investment_securities")
    }
    if any(item is None for item in observations.values()):
        return None

    current_assets = observations["current_assets"]
    liabilities = observations["liabilities"]
    investment_securities = observations["investment_securities"]
    assert current_assets is not None and current_assets.numeric_value is not None
    assert liabilities is not None and liabilities.numeric_value is not None
    assert investment_securities is not None and investment_securities.numeric_value is not None

    unit_ids = {
        item.unit_id
        for item in (current_assets, liabilities, investment_securities)
        if item.unit_id
    }
    if unit_ids != {"JPY"}:
        return None

    values = (
        float(current_assets.numeric_value),
        float(liabilities.numeric_value),
        float(investment_securities.numeric_value),
    )
    if any(value < 0 for value in values):
        return None

    provenance = data.provenance.model_copy(
        update={
            "as_of": data.period_end or filing.period_end,
            "notes": [
                *data.provenance.notes,
                f"Kiyohara-mode balance-sheet supplement from EDINET document {filing.doc_id}.",
                (
                    "Current assets, liabilities, and investment securities come from the same "
                    "annual filing in JPY; mixed-source or mixed-currency arithmetic is not used."
                ),
            ],
        }
    )
    return StrategyBalanceSheetSupplement(
        current_assets=values[0],
        liabilities=values[1],
        investment_securities=values[2],
        provenance=provenance,
    )
