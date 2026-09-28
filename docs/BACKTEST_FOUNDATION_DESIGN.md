# Backtest foundation design（バックテスト研究基盤 設計）

> **本文書は設計提案である。実装・実行はマスターの明示的承認後に着手する。** 承認まではコード・スキーマ・cron を一切変更しない。

- 状態: 設計提案（design only）。実装コード 0 行・テスト 0 本。
- 作成日: 2026-09-28（JST）
- 筆者: Hermes worker（CTO-Hephaestus 指揮下の文書化作業）
- 読者: マスター（承認者）・CTO・実装担当エージェント
- 関連文書: `docs/PRIVATE_OPERATOR_ROADMAP.md`（P3/P4）・`docs/CRYPTO.md`・`docs/STOCKS.md`・`docs/EDINET.md`・`docs/SEC.md`

## 0. 用語

- **PIT（point-in-time）**: 時点 t の判断に使える情報を「時点 t で既に発見・公表されていたもの」に限定する原則。
- **ユニバース（universe）**: 1 回のバックテストで対象とする銘柄/市場の集合。`universe_id` で識別し、通貨・カレンダー・ベンチマーク方針を伴う。
- **ホライズン（horizon）**: エントリから評価までの期間。株式は trading_days・`hl_perp` は calendar_days で数える（D4）。
- **run**: バックテスト 1 回の実行単位。`run_id`・config・データマニフェスト・成果物を 1 組に束ねる（D7）。
- **発見日（discovery date）**: システム（または市場参加者）がその情報を初めて参照可能になった日。会計期間の末日ではない（D2）。

## 1. 目的とスコープ

### 1.1 目的

投資バックテストを「正しく」行える研究基盤を設計すること。本設計が定めるのは、過去データ上で戦略・シグナルを検証するための契約（データ参照の規約・成果物の形・監査可能性）であり、バックテストの実行はまだ行わない。

「正しく」の最小要件は次の 2 点に集約される。

1. **Point-in-time（PIT）**: シミュレーション内のあらゆるデータ参照は「その時点 t で既知だったもの」に限定する（D2）。
2. **監査可能性**: どの run も、どのデータ（どの provider・どの coverage 窓）で・どの設定で回ったかを後から完全に再構成できる（D7）。

### 1.2 なぜ今この基盤か

- P3 の集計キャリブレーション（`services/strategy_calibration.py`）は既にコードとして存在し、rank IC や decile 統計を提供している。しかしロードマップのとおり **walk-forward / out-of-sample 評価は NOT STARTED** であり、「そのスコアは当時の情報だけから付けられて、その後本当に効いたのか」を検証する面が無い。
- P4 で EDINET 提出履歴・週次/日次信用残・IR 文書・crypto/US 株 OHLCV というデータ面が蓄積してきた。蓄積は検証されて初めて情報優位になる。取得面が先に育ち、検証面が未整備の状態を解消するのが本基盤の役割である。

### 1.3 ロードマップとの接続

- **P3（収益性/学習ループ）**: `docs/PRIVATE_OPERATOR_ROADMAP.md` の P3 は 2026-09-26 再ベースライン時点で「aggregate calibration DONE in code — walk-forward/out-of-sample and portfolio-aware sizing NOT STARTED」である。本基盤（特に機能提案 C）は、この NOT STARTED 部分（walk-forward / out-of-sample 評価・portfolio-aware sizing の土台）を直接実現する。
- **P4（日本市場情報優位）**: EDINET 提出履歴・信用残・IR・（追加で）Hyperliquid パーペという private/検証データの蓄積を、screener のその場の判定ではなく「当時何が既知で、その後何が起きたか」の検証対象に変換する（機能提案 B・D・E）。

### 1.4 スコープ外（v1 ではやらない）

以下は本設計の v1 スコープ外とする。実装提案の対象にもしない。

