from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from yowayowa.acquisition.auth import strip_url_query
from yowayowa.acquisition.models import AcquisitionFetchState, AuthState
from yowayowa.broker_models import BrokerExecution, BrokerOrderSide
from yowayowa.domain import Portfolio, PositionUpsert
from yowayowa.services.order_inquiry_service import OrderInquiryService
from yowayowa.services.portfolios import (
    PositionVersionConflictError,
    applied_execution_fingerprints,
    apply_execution_reconciliation,
    get_portfolio,
)
from yowayowa.symbols import InputValidationError, normalize_currency, normalize_symbol


class ExecutionReconciliationItem(BaseModel):
    execution: BrokerExecution
    state: Literal["pending", "already_applied", "incomplete", "changed_after_application"]
    fingerprint: str | None = None
    issues: list[str] = Field(default_factory=list)


class PositionReconciliationChange(BaseModel):
    symbol: str
    currency: str
    current_quantity: Decimal
    target_quantity: Decimal
    current_average_cost: Decimal | None = None
    target_average_cost: Decimal | None = None
    expected_version: int | None = None


class ExecutionReconciliationPreview(BaseModel):
    preview_id: str
    portfolio_id: int
    market: str
    generated_at: datetime
    fetch_state: AcquisitionFetchState
    auth_state: AuthState
    source_url: str | None = None
    retrieved_at: datetime | None = None
    as_of: datetime | None = None
    parser_version: str | None = None
    schema_version: str | None = None
    snapshot_id: str | None = None
    snapshot_payload_sha256: str | None = None
    catalog_verified: bool | None = None
    executions: list[ExecutionReconciliationItem]
    changes: list[PositionReconciliationChange]
    notes: list[str]
    blockers: list[str]
    can_apply: bool


class ExecutionReconciliationApplyResult(BaseModel):
    preview_id: str
    portfolio: Portfolio
    applied_execution_ids: list[str]


class ExecutionPreviewChangedError(ValueError):
    """The local portfolio or broker execution snapshot changed after preview."""


