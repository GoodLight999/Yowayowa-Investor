from __future__ import annotations

import ipaddress
import secrets
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request

from yowayowa.broker_models import (
    BrokerOrder,
    BrokerOrderIntent,
    BrokerOrderPreview,
    BrokerOrderReceipt,
)
from yowayowa.config import Settings
from yowayowa.operator_bridge.excel import XlwingsMacroRunner
from yowayowa.operator_bridge.rakuten import RakutenMs2RssLocalConnector
from yowayowa.operator_bridge.state import SQLiteOperatorState
from yowayowa.services.broker_execution import evaluate_broker_execution


def _loopback_host(host: str) -> bool:
    if host in {"localhost", "testclient"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def create_operator_bridge_app(
    *,
    token: str,
    settings: Settings,
    state: SQLiteOperatorState,
    connector: RakutenMs2RssLocalConnector,
) -> FastAPI:
    if not token:
        raise ValueError("Operator Bridge token is required")
    app = FastAPI(
        title="Yowayowa Operator Bridge",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    def authorize(
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> None:
        host = request.client.host if request.client is not None else ""
        if not _loopback_host(host):
            raise HTTPException(status_code=403, detail="Operator Bridge is loopback-only")
        expected = f"Bearer {token}"
        if authorization is None or not secrets.compare_digest(authorization, expected):
            raise HTTPException(status_code=401, detail="Invalid Operator Bridge token")

    @app.get("/health", dependencies=[Depends(authorize)])
    def health() -> dict[str, object]:
        return {
            "status": "ok",
            "profile": "full_operator",
            "broker": "rakuten-securities",
            "transport": "market-speed-ii-rss",
            "live_orders_armed": settings.broker_live_orders_enabled,
        }

    @app.post(
        "/v1/brokers/rakuten/orders/preview",
        response_model=BrokerOrderPreview,
        dependencies=[Depends(authorize)],
    )
    def preview_order(intent: BrokerOrderIntent) -> BrokerOrderPreview:
        return connector.preview_order(intent)

    @app.post(
        "/v1/brokers/rakuten/orders",
        response_model=BrokerOrderReceipt,
        dependencies=[Depends(authorize)],
    )
    def submit_order(intent: BrokerOrderIntent) -> BrokerOrderReceipt:
        prior = state.latest_order_result(intent.client_order_id)
        if prior is not None:
            return BrokerOrderReceipt.model_validate(prior)

        preview = connector.preview_order(intent)
        decision = evaluate_broker_execution(
            settings,
            preview,
            orders_submitted_today=state.count_submission_attempts_today(),
        )
        if not decision.allowed:
            raise HTTPException(status_code=409, detail=list(decision.reasons))

        state.append_audit(
            "order_submit_attempt",
            client_order_id=intent.client_order_id,
            payload={
                "broker": preview.broker,
                "transport": preview.transport,
                "intent": intent.model_dump(mode="json"),
                "estimated_notional": (
                    str(preview.estimated_notional)
                    if preview.estimated_notional is not None
                    else None
                ),
                "currency": preview.currency,
            },
        )
        try:
            receipt = connector.submit_order(intent)
        except Exception as exc:
            state.append_audit(
                "order_submit_error",
                client_order_id=intent.client_order_id,
                payload={"error_type": type(exc).__name__},
            )
            raise HTTPException(status_code=502, detail="Broker submission failed") from exc

        state.append_audit(
            "order_submit_result",
            client_order_id=intent.client_order_id,
            broker_order_id=receipt.broker_order_id,
            payload=receipt.model_dump(mode="json"),
        )
        return receipt

    @app.get(
        "/v1/brokers/rakuten/orders",
        response_model=list[BrokerOrder],
        dependencies=[Depends(authorize)],
    )
    def list_orders() -> list[BrokerOrder]:
        return connector.list_orders()

    return app


def build_local_operator_bridge(
    *,
    token: str,
    settings: Settings,
    state_path: str | Path,
    workbook: str | Path | None = None,
) -> FastAPI:
    state = SQLiteOperatorState(state_path)
    connector = RakutenMs2RssLocalConnector(
        XlwingsMacroRunner(workbook),
        allocate_rss_order_id=state.allocate_rss_order_id,
    )
    return create_operator_bridge_app(
        token=token,
        settings=settings,
        state=state,
        connector=connector,
    )