- パラメータ最適化（grid / random / Bayesian、過学習管理を含む）
- 機械学習ファクトリモデル
- オプション
- マージン解滅シミュレーション（信用取引の追証・HL の解滅ウォーターフォール再現。D4 のとおり invalidation 条件の記録のみ）
- 複数通貨の混合計算（エンジン内 FX 変換。D3 のとおりユニバース毎に通貨固定）
- 公開 SaaS 機能（ロードマップで frozen。バックテスト面は personal モード専用とする — D8）

## 2. 設計判断（CTO固定・2026-09-28）

この節の D1〜D8 は CTO による固定判断であり、提案ではない。変更・追加の案はすべて「## 5. Open Questions」または実装タスク側に分離し、この節を直接書き換えない。

### D1. 単一イベントエンジン・複数データプレーン

- バックテストの核は **1 つの point-in-time イベントエンジン** とする。エンジンの責務は「イベント（新しいバーの確定・新しい文書の到着・新しいファンディング区間の確定）が到着したとき、時点 t で既知の情報のみで判断し、次の実行可能価格で約定を記録する」ことのみとする。
- 株式（JP/US）と Hyperliquid パーペはエンジンに対する **アダプタ** である。アダプタが供給するのは:
  - 市場カレンダー（株式 = 営業日・`hl_perp` = UTC 暦日）
  - バー系列（決済済みバーのみ。未確定バーは供給しない）
  - コスト設定（maker/taker bps・slippage bps — D4/D6）
  - 指標の年化スケール（株式 252 / `hl_perp` 365 — D6）
- **1 ユニバース内に異市場のデータを混ぜない**。株式ユニバースに crypto のバーを混ぜない。HL ユニバースに株式ファンダメンタルを参照させない。これは `docs/CRYPTO.md` の「2 source は混ぜない」設計判断（2026-09-24 CTO固定）と同趣旨の分離規律である。

この構成が防止するアンチパターン: 市場ごとに場当たり的に書かれた「バックテストもどき」ループが乱立し、市場ごとに約定・コスト・時刻の扱いが微妙に異なって比較不能になること。エンジン 1 本に約定・PIT・コストの意味を集約し、市場差はアダプタの差分としてだけ表現する。

### D2. Point-in-time（PIT）原則

シミュレーション内のデータ参照は必ず「時点 t で既知だったもの」に限定する。ソース別の規約:

| ソース | 発見時刻の扱い | 再利用する既存面 | 禁止する先読み |
|---|---|---|---|
| EDINET | 書類の **発見日（filed / published date）** | `edinet_filings` インデックス・`index_coverage_complete` ゲート | 会計期間の末日を発見日とみなすこと（期末決算を期末時点で既知扱いにする） |
| SEC | Company Facts の各ファクトが保持する **filed + accession** | 既存プロバイダ（`docs/SEC.md` の正規化契約） | 最新の修正後値を過去の時点に遡って適用すること |
| OHLCV (stock/crypto) | バー T の close は T の確定時刻に既知 | 既存 store（冪等・非補間） | 確定していないバーの参照・T の close での約定 |
| P1C IR 文書 | provenance の **published_at** | P1C の文書 provenance | 収集日を公開日と混同すること |
| screening 候補 | **run_date** キーで永続化済み | `persist_screening_run` / `read_screening_candidates` | 過去 run の再解釈（永続値をそのまま使う） |

- OHLCV の約定規約: バー T の close で決定した売買は **T+1 の open で約定** する（最小 1 バー遅延）。zero-fill / forward-fill は禁止する（既存 store の冪等・非補間方針 — `docs/CRYPTO.md`「API の欠落日は zero/fwd-fill しない」— と整合）。データが欠けている区間は「取引できない区間」として扱い、値を捏造して埋めない。
- `index_coverage_complete` ゲートの意味: 部分的なインデックス coverage を「最新年報が揃っている」ことの根拠に使わない。coverage が不完全な期間はファンダメンタル参照が unavailable になる（ゼロや直近値で埋めない）。
- screening 候補は run_date キーで永続化済みのため過去 runs を再生可能。エントリ判断の時点は T+1 規約に従う。

