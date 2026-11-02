"""Analysis and measurement record for Appendix E 'Cost 2x Sensitivity Analysis' for C2.

Following governance instructions and CTO review feedback:
- The AS-TR-03 dividend/interest uplifted series under 2x cost (30 bps) is recorded as:
  '未計測 (UNMEASURED)': The authoritative v6 source (s02_new_3.json) contains only the 15 bps
  baseline run. The AS-TR-03 ledger accounting adapter was not run at 30 bps in t_c707cb4c.
  No algebraic formula extrapolation is used to guess numbers.
- In addition, an actual empirical backtest run using the core engine (yowayowa.services.backtest)
  at 15 bps vs 30 bps (commission 20 bps + slippage 10 bps) is computed and recorded directly.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

from yowayowa.backtest_models import BacktestRunRequest, BacktestStrategyDefinition
from yowayowa.services.backtest import run_backtest
from yowayowa.stock_acquisition import default_store

V6_SOURCE_PATH = Path("/root/.hermes/kanban/boards/yowayowa/attachments/t_c707cb4c/s02_new_3.json")
OUTPUT_PATH = Path("/root/current-work/yowayowa-investor/data/research/c2_cost_sensitivity_2x.json")


def sha256_of_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    v6_sha = sha256_of_file(V6_SOURCE_PATH) if V6_SOURCE_PATH.exists() else "MISSING"

    # 1. Run actual empirical backtests at 15 bps vs 30 bps
    store = default_store(Path("/root/current-work/yowayowa-investor/data"))
    histories = {
        sym: store.read(sym, provider="alpaca", limit=10_000)
        for sym in ["SPY", "IWM", "JNJ", "PG"]
    }

    empirical_results = {}
    for vol, sid in [(0.10, "s02_vol_target_10"), (0.15, "s02_vol_target_15")]:
        strat = BacktestStrategyDefinition(
            id=sid,
            name=f"Volatility target {int(vol * 100)}% weekly",
            description=f"Volatility target {int(vol * 100)}% weekly",
            signal="vol_target_defensive",
            universe=["SPY", "IWM", "JNJ", "PG"],
            rebalance="weekly",
            max_positions=4,
            weighting="equal",
            lookback=21,
            target_volatility=vol,
        )
        emp_series = {}
        for cost_label, comm, slip in [("baseline_15bps", 10.0, 5.0), ("cost_2x_30bps", 20.0, 10.0)]:
            req = BacktestRunRequest(
                strategy_id=sid,
                start=date(2021, 1, 4),
                end=date(2026, 9, 28),
                commission_bps=comm,
                slippage_bps=slip,
                bootstrap_samples=0,
            )
            res = run_backtest(req, strat, histories, _include_oos=False)
            rets = [p.daily_return for p in res.equity_curve]
            gains = sum(r for r in rets if r > 0)
            losses = sum(abs(r) for r in rets if r < 0)
            daily_pf = gains / losses if losses > 0 else 0.0
            cagr = res.metrics.cagr.value if res.metrics.cagr else 0.0
            sharpe = res.metrics.sharpe_ratio.value if res.metrics.sharpe_ratio else 0.0
            maxdd = res.metrics.max_drawdown.value if res.metrics.max_drawdown else 0.0
            emp_series[cost_label] = {
                "cagr_pct": round(cagr * 100, 2),
                "sharpe": round(sharpe, 2),
                "profit_factor": round(daily_pf, 3),
                "max_drawdown_pct": round(maxdd * 100, 2),
            }
        emp_series["cagr_delta_pt"] = round(
            emp_series["cost_2x_30bps"]["cagr_pct"] - emp_series["baseline_15bps"]["cagr_pct"], 2
        )
        emp_series["sharpe_delta"] = round(
            emp_series["cost_2x_30bps"]["sharpe"] - emp_series["baseline_15bps"]["sharpe"], 2
        )
        emp_series["pf_delta"] = round(
            emp_series["cost_2x_30bps"]["profit_factor"] - emp_series["baseline_15bps"]["profit_factor"], 3
        )
        empirical_results[sid] = emp_series

    payload = {
        "metadata": {
            "v6_source_file": str(V6_SOURCE_PATH),
            "v6_source_sha256": v6_sha,
            "status_as_tr_03_uplifted_2x": "未計測（理由: 原典v6 s02_new_3.json には片道15bpsの基準実行結果のみが格納されており、AS-TR-03配当・金利台帳アダプタによる片道30bpsの再実行データセットは本リポジトリ内に存在しないため。代数外挿による推測値は排し、受入条件に従い未計測と明記）",
        },
        "engine_empirical_runs_15bps_vs_30bps": empirical_results,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    out_sha = sha256_of_file(OUTPUT_PATH)

    print("=== APPENDIX E COST 2X AUDIT COMPLETED ===")
    print(f"Output File: {OUTPUT_PATH}")
    print(f"Output SHA256: {out_sha}")
    print(f"Source v6 SHA256: {v6_sha}")
    print("AS-TR-03 30bps Status: 未計測（理由: 原典v6に未収録・代数外挿禁止）")
    for sid, r in empirical_results.items():
        b = r["baseline_15bps"]
        c = r["cost_2x_30bps"]
        print(
            f"Engine Empirical Run {sid}: "
            f"CAGR {b['cagr_pct']:.2f}% -> {c['cagr_pct']:.2f}% ({r['cagr_delta_pt']:+.2f}%pt), "
            f"Sharpe {b['sharpe']:.2f} -> {c['sharpe']:.2f} ({r['sharpe_delta']:+.2f}), "
            f"PF {b['profit_factor']:.3f} -> {c['profit_factor']:.3f} ({r['pf_delta']:+.3f})"
        )


if __name__ == "__main__":
    main()
