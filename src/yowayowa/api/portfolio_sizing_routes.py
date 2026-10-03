from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from yowayowa.api.deps import db_session, require_api_token
from yowayowa.domain import PortfolioAnalytics
from yowayowa.providers.base import ProviderPolicyError
from yowayowa.providers.registry import yahoo_market_provider
from yowayowa.services.portfolio_sizing import (
    PortfolioSizingError,
    portfolio_sizing_proposals,
)
from yowayowa.services.portfolios import get_portfolio, portfolio_analytics
from yowayowa.sizing_models import PortfolioSizingProposal, PortfolioSizingRequest

router = APIRouter(prefix="/v1", dependencies=[Depends(require_api_token)])


@router.post(
    "/portfolios/{portfolio_id}/sizing-proposals",
    response_model=PortfolioSizingProposal,
)
def post_portfolio_sizing_proposals(
    portfolio_id: int,
    payload: PortfolioSizingRequest,
    session: Session = Depends(db_session),
) -> PortfolioSizingProposal:
    try:
        portfolio = get_portfolio(session, portfolio_id)
        valuation: PortfolioAnalytics = portfolio_analytics(portfolio, yahoo_market_provider())
        return portfolio_sizing_proposals(portfolio, valuation, payload)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PortfolioSizingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ProviderPolicyError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
