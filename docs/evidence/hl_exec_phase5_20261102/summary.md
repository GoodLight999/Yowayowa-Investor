# HL第4段（Phase 5）テストネット最小注文往復 実装再現 証跡サマリ

2026-11-02 12:31 JST に COO が手動SDKで通したテストネット往復（Phase 4: `docs/evidence/hl_exec_phase4_20261102/roundtrip_log.jsonl`・sha256 `b71f90209e3a541b3bd29dc716ec8f09c071b8c0c7a9a765575574cc412152b3`）を、**hl_exec の実装経路（`src/yowayowa/hl_exec/orders.py` と `client.py`／`killswitch.py`／`journal.py`）経由で完全自動再現**した。

---

## 1. 受入条件の達成実測値

| # | 受入条件項目 | 要求規約 | 実測結果 | 判定 |
|---|---|---|---|---|
| 1 | BTC 買い指値発注と oid 受領 | サイズ 0.0002・最良買値×0.99 切捨て | `oid: 64298026132` 受領（価格 `77256.0`、サイズ `0.0002`） | **PASS** |
| 2 | 発注直後の照会 | open orders で 1件・oid 一致 | `count: 1`、`oid: 64298026132`、`coin: BTC`、`side: B` | **PASS** |
| 3 | 取消の実行と未約定 0件確認 | 取消 `success`、取消後未約定 0件 | 取消応答 `statuses: ["success"]`、事後 `openOrders: []`（0件） | **PASS** |
| 4 | 前後 clearinghouseState 照合 | accountValue 100.0 維持・建玉 0・新規約定 0 | 事前 `100.0` → 事後 `100.0`、建玉 `0` → `0`、新規約定 `0` 件 | **PASS** |
| 5 | 三段ログ（journal.py）記録 | SIGNAL / INTENT / ORDER が JSONL に記録 | `execution_journal.jsonl`（6行・ハッシュチェーン完全性検証 PASS） | **PASS** |
| 6 | キルスイッチ疎通 | 発注前ロック取得、正常終了解放 | `killswitch_lock_acquired` → `released`、排他ロック取得 → 解放確認 | **PASS** |
| 7 | テストと静的検査 | pytest / ruff / mypy の合格 | `pytest 122 passed`、`ruff check 0 errors`、`mypy 0 issues` | **PASS** |

---

## 2. 手動往復（Phase 4）との数値対照・一致／不一致とその理由

| 項目 | 手動往復（Phase 4 / COO手動SDK） | 実装経路（Phase 5 / 本実行） | 一致/不一致 | 理由 |
|---|---|---|---|---|
| 口座アドレス | `0xF6cB1079F73cA0163E2EfF24eFbB651f99E2Dd82` | `0xF6cB1079F73cA0163E2EfF24eFbB651f99E2Dd82` | **一致** | 同一口座（テストネット） |
| エージェント | `0xa2aF82D975e52D4FA110575d6CAc8f83e1ae9239` | `0xa2aF82D975e52D4FA110575d6CAc8f83e1ae9239` | **一致** | 承認済みエージェント `yowayowa-acct` |
| 対象銘柄 | BTC Perp | BTC Perp | **一致** | 仕様通り |
| 注文サイズ | `0.0002` BTC | `0.0002` BTC | **一致** | 最小テストサイズ |
| 事前 accountValue | `100.0` USDC | `100.0` USDC | **一致** | 不変 |
| 事後 accountValue | `100.0` USDC | `100.0` USDC | **一致** | 実弾0・手数料・約定損失なし |
| 前後建玉数 | `0` | `0` | **一致** | 建玉なし維持 |
| 新規約定数 | `0` | `0` | **一致** | 約定0件 |
| 市場気配（Bid/Ask） | Bid `77541.0` / Ask `77695.0` | Bid `78037.0` / Ask `78088.0` | 不一致（正常） | 実行時刻の差異による市場気配の変動 |
| 指値価格 | `76765.0` | `77256.0` | 不一致（正常） | `77541.0 * 0.99 = 76765.59`（切捨て76765）に対し、本実行時は `78037.0 * 0.99 = 77256.63`（切捨て77256）。**「最良買値×0.99切捨て」の同一丸めロジックが動的に正確適用**されている。 |
| Order ID (oid) | `64295258088` | `64298026132` | 不一致（正常） | 取引所が発番する連番IDのため（テストネット取引所側で 2,768,044件〔64,298,026,132 − 64,295,258,088、約276.8万件〕の注文が進行） |
| 発注直後未約定数 | `1` 件 | `1` 件 | **一致** | 発注直後に即座に照会 |
| 取消応答 | `{"statuses": ["success"]}` | `{"statuses": ["success"]}` | **一致** | 公式取消応答 |
| 取消後未約定数 | `0` 件 | `0` 件 | **一致** | 確実に取消完了 |

---

## 3. 証跡ファイル一覧と SHA-256 ハッシュ

ディレクトリ: `docs/evidence/hl_exec_phase5_20261102/`

| ファイル | SHA-256 | 説明 |
|---|---|---|
| `execution_journal.jsonl` | `7b6c42c05429f8bddafb0d14a9281583494f501cbd3aa5fc0449c5fabd09aa9e` | `journal.py` 経由の三段ログ（SIGNAL→INTENT→ORDER、改ざん不能ハッシュチェーン付き） |
| `roundtrip_log.jsonl` | `711d63580d822e318a50821e3016b9a0fa8afb4945473be975b3973a2e0a3930` | 各フェーズ（キルスイッチ・発注・照会・取消・事後状態）の全生イベントJSONL |
| `pre_state.json` | `605ca9d4a128476af83862e604745cbb00d3e7b304a624e2899d672f1c0a00b9` | 発注直前の `POST /info` `clearinghouseState` 生レスポンス |
| `post_state.json` | `4a909e11bcf686f29e2528c9fab2b12dcab9ecebf74de17c6f7f39bbb7a4b8bc` | 取消後の `POST /info` `clearinghouseState` 生レスポンス |

---

## 4. 品質検査結果

### 4.1 `pytest tests/test_hl_exec_*.py`
```
........................................................................ [ 59%]
..................................................                       [100%]
122 passed in 1.48s
```
- 件数: 122 passed（前回 119 passed より 3 件増加、全件合格）
- 最終行: `122 passed in 1.48s`

### 4.2 `ruff`
```
All checks passed!
```
- 対象: `src/yowayowa/hl_exec` および `tests/test_hl_exec*.py` 全体で 0 errors

### 4.3 `mypy`
```
Success: no issues found in 6 source files
```
- 対象: `src/yowayowa/hl_exec` の全6ソースファイルで 0 issues

### 4.4 秘密鍵スキャン
- `/root/.secrets/hl_agent_testnet_acct.key` の値・中身の漏洩: **0 件（完全非出力）**
- メインネット（`api.hyperliquid.xyz`）への実リクエスト接続コード: **0 行（禁止ガードのみ）**