class ExecutionsReconciliationService:
    """Preview broker fills against local holdings; apply only after explicit approval."""

    def __init__(self, *, order_inquiry: OrderInquiryService) -> None:
        self._order_inquiry = order_inquiry

    def preview(
        self,
        session: Session,
        portfolio_id: int,
        market: str = "jp",
        *,
        force_refresh: bool = False,
    ) -> ExecutionReconciliationPreview:
        portfolio = get_portfolio(session, portfolio_id)
        outcome = self._order_inquiry.list_executions(market, force_refresh=force_refresh)
        applied = applied_execution_fingerprints(session, portfolio_id, market)
        notes = list(outcome.notes)
        notes.append(
            "Average-cost projection uses execution prices only; fees and taxes are not added."
        )
        notes.append(
            "Only fills in this broker response are projected onto local holdings; "
            "feed coverage and overlap with manually maintained/imported holdings are unknown "
            "and must be verified before approval."
        )
        blockers: list[str] = []
        catalog_verified = None
        if outcome.detail is not None:
            value = outcome.detail.get("verified")
            catalog_verified = value if isinstance(value, bool) else None

        if outcome.fetch_state != AcquisitionFetchState.OK:
            blockers.append(
                f"executions fetch is {outcome.fetch_state.value}; only a fresh OK read can apply"
            )
        if outcome.resource != "executions" or outcome.market != market:
            blockers.append("broker-read outcome resource/market does not match this preview")
        if outcome.auth_state != AuthState.AUTHENTICATED:
            blockers.append(
                f"broker authentication is {outcome.auth_state.value}; authenticated read required"
            )
        if catalog_verified is not True:
            blockers.append(
                "executions resource URL/parser is not marked verified; "
                "NEED-HUMAN validation required"
            )

        items: list[ExecutionReconciliationItem] = []
        pending: list[tuple[int, BrokerExecution, str]] = []
        seen_ids: dict[str, str] = {}
        for index, execution in enumerate(outcome.executions):
            fingerprint = _execution_fingerprint(execution)
            issues = _execution_issues(execution)
            execution_id = execution.execution_id
            state: Literal["pending", "already_applied", "incomplete", "changed_after_application"]
            if execution_id is None:
                issues.append("execution identity is missing; idempotent application is unsafe")
                state = "incomplete"
            elif len(execution_id) > 128:
                issues.append("execution identity exceeds the supported 128-character limit")
                state = "incomplete"
            elif execution_id in seen_ids:
                issues.append(
                    "duplicate execution identity in broker response; refusing to double-count"
                )
                state = "incomplete"
                blockers.append(f"duplicate execution identity: {execution_id}")
            elif execution_id in applied:
                seen_ids[execution_id] = fingerprint
                if applied[execution_id] == fingerprint:
                    state = "already_applied"
                else:
                    state = "changed_after_application"
                    issues.append("broker row differs from the previously applied execution")
                    blockers.append(f"previously applied execution changed: {execution_id}")
            elif issues:
                state = "incomplete"
            else:
                state = "pending"
                pending.append((index, execution, fingerprint))
                seen_ids[execution_id] = fingerprint
            if issues:
                blockers.extend(f"execution {execution_id or index}: {issue}" for issue in issues)
            items.append(
                ExecutionReconciliationItem(
                    execution=execution,
                    state=state,
                    fingerprint=fingerprint,
                    issues=issues,
                )
            )

        if not pending:
            blockers.append("no unapplied complete executions are available")

        local_positions: dict[str, tuple[Decimal, Decimal | None, str, int]] = {}
        for position in portfolio.positions:
            try:
                symbol = normalize_symbol(position.symbol)
                currency = normalize_currency(position.currency)
            except InputValidationError as exc:
                blockers.append(f"local position cannot be normalized: {exc}")
                continue
            if symbol in local_positions:
                blockers.append(f"portfolio contains duplicate position rows for {symbol}")
                continue
            local_positions[symbol] = (
                position.quantity,
                position.average_cost,
                currency,
                position.version,
            )

        by_symbol: dict[str, list[tuple[int, BrokerExecution]]] = {}
        for index, execution, _ in pending:
            if execution.symbol is None:
                continue
            try:
                symbol = normalize_symbol(execution.symbol)
            except InputValidationError as exc:
                blockers.append(f"execution symbol is invalid: {exc}")
                continue
            by_symbol.setdefault(symbol, []).append((index, execution))

        changes: list[PositionReconciliationChange] = []
        for symbol, symbol_executions in sorted(by_symbol.items()):
            local_quantity, local_average_cost, local_currency, local_version = local_positions.get(
                symbol, (Decimal(0), None, "", 0)
            )
            currencies = {execution.currency for _, execution in symbol_executions}
            if len(currencies) != 1:
                blockers.append(f"executions for {symbol} have inconsistent currencies")
                continue
            currency_value = next(iter(currencies))
            if currency_value is None:
                continue
            currency = normalize_currency(currency_value)
            if local_currency and local_currency != currency:
                blockers.append(
                    f"currency mismatch for {symbol}: local {local_currency}, execution {currency}"
                )
                continue

            times = [execution.executed_at for _, execution in symbol_executions]
            if any(value is None for value in times):
                continue
            awareness = {value.utcoffset() is not None for value in times if value is not None}
            if len(awareness) > 1:
                blockers.append(
                    f"execution timestamps for {symbol} mix timezone-aware and naive values"
                )
                continue
            ordered = sorted(
                symbol_executions,
                key=lambda pair: (pair[1].executed_at, pair[0]),
            )
            quantity, average_cost = local_quantity, local_average_cost
            for _, execution in ordered:
                assert execution.side is not None
                assert execution.quantity is not None
                assert execution.price is not None
                quantity, average_cost = _apply_fill(
                    quantity,
                    average_cost,
                    execution.side,
                    execution.quantity,
                    execution.price,
                )
            changes.append(
                PositionReconciliationChange(
                    symbol=symbol,
                    currency=currency,
                    current_quantity=local_quantity,
                    target_quantity=quantity,
                    current_average_cost=local_average_cost,
                    target_average_cost=average_cost,
                    expected_version=local_version if local_currency else None,
                )
            )

        if outcome.snapshot is None or not outcome.snapshot.payload_sha256:
            blockers.append(
                "execution payload hash is missing; preview cannot be bound to a source snapshot"
            )
        if outcome.fetch_state == AcquisitionFetchState.STALE:
            notes.append("stale broker data is preview-only and cannot be applied")

        source_url = strip_url_query(outcome.source_url) if outcome.source_url else None
        retrieved_at = _parse_optional_datetime(outcome.retrieved_at)
        as_of = _parse_optional_datetime(outcome.as_of)
        if outcome.retrieved_at and retrieved_at is None:
            notes.append("retrieved_at is present but not parseable as a datetime")
        if outcome.as_of and as_of is None:
            notes.append("as_of is present but not parseable as a datetime")

        stable_payload = {
            "portfolio_id": portfolio.id,
            "market": market,
            "positions": [
                [
                    position.symbol,
                    str(position.quantity),
                    str(position.average_cost),
                    position.currency,
                    getattr(position, "version", 1),
                ]
                for position in sorted(portfolio.positions, key=lambda item: item.symbol)
            ],
            "snapshot_payload_sha256": (
                outcome.snapshot.payload_sha256 if outcome.snapshot else None
            ),
            "source_url": source_url,
            "as_of": outcome.as_of,
            "parser_version": outcome.parser_version,
            "schema_version": outcome.schema_version,
            "fetch_state": outcome.fetch_state.value,
            "auth_state": outcome.auth_state.value,
            "executions": [item.fingerprint for item in items],
            "applied_execution_ids": sorted(applied),
            "catalog_verified": catalog_verified,
        }
        preview_id = hashlib.sha256(_canonical_json(stable_payload).encode("utf-8")).hexdigest()
        can_apply = (
            outcome.fetch_state == AcquisitionFetchState.OK
            and outcome.auth_state == AuthState.AUTHENTICATED
            and catalog_verified is True
            and bool(pending)
            and not blockers
        )
        return ExecutionReconciliationPreview(
            preview_id=preview_id,
            portfolio_id=portfolio.id,
            market=market,
            generated_at=datetime.now(UTC),
            fetch_state=outcome.fetch_state,
            auth_state=outcome.auth_state,
            source_url=source_url,
            retrieved_at=retrieved_at,
            as_of=as_of,
            parser_version=outcome.parser_version,
            schema_version=outcome.schema_version,
            snapshot_id=outcome.snapshot.snapshot_id if outcome.snapshot else None,
            snapshot_payload_sha256=(outcome.snapshot.payload_sha256 if outcome.snapshot else None),
            catalog_verified=catalog_verified,
            executions=items,
            changes=changes,
            notes=notes,
            blockers=list(dict.fromkeys(blockers)),
            can_apply=can_apply,
        )

    def apply(
        self,
        session: Session,
        portfolio_id: int,
        market: str,
        preview_id: str,
        *,
        operator_approved: bool,
    ) -> ExecutionReconciliationApplyResult:
        if not operator_approved:
            raise ValueError("operator_approved must be explicitly true")
        current = self.preview(session, portfolio_id, market, force_refresh=True)
        if current.preview_id != preview_id:
            raise ExecutionPreviewChangedError(
                "broker executions or local portfolio changed after preview; generate a new preview"
            )
        if not current.can_apply:
            raise ValueError("preview is not applicable: " + "; ".join(current.blockers))

        fingerprints = {
            item.execution.execution_id: item.fingerprint
            for item in current.executions
            if item.state == "pending"
            and item.execution.execution_id is not None
            and item.fingerprint is not None
        }
        positions = [
            PositionUpsert(
                symbol=change.symbol,
                quantity=change.target_quantity,
                average_cost=change.target_average_cost,
                currency=change.currency,
                expected_version=change.expected_version,
            )
            for change in current.changes
        ]
        expected_versions = {change.symbol: change.expected_version for change in current.changes}
        try:
            portfolio = apply_execution_reconciliation(
                session,
                portfolio_id,
                positions,
                market=market,
                execution_fingerprints=fingerprints,
                preview_id=current.preview_id,
                expected_versions=expected_versions,
            )
        except PositionVersionConflictError as exc:
            session.rollback()
            raise ExecutionPreviewChangedError(
                "local position changed after preview; generate a new preview"
            ) from exc
        return ExecutionReconciliationApplyResult(
            preview_id=current.preview_id,
            portfolio=portfolio,
            applied_execution_ids=sorted(fingerprints),
        )


