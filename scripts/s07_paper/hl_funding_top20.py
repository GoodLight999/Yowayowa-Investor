#!/usr/bin/env python3
"""【B1】HL メインネット「上位20銘柄」年率ファンディング観測（手数料控除後）— 読み取りのみ。

カード t_00e2c1d7（2026-11-04）。既存 S-07 ペーパー日次ランナー（s07_daily_run.py）に
best-effort ステップとして追記され、毎日1回実行される。単独実行も可。
既存の S-07 ファイル・state・health・通知には一切触れない（追記のみ）。

対象: Hyperliquid メインネット HyperCore perp の全銘柄のうち isDelisted 以外。
      HIP-3 ビルダー dex は対象外（deployer 依存の手数料設定・別途流動性評価が要るため）。

選出: 年率換算ファンディング（絶対値）の大きい順に上位20銘柄。
      流動性フィルタは掛けない（観測目的のため。OI と 24h 出来高は記録して判断材料にする）。

年率換算式（1行）:
  ann_gross_pct = funding_rate_hourly × 24 × 365 × 100    （HL の funding は 1時間ごとに精算）

手数料控除（往復 taker 9bps = 4.5bps×2。S-07 README の約定モデルと同一の想定）:
  ann_net_pct    = |ann_gross_pct| − 0.0009 × (365/30) × 100 ＝ −1.095%pt/年（30日保有で組み替え想定）
  ann_net_pct_7d = |ann_gross_pct| − 0.0009 × (365/7)  × 100 ＝ −4.6929%pt/年（7日保有の保守参考値）
  受取側: rate>0 は long→short へ支払い（short 受取）、rate<0 は short→long へ支払い（long 受取）

殺す基準（カード t_00e2c1d7）: 30日実測で、手数料控除後の年率が 3% 未満なら候補落ち。
  判定値（KILL_GATE）= 上位20銘柄それぞれの「過去30日の HL 実測 fundingHistory（1時間足）」
  を 30 日平均レートで控除後年率に換算した値の 20 銘柄中央値。
  注意: 構成銘柄は「観測時点の上位20」なので生存者バイアスがある（過去30日ずっと上位とは限らない）。
  この観測自身の日次スナップ蓄積が30日分揃えば併記する。30日未満の間は「暫定（経過N日/30日）」と明記。

出力（data/s07_paper/ 配下・既存ファイルは変更しない）:
  funding_top20/days/YYYY-MM-DD.json   1日1ファイル（UTC日キー・同日再実行は上書きの冪等）
  funding_top20/health.json            この観測専用の稼働記録（S-07 本体の health_live.json は触らない）
  reports/FUNDING_TOP20.md             人間用レポート（毎回再生成）

実弾0・注文0: 呼ぶのは POST https://api.hyperliquid.xyz/info の読み取りのみ
（type=metaAndAssetCtxs と type=fundingHistory）。署名・/exchange は使わない。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hl_client  # noqa: E402  既存の読み取り専用クライアントを再利用（新規基盤は作らない）

# --- 定数（この観測の定義。変更したら days/*.json の formulas も変わる） ---
TOP_N = 20
WINDOW_DAYS = 30
HOURS_PER_DAY = 24
ANN_DAYS = 365
FEE_ROUND_TRIP = 0.0009           # 往復 taker 9bps（4.5bps × 2。S-07 README と同一想定）
HOLD_DAYS_PRIMARY = 30            # 主要控除の保有日数想定（カードの30日実測窓に合わせる）
HOLD_DAYS_CONSERVATIVE = 7        # 保守参考の保有日数想定
KILL_THRESHOLD_PCT = 3.0          # 殺す基準: 控除後年率 3% 未満なら候補落ち
FUNDING_PAGE_MAX_CALLS = 6        # 30日=720点 → 500点/呼び で2呼び。安全弁として6まで
PAGE_SLEEP_SEC = 0.25

DEFAULT_DATA_DIR = Path("/root/current-work/yowayowa-investor/data/s07_paper")
MS_PER_HOUR = 3_600_000

DRAG_PRIMARY_PCT = FEE_ROUND_TRIP * (ANN_DAYS / HOLD_DAYS_PRIMARY) * 100    # = 1.095 %pt/年
DRAG_CONSERVATIVE_PCT = FEE_ROUND_TRIP * (ANN_DAYS / HOLD_DAYS_CONSERVATIVE) * 100  # = 4.6929 %pt/年


def ann_gross_pct(rate_hourly: float) -> float:
    """年率換算式: rate(1h) × 24 × 365 × 100（符号つき）。"""
    return rate_hourly * HOURS_PER_DAY * ANN_DAYS * 100.0


def ann_net_pct(rate_hourly: float) -> float:
    """控除後年率（受取側の価値）: |年率| − 往復手数料の年間等価（30日保有想定）。"""
    return abs(ann_gross_pct(rate_hourly)) - DRAG_PRIMARY_PCT


def ann_net_pct_7d(rate_hourly: float) -> float:
    """控除後年率の保守参考値（7日保有想定の組み替え）。"""
    return abs(ann_gross_pct(rate_hourly)) - DRAG_CONSERVATIVE_PCT


def collect_side(rate_hourly: float) -> str:
    return "short" if rate_hourly >= 0 else "long"


def _iso_utc(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_today() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")


def _write_atomic(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=False))
    os.replace(tmp, path)


# --- HL 公開API（既存 hl_client を経由。読み取りのみ） ---

def fetch_universe() -> dict:
    """metaAndAssetCtxs（main core）から isDelisted 以外の銘柄と現在fundingを返す。"""
    raw = hl_client._post({"type": "metaAndAssetCtxs"})
    if not (isinstance(raw, list) and len(raw) == 2):
        raise hl_client.HLError(f"unexpected metaAndAssetCtxs payload: {type(raw)}")
    universe, ctxs = raw[0]["universe"], raw[1]
    eligible = []
    delisted = 0
    for asset, ctx in zip(universe, ctxs):
        if asset.get("isDelisted"):
            delisted += 1
            continue
        try:
            rate = float(ctx["funding"])
            mark = float(ctx.get("markPx") or 0.0)
            oi = float(ctx.get("openInterest") or 0.0)
            vlm = float(ctx.get("dayNtlVlm") or 0.0)
        except (KeyError, TypeError, ValueError):
            continue  # funding 欠落銘柄は選出対象外（欠測として数えない・ユニバース外扱い）
        eligible.append(
            {
                "coin": asset["name"],
                "funding_rate_hourly": rate,
                "ann_gross_pct": ann_gross_pct(rate),
                "ann_net_pct": ann_net_pct(rate),
                "ann_net_pct_7d": ann_net_pct_7d(rate),
                "side_collect": collect_side(rate),
                "open_interest_usd": round(oi * mark, 2),
                "day_ntl_vlm_usd": round(vlm, 2),
                "mark_px": mark,
            }
        )
    return {"eligible": eligible, "delisted_excluded": delisted, "universe_total": len(universe)}


def top_n(eligible: list[dict], n: int = TOP_N) -> list[dict]:
    """控除後年率（=年率絶対値 − 固定控除）の降順で上位 n を返す。"""
    return sorted(eligible, key=lambda c: (-c["ann_net_pct"], c["coin"]))[:n]


def fetch_funding_30d(coin: str, now_ms: int) -> list[dict]:
    """直近 WINDOW_DAYS 日の1時間fundingを昇順で返す（500点/呼び上限のためページング）。"""
    start_ms = now_ms - WINDOW_DAYS * 24 * MS_PER_HOUR
    out: list[dict] = []
    cursor = start_ms
    for _ in range(FUNDING_PAGE_MAX_CALLS):
        batch = hl_client.fetch_funding(coin, cursor, now_ms)
        if not batch:
            break
        out.extend(batch)
        last = batch[-1]["time"]
        if last >= now_ms - MS_PER_HOUR:
            break
        cursor = int(last) + 1
        time.sleep(PAGE_SLEEP_SEC)
    # 重複除去・昇順（冪等）
    seen: dict[int, float] = {}
    for p in out:
        seen[int(p["time"])] = float(p["rate"])
    return [{"time": t, "rate": seen[t]} for t in sorted(seen)]


def hist30d_stats(points: list[dict]) -> dict:
    """30日実測fundingの統計（平均・中央値を控除後年率へ換算した値つき）。"""
    rates = [p["rate"] for p in points]
    mean_r = statistics.fmean(rates)
    median_r = statistics.median(rates)
    return {
        "status": "ok",
        "n_points": len(rates),
        "expected_points": WINDOW_DAYS * 24,
        "coverage": round(len(rates) / (WINDOW_DAYS * 24), 4),
        "from_utc": _iso_utc(points[0]["time"]),
        "to_utc": _iso_utc(points[-1]["time"]),
        "mean_rate_hourly": mean_r,
        "median_rate_hourly": median_r,
        "ann_gross_mean_pct": ann_gross_pct(mean_r),
        "ann_gross_median_pct": ann_gross_pct(median_r),
        "ann_net_mean_pct": ann_net_pct(mean_r),
        "ann_net_median_pct": ann_net_pct(median_r),
        "ann_net_mean_pct_7d": ann_net_pct_7d(mean_r),
    }


def load_day_files(days_dir: Path) -> list[dict]:
    """既存の日次ファイル（day_key昇順）。壊れた行は欠測として数える。"""
    out = []
    if not days_dir.exists():
        return out
    for p in sorted(days_dir.glob("*.json")):
        try:
            out.append(json.loads(p.read_text()))
        except (json.JSONDecodeError, OSError):
            continue
    return out


# --- メイン ---

def run(data_dir: Path) -> dict:
    now_ms = int(time.time() * 1000)
    day_key = _utc_today()
    top_dir = data_dir / "funding_top20"
    days_dir = top_dir / "days"
    reports_dir = data_dir / "reports"

    uni = fetch_universe()
    eligible = uni["eligible"]
    picks = top_n(eligible)

    # 上位20の過去30日実測（HL fundingHistory・1時間足）
    missing: list[dict] = []
    for c in picks:
        try:
            pts = fetch_funding_30d(c["coin"], now_ms)
            if not pts:
                raise hl_client.HLError("fundingHistory empty (上場日が窓内か・API応答空)")
            c["hist30d"] = hist30d_stats(pts)
        except Exception as exc:  # noqa: BLE001 - 欠測は「未取得」と明記して続行
            c["hist30d"] = {"status": "未取得", "error": f"{type(exc).__name__}: {exc}"[:200]}
            missing.append({"coin": c["coin"], "error": c["hist30d"]["error"]})
        time.sleep(PAGE_SLEEP_SEC)

    # 集計: スナップショット（今日の上位20）と 30日実測（KILL_GATE）
    snap_nets = [c["ann_net_pct"] for c in picks]
    snap_nets_7d = [c["ann_net_pct_7d"] for c in picks]
    nets_mean = [c["hist30d"]["ann_net_mean_pct"] for c in picks if c["hist30d"]["status"] == "ok"]
    nets_med = [c["hist30d"]["ann_net_median_pct"] for c in picks if c["hist30d"]["status"] == "ok"]
    gate_value = statistics.median(nets_mean) if nets_mean else None

    # この観測自身の蓄積（暫定値・経過日数）
    prior = [d for d in load_day_files(days_dir) if d.get("day_key") != day_key]
    all_days_after = prior + [None]  # None は当日分（このあと書く）
    first_day = prior[0]["day_key"] if prior else day_key
    d0 = dt.date.fromisoformat(first_day)
    dnow = dt.date.fromisoformat(day_key)
    elapsed = (dnow - d0).days + 1
    recorded_days = len(prior) + 1
    missing_days = [
        (d0 + dt.timedelta(days=i)).isoformat()
        for i in range(elapsed)
        if (d0 + dt.timedelta(days=i)).isoformat() not in {d["day_key"] for d in prior}
        and (d0 + dt.timedelta(days=i)).isoformat() != day_key
    ]
    day30 = (d0 + dt.timedelta(days=29)).isoformat()
    day_medians = []
    for d in prior:
        sa = d.get("snapshot_aggregate") or {}
        if sa.get("median_ann_net_pct") is not None:
            day_medians.append(float(sa["median_ann_net_pct"]))

    if gate_value is None:
        verdict_now = "判定不能（30日実測が未取得）"
    elif gate_value >= KILL_THRESHOLD_PCT:
        verdict_now = f"基準以上（≥{KILL_THRESHOLD_PCT}%）・候補維持の水準"
    else:
        verdict_now = f"基準未満（<{KILL_THRESHOLD_PCT}%）・30日満了時に候補落ちとなる水準"

    formulas = {
        "annualize": "ann_gross_pct = funding_rate_hourly × 24 × 365 × 100（HLは1時間精算）",
        "fee_round_trip": f"{FEE_ROUND_TRIP}（taker 4.5bps × 2。S-07 README の約定モデルと同一想定）",
        "net_primary": f"ann_net_pct = |ann_gross_pct| − {DRAG_PRIMARY_PCT:.4f}%pt/年"
                       f"（往復手数料を {HOLD_DAYS_PRIMARY} 日保有の組み替え 年365/{HOLD_DAYS_PRIMARY}回 で償却）",
        "net_conservative": f"ann_net_pct_7d = |ann_gross_pct| − {DRAG_CONSERVATIVE_PCT:.4f}%pt/年"
                            f"（{HOLD_DAYS_CONSERVATIVE} 日保有想定の保守参考値）",
        "side": "funding_rate>0: long→short へ支払い（short 受取）/ <0: short→long へ支払い（long 受取）",
        "kill_gate": f"上位20銘柄の過去{WINDOW_DAYS}日実測（1時間足）30日平均レートを控除後年率へ換算し、"
                     f"20銘柄中央値を判定値とする。基準 {KILL_THRESHOLD_PCT}% 未満なら候補落ち",
    }

    record = {
        "schema_version": 1,
        "card": "t_00e2c1d7",
        "day_key": day_key,
        "generated_at_utc": _iso_utc(now_ms),
        "source": "POST https://api.hyperliquid.xyz/info (type=metaAndAssetCtxs, type=fundingHistory) 読み取りのみ・注文0",
        "universe": {
            "scope": "HL メインネット HyperCore perp（HIP-3 dex 対象外・流動性フィルタなし）",
            "total": uni["universe_total"],
            "delisted_excluded": uni["delisted_excluded"],
            "eligible": len(eligible),
        },
        "formulas": formulas,
        "coins": picks,
        "snapshot_aggregate": {
            "n": len(picks),
            "mean_ann_net_pct": round(statistics.fmean(snap_nets), 4),
            "median_ann_net_pct": round(statistics.median(snap_nets), 4),
            "mean_ann_net_7d_pct": round(statistics.fmean(snap_nets_7d), 4),
            "median_ann_net_7d_pct": round(statistics.median(snap_nets_7d), 4),
        },
        "hist30d_aggregate": {
            "coins_with_hist": len(nets_mean),
            "coins_unfetched": missing,
            "per_coin_ann_net_mean_median_pct": round(statistics.median(nets_mean), 4) if nets_mean else None,
            "per_coin_ann_net_mean_mean_pct": round(statistics.fmean(nets_mean), 4) if nets_mean else None,
            "per_coin_ann_net_median_median_pct": round(statistics.median(nets_med), 4) if nets_med else None,
            "kill_gate_value_pct": round(gate_value, 4) if gate_value is not None else None,
            "kill_gate_threshold_pct": KILL_THRESHOLD_PCT,
            "verdict_now": verdict_now,
        },
        "provisional": {
            "is_provisional": elapsed < 30,
            "elapsed_days": elapsed,
            "recorded_days": recorded_days,
            "missing_days": missing_days,
            "first_day": first_day,
            "day30_date": day30,
            "label": f"暫定（経過{elapsed}日/30日・30日満了 {day30}）",
            "snapshot_median_series": {
                "n_days": len(day_medians) + 1,
                "median_of_day_medians_pct": round(statistics.median(day_medians + [float(statistics.median(snap_nets))]), 4),
            },
        },
    }

    # --- 【B2】清算検知用データ追加（カード t_7a53946d・2026-11-06 純追記・S-07本体無変更） ---
    # 1m蜡烛（1440点/銘柄/日）・OI高頻度スナップショット（15分間隔・前向き蓄積）・
    # 代理検知件数（清算実値ではない）を day ファイルへ追記する。B1の記録は保つ（best-effort）。
    try:
        import hl_liq_obs  # noqa: E402  追記モジュール（既存B1ロジックには触らない）

        record["b2_liquidation_observation"] = hl_liq_obs.enrich(
            data_dir=data_dir, record=record, picks=picks, now_ms=now_ms
        )
    except Exception as exc:  # noqa: BLE001 - B2は best-effort（B1の記録は落とさない）
        import traceback

        traceback.print_exc()
        record["b2_liquidation_observation"] = {
            "status": "failed", "card": "t_7a53946d",
            "error": f"{type(exc).__name__}: {exc}"[:300],
            "note": "B2のみ欠落。翌日cronで自動再試行される（B1の蓄積は無影響）",
        }

    _write_atomic(days_dir / f"{day_key}.json", record)

    # この観測専用の稼働記録（S-07 本体の health は触らない）
    health_path = top_dir / "health.json"
    try:
        health = json.loads(health_path.read_text()) if health_path.exists() else {}
    except (json.JSONDecodeError, OSError):
        health = {}
    health.setdefault("history", []).append(
        {"at": _iso_utc(now_ms), "event": "ok", "day_key": day_key,
         "coins": len(picks), "hist_ok": len(nets_mean), "gate_pct": record["hist30d_aggregate"]["kill_gate_value_pct"]}
    )
    health["history"] = health["history"][-100:]
    health["last_ok"] = _iso_utc(now_ms)
    health["last_error"] = None
    _write_atomic(health_path, health)

    write_report(reports_dir / "FUNDING_TOP20.md", record, prior)

    return record


def _fmt(v, nd=2, na="—"):
    return na if v is None else f"{v:.{nd}f}"


def write_report(path: Path, rec: dict, prior: list[dict]) -> None:
    """人間用レポート（毎回再生成・上書き）。"""
    prov = rec["provisional"]
    h30 = rec["hist30d_aggregate"]
    snap = rec["snapshot_aggregate"]
    lines = []
    a = lines.append
    a(f"# HL 上位20銘柄 年率ファンディング観測（手数料控除後）—【B1】{rec['card']}")
    a("")
    a(f"- 生成: {rec['generated_at_utc']} / 観測日(UTC): {rec['day_key']} / **{prov['label']}**")
    a(f"- ソース: {rec['source']}")
    a(f"- 対象: {rec['universe']['scope']} / universe={rec['universe']['total']}・"
      f"delisted除外={rec['universe']['delisted_excluded']}・選出対象={rec['universe']['eligible']}銘柄")
    a("")
    a("## 換算式・手数料控除（この観測の固定定義）")
    a("")
    for k in ("annualize", "fee_round_trip", "net_primary", "net_conservative", "side", "kill_gate"):
        a(f"- **{k}**: {rec['formulas'][k]}")
    a("")
    a(f"## 今日の上位20銘柄（スナップショット {rec['day_key']}・控除後年率の降順）")
    a("")
    a("| # | 銘柄 | funding(1h) | 受取側 | 年率% | 控除後年率% | 保守参考% | OI($) | 24h出来高($) |")
    a("|---|---|---|---|---|---|---|---|---|")
    for i, c in enumerate(rec["coins"], 1):
        a(f"| {i} | {c['coin']} | {c['funding_rate_hourly']:.7f} | {c['side_collect']} | "
          f"{c['ann_gross_pct']:.2f} | {c['ann_net_pct']:.2f} | {c['ann_net_pct_7d']:.2f} | "
          f"{c['open_interest_usd']:,.0f} | {c['day_ntl_vlm_usd']:,.0f} |")
    a("")
    a(f"スナップ集計（20銘柄）: 控除後年率 平均={_fmt(snap['mean_ann_net_pct'])}%・"
      f"中央値={_fmt(snap['median_ann_net_pct'])}% ／ 保守参考（7日保有想定） 平均={_fmt(snap['mean_ann_net_7d_pct'])}%・"
      f"中央値={_fmt(snap['median_ann_net_7d_pct'])}%")
    a("")
    a(f"## 過去30日の HL 実測（上位20構成銘柄・生存者バイアスあり）")
    a("")
    a("| 銘柄 | 30d平均(1h) | 30d中央値(1h) | 控除後年率%(平均ベース) | 控除後年率%(中央値ベース) | 取得点数/720 | 窓 |")
    a("|---|---|---|---|---|---|---|")
    for c in rec["coins"]:
        h = c["hist30d"]
        if h["status"] != "ok":
            a(f"| {c['coin']} | 未取得 | 未取得 | 未取得 | 未取得 | 0/720 | {h['error']} |")
        else:
            a(f"| {c['coin']} | {h['mean_rate_hourly']:.7f} | {h['median_rate_hourly']:.7f} | "
              f"{h['ann_net_mean_pct']:.2f} | {h['ann_net_median_pct']:.2f} | "
              f"{h['n_points']}/{h['expected_points']} | {h['from_utc'][:10]}〜{h['to_utc'][:10]} |")
    a("")
    a(f"30日実測の集計（取得 {h30['coins_with_hist']}/20銘柄"
      f"{'・欠測: ' + ', '.join(m['coin'] for m in h30['coins_unfetched']) if h30['coins_unfetched'] else '・欠測なし'}）:")
    a(f"- 20銘柄の控除後年率（30d平均ベース）: 平均={_fmt(h30['per_coin_ann_net_mean_mean_pct'])}%・"
      f"中央値={_fmt(h30['per_coin_ann_net_mean_median_pct'])}%")
    a(f"- 20銘柄の控除後年率（30d中央値ベース・参考）: 中央値={_fmt(h30['per_coin_ann_net_median_median_pct'])}%")
    a("")
    a("## 殺す基準の現在値（KILL_GATE: 3% 未満なら候補落ち）")
    a("")
    a(f"- 判定値 = 20銘柄の控除後年率（30d平均ベース）の**中央値 = {_fmt(h30['kill_gate_value_pct'])}%** "
      f"/ 基準 {h30['kill_gate_threshold_pct']}% → **{h30['verdict_now']}**")
    a(f"- 確定判定: 観測30日満了（{prov['day30_date']} 以降）の日次レポートで自動計算・記録する。"
      f"それまでは経過{prov['elapsed_days']}日の暫定値。")
    a("- 注意: ①構成銘柄は観測時点の上位20（生存者バイアス）②控除は手数料のみ（スリッページ・スポットヘッジ費用は不含）"
      "③30日窓は HL 実測の直近30日完全窓（fundingHistory 1時間足）。")
    a("")
    a("## 日次スナップ蓄積（この観測自身の記録）")
    a("")
    prior_ok = [d for d in prior if (d.get("snapshot_aggregate") or {}).get("median_ann_net_pct") is not None]
    series = [float(d["snapshot_aggregate"]["median_ann_net_pct"]) for d in prior_ok] + [float(snap["median_ann_net_pct"])]
    a(f"- 記録済み {prov['recorded_days']}日 / 経過 {prov['elapsed_days']}日"
      f"（初日 {prov['first_day']}・30日満了 {prov['day30_date']}）")
    a(f"- 欠測: {', '.join(prov['missing_days']) if prov['missing_days'] else 'なし'}")
    a(f"- 日次「上位20の控除後年率中央値」の平均={_fmt(statistics.fmean(series))}%・"
      f"中央値={_fmt(statistics.median(series))}%（{len(series)}日分・経過{prov['elapsed_days']}日の暫定値）")
    a("")
    a("## 非干渉（追記のみの証明）")
    a("")
    a("- S-07 本体（fetch / runner / gate / state / health / Telegram通知）は変更なし。"
      "この観測は `funding_top20/` 配下と `reports/FUNDING_TOP20.md` にのみ書く。")
    a("- 実弾0・注文0。呼ぶのは HL 公開APIの読み取りのみ。テストネット含め注文は出さない。")
    a("")
    # --- 【B2】清算検知用データ追加（t_7a53946d・代理検知件数=清算実値ではない） ---
    b2 = rec.get("b2_liquidation_observation") or {}
    if b2:
        a("")
        a("## 【B2】清算検知用データ追加（1m蜡烛・OI高頻度スナップ・代理検知件数: 清算実値ではない）")
        a("")
        st = "ok" if b2.get("status") == "ok" else f"failed: {str(b2.get('error', ''))[:80]}"
        a(f"- 状態: {st} / 記録時刻: {b2.get('collected_at_utc', '—')}")
        c1 = b2.get("candles_1m") or {}
        coll = c1.get("collection") or {}
        a(f"- 1m蜡烛: 目標1440点/銘柄/日・冪等マージ / 完全UTC日: {c1.get('latest_complete_day', '—')}"
          f" / 銘柄取得ok={coll.get('coins_ok', '—')}・欠測銘柄={coll.get('coins_failed', [])}")
        oi = b2.get("oi_snapshots") or {}
        oi_t = oi.get("today") or {}
        oi_s = oi.get("source_day") or {}
        a(f"- OIスナップ: 当日={oi_t.get('count', 0)}回・間隔中央値={oi_t.get('median_interval_seconds', '—')}s / "
          f"代理検知対象日({oi_s.get('day', '—')})={oi_s.get('count', 0)}回"
          f"（公的OI履歴API不在のため自前前向き蓄積が唯一）")
        pr = b2.get("liquidation_proxy") or {}
        cnt = pr.get("代理検知件数") or {}
        a(f"- **代理検知件数（清算実値ではない・対象={pr.get('proxy_source_day') or '—'}）**: "
          f"typeB相当(1m)={cnt.get('代理検知件数_typeB相当_1m', '—')}件 / "
          f"typeB相当(1m+OI低下)={cnt.get('代理検知件数_typeB相当_1m_OI低下含む', '—')}件 / "
          f"typeA相当(1m)={cnt.get('代理検知件数_typeA相当_1m', '—')}件 / "
          f"typeA相当(1m+OI低下)={cnt.get('代理検知件数_typeA相当_1m_OI低下含む', '—')}件")
        a("  - 注意: 公式APIに市場全体の清算履歴は無い（type=liquidations deserialization失敗・t_b6e4bec8実測）。"
          "第三者データの採用は裁可待ち。OI低下は15分スナップの前後比較で評価。OI欠測日は「計上不能・欠測」と明記。")
        lgd = b2.get("events_ledger") or {}
        a(f"- 出来事台帳: {lgd.get('name', '—')}（{lgd.get('path', '—')}）・台帳件数={lgd.get('total_events', 0)}"
          f"・予約欄「保有中funding収支」={(lgd.get('reserved_column') or {}).get('holding_funding_pnl')}"
          f"（t_b6e4bec8指摘3・実装未着手の予約）")
        vps = b2.get("vps_load") or {}
        oi_extra_kb = round((vps.get("oi_per_snapshot_kb") or 1.3) * 96, 1) if vps.get("oi_per_snapshot_kb") else "—"
        a(f"- VPS負荷推計: 日次追記≈{vps.get('daily_increment_estimate_kb', '—')}KB/日"
          f"（1m蜡烛 実測平均{vps.get('candles_mean_file_kb', '—')}KB/日・OI外挿≈{oi_extra_kb}KB/日）"
          f"・日次追加API呼び出し≈136回（1m蜡烛≤40＋OI 96）・28日で約0.2GB未満")
        thr = b2.get("thresholds") or {}
        a(f"- 閾値: 下ヒレW={thr.get('wick_threshold')}（{thr.get('wick_status', '—')}）/"
          f"OI低下 {thr.get('oi_drop_min_pct')}%（{thr.get('oi_drop_status', '—')}）")
        a("")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(lines) + "\n")
    os.replace(tmp, path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="【B1】HL 上位20銘柄 年率ファンディング観測（読み取りのみ）")
    ap.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    ap.add_argument("--json", action="store_true", help="1行JSONサマリを出力")
    args = ap.parse_args(argv)
    t0 = time.time()
    try:
        rec = run(Path(args.data_dir))
    except Exception as exc:  # noqa: BLE001 - 失敗はこの観測専用の health に記録して exit 1
        top_dir = Path(args.data_dir) / "funding_top20"
        health_path = top_dir / "health.json"
        try:
            health = json.loads(health_path.read_text()) if health_path.exists() else {}
        except (json.JSONDecodeError, OSError):
            health = {}
        health.setdefault("history", []).append(
            {"at": _iso_utc(int(time.time() * 1000)), "event": "fail",
             "error": f"{type(exc).__name__}: {exc}"[:300]}
        )
        health["history"] = health["history"][-100:]
        health["last_error"] = f"{type(exc).__name__}: {exc}"[:300]
        try:
            _write_atomic(health_path, health)
        except OSError:
            pass
        print(f"FUNDING_TOP20_FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    h30 = rec["hist30d_aggregate"]
    summary = {
        "ok": True,
        "day_key": rec["day_key"],
        "coins": len(rec["coins"]),
        "hist30d_ok": h30["coins_with_hist"],
        "hist30d_unfetched": [m["coin"] for m in h30["coins_unfetched"]],
        "gate_pct": h30["kill_gate_value_pct"],
        "verdict_now": h30["verdict_now"],
        "provisional": rec["provisional"]["label"],
        "b2": {
            "status": (rec.get("b2_liquidation_observation") or {}).get("status"),
            "collector": (rec.get("b2_liquidation_observation") or {}).get("collector", {}).get("status"),
            "complete_day": (rec.get("b2_liquidation_observation") or {})
            .get("candles_1m", {}).get("latest_complete_day"),
            "proxy_counts": (rec.get("b2_liquidation_observation") or {})
            .get("liquidation_proxy", {}).get("代理検知件数"),
        },
        "elapsed_seconds": round(time.time() - t0, 1),
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False))
    else:
        print(f"上位{len(rec['coins'])}銘柄記録・30日実測{h30['coins_with_hist']}/20取得・"
              f"KILL_GATE中央値={h30['kill_gate_value_pct']}% → {h30['verdict_now']}・{rec['provisional']['label']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
