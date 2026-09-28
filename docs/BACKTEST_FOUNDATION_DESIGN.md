# Backtest foundation design（バックテスト研究基盤 設計）

> **本文書は設計提案である。実装・実行はマスターの明示的承認後に着手する。** 承認まではコード・スキーマ・cron を一切変更しない。

- 状態: 設計提案（design only）。実装コード 0 行・テスト 0 本。
- 作成日: 2026-09-28（JST）
- 改訂: 2026-09-28 第2版 — ユニバースを株式 3 種から **MT5 5 種 + Hyperliquid 3 層** へ拡張し、**レバレッジ取引のモデル化**（証拠金・維持率・強制ロスカット・スワップ・funding）を新設（マスター指摘 2026-09-28）。
- 筆者: Hermes worker（CTO-Hephaestus 指揮下の文書化作業）
- 読者: マスター（承認者）・CTO・実装担当エージェント
- 関連文書: `docs/PRIVATE_OPERATOR_ROADMAP.md`（P3/P4）・`docs/CRYPTO.md`・`docs/CRYPTO_MT5.md`・`docs/STOCKS.md`・`docs/EDINET.md`・`docs/SEC.md`・`docs/DATA_POLICY.md`

## 0. 用語

- **PIT（point-in-time）**: 時点 t の判断に使える情報を「時点 t で既に発見・公表されていたもの」に限定する原則。
- **ユニバース（universe）**: 1 回のバックテストで対象とする銘柄/市場の集合。`universe_id` で識別し、通貨・カレンダー・ベンチマーク方針を伴う。
- **ホライズン（horizon）**: エントリから評価までの期間。株式は trading_days・24/7 市場は calendar_days で数える（D4/D5 のカレンダー契約）。
- **run**: バックテスト 1 回の実行単位。`run_id`・config・データマニフェスト・成果物を 1 組に束ねる（D7）。
- **発見日（discovery date）**: システム（または市場参加者）がその情報を初めて参照可能になった日。会計期間の末日ではない（D2）。
- **レバレッジ・モード（leverage_mode）**: ある run がレバレッジ取引をモデル化しているか否か。`unlevered` / `levered` の 2 値（D9）。
- **証拠金（margin）**: 建玉を保持するために拘束される資金。実効レバレッジ = 想定元本 ÷ 証拠金（D9）。
- **強制ロスカット（margin call / stop out）**: equity が維持率の閾値を下回ったときにブローカーが強制決済する挙動（D9）。

## 1. 目的とスコープ

### 1.1 目的

投資バックテストを「正しく」行える研究基盤を設計すること。本設計が定めるのは、過去データ上で戦略・シグナルを検証するための契約（データ参照の規約・成果物の形・監査可能性）であり、バックテストの実行はまだ行わない。

「正しく」の最小要件は次の 2 点に集約される。

1. **Point-in-time（PIT）**: シミュレーション内のあらゆるデータ参照は「その時点 t で既知だったもの」に限定する（D2）。
2. **監査可能性**: どの run も、どのデータ（どの provider・どの coverage 窓）で・どの設定で回ったかを後から完全に再構成できる（D7）。

### 1.2 なぜ今この基盤か

- P3 の集計キャリブレーション（`services/strategy_calibration.py`）は既にコードとして存在し、rank IC や decile 統計を提供している。しかしロードマップのとおり **walk-forward / out-of-sample 評価は NOT STARTED** であり、「そのスコアは当時の情報だけから付けられて、その後に本当に効いたのか」を検証する面が無い。
- P4 で EDINET 提出履歴・週次/日次信用残・IR 文書・crypto/US 株 OHLCV というデータ面が蓄積してきた。蓄積は検証されて初めて情報優位になる。取得面が先に育ち、検証面が未整備の状態を解消するのが本基盤の役割である。

### 1.3 なぜ第2版でユニバースを拡張するか（マスター指摘 2026-09-28）

第1版のユニバースは `jp_equity / us_equity / hl_perp` の 3 つだけで、FX・貴金属・コモディティ・指数・暗号 CFD が欠落していた。「株だけじゃなくて暗号通貨や FX やコモディティは？ MT5（Vantage）と Hyperliquid があれば、かなり多くのものを（レバレッジありで）取引できるよね？」という指摘のとおり、これは設計の穴である。

さらに調査の結果、Hyperliquid の `hl_perp` を「暗号のみ」とみなすのも誤りであることが実測で判明した。HIP-3 ビルダー配備 Perp を通じて **株式・コモディティ・指数・FX/債券・未上場株まで**レバレッジ取引でき、同一 `hl_perp` に暗号と株式と指数が同居している。第2版ではこの階層構造を明示的に分解する（D3）。

レバレッジ取引は本基盤の**中心的な正しさの要件**である。レバレッジありの損益とレバレッジなしの損益、レバレッジ 20 倍の損益と 500 倍の損益を無自覚に合算することは、通貨を混ぜること以上に結果を歪める。第2版では D9（レバレッジ・証拠金モデル）を新設し、run 単位でレバレッジの有無と倍率を明示する。

### 1.4 ロードマップとの接続

- **P3（収益性/学習ループ）**: `docs/PRIVATE_OPERATOR_ROADMAP.md` の P3 は 2026-09-26 再ベースライン時点で「aggregate calibration DONE in code — walk-forward/out-of-sample and portfolio-aware sizing NOT STARTED」である。本基盤（特に機能提案 C）は、この NOT STARTED 部分（walk-forward / out-of-sample 評価・portfolio-aware sizing の土台）を直接実現する。
- **P4（日本市場情報優位）**: EDINET 提出履歴・信用残・IR・Hyperliquid パーペという private/検証データの蓄積を、screener のその場の判定ではなく「当時何が既知で、その後何が起きたか」の検証対象に変換する（機能提案 B・D・E）。
- **P4-E（データ源拡張）**: 第2版で追加する `mt5_*` / `crypto_spot` は、P4-E で確立した「キー不要/自前基盤から取得し provenance 付きで永続化する」パターンの延長である（`docs/CRYPTO.md` の CTO 固定判断を踏襲）。

### 1.5 スコープ外（v1 ではやらない）

以下は本設計の v1 スコープ外とする。実装提案の対象にもしない。

- パラメータ最適化（grid / random / Bayesian、過学習管理を含む）
- 機械学習ファクトリモデル
- オプション（MT5 には `option_mode`/greeks フィールドが存在するが、本基盤の対象外）
- **清算ウォーターフォールの完全再現**（HL の backstop liquidation・ADL・保険基金の連鎖。D9 のとおり強制ロスカットの判定と invalidation 記録に留める）
- **全ユニバース横断の統合ポートフォリオ損益**（通貨・証拠金・レバレッジの異なる建玉を 1 つの equity に統合する計算。D3 の分離規律により v2 送り）
- 複数通貨の混合計算（エンジン内 FX 変換。3.2 のとおり**損益通貨と報告通貨の変換 1 つだけ**を許し、それ以上の通貨混在を認めない）
- 公開 SaaS 機能（ロードマップで frozen。バックテスト面は personal モード専用とする — D8）
- 予測市場（HIP-4 outcome markets。レバレッジなしバイナリであり本基盤の対象外 — D3）

## 2. 設計判断（CTO固定・2026-09-28）

この節の D1〜D10 は CTO による固定判断であり、提案ではない。変更・追加の案はすべて「## 5. Open Questions」または実装タスク側に分離し、この節を直接書き換えない。

### D1. 単一イベントエンジン・複数データプレーン

- バックテストの核は **1 つの point-in-time イベントエンジン** とする。エンジンの責務は「イベント（新しいバーの確定・新しい文書の到着・新しいファンディング区間の確定）が到着したとき、時点 t で既知の情報のみで判断し、次の実行可能価格で約定を記録する」ことのみとする。
- 各ユニバースはエンジンに対する **アダプタ** である。アダプタが供給するのは:
  - 市場カレンダー（株式 = 営業日・FX/指数/CFD = 24/5・crypto = UTC 暦日。D3）
  - バー系列（決済済みバーのみ。未確定バーは供給しない）
  - 取引コスト設定（spread / commission / swap。D6/D9）
  - 証拠金・レバレッジ規則（実効レバレッジ・強制ロスカット閾値。D9）
  - 指標の年化スケール（株式 252 / 24・7 市場 365 — D6）
- **1 ユニバース内に異市場のデータを混ぜない**。株式ユニバースに crypto のバーを混ぜない。HL ユニバースに株式ファンダメンタルを参照させない。これは `docs/CRYPTO.md` の「2 source は混ぜない」設計判断（2026-09-24 CTO固定）と同趣旨の分離規律である。

この構成が防止するアンチパターン: 市場ごとに場当たり的に書かれた「バックテストもどき」ループが乱立し、市場ごとに約定・コスト・時刻の扱いが微妙に異なって比較不能になること。エンジン 1 本に約定・PIT・コスト・証拠金の意味を集約し、市場差はアダプタの差分としてだけ表現する。

**先行例の活用方針（車輪の再発明を避ける — `AGENTS.md` 不変条件 5「Official/maintained sources before reinvention」の適用）:**

本基盤は「バックテストライブラリ」を自作することを目的にしない。既存の成熟ライブラリ/部品を最大限再利用し、自作するのは**既存部品で表現できない正確性要件の部分だけ**とする。具体的な切り分けは実装タスクで確定するが、設計段階の方針を次に固定する。

