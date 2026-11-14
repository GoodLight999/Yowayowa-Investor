# Hyperliquid 実行トラック 第2段 — テストネット実口座への読み取り接続と実データ検証

- 実施: 2026-11-02 11:50〜12:05 JST（VPS・HTTP読み取りのみ）
- 対象口座（テストネット）: `0xF6cB1079F73cA0163E2EfF24eFbB651f99E2Dd82`
- 設計正本: `docs/CIO_HL_EXECUTION_TRACK_PLAN.md`（§2.1〜§2.4）
- 実装（第1段・検収済）: `src/yowayowa/hl_exec/{client,risk,orders,journal,killswitch}.py`
- 再現: `python scripts/hl_exec_phase2/phase2_read_probe.py`（証跡は `docs/evidence/hl_exec_phase2_20261102/`）
- tracked 化の経緯: プローブは commit `9607e14`（ファイル単位 add）で歴史入り、同日 commit `37e4319` で新 API 追随の文言修正。証跡と実行補助スクリプトの tracked 方針は §7
- 判定: **完了条件5項目すべて充足。署名・発注・承認の実行は 0 件**

---

## 1. `/info` 読み取り（生レスポンスを証跡保存）

`hl_exec/client.py` の `HyperliquidInfoClient(base_url=INFO_URL_TESTNET)` を使用。HTTPステータスを取るため、client が公開している `transport` 注入口に記録用トランスポートを差した（実装は既定と同じ urllib・URLは client 保持の base_url に固定）。

| # | type | HTTP | 応答バイト | 応答時間 |
| --- | --- | --- | --- | --- |
| 1 | clearinghouseState | **200** | 319 | 193.5 ms |
| 2 | meta | **200** | 16879 | 106.6 ms |
| 3 | l2Book(BTC) | **200** | 1550 | 90.9 ms |

（以後の asset_meta / top_of_book 取得分を含め、実行全体で **9 リクエスト全て 200**。全リクエストの URL は `https://api.hyperliquid-testnet.xyz/info` の1種類のみ。）

残高・建玉（`clearinghouseState.json` 原文）:

```json
"marginSummary": {"accountValue": "100.0", "totalNtlPos": "0.0", "totalRawUsd": "100.0", "totalMarginUsed": "0.0"},
"withdrawable": "100.0",
"assetPositions": []
```

→ **accountValue = 100.0 USDC・withdrawable = 100.0 USDC・建玉 0 件**（カード前提と一致）。

BTC の板（`l2Book_BTC.json`・同時刻）: bid/ask = 77511.0 / 77541.0、mid = 77526.0、スプレッド **3.87 bps**（片側 1.93 bps）。

## 2. `risk.py` を実データに通した判定（境界の数値）

設計 §2.4 は「1建玉の隔離証拠金 ≤ 20.0 USDC」かつ「口座資金の 10.0%」。実残高 100.0 USDC では **10.0 USDC が拘束側**になるため、呼出側で `cap = min(20.0, 10%×実残高)` を明示適用した（理由は §5 の発見1）。

> 注（2026-11-02 追記・裁定 t_e91f2dbc）: 本節の測定後に比率側（10%）は `risk.py` 内部適用へ移された。以後 `check_isolated_margin` / `evaluate_entry_gates` は `equity_usdc` 必須で `min(20.0, 10%×equity)` を内部適用する。プローブも `equity_usdc` を渡す形に追随済み（commit `37e4319`）。下表の cap 10.0 / 名目上限 30.0 は変わらない。

| 項目 | 値 |
| --- | --- |
| 実残高 | 100.0 USDC |
| 隔離証拠金上限（設計） | min(20.0, 10.0%×100.0) = **10.0 USDC** |
| 名目上限（レバレッジ3.0倍） | min(60.0, 3×10.0) = **30.0 USDC** |
| 許可される最大サイズ（ask 77541.0） | **0.00038 BTC**（名目 29.46558 USDC） |
| その1刻み上（0.00039 BTC） | 名目 30.24099 USDC → **拒否** |

境界の実測（`evaluate_entry_gates` + `check_leverage` + `margin_preflight_ok`）:

