"""Machine screening over free sources (P4-D).

Pipeline contract (docs/PRIVATE_OPERATOR_ROADMAP.md, P4-D):

- Three independent free data sources feed one
  :class:`~yowayowa.screening_models.ScreeningRunResult`: the local EDINET
  daily filing list (keyword classification), the persisted weekly credit
  margin balances (week-over-week surge detection) and the Yahoo Finance
  custom screener (cheap valuation/attention filters).
- Sources are independent: a missing EDINET file, an empty credit-margin
  database and a screener failure each yield zero candidates plus a recorded
  coverage entry — the run still succeeds with whatever the other sources
  found; nothing is zero-filled and no run aborts another source's work.
- The result is deliberately NOT deduplicated: the same code appearing under
  several sources/signals is corroboration, and different signals are
  different candidates.
- Persistence is idempotent per run date: re-running the pipeline for the
  same run date replaces that date's rows atomically (delete+insert inside
  one transaction).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from yowayowa.config import get_settings
from yowayowa.credit_margin_models import normalize_credit_margin_code
from yowayowa.db import CreditMarginWeeklyRecord, ScreeningCandidateRecord
from yowayowa.domain import LicenseClass, Provenance
from yowayowa.providers.registry import yahoo_screener_provider
from yowayowa.research_models import MarketScreenFilter, MarketScreenRequest
from yowayowa.screening_models import (
    ScreeningCandidate,
    ScreeningRunResult,
    ScreeningSignal,
    ScreeningSource,
)
from yowayowa.services.credit_margin import read_credit_margin_by_code

__all__ = [
    "persist_screening_run",
    "read_screening_candidates",
    "run_screening_pipeline",
]

_DEFAULT_EDINET_PATH = Path("./data/edinet-daily.jsonl")

_LOW_PE_FIELD = "peratio.lasttwelvemonths"
_LOW_PE_MIN = 0.1
_LOW_PE_MAX = 8.0
_SHORT_SURGE_RATIO = 0.30
_SHORT_DROP_RATIO = -0.30
_LONG_SURGE_RATIO = 0.20

_YAHOO_SYMBOL_PATTERN = re.compile(r"^(\d{4})\.T$")
# EDINET security codes: the bare 4-digit local code or the 5-digit form the
# daily list publishes (5th digit is the JPX check digit); anything else is
# not a listed-issue code and is never repaired.
_SECURITY_CODE_PATTERN = re.compile(r"^\d{4}$|^\d{5}$")

_SCREENING_NOTE = "Machine screening output: research starting points, not recommendations."


# ------------------------------------------------------------------ source A


def _parse_submit_time(value: Any) -> datetime | None:
    """Parse an EDINET ``submitDateTime`` (``YYYY-MM-DD HH:MM``)."""

    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d %H:%M")
    except ValueError:
        return None


def _iter_edinet_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            parsed = json.loads(line)
            if isinstance(parsed, dict):
                yield parsed


# Keyword table order matters: a 訂正 filing is classified before anything
# else (a 訂正臨時報告書 may carry the actual forecast/body revision), and
# 「公開買付け」 (tender offer, incl. 自己株式の公開買付け) is matched before
# a generic 自己株式取得 mention so it classifies as the tender-offer signal.
_EDINET_SIGNAL_KEYWORDS: tuple[tuple[tuple[str, ...], ScreeningSignal], ...] = (
    (
        ("訂正有価証券報告書", "訂正有価証券届出書", "訂正報告書", "訂正臨時報告書"),
        ScreeningSignal.FILING_FORECAST_REVISION,
    ),
    (("自己株券消却",), ScreeningSignal.FILING_BUYBACK),
    (("公開買付け",), ScreeningSignal.FILING_BUYBACK),
    (("自己株式の取得",), ScreeningSignal.FILING_BUYBACK),
    (("上場廃止",), ScreeningSignal.FILING_CANCELLATION),
)


def classify_edinet_filing(description: str) -> ScreeningSignal | None:
    """Classify one EDINET doc description into a screening signal, or ``None``.

    Unknown filings (組成 wrappers, investment-trust programs, ordinary
    reports) carry no listed-company signal and are skipped by the caller —
    a missing classification is never turned into a made-up signal.
    """

    for needles, signal in _EDINET_SIGNAL_KEYWORDS:
        for needle in needles:
            if needle in description:
                return signal
    return None


def _edinet_candidates(
    path: Path,
    *,
    detected_at: datetime,
) -> tuple[list[ScreeningCandidate], dict[str, int], Provenance | None]:
    candidates: list[ScreeningCandidate] = []
    skipped_unclassified = 0
    skipped_unparseable = 0
    skipped_missing_code = 0
    row_count = 0
    first_provenance: Provenance | None = None
    for row in _iter_edinet_jsonl(path):
        row_count += 1
        doc_id = row.get("docID")
        if not isinstance(doc_id, str) or not doc_id.strip():
            skipped_unparseable += 1
            continue
        description_raw = row.get("docDescription")
        description = description_raw.strip() if isinstance(description_raw, str) else ""
        if not description:
            # docDescription is the only classification input; a None
            # description is unusable and is never guessed around.
            skipped_unclassified += 1
            continue
        signal = classify_edinet_filing(description)
        if signal is None:
            skipped_unclassified += 1
            continue
        sec_raw = row.get("secCode")
        sec_code = sec_raw.strip() if isinstance(sec_raw, str) else ""
        if not _SECURITY_CODE_PATTERN.fullmatch(sec_code):
            # EDINET fund/program filings publish no security code; without a
            # code there is no watchable issuer (never inferred).
            skipped_missing_code += 1
            continue
        submit_at = _parse_submit_time(row.get("submitDateTime"))
        provenance = Provenance(
            provider="edinet-v2",
            source="EDINET API Version 2 daily document list (type=2)",
            source_url=str(row.get("source_url")) if row.get("source_url") else None,
            license_class=LicenseClass.OFFICIAL_PUBLIC,
            retrieved_at=row.get("retrieved_at") or detected_at,
            as_of=submit_at.date() if submit_at else None,
            notes=["EDINET content is used under Public Data License 1.0."],
        )
        if first_provenance is None:
            first_provenance = provenance
        candidates.append(
            ScreeningCandidate(
                code=sec_code,
                source=ScreeningSource.EDINET_FILING,
                signal=signal,
                company_name=row.get("filerName") or None,
                value={
                    "doc_id": doc_id,
                    "doc_description": description,
                    "doc_type_code": row.get("docTypeCode") or None,
                    "submit_date_time": row.get("submitDateTime") or None,
                },
                reason=f"EDINET提出: {description}",
                provenance=provenance,
                detected_at=detected_at,
            )
        )
    counts = {
        "row_count": row_count,
        "candidates": len(candidates),
        "skipped_unclassified": skipped_unclassified,
        "skipped_unparseable": skipped_unparseable,
        "skipped_missing_code": skipped_missing_code,
    }
    return candidates, counts, first_provenance


# ------------------------------------------------------------------ source B


def _credit_signal_values(
    latest: Any,
    previous: Any,
    *,
    ratio: float,
) -> dict[str, Any]:
    return {
        "as_of_date": latest.as_of_date.isoformat(),
        "previous_as_of_date": previous.as_of_date.isoformat(),
        "previous_short_total": previous.short_total,
        "previous_long_total": previous.long_total,
        "short_change": latest.short_change,
        "long_change": latest.long_change,
        "short_total": latest.short_total,
        "long_total": latest.long_total,
        "ratio_change_1w": ratio,
    }


def _credit_margin_weekly_candidates(
    session: Session,
    *,
    detected_at: datetime,
) -> tuple[list[ScreeningCandidate], dict[str, int], Provenance | None]:
    """Week-over-week credit balance surge detection.

    Ratios use the previous week as the denominator. A previous value of
    zero (or an absent previous week) has no usable denominator and is
    skipped — never zero-divided and never replaced with a fabricated ratio.
    """

    codes = list(
        session.scalars(
            select(CreditMarginWeeklyRecord.code).distinct().order_by(CreditMarginWeeklyRecord.code)
        )
    )
    candidates: list[ScreeningCandidate] = []
    codes_with_two_weeks = 0
    codes_without_two_weeks = 0
    last_provenance: Provenance | None = None
    for raw_code in codes:
        code = normalize_credit_margin_code(raw_code)
        points = read_credit_margin_by_code(session, code, limit=2).points
        if len(points) < 2:
            codes_without_two_weeks += 1
            continue
        codes_with_two_weeks += 1
        previous, latest = points[0], points[1]
        found: list[tuple[ScreeningSignal, float, str]] = []
        if previous.short_total > 0 and latest.short_change is not None and latest.short_total > 0:
            short_ratio = latest.short_change / previous.short_total
            if short_ratio >= _SHORT_SURGE_RATIO:
                found.append(
                    (
                        ScreeningSignal.CREDIT_SHORT_SURGE,
                        short_ratio,
                        "売残が前週比+30%以上に急増",
                    )
                )
            elif short_ratio <= _SHORT_DROP_RATIO:
                found.append(
                    (
                        ScreeningSignal.CREDIT_SHORT_DROP,
                        short_ratio,
                        "売残が前週比-30%以下に急減",
                    )
                )
        if previous.long_total > 0 and latest.long_change is not None and latest.long_total > 0:
            long_ratio = latest.long_change / previous.long_total
            if long_ratio >= _LONG_SURGE_RATIO:
                found.append(
                    (
                        ScreeningSignal.CREDIT_LONG_SURGE,
                        long_ratio,
                        "買残が前週比+20%以上に急増",
                    )
                )
        if not found:
            continue
        provenance = latest.provenance.model_copy(
            update={"notes": [*(latest.provenance.notes or []), _SCREENING_NOTE]}
        )
        last_provenance = provenance
        for signal, ratio, condition in found:
            candidates.append(
                ScreeningCandidate(
                    code=code,
                    source=ScreeningSource.CREDIT_MARGIN_WEEKLY,
                    signal=signal,
                    value=_credit_signal_values(latest, previous, ratio=ratio),
                    reason=(
                        f"{condition}（前週 {previous.as_of_date.isoformat()}"
                        f" → {latest.as_of_date.isoformat()}）"
                    ),
                    provenance=provenance,
                    detected_at=detected_at,
                )
            )
    counts = {
        "codes_with_two_weeks": codes_with_two_weeks,
        "codes_without_two_weeks": codes_without_two_weeks,
        "candidates": len(candidates),
    }
    return candidates, counts, last_provenance


# ------------------------------------------------------------------ source C


def _yahoo_screener_candidates(
    provider: Any,
    *,
    market_size: int,
    detected_at: datetime,
) -> tuple[list[ScreeningCandidate], dict[str, int], Provenance | None]:
    request = MarketScreenRequest(
        filters=[
            MarketScreenFilter(field="region", operator="is-in", value=["jp"]),
            MarketScreenFilter(
                field=_LOW_PE_FIELD,
                operator="btwn",
                value=[_LOW_PE_MIN, _LOW_PE_MAX],
            ),
        ],
        size=market_size,
    )
    response = provider.screen(request)
    candidates: list[ScreeningCandidate] = []
    skipped_unparseable = 0
    for row in response.quotes:
        if not isinstance(row, dict):
            skipped_unparseable += 1
            continue
        symbol = str(row.get("symbol") or "")
        match = _YAHOO_SYMBOL_PATTERN.match(symbol)
        if match is None:
            # US symbols, 5-digit J-REIT codes with .T, etc. are outside this
            # 4-digit JPX screener surface — counted, never repaired.
            skipped_unparseable += 1
            continue
        value: dict[str, Any] = {
            "symbol": symbol,
            "filter": {"field": _LOW_PE_FIELD, "min": _LOW_PE_MIN, "max": _LOW_PE_MAX},
        }
        for key in ("regularMarketPrice", "intradaymarketcap", "dayvolume", "percentchange"):
            if isinstance(row.get(key), (int, float)):
                value[key] = row[key]
        candidates.append(
            ScreeningCandidate(
                code=match.group(1),
                source=ScreeningSource.MARKET_SCREENER,
                signal=ScreeningSignal.SCREENER_LOW_PE,
                value=value,
                reason=f"Yahooスクリーナー: PER(TTM)が{_LOW_PE_MAX}倍未満",
                provenance=response.provenance,
                detected_at=detected_at,
            )
        )
    counts = {
        "quotes": len(response.quotes),
        "candidates": len(candidates),
        "skipped_unparseable": skipped_unparseable,
    }
    return candidates, counts, response.provenance


# ------------------------------------------------------- pipeline orchestration


def _screener_policy_admits() -> bool:
    """Personal mode admits the personal-only Yahoo screener; public does not."""

    try:
        from yowayowa.providers.yahoo_screener import YahooScreenerProvider

        YahooScreenerProvider(get_settings())
        return True
    except Exception:
        return False


def _screener_factory() -> Any:
    """Default screener factory (the registry's cached provider)."""

    return yahoo_screener_provider()


def run_screening_pipeline(
    session: Session | None,
    *,
    edinet_path: str | Path | None = None,
    market_size: int = 50,
    run_date: date | None = None,
    screener_mode: str = "policy",
    credit_margin_mode: str = "on",
    screener_factory: Any = None,
) -> ScreeningRunResult:
    """Run every free-source screening signal and combine one result.

    ``screener_mode``: ``"policy"`` runs the screener only in personal mode;
    ``"on"`` forces it (tests); ``"off"`` disables it (recorded coverage).
    ``credit_margin_mode="off"`` disables source B (recorded coverage).
    The EDINET source reads the local daily JSONL
    (``data/edinet-daily.jsonl`` by default; passing an explicit path wins;
    a missing file yields no candidates with recorded coverage). Every
    source is independent: a failure in one source is recorded in
    ``per_source_counts``/``coverage`` and never aborts the remaining ones.
    """

    resolved_date = run_date or datetime.now(UTC).date()
    detected_at = datetime.now(UTC)
    candidates: list[ScreeningCandidate] = []
    per_source_counts: dict[str, Any] = {}
    coverage: dict[str, Any] = {}
    provenances: list[Provenance] = []

    # Source A: local EDINET daily JSONL.
    resolved_edinet = Path(edinet_path) if edinet_path is not None else _DEFAULT_EDINET_PATH
    coverage["edinet_daily_jsonl_path"] = str(resolved_edinet)
    if resolved_edinet.is_file():
        edinet_candidates, edinet_counts, edinet_provenance = _edinet_candidates(
            resolved_edinet,
            detected_at=detected_at,
        )
        candidates.extend(edinet_candidates)
        per_source_counts[ScreeningSource.EDINET_FILING.value] = edinet_counts
        coverage["edinet_daily_record_count"] = edinet_counts["row_count"]
        coverage["edinet_coverage_complete"] = edinet_counts["row_count"] > 0
        if edinet_provenance is not None:
            provenances.append(edinet_provenance)
    else:
        per_source_counts[ScreeningSource.EDINET_FILING.value] = {
            "candidates": 0,
            "reason": "file_missing",
        }
        coverage["edinet_daily_record_count"] = None
        coverage["edinet_coverage_complete"] = None

    # Source B: persisted weekly credit margins (empty DB = 0 candidates,
    # recorded coverage — never a failure).
    if credit_margin_mode != "off" and session is not None:
        credit_candidates, credit_counts, credit_provenance = _credit_margin_weekly_candidates(
            session, detected_at=detected_at
        )
        candidates.extend(credit_candidates)
        per_source_counts[ScreeningSource.CREDIT_MARGIN_WEEKLY.value] = credit_counts
        coverage["credit_margin_weekly_record_count"] = (
            credit_counts["codes_with_two_weeks"] + credit_counts["codes_without_two_weeks"]
        )
        coverage["credit_margin_weekly_coverage_complete"] = (
            credit_counts["codes_with_two_weeks"] > 0
        )
        if credit_provenance is not None:
            provenances.append(credit_provenance)
    else:
        per_source_counts[ScreeningSource.CREDIT_MARGIN_WEEKLY.value] = {
            "candidates": 0,
            "reason": "disabled" if credit_margin_mode == "off" else "no_session",
        }
        coverage["credit_margin_weekly_record_count"] = None
        coverage["credit_margin_weekly_coverage_complete"] = None

    # Source C: Yahoo custom screener (personal mode only; other sources run
    # regardless).
    mode_admits = {
        "on": True,
        "off": False,
        "policy": _screener_policy_admits(),
    }[screener_mode]
    if mode_admits:
        try:
            factory = screener_factory if screener_factory is not None else _screener_factory
            screener_candidates, screener_counts, screener_provenance = _yahoo_screener_candidates(
                factory(),
                market_size=market_size,
                detected_at=detected_at,
            )
            candidates.extend(screener_candidates)
            per_source_counts[ScreeningSource.MARKET_SCREENER.value] = screener_counts
            coverage["market_screener_quote_count"] = screener_counts["quotes"]
            coverage["market_screener_coverage_complete"] = screener_counts["candidates"] > 0
            if screener_provenance is not None:
                provenances.append(screener_provenance)
        except Exception as exc:  # provider/network failure: recorded, never fatal
            per_source_counts[ScreeningSource.MARKET_SCREENER.value] = {
                "candidates": 0,
                "reason": f"provider_error: {type(exc).__name__}",
            }
            coverage["market_screener_quote_count"] = None
            coverage["market_screener_coverage_complete"] = None
    else:
        per_source_counts[ScreeningSource.MARKET_SCREENER.value] = {
            "candidates": 0,
            "reason": "policy_or_off",
        }
        coverage["market_screener_quote_count"] = None
        coverage["market_screener_coverage_complete"] = None

    if provenances:
        providers = [p.provider for p in provenances]
        combined = Provenance(
            provider="yowayowa-screening",
            source="Yowayowa machine screening "
            "(EDINET daily list + credit margin + Yahoo screener)",
            source_url=provenances[0].source_url,
            license_class=(
                LicenseClass.PERSONAL_ONLY
                if any(p.license_class == LicenseClass.PERSONAL_ONLY for p in provenances)
                else LicenseClass.OFFICIAL_PUBLIC
            ),
            retrieved_at=detected_at,
            as_of=detected_at,
            notes=[
                _SCREENING_NOTE,
                "contributing_providers: " + ",".join(providers),
            ],
        )
    else:
        combined = _provenance_none(detected_at)

    return ScreeningRunResult(
        run_date=resolved_date,
        candidates=candidates,
        per_source_counts=per_source_counts,
        coverage=coverage,
        provenance=combined,
    )


def _provenance_none(retrieved_at: datetime) -> Provenance:
    return Provenance(
        provider="yowayowa-screening",
        source="Yowayowa machine screening (no source produced candidates)",
        license_class=LicenseClass.OFFICIAL_PUBLIC,
        retrieved_at=retrieved_at,
        as_of=retrieved_at,
        notes=[_SCREENING_NOTE],
    )


# ------------------------------------------------------------------ persistence


def persist_screening_run(
    session: Session,
    result: ScreeningRunResult,
) -> dict[str, int]:
    """Replace the run date's rows atomically (delete + insert, one commit).

    Re-running for the same run date is therefore idempotent: the second run
    deletes the first run's rows and re-inserts the same keys.
    Returns ``{"inserted": n, "updated": n}`` where ``updated`` counts the
    rows deleted (replaced) by this run.
    """

    try:
        replaced_result = session.execute(
            delete(ScreeningCandidateRecord).where(
                ScreeningCandidateRecord.run_date == result.run_date
            )
        )
        replaced_rowcount: int | None = replaced_result.rowcount  # type: ignore[attr-defined]
        replaced_count = int(replaced_rowcount) if replaced_rowcount is not None else 0
        for candidate in result.candidates:
            session.add(
                ScreeningCandidateRecord(
                    run_date=result.run_date,
                    source=candidate.source.value,
                    code=candidate.code,
                    signal=candidate.signal.value,
                    company_name=candidate.company_name,
                    value=candidate.value,
                    reason=candidate.reason,
                    provenance=candidate.provenance.model_dump(mode="json"),
                    retrieved_at=candidate.provenance.retrieved_at,
                )
            )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return {"inserted": len(result.candidates), "updated": replaced_count}


def read_screening_candidates(
    session: Session,
    *,
    run_date: date | None = None,
    source: str | None = None,
    signal: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Persisted screening candidates, newest run date first, id order.

    ``provenance`` is restored from the persisted JSON (the exact Provenance
    the pipeline captured); a missing/legacy provenance payload degrades to
    ``None`` rather than being fabricated.
    """

    statement = select(ScreeningCandidateRecord).order_by(
        ScreeningCandidateRecord.run_date.desc(),
        ScreeningCandidateRecord.id,
    )
    if run_date is not None:
        statement = statement.where(ScreeningCandidateRecord.run_date == run_date)
    if source is not None:
        statement = statement.where(ScreeningCandidateRecord.source == source)
    if signal is not None:
        statement = statement.where(ScreeningCandidateRecord.signal == signal)
    statement = statement.limit(max(1, min(int(limit), 250)))
    rows = list(session.scalars(statement))
    return [
        {
            "run_date": row.run_date.isoformat(),
            "source": row.source,
            "code": row.code,
            "signal": row.signal,
            "company_name": row.company_name,
            "value": row.value,
            "reason": row.reason,
            "provenance": row.provenance,
            "retrieved_at": row.retrieved_at.isoformat(),
        }
        for row in rows
    ]