- **無条件に再利用する**: `numpy` / `pandas`（既に依存にある）を計算基盤とする。指標（Sharpe・CAGR・max drawdown・total return）は**標準的な公開定義に従い、既存の成熟実装がある場合はそれを用いる**（社内で独自定義を作らない）。`pandas-ta-classic` は既に依存にあり、テクニカル指標の自作を避ける。
- **ライブラリを実行カーネルとして採用しうる**: vectorbt / backtesting.py / backtrader 等を、**`unlevered` かつ単一ユニバースのベクトル化ケース**の実行カーネルとして採用することを検討する。採用条件は「D2（PIT・約定遅延・非補間）と D8（run マニフェスト・provenance・再現性）を破らずに実現できること」であり、条件を満たせば自作カーネルを持たない。
- **採用できない部分は自作する（NIH ではなく正確性のため）**: D9 の証拠金・強制ロスカット・キャリー（MT5 の `swap_mode` 5 種・`swap_rollover3days`・HL の毎時 funding と HIP-3 の multiplier / interest rate / 清算価格式）は、**どの汎用ライブラリも venue 固有のこの会計を実装していない**。したがってこの部分は自作が必須であり、D9 の恒等式をそのまま実装する。同様に PIT 評価（D2）とマニフェスト（D8）は本製品固有の要件である。
- **判定の手順**: エンジン（C）着手前に、上記の条件を**受入テスト（PIT 違反を検出できるか・マニフェストが再現できるか・MT5/HL のコストを表現できるか）で比較する spike** を実施し、その結論を実装タスクの記録として残す。設計段階でライブラリを決め打ちしない（未検証の採用判断を埋め込まない）。

### D2. Point-in-time（PIT）原則

シミュレーション内のデータ参照は必ず「時点 t で既知だったもの」に限定する。ソース別の規約:

| ソース | 発見時刻の扱い | 再利用する既存面 | 禁止する先読み |
|---|---|---|---|
| EDINET | 書類の **発見日（filed / published date）** | `edinet_filings` インデックス・`index_coverage_complete` ゲート | 会計期間の末日を発見日とみなすこと（期末決算を期末時点で既知扱いにする） |
| SEC | Company Facts の各ファクトが保持する **filed + accession** | 既存プロバイダ（`docs/SEC.md` の正規化契約） | 最新の修正後値を過去の時点に遡って適用すること |
| OHLCV (stock/crypto/MT5) | バー T の close は T の確定時刻に既知 | 既存 store（冪等・非補間） | 確定していないバーの参照・T の close での約定 |
| P1C IR 文書 | provenance の **published_at** | P1C の文書 provenance | 収集日を公開日と混同すること |
| screening 候補 | **run_date** キーで永続化済み | `persist_screening_run` / `read_screening_candidates` | 過去 run の再解釈（永続値をそのまま使う） |
| HL funding | 各区間の **確定時刻（`time`）** | D4 の取得面 | 将来区間の funding を当日に適用すること |
| MT5 swap | 建玉を翌営業日へ持ち越した時点 | `symbol_info` のスワップ値（実測値） | 現在のスワップ値を過去の全期間に一律適用すること（D9・Open Question） |

- OHLCV の約定規約: バー T の close で決定した売買は **T+1 の open で約定** する（最小 1 バー遅延）。zero-fill / forward-fill は禁止する（既存 store の冪等・非補間方針 — `docs/CRYPTO.md`「API の欠落日は zero/fwd-fill しない」— と整合）。データが欠けている区間は「取引できない区間」として扱い、値を捏造して埋めない。
- `index_coverage_complete` ゲートの意味: 部分的なインデックス coverage を「最新年報が揃っている」ことの根拠に使わない。coverage が不完全な期間はファンダメンタル参照が unavailable になる（ゼロや直近値で埋めない）。
- screening 候補は run_date キーで永続化済みのため過去 runs を再生可能。エントリ判断の時点は T+1 規約に従う。

### D3. ユニバース / 通貨 / レバレッジ・レーンの分離

#### 3.1 ユニバース一覧（第2版・14 種）

入力系統（データ源）で 3 つのプレーンに分かれる。**プレーンは入力系統であり、資産クラスではない**。`hl_perp` プレーンは暗号だけでなく株式・コモディティ・指数・FX を（HIP-3 経由で）含み、`mt5` プレーンも CFD 株式・指数・コモディティを含む。したがって資産クラスの区別はユニバース ID で表す。

| # | universe_id | 資産クラス | プレーン | 通貨 | カレンダー | レバレッジ | ベンチマーク |
|---|---|---|---|---|---|---|---|
| 1 | `jp_equity` | 日本株 | store (Yahoo/EDINET) | JPY | 営業日（JPX） | なし | `^N225` |
| 2 | `us_equity` | 米国株 | store (Alpaca) | USD | 営業日（NYSE） | なし | `^GSPC` |
| 3 | `crypto_spot` | 暗号現物 | store (CoinGecko/Binance) | USD/USDT | 24/7 UTC 暦日 | なし | 任意（BTC 等） |
| 4 | `mt5_fx` | FX | MT5 copy_rates | **建玉ごと（下記 3.2）** | 24/5 | 実効 1000 倍（口座） | 任意 |
| 5 | `mt5_metal` | 貴金属 | MT5 copy_rates | USD | 24/5 | 20〜1000 倍（品目別・金/銀 1000/100） | 任意 |
| 6 | `mt5_commodity` | エネルギー・金属 | MT5 copy_rates | USD | 24/5 | 20〜500 倍（品目別） | 任意 |
| 7 | `mt5_index` | 株価指数 CFD | MT5 copy_rates | 建玉ごと（USD/EUR/JPY/GBP） | 24/5 | 377〜500 倍（品目別） | 同一指数 |
| 8 | `mt5_cfd_stock` | 米国株 CFD | MT5 copy_rates | USD | 24/5 | 20 倍（実測。全域は 824 銘柄・品目別に要確認） | 現物株（`us_equity` とは別 run） |
| 9 | `hl_perp` | 暗号無期限 | HL 公開 API | USDC | 24/7 UTC 暦日 | **3〜40 倍**（`max` 分布 3:130/5:63/10:35/20:4/25:1/40:1・実測） | 任意（none 許容） |
| 10 | `hl_stock` | 株式無期限（HIP-3） | HL 公開 API | USDC | 24/5（配当なし） | 3〜50 倍（`xyz` 実測。dex 間で最大 50） | 任意（none 許容） |
| 11 | `hl_commodity` | コモディティ無期限（HIP-3） | HL 公開 API | USDC | 24/7（ブロックごと精算） | 3〜30 倍（`xyz` 実測） | 任意（none 許容） |
| 12 | `hl_index` | 指数無期限（HIP-3） | HL 公開 API | USDC | 24/7（ブロックごと精算） | 10〜50 倍（`xyz`/`km` 実測） | 任意（none 許容） |
| 13 | `hl_fx` | FX・債券無期限（HIP-3） | HL 公開 API | USDC | 24/7（ブロックごと精算） | 10〜50 倍（`xyz`/`km` 実測） | 任意（none 許容） |
| 14 | `hl_private` | 未上場株無期限（HIP-3） | HL 公開 API | USDC | 24/7（ブロックごと精算） | 3〜20 倍（`vntl` 実測） | none 固定 |

> 表は 14 行（ID 14 種）。第1版の 3 種に `mt5_*` 5 種と `hl_*` 6 種を加えたものである。
> `hl_stock` のカレンダーは **24/5**（HIP-3 の株式 Perp は現物市場の閉場中も取引できるが、原資産の取引時間に規律される）。`hl_index`/`hl_fx` は 24/7。この差は D4 のカレンダー契約として明示する。

#### 3.2 通貨の扱い（第2版で精密化）

- 第1版は「通貨はユニバース毎に固定」としたが、**MT5 の FX と指数はそもそも多通貨である**。実測（2026-09-28・account 17128064）:
  - `USDJPY` の `currency_profit` = **JPY**・`EURJPY`/`GBPJPY`/`AUDJPY` = **JPY**・`USDCHF` = **CHF**・`USDCAD` = **CAD**
  - `GER40.r` = **EUR**・`Nikkei225`/`JPN225ft` = **JPY**・`UK100.r` = **GBP**
- したがって契約は次のように精密化する: **エンジンは 1 run 内で 2 通貨まで許容する**。すなわち
  1. **損益通貨（P&L currency / `currency_profit`）** — 建玉損益の単位。
  2. **証拠金通貨（margin currency / 口座通貨 = USD）** — 証拠金・equity・ロスカット判定の単位。
- この 2 通貨間の換算は、**建玉が損益を出す瞬間に発生する変換 1 つだけ**に限定する（USDJPY の損益は JPY 建てだが、equity と証拠金は USD 建て）。換算レートには PIT 規律を適用し、**当該時刻のレートを保持しているユニバース（`mt5_fx`）または FRED 等の公表レートを使う**。
- したがって `mt5_fx` は「FX ペアの価格系列」と「レポート通貨への換算レート系列」の両方を供給する。換算レートが欠けている期間は **equity を計算しない**（unavailable。1.0 で代用しない）。
- **禁止**: 異なる `currency_profit` の run を 1 つの数値列に合算すること。JPY 建て run の total return と USD 建て run の total return を単純合算した「合計リターン」を名乗ること。
- **禁止**: ポジションサイズを「1 lot」で固定したまま、異なる実効レバレッジの run を比較して「レバレッジの効果」を論じること（D9/3.3）。
- クロスユニバース比較は **通貨ラベル付きレポート**でのみ行う。run には `report_currency`（通常 USD）と `native_currencies`（その run に現れた損益通貨の集合）を必ず記録する。

