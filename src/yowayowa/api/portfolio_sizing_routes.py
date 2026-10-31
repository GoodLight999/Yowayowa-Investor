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
from yowayowa.services.sizing_idea import QuoteSizingIdeaError, build_quote_sizing_idea
from yowayowa.sizing_models import (
    PortfolioSizingIdea,
    PortfolioSizingProposal,
    PortfolioSizingRequest,
    QuoteSizingIdeaRequest,
)
from yowayowa.vercel_observability import (
    UPSTREAM_UNAVAILABLE_DETAIL,
    log_upstream_exception,
)

router = APIRouter(prefix="/v1", dependencies=[Depends(require_api_token)])

# Failure details are fixed code/reason pairs: no URL, header, token, raw
# exception, or upstream body ever reaches the operator.
_QUOTE_IDEA_ERROR_STATUS: dict[str, int] = {
    "symbol_invalid": 422,
    "quote_unavailable": 404,
    "quote_symbol_mismatch": 404,
    "price_invalid": 422,
    "currency_missing": 422,
    "currency_conflict": 422,
    "currency_source_missing": 422,
    "currency_invalid": 422,
    "provenance_insufficient": 422,
    "stop_not_below_entry": 422,
}
_PROVIDER_POLICY_DETAIL = {
    "code": "provider_policy",
    "reason": "Quote provider is not approved for this deployment mode.",
}
_UPSTREAM_DETAIL = {
    "code": "upstream_provider_failed",
    "reason": "Quote provider request failed; no idea was built.",
}


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
        log_upstream_exception(exc)
        raise HTTPException(status_code=502, detail=UPSTREAM_UNAVAILABLE_DETAIL) from exc


@router.post("/sizing/quote-idea", response_model=PortfolioSizingIdea)
def post_sizing_quote_idea(payload: QuoteSizingIdeaRequest) -> PortfolioSizingIdea:
    """Build one sizing idea from the latest provider quote (P5 R5).

    Response is the bare ``PortfolioSizingIdea`` so its JSON can be handed to
    the existing sizing-proposals surface unchanged. Failures are fixed
    code/reason details: input, price, currency, and provenance defects are
    422; unknown or unavailable symbols are 404; provider policy is 403;
    upstream failures are 502. No order is created or sent.
    """

    try:
        return build_quote_sizing_idea(yahoo_market_provider(), payload)
    except QuoteSizingIdeaError as exc:
        raise HTTPException(
            status_code=_QUOTE_IDEA_ERROR_STATUS.get(exc.code, 422),
            detail={"code": exc.code, "reason": exc.reason},
        ) from exc
    except ProviderPolicyError as exc:
        raise HTTPException(status_code=403, detail=dict(_PROVIDER_POLICY_DETAIL)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=dict(_UPSTREAM_DETAIL)) from exc
