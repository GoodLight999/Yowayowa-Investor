"""Acquisition, persistence and read services for free JPX margin publications."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from datetime import UTC, date, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Final, Literal
from urllib.parse import urljoin, urlparse

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from yowayowa.db import JpxMarginAuxRecord, JpxMarginBalanceRecord
from yowayowa.domain import LicenseClass, Provenance
from yowayowa.jpx_public_models import (
    JpxMarginDailyDetail,
    JpxMarginFlow,
    JpxMarginWatch,
    JpxPremiumCharge,
    JpxPublicIngestResult,
)
from yowayowa.providers.jpx_public_margin import (
    JpxPublicMarginParseError,
    parse_jpx_margin_flow_pdf,
    parse_jpx_margin_watch_xlsx,
    parse_jpx_premium_xlsx,
    parse_jpx_public_balance_pdf,
    safe_jpx_code_from_premium,
)

JpxPublicKind = Literal["balance", "watch", "premium", "flow"]

PAGE_URLS: Final[dict[JpxPublicKind, str]] = {
    "balance": "https://www.jpx.co.jp/markets/statistics-equities/margin/01.html",
    "watch": "https://www.jpx.co.jp/markets/statistics-equities/margin/index.html",
    "premium": "https://www.jpx.co.jp/markets/statistics-equities/margin/02.html",
    "flow": "https://www.jpx.co.jp/markets/statistics-equities/margin/03.html",
}
ARTIFACT_EXTENSIONS: Final[dict[JpxPublicKind, str]] = {
    "balance": ".pdf",
    "watch": ".xlsx",
    "premium": ".xlsx",
    "flow": ".pdf",
}
USER_AGENT = "Yowayowa-Investor/0.1 JPX-public-margin"


class _AnchorCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if tag.casefold() != "a":
            return
        for name, value in attrs:
            if name.casefold() == "href" and value:
                self.hrefs.append(value)


def discover_jpx_artifact_url(
    html: str, *, page_url: str, extension: str
) -> str:
    """Discover the latest artifact from the official page, preserving DOM order."""

    parser = _AnchorCollector()
    parser.feed(html)
    page_host = urlparse(page_url).netloc.casefold()
    for href in parser.hrefs:
        candidate = urljoin(page_url, href)
        parsed = urlparse(candidate)
        if parsed.netloc.casefold() != page_host:
            continue
        if parsed.path.casefold().endswith(extension.casefold()):
            return candidate
    raise JpxPublicMarginParseError(
        f"no {extension} artifact link found on JPX page {page_url}"
    )


def _cache_raw(
    data: bytes,
    *,
    kind: JpxPublicKind,
    source_url: str,
    cache_dir: Path,
) -> Path:
    digest = hashlib.sha256(data).hexdigest()
    suffix = Path(urlparse(source_url).path).suffix or ARTIFACT_EXTENSIONS[kind]
    target_dir = cache_dir / kind
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{digest}{suffix}"
    if not target.exists():
        target.write_bytes(data)
    return target


def _payload(model: BaseModel, *, exclude: set[str]) -> dict[str, object]:
    result = model.model_dump(mode="json", exclude=exclude)
    if not isinstance(result, dict):
        raise TypeError("unexpected pydantic model dump")
    return result


def _add_aux(
    session: Session,
    *,
    kind: str,
    as_of_date: date,
    key: str,
    code: str | None,
    source_code: str | None,
    payload: dict[str, object],
    source_url: str | None,
    source_sha256: str | None,
    published_at: datetime | None,
    retrieved_at: datetime,
) -> None:
    session.add(
        JpxMarginAuxRecord(
            kind=kind,
            as_of_date=as_of_date,
            key=key,
            code=code,
            source_code=source_code,
            payload=payload,
            source_url=source_url,
            source_sha256=source_sha256,
            published_at=published_at,
            retrieved_at=retrieved_at,
        )
    )


def replace_aux_dates(session: Session, *, kind: str, dates: set[date]) -> None:
    if not dates:
        return
    session.execute(
        delete(JpxMarginAuxRecord).where(
            JpxMarginAuxRecord.kind == kind,
            JpxMarginAuxRecord.as_of_date.in_(dates),
        )
    )


def ingest_jpx_public_balance_pdf(
    session: Session,
    data: bytes,
    *,
    source_url: str,
    retrieved_at: datetime | None = None,
) -> JpxPublicIngestResult:
    """Validate the complete PDF, then atomically replace its canonical day."""

    retrieved = retrieved_at or datetime.now(UTC)
    batch = parse_jpx_public_balance_pdf(
        data,
        source_url=source_url,
        retrieved_at=retrieved,
    )
    day = batch.application_date
    try:
        session.execute(
            delete(JpxMarginBalanceRecord).where(
                JpxMarginBalanceRecord.application_date == day
            )
        )
        replace_aux_dates(session, kind="balance_source", dates={day})
        replace_aux_dates(session, kind="balance_detail", dates={day})
        for balance, detail in zip(batch.balances, batch.details, strict=True):
            session.add(
                JpxMarginBalanceRecord(
                    application_date=balance.application_date,
                    code=balance.code,
                    company_name=balance.company_name,
                    isin=balance.isin,
                    market_code=balance.market_code,
                    margin_code=balance.margin_code,
                    short_total=balance.short_total,
                    long_total=balance.long_total,
                    short_negotiable=balance.short_negotiable,
                    short_standardized=balance.short_standardized,
                    long_negotiable=balance.long_negotiable,
                    long_standardized=balance.long_standardized,
                    short_total_value=balance.short_total_value,
                    long_total_value=balance.long_total_value,
                    short_negotiable_value=balance.short_negotiable_value,
                    short_standardized_value=balance.short_standardized_value,
                    long_negotiable_value=balance.long_negotiable_value,
                    long_standardized_value=balance.long_standardized_value,
                    source_url=source_url,
                    retrieved_at=retrieved,
                )
            )
            _add_aux(
                session,
                kind="balance_source",
                as_of_date=day,
                key=balance.code,
                code=balance.code,
                source_code=None,
                payload={"provenance": balance.provenance.model_dump(mode="json")},
                source_url=source_url,
                source_sha256=batch.source_sha256,
                published_at=batch.published_at,
                retrieved_at=retrieved,
            )
            _add_aux(
                session,
                kind="balance_detail",
                as_of_date=day,
                key=detail.code,
                code=detail.code,
                source_code=None,
                payload=_payload(
                    detail,
                    exclude={
                        "application_date",
                        "code",
                        "published_at",
                        "retrieved_at",
                        "source_sha256",
                        "provenance",
                    },
                ),
                source_url=source_url,
                source_sha256=batch.source_sha256,
                published_at=detail.published_at,
                retrieved_at=retrieved,
            )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return JpxPublicIngestResult(
        kind="balance",
        row_count=len(batch.balances),
        as_of_dates=[day],
        source_url=source_url,
        source_sha256=batch.source_sha256,
    )


def _detail_from_aux(row: JpxMarginAuxRecord) -> JpxMarginDailyDetail:
    if row.code is None or row.published_at is None or row.source_sha256 is None:
        raise ValueError("incomplete balance_detail persistence row")
    return JpxMarginDailyDetail.model_validate(
        {
            **row.payload,
            "application_date": row.as_of_date,
            "code": row.code,
            "published_at": row.published_at,
            "retrieved_at": row.retrieved_at,
            "source_sha256": row.source_sha256,
            "provenance": _aux_provenance(
                row, default_source="JPX 銘柄別信用取引残高（日次） public PDF"
            ),
        }
    )


def _watch_from_aux(row: JpxMarginAuxRecord) -> JpxMarginWatch:
    if row.code is None or row.published_at is None or row.source_sha256 is None:
        raise ValueError("incomplete watch persistence row")
    return JpxMarginWatch.model_validate(
        {
            **row.payload,
            "application_date": row.as_of_date,
            "code": row.code,
            "published_at": row.published_at,
            "retrieved_at": row.retrieved_at,
            "source_sha256": row.source_sha256,
            "provenance": _aux_provenance(
                row, default_source="JPX 日々公表銘柄等信用取引残高 XLSX"
            ),
        }
    )


def _premium_from_aux(row: JpxMarginAuxRecord) -> JpxPremiumCharge:
    if row.source_code is None or row.published_at is None or row.source_sha256 is None:
        raise ValueError("incomplete premium persistence row")
    return JpxPremiumCharge.model_validate(
        {
            **row.payload,
            "trade_date": row.as_of_date,
            "source_code": row.source_code,
            "resolved_jpx_code": row.code,
            "published_at": row.published_at,
            "retrieved_at": row.retrieved_at,
            "source_sha256": row.source_sha256,
            "provenance": _aux_provenance(row, default_source="JPX 品貸料率一覧 XLSX"),
        }
    )


def _flow_from_aux(row: JpxMarginAuxRecord) -> JpxMarginFlow:
    if row.code is None or row.published_at is None or row.source_sha256 is None:
        raise ValueError("incomplete flow persistence row")
    return JpxMarginFlow.model_validate(
        {
            **row.payload,
            "trade_date": row.as_of_date,
            "code": row.code,
            "published_at": row.published_at,
            "retrieved_at": row.retrieved_at,
            "source_sha256": row.source_sha256,
            "provenance": _aux_provenance(row, default_source="JPX 信用取引売買比率 PDF"),
        }
    )


def _aux_provenance(
    row: JpxMarginAuxRecord, *, default_source: str
) -> Provenance:
    raw = row.payload.get("provenance")
    if isinstance(raw, dict):
        return Provenance.model_validate(raw)
    notes: list[str] = []
    if row.source_sha256:
        notes.append(f"sha256:{row.source_sha256}")
    if row.published_at:
        notes.append(f"published_at:{row.published_at.isoformat()}")
    return Provenance(
        provider="jpx_public_margin",
        source=default_source,
        source_url=row.source_url,
        license_class=LicenseClass.OFFICIAL_PUBLIC,
        retrieved_at=row.retrieved_at,
        as_of=row.as_of_date,
        notes=notes,
    )


def _validate_watch_against_balance(
    session: Session, rows: list[JpxMarginWatch]
) -> None:
    if not rows:
        return
    application_date = rows[0].application_date
    balances = {
        row.code: row
        for row in session.scalars(
            select(JpxMarginBalanceRecord).where(
                JpxMarginBalanceRecord.application_date == application_date
            )
        )
    }
    if not balances:
        return
    details = {
        row.code: _detail_from_aux(row)
        for row in session.scalars(
            select(JpxMarginAuxRecord).where(
                JpxMarginAuxRecord.kind == "balance_detail",
                JpxMarginAuxRecord.as_of_date == application_date,
            )
        )
        if row.code is not None
    }
    missing = sorted(row.code for row in rows if row.code not in balances)
    if missing:
        raise JpxPublicMarginParseError(
            "watch/all-issue cross-check missing codes: " + ",".join(missing[:10])
        )
    for watch in rows:
        balance = balances[watch.code]
        required_pairs = (
            ("short_total", watch.short_total, balance.short_total),
            ("long_total", watch.long_total, balance.long_total),
            ("short_negotiable", watch.short_negotiable, balance.short_negotiable),
            ("short_standardized", watch.short_standardized, balance.short_standardized),
            ("long_negotiable", watch.long_negotiable, balance.long_negotiable),
            ("long_standardized", watch.long_standardized, balance.long_standardized),
        )
        for field, left, right in required_pairs:
            if left != right:
                raise JpxPublicMarginParseError(
                    f"watch/all-issue mismatch {watch.code}.{field}: {left} != {right}"
                )
        detail = details.get(watch.code)
        if detail is None:
            continue
        optional_pairs = (
            ("short_change", watch.short_change, detail.short_source_change),
            ("long_change", watch.long_change, detail.long_source_change),
            (
                "short_listed_ratio_pct",
                watch.short_listed_ratio_pct,
                detail.short_listed_ratio_pct,
            ),
            (
                "long_listed_ratio_pct",
                watch.long_listed_ratio_pct,
                detail.long_listed_ratio_pct,
            ),
            (
                "short_negotiable_change",
                watch.short_negotiable_change,
                detail.short_negotiable_source_change,
            ),
            (
                "short_standardized_change",
                watch.short_standardized_change,
                detail.short_standardized_source_change,
            ),
            (
                "long_negotiable_change",
                watch.long_negotiable_change,
                detail.long_negotiable_source_change,
            ),
            (
                "long_standardized_change",
                watch.long_standardized_change,
                detail.long_standardized_source_change,
            ),
        )
        for field, left, right in optional_pairs:
            if left is not None and right is not None and left != right:
                raise JpxPublicMarginParseError(
                    f"watch/all-issue mismatch {watch.code}.{field}: {left} != {right}"
                )


def ingest_jpx_margin_watch_xlsx(
    session: Session,
    data: bytes,
    *,
    source_url: str,
    retrieved_at: datetime | None = None,
) -> JpxPublicIngestResult:
    retrieved = retrieved_at or datetime.now(UTC)
    rows = parse_jpx_margin_watch_xlsx(
        data, source_url=source_url, retrieved_at=retrieved
    )
    _validate_watch_against_balance(session, rows)
    dates = {row.application_date for row in rows}
    try:
        replace_aux_dates(session, kind="watch", dates=dates)
        for row in rows:
            _add_aux(
                session,
                kind="watch",
                as_of_date=row.application_date,
                key=row.code,
                code=row.code,
                source_code=None,
                payload=_payload(
                    row,
                    exclude={
                        "application_date",
                        "code",
                        "published_at",
                        "retrieved_at",
                        "source_sha256",
                        "provenance",
                    },
                ),
                source_url=source_url,
                source_sha256=row.source_sha256,
                published_at=row.published_at,
                retrieved_at=retrieved,
            )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return JpxPublicIngestResult(
        kind="watch",
        row_count=len(rows),
        as_of_dates=sorted(dates),
        source_url=source_url,
        source_sha256=rows[0].source_sha256,
    )


def ingest_jpx_premium_xlsx(
    session: Session,
    data: bytes,
    *,
    source_url: str,
    retrieved_at: datetime | None = None,
) -> JpxPublicIngestResult:
    retrieved = retrieved_at or datetime.now(UTC)
    rows = parse_jpx_premium_xlsx(
        data, source_url=source_url, retrieved_at=retrieved
    )
    dates = {row.trade_date for row in rows}
    known_by_date: dict[date, set[str]] = {}
    resolved_rows: list[JpxPremiumCharge] = []
    for row in rows:
        known = known_by_date.get(row.trade_date)
        if known is None:
            known = set(
                session.scalars(
                    select(JpxMarginBalanceRecord.code).where(
                        JpxMarginBalanceRecord.application_date == row.trade_date
                    )
                )
            )
            known_by_date[row.trade_date] = known
        resolved_rows.append(
            row.model_copy(
                update={
                    "resolved_jpx_code": safe_jpx_code_from_premium(
                        row.source_code, known
                    )
                }
            )
        )
    try:
        replace_aux_dates(session, kind="premium", dates=dates)
        for row in resolved_rows:
            _add_aux(
                session,
                kind="premium",
                as_of_date=row.trade_date,
                key=f"{row.source_code}:{row.exchange or ''}",
                code=row.resolved_jpx_code,
                source_code=row.source_code,
                payload=_payload(
                    row,
                    exclude={
                        "trade_date",
                        "published_at",
                        "retrieved_at",
                        "source_sha256",
                        "provenance",
                    },
                ),
                source_url=source_url,
                source_sha256=row.source_sha256,
                published_at=row.published_at,
                retrieved_at=retrieved,
            )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return JpxPublicIngestResult(
        kind="premium",
        row_count=len(resolved_rows),
        as_of_dates=sorted(dates),
        source_url=source_url,
        source_sha256=resolved_rows[0].source_sha256,
    )


def ingest_jpx_margin_flow_pdf(
    session: Session,
    data: bytes,
    *,
    source_url: str,
    retrieved_at: datetime | None = None,
) -> JpxPublicIngestResult:
    retrieved = retrieved_at or datetime.now(UTC)
    rows = parse_jpx_margin_flow_pdf(
        data, source_url=source_url, retrieved_at=retrieved
    )
    dates = {row.trade_date for row in rows}
    try:
        replace_aux_dates(session, kind="flow", dates=dates)
        for row in rows:
            _add_aux(
                session,
                kind="flow",
                as_of_date=row.trade_date,
                key=row.code,
                code=row.code,
                source_code=None,
                payload=_payload(
                    row,
                    exclude={
                        "trade_date",
                        "code",
                        "published_at",
                        "retrieved_at",
                        "source_sha256",
                        "provenance",
                    },
                ),
                source_url=source_url,
                source_sha256=row.source_sha256,
                published_at=row.published_at,
                retrieved_at=retrieved,
            )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return JpxPublicIngestResult(
        kind="flow",
        row_count=len(rows),
        as_of_dates=sorted(dates),
        source_url=source_url,
        source_sha256=rows[0].source_sha256,
    )


def ingest_jpx_public_artifact(
    session: Session,
    data: bytes,
    *,
    kind: JpxPublicKind,
    source_url: str,
    retrieved_at: datetime | None = None,
) -> JpxPublicIngestResult:
    if kind == "balance":
        return ingest_jpx_public_balance_pdf(
            session, data, source_url=source_url, retrieved_at=retrieved_at
        )
    if kind == "watch":
        return ingest_jpx_margin_watch_xlsx(
            session, data, source_url=source_url, retrieved_at=retrieved_at
        )
    if kind == "premium":
        return ingest_jpx_premium_xlsx(
            session, data, source_url=source_url, retrieved_at=retrieved_at
        )
    return ingest_jpx_margin_flow_pdf(
        session, data, source_url=source_url, retrieved_at=retrieved_at
    )


def sync_jpx_public_margin(
    session: Session,
    *,
    kinds: Iterable[JpxPublicKind] = ("balance", "watch", "premium", "flow"),
    cache_dir: Path = Path("./data/jpx/raw"),
    timeout_seconds: float = 30.0,
) -> list[JpxPublicIngestResult]:
    """Fetch latest official artifacts and ingest them in dependency order."""

    requested = list(dict.fromkeys(kinds))
    invalid = [kind for kind in requested if kind not in PAGE_URLS]
    if invalid:
        raise ValueError(f"unknown JPX public kinds: {invalid}")
    order: list[JpxPublicKind] = [
        kind
        for kind in ("balance", "watch", "premium", "flow")
        if kind in requested
    ]
    results: list[JpxPublicIngestResult] = []
    with httpx.Client(
        follow_redirects=True,
        timeout=timeout_seconds,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        for kind in order:
            page_url = PAGE_URLS[kind]
            page = client.get(page_url)
            page.raise_for_status()
            artifact_url = discover_jpx_artifact_url(
                page.text,
                page_url=page_url,
                extension=ARTIFACT_EXTENSIONS[kind],
            )
            retrieved_at = datetime.now(UTC)
            response = client.get(artifact_url)
            response.raise_for_status()
            raw = response.content
            cached = _cache_raw(
                raw,
                kind=kind,
                source_url=artifact_url,
                cache_dir=cache_dir,
            )
            result = ingest_jpx_public_artifact(
                session,
                raw,
                kind=kind,
                source_url=artifact_url,
                retrieved_at=retrieved_at,
            )
            results.append(result.model_copy(update={"cached_path": str(cached)}))
    return results


def read_jpx_margin_details(
    session: Session, code: str, *, limit: int = 30
) -> list[JpxMarginDailyDetail]:
    rows = list(
        session.scalars(
            select(JpxMarginAuxRecord)
            .where(
                JpxMarginAuxRecord.kind == "balance_detail",
                JpxMarginAuxRecord.code == code,
            )
            .order_by(JpxMarginAuxRecord.as_of_date.desc())
            .limit(limit)
        )
    )
    rows.reverse()
    return [_detail_from_aux(row) for row in rows]


def read_jpx_margin_watch(
    session: Session, code: str, *, limit: int = 30
) -> list[JpxMarginWatch]:
    rows = list(
        session.scalars(
            select(JpxMarginAuxRecord)
            .where(
                JpxMarginAuxRecord.kind == "watch",
                JpxMarginAuxRecord.code == code,
            )
            .order_by(JpxMarginAuxRecord.as_of_date.desc())
            .limit(limit)
        )
    )
    rows.reverse()
    return [_watch_from_aux(row) for row in rows]


def read_jpx_premium(
    session: Session, source_code: str, *, limit: int = 30
) -> list[JpxPremiumCharge]:
    rows = list(
        session.scalars(
            select(JpxMarginAuxRecord)
            .where(
                JpxMarginAuxRecord.kind == "premium",
                JpxMarginAuxRecord.source_code == source_code,
            )
            .order_by(JpxMarginAuxRecord.as_of_date.desc())
            .limit(limit)
        )
    )
    rows.reverse()
    return [_premium_from_aux(row) for row in rows]


def read_jpx_margin_flow(
    session: Session, code: str, *, limit: int = 30
) -> list[JpxMarginFlow]:
    rows = list(
        session.scalars(
            select(JpxMarginAuxRecord)
            .where(
                JpxMarginAuxRecord.kind == "flow",
                JpxMarginAuxRecord.code == code,
            )
            .order_by(JpxMarginAuxRecord.as_of_date.desc())
            .limit(limit)
        )
    )
    rows.reverse()
    return [_flow_from_aux(row) for row in rows]


__all__ = [
    "JpxPublicKind",
    "discover_jpx_artifact_url",
    "ingest_jpx_margin_flow_pdf",
    "ingest_jpx_margin_watch_xlsx",
    "ingest_jpx_premium_xlsx",
    "ingest_jpx_public_artifact",
    "ingest_jpx_public_balance_pdf",
    "read_jpx_margin_details",
    "read_jpx_margin_flow",
    "read_jpx_margin_watch",
    "read_jpx_premium",
    "replace_aux_dates",
    "sync_jpx_public_margin",
]
