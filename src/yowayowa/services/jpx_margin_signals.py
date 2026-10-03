"""Transparent deterministic signals derived from persisted JPX margin facts."""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from yowayowa.db import JpxMarginAuxRecord, JpxMarginBalanceRecord
from yowayowa.domain import Provenance
from yowayowa.jpx_public_models import JpxMarginSignal, JpxMarginSignalKind
from yowayowa.services.jpx_public_margin import (
    JpxPublicMarginInstantError,
    _detail_from_aux,
    _flow_from_aux,
    _premium_from_aux,
    _watch_from_aux,
)


def _latest_aux_date(session: Session, kind: str) -> date | None:
    return session.scalar(
        select(func.max(JpxMarginAuxRecord.as_of_date)).where(JpxMarginAuxRecord.kind == kind)
    )


def _aux_date_set(session: Session, kind: str) -> set[date]:
    return set(
        session.scalars(
            select(JpxMarginAuxRecord.as_of_date).where(JpxMarginAuxRecord.kind == kind).distinct()
        )
    )


def _aux_rows(session: Session, *, kind: str, as_of_date: date) -> list[JpxMarginAuxRecord]:
    return list(
        session.scalars(
            select(JpxMarginAuxRecord).where(
                JpxMarginAuxRecord.kind == kind,
                JpxMarginAuxRecord.as_of_date == as_of_date,
            )
        )
    )


def _balances(session: Session, application_date: date) -> dict[str, JpxMarginBalanceRecord]:
    return {
        row.code: row
        for row in session.scalars(
            select(JpxMarginBalanceRecord).where(
                JpxMarginBalanceRecord.application_date == application_date
            )
        )
    }


def _signal(
    *,
    signal: JpxMarginSignalKind,
    as_of_date: date,
    code: str,
    company_name: str | None,
    metrics: dict[str, int | float | str | bool | None],
    reason: str,
    provenances: list[Provenance],
    available_times: list[datetime],
) -> JpxMarginSignal:
    # The availability instant is the *absolute* latest of every contributing
    # source, and JPX publication times are JST while first-observed XLSX
    # times are UTC. Comparing raw wall times would pick the larger clock
    # reading rather than the later instant, exposing the signal before one of
    # its inputs was actually known (AS-JPX-03). Normalizing to UTC first makes
    # max() an absolute-instant comparison; a naive input is a bug upstream and
    # is rejected rather than guessed at.
    normalized: list[datetime] = []
    for instant in available_times:
        if instant.tzinfo is None:
            raise JpxPublicMarginInstantError(
                f"naive availability instant {instant.isoformat()} for signal "
                f"{signal} {code}: an explicit timezone offset is required"
            )
        normalized.append(instant.astimezone(UTC))
    return JpxMarginSignal(
        signal=signal,
        as_of_date=as_of_date,
        code=code,
        company_name=company_name,
        metrics=metrics,
        reason=reason,
        available_at=max(normalized),
        provenance=provenances,
    )