#### 3.3 レバレッジ・レーンの分離

同一資産クラスでも、レバレッジの有無で損益の意味が変わる。契約:

- 全 run は `leverage_mode`（`unlevered` / `levered`）を必ず持つ。未指定は不可（「暗黙のレバレッジなし」を認めない。レバレッジの有無自体が結果を左右するため、明示を要求する）。
- `levered` run は `leverage`（倍率）と `position_sizing`（証拠金基準か想定元本基準か）を明示する。
- **`unlevered` run と `levered` run を 1 つの比較表の同一列に置かない**。レポートは `leverage_mode` で必ずグループ化し、異なるレバレッジの数値に共通の「合計」を出さない。
- 同一戦略のレバレッジ感度を見る場合は、`leverage` 以外の config が同一の run 群（同一 `config hash` の `leverage` のみ差分）として並べる。

この分離が防止するアンチパターン: 20 倍の run と 500 倍の run のリターンを並べて「この戦略は儲かる」と結論すること。株式（レバレッジなし）と FX（1000 倍）と HL パーペ（40 倍）の損益を通貨もレバレッジも揃えずに合算すること。

### D4. Hyperliquid データプレーン

調査結果（2026-09-28・公式 docs 確認 + 公開 info API 実測）に基づく。検証済み事実と実装時確認事項を混ぜない。

#### 4.1 検証済み（実測値・2026-09-28）

**取引対象の 3 層構造（実測）:**

| 層 | 実測数 | 取得方法 |
|---|---|---|
| HyperCore perp（メイン無期限・暗号） | **234 銘柄** | `{"type":"meta"}` |
| 現物（spot） | **330 ペア**（うち名前付き 1・`@NNNN` 形式 329） | `{"type":"spotMeta"}` |
| HIP-3 ビルダー配備 Perp | **294 銘柄 / 10 dex** | `{"type":"perpDexs"}` → 各 dex に `{"type":"meta","dex":<name>}` |

HIP-3 dex 内訳（実測）: `xyz`=126 / `flx`=16 / `vntl`=15 / `hyna`=25 / `km`=23 / `abcd`=1 / `cash`=17 / `para`=36 / `mkts`=24 / `io`=11。

**HIP-3 が暗号以外を含むこと（実測で確認）:**

- 株式: `xyz:TSLA` / `xyz:NVDA` / `xyz:AAPL` / `xyz:MSFT` / `xyz:GOOGL` / `xyz:AMZN` / `xyz:META` / `xyz:BABA` / `km:TENCENT` / `xyz:HOOD` / `xyz:COIN` / `xyz:PLTR` / `xyz:AMD` / `xyz:MU` ほか
- コモディティ: `xyz:GOLD` / `xyz:SILVER` / `xyz:PLATINUM` / `xyz:PALLADIUM` / `xyz:COPPER` / `xyz:WHEAT` / `xyz:NATGAS` / `xyz:CL`（WTI）/ `xyz:BRENTOIL` / `vntl:SOY`
- 指数: `km:US500` / `xyz:SP500` / `xyz:XYZ100` / `flx:USA100` / `km:JPN225` / `km:USTECH` / `km:SMALL2000` / `vntl:MAG7` / `vntl:SEMIS`
- FX・債券: `xyz:EUR` / `xyz:GBP` / `xyz:JPY` / `km:USBOND` / `para:10Y` / `para:2Y`
- 未上場株: `vntl:SPACEX` / `vntl:OPENAI` / `vntl:ANTHROPIC`

**流動性の実測（24h `dayNtlVlm`）— 設計上の決定的な事実:**

- HIP-3 全体の 24h 出来高は **約 18.9 億 USD**、うち **`xyz` が 18.3 億（96.8%）**。
- **`flx` / `vntl` / `hyna` / `km` / `abcd` / `cash` の 6 dex は 24h 出来高ゼロ**（実測。各 dex の全銘柄が `dayNtlVlm = 0`）。
- 294 銘柄のうち **144 銘柄が出来高ゼロ**、152 銘柄が 10k USD 未満、177 銘柄が 100k USD 未満。中央値 2,632 USD。
- 出来高上位は `xyz:SILVER`(2.14 億) / `xyz:SP500`(1.85 億) / `xyz:XYZ100`(1.54 億) / `xyz:CL`(1.29 億) / `xyz:GOLD`(0.99 億) / `xyz:BRENTOIL`(0.93 億) / `xyz:META`(0.59 億) / `xyz:NVDA`(0.51 億)。
- メイン perp の 24h 出来高は 57.3 億 USD・中央値 293,928 USD・**234 銘柄中 56 銘柄が出来高ゼロ**。
- 現物は **330 ペア中 232 ペアが出来高ゼロ**・中央値 0。

→ **設計判断**: HIP-3 を「株式も指数も FX も取引できる」と扱うことは**技術的には正しいが、流動性の面では `xyz` の一部銘柄に限られる**。ユニバース定義は「取引可能な銘柄集合」ではなく「**検証可能な銘柄集合**」でなければならない。したがって各ユニバースは **流動性ゲート（最低 24h 出来高）を必須属性**として持ち、ゲートを満たさない銘柄は unavailable として run 記録に残す（ゼロ埋めしない）。

**データ取得 API の実測（キー不要・認証不要）:**

- 単一エンドポイント `POST https://api.hyperliquid.xyz/info`。
- ローソク足: `{"type":"candleSnapshot","req":{"coin","interval","startTime","endTime"}}`。**最新 5000 本のみ**（公式明記・実測でも 5000 超は返らない）。
- 履歴ファンディング: `{"type":"fundingHistory","coin","startTime","endTime"}` → `[{coin, fundingRate, premium, time}]`。**1 時間粒度（実測: 7 日で 168 件・区間 3,599,897 ms ≈ 1h）**。
- OI: `{"type":"metaAndAssetCtxs"}` の asset ctx に `openInterest` が含まれる（**実測で確認済み**。フィールド: `funding` / `openInterest` / `prevDayPx` / `dayNtlVlm` / `premium` / `oraclePx` / `markPx` / `midPx` / `impactPxs` / `dayBaseVlm`）。
- HIP-3 は `dex` 引数で取得: `{"type":"meta","dex":"xyz"}` / `{"type":"metaAndAssetCtxs","dex":"xyz"}`（実測で 126 銘柄分の ctx を取得）。
- 予測ファンディング: `{"type":"predictedFundings"}` → 234 銘柄分。`fundingIntervalHours: 1` を明示（**実測。設計段階で正規化定数を仮定する必要がなくなった**）。

#### 4.2 検証済み（公式 docs 明記）— 金融正確性に関わる定数

- **ファンディング支払いは毎時**。金利成分は **8 時間あたり 0.01% = 1 時間あたり 0.00125%**（= 年率 11.6% をショートが受け取る）。「計算は 8 時間レートに対して行うが、支払いは毎時その 1/8」。
- **ファンディング上限は 4%/時**。上限も区間も資産に依存しない。
- HIP-3 はより応答的なプレミアム式を使う: `premium = (0.5 * (impact_bid_px + impact_ask_px) / oracle_px) - 1`。deployer は funding rate multiplier と interest rate を設定できる。
  - 実測: `xyz` の 126 銘柄中 123 銘柄に funding multiplier（例 `0.5`）が設定され、4 銘柄に interest rate。`hyna`=3、`cash`=17、`para`=29+clamp 3、`io`=8、`flx`=15、`vntl`=15 が multiplier を持つ。`km`/`mkts` は multiplier 0 だが interest rate を持つ（22/5）。
- **手数料**: perp の基準 taker 0.045% / maker 0.015%（14 日出来高で 7 段階）。**HIP-3 は deployer fee share が 0〜300%（growth mode は 0〜100%）上乗せされ、100% 超ならプロトコル手数料も同率に上がる**。growth mode では全込み taker が 0.0045〜0.009% に下がる。
- **強制ロスカット**: 維持証拠金は「最大レバレッジ時の初期証拠金の半分」。最大レバレッジ 3〜40 倍の資産で維持証拠金は 1.25%〜16.7%。清算価格式は `liq_price = price - side * margin_available / position_size / (1 - l * side)`（`l = 1/維持レバレッジ`）。
- **証拠金モード**: cross（既定）・isolated・strict isolated。HIP-3 は "no cross" も持つ。unified account / portfolio margin では同一 collateral の dex 間で cross 証拠金が共有される（standard abstraction では同一 dex 内のみ）。
- **初期証拠金** = `position_size * mark_price / leverage`。レバレッジは 1〜最大レバレッジの整数。
- **資産 ID**: perp は `meta` の index。HIP-3 は `100000 + perp_dex_index * 10000 + index_in_meta`。現物は `10000 + spotInfo["index"]`。HIP-4 outcome は `10 * outcome + side`。
- **履歴データ（公式アーカイブ）**: S3 バケット `hyperliquid-archive` が月 1 回更新。**L2 book スナップショット（`market_data`）と asset contexts（`asset_ctxs`）のみで、ローソク足と現物データは S3 に無い**（公式明記）。fills は `hl-mainnet-node-data/node_fills_by_block`。

#### 4.3 実測で判明した設計上の制約（重要）

**candleSnapshot はページングできない（実測で確定）:**

