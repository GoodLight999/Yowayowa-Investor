# 第2段 実データ検証サマリ（自動生成）

- 生成時刻: 2026-11-02T11:55:11+09:00
- endpoint: `https://api.hyperliquid-testnet.xyz/info`（POST /info のみ・/exchange 呼出し 0 件）
- 口座: `0xF6cB1079F73cA0163E2EfF24eFbB651f99E2Dd82`

## 1. 読み取り（HTTPステータス）

| call | type | HTTP | bytes | ms |
| --- | --- | --- | --- | --- |
| 1 | clearinghouseState | 200 | 319 | 135.1 |
| 2 | meta | 200 | 16879 | 162.9 |
| 3 | l2Book | 200 | 1547 | 86.8 |
| 4 | meta | 200 | 16879 | 97.1 |
| 5 | l2Book | 200 | 1547 | 87.9 |
| 6 | l2Book | 200 | 1468 | 85.9 |
| 7 | l2Book | 200 | 1379 | 86.2 |
| 8 | l2Book | 200 | 1563 | 84.6 |
| 9 | l2Book | 200 | 796 | 83.6 |

- accountValue = **100.0** USDC / withdrawable = **100.0** USDC / 建玉数 = **0**
- BTC ask = 77541.0 / mid = 77526.0

## 2. risk.py の実データ判定

- 実残高 100.0 USDC → 設計の隔離証拠金上限 = min(20.0, 10%×残高) = **10.0** USDC
- そのレバレッジ上限 3.0 倍 → 名目上限 = min(60.0, 3×10.0) = **30.0** USDC
- 許可される最大サイズ = **0.00038 BTC**（名目 29.46558 USDC）
- 1刻み上乗せ = 0.00039 BTC（名目 30.24099 USDC）

| ケース | ルール | ok | value | limit |
| --- | --- | --- | --- | --- |
| 最大サイズ | isolated_margin_per_position | True | 10.0 | 20.0 |
| 最大サイズ | max_concurrent_positions | True | 0.0 | 1.0 |
| 最大サイズ | position_notional_cap | True | 29.46558 | 60.0 |
| 最大サイズ | min_notional | True | 29.46558 | 10.0 |
| 最大サイズ | leverage_cap | True | 2.946558 | 3.0 |
| 最大サイズ | margin_preflight | True | 100.0 | 15.0 |
| 1刻み超過 | isolated_margin_per_position | True | 10.0 | 20.0 |
| 1刻み超過 | max_concurrent_positions | True | 0.0 | 1.0 |
| 1刻み超過 | position_notional_cap | True | 30.240990000000004 | 60.0 |
| 1刻み超過 | min_notional | True | 30.240990000000004 | 10.0 |
| 1刻み超過 | leverage_cap | False | 3.0240990000000005 | 3.0 |
| 1刻み超過 | margin_preflight | True | 100.0 | 15.0 |
| 最小サイズ割れ | isolated_margin_per_position | True | 10.0 | 20.0 |
| 最小サイズ割れ | max_concurrent_positions | True | 0.0 | 1.0 |
| 最小サイズ割れ | position_notional_cap | True | 8.52951 | 60.0 |
| 最小サイズ割れ | min_notional | False | 8.52951 | 10.0 |
| 最小サイズ割れ | leverage_cap | True | 0.852951 | 3.0 |
| 最小サイズ割れ | margin_preflight | True | 100.0 | 15.0 |
| 2建玉目 | isolated_margin_per_position | True | 10.0 | 20.0 |
| 2建玉目 | max_concurrent_positions | False | 2.0 | 1.0 |
| 2建玉目 | position_notional_cap | True | 29.46558 | 60.0 |
| 2建玉目 | min_notional | True | 29.46558 | 10.0 |
| 2建玉目 | leverage_cap | True | 2.946558 | 3.0 |
| 2建玉目 | margin_preflight | True | 100.0 | 15.0 |

