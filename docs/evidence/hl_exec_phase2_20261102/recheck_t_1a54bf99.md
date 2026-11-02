# 第2段プローブの引き取り検収 — HEAD版を再実行してアーカイブ証跡と照合（t_1a54bf99）

- 実施: 2026-11-02 (JST) / VPS / 読み取りのみ（`POST /info`）
- 対象: `scripts/hl_exec_phase2/phase2_read_probe.py`（HEAD `37e4319` で tracked・532行）
  - worktree 実ファイル sha256 = `2bcea5d45a6aebe8be85a8ad8fa3c399ea5595bc92e53020f6c5a5326f648114`
  - `git cat-file -p HEAD:scripts/hl_exec_phase2/phase2_read_probe.py | sha256sum` と**一致**
    → **実行した版は tracked 版そのもの**（worktree と歴史の食い違いなし）
- 再実行コマンド: `.venv/bin/python scripts/hl_exec_phase2/phase2_read_probe.py --evidence-dir <scratch>` → **exit 0**
- 比較対象: 同一ディレクトリの `summary.json`（2026-11-02T11:55:11+09:00 生成・プローブがまだ未 tracked だった時点のアーカイブ）

## 判定

**意味で一致。** 差分は次の2種類だけ:

1. **市況**（価格・HYPE 板の一時的な空）— 実装差ではない。
2. **裁定 t_e91f2dbc による意図した比率cap適用**（`isolated_margin_per_position` の `limit` が 20.0 → 10.0）。
   HEAD 版は `equity_usdc` を渡す新 API に追随しており、この差は「追随が効いている証拠」で、`ok` 判定は不変。

読み取り専用契約は不変: `signature_calls=0` / `exchange_calls=0` / `key_reads=0` / endpoint はテストネット `/info` のみ / 全 9 呼出しが HTTP 200。

## 再実行 stdout

```json
{
  "statuses": [
    200,
    200,
    200,
    200,
    200,
    200,
    200,
    200,
    200
  ],
  "account_value": 100.0,
  "positions": 0,
  "btc_ask": 78078.0,
  "btc_szDecimals": 5,
  "btc_asset_index": 3,
  "max_size_allowed": 0.00038,
  "design_margin_cap": 10.0,
  "evidence_dir": "<scratch>/recheck2"
}
```

## 再実行 stderr

```
warn: HYPE book unavailable: empty L2 book
```

（HYPE の板が一時的に空だったため `rounding.per_symbol_price_rounding` から HYPE の1行だけ落ちた。下の [1] の差分はこれが原因で、コード差ではない。）

## 照合結果

### [1] 生成物のキー構造（葉パスの集合）差分

旧 = アーカイブ summary.json / 新 = HEAD 版プローブ再実行

```
275,282d274
< rounding.per_symbol_price_rounding.4.coin
< rounding.per_symbol_price_rounding.4.max_price_decimals
< rounding.per_symbol_price_rounding.4.mid_px
< rounding.per_symbol_price_rounding.4.price_is_valid
< rounding.per_symbol_price_rounding.4.rounded_px
< rounding.per_symbol_price_rounding.4.rounded_px_wire
< rounding.per_symbol_price_rounding.4.szDecimals
< rounding.per_symbol_price_rounding.4.truncated
```

→ 落ちたのは `per_symbol_price_rounding[4]`（= HYPE）の1要素のみ。他の葉パスは完全一致。

### [2] 読み取り専用契約（署名 / 送信 / 鍵読み）

```
旧 {"signature_calls":0,"exchange_calls":0,"key_reads":0,"endpoint":"https://api.hyperliquid-testnet.xyz/info","account_address":"0xF6cB1079F73cA0163E2EfF24eFbB651f99E2Dd82"}
新 {"signature_calls":0,"exchange_calls":0,"key_reads":0,"endpoint":"https://api.hyperliquid-testnet.xyz/info","account_address":"0xF6cB1079F73cA0163E2EfF24eFbB651f99E2Dd82"}
```

### [3] `/info` 呼出し（HTTP ステータス・リクエスト種別）

```
旧 [200,200,200,200,200,200,200,200,200]
新 [200,200,200,200,200,200,200,200,200]
旧 ["clearinghouseState","meta","l2Book","meta","l2Book","l2Book","l2Book","l2Book","l2Book"]
新 ["clearinghouseState","meta","l2Book","meta","l2Book","l2Book","l2Book","l2Book","l2Book"]
```

（全て `/info`。`/exchange` への送信は 0 件）

### [4] 境界ゲート（rule / ok / limit）

旧:

```
{"rule":"isolated_margin_per_position","ok":true,"limit":20.0}
{"rule":"max_concurrent_positions","ok":true,"limit":1.0}
{"rule":"position_notional_cap","ok":true,"limit":60.0}
{"rule":"min_notional","ok":true,"limit":10.0}
{"rule":"leverage_cap","ok":true,"limit":3.0}
{"rule":"margin_preflight","ok":true,"limit":15.0}
```

新:

```
{"rule":"isolated_margin_per_position","ok":true,"limit":10.0}
{"rule":"max_concurrent_positions","ok":true,"limit":1.0}
{"rule":"position_notional_cap","ok":true,"limit":60.0}
{"rule":"min_notional","ok":true,"limit":10.0}
{"rule":"leverage_cap","ok":true,"limit":3.0}
{"rule":"margin_preflight","ok":true,"limit":15.0}
```

→ 差は `isolated_margin_per_position.limit` の 20.0 → 10.0 のみ（裁定 t_e91f2dbc の比率10%内部適用）。`ok` は全ルール true で不変。

### [5] 安定数値（価格に依存しない定数由来）— 旧/新で完全一致

```
{"design_margin_cap_usdc":10.0,"design_notional_cap_usdc":30.0,"notional_from_leverage_usdc":30.0,"min_tradable_size":0.00013,"stop_levels":{"daily_loss_stop_threshold_usdc":2.0,"daily_loss_stop_triggered_at":-2.0,"daily_stop_hit_at_threshold":true,"daily_stop_hit_just_before":false,"drawdown_stop_threshold_usdc":4.0,"drawdown_hit_at_threshold":true,"drawdown_hit_just_before":false,"liquidation_distance_ok_0_15":true,"liquidation_distance_ok_0_1499":false},"module_absolute_boundary":{"module_margin_cap_usdc":20.0,"at_cap_ok":true,"at_cap_usdc":20.01,"just_over_cap_ok":false,"at_cap_share_of_real_equity":0.2}}
```

（旧・新で同一。リスク数値定数 20.0 / 60.0 / 3.0 / 2% / 4% / 5.0 / 10% は不変）

### [6] 価格依存値（市況差のため一致しない）

```
旧 {"btc_ask":77541.0,"btc_mid":77526.0}
新 {"btc_ask":78078.0,"btc_mid":78063.5}
```

### [7] 切り捨て・境界の補助値（価格連動）

```
旧 min_size_allowed=0.00012  min_tradable_notional_usdc=10.08033
新 min_size_allowed=0.00012  min_tradable_notional_usdc=10.15014
```

（サイズ刻みは同一。名目額は ask が 77541 → 78078 に動いたぶんだけ変化）

## 付録: プローブ本文の 9607e14 → 37e4319 差分は文言のみ

`git diff 9607e14 37e4319 -- scripts/hl_exec_phase2/phase2_read_probe.py`

- 変更行: **-7 行 / +7 行**（すべてコメントと Markdown 文字列。実効コード・出力構造・数値定数は不変）
- 差分に現れる数値トークンは `20.0`（設計上の絶対上限の記述）と `100.0`（実残高の記述）のみ
