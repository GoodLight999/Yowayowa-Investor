# S-07 復帰候補：設計・受入試験仕様（2026-11-09）

**状態: 設計のみ／未実装・未検収・再開不許可。** 採否は `docs/HL_EXEC_DISPOSITION_20261108.md` が正。停止標識 `scripts/hl_testnet_s07/s07_daily_forward.py` を変更・解除しない。旧コード/旧PASS/旧往復は歴史資料であり、安全性・現行実績の証拠ではない。本書の試験は仕様であって実行結果ではない。資金/建玉操作が必要な箇所は **実装せず独立裁可へ**。testnetを含む発注・取消・署名・鍵生成、新cron、mainnet送信、pushは禁止。中立は何もしない（SHORT強制禁止）。

## 1. 基準の凍結と未解決点

対象は **HL testnet forward** であり、`scripts/s07_paper/s07_gate.py` の20/90営業日ペーパー判定と混同しない。歴史的な対象窓は `docs/evidence/hl_testnet_s07_forward_20261103/S07_TESTNET_FORWARD_EVALUATION.md:6` の **2026-11-04 00:00 UTC〜2026-12-02 00:00 UTC、28日**。同:22-28および退避した `rejected/s07_forward_evaluator.py.txt:5-16` の5条件を変更しない：①完了往復8以上、②手数料・funding後純損益>0、③ピーク比最大DD≤10%、④全約定記録かつslippage中央値≤5bp、⑤日次終了建玉0・全日ロック取得/解放記録・未約定残留0。8未満はINCONCLUSIVE。数値・比較演算子・判定区間・通貨を勝手に変更しない。窓外件数と別の参考集計を分離する。正式成績は不完全入力時 INCONCLUSIVE、違反の証拠があれば別に警告を出し、欠損を0としない。

**矛盾・差分を隠さない**：上記の歴史レポートは開始前11/03の取引を掲げる（同:6,38,46）。これは凍結窓の実績に使えない。旧evaluatorは窓をフィルタせず行数を数え、欠測を0補完し（同退避ファイル:43-111）、8未満を優先して統制違反を総合判定で覆い隠す（:121-133）。これらは旧実装の観測であり、既定の正しい成績定義として再採用しない。本設計はデータ品質を前提にするが、境界・サンプリングや不足時の扱いが実際の凍結仕様と異なると判明したら独断で確定せず差分・根拠を起票して返し、公式判定を保留する。とくに日次建玉0制約と自然な持越し/部分約定が矛盾する場合、強制決済を自動追加せず停止・独立裁可へ。

## 2. データと処理境界

- 対象の `network=testnet`、DEX、coin=BTC、interval=1d、account、取得元URL、取得時刻UTC、as-of、元応答hash、データ種別を各バッチおよび各行の由来に保持する。URL・実transportの一致を検証し、未知・不一致・混在は全バッチ拒否。ラベルだけを手で変えた混入も入力manifestと取得済み元応答の結び付きを照合し拒否。provider自身がnetworkを証明しないオフラインfixtureはfixtureの前提しか証明しない。
- 日足はUTCの開始 `t` と終了 `T`（HL例は最終ミリ秒）を検証し、`T < decision_as_of` かつ期待する1日長・UTC境界でのみ使用。未確定足や未来足は演算へ入れず検出を記録。ウォームアップは窓前の確定足を**指標用としてのみ**許し、窓内の取引・成績へ混入させない。候補試験上のイベント窓は `[開始,終了)` とし端点テストを行うが、歴史資料の閉区間明記は未確認のため、これを新たな正式成績条件と断定しない。差が生じる場合は裁定まで保留。
- シグナルは検証済み足と同network funding履歴だけで作る。`funding_annualized_pct` の半分欠損許容・`Strategy` のfunding欠測時の遮断なしは現状の注意点で、前段で必要時間帯の完全性を検証し、不足なら判断不能。将来の調査用補間は合成ラベル付き非正式出力のみ。ネットワーク切替・主観的補完は禁止。
- 取引所fillは `userFillsByTime` / `userFills` の個別fill、`aggregateByTime=false`、`oid/tid/time/coin/side/sz/px/fee/feeToken/closedPnl` を保存・照合。feeはbuilderFeeを含むため二重加算しない。通貨不一致は適切な出典付換算がなければ判定不能。公式 `/info` は `userFills` 最新2000、時刻指定でも1応答最大2000・最新10000制限。ページ境界が同時刻で飽和する等、全件性を立証できない場合は欠損として停止し、ページング成功と完全性を同一視しない。funding率は市場 `fundingHistory`、実損益は同口座・同networkの実際の `userFunding` 支払/受取を利用し、率を実支払に置換しない。原符号と単位を保存し、net=実現gross−実fee＋符号付き実fundingを一度だけ適用、口座明細と照合。生データがないときゼロを創作しない。合成fee/fundingは `synthetic` 明示のテスト出力のみ、公式成績に算入不可。
- 同一IDかつ内容同一の重複は1回のみ適用し件数・hashを記録。ID衝突で内容不一致、ID不明、範囲外取引の混入、欠落時刻/日/約定/手数料/fundingやページ欠けは検出・隔離し判定不能。範囲外行はカウントしないが入力証跡は保持。既存値を上書き補正せず、出典付き追記で訂正し再検証する。推測・前値・ゼロ補間は禁止。時刻・単位・銘柄・符号・負数/非有限値を検証。