- 過去の任意窓を指定しても返る本数は常に「直近 5000 本」に切り詰められる。
- 実測: `2024-01-01〜2024-03-01` の 1h 窓 → **0 本**。`BTC` 1h を「5000 本の先頭時刻より前」に遡るチェーン取得 → **2 回目で 0 本**。15m は 5,006 本（約 52 日分）のみ。
- したがって**ローソク足の長期履歴は API では取得できない**。取得できる範囲は次のとおり（実測）:

| 対象 | 1d | 1h | 15m |
|---|---|---|---|
| `BTC`（メイン perp） | 2,232 本（2020-08-19 〜） | 5,001 本（約 208 日） | 5,006 本（約 52 日） |
| `xyz:TSLA`（HIP-3 株式） | 320 本（2025-11-13 〜） | 5,000 本（約 208 日） | 5,006 本（約 52 日） |
| `xyz:GOLD` | 281 本（2025-12-22 〜） | — | — |
| `vntl:SPACEX` | 212 本（〜2026-06-12 で**停止**） | — | — |
| `io:OAI` | 27 本（2026-09-02 〜） | — | — |
| `PURR/USDC`（現物） | 896 本（2024-04-16 〜） | 5,000 本 | — |

→ **設計判断**: HL の 1d 系列は「その資産のローンチ以降・直近 5000 日以内」しか得られない。**日足より細かい粒度は約 208 日（1h）／約 52 日（15m）が上限**である。したがって
1. HL ユニバースの `horizon` は **日足基準（最大 5,000 日）を既定**とする。1h 以下の粒度で検証できる期間は約 208 日と明示し、それを超える期間を要求する run は **fail-closed**（「検証不能」として拒否。短期データで長期を装わない）。
2. **自前蓄積（daily cron）が長期化の唯一の道**である。`candleSnapshot` は「直近を毎日取り込んで自前 store を伸ばす」用途に使う。公式 S3 アーカイブにはローソク足が無いため代替できない。
3. 資産によっては**データが停止している**（`vntl:SPACEX` は 2026-06-12 で最終足）。取得面は「最終バーが古い場合は unavailable」を返す必要がある（欠損を直近値で埋めない）。

**資産の階層強制（実測）:**

- `hyna:BTC` の最終 1d 足は **2026-09-02**（以降ゼロ）だが、`hyna` dex の 24h 出来高は **0**。
- `vntl` / `km` / `flx` などは 1d 足が 2026-06 で止まっている。
- → **出来高ゼロの dex の銘柄は 1d 系列が停止している**。流動性ゲートはデータ可用性ゲートとしても機能する。

#### 4.4 要確認（実装時に確認 — Open Questions にも記載）

- HIP-3 の**手数料実値**（dex ごとの deployer fee share。`perpDexs` には fee share の数値が含まれないため、`userFees` または deployer 行動記録から取得する必要がある）。
- 現物ペアの名前解決（329/330 が `@NNNN` 形式。`spotMeta.universe[].tokens` → `tokens[].name` の 2 段解決が必要）。
- 公式アーカイブの取得範囲・粒度（candles が無いことは確認済み。L2 book の実用性は未検証）。
- 各 dex の**実スプレッド**（`allMids`/`l2Book` からの推定。流動性ゲートの閾値設計に必要）。

#### 4.5 設計判断

- ライセンスクラス: `LicenseClass.PERSONAL_ONLY`（保守的判定）。`docs/CRYPTO.md` の CoinGecko/Binance 判定（2026-09-24 CTO固定・取引所は公式参照レートではないため OFFICIAL_PUBLIC にしない）と同一論理。`SOURCE_POLICIES` に登録する設計とする。
- 市場差の扱い: メイン perp は 24/7 のため「営業日」でなく **UTC 暦日** を使う。HIP-3 は株式カレンダー（24/5）と 24/7 が混在するため、**カレンダーはユニバース属性として明示**する（D3 の表）。`StrategyForwardOutcome`（`src/yowayowa/strategy_models.py`）に **`horizon_unit` の追加フィールドを設計する（additive）**。既存 `horizon_trading_days` の意味は株式系で不変とし、既定値（trading_days 相当）で後方互換を保つ。
- ファンディング計算: バックテストのパーペ損益は、1 時間粒度のファンディングをポジションサイズに応じて加算して計算する。符号規約: **fundingRate > 0 のときロングが支払う**（ロング側は負のキャリー、ショート側は受取り）。正規化は公式明記（毎時支払い・8h レートの 1/8・金利成分 0.00125%/h）に従い、**設計段階で未検証の定数を埋め込まない**。HIP-3 の multiplier / interest rate は銘柄ごとに `perpDexs` から取得して適用する。
- レバレッジ: 入力パラメータとして **上限を明示するのみ**。清算ウォーターフォールはシミュレートしない。レバレッジ設定が破綻条件（清算価格到達相当）に触れた区間は、損益を捏造して続行せず **invalidation 条件として run 記録に残す**。清算価格式は公式式（D 4.2）を用いる設計とし、実装時に検証する。
- 手数料/スリッページ: venue 毎に設定値（maker / taker bps・slippage bps）を明示的に持ち、既定値は公式テーブル（perp 基準 taker 0.045% / maker 0.015%）＋HIP-3 の deployer fee share とする。コスト 0 の「理想的約定」で結果を美化しない（D6 の cost 明示設定のみと対になる）。

### D5. MT5 データプレーン（第2版で新設・実測）

調査結果（2026-09-28・tsukumo の MT5 Vantage Live・account 17128064）に基づく。

#### 5.1 検証済み（実測）

- `symbols_get()` = **1,316 銘柄**。アカウント: login 17128064 / server `VantageTradingLtd-Live` / currency USD / **leverage 1000** / margin_mode 2 / company Vantage Trading Ltd / balance 0.0 / equity 55.86（**実口座・実資金**。発注は行わない）。
- カテゴリ内訳（`symbol_info.path` の第 1 階層・実測）:
  | パス | 銘柄数 | 内容 |
  |---|---|---|
  | `Stocks` | **824** | 米国株 CFD（`Stocks\US\...`） |
  | `247 Product` | 144 | 24/7 商品（株式の時間外を含む） |
  | `ETF` | 73 | ETF CFD |
  | `Crypto Currency` | 68 | 暗号 CFD |
  | `Forex Major` | 46 | 主要 FX |
  | `Forex` | 44 | その他 FX |
  | `Synthetic Pairs` | 38 | 合成ペア |
  | `CFDs.r` | 29 | 指数 CFD |
  | `Synthetic Indices` | 18 | 合成指数 |
  | `Commodities.r` | 13 | コモディティ |
  | `bond CFDs` | 7 | 債券 CFD |
  | `Gold` / `Oil` / `Silver` / `Nikkei` | 4 / 4 / 2 / 2 | 個別 |

- **暗号 CFD は 6 銘柄ではなく 68 銘柄**（`Crypto Currency\Crypto Major\...`）。実測で確認したのは BTCUSD / ETHUSD / SOLUSD / XRPUSD / LTCUSD / ADAUSD だが、パスには 68 銘柄が存在する。
- **米国株 CFD は 7 銘柄ではなく 824 銘柄**。`NVIDIA`/`TSLA`/`AAPL`/`MSFT`/`GOOG`/`AMAZON`/`META` はその一部。
- 指数: `NAS100.r` / `SP500.r` / `DJ30.r` / `GER40.r` / `UK100.r` / `US2000.r` / `ES35.r` / `EU50.r` / `FRA40.r` / `HK50.r` / `CHINA50.r` / `SPI200.r` / `JP225` **は存在しない**（実測で `*225*` は `JPN225ft` と `Nikkei225` のみ）。日本株指数は `Nikkei225` / `JPN225ft`。
- `AUS200.r` は**存在しない**（`SPI200.r` が豪州指数）。

#### 5.2 履歴深度の実測（copy_rates）

| 銘柄 | 1H 本数 | 最古 | 実質年数 |
|---|---|---|---|
| `EURUSD`/`USDJPY`/`GBPUSD`/`AUDUSD` | 60,000 | 2017-02-06 | 約 9.6 年 |
| `XAUUSD`（金） | 53,376 | **2007-06-22** | 約 19 年 |
| `XAGUSD`（銀） | 53,375 | **2007-04-18** | 約 19 年 |
| `XPTUSD.r`/`XPDUSD.r`（白金/パラジウム） | 26,980 / 27,158 | 2022-02/2022-02 | 約 4.6 年 |
| `BTCUSD`/`ETHUSD`/…（暗号 CFD） | 20,000 | 2024-06-10 | 約 2.3 年 |
| `NAS100.r`/`SP500.r`/`DJ30.r` | 20,000 | 2023-05-09 | 約 3.4 年 |
| `GER40.r` | 20,000 | 2023-01-04 | 約 3.7 年 |
| `CL-OIL`（WTI） | 20,000 | 2023-05-11 | 約 3.4 年 |
| `GASOIL-Cr` | 20,000 | 2023-02-06 | 約 3.6 年 |
| `NVIDIA` | 6,576 | 2023-09-05 | 約 3.1 年 |
| `GOOG` | 10,368 | 2021-07-08 | 約 5.2 年 |
| `AMAZON` | 10,608 | 2021-05-18 | 約 5.4 年 |
| `TSLA`/`AAPL`/`MSFT`/`META` | 5,000 | 2024-05-22 | 約 2.4 年 |

- 日足は `XAUUSD` 5,091 本（2007-06-22 〜）・`XAGUSD` 5,140 本・`XPTUSD.r`/`XPDUSD.r` 約 1,180 本・`NVIDIA` 768 本・`GOOG` 1,312 本・`AMAZON` 1,346 本。

