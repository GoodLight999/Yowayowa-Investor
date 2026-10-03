from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from yowayowa.api.deps import db_session, require_api_token
from yowayowa.providers.base import ProviderPolicyError
from yowayowa.providers.registry import yahoo_market_provider, yahoo_risk_provider
from yowayowa.risk_models import PortfolioRiskAnalytics
from yowayowa.services.portfolios import get_portfolio, portfolio_analytics
from yowayowa.services.risk import portfolio_risk_analytics
from yowayowa.symbols import normalize_symbol

router = APIRouter(prefix="/v1", dependencies=[Depends(require_api_token)])


@router.get("/portfolios/{portfolio_id}/risk", response_model=PortfolioRiskAnalytics)
def get_portfolio_risk(
    portfolio_id: int,
    benchmark: str = Query(default="^GSPC", min_length=1, max_length=32),
    period: str = Query(default="1y", pattern=r"^(?:[0-9]+(?:d|mo|y)|max)$"),
    risk_free_rate: float = Query(default=0.0, ge=-1.0, le=1.0),
    session: Session = Depends(db_session),
) -> PortfolioRiskAnalytics:
    normalized_benchmark = normalize_symbol(benchmark)
    try:
        portfolio = get_portfolio(session, portfolio_id)
        valuation = portfolio_analytics(portfolio, yahoo_market_provider())
        history = yahoo_risk_provider().portfolio_returns(
            portfolio,
            benchmark=normalized_benchmark,
            period=period,
        )
        return portfolio_risk_analytics(
            portfolio,
            valuation,
            history,
            benchmark=normalized_benchmark,
            period=period,
            risk_free_rate=risk_free_rate,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProviderPolicyError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
