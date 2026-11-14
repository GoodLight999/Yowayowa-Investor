"""Private API for append-only hypothesis and falsification records."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.orm import Session

from yowayowa.api.deps import db_session, require_api_token
from yowayowa.config import Settings, get_settings
from yowayowa.hypothesis_models import HypothesisCreate, InvestmentHypothesis
from yowayowa.services.hypotheses import (
    create_hypothesis,
    get_hypothesis,
    list_hypotheses,
)


def _require_personal_mode(settings: Settings = Depends(get_settings)) -> None:
    if settings.mode != "personal":
        raise HTTPException(
            status_code=403,
            detail="Hypothesis records are private operator data and require personal mode",
        )


router = APIRouter(
    prefix="/v1/hypotheses",
    dependencies=[Depends(require_api_token), Depends(_require_personal_mode)],
)


@router.post("", response_model=InvestmentHypothesis, status_code=201)
def post_hypothesis(
    payload: HypothesisCreate,
    session: Session = Depends(db_session),
) -> InvestmentHypothesis:
    """Capture a research judgment; this endpoint never executes or changes orders."""

    try:
        return create_hypothesis(session, payload)
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Unable to persist hypothesis record") from exc


@router.get("", response_model=list[InvestmentHypothesis])
def hypotheses(
    symbol: str | None = Query(default=None, min_length=1, max_length=32),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100000),
    session: Session = Depends(db_session),
) -> list[InvestmentHypothesis]:
    return list_hypotheses(session, symbol=symbol, limit=limit, offset=offset)


@router.get("/{hypothesis_id}", response_model=InvestmentHypothesis)
def hypothesis(
    hypothesis_id: int = Path(ge=1),
    session: Session = Depends(db_session),
) -> InvestmentHypothesis:
    try:
        return get_hypothesis(session, hypothesis_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
