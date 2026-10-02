"""Auditable SEC filing-to-bar PBR supply, separate from immutable price storage.

Companyfacts must remain unabridged: deduplication by period alone would replace
original disclosures with future restatements. All financial inputs in a row
come from ONE accession. Split events change share units, not the engine.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date
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
        records.append(
            {
                "equity": eq,
                "shares": shares,
                "accession": accn,
                "filed": eq["filed"],
                "cik": f"{int(payload['cik']):010d}",
            }
        )
    return sorted(records, key=lambda r: (r["filed"], r["equity"]["end"], r["accession"]))


def positive_finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def split_calendar(
    bars: list[dict[str, Any]], payload: dict[str, Any] | list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Reconstruct corroborated share-unit changes, not a live action feed.

    Require a positive SEC ratio (including reverse/fractional ratios), a nearby
    fact period, a filing within 180 days after the observed event, and both
    open/close price agreement within 15%. Old unrelated tags cannot authorize
    new discontinuities. SEC dates are often announcement/period dates, NOT
    effective dates; the price observation dates the event. Later filings gate
    eligibility and provide the exact ratio: this retrospective reconstruction
    must not be represented as an ex-ante corporate-action detector.
    """
    payloads = payload if isinstance(payload, list) else [payload]
    tags = [
        {**r, "cik": f"{int(p['cik']):010d}"}
        for p in payloads
        for r in fact_rows(p, "us-gaap", SPLIT_TAG, "pure")
        if positive_finite(r["val"]) is not None and float(r["val"]) != 1
    ]
    events: list[dict[str, Any]] = []
    ordered = sorted(bars, key=lambda r: str(r["as_of"]))
    for previous, current in pairwise(ordered):
        before = positive_finite(previous.get("close"))
        close = positive_finite(current.get("close"))
        opening_price = positive_finite(current.get("open"))
        if before is None or close is None or opening_price is None:
            continue
        observed, opening = before / close, before / opening_price
        if not math.isfinite(observed) or not math.isfinite(opening):
            continue
        day = str(current["as_of"])[:10]
        event_date = date.fromisoformat(day)
        evidence = []
        for row in tags:
            ratio = float(row["val"])
            filed_gap = (date.fromisoformat(row["filed"]) - event_date).days
            period_gap = abs((date.fromisoformat(row["end"]) - event_date).days)
            if (
                0 <= filed_gap <= 180
                and period_gap <= 180
                and abs(opening / ratio - 1) <= 0.15
                and abs(observed / ratio - 1) <= 0.15
                # Small price changes are not corporate-action evidence.
                and abs(opening - 1) > 0.15
            ):
                evidence.append(row)
        if not evidence:
            continue
        first = min(
            evidence,
            key=lambda r: (abs(opening / float(r["val"]) - 1), r["filed"], r["accn"], r["cik"]),
        )
        ratio = float(first["val"])
        # A repeated historical tag must not confirm multiple price shocks.
        if any(
            e["ratio"] == ratio
            and abs((event_date - date.fromisoformat(e["effective_date"])).days) <= 180
            for e in events
        ):
            continue
        events.append(
            {
                "effective_date": day,
                "ratio": ratio,
                "observed_close_ratio": observed,
                "previous_close": before,
                "close": close,
                "residual_return": close * ratio / before - 1,
                "method": "price_discontinuity_with_nearby_sec_ratio_confirmation",
                "sec_first_filed": first["filed"],
                "sec_accession": first["accn"],
                "sec_cik": first["cik"],
                "sec_tag": f"us-gaap:{SPLIT_TAG}",
                "sec_is_retrospective_validation_only": False,
                "sec_is_retrospective_eligibility_gate": True,
            }
        )
    return events