## 3. 分離・状態・証跡

**設計上の許可先**：オフライン実装は呼出側が明示した一時dir以下 `dry-run/<run_id>/` のみへ state.json、events.jsonl、report.json、report.md を書く。既存 `data/hl_testnet_s07/`、`data/execution_logs/`、その他実台帳・state・既存レポートを開かない。run毎の新規ルート、fake clock/transport、network拒否、署名器/注文クライアント不注入を必須にする。パスの実体（realpath）、ディレクトリ包含、symlink・hardlink・同一inode・相対パス・親ディレクトリ再参照を検証して拒否。権限分離は可能な限りOSでも強制し、文字列prefix検査だけに依存しない。代替経路で実台帳を読んでしまうことも不可。出力先指定が無い場合fail closed。

試験では実台帳の**模擬コピー**をdry-runとは別ルートに置いて、開始/終了のパス一覧・inode・size・mtime・sha256を比較し、ファイルopen/write/rename/lockとネットワークの試行を監視する。symlink/hardlinkによる別名、同一path、親子path、相対脱出、出力・state交差を負の対照にする。実データのコピー・本物のパスへのアクセスは不要であり、実台帳が不変だったと錯覚させない。守れない環境では試験をSKIPと明記する。

各注文の独立したfillをID重複排除後に合計し、約定数量は要求数量を超えないこと、残数＝要求−累計fill、手数料は各fillの実数合計（リベートは符号維持）を検証。状態は `NEW → PARTIAL → FILLED` または `PARTIAL → CANCELED/REJECTED`、通信失敗や不一致は `UNKNOWN` として保持。取消後の残数も実約定を消さない。約定済みentry量とexit量が一致し、ポジションと未約定が照合済みの場合のみ往復完了。未解決時の再送・新規注文は禁止。注文不明時の状態照合は別作業 `t_49064d09` の契約に委ね、未知なら再送しない。

失敗・例外・中断も `dry-run/<run_id>/events.jsonl` に逐次記録、終了時 `report.json` に run ID、schema version、mode、network、data_kind、時刻、段階、入力hash/対象期間、エラー分類・安全化した例外、適用/除外件数、実行前後state hash、照合状態、retry可否と根拠、未確定注文ID（秘密なし）を記録する。生応答は機微・署名・鍵・token除去後のJSONを同runに保存、消去不能な秘密を含む場合は保存せず安全な分類とハッシュのみ。既存 `ExecutionJournal` はSIGNAL/INTENT/ORDER限定なので一般失敗イベントに無理に流用せず別JSONL。書込失敗時はstderrに安全な最小診断を出し非zero・処理停止、証跡保存成功とは主張しない。読み取りのみの一時障害はbounded retry可、書込/検証失敗は修復後新runで可、送信結果UNKNOWNや部分決済は自動再送不可・独立裁可。原本追記の巻戻しは禁止。

## 4. ピークDDの候補計測契約

