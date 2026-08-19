from __future__ import annotations

import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from yowayowa.domain import Provenance
from yowayowa.edinet_models import (
    EdinetDocumentList,
    EdinetDocumentSummary,
    EdinetFact,
    EdinetFactSearchResult,
    EdinetFinancials,
    EdinetMetricObservation,
)
from yowayowa.providers.edinet import EdinetClient, EdinetCsvPayload

_CANONICAL_ALIASES: dict[str, tuple[str, ...]] = {
    "revenue": (
        "NetSales",
        "Revenue",
        "RevenueIFRS",
        "RevenueFromContractsWithCustomers",
        "OperatingRevenue1",
    ),
    "gross_profit": ("GrossProfit", "GrossProfitLoss"),
    "operating_income": (
        "OperatingIncome",
        "OperatingProfitLoss",
        "OperatingProfitLossIFRS",
    ),
    "ordinary_income": ("OrdinaryIncome", "OrdinaryIncomeLoss"),
    "net_income": (
        "ProfitLoss",
        "ProfitLossAttributableToOwnersOfParent",
        "ProfitLossAttributableToOwnersOfParentIFRS",
        "NetIncomeLoss",
    ),
    "assets": ("Assets", "AssetsIFRS"),
    "current_assets": ("CurrentAssets", "CurrentAssetsIFRS"),
    "liabilities": ("Liabilities", "LiabilitiesIFRS"),
    "current_liabilities": ("CurrentLiabilities", "CurrentLiabilitiesIFRS"),
    "equity": (
        "NetAssets",
        "Equity",
        "EquityIFRS",
        "EquityAttributableToOwnersOfParent",
        "EquityAttributableToOwnersOfParentIFRS",
    ),
    "cash": (
        "CashAndDeposits",
        "CashAndCashEquivalents",
        "CashAndCashEquivalentsIFRS",
    ),
    "operating_cash_flow": (
        "NetCashProvidedByUsedInOperatingActivities",
        "CashFlowsFromUsedInOperatingActivities",
        "CashFlowsFromUsedInOperatingActivitiesIFRS",
    ),
    "investing_cash_flow": (
        "NetCashProvidedByUsedInInvestingActivities",
        "CashFlowsFromUsedInInvestingActivities",
        "CashFlowsFromUsedInInvestingActivitiesIFRS",
    ),
    "financing_cash_flow": (
        "NetCashProvidedByUsedInFinancingActivities",
        "CashFlowsFromUsedInFinancingActivities",
        "CashFlowsFromUsedInFinancingActivitiesIFRS",
    ),
    "eps_basic": (
        "BasicEarningsLossPerShare",
        "BasicEarningsLossPerShareIFRS",
        "BasicEarningsPerShare",
    ),
    "eps_diluted": (
        "DilutedEarningsPerShare",
        "DilutedEarningsLossPerShare",
        "DilutedEarningsLossPerShareIFRS",
    ),
}

_METADATA_ALIASES: dict[str, tuple[str, ...]] = {
    "company_name": ("FilerNameInJapaneseDEI", "FilerNameInEnglishDEI"),
    "edinet_code": ("EDINETCodeDEI",),
    "security_code": ("SecurityCodeDEI",),
    "accounting_standard": ("AccountingStandardsDEI",),
    "document_type": ("DocumentTypeDEI",),
    "period_start": ("CurrentPeriodStartDateDEI",),
    "period_end": ("CurrentPeriodEndDateDEI",),
}

_DIMENSION_CONTEXT_TOKENS = ("member", "segment", "axis", "scenario")


def _local_name(element_id: str) -> str:
    token = element_id.rsplit(":", 1)[-1]
    return token.rsplit("}", 1)[-1]


def _flag(value: object) -> bool:
    return str(value or "").strip() == "1"