### D3. ユニバース / 通貨の分離

- `universe_id` は次の 3 種のみ: `jp_equity` / `us_equity` / `hl_perp`。
- 通貨はユニバース毎に固定: `jp_equity` = JPY・`us_equity` = USD・`hl_perp` = USD。**エンジン内で FX 変換を行わない**。
- クロスユニバース比較は **通貨ラベル付きレポート** でのみ行う（JPY の数値と USD の数値を 1 つの数値列に混ぜない）。
- ベンチマーク: 株式側は既存 `REGION_BENCHMARKS`（`services/strategy_outcomes.py`: us → ^GSPC・jp → ^N225・gb → ^FTSE）を株式ユニバースで再利用する。`hl_perp` はベンチマーク **任意**（`none` を許容し、選択を run 記録に明示する）。
- ユニバース定義が持つ属性（設計上の契約）: `universe_id`・市場種・通貨・カレンダー（営業日/UTC暦日）・ベンチマーク方針・入力系統（D5 の 3 系統のいずれか）。

この分離が防止するアンチパターン: 異通貨の損益を無自覚に合算して「合計リターン」を名乗ること。FX をエンジンに持ち込まないことで、通貨の混在は必ずレポート層の明示的な選択として表面化する。

### D4. Hyperliquid データプレーン

調査結果（2026-09-28・公式 docs 確認）に基づく。検証済み事実と実装時確認事項を混ぜない。

**検証済み（公式 docs 明記）:**

- 単一の公開エンドポイント `POST https://api.hyperliquid.xyz/info`。API キー不要・認証不要。
- ローソク足: `{"type":"candleSnapshot","req":{"coin","interval","startTime","endTime"}}`。**最新 5000 本のみ**（公式明記）。日足であれば 2023 年ローンチ以降の全履歴が 5000 本内に収まる想定（実機確認は実装時 — Open Questions）。
- 履歴ファンディング: `{"type":"fundingHistory","coin","startTime","endTime"}` → `[{coin, fundingRate, premium, time}]`。1 時間粒度。

**要確認（実装時に公式 docs で確認 — Open Questions にも記載）:**

- OI（open interest）は assetCtxs 系レスポンスに含まれる **想定**（正確なレスポンス型は実装時に確認）。
- ファンディングの正規化定数（8h レートと時間レートの関係）。**設計段階で未検証の定数を埋め込まない**（金融正確性優先）。
- 手数料テーブルの既定値（maker / taker bps・slippage）。

**設計判断:**

- ライセンスクラス: `LicenseClass.PERSONAL_ONLY`（保守的判定）。`docs/CRYPTO.md` の CoinGecko/Binance 判定（2026-09-24 CTO固定・取引所は公式参照レートではないため OFFICIAL_PUBLIC にしない）と同一論理。`SOURCE_POLICIES` に登録する設計とする。
- 市場差の扱い: 24/7 市場のため「営業日」でなく **UTC 暦日** を使う。ホライズンは株式 = trading_days・`hl_perp` = calendar_days とし、`StrategyForwardOutcome`（`src/yowayowa/strategy_models.py`）に **`horizon_unit` の追加フィールドを設計する（additive）**。既存 `horizon_trading_days` の意味は株式系で不変とし、既定値（trading_days 相当）で後方互換を保つ。
- ファンディング計算: バックテストのパーペ損益は、時間粒度（1 時間）のファンディングをポジションサイズに応じて加算して計算する。符号規約: **fundingRate > 0 のときロングが支払う**（ロング側は負のキャリー、ショート側は受取り）。8h/1h 正規化等の詳細定数は実装時に公式 docs で確認する Open Question とし、設計段階で埋め込まない。
- レバレッジ: 入力パラメータとして **上限を明示するのみ**。解滅ウォーターフォールはシミュレートしない。レバレッジ設定が破綻条件（清算価格到達相当）に触れた区間は、損益を捏造して続行せず **invalidation 条件として run 記録に残す**。
- 手数料/スリッページ: venue 毎に設定値（maker / taker bps・slippage bps）を明示的に持ち、既定値は実装時に公式テーブルで確認する。コスト 0 の「理想的約定」で結果を美化しない（D6 の cost 明示設定のみと対になる）。