def build_pbr(
    symbol: str, bars: list[dict[str, Any]], payload: dict[str, Any] | list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    payloads = payload if isinstance(payload, list) else [payload]
    # Keep whole filings and their source CIK; never mix equity/shares across CIKs.
    records = [r for p in payloads for r in filing_inputs(p)]
    events = split_calendar(bars, payloads)
    disclosed_ratios = [
        {**r, "cik": f"{int(p['cik']):010d}"}
        for p in payloads
        for r in fact_rows(p, "us-gaap", SPLIT_TAG, "pure")
    ]
    result = []
    for bar in sorted(bars, key=lambda r: str(r["as_of"])):
        day = str(bar["as_of"])[:10]
        available = [r for r in records if r["filed"] <= day]
        if not available:
            continue
        record = max(available, key=lambda r: (r["filed"], r["equity"]["end"], r["accession"]))
        eq, shares = record["equity"], record["shares"]
        basis_date = shares["end"] if shares else None
        # GAAP comparative/weighted shares can already be restated to a split
        # occurring AFTER period end. A same-accession split disclosure known
        # at this filing establishes the presentation basis; do not multiply twice.
        presented_splits = []
        if shares and shares["tag"].startswith("us-gaap:"):
            for event in events:
                if shares["end"] < event["effective_date"] <= record["filed"] and any(
                    r["accn"] == record["accession"]
                    and r["cik"] == record["cik"]
                    and r["filed"] == record["filed"]
                    and float(r["val"]) == event["ratio"]
                    for r in disclosed_ratios
                ):
                    presented_splits.append(event["effective_date"])
            basis_date = max([shares["end"], *presented_splits])
        applied = [e for e in events if basis_date and basis_date < e["effective_date"] <= day]
        factor = math.prod(e["ratio"] for e in applied)
        adjusted = float(shares["val"]) * factor if shares else None
        close = positive_finite(bar.get("close"))
        pbr = (
            close * adjusted / float(eq["val"])
            if close is not None and adjusted is not None and float(eq["val"]) > 0
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
                "cik": record["cik"],
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
                    f"https://data.sec.gov/api/xbrl/companyfacts/CIK{record['cik']}.json"
                ),
                "price_provider": bar.get("provider", "alpaca"),
                "price_license_class": bar.get("license_class", "personal_only"),
                "derived_pbr_license_class": bar.get("license_class", "personal_only"),
                "price_basis": "persisted_close_with_same_day_share_unit_adjustment",
            }
        )
    return result, events


def verify_supply(directory: Path) -> dict[str, Any]:
    """Fail closed on missing/mutated artifacts and nonportable manifest paths.

    SHA256 protects integrity relative to the manifest, not authenticity of a
    manifest supplied by an adversary. Raw paths are relative to the PBR store;
    sibling sec-raw is allowed, escape outside fundamentals is not.
    """
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError(f"Missing supply manifest: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 2 or not isinstance(manifest.get("symbols"), dict):
        raise ValueError("Unsupported supply manifest; regenerate with the current generator")

    def check(path: Path, expected: Any) -> None:
        if not isinstance(expected, str) or len(expected) != 64 or not path.is_file():
            raise ValueError(f"Missing artifact/hash: {path}")
        if digest(path) != expected:
            raise ValueError(f"SHA256 mismatch: {path}")

    check(directory / "split-calendar.json", manifest.get("split_calendar_sha256"))
    root = directory.parent.resolve()
    for symbol, item in manifest["symbols"].items():
        if not isinstance(symbol, str) or not symbol.isascii() or not symbol.isalnum():
            raise ValueError(f"Invalid supply symbol: {symbol}")
        check(directory / f"{symbol}.jsonl", item.get("output_sha256"))
        for raw in item.get("raw_snapshots", []):
            relative = Path(raw["raw_snapshot"])
            path = (directory / relative).resolve()
            if relative.is_absolute() or not path.is_relative_to(root):
                raise ValueError(f"Nonportable or escaping snapshot path: {relative}")
            check(path, raw.get("raw_sha256"))
    mapping = manifest.get("ticker_map", {})
    relative = Path(mapping.get("raw_snapshot", ""))
    path = (directory / relative).resolve()
    if relative.is_absolute() or not path.is_relative_to(root):
        raise ValueError(f"Nonportable ticker map: {relative}")
    check(path, mapping.get("raw_sha256"))
    return dict(manifest)


def join_pbr(
    histories: dict[str, list[dict[str, Any]]], directory: Path
) -> dict[str, list[dict[str, Any]]]:
    """Verified exact-date sidecar join; missing dates stay missing, no fill."""
    manifest = verify_supply(directory)
    joined = {}
    for symbol, bars in histories.items():
        path = directory / f"{symbol}.jsonl"
        financial = {}
        if symbol not in manifest["symbols"]:
            raise ValueError(f"Symbol not declared in supply manifest: {symbol}")
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != manifest["symbols"][symbol]["output_sha256"]:
            raise ValueError(f"SHA256 mismatch: {path}")
        if content:
            for line in content.decode("utf-8").splitlines():
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
