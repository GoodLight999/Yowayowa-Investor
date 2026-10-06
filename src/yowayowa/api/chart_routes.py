from __future__ import annotations

from fastapi import APIRouter, Depends

from yowayowa.api.deps import require_api_token
from yowayowa.chart_models import ChartComposeRequest, ChartComposeResponse
from yowayowa.providers.registry import fred_client, sec_client, yahoo_market_provider
from yowayowa.services.charts import compose_chart

router = APIRouter(prefix="/v1", dependencies=[Depends(require_api_token)])


@router.post("/charts/compose", response_model=ChartComposeResponse)
def post_chart_compose(payload: ChartComposeRequest) -> ChartComposeResponse:
    return compose_chart(
        payload,
        yahoo_market_provider(),
        sec_client(),
        fred_client(),
    )