### D5. 既存候補・既存指標の再利用

- バックテストの入力ユニバースは次の 3 系統に限る:
  1. 永続化済み screening 候補（run_date キーで再生）
  2. watchlist
  3. 明示シンボルリスト
- ファンダメンタル指標計算はバックテスト側で **再実装しない**。既存の canonical metrics 解決サービス（EDINET / SEC）に **as-of 参照** を追加する形で再利用する。
- 設計するもの: `services/pit_fundamentals.py` — as_of 付き PIT アクセサ。契約の形（実装はしない）:
  - 入力: symbol（および EDINET/SEC の発行体識別）・metric family・`as_of`。
  - 出力: 「`as_of` 時点で発見済みの最新 canonical 値」＋その発見日＋provenance。発見済みの値が無ければ **unavailable を返す**（補間しない・ゼロを返さない）。
  - 内部では EDINET/SEC の既存 canonical metrics 解決を呼び出し、発見日（filed / published date）で as-of フィルタする。指標の定義・優先順位・通貨整合の規約は既存実装に一本化する。

これが防止するアンチパターン: バックテスト専用に指標計算を複製し、ライブの canonical 定義と食い違った値で検証すること。

### D6. 実行モデル

- **発注は行わない**（broker への接点ゼロ）。P2 の発注系・interlock とは無関係であり、`submissions_enabled` 等のゲートに触れない。
- 出力は次の 3 種（いずれも D7 の run 成果物として保存）:
  - **取引/イベントログ**: 約定 1 件ごとに時刻・ユニバース・シンボル・方向・数量・約定価格・適用コスト・判断の根拠イベント（どのバー/文書/ファンディング区間に基づいたか）を記録。
  - **資産曲線**: 時刻ごとの equity（通貨ラベル付き。ユニバース毎に D3 の固定通貨）。
  - **指標**: total return・CAGR・Sharpe・max drawdown・turnover・benchmark 超過。
- 指標の定義（設計段階で固定する意味の規約）:
  - **Sharpe**: risk-free = **0**。年化は株式 = 252・`hl_perp` = 365 と明示する（区間の長さの単位に合わせる。株式=trading_days・HL=calendar_days）。
  - **CAGR**: 期間長の単位（営業日/暦日）を run 記録に明示した上で年換算する。
  - **max drawdown**: 資産曲線の最高値からの最大下落率。
  - **turnover**: 期間内の売買回転（約定額の集計方法は実装時に確定 — Open Questions）。
  - **benchmark 超過**: ベンチマーク無し（`hl_perp` で none 選択）の場合は表示しない（0 と表記しない）。
- cost は明示設定のみ（エンジンに暗黙の既定コストを埋め込まない）。設定無し run は「コスト 0」ではなく「コスト未設定」として区分し、美化を防ぐ。

### D7. provenance / 監査

- 各 run は次を持つ:
  - `run_id` — 成果物ディレクトリの鍵。
  - `engine_version` — エンジン挙動のバージョン。同じ run_id の再現比較に使う。
  - **データマニフェスト** — ソース毎の coverage 窓・行数・EDINET インデックス coverage・provider set。バックテストが「どのデータで回ったか」を後から確定させる。
  - **config hash** — 実行設定（ユニバース・期間・コスト・レバレッジ上限等）のハッシュ。同一 config hash = 同一設定の再実行を識別できる。