#### 5.3 実測で判明した取得契約上の制約（重要）

**copy_rates の要求上限は約 100,000 本で、それを超える要求はエラーになる（実測）:**

- `9999` → 9,999 本 OK。`50000` → 50,000 本 OK。**`100000` → `(-2, 'Terminal: Invalid params')`**（`_TZ` 無し）。
- したがって**「日足を 5,000 本取得」という要求でも、利用可能本数が 5,000 未満の場合に `None` を返すことがある**（実測: `D1 BTCUSD` は `from_pos(0, 5000)` で `None`、`from_now(..., 5000)` で 2,962 本）。**取得面は「要求本数に満たない場合は本数を減らして再試行し、返却本数と最古時刻を必ず記録する」契約とする**。取得失敗を「データが無い」と混同しない。
- D1 は「`from_pos` で大きな本数を要求すると失敗する」が、`copy_rates_from`（現在時刻起点）または 5 年レンジ指定では成功する（実測: `D1 EURUSD` from_now 5,000 本・range 5y 1,489 本）。**取得経路によって得られる本数が異なる**ため、取得面は経路と結果を provenance に残す。

#### 5.4 取引コスト・証拠金の実測（`order_calc_margin` / `order_calc_profit`・実ブローカー値）

`symbol_info` の `margin_initial` は FX 以外でほぼ 0.0 のため**信用できない**（実測）。**ブローカー自身の計算値**を cost/margin モデルの入力にする:

| 銘柄 | 価格 | 契約サイズ | 想定元本(1lot) | 証拠金(1lot) | **実効レバレッジ** | 損益通貨 |
|---|---|---|---|---|---|---|
| `EURUSD` | 1.13674 | 100,000 | 113,674 | **113.67** | **1000 倍** | USD |
| `USDJPY` | 157.213 | 100,000 | 15,721,300 JPY | 100.0 USD | （証拠金は USD 建て） | **JPY** |
| `XAUUSD` | 4,151.14 | 100 | 415,114 | **415.11** | **1000 倍** | USD |
| `XAGUSD` | 61.275 | 5,000 | 306,375 | 3,063.75 | **100 倍** | USD |
| `XPTUSD.r` | 1,735.19 | 10 | 17,352 | 867.60 | **20 倍** | USD |
| `XPDUSD.r` | 1,226.22 | 10 | 12,262 | 613.11 | **20 倍** | USD |
| `CL-OIL` | 94.328 | 1,000 | 94,328 | 188.66 | **500 倍** | USD |
| `GASOIL-Cr` | 1,513.95 | 100 | 151,395 | 7,569.75 | **20 倍** | USD |
| `GAS-Cr` | 3.508 | 42,000 | 147,336 | 7,366.80 | **20 倍** | USD |
| `COPPER-Cr` | 6.5332 | 25,000 | 163,330 | 3,266.60 | **50 倍** | USD |
| `NAS100.r` | 30,509.9 | 1 | 30,509.9 | 61.02 | **500 倍** | USD |
| `SP500.r` | 7,729.53 | 1 | 7,729.53 | 15.46 | **500 倍** | USD |
| `DJ30.r` | 51,621.36 | 1 | 51,621.36 | 103.24 | **500 倍** | USD |
| `GER40.r` | 25,528.13 | 1 | 25,528.13 | 58.04 | **440 倍** | **EUR** |
| `Nikkei225` | 66,118.5 | 1 | 66,118.5 | **0.84** | （要検証） | **JPY** |
| `JPN225ft` | 65,900.0 | 1 | 65,900.0 | **0.84** | （要検証） | **JPY** |
| `UK100.r` | 10,752.01 | 1 | 10,752.01 | 28.50 | **377 倍** | **GBP** |
| `NVIDIA`〜`META`（米株 CFD） | — | 1 | 225〜752 | 11.26〜37.58 | **20 倍** | USD |

- **実効レバレッジはユニバース内でも品目ごとに大きく違う**（貴金属 20〜1000 倍・コモディティ 20〜500 倍・指数 377〜500 倍）。→ D9 のとおり **`leverage` は run の必須入力**であり、ユニバース既定値で暗黙に決めてはならない。
- `Nikkei225`/`JPN225ft` の証拠金 0.84 USD は JPY 建て契約を USD 口座で計算した結果であり、**換算レートを伴わないため異常に見える**。実装時に検証する（Open Question）。
- **スワップ（ロールオーバー）**: `swap_mode` が単位を決める（**1** = points/通貨建て、**2** = interest、**5** = 年率%）。実測例: `EURUSD` swapL=-5.76 / swapS=2.5（mode 1）、`GASOIL-Cr` swapL=194.26 / swapS=-305.28（mode 1）、`NAS100.r` swapL=-6.082 / swapS=1.1225（mode 2 = 年率）、米株 CFD swapL=-6.0 / swapS=2.0（mode 5 = 年率%）。`swap_rollover3days` = 3（FX/コモディティ）または 5（指数/CFD・水曜 3 倍）。
- **`CL-OIL` は swap_mode 0（スワップ無し）**。`JPN225ft` も 0。
- スプレッド: FX 13〜31 points、金 43、銀 31、指数 60〜1,800、白金 1,067、パラジウム 583、米株 CFD 7〜69。**`spread_float` = True**（変動。静的値ではない）。

#### 5.5 カレンダーの実測

- `symbol_info_session_trade` / `symbol_info_session_quote` は **mt5linux 経由では公開されていない**（実測: `MISSING`）。
- 実バーの曜日分布（H1・直近 5,000 本・実測）で代替確認:
  - `EURUSD` / `XAUUSD` / `NAS100.r` / `NVIDIA` → **月〜金のみ**（週末バー無し）= 24/5
  - `BTCUSD` → **月〜日の 7 曜日すべて**（土 692・日 714 本）= 24/7
- → カレンダー契約は「ユニバース属性として明示」＋「実バーの曜日分布で検証可能」とする。休場日の判定をセッション API に依存させない。

#### 5.6 取得経路（実測で確立）

- MT5 は Windows ネイティブ（Wine コンテナ `mt5-gmag11`）で稼働し、**MT5 Python SDK は VPS から直接使えない**（マスター制約・`docs/AUTONOMOUS_AGENT_HANDOFF.md`／COO 実測の記載方針と一致）。実測で確立した経路:
  `VPS → ssh -L 18001:localhost:8001 tsukumo → wine:8001 (mt5linux) → MT5 terminal`
- 実測: トンネル経由で `initialize() = True`・account 17128064 取得・`XAUUSD` H1 5 本取得（OHLC＋tick_volume＋spread）に成功。
- ただし **`mt5linux` と `rpyc` は repo venv（Python 3.13）に入っていない**（実測: `rpyc: False` / `mt5linux: False`。system python 3.12 には存在）。取得面を実装する場合は依存追加が必要（実装タスクの判断事項。本設計では「経路は実証済み・依存は未導入」を明示する）。
- 既知の API 差分（実測）: `symbol_info` に `points` フィールドは無い（`point`）。`copy_rates_*` に `datetime.timezone` を要求する経路がある。
- **ライセンス**: Vantage の MT5 履歴データは取引口座に付随して提供される。`LicenseClass.PERSONAL_ONLY`（保守的判定）として `SOURCE_POLICIES` に登録する設計とする。

#### 5.7 設計判断

- 取得面は `providers/mt5.py` として、既存プロバイダパターン（ProviderDescriptor + enforce_provider_policy・SOURCE_POLICIES 登録・store 冪等・非補間）を踏襲する。**MT5 は VPS から直接到達できないため、取得面は「MT5 ブリッジ（tsukumo）に到達できる実行環境」でのみ有効**とし、到達不能時は fail-closed（unavailable を返す。空データで成功扱いにしない）。
- 取得面の契約: 「要求本数 → 返却本数 → 最古/最新時刻 → 取得経路」を必ず provenance に残す（5.3 の制約への対応）。

### D6. 既存候補・既存指標の再利用

- バックテストの入力ユニバースは次の 3 系統（第2版で拡張）:
  1. 永続化済み screening 候補（run_date キーで再生）
  2. watchlist
  3. 明示シンボルリスト（MT5 シンボル名・HL coin 名・`dex:coin` 形式）
- ファンダメンタル指標計算はバックテスト側で **再実装しない**。既存の canonical metrics 解決サービス（EDINET / SEC）に **as-of 参照** を追加する形で再利用する。
- 設計するもの: `services/pit_fundamentals.py` — as_of 付き PIT アクセサ。契約の形（実装はしない）:
  - 入力: symbol（および EDINET/SEC の発行体識別）・metric family・`as_of`。
  - 出力: 「`as_of` 時点で発見済みの最新 canonical 値」＋その発見日＋provenance。発見済みの値が無ければ **unavailable を返す**（補間しない・ゼロを返さない）。
  - 内部では EDINET/SEC の既存 canonical metrics 解決を呼び出し、発見日（filed / published date）で as-of フィルタする。指標の定義・優先順位・通貨整合の規約は既存実装に一本化する。

これが防止するアンチパターン: バックテスト専用に指標計算を複製し、ライブの canonical 定義と食い違った値で検証すること。

### D7. 実行モデル