凍結値は③のピーク比10%であり、以下は**候補の明確化であって旧実装の証明でも成績基準変更の承認でもない**。期間は§1窓、起点の実測開始equity（旧資料の名目100 USDCは実測の代替不可）と、各UTC日末の同一口座・同一networkの手数料/funding反映後equityをサンプルする。終了境界は最終日の完了時点のmarkとし、境界の実データ取得可否を明記。各時点 `peak_i=max(E_0,...,E_i)`、`DD_i=(peak_i-E_i)/peak_i`、`MaxDD=max(DD_i)×100%`。分母は各時点の正の累積ピークequityで固定100ではない。欠損・非有限・peak≤0・入出金等の外部資金移動で整合不能なら判定不能。日末サンプルは日中最大DDを保証せず、頻度/窓/実際の観測時刻を出力。別途取得した日中DDは参考のみ、凍結判定に混ぜない。候補に対し正式原典のサンプリング等の根拠が得られない場合は INCONCLUSIVE とし、差分を起票して返す。合成例 [100,120,108] の候補DDは10%（ピーク120分母）で、起点100分母と区別する。

## 5. 既存部品・最小追加範囲

| 部品 | 再利用範囲と制限 |
|---|---|
| `scripts/s07_paper/s07_strategy.py` `Strategy`, `funding_annualized_pct` | 検証済み配列への純計算のみ候補。旧名前 `sma200/ema20/ema50` は実際のDEFAULTS 50/10/20と異なるため表示時に実窓を明示。欠損・ネットワーク・確定足は呼出前に検査。 |
| `scripts/s07_paper/hl_client.py`, `fetch_s07_data.py` | 取得方法・ページングの先例のみ。後者はmainnet固定データを書き込み、不正JSONを空扱いするので本用途へ直接呼ばない。testnet由来保証を追加した別のread-only adapterが必要。 |
| `src/yowayowa/hl_exec/client.py` `HyperliquidInfoClient` | 偽transportを使うread-only照合。既定はmainnetでURLの厳格検証もないため、外側でtestnet exact URLと由来を検証。時刻別fill/funding等は現メソッドにない。`HyperliquidExchangeClient` はdry-runで生成も使用もしない。 |
| `src/yowayowa/hl_exec/orders.py` | `round_price`, `round_size`, 注文組立等の純関数だけ。`run_order_roundtrip` は送信経路なので呼ばない。 |
| `src/yowayowa/hl_exec/risk.py`, `killswitch.py` | 純粋検証・fakeによる挙動検査の候補。実lock/取消を実台帳や既存システムに向けない。 |
| `src/yowayowa/hl_exec/journal.py` | 分離root下で `ExecutionJournal` と `verify_chain` をSIGNAL/INTENT/ORDERに使用可能。hash chainは無改ざん保証ではなく外部保全された末尾hashなしに切詰めを検出できない。一般失敗用イベントは別に必要。 |
| `src/yowayowa/paperloop.py` `_max_drawdown_since` | running-peakの先例。単位/符号・期間/サンプリングが異なる可能性があり、同一契約と試験で確認するまで直接流用しない。 |

最小追加は隔離入力manifest検証、足/fee/funding正規化と完全性、個別fill状態再生、候補DD、隔離pathガード、失敗証跡とデータ種別付き表示。既存の不採用 `rejected/*.txt` を実行コードに戻さない。SDK接続先と不明注文照合は別担当の成果を利用し、未完成ならSKIP。独立裁可前に送信経路を組み込まない。

## 6. 受入試験表（要件1〜9とIDは1:1）

表のPASSは**試験アサーションのPASS**であり運用成績ではない。全行はまだ未実行。複数ケースは同一IDのサブケースで実施する。