- 成果物は `data/backtests/{run_id}/` に manifest 付きで保存する（取引/イベントログ・資産曲線・指標・上記マニフェスト）。
- 成果物は **ライブのスコアリングへ自動還流させない**（研究成果物である。ライブ運用に反映させる場合は人間が成果物を読んだ上で別途意思決定する）。

### D8. 提案モジュール構成（名前のみ・コードなし）

- `providers/hyperliquid.py` — 取得面。`docs/CRYPTO.md` / `docs/STOCKS.md` の provider パターン（ProviderDescriptor + enforce_provider_policy・SOURCE_POLICIES 登録・store 冪等・非補間）を踏襲する。
- `services/pit_fundamentals.py` — as-of アクセサ（D5）。
- `services/backtest_service.py` + バックテストエンジン群 — 実装時の配置は実装タスクで確定する（エンジン内部のクラス構成は本設計で固定しない）。
- `data/backtests/{run_id}/` — 成果物（D7）。
- API: `POST /v1/backtest/runs`・`GET /v1/backtest/runs/{id}` — personal モード専用・public は 404 fail-closed（CRYPTO/STOCKS 前例と同じ）。
- CLI: `yowayowa backtest-run` / `yowayowa backtest-show`。
- agent tools: `get_backtest_run` 等の読み取り系。catalog 追加は API/agent parity の CI ピン留め（CG-20260925-003）更新を伴う旨を注記する。

**API / CLI 契約の形状（名前と意味のみ・スキーマ定義は実装タスクで確定）:**

- `POST /v1/backtest/runs` の入力（想定）: `universe_id`（D3 の 3 種）・評価期間（開始/終了）・入力ユニバースの系統（D5 の 3 系統のいずれかとその識別子）・cost 設定（未設定なら「コスト未設定」区分）・ベンチマーク選択（`hl_perp` は none 許容）・レバレッジ上限（`hl_perp` のみ・任意）。応答は `run_id` と受付状態。
- `GET /v1/backtest/runs/{id}` の出力（想定）: run の状態・config の要約・データマニフェストの要約・指標・成果物一覧。失敗 run も状態として区別して参照可能にする（黙って消さない）。
- CLI `backtest-run` は同等の入力を引数で受け、`backtest-show` は run の要約を表示する。API と CLI は同じ service 層を共有する（API-first 規約の踏襲）。
- 書き込み系は POST のみ。削除・上書きの面は持たせない（run 成果物は追加のみ・追跡可能にする）。

**想定する実行フロー（承認後・提案・コードなし）:**

1. データ取得が日次で回り、OHLCV・funding が store に蓄積する（機能提案 A・CRYPTO.md の cron 運用パターン準拠。登録作業自体が承認後の実装タスク）。
2. オペレーター（またはエージェント）が CLI / API で run を作成する。config hash とデータマニフェストが生成される（D7）。
3. エンジンがイベントを消費し、成果物 3 種（D6）を `data/backtests/{run_id}/` に書き出す。
4. オペレーター / エージェントが `backtest-show` / `get_backtest_run` で成果物を読む。agent tools は読み取り系のみ（D8）。
5. 成果物はライブのスコアリングへ自動還流しない（D7）。scoring version を変える判断は人間が行う。

## 3. 優先順位付き機能提案（5件）

工数（S/M/L）と価値（高/中/低）は設計段階の目安であり、確定した実装計画ではない。

### 3.1 提案一覧