- **発注は行わない**（broker への接点ゼロ）。P2 の発注系・interlock とは無関係であり、`submissions_enabled` 等のゲートに触れない。MT5 接続は **読み取り専用**（`symbols_get` / `symbol_info` / `copy_rates` / `order_calc_margin` / `order_calc_profit`）に限定する。`order_send` を呼ばない。
- 出力は次の 3 種（いずれも D8 の run 成果物として保存）:
  - **取引/イベントログ**: 約定 1 件ごとに時刻・ユニバース・シンボル・方向・数量・約定価格・適用コスト（spread/commission/swap）・適用証拠金・実効レバレッジ・判断の根拠イベント（どのバー/文書/ファンディング区間に基づいたか）を記録。
  - **資産曲線**: 時刻ごとの equity（`report_currency` と `leverage_mode` のラベル付き）。
  - **指標**: total return・CAGR・Sharpe・max drawdown・turnover・benchmark 超過。
- 指標の定義（設計段階で固定する意味の規約）:
  - **Sharpe**: risk-free = **0**。年化は株式 = 252・24/7 市場 = 365 と明示する（区間の長さの単位に合わせる）。
  - **CAGR**: 期間長の単位（営業日/暦日）を run 記録に明示した上で年換算する。
  - **max drawdown**: 資産曲線の最高値からの最大下落率。**レバレッジありの run では証拠金が小さいため drawdown が 100% を超えうる**。100% 超の drawdown は「口座消滅」を意味するため、**強制ロスカット到達として扱い、それ以降の区間を invalidation として記録する**（損益を続行しない）。
  - **turnover**: 期間内の売買回転（約定額の集計方法は実装時に確定 — Open Questions）。
  - **benchmark 超過**: ベンチマーク無し（`hl_*` で none 選択）の場合は表示しない（0 と表記しない）。
- cost は明示設定のみ（エンジンに暗黙の既定コストを埋め込まない）。設定無し run は「コスト 0」ではなく「コスト未設定」として区分し、美化を防ぐ。

### D8. provenance / 監査

- 各 run は次を持つ:
  - `run_id` — 成果物ディレクトリの鍵。
  - `engine_version` — エンジン挙動のバージョン。同じ run_id の再現比較に使う。
  - **データマニフェスト** — ソース毎の coverage 窓・行数・EDINET インデックス coverage・provider set。**加えて MT5/HL では「要求本数 → 返却本数 → 最古時刻」**（D5 実測 5.3・D4 実測 4.3 の制約に対応）。
  - **config hash** — 実行設定（ユニバース・期間・コスト・`leverage_mode`・`leverage`・`position_sizing` 等）のハッシュ。同一 config hash = 同一設定の再実行を識別できる。
- 成果物は `data/backtests/{run_id}/` に manifest 付きで保存する（取引/イベントログ・資産曲線・指標・上記マニフェスト）。
- 成果物は **ライブのスコアリングへ自動還流させない**（研究成果物である。ライブ運用に反映させる場合は人間が成果物を読んだ上で別途意思決定する）。

### D9. レバレッジ・証拠金・強制ロスカットのモデル化（第2版で新設・本改訂の核心）

レバレッジ取引を正しく検証できない基盤は、FX・貴金属・指数・暗号 CFD・HL パーペという本基盤の対象の大半を扱えない。以下を固定する。

#### 9.1 語彙と入力（run の必須入力）

| 入力 | 値 | 意味 |
|---|---|---|
| `leverage_mode` | `unlevered` / `levered` | レバレッジをモデル化するか（必須・既定なし） |
| `leverage` | 整数 ≥ 1 | 建玉ごとの設定倍率。最大は**実測値**（D5 5.4 / D4 実測の `maxLeverage`）を上限とする |
| `position_sizing` | `margin` / `notional` / `fixed_units` | サイズ決定の基準 |
| `margin_mode` | `cross` / `isolated` | 証拠金モード（既定 `isolated` — 1 建玉の破綻を他に波及させない） |
| `maintenance_margin_ratio` | 比率 | 維持証拠金率。既定は「初期証拠金の半分」（HL 公式）またはブローカー実測 |
| `stop_out_level` | 比率 | 強制ロスカット水準（MT5 `margin_so_call`=50% / `margin_so_so`=10% — 実測） |
| `liquidation_model` | `none` / `stop_out` | 強制ロスカットをモデル化するか |

#### 9.2 会計恒等式（不動点として固定する）

1. **証拠金**: `margin = notional / leverage`（HL 公式: `position_size * mark_price / leverage`）。MT5 側は `order_calc_margin` の実測値を使う（`symbol_info.margin_initial` は FX 以外で 0.0 のため使わない — D5 5.4 実測）。
2. **equity**: `equity = balance + unrealized_pnl + 累積swap/funding`（**`report_currency` 建て**。
   異なる `currency_profit` の損益は D3 3.2 の換算規律に従って換算する）。
3. **維持証拠金**: `maintenance_margin = notional * maintenance_margin_ratio`。
4. **強制ロスカット判定（MT5 型）**: `equity / margin_used * 100 <= stop_out_level` で全建玉を強制決済。
5. **強制ロスカット判定（HL 型）**: `equity < maintenance_margin * total_open_notional` で清算。清算価格は公式式
   `liq_price = price - side * margin_available / position_size / (1 - l * side)`、`l = 1/維持レバレッジ`。
   - `margin_available(cross) = account_value - maintenance_margin_required`
   - `margin_available(isolated) = isolated_margin - maintenance_margin_required`
6. **実効レバレッジ**: `notional / margin`。**設定 `leverage` と実効レバレッジは一致しない**（実測: `XAGUSD` は口座 1000 倍でも実効 100 倍、`XPTUSD.r` は 20 倍）。run 記録には **両方**を残す。

#### 9.3 コスト・キャリーの分離（証拠金コストと取引コストを混ぜない）

| コスト種別 | MT5 | HL | 適用タイミング |
|---|---|---|---|
| 取引コスト | `spread`（実測 points）＋ commission | maker/taker bps（`userFees`・HIP-3 は deployer share 加算） | 約定時 |
| キャリー（正） | **swap**（建玉持ち越し。`swap_mode` が単位を決める。水曜 3 倍） | **funding**（毎時。8h レートの 1/8。金利成分 0.00125%/h） | 持ち越し時 |
| 借入コスト | 上記 swap に内包 | 上記 funding に内包 | — |

- **`swap_mode` の解釈は実装時に確定する**（1 = points・2 = interest・3 = 通貨・4 = ？・5 = 年率%）。実測値の目視整合（`GASOIL-Cr` swapL=194.26 vs 想定元本 151,395）から **mode 1 は points × 契約サイズ**の可能性が高いが、**設計段階で断定しない**（金融正確性優先・Open Question）。
- スワップは **`swap_rollover3days`（実測 3 または 5）で水曜（または金曜）が 3 倍**になる。日付境界はブローカーのサーバー時刻に従う（実測 TZ=Asia/Tokyo のコンテナ。実装時にサーバー時刻を確認）。
- **レバレッジの資金調達コストを無視しない**: レバレッジ 1000 倍で 1 晩持ち越すと、損益通貨ベースでスワップが想定元本に比例して効く。`EURUSD` swapL=-5.76（mode 1）は 1 lot あたりの持ち越しコストとして run に計上する。

#### 9.4 run 単位の明示（混ぜない規律）

- **`unlevered` と `levered` を 1 つの比較表の同一列に置かない**。レポートは `leverage_mode` で必ずグループ化する。
- **異なる `leverage` の run の指標を単一の「合計」「平均」に集約しない**。レバレッジは戦略の一部ではなく**設定**であり、設定の異なる結果を合算することは意味を持たない。
- 同一戦略のレバレッジ感度は「`leverage` のみが異なる run 群」として並べる（`config hash` の差分が `leverage` のみ）。
- invalidation（強制ロスカット到達・清算価格到達相当）が発生した run は、**その時点で損益を打ち切り、以降を未評価として明示する**。破綻した run の資産曲線を「その後も同じ戦略が続いた」かのように伸ばさない。

#### 9.5 この設計が防止するアンチパターン

1. **レバレッジの無自覚な混在**: 20 倍の run と 500 倍の run のリターンを並べて「戦略は儲かる」と結論する。
2. **証拠金コストの欠落**: レバレッジとスワップ/funding を計上せず、コスト 0 の世界で高レバレッジ戦略を「高リターン」と表示する。
3. **破綻の隠蔽**: 強制ロスカットに到達した run を、到達していない run と同じ列で平均する。
4. **設定倍率と実効レバレッジの混同**: 口座レバレッジ 1000 倍を「全商品 1000 倍で検証した」と記述する（実測では貴金属 20〜1000 倍・指数 377〜500 倍・米株 CFD 20 倍と大きく異なる）。

### D10. 提案モジュール構成（名前のみ・コードなし）

- `providers/hyperliquid.py` — 取得面。`docs/CRYPTO.md` / `docs/STOCKS.md` の provider パターン（ProviderDescriptor + enforce_provider_policy・SOURCE_POLICIES 登録・store 冪等・非補間）を踏襲する。`meta` / `perpDexs` / `spotMeta` / `candleSnapshot` / `fundingHistory` / `metaAndAssetCtxs`（`dex` 対応）を扱う。
- `providers/mt5.py` — 取得面（`copy_rates_*` / `symbol_info` / `order_calc_*`）。MT5 ブリッジ（tsukumo）到達を前提とし、到達不能時は fail-closed。
- `services/pit_fundamentals.py` — as-of アクセサ（D6）。
- `services/backtest_service.py` + バックテストエンジン群 — 実装時の配置は実装タスクで確定する（エンジン内部のクラス構成は本設計で固定しない）。
- `services/margin_model.py` — D9 の証拠金・強制ロスカット・キャリー計算（実装時の配置は実装タスクで確定）。
- `data/backtests/{run_id}/` — 成果物（D8）。
- API: `POST /v1/backtest/runs`・`GET /v1/backtest/runs/{id}` — personal モード専用・public は 404 fail-closed（CRYPTO/STOCKS 前例と同じ）。
- CLI: `yowayowa backtest-run` / `yowayowa backtest-show`。
- agent tools: `get_backtest_run` 等の読み取り系。catalog 追加は API/agent parity の CI ピン留め（CG-20260925-003）更新を伴う旨を注記する。

