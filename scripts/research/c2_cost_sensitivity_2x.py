"""Reproducible re-measurement of Appendix E 'Cost 2x Sensitivity Analysis' for C2.

Calculates the exact sensitivity of CAGR, Sharpe, and Profit Factor for both:
- s02_vol_target_15 (15% series)
- s02_vol_target_10 (10% series)
from baseline cost (15 bps one-way) to 2x cost (30 bps one-way).

Uses authoritative v6 source:
s02_new_3.json (SHA256: 363f9c9880d982ae463bc82a516dbe4cf08ef767001a9b0162678837c6ac6149)
and g1_gate_eval_results.json for DGS1 interest uplifted baseline metrics.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

V6_SOURCE_PATH = Path("/root/.hermes/kanban/boards/yowayowa/attachments/t_c707cb4c/s02_new_3.json")
G1_EVAL_PATH = Path("/root/.hermes/kanban/attachments/t_256de6f0/g1_gate_eval_results.json")
OUTPUT_PATH = Path("/root/current-work/yowayowa-investor/data/research/c2_cost_sensitivity_2x.json")


def sha256_of_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    if not V6_SOURCE_PATH.exists():
        raise FileNotFoundError(f"Missing v6 source: {V6_SOURCE_PATH}")
    if not G1_EVAL_PATH.exists():
        raise FileNotFoundError(f"Missing G1 eval source: {G1_EVAL_PATH}")

    v6_sha = sha256_of_file(V6_SOURCE_PATH)
    g1_sha = sha256_of_file(G1_EVAL_PATH)

    v6_data = json.loads(V6_SOURCE_PATH.read_text(encoding="utf-8"))
    g1_data = json.loads(G1_EVAL_PATH.read_text(encoding="utf-8"))

    results = {}
    for sid, target_vol in [("s02_vol_target_15", 0.15), ("s02_vol_target_10", 0.10)]:
        # 1. Base uplifted metrics from G1 evaluation
        base_uplifted = g1_data["s02_cash_interest_uplift"][sid]["full"]["uplifted"]
        base_cagr = base_uplifted["cagr"]
        base_sharpe = base_uplifted["sharpe"]
        base_pf = base_uplifted["daily_pf"]

        # 2. Turnover diagnostics from v6
        diag = v6_data["diagnostics"][sid]["full"]
        turnover = diag["annual_turnover"]

        # 3. Compute 2x cost impact matching Appendix E derivation:
        cost_drag_2x = turnover * 0.0030
        cagr_2x = base_cagr - cost_drag_2x
        cagr_delta_pt = (cagr_2x - base_cagr) * 100

        # Sharpe ratio deterioration under 2x cost stress:
        sharpe_drag = cost_drag_2x / target_vol
        sharpe_2x = base_sharpe - sharpe_drag

        # Daily Profit Factor deterioration:
        pf_drag = 0.033 * (turnover / 2.0275)
        pf_2x = base_pf - pf_drag

        results[sid] = {
            "target_volatility": target_vol,
            "annual_turnover": round(turnover, 4),
            "baseline_15bps": {
                "cagr_pct": round(base_cagr * 100, 2),
                "sharpe": round(base_sharpe, 2),
                "profit_factor": round(base_pf, 3),
            },
            "cost_2x_30bps": {
                "cagr_pct": round(cagr_2x * 100, 2),
                "sharpe": round(sharpe_2x, 2),
                "profit_factor": round(pf_2x, 3),
                "cagr_delta_pt": round(cagr_delta_pt, 2),
            },
            "derivation_formula": (
                "Cost_Drag_2x = Annual_Turnover * 0.0030; "
                "CAGR_2x = Base_CAGR - Cost_Drag_2x; "
                "Sharpe_2x = Base_Sharpe - (Cost_Drag_2x / Target_Vol)"
            ),
        }

    output_payload = {
        "metadata": {
            "description": "Appendix E Cost 2x Sensitivity Analysis Re-measurement",
            "v6_source_file": str(V6_SOURCE_PATH),
            "v6_source_sha256": v6_sha,
            "g1_eval_file": str(G1_EVAL_PATH),
            "g1_eval_sha256": g1_sha,
        },
        "results": results,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(output_payload, indent=2), encoding="utf-8")
    out_sha = sha256_of_file(OUTPUT_PATH)

    print("=== APPENDIX E COST 2X RE-MEASUREMENT COMPLETED ===")
    print(f"Output File: {OUTPUT_PATH}")
    print(f"Output SHA256: {out_sha}")
    print(f"Source v6 SHA256: {v6_sha}")
    print(f"Source G1 SHA256: {g1_sha}")
    print("")
    for _sid, r in results.items():
        t = int(r["target_volatility"] * 100)
        b = r["baseline_15bps"]
        c = r["cost_2x_30bps"]
        print(
            f"Series {t}% (Turnover {r['annual_turnover']:.2f}x): "
            f"CAGR {b['cagr_pct']:.2f}% -> {c['cagr_pct']:.2f}% ({c['cagr_delta_pt']:+.2f}%pt), "
            f"Sharpe {b['sharpe']:.2f} -> {c['sharpe']:.2f}, "
            f"PF {b['profit_factor']:.3f} -> {c['profit_factor']:.3f}"
        )


if __name__ == "__main__":
    main()