def _execution_issues(execution: BrokerExecution) -> list[str]:
    issues: list[str] = []
    for field_name in ("symbol", "side", "quantity", "price", "currency", "executed_at"):
        if getattr(execution, field_name) is None:
            issues.append(f"{field_name} missing; value remains missing")
    if execution.quantity is not None and execution.quantity <= 0:
        issues.append("quantity must be positive; value is not coerced")
    if execution.price is not None and execution.price <= 0:
        issues.append("execution price must be positive")
    if execution.currency is not None:
        try:
            normalize_currency(execution.currency)
        except InputValidationError:
            issues.append(f"currency is not a valid three-letter code: {execution.currency!r}")
    if execution.symbol is not None:
        try:
            normalize_symbol(execution.symbol)
        except InputValidationError:
            issues.append(f"symbol is invalid: {execution.symbol!r}")
    return issues


def _execution_fingerprint(execution: BrokerExecution) -> str:
    payload = _canonical_json(execution.model_dump(mode="json"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _parse_optional_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _apply_fill(
    quantity: Decimal,
    average_cost: Decimal | None,
    side: BrokerOrderSide,
    fill_quantity: Decimal,
    fill_price: Decimal,
) -> tuple[Decimal, Decimal | None]:
    if side == BrokerOrderSide.BUY:
        if quantity < 0:
            remaining_short = quantity + fill_quantity
            if remaining_short < 0:
                return remaining_short, average_cost
            if remaining_short == 0:
                return Decimal(0), None
            return remaining_short, fill_price
        next_quantity = quantity + fill_quantity
        if average_cost is None:
            next_average = fill_price if quantity == 0 else None
        else:
            next_average = (quantity * average_cost + fill_quantity * fill_price) / next_quantity
        return next_quantity, next_average

    if quantity > 0:
        remaining_long = quantity - fill_quantity
        if remaining_long > 0:
            return remaining_long, average_cost
        if remaining_long == 0:
            return Decimal(0), None
        return remaining_long, fill_price
    next_quantity = quantity - fill_quantity
    if average_cost is None:
        next_average = fill_price if quantity == 0 else None
    else:
        next_average = (abs(quantity) * average_cost + fill_quantity * fill_price) / abs(
            next_quantity
        )
    return next_quantity, next_average


__all__ = [
    "ExecutionPreviewChangedError",
    "ExecutionReconciliationApplyResult",
    "ExecutionReconciliationItem",
    "ExecutionReconciliationPreview",
    "ExecutionsReconciliationService",
    "PositionReconciliationChange",
]
