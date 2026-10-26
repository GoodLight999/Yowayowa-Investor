"""Prove every accident class M1-M7 overrides the LLM value on synthetic input.

    python3 -m ops.shadow_llm.selftest_enforcement

Each case builds a *clean* context (which must NOT fire anything - that is the
control) and then changes exactly one field so that one class has to fire.  The
test fails loudly if a class stays silent or if the value does not change.
"""

from __future__ import annotations

import copy
import json
import sys

from . import enforce

BASE_CTX = {
    "candidates": ("BTC", "ETH"),
    "targets": {"BTC": 0.5, "ETH": 0.0},
    "held": {"BTC": 0.0, "ETH": 0.0},
    "prices": {"BTC": 80000.0, "ETH": 2400.0},
    "equity": 1.0,
    "loss_today_pct": 0.0,
    "loss_week_pct": 0.0,
    "reconcile_gap_usd": 0.0,
    "stop_basis": "wick_low",
    "prev_stop": {"BTC": None, "ETH": None},
    "series": {
        "BTC": {
            "close": [79000.0 + i * 10 for i in range(25)],
            "high": [79500.0 + i * 10 for i in range(25)],
            "low": [78000.0 + i * 5 for i in range(25)],
        },
        "ETH": {
            "close": [2300.0 + i for i in range(25)],
            "high": [2350.0 + i for i in range(25)],
            "low": [2250.0 + i for i in range(25)],
        },
    },
    "atr14": {"BTC": 900.0, "ETH": 40.0},
    "halt_reasons": [],
    "deadline_exceeded": False,
    "contract_ok": True,
    "degraded": [],
}


def _case(name: str, mutate) -> dict:
    ctx = copy.deepcopy(BASE_CTX)
    mutate(ctx)
    return ctx


CASES = [
    ("M1  stop basis cannot be resolved (fail-closed)",
     _case("M1", lambda c: c.update(stop_basis="guess_the_dip"))),
    ("M1b stop moved adversely (new stop worse than previous)",
     _case("M1b", lambda c: c["prev_stop"].update({"BTC": 77000.0}))),
    ("M2  daily loss ceiling (loss_today -6%)",
     _case("M2", lambda c: c.update(loss_today_pct=-0.06))),
    ("M2b weekly loss ceiling (loss_week -12%)",
     _case("M2", lambda c: c.update(loss_week_pct=-0.12))),
    ("M3  position risk > 5% of equity",
     _case("M3", lambda c: c["series"]["BTC"].update(
         {"low": [30000.0] * 25, "close": [80000.0] * 25, "high": [80100.0] * 25}))),
    ("M4  leverage ceiling (sum 1.5 > 1.0)",
     _case("M4", lambda c: c["targets"].update({"BTC": 0.75, "ETH": 0.75}))),
    ("M5  data stale / integrity HALT",
     _case("M5", lambda c: c.update(halt_reasons=["stale_quotes.BTC=9 samples unchanged"]))),
    ("M6  contract rejected -> hold previous (fallback C)",
     _case("M6", lambda c: (c.update(contract_ok=False), c["held"].update({"BTC": 0.0})))),
    ("M6b deadline exceeded -> hold previous",
     _case("M6b", lambda c: (c.update(deadline_exceeded=True), c["held"].update({"BTC": 0.25})))),
    ("M7  equity not explainable (gap 0.50 USD on 1.00 equity)",
     _case("M7", lambda c: c.update(reconcile_gap_usd=0.5))),
]

EXPECTED = {
    "M1  stop basis cannot be resolved (fail-closed)": "M1",
    "M1b stop moved adversely (new stop worse than previous)": "M1b",
    "M2  daily loss ceiling (loss_today -6%)": "M2",
    "M2b weekly loss ceiling (loss_week -12%)": "M2",
    "M3  position risk > 5% of equity": "M3",
    "M4  leverage ceiling (sum 1.5 > 1.0)": "M4",
    "M5  data stale / integrity HALT": "M5",
    "M6  contract rejected -> hold previous (fallback C)": "M6",
    "M6b deadline exceeded -> hold previous": "M6",
    "M7  equity not explainable (gap 0.50 USD on 1.00 equity)": "M7",
}


def main() -> int:
    failures: list[str] = []

    control = enforce.apply(copy.deepcopy(BASE_CTX))
    control_ok = not control["fired"] and control["targets"] == {"BTC": 0.5, "ETH": 0.0}
    print(json.dumps({
        "case": "control (clean context)",
        "expected": "no override",
        "fired": control["fired"],
        "classes": control["classes"],
        "targets": control["targets"],
        "result": "PASS" if control_ok else "FAIL",
    }, ensure_ascii=False))
    if not control_ok:
        failures.append("control fired although the context is clean")

    for name, ctx in CASES:
        verdict = enforce.apply(ctx)
        expected = EXPECTED[name]
        ok = expected in verdict["classes"] and verdict["fired"] and any(
            o["before"] != o["after"] for o in verdict["overrides"]
        )
        print(json.dumps({
            "case": name,
            "expected": expected,
            "fired": verdict["fired"],
            "classes": verdict["classes"],
            "targets_before": {a: ctx["targets"][a] for a in ctx["candidates"]},
            "targets_after": verdict["targets"],
            "overrides": verdict["overrides"],
            "result": "PASS" if ok else "FAIL",
        }, ensure_ascii=False))
        if not ok:
            failures.append(f"{name}: expected {expected}, got {verdict['classes']}")

    print(json.dumps({"cases": len(CASES), "failures": failures}, ensure_ascii=False))
    return 0 if not failures else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