def _optional_date(value: object) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _optional_datetime(value: object) -> datetime | None:
    text = str(value or "").strip().replace("Z", "+00:00")
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def normalize_security_code(value: str) -> str:
    normalized = value.strip()
    if not normalized.isascii() or not normalized.isdigit() or len(normalized) not in {4, 5}:
        raise ValueError("Japanese security code must contain four or five ASCII digits")
    return normalized + "0" if len(normalized) == 4 else normalized


def document_list(
    client: EdinetClient,
    filing_date: date,
    *,
    security_code: str | None = None,
    edinet_code: str | None = None,
    doc_type_codes: set[str] | None = None,
    csv_only: bool = False,
    downloadable_only: bool = True,
    limit: int = 500,
) -> EdinetDocumentList:
    payload = client.documents(filing_date)
    raw_results = payload.get("results")
    if not isinstance(raw_results, list):
        raw_results = []
    normalized_security_code = (
        normalize_security_code(security_code) if security_code is not None else None
    )
    normalized_edinet_code = edinet_code.strip().upper() if edinet_code else None
    normalized_doc_types = {code.strip() for code in (doc_type_codes or set()) if code.strip()}

    documents: list[EdinetDocumentSummary] = []
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        sec_code = str(item.get("secCode") or "").strip() or None
        item_edinet_code = str(item.get("edinetCode") or "").strip() or None
        doc_type = str(item.get("docTypeCode") or "").strip() or None
        legal_status = str(item.get("legalStatus") or "").strip() or None
        if normalized_security_code and sec_code != normalized_security_code:
            continue
        if normalized_edinet_code and item_edinet_code != normalized_edinet_code:
            continue
        if normalized_doc_types and doc_type not in normalized_doc_types:
            continue
        if csv_only and not _flag(item.get("csvFlag")):
            continue
        if downloadable_only and legal_status not in {"1", "2", None}:
            continue
        if downloadable_only and _flag(item.get("withdrawalStatus")):
            continue

        doc_id = str(item.get("docID") or "").strip().upper()
        filer_name = str(item.get("filerName") or "").strip()
        if not doc_id or not filer_name:
            continue
        documents.append(
            EdinetDocumentSummary(
                doc_id=doc_id,
                edinet_code=item_edinet_code,
                security_code=sec_code,
                filer_name=filer_name,
                fund_code=str(item.get("fundCode") or "").strip() or None,
                ordinance_code=str(item.get("ordinanceCode") or "").strip() or None,
                form_code=str(item.get("formCode") or "").strip() or None,
                doc_type_code=doc_type,
                description=str(item.get("docDescription") or "").strip() or None,
                period_start=_optional_date(item.get("periodStart")),
                period_end=_optional_date(item.get("periodEnd")),
                submitted_at=_optional_datetime(item.get("submitDateTime")),
                xbrl_available=_flag(item.get("xbrlFlag")),
                csv_available=_flag(item.get("csvFlag")),
                legal_status=legal_status,
            )
        )

    documents.sort(
        key=lambda item: (
            item.submitted_at.isoformat() if item.submitted_at is not None else "",
            item.doc_id,
        ),
        reverse=True,
    )
    matched_count = len(documents)
    provenance = Provenance.model_validate(payload["provenance"])
    return EdinetDocumentList(
        filing_date=filing_date,
        documents=documents[:limit],
        matched_count=matched_count,
        total_count=len(raw_results),
        provenance=provenance,
    )


def _numeric_value(value: str) -> Decimal | None:
    text = unicodedata.normalize("NFKC", value).strip().replace(",", "")
    if not text or text in {"-", "—", "―"}:
        return None
    if text.startswith("△"):
        text = "-" + text[1:]
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _metadata_value(facts: list[EdinetFact], aliases: tuple[str, ...]) -> str | None:
    by_name: dict[str, list[EdinetFact]] = {}
    for fact in facts:
        by_name.setdefault(_local_name(fact.element_id), []).append(fact)
    for alias in aliases:
        candidates = by_name.get(alias, [])
        for candidate in sorted(candidates, key=_fact_rank):
            value = candidate.value.strip()
            if value:
                return value
    return None


