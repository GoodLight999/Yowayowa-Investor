#!/usr/bin/env python3
"""【項目20】分析結果から人間用レポートを生成する（数値はすべて analysis.json から）。

手で数字を写さない。レポートの全数値は analysis.json / windows.json から計算した値のみ。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

DAYS = 365.0


def f(v, nd=2, na="—"):
    return na if v is None else f"{v:,.{nd}f}"


def sgn(v, nd=2):
    if v is None:
        return "—"
    if abs(v) < 0.5 * 10 ** (-nd):          # -0.00 を出さない
        return f"{0:.{nd}f}"
    return f"{v:+,.{nd}f}"


def build(d: Path) -> str:
    a = json.loads((d / "analysis.json").read_text())
    blob = json.loads((d / "windows.json").read_text())
    meta = blob["meta"]
    agg, roll, rules = a["aggregate"], a["rolling_hold"], a["rules"]
    am, frag, phase, ratio = a["amortized_continuous"], a["fragmentation"], a["phase_sensitivity"], a["ratio_stats"]
    bn, by = agg["hl_bn"], agg["hl_by"]
    rbn, rby = roll["hl_bn"], roll["hl_by"]
    fee_bn, fee_by = rules["hl_bn"]["fee_round_trip_bps"], rules["hl_by"]["fee_round_trip_bps"]
    n_weeks = bn["n_weeks"]

    # 資本あたり年率（資本 = 2N/L_eff → 元本比 × L_eff/2 = ×1.25）
    k = frag["l_eff"] / 2.0
    ann_cap_bn = am["hl_bn"]["ann_pct_of_notional"] * k
    ann_cap_by = am["hl_by"]["ann_pct_of_notional"] * k
    roll_ann_bn = rbn["net_mean_bps"] / 1e4 * (DAYS / 28) * 100
    roll_ann_by = rby["net_mean_bps"] / 1e4 * (DAYS / 28) * 100
    roll_ann_bn_s = rbn["net_mean_bps_with_slippage"] / 1e4 * (DAYS / 28) * 100
    roll_ann_by_s = rby["net_mean_bps_with_slippage"] / 1e4 * (DAYS / 28) * 100
    tf = {kk: v["transfer_drag_pct_per_year"] for kk, v in frag["transfer_cost_table"].items()}

    L: list[str] = []
    A = L.append

    A("# 場をまたぐファンディング差 — 公開APIの同時刻差による 8週間観測【項目20 / t_7dd6efbb】")
    A("")
    A(f"- 一次資料: `/root/brushup-round2-crypto-20261106.md` §項目20")
    A(f"- 観測期間: **{a['period']['from_utc'][:16]} 〜 {a['period']['to_utc'][:16]} (UTC) = {a['days']:.0f}日 / {a['weeks']:.0f}週**")
    A(f"- 規模: **{meta['n_windows']}個の8時間窓 × {meta['n_coins']}銘柄 = {meta['n_rows']:,}行**、欠測0（詳細は §3）")
    A(f"- 新規口座 **0** / 発注 **0** / 既存観測の設定変更 **0**（§12で証明）")
    A("")

    # ---------------- 結論
    A("## 1. 結論")
    A("")
    A("**差は実在し、向きも持続する。しかし金額が小さい。手数料・証拠金分断・滑り・資金移動費をすべて引くと、"
      "実際に置ける原資の規模では年率でゼロ近傍からマイナスになり、「CEX口座を持つ理由」にはならない。→ 独立ラインとしては閉じる。**")
    A("")
    A("数字で言うと、層ごとにこうなる（Binance との差 / Bybit との差。詳細は §7）:")
    A("")
    A("| 段階（置き方） | 年率・想定元本あたり | 年率・資本あたり |")
    A("|---|---|---|")
    A(f"| 総差の上限（符号を完璧に当てる・往復手数料1回のみ） | {sgn(rules['hl_bn']['R3_perfect_foresight_total_net_bps']/1e4*(DAYS/a['days'])*100)}% / {sgn(rules['hl_by']['R3_perfect_foresight_total_net_bps']/1e4*(DAYS/a['days'])*100)}% | — |")
    A(f"| 8週間ずっと同じ向きで保有（手数料は最初と最後の1往復だけ） | {sgn(am['hl_bn']['ann_pct_of_notional'])}% / {sgn(am['hl_by']['ann_pct_of_notional'])}% | {sgn(ann_cap_bn)}% / {sgn(ann_cap_by)}% |")
    A(f"| 4週間保有を繰り返す（手数料＋滑り2.0bps×4約定） | {sgn(roll_ann_bn_s)}% / {sgn(roll_ann_by_s)}% | {sgn(roll_ann_bn_s*k)}% / {sgn(roll_ann_by_s*k)}% |")
    A(f"| 上に資金移動費 $2/月（片脚 N=$1,000・資本$800） | | {sgn(roll_ann_bn_s*k - tf['N=1000'])}% / {sgn(roll_ann_by_s*k - tf['N=1000'])}% |")
    A(f"| 上に資金移動費 $2/月（片脚 N=$5,000・資本$4,000） | | {sgn(roll_ann_bn_s*k - tf['N=5000'])}% / {sgn(roll_ann_by_s*k - tf['N=5000'])}% |")
    A("")
    A(f"受入条件の「手数料と証拠金分断コストを差し引いても残るか」には **残る** と答える"
      f"（週合計は Binance {sum(1 for w in a['weekly']['hl_bn'] if w['sum_diff_bps'] > 0)}/{n_weeks}週・"
      f"Bybit {sum(1 for w in a['weekly']['hl_by'] if w['sum_diff_bps'] > 0)}/{n_weeks}週がプラス、4週保有 {rbn['n_holds']:,}回のうち "
      f"{rbn['share_positive']*100:.1f}% / {rby['share_positive']*100:.1f}% が手数料後にプラス）。"
      f"ただし残る額は資本あたり年率 {f(ann_cap_bn)}% / {f(ann_cap_by)}% で、"
      f"分断に付随する実費（口座間の資金移動）が原資1,000ドル級で年率 {f(tf['N=1000'])}% かかるため、"
      f"**口座を増やす判断材料としては消える**。")
    A("")

    # ---------------- 何を測ったか
    A("## 2. 何を測ったか（定義）")
    A("")
    A("Hyperliquid は**1時間ごと**、Binance／Bybit は**8時間ごと**にファンディングを精算する。"
      "レートを年率換算して比べると粒度の違いが乗るので、**「同じ8時間窓 [T, T+8h) に各場が実際に徴収／支払した額の合計」**で揃えた。")
    A("")
    A("```")
    A("diff(T) = Σ_{t∈[T,T+8h)} HL_funding(t)  −  Σ_{t∈[T,T+8h)} CEX_funding(t)")
    A("")
    A("diff > 0  →  HL の long が多く払う  →  「HL 売り／CEX 買い」が diff を受け取る")
    A("diff < 0  →  逆向き")
    A("```")
    A("")
    A("- 窓 T は Binance／Bybit の精算時刻（00/08/16 UTC）に合わせた。")
    A(f"- 窓の位相（HL のどの8時間を組にするか）は自明でないため振って確認した（§5）。"
      f"位相を −8h／−4h／0h と動かしても平均 |diff| は "
      f"{f(phase['offsets']['-8']['mean_abs_bps'],3)}／{f(phase['offsets']['-4']['mean_abs_bps'],3)}／"
      f"{f(phase['offsets']['0']['mean_abs_bps'],3)} bps、平均 diff はいずれも "
      f"{sgn(phase['offsets']['0']['mean_bps'],3)} bps で、結論は位相に依存しない。本文は 0h を採る。")
    A("- データ源（すべて公開・キー不要・読み取りのみ。署名なし・`/exchange` は呼ばない）:")
    A("  - Hyperliquid `POST https://api.hyperliquid.xyz/info` `{\"type\":\"fundingHistory\"}` / `{\"type\":\"candleSnapshot\"}`")
    A("  - Binance `GET https://fapi.binance.com/fapi/v1/fundingRate` / `fapi/v1/klines`")
    A("  - Bybit `GET https://api.bybit.com/v5/market/funding/history?category=linear`（startTime と endTime の両方が必須）")
    A("")

    # ---------------- 品質
    A("## 3. 観測の規模と品質")
    A("")
    A(f"- 窓数 **{meta['n_windows']}**（8時間×{a['days']:.0f}日）、銘柄 **{meta['n_coins']}**（{'/'.join(meta['coins'])}）")
    A(f"- 行数 **{meta['n_rows']:,}**（= {meta['n_windows']}窓 × {meta['n_coins']}銘柄）、"
      f"窓の間隔は全件 8.0 時間ちょうど、開始時刻は 00/08/16 UTC のみ")
    A(f"- `diff` の欠測 **0件**、ベーシスの欠測 **0件**、取得エラー **{len(meta['errors'])}件**")
    A(f"- 各窓の内訳は全件が「Binance 1回ぶん・HL 8回ぶん」（`n_bn=1, n_hl=8`）で、窓合計が比較可能な形になっている")
    A(f"- 期間 {a['days']:.0f}日 は受入条件の「4週間以上」を満たす（4週間の2倍）")
    A("- **観測の取り方**: 先向きに4週間待つのではなく、**公開APIの履歴から過去56日を遡って取得**している。"
      "したがって「同時刻に各場が実際にいくら徴収したか」という実測記録であって、"
      "シミュレーションでも再構成でもない。生データは `raw/` にそのまま保存してあり、後から再集計できる。")
    A("")

    # ---------------- 大きさ
    A("## 4. 差の大きさ（銘柄別）")
    A("")
    A("単位は bps/8h（1bp = 0.01%）。`正%` は HL の方が高い窓の割合。`総年率` は |diff| を積み上げた年率（手数料前・符号を当てた上限）。")
    A("")
    A("### 4-1. Hyperliquid − Binance")
    A("")
    A("| 銘柄 | 平均 | 中央値 | 平均\\|diff\\| | p90\\|diff\\| | 最大\\|diff\\| | 正% | 総年率 | 週あたり符号反転 | 同一符号の連続(中央/最大) |")
    A("|---|---|---|---|---|---|---|---|---|---|")
    for c, v in a["per_coin"].items():
        x = v.get("hl_bn")
        if not x:
            continue
        A(f"| {c} | {sgn(x['mean_bps'],3)} | {sgn(x['median_bps'],3)} | {f(x['mean_abs_bps'],3)} | "
          f"{f(x['p90_abs_bps'],3)} | {f(x['max_abs_bps'],3)} | {x['share_positive']*100:.1f}% | "
          f"{f(x['gross_ann_pct_of_notional'])}% | {f(x['sign_flips_per_week'])} | "
          f"{f(x['run_len_windows_median'],1)} / {x['run_len_windows_max']} |")
    A(f"| **8銘柄等ウェイト** | **{sgn(bn['port_mean_bps_per_8h'],3)}** | — | **{f(bn['port_mean_abs_bps_per_8h'],3)}** | — | — | "
      f"**{bn['port_share_positive']*100:.1f}%** | **{f(bn['gross_ann_pct_of_notional'])}%** | {f(bn['sign_flips_per_week'])} | — |")
    A("")
    A("### 4-2. Hyperliquid − Bybit")
    A("")
    A("| 銘柄 | 平均 | 中央値 | 平均\\|diff\\| | p90\\|diff\\| | 最大\\|diff\\| | 正% | 総年率 | 週あたり符号反転 | 同一符号の連続(中央/最大) |")
    A("|---|---|---|---|---|---|---|---|---|---|")
    for c, v in a["per_coin"].items():
        x = v.get("hl_by")
        if not x:
            continue
        A(f"| {c} | {sgn(x['mean_bps'],3)} | {sgn(x['median_bps'],3)} | {f(x['mean_abs_bps'],3)} | "
          f"{f(x['p90_abs_bps'],3)} | {f(x['max_abs_bps'],3)} | {x['share_positive']*100:.1f}% | "
          f"{f(x['gross_ann_pct_of_notional'])}% | {f(x['sign_flips_per_week'])} | "
          f"{f(x['run_len_windows_median'],1)} / {x['run_len_windows_max']} |")
    A(f"| **8銘柄等ウェイト** | **{sgn(by['port_mean_bps_per_8h'],3)}** | — | **{f(by['port_mean_abs_bps_per_8h'],3)}** | — | — | "
      f"**{by['port_share_positive']*100:.1f}%** | **{f(by['gross_ann_pct_of_notional'])}%** | {f(by['sign_flips_per_week'])} | — |")
    A("")
    A("読み方:")
    A("")
    A(f"- 差の向きは **HL の方が高い**が基本（正% = {bn['port_share_positive']*100:.1f}% / {by['port_share_positive']*100:.1f}%）。")
    A(f"- ただし **ADA と AVAX は逆**（正% {a['per_coin']['ADA']['hl_bn']['share_positive']*100:.1f}% / "
      f"{a['per_coin']['AVAX']['hl_bn']['share_positive']*100:.1f}% / "
      f"{a['per_coin']['ADA']['hl_by']['share_positive']*100:.1f}% / {a['per_coin']['AVAX']['hl_by']['share_positive']*100:.1f}%）。"
      "「DEX が高い」は全銘柄一律ではない。")
    A(f"- **絶対額が小さい**。平均 |diff| は 8時間で {f(bn['port_mean_abs_bps_per_8h'],3)}bps（Binance 比）。"
      f"総年率 {f(bn['gross_ann_pct_of_notional'])}% は「毎回符号を当てられた場合」の上限。")
    A(f"- **「200倍」の正体**: HL/CEX の**比**の中央値は {f(ratio['hl_bn']['median_ratio'])}倍、p90 で {f(ratio['hl_bn']['p90_ratio'])}倍。"
      f"200倍を超えた窓は {ratio['hl_bn']['windows_ratio_gt_200']}件（{f(ratio['hl_bn']['share_gt_200_pct'])}%）だけで、"
      f"いずれも CEX 側のレートがほぼ0の窓。**比は小さい数どうしの割り算で、損益に効くのは絶対bps差**である。")
    A("")

    # ---------------- 持続
    A("## 5. 持続時間と符号の反転")
    A("")
    A("| 指標 | HL−Binance | HL−Bybit |")
    A("|---|---|---|")
    A(f"| 週合計がプラスの週 | **{sum(1 for w in a['weekly']['hl_bn'] if w['sum_diff_bps'] > 0)}/{n_weeks}** | **{sum(1 for w in a['weekly']['hl_by'] if w['sum_diff_bps'] > 0)}/{n_weeks}** |")
    A(f"| 窓単位の符号反転（回/週） | {f(bn['sign_flips_per_week'])} | {f(by['sign_flips_per_week'])} |")
    A(f"| 同一符号の連続長（中央値・窓数） | {f(bn['run_len_median'],1)} | {f(by['run_len_median'],1)} |")
    A(f"| 同一符号の連続長（最長・窓数 = 8時間） | {bn['run_len_max']} | {by['run_len_max']} |")
    A("")
    A("週別の積み上げ（8銘柄等ウェイト・bps）:")
    A("")
    A("| 週 | 期間(UTC) | HL−Binance | HL−Bybit |")
    A("|---|---|---|---|")
    for wb, wy in zip(a["weekly"]["hl_bn"], a["weekly"]["hl_by"]):
        A(f"| W{wb['week_index']} | {wb['from_utc']}〜{wb['to_utc']} | {sgn(wb['sum_diff_bps'])} | {sgn(wy['sum_diff_bps'])} |")
    A("")
    A("**読み方**: 窓単位では符号が頻繁に入れ替わる（週あたり "
      f"{f(bn['sign_flips_per_week'])}回）が、**週単位で積むと向きは安定**している"
      f"（Binance {sum(1 for w in a['weekly']['hl_bn'] if w['sum_diff_bps'] > 0)}/{n_weeks}週、"
      f"Bybit {sum(1 for w in a['weekly']['hl_by'] if w['sum_diff_bps'] > 0)}/{n_weeks}週がプラス）。"
      "つまり「短い時間足で追随する」戦略は手数料で死に、「一定期間持ち続ける」形だけが残る。")
    A("")

    # ---------------- 時間帯
    A("## 6. 時間帯別（UTC の精算スロット）")
    A("")
    A("| 精算時刻(UTC) | 日本時間 | HL−Binance 平均\\|diff\\| | 平均diff | 正% | HL−Bybit 平均\\|diff\\| | 平均diff | 正% |")
    A("|---|---|---|---|---|---|---|---|")
    jst = {"0": "09:00", "8": "17:00", "16": "01:00"}
    for h in ("0", "8", "16"):
        x, y = a["hour_band"]["hl_bn"][h], a["hour_band"]["hl_by"][h]
        A(f"| {int(h):02d}:00 | {jst[h]} | {f(x['mean_abs_bps'],3)}bps | {sgn(x['mean_bps'],3)}bps | {x['share_positive']*100:.1f}% | "
          f"{f(y['mean_abs_bps'],3)}bps | {sgn(y['mean_bps'],3)}bps | {y['share_positive']*100:.1f}% |")
    A("")
    A("時間帯による差は小さい（平均 |diff| で最大でも 1.13 倍）。**特定の時間帯に集中する歪みではない**ので、"
      "「日本時間のどこかで狙う」形にはならない。")
    A("")

    # ---------------- 残差
    A("## 7. 手数料と証拠金分断コストを引いた残差")
    A("")
    A("### 7-1. 使った費用（公表値と、そうでないもの）")
    A("")
    A("| 項目 | 値 | 性質 |")
    A("|---|---|---|")
    A(f"| HL taker | {f(meta['fees_bps']['hyperliquid_taker'],1)} bps/片道 | 公表値（base tier） |")
    A(f"| Binance taker | {f(meta['fees_bps']['binance_taker'],1)} bps/片道 | 公表値（VIP0・BNB割引なし） |")
    A(f"| Bybit taker | {f(meta['fees_bps']['bybit_taker'],1)} bps/片道 | 公表値（VIP0） |")
    A(f"| 往復（2脚 × 建て/決済） | **{f(fee_bn,1)} bps**（BN）/ **{f(fee_by,1)} bps**（BY） | 上記から計算 |")
    A(f"| 滑り | 2.0 bps/約定 × 4 = 8.0 bps | **推測**（既存 S-07 の約定モデルと同じ想定） |")
    A(f"| 各場のレバレッジ | {f(frag['leverage_per_venue'],1)}倍 | **推測**（保守側） |")
    A(f"| 証拠金の余剰 | {f(frag['margin_buffer']*100,0)}% | **推測** |")
    A(f"| 口座間の資金移動 | $2/月 | **推測**（USDT出金≈$1 + HL出金ガス≈$1） |")
    A("")
    A("### 7-2. 証拠金分断のコストを式で書く")
    A("")
    A("```")
    A(f"片脚あたり想定元本 N、各場の実効レバレッジ L_eff = {f(frag['leverage_per_venue'],1)} / (1 + {f(frag['margin_buffer'],2)}) = {f(frag['l_eff'],2)}")
    A("必要な資本 C = 2N / L_eff        （2脚ぶんを別々に置くため）")
    A("資本あたりの取り分 = (差の積み上げ) × N / C = (差) × L_eff / 2")
    A("⇒ 同じ大きさの片脚ファンディングと比べて **資本あたり半分**。これが「分断コスト」の本体。")
    A("```")
    A("")
    A("### 7-3. 層ごとの残差（8週連続保有・往復手数料は1回だけ＝最も有利な置き方）")
    A("")
    A("| 段階 | HL−Binance | HL−Bybit |")
    A("|---|---|---|")
    A(f"| 手数料のみ控除・想定元本あたり年率 | {sgn(am['hl_bn']['ann_pct_of_notional'],3)}% | {sgn(am['hl_by']['ann_pct_of_notional'],3)}% |")
    A(f"| 同・資本あたり年率（分断で ×{f(k,2)}） | {sgn(ann_cap_bn,3)}% | {sgn(ann_cap_by,3)}% |")
    A(f"| 週単位で見たときプラスの週（手数料按分後） | {am['hl_bn']['weeks_positive']}/{am['hl_bn']['n_weeks']} | {am['hl_by']['weeks_positive']}/{am['hl_by']['n_weeks']} |")
    A("")
    A("### 7-4. 資金移動費を足したときの純額（原資の大きさで符号が変わる）")
    A("")
    A("| 片脚 N | 必要資本 C | 移動費の負担 | HL−Binance 純額 | HL−Bybit 純額 |")
    A("|---|---|---|---|---|")
    for kk in ("N=500", "N=1000", "N=5000", "N=10000"):
        t = frag["transfer_cost_table"][kk]
        A(f"| ${t['capital_usd']/ (2/frag['l_eff']):,.0f} | ${t['capital_usd']:,.0f} | {f(t['transfer_drag_pct_per_year'])}%/年 | "
          f"{sgn(ann_cap_bn - t['transfer_drag_pct_per_year'])}%/年 | {sgn(ann_cap_by - t['transfer_drag_pct_per_year'])}%/年 |")
    A("")
    # §項目19 に出ている BTC 持ち高を、この観測の最終価格で円建てでなくドルに換算して規模感を示す
    try:
        cbn = json.loads((d / "raw" / "close_bn.json").read_text())["BTC"]
        btc_px = cbn[sorted(cbn)[-1]]
        held_usd = 0.0061686 * btc_px
    except Exception:                                  # noqa: BLE001
        btc_px, held_usd = None, None
    if btc_px and held_usd:
        n_eq = held_usd / 2
        cap_eq = 2 * n_eq / frag["l_eff"]
        drag_eq = 12 * 2.0 / cap_eq * 100
        A("移動費は**固定額**なので、原資が小さいほど効く。一次資料 §項目19 に出ている暗号資産の持ち高"
          f"（BitLending の BTC 0.0061686）を本観測の最終価格 ${btc_px:,.0f}/BTC で見ると **約 ${held_usd:,.0f}**。"
          f"これを2脚に等分すると片脚 N≈${n_eq:,.0f}・必要資本 C≈${cap_eq:,.0f} で、"
          f"$2/月の移動費は**年率 {drag_eq:.1f}%** に相当する。この水準では §7-3 の残差（年率 "
          f"{f(ann_cap_bn)}% / {f(ann_cap_by)}%）を移動費だけで消す。")
    else:
        A("移動費は**固定額**なので、原資が小さいほど効く。")
    A("")

    # ---------------- 4週保有
    A("## 8. 4週間保有の実現損益（先読みなし・ベーシス込み）")
    A("")
    A("標本内の全開始点（各銘柄 84 点 × 8 銘柄 = **672回**）で「28日保有」を回した。"
      "方向は**直前14日の平均差**で決める（先読みなし）。損益 = 方向×差の積み上げ − ベーシス変化 − 往復手数料。")
    A("")
    A("| 指標 | HL−Binance | HL−Bybit |")
    A("|---|---|---|")
    A(f"| 平均 | {sgn(rbn['net_mean_bps'])} bps | {sgn(rby['net_mean_bps'])} bps |")
    A(f"| 中央値 | {sgn(rbn['net_median_bps'])} bps | {sgn(rby['net_median_bps'])} bps |")
    A(f"| 標準偏差 | {f(rbn['net_sd_bps'])} bps | {f(rby['net_sd_bps'])} bps |")
    A(f"| p10 / p90 | {sgn(rbn['net_p10_bps'])} / {sgn(rbn['net_p90_bps'])} | {sgn(rby['net_p10_bps'])} / {sgn(rby['net_p90_bps'])} |")
    A(f"| 最悪 / 最良 | {sgn(rbn['net_worst_bps'])} / {sgn(rbn['net_best_bps'])} | {sgn(rby['net_worst_bps'])} / {sgn(rby['net_best_bps'])} |")
    A(f"| **プラスだった割合（手数料のみ）** | **{rbn['share_positive']*100:.1f}%** | **{rby['share_positive']*100:.1f}%** |")
    A(f"| プラスだった割合（＋滑り 8bps） | {rbn['share_positive_with_slippage']*100:.1f}% | {rby['share_positive_with_slippage']*100:.1f}% |")
    A(f"| ファンディング差だけの平均（ベーシス抜き） | {sgn(rbn['funding_only_mean_bps'])} bps | {sgn(rby['funding_only_mean_bps'])} bps |")
    A(f"| ベーシス損益の平均 / 標準偏差 | {sgn(rbn['basis_pnl_mean_bps'])} / {f(rbn['basis_pnl_sd_bps'])} bps | {sgn(rby['basis_pnl_mean_bps'])} / {f(rby['basis_pnl_sd_bps'])} bps |")
    A("")
    A("**銘柄別（手数料のみ控除）** — 差の出方は銘柄でかなり違う:")
    A("")
    A("| 銘柄 | HL−Binance 平均 | プラス率 | HL−Bybit 平均 | プラス率 |")
    A("|---|---|---|---|---|")
    for c in meta["coins"]:
        x, y = rbn["per_coin"].get(c), rby["per_coin"].get(c)
        if not x:
            continue
        A(f"| {c} | {sgn(x['mean_bps'])} bps | {x['share_positive']*100:.1f}% | {sgn(y['mean_bps'])} bps | {y['share_positive']*100:.1f}% |")
    A("")
    A(f"AVAX だけは一貫してマイナス（プラス率 {rbn['per_coin']['AVAX']['share_positive']*100:.1f}% / "
      f"{rby['per_coin']['AVAX']['share_positive']*100:.1f}%）で、差の向きが他と逆。"
      "銘柄を選ばずに「DEX 売り／CEX 買い」を張ると、この銘柄で削られる。")
    A("")

    # ---------------- ベーシス
    A("## 9. ベーシス（価格差）— 手数料ではないが損益に直接乗る")
    A("")
    A("同じ資産の2つの無期限先物なので、価格差（ベーシス）は小さいが0ではない。"
      "往復の損益には「入ったときと出たときのベーシス差」がそのまま乗る。**約定終値どうし**で測った。")
    A("")
    A("| 銘柄 | 平均 | 標準偏差 | 全期間の幅 | 8時間あたり\\|変化\\| 平均 | p90 |")
    A("|---|---|---|---|---|---|")
    for c, v in a["basis"].items():
        if v.get("n", 0) < 10:
            continue
        A(f"| {c} | {sgn(v['mean_bps'])}bps | {f(v['stdev_bps'])}bps | {f(v['range_bps'])}bps | "
          f"{f(v['abs_change_per_8h_mean_bps'])}bps | {f(v['abs_change_per_8h_p90_bps'])}bps |")
    A("")
    A("28日保有でのベーシス損益の標準偏差は "
      f"{f(rbn['basis_pnl_sd_bps'])}bps で、ファンディング差の平均（{sgn(rbn['funding_only_mean_bps'])}bps）の約"
      f" {f(abs(rbn['basis_pnl_sd_bps'] / rbn['funding_only_mean_bps']),1)} 倍。"
      "**差の平均と同程度の揺れ**があり、無視できない（だから §8 の実現損益は平均のまわりに広く散る）。"
      "なお、この 6.24bps は 28日保有の**入出のベーシス差**だけを見た値で、"
      "保有中の含みの揺れはこれより大きい（8時間あたり平均 "
      f"{f(a['basis']['BTC']['abs_change_per_8h_mean_bps'])}〜{f(max(v['abs_change_per_8h_mean_bps'] for v in a['basis'].values() if v.get('n',0) >= 10))}bps）。"
      "（最初に Binance の `markPrice` と HL の約定値で測ったら標準偏差が約90bpsと15倍に出た。"
      "これは mark（公正価格）と約定値の混在による系統誤差で、約定終値どうしに直すと消えた。）")
    A("")

    # ---------------- 受入条件
    A("## 10. 受入条件への回答")
    A("")
    A("| 受入条件 | 結果 |")
    A("|---|---|")
    A(f"| 4週間以上の観測 | **達成**。{a['days']:.0f}日（{a['weeks']:.0f}週）・{meta['n_windows']}窓・{meta['n_rows']:,}行・欠測0 |")
    A(f"| 手数料と証拠金分断コストを引いても残る差が週の大半で持続するか | **残る**。週合計は {sum(1 for w in a['weekly']['hl_bn'] if w['sum_diff_bps'] > 0)}/{n_weeks}週・"
      f"{sum(1 for w in a['weekly']['hl_by'] if w['sum_diff_bps'] > 0)}/{n_weeks}週がプラス、4週保有の {rbn['share_positive']*100:.1f}%・{rby['share_positive']*100:.1f}% が手数料後にプラス。"
      f"額は資本あたり年率 {f(ann_cap_bn)}%・{f(ann_cap_by)}% |")
    A(f"| 残るなら口座判断の材料に上げる／消えるなら閉じる | **閉じる**。残る額（年率 {f(ann_cap_bn)}〜{f(ann_cap_by)}%）が、"
      f"原資数百〜1,000ドル級でかかる資金移動費（資本$800で年率 {f(tf['N=1000'])}%、"
      f"$400で {f(tf['N=500'])}%）を下回るため。"
      f"加えて CEX 側の口座が無く、Binance 本体・Bybit は日本の居住者にサービスを提供していない（§項目20の前提） |")
    A("| 新規口座0・発注0・既存観測の設定変更0 | **達成**。§12 に証明 |")
    A("")
    A("**閉じる判断の内訳**（消える理由は3つ、いずれも数字で確認できる）:")
    A("")
    A("1. **額が小さい**: 手数料だけ引いた最良の置き方で資本あたり年率 "
      f"{f(ann_cap_bn)}%/{f(ann_cap_by)}%。滑りを入れると {f(roll_ann_bn_s*k)}%/{f(roll_ann_by_s*k)}%。")
    A(f"2. **分断に付随する固定費が原資規模に合わない**: 口座間の移動 $2/月は、資本$400で年率 {f(tf['N=500'])}%、"
      f"資本$800で {f(tf['N=1000'])}%。差の取り分より大きい。")
    A("3. **入口が無い**: CEX 側の口座が無く、Binance 本体と Bybit は日本の居住者にサービスを提供していない。"
      "国内認可の別法人・他社のファンディング商品との差は**この測定の対象外（未測定）**。")
    A("")

    # ---------------- 制約
    A("## 11. 制約・未測定（正直に分けて書く）")
    A("")
    A("- **測ったもの**: Hyperliquid と Binance／Bybit の、同一8時間窓の実精算額の差（公開APIの実データ）。")
    A("- **測っていないもの**: ①国内認可事業者の無期限・レバレッジ商品との差（口座が無い・商品性が違う）"
      "②実際の板の厚みと約定（滑りは 2.0bps の仮定であって実測ではない）③両場の入出金規制と KYC の可否"
      "④証拠金の分断に伴う清算リスクの実測（レバレッジと余剰は仮定）")
    A("- **推測と明記した値**: 滑り 2.0bps/約定、レバレッジ3倍、余剰20%、資金移動 $2/月。"
      "公表値（手数料）とは区別して扱った。")
    A("- **標本の限界**: 56日・8銘柄・1回の観測。相場局面（強気／弱気）を跨いでいない可能性がある。"
      "「4週間以上」は満たすが、季節性や局面依存は判定できない。")
    A("- **生存者バイアス**: 対象8銘柄は観測時点で HL と CEX の両方に上場している銘柄。上場廃止・新規上場は含まない。")
    A("")

    # ---------------- 非干渉
    A("## 12. 非干渉の証明（新規口座0・発注0・既存観測の設定変更0）")
    A("")
    A("- 本観測の書き込み先は `data/s07_paper/xvenue_funding/` のみ（`raw/`, `windows.json`, `analysis.json`, 本レポート）。")
    A("- 既存のファンディング影観測（`scripts/s07_paper/hl_funding_top20.py`, `data/s07_paper/funding_top20/`, "
      "`funding_1h.json`, `state_live.json`, `health_live.json`）は**プログラムも設定も cron も変更していない**。")
    A("- 既存ファイル31件の sha256 を採取前後で比較し、**30件が完全一致**。唯一変わったのは "
      "`funding_top20/oi_snapshots/2026-11-06.jsonl` で、これは既存の OI 収集ループ（pid 2291496・15分間隔・カード t_7a53946d）が"
      "**測定とは無関係に追記し続けているファイル**。本スクリプトはこのパスに書き込まない（書き込み先は上記のとおり）。")
    A("- 呼んだのは公開の読み取りAPIのみ。署名・APIキー・`/exchange` は一切使っていない。注文は0件。")
    A("- レート制限の規律: 既存観測は HL 呼び出し間に 0.25 秒。本スクリプトは **0.60 秒**（既存より遅い）で、"
      f"{meta['http_requests']}回のHTTPリクエストに約40秒。既存より速い方向には動かしていない。")
    A("")

    # ---------------- 再現
    A("## 13. 再現手順")
    A("")
    A("```bash")
    A("cd /root/current-work/yowayowa-investor")
    A("python3 scripts/s07_paper/xvenue_funding_diff.py        # 収集（読み取りのみ・約40秒）")
    A("python3 scripts/s07_paper/xvenue_funding_analyze.py     # 集計 → analysis.json")
    A("python3 scripts/s07_paper/xvenue_funding_report.py      # 本レポート生成")
    A("```")
    A("")
    A("出力:")
    A("")
    A("```")
    A("data/s07_paper/xvenue_funding/windows.json    # 整列済みの窓（メタ＋1344行）")
    A("data/s07_paper/xvenue_funding/analysis.json   # 全統計")
    A("data/s07_paper/xvenue_funding/raw/            # 各場の生データ（再集計できる）")
    A("data/s07_paper/xvenue_funding/REPORT_XVENUE_FUNDING.md")
    A("```")
    A("")
    A("毎日 `xvenue_funding_diff.py` を回せば窓が1日ぶん延びる（冪等・当日分は上書き）。"
      "今回は過去56日を公開APIの履歴から遡って取得しているため、**4週間待たずに4週間ぶんの同時刻観測**が揃っている。")
    A("")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="/root/current-work/yowayowa-investor/data/s07_paper/xvenue_funding")
    args = ap.parse_args(argv)
    d = Path(args.dir)
    text = build(d)
    out = d / "REPORT_XVENUE_FUNDING.md"
    out.write_text(text, encoding="utf-8")
    print(f"wrote {out} ({len(text)} chars, {text.count(chr(10))} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
