from __future__ import annotations

from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from yowayowa.chart_models import ChartComposeRequest
from yowayowa.db import ResearchPresetRecord, utcnow
from yowayowa.domain import ComparisonRequest, ScreenRequest
from yowayowa.preset_models import PresetKind, ResearchPreset
from yowayowa.services.comparison import METRICS as COMPARISON_METRICS


def validate_preset_payload(kind: PresetKind, payload: dict[str, Any]) -> dict[str, Any]:
    if kind == "chart":
        return ChartComposeRequest.model_validate(payload).model_dump(
            mode="json",
            exclude_none=True,
        )
    if kind == "screener":
        return ScreenRequest.model_validate(payload).model_dump(mode="json", exclude_none=True)
    request = ComparisonRequest.model_validate(payload)
    unknown = [key for key in request.metrics if key not in COMPARISON_METRICS]
    if unknown:
        raise ValueError(f"Unknown comparison metrics: {', '.join(unknown)}")
    return request.model_dump(mode="json", exclude_none=True)


def _model(row: ResearchPresetRecord) -> ResearchPreset:
    return ResearchPreset(
        id=row.id,
        name=row.name,
        kind=cast(PresetKind, row.kind),
        payload=row.payload,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def list_presets(session: Session, kind: PresetKind | None = None) -> list[ResearchPreset]:
    statement = select(ResearchPresetRecord)
    if kind is not None:
        statement = statement.where(ResearchPresetRecord.kind == kind)
    statement = statement.order_by(ResearchPresetRecord.kind, ResearchPresetRecord.name)
    return [_model(row) for row in session.scalars(statement).all()]


def get_preset(session: Session, preset_id: int) -> ResearchPreset:
    row = session.get(ResearchPresetRecord, preset_id)
    if row is None:
        raise LookupError(f"Research preset {preset_id} not found")
    return _model(row)


def create_preset(
    session: Session,
    name: str,
    kind: PresetKind,
    payload: dict[str, Any],
) -> ResearchPreset:
    now = utcnow()
    row = ResearchPresetRecord(
        name=" ".join(name.split()),
        kind=kind,
        payload=validate_preset_payload(kind, payload),
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return _model(row)


def update_preset(
    session: Session,
    preset_id: int,
    name: str,
    kind: PresetKind,
    payload: dict[str, Any],
) -> ResearchPreset:
    row = session.get(ResearchPresetRecord, preset_id)
    if row is None:
        raise LookupError(f"Research preset {preset_id} not found")
    row.name = " ".join(name.split())
    row.kind = kind
    row.payload = validate_preset_payload(kind, payload)
    row.updated_at = utcnow()
    session.commit()
    session.refresh(row)
    return _model(row)


def delete_preset(session: Session, preset_id: int) -> None:
    row = session.get(ResearchPresetRecord, preset_id)
    if row is None:
        raise LookupError(f"Research preset {preset_id} not found")
    session.delete(row)
    session.commit()