| 優先 | 機能 | 工数 | 価値 | 依存 | 根拠 |
|---|---|---|---|---|---|
| A | Hyperliquid データ取得面（ohlcv+funding 永続化・CRYPTO.md パターン） | S〜M | 中〜高 | なし | パターンが既存で即着手可能・HL 研究の全ての前提 |
| B | PIT ファンダメンタル accessor（as-of・EDINET/SEC） | M | 高 | なし | バックテストの正確性の要・単体でも「当時何が既知か」に正確に答えられる |
| C | バックテストエンジン MVP（株式ユニバース先・HL 後続） | L | 高 | B | P3 の walk-forward 未着手部分の直接実現 |
| D | アラートルール拡張（信用残急変・IR キーワード・価格/ファンディング閾値 → Telegram） | S〜M | 高 | なし | 日常運用の情報優位に直結・既存 alerts の延長 |
| E | IR イベントスタディ（文書到着 → 前後リターン窓） | S | 中 | B, C | 研究の質を上げる薄い分析レイヤ |

### 3.2 各機能の詳細

- **A: Hyperliquid データ取得面** — candleSnapshot（OHLCV）と fundingHistory の 2 系統を取得し、CRYPTO/STOCKS と平行構造の store に provider 別で永続化する。`SOURCE_POLICIES` への登録・provenance 付与までを範囲に含む。BTC/ETH 同様、取得と解析は分離する。
- **B: PIT ファンダメンタル accessor** — `services/pit_fundamentals.py`。EDINET/SEC の既存 canonical metrics 解決に as_of 参照を追加する契約。バックテストなしでも単体で「当時何が既知か」に正確に答えられるため、単独でも価値が立つ。
- **C: バックテストエンジン MVP** — D1〜D7 の契約を実装する。株式ユニバース（jp/us いずれか）を先とし、`hl_perp` プレーンの接続は後続カード（C-2）に分離する。最初の MVP では total return・CAGR・max drawdown・benchmark 超過までを出せればよく、指標の充実は漸進でよい。
- **D: アラートルール拡張** — 既存 alerts の延長として、信用残急変・IR キーワード・価格/ファンディング閾値の検出ルールを追加し、Telegram（既存の通知経路）へ届ける。バックテスト本体に依存しないため独立して着手可能。
- **E: IR イベントスタディ** — P1C で取得済みの IR 文書到着をイベントとし、イベント前後のリターン窓を集計する薄い分析レイヤ。B（当時の既知情報の確定）と C（リターン窓の計算基盤）に依存するため最後。

### 3.3 実装着手条件とタスク分割の目安

各機能とも **マスター承認後に実装着手する**（本表は準備物の優先順位であり、着手承認を意味しない）。タスク分割の目安は **1 機能 = 1 カード**（C の `hl_perp` 接続のみ C-2 に分離）。承認されたカードは通常の検証契約（`make verify`・実データ受入）に従う。

## 4. ロードマップとの整合

### 4.1 P3 / P4 への接続

| 提案 | 接続先 | 接続先の現状（2026-09-26 再ベースライン時点） |
|---|---|---|
| C | P3 — walk-forward / out-of-sample・portfolio-aware sizing の土台 | NOT STARTED |
| B | P4 — EDINET/SEC ファンダメンタルの検証面・P3 の PIT 入力 | canonical metrics は DONE、as-of 参照は未整備 |
| A | P4-E 系 — crypto データ面の拡張（検証対象データの追加） | P4-E phase 1（BTC/ETH OHLCV）DONE、HL は未着手 |
| D | P4 — 信用残・IR の日常消費面（alerts） | 週次信用残 DONE・日次 JPX は 2026-09-28 から形式確定待ち |
| E | P4/P5 — IR 蓄積の研究消費面 | P1C 取得 DONE・イベントスタディは未着手 |

- 機能提案 C は既存 `services/strategy_calibration.py` の集計キャリブレーション（rank IC・decile 統計等）を **置き換えない**。既存の注意書き（重複するフォワード窓を独立扱いしている旨の bucket notes）はそのまま残り、本基盤はその解消に使える out-of-sample 評価の面を提供する。
- 機能提案 A・B・D・E は P4（日本市場情報優位）および P4-E 以降に広がった crypto / US 株データ面を、「取得」から「検証」へ進める面を提供する。EDINET・信用残・IR の蓄積が screener 入力・アラート・イベントスタディ・バックテストの 4 つの消費先を持つことになる。