def _balance_signal_rows(
    session: Session,
    signal: JpxMarginSignalKind,
    *,
    long_ratio_threshold: float,
    short_ratio_threshold: float,
    unwind_threshold: float,
) -> list[JpxMarginSignal]:
    day = _latest_aux_date(session, "balance_detail")
    if day is None:
        return []
    balances = _balances(session, day)
    results: list[JpxMarginSignal] = []

    for aux in _aux_rows(session, kind="balance_detail", as_of_date=day):
        detail = _detail_from_aux(aux)
        balance = balances.get(detail.code)
        if balance is None:
            continue

        metrics: dict[str, int | float | str | bool | None]
        if signal == "crowded-long":
            if (
                detail.long_listed_ratio_pct is None
                or detail.long_source_change is None
                or detail.long_listed_ratio_pct < long_ratio_threshold
                or detail.long_source_change <= 0
            ):
                continue
            metrics = {
                "long_total": balance.long_total,
                "long_change": detail.long_source_change,
                "long_listed_ratio_pct": detail.long_listed_ratio_pct,
            }
            reason = "high listed-share long balance and positive source-reported daily change"
        elif signal == "crowded-short":
            if (
                detail.short_listed_ratio_pct is None
                or detail.short_source_change is None
                or detail.short_listed_ratio_pct < short_ratio_threshold
                or detail.short_source_change <= 0
            ):
                continue
            metrics = {
                "short_total": balance.short_total,
                "short_change": detail.short_source_change,
                "short_listed_ratio_pct": detail.short_listed_ratio_pct,
            }
            reason = "high listed-share short balance and positive source-reported daily change"
        elif signal == "long-unwind":
            change = detail.long_source_change
            if change is None or change >= 0:
                continue
            previous = balance.long_total - change
            fraction = (-change / previous) if previous > 0 else None
            if fraction is None or fraction < unwind_threshold:
                continue
            metrics = {
                "long_total": balance.long_total,
                "long_change": change,
                "reduction_fraction": fraction,
            }
            reason = "material source-reported reduction in long margin balance"
        else:
            change = detail.short_source_change
            if change is None or change >= 0:
                continue
            previous = balance.short_total - change
            fraction = (-change / previous) if previous > 0 else None
            if fraction is None or fraction < unwind_threshold:
                continue
            metrics = {
                "short_total": balance.short_total,
                "short_change": change,
                "reduction_fraction": fraction,
            }
            reason = "material source-reported reduction in short margin balance"

        results.append(
            _signal(
                signal=signal,
                as_of_date=day,
                code=detail.code,
                company_name=balance.company_name,
                metrics=metrics,
                reason=reason,
                provenances=[detail.provenance],
                available_times=[detail.published_at],
            )
        )
    return results


def _flow_signal_rows(
    session: Session,
    signal: JpxMarginSignalKind,
    *,
    flow_buy_threshold: float,
    flow_sell_threshold: float,
) -> list[JpxMarginSignal]:
    day = _latest_aux_date(session, "flow")
    if day is None:
        return []
    results: list[JpxMarginSignal] = []
    for aux in _aux_rows(session, kind="flow", as_of_date=day):
        flow = _flow_from_aux(aux)
        value = flow.new_purchase_ratio_pct if signal == "flow-buy" else flow.new_sales_ratio_pct
        threshold = flow_buy_threshold if signal == "flow-buy" else flow_sell_threshold
        if value is None or value < threshold:
            continue
        results.append(
            _signal(
                signal=signal,
                as_of_date=day,
                code=flow.code,
                company_name=flow.company_name,
                metrics={
                    "new_sales_ratio_pct": flow.new_sales_ratio_pct,
                    "new_purchase_ratio_pct": flow.new_purchase_ratio_pct,
                    "status_marker": flow.status_marker,
                },
                reason=(
                    "high new-margin purchase ratio"
                    if signal == "flow-buy"
                    else "high new-margin sales ratio"
                ),
                provenances=[flow.provenance],
                available_times=[flow.published_at],
            )
        )
    return results