| ケース | 判定 | 落ちたルール |
| --- | --- | --- |
| 最大サイズ 0.00038 BTC（notional 29.4656） | **全ルール許可** | — （leverage 2.9466 ≤ 3.0、margin 10.0 ≤ 20.0〔測定時〕、notional 29.47 ≤ 60.0、min_notional 10.0 以上） |
| 1刻み上 0.00039 BTC（notional 30.2410） | **拒否** | `leverage_cap`（3.0241 > 3.0） |
| 最小発注額割れ 0.00012 BTC（notional 9.3049） | **拒否** | `min_notional`（9.305 < 10.0） |
| 最小発注額を満たす最小 0.00013 BTC | 許可 | — （notional 10.0803） |
| 2建玉目（同時保有） | **拒否** | `max_concurrent_positions`（2 > 1） |
| 証拠金事前チェック（margin 10.0 + バッファ5.0 ≤ withdrawable 100.0） | 許可 | — |

停止系（実残高基準で閾値が自動的に小さくなる側を採用＝fail-safe）:

- 日次損失停止: 閾値 = min(4.0, 2.0%×100.0) = **2.0 USDC**。−2.0 で発動、−1.999999 では非発動（実測）。
- 累積DD停止: 閾値 = min(8.0, 4.0%×100.0) = **4.0 USDC**。100.0→96.0 で発動、96.000001 では非発動（実測）。
- 清算距離: 0.15 → True / 0.1499 → False（実測）。

→ **許可/拒否の境界は設計の上限値と一致**。200 USDC 想定口座では 20.0/60.0 が拘束だが、実口座 100 USDC では 10.0/30.0 が拘束側になる（比率ルールが先に効く）。

## 3. `orders.py` の丸め（テストネットの実 `szDecimals`）

`meta` から取得した実値: BTC=5, ETH=4, SOL=2, DOGE=0, HYPE=2（同一実行時の `/info` meta をそのまま使用。設計書 §2.3 の「BTC szDecimals=5」と一致）。

価格（実 mid をそのまま通した例・全て切捨て）:

| coin | szDecimals | 価格小数上限（6−sz） | mid | 丸め後 | wire | `price_is_valid` |
| --- | --- | --- | --- | --- | --- | --- |
| BTC | 5 | 1 | 77526.0 | 77526.0 | `77526` | True |
| ETH | 4 | 2 | 2304.75 | **2304.7** | `2304.7` | True |
| SOL | 2 | 4 | 97.483 | 97.483 | `97.483` | True |
| DOGE | 0 | 6 | 0.075521 | 0.075521 | `0.075521` | True |
| HYPE | 2 | 4 | 82.056 | 82.056 | `82.056` | True |

公式 tick 契約の境界例（実 szDecimals ごとに同じ入力を通した結果）:

| 入力 | BTC(5) | ETH(4) | DOGE(0) |
| --- | --- | --- | --- |
| 78012.1 | **78012** | 78012 | 78012 |
| 12345.6 | **12345** | 12345 | 12345 |
| 1234.5 | 1234.5 | 1234.5 | 1234.5 |
| 0.1234567 | **0.1**（小数上限1桁が拘束） | **0.12**（上限2桁） | **0.12345**（有効数字5桁） |

サイズ（BTC・`szDecimals=5`・刻み 0.00001）:

| 要求 | 丸め後 | `size_is_valid` |
| --- | --- | --- |
| 0.000789012 | **0.00078** | True |
| 0.0012345 | **0.00123** | True |
| 0.000999949 | **0.00099** | True |
| 1/3 | **0.33333** | True |

→ 実銘柄で「切り捨て・有効数字5桁・小数上限 6−szDecimals 桁」の3条件が同時に効いていることを確認。`0.1234567` が BTC で `0.1` になるのは仕様どおり（小数上限1桁が有効数字より厳しい）。

組立（実 `asset_index` = **3**・送信なし）:

```json
{"a": 3, "b": true, "p": "77541", "s": "0.00038", "r": false, "t": {"limit": {"tif": "Ioc"}}}
```

名目 29.46558 USDC（min 10.0 / cap 60.0 の内側）。上限超過サイズは `notional 119.41314 exceeds cap 60.0 USDC (reject before submit)` で**送信前に拒否**されることを実測。