- 1刻み超過で最初に落ちるルール: leverage_cap
- 2建玉目で落ちるルール: max_concurrent_positions
- 最小発注額 10.0 USDC を満たす最小サイズ: **0.00013 BTC**（名目 10.08033 USDC）
- 実データでの最小サイズ 0.00012 BTC は名目 8.52951 USDC で **min_notional により拒否**（境界が効いている証明）

### 参考: risk.py モジュール自身の絶対上限（20.0 USDC）の境界

- margin = 20.0 USDC → check_isolated_margin = **True**（上限ちょうどは許可）
- margin = 20.01 USDC → **False**（超過は拒否）
- 注意: モジュールの cap は絶対額 20.0 USDC（設計の200 USDC口座の10%）。実残高 100.0 USDC では 20.0 USDC = 残高の 20.0%。
  比率側（10%）は呼出側の責務のため、本検証では cap = min(20.0, 10%×実残高) = 10.0 USDC を明示適用した。

## 3. orders.py の丸め（実 szDecimals）

| coin | szDecimals | 価格小数上限 | mid | 丸め後 | wire | 有効 | 切捨て |
| --- | --- | --- | --- | --- | --- | --- | --- |
| BTC | 5 | 1 | 77526.0 | 77526.0 | `77526` | True | True |
| ETH | 4 | 2 | 2304.75 | 2304.7 | `2304.7` | True | True |
| SOL | 2 | 4 | 97.483 | 97.483 | `97.483` | True | True |
| DOGE | 0 | 6 | 0.075521 | 0.075521 | `0.075521` | True | True |
| HYPE | 2 | 4 | 82.056 | 82.056 | `82.056` | True | True |

| 要求サイズ | 丸め後 | 有効 |
| --- | --- | --- |
| 0.000789012 | 0.00078 | True |
| 0.0012345 | 0.00123 | True |
| 0.000999949 | 0.00099 | True |
| 0.3333333333333333 | 0.33333 | True |

公式 tick 契約の実例（実 szDecimals ごと・すべて切捨て）:

| coin | szDecimals | 入力 | 丸め後 | wire | 有効な価格か | 切捨て |
| --- | --- | --- | --- | --- | --- | --- |
| BTC | 5 | 78012.1 | 78012.0 | `78012` | True | True |
| BTC | 5 | 12345.6 | 12345.0 | `12345` | True | True |
| BTC | 5 | 1234.5 | 1234.5 | `1234.5` | True | True |
| BTC | 5 | 0.1234567 | 0.1 | `0.1` | True | True |
| ETH | 4 | 78012.1 | 78012.0 | `78012` | True | True |
| ETH | 4 | 12345.6 | 12345.0 | `12345` | True | True |
| ETH | 4 | 1234.5 | 1234.5 | `1234.5` | True | True |
| ETH | 4 | 0.1234567 | 0.12 | `0.12` | True | True |
| DOGE | 0 | 78012.1 | 78012.0 | `78012` | True | True |
| DOGE | 0 | 12345.6 | 12345.0 | `12345` | True | True |
| DOGE | 0 | 1234.5 | 1234.5 | `1234.5` | True | True |
| DOGE | 0 | 0.1234567 | 0.12345 | `0.12345` | True | True |

## 4. 組立（送信なし・実 asset_index と実 szDecimals）

- BTC の asset_index = **3**（`meta.universe` の並び位置）/ szDecimals = 5
- wire = `{"a": 3, "b": true, "p": "77541", "s": "0.00038", "r": false, "t": {"limit": {"tif": "Ioc"}}}`
- 名目 = 29.465580 USDC（min 10.0 / cap 60.0 の内側）
- 上限超過サイズの拒否: `notional 119.41314 exceeds cap 60.0 USDC (reject before submit)`
- 送信フラグ `sent` = **False**（このスクリプトから /exchange への送信は 0 件）