def _divergence_rows(
    session: Session,
    signal: JpxMarginSignalKind,
    *,
    flow_buy_threshold: float,
    flow_sell_threshold: float,
) -> list[JpxMarginSignal]:
    balance_day = _latest_aux_date(session, "balance_detail")
    if balance_day is None or balance_day not in _aux_date_set(session, "flow"):
        return []
    details = {
        row.code: _detail_from_aux(row)
        for row in _aux_rows(session, kind="balance_detail", as_of_date=balance_day)
        if row.code is not None
    }
    flows = {
        row.code: _flow_from_aux(row)
        for row in _aux_rows(session, kind="flow", as_of_date=balance_day)
        if row.code is not None
    }
    results: list[JpxMarginSignal] = []
    for code in sorted(details.keys() & flows.keys()):
        detail = details[code]
        flow = flows[code]
        if signal == "buy-flow-divergence":
            if (
                flow.new_purchase_ratio_pct is None
                or flow.new_purchase_ratio_pct < flow_buy_threshold
                or detail.long_source_change is None
                or detail.long_source_change >= 0
            ):
                continue
            reason = "strong same-day new-margin buying while long outstanding fell"
        else:
            if (
                flow.new_sales_ratio_pct is None
                or flow.new_sales_ratio_pct < flow_sell_threshold
                or detail.short_source_change is None
                or detail.short_source_change >= 0
            ):
                continue
            reason = "strong same-day new-margin selling while short outstanding fell"
        results.append(
            _signal(
                signal=signal,
                as_of_date=balance_day,
                code=code,
                company_name=flow.company_name,
                metrics={
                    "new_sales_ratio_pct": flow.new_sales_ratio_pct,
                    "new_purchase_ratio_pct": flow.new_purchase_ratio_pct,
                    "short_change": detail.short_source_change,
                    "long_change": detail.long_source_change,
                },
                reason=reason,
                provenances=[detail.provenance, flow.provenance],
                available_times=[detail.published_at, flow.published_at],
            )
        )
    return results


def _borrow_stress_rows(session: Session) -> list[JpxMarginSignal]:
    day = _latest_aux_date(session, "premium")
    if day is None:
        return []
    results: list[JpxMarginSignal] = []
    for aux in _aux_rows(session, kind="premium", as_of_date=day):
        premium = _premium_from_aux(aux)
        if premium.resolved_jpx_code is None or premium.over_lent_shares <= 0:
            continue
        results.append(
            _signal(
                signal="borrow-stress",
                as_of_date=day,
                code=premium.resolved_jpx_code,
                company_name=premium.company_name,
                metrics={
                    "over_lent_shares": premium.over_lent_shares,
                    "premium_charge": premium.premium_charge,
                    "maximum_premium_charge": premium.maximum_premium_charge,
                },
                reason="stock-loan shortage is present; premium charge is reported separately",
                provenances=[premium.provenance],
                available_times=[premium.published_at],
            )
        )
    return results


def _squeeze_rows(
    session: Session,
    *,
    short_ratio_threshold: float,
    flow_buy_threshold: float,
) -> list[JpxMarginSignal]:
    common = (
        _aux_date_set(session, "balance_detail")
        & _aux_date_set(session, "premium")
        & _aux_date_set(session, "flow")
    )
    if not common:
        return []
    day = max(common)
    details = {
        row.code: _detail_from_aux(row)
        for row in _aux_rows(session, kind="balance_detail", as_of_date=day)
        if row.code is not None
    }
    flows = {
        row.code: _flow_from_aux(row)
        for row in _aux_rows(session, kind="flow", as_of_date=day)
        if row.code is not None
    }
    premiums = {
        row.code: _premium_from_aux(row)
        for row in _aux_rows(session, kind="premium", as_of_date=day)
        if row.code is not None
    }

    results: list[JpxMarginSignal] = []
    for code in sorted(details.keys() & flows.keys() & premiums.keys()):
        detail = details[code]
        flow = flows[code]
        premium = premiums[code]
        if (
            detail.short_listed_ratio_pct is None
            or detail.short_listed_ratio_pct < short_ratio_threshold
            or premium.over_lent_shares <= 0
            or premium.premium_charge is None
            or premium.premium_charge <= 0
            or flow.new_purchase_ratio_pct is None
            or flow.new_sales_ratio_pct is None
            or flow.new_purchase_ratio_pct < flow_buy_threshold
            or flow.new_purchase_ratio_pct <= flow.new_sales_ratio_pct
        ):
            continue
        results.append(
            _signal(
                signal="squeeze-watch",
                as_of_date=day,
                code=code,
                company_name=flow.company_name,
                metrics={
                    "short_listed_ratio_pct": detail.short_listed_ratio_pct,
                    "over_lent_shares": premium.over_lent_shares,
                    "premium_charge": premium.premium_charge,
                    "new_sales_ratio_pct": flow.new_sales_ratio_pct,
                    "new_purchase_ratio_pct": flow.new_purchase_ratio_pct,
                },
                reason=(
                    "short crowding + positive stock-loan premium + observed buy-flow dominance"
                ),
                provenances=[
                    detail.provenance,
                    premium.provenance,
                    flow.provenance,
                ],
                available_times=[
                    detail.published_at,
                    premium.published_at,
                    flow.published_at,
                ],
            )
        )
    return results


