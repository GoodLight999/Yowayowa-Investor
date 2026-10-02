"""Auditable SEC filing-to-bar PBR supply, separate from immutable price storage.

Companyfacts must remain unabridged: deduplication by period alone would replace
original disclosures with future restatements. All financial inputs in a row
come from ONE accession. Split events change share units, not the engine.
"""

from __future__ import annotations

import hashlib
import json
import math
from itertools import pairwise
from pathlib import Path
from typing import Any

FORMS = frozenset({"10-K", "10-Q", "10-K/A", "10-Q/A", "20-F", "20-F/A"})
EQUITY_TAGS = (
    "StockholdersEquity",
    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
)
SHARE_TAGS = (
    ("dei", "EntityCommonStockSharesOutstanding", "instant"),
    ("us-gaap", "CommonStockSharesOutstanding", "instant"),
    ("us-gaap", "WeightedAverageNumberOfSharesOutstandingBasic", "period_average"),
)
SPLIT_TAG = "StockholdersEquityNoteStockSplitConversionRatio1"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fact_rows(payload: dict[str, Any], namespace: str, tag: str, unit: str) -> list[dict[str, Any]]:
    rows = payload.get("facts", {}).get(namespace, {}).get(tag, {}).get("units", {}).get(unit, [])
    result = []
    for row in rows:
        try:
            valid = (
                row.get("form") in FORMS
                and row["accn"]
                and row["end"] <= row["filed"]
                and math.isfinite(float(row["val"]))
            )
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if valid:
            result.append(dict(row))
    return result