| ID / 要件 | 前提 | 入力データ種別 | 期待結果 | オフライン | 既存試験との重複 |
|---|---|---|---|---|---|
| S07-A01 / 1 | fake clock・testnet manifest | 合成確定/未確定日足、mainnet混入、偽装由来 | 確定日足のみ指標に入る。未確定混入を検知、別network/未知/偽装由来は拒否、理由と件数を記録。境界直前・直後を検証 | 可 | 一部：`tests/test_hl_exec_client.py` はclient基本、混入保証なし |
| S07-A02 / 2 | 隔離2ルート・I/O監視 | 合成stateと模擬実台帳、link/親子/相対path | dry-runのpath/state/outputが実台帳と交差せず、負対照を拒否。実台帳模擬の一覧/inode/size/mtime/hash不変、open/write/lock試行0、送信0 | 可 | 一部：`tests/test_hl_exec_dryrun.py`、実台帳物理分離は別途 |
| S07-A03 / 3 | raw経路と同一口座/通貨検査 | 合成fill fee・符号付きfunding・欠損/他通貨 | 各feeとfundingを1回適用しnetを照合。欠落/換算不能は判定不能、合成は明示し正式実績不可。実取引値の真正性は別途 | 計算は可／実データ真正性は不可 | 一部：`tests/test_hl_exec_client.py` のfills取得、実fee/funding会計は別途 |
| S07-A04 / 4 | 候補DDと固定窓、同口座・実時刻 | 合成equity [100,120,108]、境界/欠損/非正peak | peak基準DD 10%、期間外を除外し日末頻度を表示。欠測/peak≤0/出典不明は判定不能。候補定義と凍結原典の整合未確認なら正式判定保留 | 可 | 一部：paperloopのDD試験は異なる帳簿・窓で同等保証なし |
| S07-A05 / 5 | 証跡と範囲指定 | 合成重複/衝突/期間外/欠測/ページ飽和 | 同一duplicateは1回・件数表示、衝突は拒否、窓外は計上せず隔離、欠測/完全性不明はINCONCLUSIVE。修正は追記のみ | 可 | 部分：`tests/test_hl_exec_journal.py` はチェーン検証のみ |
| S07-A06 / 6 | fake注文stateと照合 | 合成複数partial fill、取消、重複、通信断 | 累計quantity/feeを1回ずつ正しく計上、残数量と状態遷移を検証。未完了往復は数えずUNKNOWN時自動再送0 | 可 | 一部：`tests/test_hl_exec_roundtrip.py` は往復の先例、部分fill会計の証明ではない |
| S07-A07 / 7 | failure injection・隔離先 | 合成timeout、malformed、write拒否、UNKNOWN | events.jsonl/report.jsonに原因・入力hash・retry判断を保存。保存失敗は非zero/stderr、成功を装わず停止。UNKNOWNは再送不可 | 可 | 一部：`tests/test_hl_exec_journal.py` はchain改変検査、失敗証跡全経路は別途 |
| S07-A08 / 8 | JSONと人向けMarkdown両方のrenderer | 合成、実取得の保存済みfixture、混在 | 全レポートにdata_kindを必須表示。合成/混在は `synthetic`/`mixed` と非正式を明記、試験PASSを実績PASSに変換不可。欠落ラベル拒否 | 可 | 該当S-07実装なし（旧 `rejected/test_hl_testnet_s07_forward.py.txt` は不採用） |
| S07-A09 / 9 | §1原典の数値・窓をfixture化 | 合成閾値境界・窓開始/終了/旧11-03行 | 8、0、10%、5bpの等号/不等号と5条件の凍結値不変、窓外除外・窓境界の差は起票・公式保留。基準や期間を変更しない | 可（原典解釈の裁定は不可） | 旧退避evaluator試験は歴史資料のみ、採用・現行合格扱いしない |

## 7. オフライン外と引継ぎ

オフラインで証明できるのはモック/保存済みデータに対する契約のみ。実口座が必要な read-only `/info` 照合は公開account address・正しいnetwork・完全な取得権限とデータが必要で、同じ口座のfill/fundingとequity、注文/建玉、データの完全性を検証する。今回照会していない。新たな実約定・部分約定、手数料発生、注文取消、決済、運用中ロックの実効性等は**実口座・実注文が必要**で、オフラインの合格から推定できない。これらは **実装せず独立裁可へ**。現状の停止標識とcronの扱いをこの文書は変更しない。

実装担当は各IDのPASS/SKIP/NOT-IMPLEMENTED、根拠ログ・データ種別・未検証事項を記録。重い `make verify` はTSUKUMO専用snapshot、通信遮断および送信0を証拠にする。実装と独立レビューの後も停止解除には別途裁可が必要。

出典：`docs/HL_EXEC_DISPOSITION_20261108.md`、`docs/evidence/hl_exec_disposition_20261108/rejected/s07_forward_evaluator.py.txt`（歴史記録）、`docs/evidence/hl_testnet_s07_forward_20261103/README.md`、[HL公式 Info](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint)、[HL公式 Perpetuals](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint/perpetuals)、[HL公式 Exchange](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/exchange-endpoint)。`precedent-check S07` は環境に存在せず exit 127。代わりにrepoの既存strategy/client/journal/order/testsおよび公式文書を確認。調査時HEAD `40dc5e43ac08e8b0e0f75dfd21945085a64401dc`、PR #1 remote head `4c86de9cbfc1c4b0da074e07383dd67fd6854489`（ローカルと異なるため自動上書き・pushなし）。
