"""Shadow-LLM (book D v2 prototype) — one tick.

    python3 -m ops.shadow_llm.run_once            # one live tick (free model)
    python3 -m ops.shadow_llm.run_once --offline  # no public API calls
    python3 -m ops.shadow_llm.run_once --dry-run  # do not append the journal

One tick: build the material packet -> one LLM call -> contract validation ->
M1-M7 enforcement -> append exactly one line to data/shadow-llm/journal.jsonl.

It places no orders, touches no broker code, does not write to
``data/demo-loop/`` and does not run the demo loop.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config, enforce, llm, materials, schema

SCHEMA_ID = "shadow-llm-v0.2"
JOURNAL_RELATIVE = Path("shadow-llm") / "journal.jsonl"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def run_once(offline: bool | None = None, append: bool = True) -> dict[str, Any]:
    cfg = config.load()
    if offline is None:
        offline = bool(cfg["offline"])

    packet = materials.build(offline=offline)
    packet_text = _canonical(packet)
    materials_sha = _sha256(packet_text)

    prev = materials.last_shadow_row() or {}
    prev_targets = (prev.get("effective_targets") or {})
    prev_stops = (prev.get("stops") or {})

    held = {
        asset: float(packet["materials"].get(f"book.weight.{asset}") or 0.0)
        for asset in materials.CANDIDATES
    }
    prices = {
        asset: float((packet["per_asset"].get(asset) or {}).get("close") or 0.0)
        for asset in materials.CANDIDATES
    }

    response = llm.call(cfg, llm.build_messages(packet))
    if response["ok"]:
        answer, parse_error = schema.parse(response["content"])
    else:
        answer, parse_error = None, f"no_answer: {response['error']}"

    report = schema.validate(
        answer,
        provided_ids=materials.provided_ids(packet),
        candidates=materials.CANDIDATES,
        degraded=packet["degraded"],
        rejection_reason=parse_error,
    )

    # §5-3 plan C: a failed or rejected answer keeps the previous executed
    # targets (never "mechanical = full size"), and the record says fallback.
    # On the very first tick there is no previous row, so the book's actual
    # weights are the honest "previous executed value".
    fallback = not report["ok"]
    if fallback:
        targets = {
            asset: float(prev_targets.get(asset, held[asset]) or 0.0)
            for asset in materials.CANDIDATES
        }
        stop_basis = ""
    else:
        targets = {asset: float(report["answer"]["targets"][asset]) for asset in materials.CANDIDATES}
        stop_basis = str(report["answer"].get("stop_basis") or "")

    halt_reasons = [
        f"stale_quotes.{a}={packet['materials'].get(f'tech.stale_quotes.{a}')}"
        for a in materials.CANDIDATES
        if isinstance(packet["materials"].get(f"tech.stale_quotes.{a}"), int)
        and packet["materials"][f"tech.stale_quotes.{a}"] >= 7
    ]
    for asset in materials.CANDIDATES:
        age = packet["materials"].get(f"tech.quote_age_min.{asset}")
        if isinstance(age, int) and age > 26 * 60:
            halt_reasons.append(f"quote_age_min.{asset}={age} > 1560")
    if packet["materials"].get("book.reconcile_gap_usd") is None:
        halt_reasons.append("reconcile_unavailable")

    ctx = {
        "candidates": materials.CANDIDATES,
        "targets": targets,
        "held": held,
        "prices": prices,
        "equity": float(packet["materials"].get("book.equity_usd") or 0.0),
        "loss_today_pct": packet["materials"].get("book.loss_today_pct"),
        "loss_week_pct": packet["materials"].get("book.loss_week_pct"),
        "reconcile_gap_usd": packet["materials"].get("book.reconcile_gap_usd"),
        "stop_basis": stop_basis,
        "prev_stop": {a: prev_stops.get(a) for a in materials.CANDIDATES},
        "series": {a: materials.series(a) for a in materials.CANDIDATES},
        "atr14": {a: packet["materials"].get(f"tech.atr14.{a}") for a in materials.CANDIDATES},
        "halt_reasons": halt_reasons,
        "deadline_exceeded": bool(
            not response["ok"]
            and str(response.get("error", "")).startswith(("TimeoutError", "URLError"))
        ),
        "contract_ok": report["ok"],
        "degraded": packet["degraded"],
    }
    verdict = enforce.apply(ctx)

    stops: dict[str, float | None] = {}
    for asset in materials.CANDIDATES:
        if verdict["targets"][asset] > held[asset]:
            price, _why = enforce.resolve_stop(stop_basis, asset, ctx)
            stops[asset] = price
        else:
            stops[asset] = prev_stops.get(asset)

    usage = response.get("usage") or {}
    cost = usage.get("cost")
    record: dict[str, Any] = {
        "event": "shadow_decision",
        "schema": SCHEMA_ID,
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tick_id": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "materials": {
            "sha256": materials_sha,
            "bytes": len(packet_text.encode("utf-8")),
            "token_estimate": materials.estimate_tokens(packet),
            "bar_as_of": {a: packet["per_asset"][a]["bar_as_of"] for a in materials.CANDIDATES},
            "unavailable_count": len(packet["unavailable"]),
            "degraded": packet["degraded"],
        },
        "llm": {
            "provider_base_url": cfg["base_url"],
            "model": cfg["model"],
            "free_lane": cfg["model"].endswith(":free"),
            "attempts": response["attempts"],
            "latency_ms": response["latency_ms"],
            "http_status": response.get("http_status"),
            "finish_reason": response.get("finish_reason"),
            "usage": {
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "total_tokens": usage.get("total_tokens"),
                "cost_usd": cost,
            },
            "cost_measured_zero": cost in (0, 0.0),
            "answer_sha256": _sha256(response["content"]) if response["content"] else None,
            "raw_output": response["content"],
            "error": response.get("error") or None,
        },
        "contract": {
            "ok": report["ok"],
            "reasons": report["reasons"],
            "checks": report["checks"],
        },
        "decision": (
            {
                "action": report["answer"].get("action"),
                "targets": report["answer"].get("targets"),
                "hold_plan": report["answer"].get("hold_plan"),
                "stop_basis": report["answer"].get("stop_basis"),
                "confidence": report["answer"].get("confidence"),
                "cited": report["answer"].get("cited"),
                "rationale": report["answer"].get("rationale"),
                "invalidators": report["answer"].get("invalidators"),
                "reentry_condition": report["answer"].get("reentry_condition"),
            }
            if report["ok"] else None
        ),
        "fallback": fallback,
        "degraded": packet["degraded"],
        "targets_before_enforcement": targets,
        "effective_targets": verdict["targets"],
        "held_before": held,
        "stops": stops,
        "enforcement": {
            "fired": verdict["fired"],
            "classes": verdict["classes"],
            "overrides": verdict["overrides"],
            "halt": verdict["halt"],
            "halt_reason": verdict["halt_reason"],
        },
        "orders_placed": 0,
    }

    if append:
        path = config.journal_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        record["_journal_path"] = str(path)

    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="shadow-LLM one tick (no orders)")
    parser.add_argument("--offline", action="store_true", help="skip free public API calls")
    parser.add_argument("--dry-run", action="store_true", help="do not append the journal")
    args = parser.parse_args(argv)

    try:
        record = run_once(offline=True if args.offline else None, append=not args.dry_run)
    except config.ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(record, ensure_ascii=False, indent=2))
    return 0 if record["contract"]["ok"] else 3


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