def filing_inputs(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Latest equity and compatible shares within each filing, never across filings."""
    equity: dict[str, list[dict[str, Any]]] = {}
    for priority, tag in enumerate(EQUITY_TAGS):
        for row in fact_rows(payload, "us-gaap", tag, "USD"):
            equity.setdefault(row["accn"], []).append({**row, "tag": tag, "priority": priority})
    records = []
    for accn, choices in equity.items():
        latest_end = max(r["end"] for r in choices)
        eq = min((r for r in choices if r["end"] == latest_end), key=lambda r: r["priority"])
        shares = None
        for namespace, tag, basis in SHARE_TAGS:
            candidates = [
                r
                for r in fact_rows(payload, namespace, tag, "shares")
                if r["accn"] == accn
                and r["filed"] == eq["filed"]
                and float(r["val"]) > 0
                and (
                    (basis == "instant" and eq["end"] <= r["end"] <= eq["filed"])
                    or (basis == "period_average" and r["end"] == eq["end"] and r.get("start"))
                )
            ]
            if candidates:
                # Newest instant; shortest compatible duration for basic shares.
                chosen = max(candidates, key=lambda r: (r["end"], r.get("start", "")))
                shares = {**chosen, "tag": f"{namespace}:{tag}", "basis": basis}
                break
        records.append({"equity": eq, "shares": shares, "accession": accn, "filed": eq["filed"]})
    return sorted(records, key=lambda r: (r["filed"], r["equity"]["end"], r["accession"]))


def split_calendar(bars: list[dict[str, Any]], payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Detect observed unit discontinuities; later SEC tags are audit evidence only.

    Snap to standard corporate-action ratios within 15%, retaining the observed
    ratio and residual. Do NOT use the raw price ratio as a share multiplier:
    it contains overnight market return. No SEC publication moves the event date.
    """
    tags = fact_rows(payload, "us-gaap", SPLIT_TAG, "pure")
    ratios = (2.0, 3.0, 4.0, 5.0, 7.0, 10.0, 20.0)
    events = []
    ordered = sorted(bars, key=lambda r: str(r["as_of"]))
    for previous, current in pairwise(ordered):
        observed = float(previous["close"]) / float(current["close"])
        opening = float(previous["close"]) / float(current.get("open", current["close"]))
        # Overnight unit change is observed at the opening, before intraday return.
        ratio = min(ratios, key=lambda r: abs(opening / r - 1))
        if abs(observed / ratio - 1) > 0.15:
            continue
        # Require open AND close to be consistent with a unit change.
        if abs(opening / ratio - 1) > 0.15:
            continue
        day = str(current["as_of"])[:10]
        evidence = [r for r in tags if float(r["val"]) == ratio and r["filed"] >= day]
        first = min(evidence, key=lambda r: r["filed"]) if evidence else None
        events.append(
            {
                "effective_date": day,
                "ratio": ratio,
                "observed_close_ratio": observed,
                "previous_close": float(previous["close"]),
                "close": float(current["close"]),
                "residual_return": float(current["close"]) * ratio / float(previous["close"]) - 1,
                "method": "observed_price_discontinuity_standard_ratio",
                "sec_first_filed": first["filed"] if first else None,
                "sec_accession": first["accn"] if first else None,
                "sec_tag": f"us-gaap:{SPLIT_TAG}" if first else None,
                "sec_is_retrospective_validation_only": True,
            }
        )
    return events


def build_pbr(
    symbol: str, bars: list[dict[str, Any]], payload: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    records = filing_inputs(payload)
    events = split_calendar(bars, payload)
    result = []
    for bar in sorted(bars, key=lambda r: str(r["as_of"])):
        day = str(bar["as_of"])[:10]
        available = [r for r in records if r["filed"] <= day]
        if not available:
            continue
        record = max(available, key=lambda r: (r["equity"]["end"], r["filed"], r["accession"]))
        eq, shares = record["equity"], record["shares"]
        basis_date = shares["end"] if shares else None
        # GAAP comparative/weighted shares can already be restated to a split
        # occurring AFTER period end. A same-accession split disclosure known
        # at this filing establishes the presentation basis; do not multiply twice.
        presented_splits = []
        if shares and shares["tag"].startswith("us-gaap:"):
            disclosed_ratios = fact_rows(payload, "us-gaap", SPLIT_TAG, "pure")
            for event in events:
                if shares["end"] < event["effective_date"] <= record["filed"] and any(
                    r["accn"] == record["accession"]
                    and r["filed"] == record["filed"]
                    and float(r["val"]) == event["ratio"]
                    for r in disclosed_ratios
                ):
                    presented_splits.append(event["effective_date"])
            basis_date = max([shares["end"], *presented_splits])
        applied = [e for e in events if basis_date and basis_date < e["effective_date"] <= day]
        factor = math.prod(e["ratio"] for e in applied)
        adjusted = float(shares["val"]) * factor if shares else None
        pbr = (
            float(bar["close"]) * adjusted / float(eq["val"])
            if adjusted is not None and float(eq["val"]) > 0
            else None
        )
        if pbr is not None and (not math.isfinite(pbr) or pbr <= 0):
            pbr = None
        result.append(
            {
                "symbol": symbol,
                "as_of": day,
                "disclosure_date": record["filed"],
                "pbr": pbr,
                "per": None,
                "accession": record["accession"],
                "cik": f"{int(payload['cik']):010d}",
                "equity": float(eq["val"]),
                "equity_tag": f"us-gaap:{eq['tag']}",
                "period_end": eq["end"],
                "reported_shares": float(shares["val"]) if shares else None,
                "shares_as_of": shares["end"] if shares else None,
                "shares_presentation_basis_date": basis_date,
                "shares_already_presented_splits": presented_splits,
                "shares_tag": shares["tag"] if shares else None,
                "shares_basis": shares["basis"] if shares else None,
                "split_factor": factor,
                "adjusted_shares": adjusted,
                "split_events": [e["effective_date"] for e in applied],
                "provider": "sec-edgar",
                "license_class": "official_public",
                "source_url": (
                    f"https://data.sec.gov/api/xbrl/companyfacts/CIK{int(payload['cik']):010d}.json"
                ),
                "price_provider": bar.get("provider", "alpaca"),
                "price_license_class": bar.get("license_class", "personal_only"),
                "derived_pbr_license_class": bar.get("license_class", "personal_only"),
                "price_basis": "persisted_close_with_same_day_share_unit_adjustment",
            }
        )
    return result, events


def join_pbr(
    histories: dict[str, list[dict[str, Any]]], directory: Path
) -> dict[str, list[dict[str, Any]]]:
    """Exact bar-date sidecar join; missing row stays missing, no cross-date fill."""
    joined = {}
    for symbol, bars in histories.items():
        path = directory / f"{symbol}.jsonl"
        financial = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                row = json.loads(line)
                day = row["as_of"]
                if row["symbol"] != symbol or day in financial or row["disclosure_date"] > day:
                    raise ValueError(f"Invalid or duplicate PIT financial row in {path}: {day}")
                financial[day] = row
        joined[symbol] = [
            {
                **bar,
                **{
                    field: financial.get(str(bar["as_of"])[:10], {}).get(field)
                    for field in ("pbr", "per", "disclosure_date")
                },
            }
            for bar in bars
        ]
    return joined
