"""One prompt, one call, at most one retry (設計V2 §5-3 / タスク制約).

The prompt hands the LLM the seven decision points (J1, J2, J4, J5, J6, J7, J8)
and the material packet; everything mechanical (allocation J9, execution J10,
safety stops J11) stays on the machine side and is not offered as a choice.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

DECISION_POINTS = """
You decide exactly these 7 points (nothing else):
J1 entry or not today (do not assume "no entry" is the safe choice: 59 hours with
   zero fills cost more in the record than the worst explained loss)
J2 which of the fixed candidates to hold (BTC, ETH only; the candidate set is fixed)
J4 gross exposure today, total across candidates: 0 / 0.25 / 0.5 / 0.75 / 1.0
J5 holding time: hold_plan.min_days / max_days (1..20)
J6 partial take profit: use a lower target first and step down later (0.25 grid)
J7 skip (abstain) with a written reentry_condition - skipping is allowed, but an
   unexplained skip is recorded and counted
J8 exit / risk-off: cut or flatten when the materials say the premise broke

Not yours: per-asset allocation ratios (J9), order execution (J10), safety stops
(J11). Stop/take profit prices are computed by the machine from stop_basis:
choose one of wick_low | sma20 | atr15 | fib. Never write a price.
""".strip()

CONTRACT = """
Answer with ONE JSON object and no prose:
{
  "action": "enter|hold|add|reduce|flatten|skip",
  "targets": {"BTC": 0.0, "ETH": 0.0},
  "hold_plan": {"min_days": 1, "max_days": 6, "time_stop_to": 0.0},
  "stop_basis": "wick_low|sma20|atr15|fib",
  "confidence": 0.0,
  "cited": ["<material id copied exactly from the packet>"],
  "rationale": "<= 320 chars",
  "invalidators": ["what would prove this decision wrong"],
  "reentry_condition": "required when action=skip"
}
Rules that are checked mechanically:
- targets must contain every candidate key, each on the 0.25 grid, sum <= 1.0
- cited must be material ids present in the packet (unknown id = rejected)
- skip requires a non-empty reentry_condition
- hold_plan: 1 <= min_days <= max_days <= 20, time_stop_to <= max target
- if the packet says degraded, enter/add is rejected (reduce/flatten/skip allowed)
""".strip()


def build_messages(packet: dict[str, Any]) -> list[dict[str, str]]:
    payload = json.dumps(packet, ensure_ascii=False, separators=(",", ":"))
    system = (
        "You are the discretionary overlay (book D v2) of a paper-trading loop. "
        "You see only the packet below; you do not see future bars. "
        "Be decisive: base every target on a cited material id. "
        "Output ONLY one JSON object that satisfies the contract below. "
        "Do not write analysis, reasoning, markdown or prose before or after the JSON."
    )
    user = (
        DECISION_POINTS
        + "\n\nMATERIAL PACKET (free public data + local paper journal):\n"
        + payload
        + "\n\n"
        + CONTRACT
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def call(cfg: dict[str, Any], messages: list[dict[str, str]]) -> dict[str, Any]:
    """Single call, one retry on transport/empty-body failures only."""
    if not cfg.get("api_key"):
        return {
            "ok": False, "content": "", "attempts": 0, "latency_ms": 0,
            "usage": {}, "error": f"no api key for {cfg.get('key_env')}",
            "http_status": None,
        }

    body_obj: dict[str, Any] = {
        "model": cfg["model"],
        "messages": messages,
        "max_tokens": cfg["max_tokens"],
        "temperature": cfg["temperature"],
    }
    if cfg.get("json_mode"):
        body_obj["response_format"] = {"type": "json_object"}
    body = json.dumps(body_obj).encode("utf-8")

    started = time.monotonic()
    last_error = ""
    status: int | None = None
    finish_seen: str | None = None
    usage_seen: dict[str, Any] = {}
    head_seen = ""
    for attempt in range(1, cfg["max_attempts"] + 1):
        req = urllib.request.Request(cfg["endpoint"], data=body, headers={
            "Authorization": "Bearer " + str(cfg["api_key"]),
            "Content-Type": "application/json",
            "User-Agent": "shadow-llm/0.1",
        })
        try:
            with urllib.request.urlopen(req, timeout=cfg["timeout_seconds"]) as resp:
                status = resp.status
                raw = resp.read()
            data = json.loads(raw)
            choice = (data.get("choices") or [{}])[0]
            content = (choice.get("message") or {}).get("content") or ""
            finish_reason = choice.get("finish_reason")
            usage = data.get("usage") or {}
            # §5-3: a truncated answer counts as "no answer", not as a decision
            if content.strip() and finish_reason != "length":
                return {
                    "ok": True,
                    "content": content,
                    "attempts": attempt,
                    "latency_ms": int((time.monotonic() - started) * 1000),
                    "usage": usage,
                    "error": "",
                    "http_status": status,
                    "finish_reason": finish_reason,
                }
            last_error = "finish_reason=length" if finish_reason == "length" else "empty_content"
            # evidence for the rejected attempt (usage/content head): without
            # this the journal only says "length" and the truncation itself is
            # unmeasurable (11-10..12 incident)
            finish_seen = finish_reason
            usage_seen = usage if isinstance(usage, dict) else {}
            head_seen = content[:200]
        except urllib.error.HTTPError as exc:
            status = exc.code
            last_error = f"HTTP {exc.code}: {exc.read()[:200].decode('utf-8', 'replace')}"
            if exc.code not in (429, 500, 502, 503, 504):
                break
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        except json.JSONDecodeError as exc:
            last_error = f"bad_json_response: {exc}"
            break
        if attempt < cfg["max_attempts"]:
            time.sleep(2.0)

    return {
        "ok": False,
        "content": "",
        "attempts": cfg["max_attempts"],
        "latency_ms": int((time.monotonic() - started) * 1000),
        "usage": usage_seen,
        "error": last_error,
        "http_status": status,
        "finish_reason": finish_seen,
        "fail_content_head": head_seen,
    }