**API / CLI 契約の形状（名前と意味のみ・スキーマ定義は実装タスクで確定）:**

- `POST /v1/backtest/runs` の入力（想定）: `universe_id`（D3 の ID）・評価期間（開始/終了）・入力ユニバースの系統（D6 の 3 系統のいずれかとその識別子）・cost 設定（未設定なら「コスト未設定」区分）・ベンチマーク選択（`hl_*` は none 許容）・**`leverage_mode`（必須）**・**`leverage`（`levered` のとき必須）**・`position_sizing`・`margin_mode`・`slippage_bps`。応答は `run_id` と受付状態。
- `GET /v1/backtest/runs/{id}` の出力（想定）: run の状態・config の要約（`leverage_mode`/`leverage`/`report_currency` を含む）・データマニフェストの要約（要求本数/返却本数/最古時刻）・指標・invalidation の有無と時点・成果物一覧。失敗 run も状態として区別して参照可能にする（黙って消さない）。
- CLI `backtest-run` は同等の入力を引数で受け、`backtest-show` は run の要約を表示する。API と CLI は同じ service 層を共有する（API-first 規約の踏襲）。
- 書き込み系は POST のみ。削除・上書きの面は持たせない（run 成果物は追加のみ・追跡可能にする）。

**想定する実行フロー（承認後・提案・コードなし）:**

1. データ取得が日次で回り、OHLCV・funding が store に蓄積する（機能提案 A・CRYPTO.md の cron 運用パターン準拠。MT5 はブリッジ経由）。**HL の長期化はこの蓄積が唯一の道**（D4 4.3）。
2. オペレーター（またはエージェント）が CLI / API で run を作成する。config hash とデータマニフェストが生成される（D8）。
3. エンジンがイベントを消費し、約定・証拠金・スワップ/funding を計上しながら成果物 3 種（D7）を `data/backtests/{run_id}/` に書き出す。強制ロスカット到達時は invalidation として記録し打ち切る（D9）。
4. オペレーター / エージェントが `backtest-show` / `get_backtest_run` で成果物を読む。agent tools は読み取り系のみ（D10）。
5. 成果物はライブのスコアリングへ自動還流しない（D8）。scoring version を変える判断は人間が行う。

## 3. 優先順位付き機能提案（7件・第2版）

工数（S/M/L）と価値（高/中/低）は設計段階の目安であり、確定した実装計画ではない。

### 3.1 提案一覧

| 優先 | 機能 | 工数 | 価値 | 依存 | 根拠 |
|---|---|---|---|---|---|
| A | Hyperliquid データ取得面（perp/spot/HIP-3 の ohlcv+funding 永続化・CRYPTO.md パターン） | M | 中〜高 | なし | パターンが既存で即着手可能・HL 研究の全ての前提。**長期化は自前蓄積が唯一の道**（D4 4.3） |
| B | PIT ファンダメンタル accessor（as-of・EDINET/SEC） | M | 高 | なし | バックテストの正確性の要・単体でも「当時何が既知か」に正確に答えられる |
| **A2** | **MT5 データ取得面（FX/貴金属/コモディティ/指数/米株 CFD の copy_rates 永続化・トンネル経路）** | **M〜L** | **高** | **なし** | **マスター指摘の中心。9.6 年〜19 年の長期履歴（金 2007 年〜）が既に存在する。HL が持たない長期 FX/貴金属/指数をここで確保する** |
| **C0** | **証拠金・レバレッジ・強制ロスカットのモデル（`services/margin_model.py`・D9）** | **M** | **高** | **なし** | **本改訂の核心。FX(1000倍)・貴金属(20〜1000倍)・指数(377〜500倍)・HL(3〜50倍) の損益を正しく比較する前提。単体テスト可能** |
| C | バックテストエンジン MVP（株式ユニバース先・HL/MT5 後続） | L | 高 | B, C0 | P3 の walk-forward 未着手部分の直接実現 |
| D | アラートルール拡張（信用残急変・IR キーワード・価格/funding 閾値 → Telegram） | S〜M | 高 | なし | 日常運用の情報優位に直結・既存 alerts の延長 |
| E | IR イベントスタディ（文書到着 → 前後リターン窓） | S | 中 | B, C | 研究の質を上げる薄い分析レイヤ |

### 3.2 各機能の詳細

- **A: Hyperliquid データ取得面** — `candleSnapshot`（OHLCV）と `fundingHistory` の 2 系統を、メイン perp / 現物 / HIP-3（`dex` 指定）の 3 層で取得し、CRYPTO/STOCKS と平行構造の store に provider 別で永続化する。`SOURCE_POLICIES` への登録・provenance 付与までを範囲に含む。BTC/ETH 同様、取得と解析は分離する。**流動性ゲート（24h 出来高）を銘柄選択の必須条件として組み込む**（D4 4.1 の実測: 294 銘柄中 144 が出来高ゼロ）。
- **A2: MT5 データ取得面** — MT5 ブリッジ（tsukumo・SSH トンネル）経由で `copy_rates` を取得し、`data/mt5-ohlcv/{SYMBOL}/ohlcv.jsonl` に provenance 付きで永続化する。対象: FX（24/5・2017 年〜）・貴金属（24/5・金/銀 2007 年〜）・コモディティ・指数（24/5・3〜4 年）・米株 CFD（24/5・2〜5 年）。**取得契約（要求本数/返却本数/最古時刻/経路）を provenance に残す**（D5 5.3）。VPS からはブリッジ到達が必要（未達時 fail-closed）。依存（`mt5linux`/`rpyc`）の追加判断を含む。
- **C0: 証拠金・レバレッジ・強制ロスカットのモデル** — D9 の会計恒等式（証拠金・equity・維持証拠金・清算判定・実効レバレッジ）とキャリー（MT5 swap / HL funding）を純関数として実装する。**エンジン MVP の前に単体で検証できる**（実測済みの実ブローカー値 `order_calc_margin`/`order_calc_profit` をテストの期待値にできる点が大きい）。`report_currency` 換算の規律（D3 3.2）もここで実装する。
- **B: PIT ファンダメンタル accessor** — `services/pit_fundamentals.py`。EDINET/SEC の既存 canonical metrics 解決に as_of 参照を追加する契約。バックテストなしでも単体で「当時何が既知か」に正確に答えられるため、単独でも価値が立つ。
- **C: バックテストエンジン MVP** — D1〜D10 の契約を実装する。株式ユニバース（jp/us いずれか）を先とし、`mt5_*` / `hl_*` プレーンの接続は後続カードに分離する。最初の MVP では total return・CAGR・max drawdown・benchmark 超過までを出せればよく、指標の充実は漸進でよい。**`unlevered` を先に完成させ、`levered` は `C0` の上に載せる**。
- **D: アラートルール拡張** — 既存 alerts の延長として、信用残急変・IR キーワード・価格/funding 閾値の検出ルールを追加し、Telegram（既存の通知経路）へ届ける。バックテスト本体に依存しないため独立して着手可能。
- **E: IR イベントスタディ** — P1C で取得済みの IR 文書到着をイベントとし、イベント前後のリターン窓を集計する薄い分析レイヤ。B（当時の既知情報の確定）と C（リターン窓の計算基盤）に依存するため最後。

### 3.3 実装着手条件とタスク分割の目安

各機能とも **マスター承認後に実装着手する**（本表は準備物の優先順位であり、着手承認を意味しない）。タスク分割の目安は **1 機能 = 1 カード**（A は「メイン perp」→「HIP-3」、C は `unlevered` → `levered`、`hl_*`/`mt5_*` 接続は各々別カードに分離）。承認されたカードは通常の検証契約（`make verify`・実データ受入）に従う。

## 4. ロードマップとの整合

### 4.1 P3 / P4 への接続

| 提案 | 接続先 | 接続先の現状（2026-09-26 再ベースライン時点） |
|---|---|---|
| C / C0 | P3 — walk-forward / out-of-sample・portfolio-aware sizing の土台 | NOT STARTED |
| B | P4 — EDINET/SEC ファンダメンタルの検証面・P3 の PIT 入力 | canonical metrics は DONE、as-of 参照は未整備 |
| A | P4-E 系 — crypto データ面の拡張（検証対象データの追加） | P4-E phase 1（BTC/ETH OHLCV）DONE、HL は未着手 |
| A2 | P4-E 系 — MT5 経由の多資産データ面（**レバレッジ検証の前提**） | 未着手（MT5 は対話接続のみ実績） |
| D | P4 — 信用残・IR の日常消費面（alerts） | 週次信用残 DONE・日次 JPX は 2026-09-28 から形式確定待ち |
| E | P4/P5 — IR 蓄積の研究消費面 | P1C 取得 DONE・イベントスタディは未着手 |

