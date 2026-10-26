"""Contract validation for the shadow-LLM answer (設計V2 §5-2).

The nine checks here are interface checks only: they never judge whether the
reasoning is good, they only reject answers the machine cannot read.  First-hit
wins (the reported reason list stops at the first failing check) and a
rejection is *not* a retry - it drops to the fallback of §5-3.
"""

from __future__ import annotations

import json
from typing import Any

ACTIONS = {"enter", "hold", "add", "reduce", "flatten", "skip"}
REQUIRED_KEYS = ("action", "targets", "confidence", "cited", "rationale")
GRID = (0.0, 0.25, 0.5, 0.75, 1.0)
GRID_EPS = 1e-9
MAX_HOLD_DAYS = 20


def parse(raw: str) -> tuple[dict | None, str | None]:
    """Check 1: the body must be a JSON object."""
    if not raw or not raw.strip():
        return None, "not_json: empty body"
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, f"not_json: {exc.msg} at {exc.pos}"
    if not isinstance(obj, dict):
        return None, "not_json: top level is not an object"
    return obj, None


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate(
    answer: dict | None,
    *,
    provided_ids: set[str],
    candidates: tuple[str, ...],
    degraded: list[str],
    rejection_reason: str | None = None,
) -> dict[str, Any]:
    """Run checks 2-8 (+ the degraded rule of §6-3). Returns a report dict."""
    checks: list[dict[str, Any]] = []
    reasons: list[str] = []

    def record(name: str, ok: bool, detail: str = "") -> bool:
        checks.append({"check": name, "ok": ok, "detail": detail})
        if not ok:
            reasons.append(detail or name)
        return ok

    if not record("1.parse", answer is not None,
                  "" if answer is not None else (rejection_reason or "not_json")):
        return {"ok": False, "reasons": reasons, "checks": checks, "answer": None}
    assert answer is not None

    missing = [k for k in REQUIRED_KEYS if k not in answer]
    if not record("2.required_keys", not missing, "missing:" + ",".join(missing) if missing else ""):
        return {"ok": False, "reasons": reasons, "checks": checks, "answer": answer}

    action = answer.get("action")
    if not record("3.action", action in ACTIONS, "" if action in ACTIONS else f"action_not_in_enum:{action!r}"):
        return {"ok": False, "reasons": reasons, "checks": checks, "answer": answer}

    targets = answer.get("targets")
    targets = targets if isinstance(targets, dict) else None
    targets_ok = targets is not None and set(targets) == set(candidates)
    detail = ""
    if not targets_ok:
        detail = f"targets_keys:{sorted(targets) if targets else type(answer.get('targets')).__name__}"
    elif not all(_is_number(v) and 0.0 <= float(v) <= 1.0 for v in targets.values()):
        targets_ok, detail = False, "targets_range: values must be numbers in [0,1]"
    elif sum(float(v) for v in targets.values()) > 1.0 + GRID_EPS:
        targets_ok, detail = False, f"targets_sum:{sum(float(v) for v in targets.values()):.3f}>1.0"
    if not record("4.targets", targets_ok, detail):
        return {"ok": False, "reasons": reasons, "checks": checks, "answer": answer}
    assert targets is not None

    off_grid = [k for k, v in targets.items() if all(abs(float(v) - g) > GRID_EPS for g in GRID)]
    if not record("5.grid", not off_grid, "grid_off:" + ",".join(off_grid) if off_grid else ""):
        return {"ok": False, "reasons": reasons, "checks": checks, "answer": answer}

    cited = answer.get("cited")
    cited_ok = isinstance(cited, list) and all(isinstance(c, str) for c in cited)
    unknown = [c for c in cited if isinstance(c, str) and c not in provided_ids] if cited_ok else []
    if not record("6.cited", cited_ok and not unknown,
                  ("cited_unknown:" + ",".join(unknown)) if unknown else ("cited_not_list" if not cited_ok else "")):
        return {"ok": False, "reasons": reasons, "checks": checks, "answer": answer}

    if action == "skip":
        reentry = answer.get("reentry_condition")
        if not record("7.skip_reentry", isinstance(reentry, str) and reentry.strip() != "",
                      "skip_without_reentry_condition"):
            return {"ok": False, "reasons": reasons, "checks": checks, "answer": answer}
    else:
        record("7.skip_reentry", True, "n/a")

    hold = answer.get("hold_plan", {"min_days": 1, "max_days": 1, "time_stop_to": 0.0})
    hold_ok = isinstance(hold, dict)
    hold_detail = ""
    if hold_ok:
        lo, hi = hold.get("min_days"), hold.get("max_days")
        hold_ok = _is_number(lo) and _is_number(hi) and 1 <= lo <= hi <= MAX_HOLD_DAYS
        if not hold_ok:
            hold_detail = f"hold_plan_range:{lo},{hi}"
        else:
            ts = hold.get("time_stop_to", 0.0)
            hold_ok = _is_number(ts) and 0.0 <= float(ts) <= max(float(v) for v in targets.values())
            if not hold_ok:
                hold_detail = f"time_stop_to_range:{ts}"
    if not record("8.hold_plan", hold_ok, hold_detail):
        return {"ok": False, "reasons": reasons, "checks": checks, "answer": answer}

    # §6-3: a degraded packet may not open new exposure
    if degraded and action in {"enter", "add"}:
        return {
            "ok": False,
            "reasons": [f"degraded_blocks_entry:{','.join(degraded[:3])}"],
            "checks": checks + [{"check": "8b.degraded", "ok": False, "detail": "enter/add on degraded packet"}],
            "answer": answer,
        }
    record("8b.degraded", True, "n/a")

    return {"ok": True, "reasons": [], "checks": checks, "answer": answer}
