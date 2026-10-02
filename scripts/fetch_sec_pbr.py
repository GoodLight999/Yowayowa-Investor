#!/usr/bin/env python3
"""Fetch once/cache forever SEC snapshots, then deterministically generate PIT PBR.

Run with PYTHONPATH=src .venv/bin/python scripts/fetch_sec_pbr.py.
Use --offline to prove replay uses no network. Existing OHLCV is read-only.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from yowayowa.config import Settings
from yowayowa.providers.sec import SecClient
from yowayowa.sec_pbr import build_pbr, digest, filing_inputs
from yowayowa.stock_acquisition import default_store

ROOT = Path(__file__).resolve().parents[1]
UNIVERSE = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "JPM", "XOM", "SPY"]
# Explicit same-issuer lineage, not a record-count heuristic or fuzzy name match.
# Exxon Mobil's 2026 holding-company reorganization moved current companyfacts
# to CIK 2115436; historical disclosures remain under 34088. The 2026-08-03
# 10-Q accession 0000034088-26-000093 is in the new CIK snapshot only. Accession
# prefix is NOT necessarily the endpoint CIK, so preserve both unchanged.
ISSUER_CIK_GROUPS = {"XOM": ("0002115436", "0000034088")}


def issuer_candidates(symbol: str, mapped_cik: str | None) -> list[str]:
    group = ISSUER_CIK_GROUPS.get(symbol)
    if group:
        if mapped_cik is not None and mapped_cik not in group:
            raise ValueError(f"Unreviewed issuer change for {symbol}: {mapped_cik}")
        return list(group)
    return [mapped_cik] if mapped_cik else []


def save(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def generate(data_dir: Path, offline: bool = False) -> dict[str, Any]:
    raw = data_dir / "fundamentals/sec-raw"
    output = data_dir / "fundamentals/pbr"
    output.mkdir(parents=True, exist_ok=True)
    client: SecClient | None = None

    def network_client() -> SecClient:
        nonlocal client
        if offline:
            raise RuntimeError("Missing snapshot in offline mode; run a live acquisition first")
        if client is None:
            client = SecClient(Settings())
        return client

    mapping_path = raw / "ticker-map.json"
    if not mapping_path.exists():
        mapping = {
            symbol: {"cik": instrument.cik, "name": instrument.name}
            for symbol, instrument in network_client().ticker_map().items()
        }
        save(mapping_path, {"retrieved_at": datetime.now(UTC).isoformat(), "mapping": mapping})
    mapping = json.loads(mapping_path.read_text())["mapping"]

    def snapshot(cik: str) -> dict[str, Any]:
        path = raw / f"CIK{cik}.json"
        if not path.exists():
            try:
                payload = network_client().raw_company_facts(cik)
                envelope = {"payload": payload, "status": 200}
            except httpx.HTTPStatusError as exc:
                # Cache structural absence only; never bypass or hide access failures.
                if exc.response.status_code != 404:
                    raise
                envelope = {"payload": None, "status": 404}
            save(path, {**envelope, "retrieved_at": datetime.now(UTC).isoformat()})
        return dict(json.loads(path.read_text()))

    store = default_store(data_dir)
    summary: dict[str, Any] = {
        "schema_version": 2,
        "provider": "sec-edgar",
        "license_class": "official_public",
        "ticker_map": {
            "raw_snapshot": "../sec-raw/ticker-map.json",
            "raw_sha256": digest(mapping_path),
        },
        "symbols": {},
    }
    calendars = {}
    try:
        for symbol in UNIVERSE:
            path = output / f"{symbol}.jsonl"
            if symbol == "SPY":
                path.write_text("")
                summary["symbols"][symbol] = {
                    "rows": 0,
                    "valid_pbr_rows": 0,
                    "reason": "ETF: structural PBR absence; companyfacts 404 confirmed by CTO",
                }
                calendars[symbol] = []
                continue
            cik = mapping.get(symbol, {}).get("cik")
            candidates = issuer_candidates(symbol, cik)
            if not candidates:
                path.write_text("")
                summary["symbols"][symbol] = {
                    "rows": 0,
                    "valid_pbr_rows": 0,
                    "reason": "ticker unmapped",
                }
                calendars[symbol] = []
                continue
            facts = []
            counts = {}
            snapshots = []
            for candidate in candidates:
                cached = snapshot(candidate)
                payload = cached["payload"]
                snapshots.append(
                    {
                        "cik": candidate,
                        "raw_snapshot": f"../sec-raw/CIK{candidate}.json",
                        "raw_sha256": digest(raw / f"CIK{candidate}.json"),
                    }
                )
                if payload and f"{int(payload['cik']):010d}" != candidate:
                    raise ValueError(f"Snapshot CIK does not match endpoint: {candidate}")
                count = len(filing_inputs(payload)) if payload else 0
                counts[candidate] = count
                if payload:
                    facts.append(payload)
            if not facts:
                path.write_text("")
                summary["symbols"][symbol] = {
                    "rows": 0,
                    "valid_pbr_rows": 0,
                    "reason": "SEC 404",
                    "raw_snapshots": snapshots,
                }
                calendars[symbol] = []
                continue
            bars = store.read(symbol, provider="alpaca", limit=10_000)
            rows, events = build_pbr(symbol, bars, facts)
            path.write_text(
                "".join(json.dumps(r, sort_keys=True, allow_nan=False) + "\n" for r in rows)
            )
            summary["symbols"][symbol] = {
                "mapped_cik": cik,
                "merged_ciks": [f"{int(p['cik']):010d}" for p in facts],
                "used_ciks": sorted({r["cik"] for r in rows}),
                "cik_filing_counts": counts,
                "rows": len(rows),
                "valid_pbr_rows": sum(r["pbr"] is not None for r in rows),
                "first": rows[0]["as_of"] if rows else None,
                "last": rows[-1]["as_of"] if rows else None,
                "share_tags": sorted({r["shares_tag"] for r in rows if r["shares_tag"]}),
                "share_bases": sorted({r["shares_basis"] for r in rows if r["shares_basis"]}),
                "raw_snapshots": snapshots,
                "output_sha256": digest(path),
            }
            calendars[symbol] = events
            print(symbol, json.dumps(summary["symbols"][symbol]), flush=True)
    finally:
        if client is not None:
            client.client.close()
    summary["method"] = {
        "financial_selection": (
            "latest filed across reviewed issuer CIKs; whole same-CIK/accession equity/shares"
        ),
        "disclosure": "SEC filed, never period_end; engine keeps strict < decision date",
        "split_detection": (
            "positive finite open/close; nearby SEC ratio confirmation within 15%; "
            "reverse/fractional supported"
        ),
        "split_sec_tags": (
            "retrospective eligibility and exact ratio; observed prices date event; "
            "not an ex-ante action feed"
        ),
        "per": "not supplied; no same-accession trailing earnings pipeline implemented",
        "price_storage": "unchanged; share unit series alone adjusted",
        "meta_approximation": "same-accession basic period-average shares, not instant shares",
    }
    save(output / "split-calendar.json", calendars)
    for symbol, item in summary["symbols"].items():
        item["output_sha256"] = digest(output / f"{symbol}.jsonl")
    summary["split_calendar_sha256"] = digest(output / "split-calendar.json")
    save(output / "manifest.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    generate(args.data_dir, args.offline)


if __name__ == "__main__":
    main()
