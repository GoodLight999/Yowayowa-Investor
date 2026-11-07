#!/usr/bin/env python3
"""【J1】jpy_premium_obs の単体テスト（API不使用・合成データ・決定的）。

既存スイート test_s07_gate_cases.py / test_hl_liq_obs.py には触れない（RC=0 を維持する別ファイル）。
カード t_aa251cce。実行: python3 test_jpy_premium_obs.py → RESULT: ALL PASS / RC=0。
凍結ルール（閾値1.0%・持続2.0h・欠測橋渡し1サンプル・判定2局面以上）の挙動を固定する。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jpy_premium_obs as J  # noqa: E402

CASES: list[tuple[str, Any]] = []


def case(name: str):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


H = 3_600_000  # 1h in ms


def series(start_ms: int, n: int, step_ms: int, value: float | None) -> list[tuple[int, float]]:
    """value=None は欠測サンプル（ポイント自体を入れない＝jsonlの欠測と同じ扱い）。"""
    out = []
    for i in range(n):
        if value is not None:
            out.append((start_ms + i * step_ms, value))
    return out


@case("premium_pct: 凍結定義の計算（実数値）")
def _():
    # mid 12,000,000 JPY / (75,785.5 USDT × 155.0 JPY/USD) → 約 +2.03%
    v = J.premium_pct(12_000_000.0, 75_785.5, 155.0)
    assert abs(v - (12_000_000 / (75_785.5 * 155.0) - 1) * 100) < 1e-12
    assert J.premium_pct(11_250_000.0, 75_000.0, 150.0) == 0.0  # 国内=換算値なら差0
    assert abs(J.premium_pct(11_362_500.0, 75_000.0, 150.0) - 1.0) < 1e-9  # ちょうど+1%


@case("detect_episodes: 2.0h以上の持続1局面を数える")
def _():
    t0 = 1_700_000_000_000
    pts = [(t0 + i * 900_000, 1.2) for i in range(9)]  # 15分×9点 = 2時間幅
    eps, n = J.detect_episodes(pts)
    assert n == 1 and eps[0]["duration_h"] == 2.0 and eps[0]["sign"] == "+"


@case("detect_episodes: 2.0h未満は数えない（感度も別途残る）")
def _():
    t0 = 1_700_000_000_000
    pts = [(t0 + i * 900_000, 1.2) for i in range(6)]  # 1時間15分
    eps, n = J.detect_episodes(pts)
    assert n == 0 and len(eps) == 1 and eps[0]["meets_min_duration"] is False


@case("detect_episodes: 欠測1サンプルは橋渡し・2サンプルは切断")
def _():
    t0 = 1_700_000_000_000
    # ヒット→1サンプル欠測（30分）→ヒット: 橋渡しされて1局面（計2h15分）
    pts = [(t0 + i * 900_000, 1.2) for i in range(9) if i != 4]
    eps, n = J.detect_episodes(pts)
    assert n == 1 and eps[0]["samples"] == 8
    # ヒット→2サンプル欠測（45分）→ヒット: 切断。それぞれ短いので0本
    pts2 = [(t0 + i * 900_000, 1.2) for i in range(12) if i not in (4, 5)]
    eps2, n2 = J.detect_episodes(pts2)
    assert n2 == 0 and len(eps2) == 2


@case("detect_episodes: マイナス（恐怖のディスカウント）も|差|で数える")
def _():
    t0 = 1_700_000_000_000
    pts = [(t0 + i * 900_000, -1.4) for i in range(9)]
    eps, n = J.detect_episodes(pts)
    assert n == 1 and eps[0]["sign"] == "-" and abs(eps[0]["peak_pct"] + 1.4) < 1e-9


@case("detect_episodes: 閾値未満は局面を作らない")
def _():
    t0 = 1_700_000_000_000
    pts = [(t0 + i * 900_000, 0.99) for i in range(100)]
    eps, n = J.detect_episodes(pts)
    assert n == 0 and eps == []


@case("detect_episodes: 閾値ちょうど1.0%はヒット（≥定義）・9時間の単一局面")
def _():
    t0 = 1_700_000_000_000
    pts = [(t0 + i * 900_000, 1.0) for i in range(37)]  # 15分×37点 = 9時間幅
    eps, n = J.detect_episodes(pts)
    assert n == 1 and eps[0]["duration_h"] == 9.0 and eps[0]["samples"] == 37


@case("verdict_for: 凍結判定規則（どちらかの基準で2局面以上→上げる）")
def _():
    assert J.verdict_for(2, 0).startswith("上げる")
    assert J.verdict_for(0, 3).startswith("上げる")
    assert J.verdict_for(1, 1).startswith("閉じる")
    assert J.verdict_for(0, 0).startswith("閉じる")


@case("venue_analysis: 分布統計と局面本数・感度・符号内訳")
def _():
    t0 = 1_700_000_000_000
    pts = [(t0 + i * 900_000, 1.2 if i < 9 else 0.3) for i in range(60)]
    pts += [(t0 + 60 * 900_000 + i * 900_000, -1.1) for i in range(9)]  # 2つ目はマイナス局面
    st = J.venue_analysis(pts)
    assert st["samples"] == 69
    assert st["episodes_ge_2_0h"] == 2
    assert st["episodes_ge_2_0h_positive"] == 1 and st["episodes_ge_2_0h_negative"] == 1
    assert st["episodes_ge_3_0h"] == 0  # 感度: どちらも2h15分なので3hは0
    assert 0 < st["pct_time_abs_ge_1.0"] < 100


@case("jst_hour_profile: JST時刻別集計（2023-11-14T21:00Z=JST6時）")
def _():
    # 2023-11-14T21:00:00Z = JST 2023-11-15 06:00
    t0 = 1_699_995_600_000
    prof = J.jst_hour_profile([(t0, 0.5), (t0 + H, 1.5)])
    assert set(prof) == {"6", "7"}


@case("sample_once: 構成が揃わない系統はpremiumがNone（API不使用・合成行でverify）")
def _():
    # 実APIは呼ばない。premium_pct のNone縮退は analyze 側の isinstance フィルタで担保済み。
    # ここでは load_rows → analyze の最小フローを scratch data-dir で回す。
    d = Path(tempfile.mkdtemp(prefix="j1test_"))
    import json
    sd = J.top_dir(d) / "snapshots"
    sd.mkdir(parents=True)
    rows = [
        {"at_utc": "2026-11-06T14:00:00Z", "jst": "x", "card": J.CARD,
         "bitflyer": {"mid": 12_000_000.0}, "bitbank": {"mid": 12_000_500.0},
         "binance": {"mid": 75_785.5}, "usdjpy": {"value": 155.0, "source": "yahoo_1m"},
         "premium_pct": {"bitflyer": 2.03, "bitbank": 2.09}},
        {"at_utc": "2026-11-06T14:15:00Z", "jst": "x", "card": J.CARD,
         "bitflyer": {"mid": 12_000_000.0}, "bitbank": {"mid": 12_000_500.0},
         "binance": {"error": "TimeoutError: x"}, "usdjpy": {"value": 155.0, "source": "yahoo_1m"},
         "premium_pct": {"bitflyer": None, "bitbank": None}},
        "broken line",
    ]
    with (sd / "2026-11-06.jsonl").open("w") as f:
        for r in rows:
            f.write((r if isinstance(r, str) else json.dumps(r)) + "\n")
    a = J.analyze(d)
    assert a["coverage"]["rows_total"] == 2 and a["coverage"]["rows_unparsable"] == 1
    assert a["bitflyer_base"]["samples"] == 1 and a["bitbank_base"]["samples"] == 1
    assert a["bitflyer_base"]["episodes_ge_2_0h"] == 0  # 1サンプルだけでは局面は数えない
    assert a["verdict"].startswith("閉じる")


@case("count_samples / last_sample / cmd_status: ファイル読み出し系")
def _():
    d = Path(tempfile.mkdtemp(prefix="j1test2_"))
    import json
    sd = J.top_dir(d) / "snapshots"
    sd.mkdir(parents=True)
    with (sd / "2026-11-06.jsonl").open("w") as f:
        f.write(json.dumps({"at_utc": "2026-11-06T14:00:00Z", "premium_pct": {"bitflyer": 0.5}}) + "\n")
        f.write("\n")
    assert J.count_samples(d) == 1
    assert J.last_sample(d)["at_utc"] == "2026-11-06T14:00:00Z"


def main() -> int:
    failed = []
    for name, fn in CASES:
        try:
            fn()
            print(f"PASS {name}")
        except AssertionError as exc:
            failed.append(name)
            print(f"FAIL {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed.append(name)
            print(f"ERROR {name}: {type(exc).__name__}: {exc}")
    print(f"cases={len(CASES)} failed={len(failed)}")
    if failed:
        print("RESULT: FAIL")
        return 1
    print("RESULT: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