- 機能提案 C は既存 `services/strategy_calibration.py` の集計キャリブレーション（rank IC・decile 統計等）を **置き換えない**。既存の注意書き（重複するフォワード窓を独立扱いしている旨の bucket notes）はそのまま残り、本基盤はその解消に使える out-of-sample 評価の面を提供する。
- 機能提案 A・B・D・E は P4（日本市場情報優位）および P4-E 以降に広がった crypto / US 株データ面を、「取得」から「検証」へ進める面を提供する。
- **第2版で追加した A2 / C0 / `mt5_*` / `hl_*` は、P4-E（データ面の拡張）と P3（検証面）の交点に新しい軸を足す**。すなわち「株式のみ・レバレッジなし」の検証から「多資産クラス・レバレッジあり」の検証へ検証対象空間を広げる。これはロードマップの完了率を直接押し上げる主張ではない（コード進捗 0 の文書である）。

### 4.2 完了率ベースライン・frozen 項目との整合

- 完了率ベースラインは 67%（2026-09-26 再ベースライン）。本設計はコード進捗 0 の文書であり、ベースラインを変えない・水増ししない。実装承認後は各 P3/P4 項目の status 語彙（NOT STARTED → IN PROGRESS 等）で進捗を追跡する。
- 公開 SaaS 等 frozen 項目には触れない。バックテスト面は personal モード専用 API（public は 404 fail-closed）であり、フリーズ方針と矛盾しない。
- 人間ブロック項目（real-session / live-broker 受け入れ）と無関係であり、本設計の実装がブロックを解消する主張もしない。完了率の最大控除要因（real-session 受け入れ）は本基盤では変動しない。
- **本設計は発注機能を含まない**（D7）。MT5 の実資金口座（equity 55.86 USD・実測）に読み取り以外で触れない。`order_send` を実装対象にしない。

### 4.3 実行開始条件

マスター方針は「（発注などの実行は）十分に機能が育ちデバッグが済んでから」。本設計は **育成段階の準備物** であり、実行フェーズ（発注・資金を動かす運用）の開始を提案しない・前提にしない。バックテスト機能自体の実装も、各機能のマスター承認後に着手する（§3.3）。

## 5. Open Questions（実装時に確定する事項）

1. **MT5 `swap_mode` の解釈**（1/2/3/4/5 の単位）。実測値からの目視整合は mode 1 = points 系を示唆するが、**設計段階で断定しない**。`symbol_info` の `swap_rollover3days`（実測 3/5）とサーバー時刻を含めて実装時に確定する。
2. **MT5 スワップの PIT 性**: `symbol_info` が返すのは**現在の**スワップ値であり、過去の値ではない。過去のスワップをどう扱うか（現在値で近似するか、取得履歴を貯めるか、スワップ除外を明示するか）。D2 のとおり「現在のスワップ値を過去に一律適用」は先読みに当たるため、既定は **swap 除外＋run 記録への明示** とし、蓄積が進んだ段階で有効化する。
3. **`Nikkei225`/`JPN225ft` の証拠金 0.84 USD**（JPY 建て契約・USD 口座の換算結果）。異常に見えるため実装時に検証する。
4. **MT5 依存の導入**: `mt5linux`/`rpyc` を repo venv（3.13）へ入れるか、取得を別プロセス（system python 3.12）に分離するか。経路（SSH トンネル）は実証済み・依存は未導入。
5. **MT5 ブリッジの可用性契約**: tsukumo の MT5 コンテナ（`mt5-gmag11`）は常時稼働が前提か。停止時の取得面挙動（fail-closed の粒度）。
6. **HIP-3 の手数料実値**: `perpDexs` に deployer fee share の数値が含まれない。`userFees` か deployer 行動記録から取得する必要がある。dex ごとに異なる。
7. **HIP-3 の流動性ゲート閾値**: 実測で 294 銘柄中 144 が出来高ゼロ。ゲートを「24h 出来高 ≥ X USD」で切る場合の X と、判定時刻（UTC 00:00 時点か直近 24h か）。
8. **HL 現物ペアの名前解決**: 330 ペア中 329 が `@NNNN` 形式。`spotMeta.universe[].tokens` → `tokens[].name` の 2 段解決を実装時に確定する。`HYPE/USDC` の `candleSnapshot` が HTTP 500 を返した（実測。`@107` では成功）ため、名前・ID の正規化が必要。
9. **HL の長期履歴戦略**: `candleSnapshot` はページング不可（実測）。1h は約 208 日・15m は約 52 日が上限。自前蓄積（daily cron）の開始時点と、蓄積前の run をどう扱うか（fail-closed の範囲）。
10. **公式 S3 アーカイブの実用性**: candles が無いことは確認済み。L2 book（`market_data`）と asset ctxs（`asset_ctxs`）の粒度・期間・取得コスト（requester pays）を実測する。
11. **`pit_fundamentals` のスナップショットキャッシュ要否**: SQLite テーブルを新設するか都度計算か。run 数・as-of 参照粒度が増えた場合の計算量次第で確定する。
12. **`hl_*` のベンチマーク扱い**: none / BTC / 同一原資産。比較の意味（何と比べるか）が確定するまで none を既定として設計する。
13. **株式ユニバースの長期日足ソース**: 株式バックテストに必要な過去日足の供給面（既存 store の coverage は run 単位の蓄積で、長期履歴をどこまで持つか）を確定する。エンジン MVP（C）着手前の前提確認。**MT5 の米株 CFD（824 銘柄・最長 5.4 年）を代替に使えるかは、CFD と現物の差（配当・議決権・価格連動）を明示できる場合に限る**。
14. **turnover の集計方法**（追加提案）: 約定額ベースか保有回転ベースか。D7 の指標定義として実装タスクで確定する。
15. **cross margin のモデル化範囲**: 1 run 内に複数建玉を持つ場合の cross 証拠金（HL の unified account / portfolio margin・MT5 の margin_mode 2）を v1 で扱うか。D9 の既定は `isolated`。cross は「建玉間の証拠金共有」という非自明な結合を持つため、v1 では対象外とし明示する。

## 6. 参照

- 公式 docs:
  - Hyperliquid info endpoint: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint
  - Hyperliquid funding（毎時支払い・0.00125%/h・上限 4%/h・HIP-3 プレミアム式）: https://hyperliquid.gitbook.io/hyperliquid-docs/trading/funding
  - Hyperliquid fees（perp 0.045%/0.015%・HIP-3 deployer share 0〜300%・growth mode）: https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees
  - Hyperliquid liquidations（維持証拠金・清算価格式・partial liquidation）: https://hyperliquid.gitbook.io/hyperliquid-docs/trading/liquidations
  - Hyperliquid margining（cross/isolated・初期証拠金・HIP-3 の証拠金モード）: https://hyperliquid.gitbook.io/hyperliquid-docs/trading/margining
  - Hyperliquid asset IDs（HIP-3 = 100000 + dex_index*10000 + index）: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/asset-ids
  - Hyperliquid HIP-3（builder-deployed perpetuals）: https://hyperliquid.gitbook.io/hyperliquid-docs/hyperliquid-improvement-proposals-hips/hip-3-builder-deployed-perpetuals
  - Hyperliquid historical data（S3 archive・candles 無し）: https://hyperliquid.gitbook.io/hyperliquid-docs/historical-data
  - Hyperliquid WebSocket subscriptions（candle/l2Book/fastAssetCtxs 等）: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket/subscriptions
- 実測証跡（本設計の数値の出所）:
  - MT5: tsukumo の MT5 Vantage Live（account 17128064 / `VantageTradingLtd-Live`）に対し、`symbols_get` / `symbol_info` / `copy_rates_from_pos` / `copy_rates_from` / `copy_rates_range` / `order_calc_margin` / `order_calc_profit` / `account_info` を実行（2026-09-28）。経路は `VPS → ssh -L → mt5linux(8001) → MT5`。
  - Hyperliquid: 公開 `POST https://api.hyperliquid.xyz/info` に対し `meta` / `perpDexs` / `spotMeta` / `metaAndAssetCtxs`（`dex` 指定含む）/ `candleSnapshot` / `fundingHistory` / `predictedFundings` を実行（2026-09-28）。
  - COO 能力マップ: `/root/.hermes/docs/HYPERLIQUID_CAPABILITY_MAP_20260928.md`（Molt・2026-09-28）
- repo 内:
  - `docs/PRIVATE_OPERATOR_ROADMAP.md` — P3/P4・完了率ベースライン（67%, 2026-09-26）・frozen 項目の正本
  - `docs/CRYPTO.md` — provider パターン・`LicenseClass.PERSONAL_ONLY` 判定（2026-09-24 CTO固定）・public 404 fail-closed の前例
  - `docs/STOCKS.md` — Alpaca OHLCV・crypto と平行の store 構造の前例
  - `docs/DATA_POLICY.md` — LicenseClass と public-mode 規則
  - `docs/EDINET.md` — 提出履歴インデックス・canonical metrics 契約
  - `docs/SEC.md` — Company Facts 正規化契約（filed + accession）
  - `src/yowayowa/services/licensing.py` — `SOURCE_POLICIES`（新 provider の登録先）
  - `src/yowayowa/domain.py` — `LicenseClass` / `Provenance`
  - `src/yowayowa/services/screening_pipeline.py` — screening 候補の run_date 永続化
  - `src/yowayowa/strategy_models.py` — `StrategyForwardOutcome`（`horizon_unit` 追加先）
  - `src/yowayowa/services/strategy_calibration.py` — 集計キャリブレーションの現状（置き換え対象外）
  - `AGENTS.md` — リポジトリ運用契約（不変条件・必須検証）