## 4. 署名・発注・承認 0 件の証明

| 検査 | 方法 | 結果 |
| --- | --- | --- |
| 送信先URL | 証跡 `call_log.json` の全リクエスト | `https://api.hyperliquid-testnet.xyz/info` の **1種類のみ**（`/exchange` 0 件） |
| リクエスト種別 | 同上 | clearinghouseState 1 / meta 2 / l2Book 6 の計9件。`order`・`cancel`・`approveAgent` は 0 件 |
| コード上の送信経路 | `grep -rn "api.hyperliquid.xyz/exchange\|/exchange\|eth_account\|private_key\|sign(" src/yowayowa/hl_exec/*.py` | ヒットは docstring/コメント/出典URL のみ。送信・署名のコード行 0 |
| 鍵の読み書き | 実行スクリプトは鍵ファイルを一切開かない（`wallet`・`private` の参照 0） | `/root/hyperliquid-testnet-wallet.json` の mtime は **2026-09-27 20:20:49**（不変）・mode 600 |
| 鍵の新規生成 | `find /root/.secrets -newermt "2026-11-02 00:00"` | **0 件** |
| 承認状況 | `POST /info {"type":"extraAgents","user":…}` | `[]`（承認済みエージェント 0 件） |

第1段の回帰も再確認: `pytest tests/test_hl_exec_*.py` → **107 collected / 全 pass**（client 15, risk 20, orders 53, journal 9, killswitch 9, dryrun 1）。

## 5. 発見（第3段への申し送り）

1. **`risk.py` の隔離証拠金上限は、測定時点（11:55）は絶対額 20.0 USDC のみで、比率（10%）は呼出側の責務だった**。実残高 100.0 USDC に対して 20.0 USDC を許すと口座の 20% になり、設計 §2.4 の 10% ルールから外れる — この穴は同日の裁定 **t_e91f2dbc** で塞がれ、比率 10% は `risk.py` 内部適用（`min(20.0, 10%×equity)`・`equity_usdc` 必須）に移った（commit `37e4319`）。本検証の数値（cap 10.0 USDC）は内部適用後も同じで、プローブは `equity_usdc` を渡す形に追随済み。再実行による照合は `docs/evidence/hl_exec_phase2_20261102/recheck_t_1a54bf99.md`。
2. 実口座 100 USDC では **名目上限は 60.0 ではなく 30.0 USDC**（レバレッジ3.0倍が先に効く）。BTC の1刻み（0.00001 BTC ≒ 0.78 USDC）が粗いため、30.0 USDC ぴったりには寄らず **0.00038 BTC（29.47 USDC）が実用最大**。
3. 最小発注額 10.0 USDC のため、**実用最小は 0.00013 BTC**（10.08 USDC）。0.00012 BTC は 9.3 USDC で拒否される。
4. 同時刻の BTC スプレッドは **3.87 bps（片側 1.93 bps）**で、設計の IOC 許容幅 **2.0 bps** に対する余裕は **0.07 bps** しかない。板がわずかに広がるだけで mid±2.0 bps の指値IOCは約定せず即キャンセルされるため、テストネット14日ゲート（§2.6 G2）では「発注したが約定しない」件数を必ず分離して計測する必要がある。

## 6. 証跡ファイル

すべて `docs/evidence/hl_exec_phase2_20261102/`（秘密値なし）:

| ファイル | 内容 |
| --- | --- |
| `clearinghouseState.json` | `/info` 生レスポンス（残高・建玉） |
| `meta.json` | `/info` 生レスポンス（全銘柄の szDecimals・maxLeverage） |
| `l2Book_BTC.json` | `/info` 生レスポンス（BTC 板） |
| `call_log.json` | 全リクエストの URL・電文・HTTPステータス・応答時間 |
| `btc_tick.json` | BTC の asset_index / szDecimals / 最良気配 |
| `summary.json` / `summary.md` | 上記を集計した判定結果（本稿の数値の一次データ） |
| `ui_api_page_observation.md` | approveAgent 手順書の根拠（APIページの実DOM） |
| `recheck_t_1a54bf99.md` | HEAD 版プローブの再実行ログと、アーカイブ証跡との照合結果（引き取り検収） |