def _fact_rank(fact: EdinetFact) -> tuple[int, int, int, str, str]:
    context = fact.context_id.casefold()
    relative = (fact.relative_year or "").casefold()
    consolidation = (fact.consolidation or "").casefold()

    if "current" in context or "当" in relative:
        period_rank = 0
    elif "prior1" in context or "前" in relative:
        period_rank = 1
    else:
        period_rank = 2

    if ("consolidated" in context and "nonconsolidated" not in context) or (
        "連結" in consolidation and "個別" not in consolidation
    ):
        consolidation_rank = 0
    elif "nonconsolidated" in context or "個別" in consolidation:
        consolidation_rank = 1
    else:
        consolidation_rank = 2

    dimension_rank = int(any(token in context for token in _DIMENSION_CONTEXT_TOKENS))
    return period_rank, consolidation_rank, dimension_rank, context, fact.element_id


def _canonical_observations(
    payload: EdinetCsvPayload,
    aliases: tuple[str, ...],
) -> list[EdinetMetricObservation]:
    alias_order = {name: index for index, name in enumerate(aliases)}
    matches = [fact for fact in payload.facts if _local_name(fact.element_id) in alias_order]
    matches.sort(
        key=lambda fact: (
            _fact_rank(fact),
            alias_order[_local_name(fact.element_id)],
            fact.source_file,
        )
    )
    seen: set[tuple[str, str, str | None, str]] = set()
    observations: list[EdinetMetricObservation] = []
    for fact in matches:
        numeric = _numeric_value(fact.value)
        if numeric is None:
            continue
        identity = (fact.element_id, fact.context_id, fact.unit_id, fact.value)
        if identity in seen:
            continue
        seen.add(identity)
        observations.append(
            EdinetMetricObservation(
                **fact.model_dump(),
                numeric_value=numeric,
            )
        )
    return observations


def financials(client: EdinetClient, doc_id: str) -> EdinetFinancials:
    normalized = client.normalize_doc_id(doc_id)
    payload = client.csv_facts(normalized)
    metrics = {
        key: observations
        for key, aliases in _CANONICAL_ALIASES.items()
        if (observations := _canonical_observations(payload, aliases))
    }
    metadata = {
        key: _metadata_value(payload.facts, aliases) for key, aliases in _METADATA_ALIASES.items()
    }
    return EdinetFinancials(
        doc_id=normalized,
        company_name=metadata["company_name"],
        edinet_code=metadata["edinet_code"],
        security_code=metadata["security_code"],
        accounting_standard=metadata["accounting_standard"],
        document_type=metadata["document_type"],
        period_start=_optional_date(metadata["period_start"]),
        period_end=_optional_date(metadata["period_end"]),
        metrics=metrics,
        unavailable_metrics=[key for key in _CANONICAL_ALIASES if key not in metrics],
        fact_count=len(payload.facts),
        source_files=payload.source_files,
        parse_warnings=payload.parse_warnings,
        provenance=client.provenance_for_document(normalized),
    )


def search_facts(
    client: EdinetClient,
    doc_id: str,
    *,
    query: str | None = None,
    limit: int = 200,
) -> EdinetFactSearchResult:
    normalized = client.normalize_doc_id(doc_id)
    payload = client.csv_facts(normalized)
    needle = query.strip().casefold() if query else None
    if needle:
        matches = [
            fact
            for fact in payload.facts
            if needle
            in " ".join(
                (
                    fact.element_id,
                    fact.label,
                    fact.context_id,
                    fact.relative_year or "",
                    fact.consolidation or "",
                    fact.value,
                )
            ).casefold()
        ]
    else:
        matches = payload.facts
    return EdinetFactSearchResult(
        doc_id=normalized,
        query=query,
        facts=matches[:limit],
        matched_count=len(matches),
        total_count=len(payload.facts),
        source_files=payload.source_files,
        parse_warnings=payload.parse_warnings,
        provenance=client.provenance_for_document(normalized),
    )
