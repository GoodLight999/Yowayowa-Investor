from __future__ import annotations

from datetime import UTC, datetime

from yowayowa.institutional_models import (
    HoldingChangeStatus,
    InstitutionalHolding,
    ThirteenFHoldingChange,
    ThirteenFManagerReport,
)
from yowayowa.providers.sec_13f import Sec13FProvider


def _holding_key(item: InstitutionalHolding) -> tuple[str, str, str]:
    return (
        item.cusip,
        (item.title_of_class or "").casefold(),
        (item.put_call or "").casefold(),
    )


def _change_status(current: float, previous: float) -> HoldingChangeStatus:
    if previous == 0 and current != 0:
        return "new"
    if current == 0 and previous != 0:
        return "exited"
    if current > previous:
        return "increased"
    if current < previous:
        return "decreased"
    return "unchanged"


def _compare_holdings(
    current: list[InstitutionalHolding],
    previous: list[InstitutionalHolding],
) -> list[ThirteenFHoldingChange]:
    current_map = {_holding_key(item): item for item in current}
    previous_map = {_holding_key(item): item for item in previous}
    changes: list[ThirteenFHoldingChange] = []
    for key in current_map.keys() | previous_map.keys():
        current_item = current_map.get(key)
        previous_item = previous_map.get(key)
        current_shares = current_item.shares_or_principal if current_item else 0.0
        previous_shares = previous_item.shares_or_principal if previous_item else 0.0
        current_value = current_item.value_usd if current_item else 0
        previous_value = previous_item.value_usd if previous_item else 0
        fraction = (
            (current_shares - previous_shares) / previous_shares if previous_shares != 0 else None
        )
        representative = current_item or previous_item
        assert representative is not None
        changes.append(
            ThirteenFHoldingChange(
                issuer=representative.issuer,
                title_of_class=representative.title_of_class,
                cusip=representative.cusip,
                put_call=representative.put_call,
                status=_change_status(current_shares, previous_shares),
                current_shares=current_shares,
                previous_shares=previous_shares,
                share_change=current_shares - previous_shares,
                share_change_fraction=fraction,
                current_value_usd=current_value,
                previous_value_usd=previous_value,
            )
        )
    priority = {
        "new": 0,
        "increased": 1,
        "decreased": 2,
        "exited": 3,
        "unchanged": 4,
    }
    return sorted(
        changes,
        key=lambda item: (
            priority[item.status],
            -abs(item.current_value_usd - item.previous_value_usd),
            item.issuer.casefold(),
        ),
    )


def manager_report(
    provider: Sec13FProvider,
    cik: str | int,
    *,
    quarters: int = 2,
) -> ThirteenFManagerReport:
    if not 1 <= quarters <= 8:
        raise ValueError("13F quarter count must be between 1 and 8")
    normalized = provider.normalize_cik(cik)
    manager_name, refs = provider.recent_references(normalized, limit=quarters)
    filings = []
    unavailable: list[str] = []
    for ref in refs:
        try:
            filings.append(provider.filing(normalized, ref))
        except Exception:
            unavailable.append(ref.accession_number)
    filings.sort(key=lambda item: (item.report_date, item.filing_date), reverse=True)
    changes = (
        _compare_holdings(filings[0].holdings, filings[1].holdings) if len(filings) >= 2 else []
    )
    return ThirteenFManagerReport(
        cik=normalized,
        manager_name=manager_name,
        filings=filings,
        changes=changes,
        unavailable_filings=unavailable,
        retrieved_at=datetime.now(UTC),
        notes=[
            "13F is a delayed quarterly disclosure. Report dates describe quarter-end "
            "positions and do not imply current ownership.",
            "Quarter-over-quarter changes compare reported shares/principal by CUSIP, "
            "class and put/call designation.",
        ],
    )