## 7. 成果物の tracked 方針（t_1a54bf99 で確定）

### 7.1 経緯（プローブが歴史入りした理由）

1. 第2段（t_d8b2dbe1）の完了時点で、プローブ `scripts/hl_exec_phase2/phase2_read_probe.py`（532行）と `docs/evidence/hl_exec_phase2_20261102/` は**未コミット**のままだった。
2. その後、隔離証拠金cap比率適用カード（t_dad0e051 / worker-agy）が新 API（`equity_usdc` 必須化）への追随でこのファイルを編集し、**ファイル単位の `git add`** によりプローブ全文が commit `9607e14` で歴史に入った（意図した追加ではなく巻き添え）。
3. Round 1 の指摘を受け、同カードはコミットを作り直して `37e4319`（= 現在の `agent/commercial-foundation` HEAD）とした。**`9607e14` は revert も履歴改変もせず、どのブランチからも到達しない位置に残している**（巻き添えを消すために履歴を書き換えない方針）。
4. 本カード（t_1a54bf99）で HEAD 版プローブを**実際に再実行**し、アーカイブ証跡と意味で一致することを確認して引き取った（`recheck_t_1a54bf99.md`）。worktree のファイル sha256 と `HEAD:` の blob sha256 が一致（`2bcea5d4…8114`）しており、実行した版＝歴史に入った版。

### 7.2 方針: `docs/evidence/<label>_<yyyymmdd>/` は **tracked**（`.gitignore` 運用にしない）

- 先例: `docs/evidence/20261023/`（18ファイル）が既に tracked で、`docs/MONETIZATION_READING_20261023.md` 等から「生データ（恒久）」として参照されている。証跡を git の外に置く運用は本リポジトリに存在しない。
- 規模: 第2段の証跡は 61KB / 9ファイル（`meta.json` が 27KB、引き取り検収ログ 6KB、当初の8ファイルで 54KB）。追記型のスナップショットで、継続的に膨らむ種類のデータではない。
- 安全性: `grep` で秘密鍵・シード・署名の類は**0件**（公開アドレスと `/info` の公開レスポンスのみ）。
- 利点: プローブが tracked になった以上、**生成器と生成物が同じ歴史に入る**ことで再現性が閉じる。ポインタ運用にすると、外部パスが消えた時点で証跡が失われる。

運用ルール:
1. 証跡は**実行後に上書きせず、`<label>` 単位でディレクトリを切る**（時系列の追記）。
2. 秘密値（秘密鍵・シード・署名・APIトークン）は**絶対に入れない**。入る余地がある出力は保存前に落とす。
3. `__pycache__/` は既存の `.gitignore` で除外（証跡ディレクトリ配下も同様）。

### 7.3 `scripts/hl_exec_phase2/check_agents.py` も tracked

`extraAgents` / `userRole` を `/info` だけで照会する読み取り専用の実行補助（44行・鍵不要・`/exchange` 送信なし）。approveAgent 手順書の「完了確認」で使うコマンドの実体なので、手順書と同じく履歴に置く。プローブと同じ**読み取り専用契約**（`POST /info` のみ）を守る。

### 7.4 `scripts/` の lint は正規範囲外（据え置き）

プローブには ruff 指摘が26件（E501 等）あるが、プロジェクトの正規 lint は `Makefile` の `lint: ruff check src tests` / `typecheck: mypy src/yowayowa`、CI（`.github/workflows/ci.yml`）でも `ruff check src tests browser_tests` であり、**`scripts/` は対象外**という既存運用。本カードでは指摘の修正（整形）を行わない — 検収済みの内容を動かさないため。`scripts/` を lint 対象に加えるかは別カードで扱う。

### 7.5 他フェーズの証跡

`docs/evidence/hl_exec_phase3_20261102/`・`docs/evidence/hl_exec_phase4_20261102/`・`docs/HL_EXEC_APPROVEAGENT_RUNBOOK.md` はそれぞれのカード（第3段 t_22130424 / 第4段）の管轄。**同じ方針（証跡は tracked）**を適用するが、当該カードの完了時にそのカードが commit する。本コミットでは触っていない。