### 4.2 完了率ベースライン・frozen 項目との整合

- 完了率ベースラインは 67%（2026-09-26 再ベースライン）。本設計はコード進捗 0 の文書であり、ベースラインを変えない・水増ししない。実装承認後は各 P3/P4 項目の status 語彙（NOT STARTED → IN PROGRESS 等）で進捗を追跡する。
- 公開 SaaS 等 frozen 項目には触れない。バックテスト面は personal モード専用 API（public は 404 fail-closed）であり、フリーズ方針と矛盾しない。
- 人間ブロック項目（real-session / live-broker 受け入れ）と無関係であり、本設計の実装がブロックを解消する主張もしない。完了率の最大控除要因（real-session 受け入れ）は本基盤では変動しない。

### 4.3 実行開始条件

マスター方針は「（発注などの実行は）十分に機能が育ちデバッグが済んでから」。本設計は **育成段階の準備物** であり、実行フェーズ（発注・資金を動かす運用）の開始を提案しない・前提にしない。バックテスト機能自体の実装も、各機能のマスター承認後に着手する（§3.3）。

## 5. Open Questions（実装時に確定する事項）

1. **Hyperliquid の未検証定数**: ファンディングの正規化定数（8h レート基準か 1h レートか・換算係数）・OI の正確なレスポンス型（assetCtxs 系に含まれる想定・要確認）・手数料テーブル（maker / taker bps・slippage 既定値）。実装時に公式 docs で確認し、設計書に定数を埋め込まない。
2. **`pit_fundamentals` のスナップショットキャッシュ要否**: SQLite テーブルを新設するか都度計算か。run 数・as-of 参照粒度が増えた場合の計算量次第で確定する。
3. **`hl_perp` のベンチマーク扱い**: none / BTC。比較の意味（何と比べるか）が確定するまで none を既定として設計する。
4. **日足 candleSnapshot の履歴収まり**: 5000 本上限は公式明記（検証済み）。2023 年ローンチ以降の全履歴が 5000 本内に収まるかは実機確認が必要。
5. **株式ユニバースの長期日足ソース**（追加提案）: 株式バックテストに必要な過去日足の供給面（既存 store の coverage は run 単位の蓄積で、長期履歴をどこまで持つか）を確定する。エンジン MVP（C）着手前の前提確認。
6. **turnover の集計方法**（追加提案）: 約定額ベースか保有回転ベースか。D6 の指標定義として実装タスクで確定する。

## 6. 参照

- 公式 docs:
  - Hyperliquid info endpoint: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint
  - Hyperliquid perpetuals（funding・手数料の確認先）: https://hyperliquid.gitbook.io/hyperliquid-docs 配下の perpetuals ページ
- repo 内:
  - `docs/PRIVATE_OPERATOR_ROADMAP.md` — P3/P4・完了率ベースライン（67%, 2026-09-26）・frozen 項目の正本
  - `docs/CRYPTO.md` — provider パターン・`LicenseClass.PERSONAL_ONLY` 判定（2026-09-24 CTO固定）・public 404 fail-closed の前例
  - `docs/STOCKS.md` — Alpaca OHLCV・crypto と平行の store 構造の前例
  - `docs/EDINET.md` — 提出履歴インデックス・canonical metrics 契約
  - `docs/SEC.md` — Company Facts 正規化契約（filed + accession）
  - `src/yowayowa/services/screening_pipeline.py` — screening 候補の run_date 永続化
  - `src/yowayowa/strategy_models.py` — `StrategyForwardOutcome`（`horizon_unit` 追加先）
  - `src/yowayowa/services/strategy_calibration.py` — 集計キャリブレーションの現状（置き換え対象外）