def _watch_rows(session: Session) -> list[JpxMarginSignal]:
    day = _latest_aux_date(session, "watch")
    if day is None:
        return []
    results: list[JpxMarginSignal] = []
    for aux in _aux_rows(session, kind="watch", as_of_date=day):
        watch = _watch_from_aux(aux)
        if watch.primary_status is None and watch.jsf_status is None:
            continue
        results.append(
            _signal(
                signal="watch-flags",
                as_of_date=day,
                code=watch.code,
                company_name=watch.company_name,
                metrics={
                    "primary_status": watch.primary_status,
                    "jsf_status": watch.jsf_status,
                    "short_listed_ratio_pct": watch.short_listed_ratio_pct,
                    "long_listed_ratio_pct": watch.long_listed_ratio_pct,
                },
                reason="JPX/JSF watch or regulatory flag is present",
                provenances=[watch.provenance],
                available_times=[watch.published_at],
            )
        )
    return results


def scan_jpx_margin_signals(
    session: Session,
    signal: JpxMarginSignalKind,
    *,
    limit: int = 50,
    long_ratio_threshold: float = 10.0,
    short_ratio_threshold: float = 2.0,
    unwind_threshold: float = 0.10,
    flow_buy_threshold: float = 40.0,
    flow_sell_threshold: float = 20.0,
) -> list[JpxMarginSignal]:
    """Return source-backed candidates with explicit metrics and no opaque score."""

    if signal in {"crowded-long", "crowded-short", "long-unwind", "short-cover"}:
        results = _balance_signal_rows(
            session,
            signal,
            long_ratio_threshold=long_ratio_threshold,
            short_ratio_threshold=short_ratio_threshold,
            unwind_threshold=unwind_threshold,
        )
    elif signal == "borrow-stress":
        results = _borrow_stress_rows(session)
    elif signal in {"flow-buy", "flow-sell"}:
        results = _flow_signal_rows(
            session,
            signal,
            flow_buy_threshold=flow_buy_threshold,
            flow_sell_threshold=flow_sell_threshold,
        )
    elif signal in {"buy-flow-divergence", "sell-flow-divergence"}:
        results = _divergence_rows(
            session,
            signal,
            flow_buy_threshold=flow_buy_threshold,
            flow_sell_threshold=flow_sell_threshold,
        )
    elif signal == "squeeze-watch":
        results = _squeeze_rows(
            session,
            short_ratio_threshold=short_ratio_threshold,
            flow_buy_threshold=flow_buy_threshold,
        )
    elif signal == "watch-flags":
        results = _watch_rows(session)
    else:
        raise ValueError(f"unknown JPX margin signal: {signal}")

    priority = {
        "crowded-long": "long_listed_ratio_pct",
        "crowded-short": "short_listed_ratio_pct",
        "long-unwind": "reduction_fraction",
        "short-cover": "reduction_fraction",
        "borrow-stress": "over_lent_shares",
        "flow-buy": "new_purchase_ratio_pct",
        "flow-sell": "new_sales_ratio_pct",
        "buy-flow-divergence": "new_purchase_ratio_pct",
        "sell-flow-divergence": "new_sales_ratio_pct",
        "squeeze-watch": "short_listed_ratio_pct",
        "watch-flags": "long_listed_ratio_pct",
    }[signal]

    def sort_key(item: JpxMarginSignal) -> tuple[float, str]:
        value = item.metrics.get(priority)
        numeric = float(value) if isinstance(value, (int, float)) else 0.0
        return numeric, item.code

    results.sort(key=sort_key, reverse=True)
    return results[:limit]


__all__ = ["scan_jpx_margin_signals"]
