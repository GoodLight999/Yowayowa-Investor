# docs/INDEX_ja.md — ドキュメント日本語インデックス

対象: `docs/` 配下の全 Markdown 73 ファイル（この INDEX 自身を除く 72 文書を掲載）。各ファイルのタイトル/目的/主要セクションを3行で要約。
最終更新: 2026-10-24（JST・カード t_b0c28ad2 の snapshot）。内容の正本は各英語原文ドキュメント。食い違いがあれば原文を優先。

## 目次（カテゴリ別）

- 全体像・運用: ARCHITECTURE / PRIVATE_OPERATOR_ROADMAP / AUTONOMOUS_AGENT_HANDOFF / OPERATOR_MODE / AGENT_MESSAGE_BOARD / AGENT_ENVIRONMENT / README / DEMO_LOOP_V1_OPS / STATE_P4_REMEDIATION_20261001 / CFO_HFT_BRIEFING_20261002
- データソース・取得: DATA_POLICY / SEC / EDINET / ESTAT / JPX_DAILY_MARGIN / CREDIT_MARGIN / CRYPTO / STOCKS / MS2_RSS
- ブローカー・実行: BROKER_ACCEPTANCE_MATRIX / RAKUTEN_WEB_SESSION / EXECUTION_ADMISSION_CONTRACT_20261002
- 研究・AI: RESEARCH_BRIEF
- 研究基盤: BACKTEST_FOUNDATION_DESIGN / BACKTESTING / STRATEGY_SIGNAL_QUALITY / CIO_STRATEGY_PROPOSALS_20261002 / BACKTEST_LEAK_REVIEW_20261023 / BACKTEST_SPLIT_ADJUSTMENT_C2_20261023 / BACKTEST_UNIVERSE_BIAS_20261023 / BACKTEST_REPORT_P3 / BACKTEST_REPORT_CRYPTO / BACKTEST_REPORT_S01_CRYPTO_TREND / BACKTEST_REPORT_S02_VOL_TARGET / BACKTEST_REPORT_S03_GARP_HYBRID / BACKTEST_REPORT_S03_PBR_SUPPLY / BACKTEST_REPORT_S05_DEFENSIVE_ROTATION
- 実測証跡: P1C_EVIDENCE / P1D_EVIDENCE / P5_R6_BRIEF_EDINET_RECENCY_20261023 / P5_R3_PORTFOLIO_DELETE_20261023 / P5_R3_UI_DELETE_20261023 / P5_R4_QUOTE_CURRENCY_20261023 / P5_DEADEND_AUDIT_20261023 / P5_MS2_QUANTITY_UNKNOWN_20261023 / OPENAPI_BASELINE / P5_OPENAPI_BASELINE_GATE_20261023 / P5_IR_MONITOR_CORRUPT_ROWS_NOTE_20261024 / P5_ACQUISITION_CORRUPT_ROWS_20261024 / P5_MYPY_DEBT_20261024 / WORKFLOW_DEADEND_AUDIT / SILENT_FAILURE_AUDIT_20261023 / OPENAPI_DRIFT_20261023 / P1D_JPX_MARGIN_DAILY_EVIDENCE / P1D_JPX_REGRESSION_20261023 / P4_EDINET_INDEX_BOOTSTRAP_20261023 / SECURITY_ORDERBOOK_AUTH_20261023 / SECURITY_CRON_FAILCLOSED_20261023 / PAPERLOOP_MONEY_REVIEW_20261023 / PAPERLOOP_C02_WEEK_RETURN_20261023 / PAPERLOOP_C03_WEEK_SHORTFALL_20261023 / PAPERLOOP_C04_MTM_UNPRICED_20261023 / PAPERLOOP_COMPARISON_ENDPOINT_20261024 / MONETIZATION_READING_20261023 / P3_LOOP_20261023 / P5_CLI_HTTP_ERRORS_20261023 / P5_FUNDAMENTALS_FAILURE_SIGNAL_20261023 / P5_R2_RESEARCH_UNKNOWN_SYMBOL_20261023 / P5_R5_QUOTE_SIZING_IDEA_20261023 / P5_R10_RISK_BENCHMARK_20261023 / P5_R11_SCREEN_METRICS_20261023 / P5_SCREENING_SOURCE_DATES_20261023 / P5_DOCUMENT_PARTIAL_EXTRACTION_20261024

---

## 全体像・運用

### ARCHITECTURE.md
- タイトル: Architecture（システム構成）
- 目的: Yowayowa-Investor の「モジュラーモノリス」としての全体構造と、プロバイダ/AI/ブローカー接続の設計原則を定める。
- 主要セクション: Shape（ドメインモデル/Providers/Services/FastAPI/CLI/SQLite）、Provider policy（LicenseClass、publicモードでpersonal_only拒否）、Provenance、AI operations（OperationPlan化）、Deliberate reuse（Lightweight Charts・pandas-ta・Arelle。OpenBBはAGPLのため不使用）、Product profiles（personal=Full/Operator、public=Safe/Limited）、Private connector and broker architecture（capability駆動の転送選択、MARKET SPEED II RSS経由の楽天対応）。

### PRIVATE_OPERATOR_ROADMAP.md
- タイトル: Private / Family Operator Roadmap
- 目的: 個人/家族オペレーター向け投資研究・実行システムとしてのロードマップ。P0〜P6 の目的・状態・終了条件を管理する正本文書。
- 主要セクション: Product target（discover→…→recalibrate の一次ループ）、Progress baseline（v1完成度67%、2026-09-26再ベースライン。人間ブロック項目を明示）、Roadmap rules（API-first必須・スクレイピング第一級・認証は迂回しない等8か条）、P0（AI接続）/P1（privateデータ優位）/P2（Linux発注実行）/P3（収益性学習ループ）/P4（日本市場情報優位）/P5（製品完成監査）/P6（v1フリーズ）、Frozen / non-critical work。

### AUTONOMOUS_AGENT_HANDOFF.md
- タイトル: Autonomous Agent Handoff — Hermes Primary
- 目的: 自律開発エージェント（Hermes）への運用ハンドオフ。ミッション・開発所有権・検証済みチェックポイント群・不変条件を読む順序付きで引き継ぐ。
- 主要セクション: Mission（Private/Family Operator、公開SaaSは凍結）、Development ownership と Autonomy rule（OAuth/MFA等の人間アクションで待たない）、Repository / delivery state（branch: agent/commercial-foundation、Draft PR、Vercel本番）、P1B〜P4-F の検証済みチェックポイント群、Product architecture invariants（API-first/財務的正確性/Provenance/AI-led but interpretable/private connectors）、重要サブシステム、Current roadmap、Verification contract、Documentation hygiene。

### OPERATOR_MODE.md
- タイトル: Full / Operator mode（personalモードの全容）
- 目的: 主製品プロファイル personal（Full/Operator）モードで許容される能力（認証付きスクレイピング・privateプロトコル・ローカルブリッジ・ブローカー制御等）とその実装を説明する。
- 主要セクション: Connector priority（公式API→認証済みブラウザセッション→XHR/JSON→DOM→スクレイピングの順）、Scraping（構造化ソース優先・スクレイパー宣言事項）、Private acquisition toolkit（src/yowayowa/acquisition/ のコネクタレジストリ・セッション転送・auth状態検出・スナップショット/diff）、Broker read-side (P1B)、Strategy research loop (P3)、Session expiry notification (P2C)、Company IR acquisition (P1C)、Broker control（Live order interlock・取消はP2C2設計のみ）、Rakuten Securities first path、Deployment。

### AGENT_MESSAGE_BOARD.md
- タイトル: Agent Message Board — ChatGPT ↔ Hermes
- 目的: ChatGPT と Hermes の間の軽量で永続的なメッセージボード。ロードマップ/ハンドオフの代替ではなく、レビュー観察・デバッグ助言・リグレッション疑い等を交換する。
- 主要セクション: Operating protocol（ID/From/To/Priority/Status 付きメッセージ規約、シークレット禁止、セッション開始時にOPENを読む等8規則）、OPEN messages（CG-20260925-001〜008: 再ベースライン・ロードマップ誤差・API/agent parity・実セッションブロッカー明示・privateコネクタのデバッグ手順・LLM研究でのprovenance保持・Codexは人間受け入れ・現状チェックポイント）、ACK/DONE（CG-009: P5-A OHLCVツールバグ修正）、Message template。

### AGENT_ENVIRONMENT.md
- タイトル: Agent environment: shared tool venv and verification
- 目的: 複数自律エージェントが同一リポジトリの worktree で並行作業しても検証結果を壊さないよう、共有 Python ツール venv の構築・使用規約を定める。
- 主要セクション: Background（editable .pth race — 2026-09-24 のホットスポット）、Rules 5か条（venvはツールのみ・PYTHONPATH=$(CURDIR)/src で自ツリー解決・自分のツリーでのみ検証・sync は --no-install-project のみ・make install は初回構築のみ）、Makefile contract（make verify = lint→typecheck→test→openapi）、GitHub CI（使い捨てランナーのため規約適用外）、History。

### README.md
- タイトル: docs インデックス（日本語・軽量版）
- 目的: docs/ 全文書の一行インデックスだけを扱う軽量版（worker-luna 自動生成・2026-09-27）。要約の正本は各文書本体で、詳細版は本 INDEX_ja.md。
- 主要セクション: 全体像・方針、データソース — 公開 API、データソース — 実機・認証系／スクレイピング、エージェント運用・メッセージ、証跡・受入。

### DEMO_LOOP_V1_OPS.md
- タイトル: デモ運用ループ v1.1 運用手順（ゼロ発注・ペーパー）
- 目的: カード t_d9a25dd2（実装 worker-flash）。SHORT_TERM_STRATEGY_DESIGN（t_421158aa）§2-2・§2-1・§3 M1–M9 に基づく、cron 3本・VPS で軽い処理のみのゼロ発注デモ運用の手順と規律を定める（v1.1・2026-10-11）。
- 主要セクション: 構成（cron 3本・VPS で軽い処理のみ）、データ source と追記手順、規律（クリティカル）、継続・中止の機械基準（設計 §4・数値固定・評価期間中の変更禁止）、失敗時の復旧、変更管理、cron 登録（cronjob_manage・no_agent=True・deliver local）、検収コマンド。

### STATE_P4_REMEDIATION_20261001.md
- タイトル: P4是正スレッド状態スナップショット（2026-10-01 14:05 JST・COO記）
- 目的: P4再監査REJECT是正（t_1509396e・worker-sol が 93be654 実装・make verify 1645 passed/2 skipped・CTO統合待ち）を中心に、当時のカード群の状態・滞留（agy 5時間枠切れ）・重複カード整理を記録した時点スナップショット。
- 主要セクション: 現在の滞留、重複カード整理、運用上の教訓、追記（2026-10-02 20:15 JST）〜安全保護フェーズ1の監査ループ進捗、追記（2026-10-02 20:50 JST）・運用拡張（マスター指示）。

### CFO_HFT_BRIEFING_20261002.md
- タイトル: CFOブリーフィング — 金融庁「高速取引行為の動向について」の分析と財務・執行コストへの示唆
- 目的: 受取人 CFO-Plutus／報告者 Molt（COO窓口）・2026-10-02。金融庁の高速取引（HFT）動向報告を市場構造と財務・資産運用・執行コストの観点で分析し、板系機能の新設というシステム対応に繋げる。
- 主要セクション: エグゼクティブサマリー、詳細分析と市場構造、CFO（財務・資産運用・執行コスト）への重要示唆、システム対応（板系機能の新設）。

## データソース・取得

### DATA_POLICY.md
- タイトル: Data provenance and licensing policy
- 目的: プロバイダがデータを提供する前に出所と再配分クラスを宣言することを義務付ける、データライセンス方針の最上位文書。
- 主要セクション: Public-mode rule（YOWAYOWA_MODE=public では PERSONAL_ONLY を拒否）、SEC EDGAR（公式API・10 req/s以内のフェアアクセス）、EDINET（v2はキー必須・XBRL解析は取得と分離）、FRED（BYOK・系列ごとに元ソースの制約を保持）、Yahoo/yfinance（personal-only であり商用再配分手法ではない）。

### SEC.md
- タイトル: SEC EDGAR normalization and strategy enrichment
- 目的: SEC公式EDGAR XBRL API を official-public ソースとして米国発行体のファンダメンタルズを正規化し、戦略エンリッチメントに使う際の境界を定める。
- 主要セクション: Company Facts boundary（次元開示は損失なく表現されない＝欠落はゼロではなく「正規化不能」）、Balance-sheet period consistency（同一 period end / accession / USD / instant の事実のみ組合せ）、Noncurrent marketable securities mapping（us-gaap:MarketableSecuritiesNoncurrent のみ使用する狭いマッピング・二重計上回避）、Provenance and source isolation（provider補間は会計スコープ単位で原子）、Future expansion（US-GAAP概念追加の手順とテスト要件）。

### EDINET.md
- タイトル: EDINET financial research
- 目的: 金融庁 EDINET API v2 を日本の開示情報研究の公式ソースとして利用する方法（文書取得・XBRL正規化・提出履歴インデックス）を定める。
- 主要セクション: Data paths（type=5 XBRL-to-CSV ZIP→正規化 EdinetFact／日次文書リスト→SQL提出メタデータインデックスの2系統）、Filing-history index（edinet_filings / edinet_index_days でカバレッジ監査可能・部分カバーを完全履歴と偽らない・1日31リクエスト上限のブートストラップ）、Canonical metrics（売上・利益・EPS等ファミリへのマッピングと優先順位ランキング、欠損は unavailable_metrics で明示）、API・CLI。

### ESTAT.md
- タイトル: e-Stat official statistics research
- 目的: 政府統計 e-Stat REST API を日本のマクロ・統計研究の public-safe な一次ソースとして利用する契約を定める。
- 主要セクション: Why discovery comes before table IDs（統計表IDは固定契約にしない・2026年7月CPI基準改定が実例）、Server configuration（App ID はサーバ側のみ）、API（getStatsList/getMetaInfo/getStatsData 対応の /v1/macro/estat/*、フィルタ許可リスト・1万件上限と next_key）、Data contract（原文文字列を保持・非数値は null で捏造しない）、CLI・Browser（e-Statワークベンチ）、Provenance and licensing（official_public・出典表記）、Validation。

### JPX_DAILY_MARGIN.md
- タイトル: JPX daily margin balances（銘柄別信用取引残高・日次）
- 目的: JPX総研の新日次銘柄別信用残サービス（2026-09-28開始・有償契約）の公開事実と取り込み契約を記録する（P4-A）。
- 主要セクション: Publication facts（全TSE上場銘柄・前営業日適用・16:00頃公表・金額列は2026-09-25以降）、Official URLs（現行の仕様PDF・サンプルZIP・料金表・J-Quants Pro API・無料公開の日次PDF）、Specification verification（2026-10-23: 1行目がヘッダ・全項目ダブルクォート・NULLは空文字・列長・市場コード0111/0112/0113/0109/0105・銘柄種別1/2/3の正確な意味・合計レコード廃止・エンコーディングは仕様書に記載なし）、Live data availability（有償CSVは401／無料PDFは全銘柄・約1週間保持・修正版は_01）、「日々公表信用取引残高」との混同禁止、Observed CSV format（公式サンプルから実測: 18列・英語CSVはUTF-8無BOM/日本語はCP932・CRLF）。証跡は P1D_JPX_MARGIN_DAILY_EVIDENCE.md、取込の回帰確認（別経路の再パースと再取込、2026-10-23・差分0）は P1D_JPX_REGRESSION_20261023.md。

### CREDIT_MARGIN.md
- タイトル: Weekly credit margin（信用残・週次）— Yahoo!ファイナンス + 株探
- 目的: 週次銘柄別信用残を Yahoo!ファイナンスと株探からスクレイピングする personal-only 取り込み契約（P4-C、2026-09-24実装）。JPX日次とは別の永続化サーフェス。
- 主要セクション: Observed formats（Yahoo: 信用残時系列テーブルで直近20週・単位株／株探: 信用取引セクションで直近4週・単位千株→×1000で株に正規化）、Cross-validation（4銘柄の共通週が±100株以内で一致）、Dead ends（404となるURL群）、Access etiquette（1URL=1リクエスト・順次アクセス・fail-closed）。

### CRYPTO.md
- タイトル: Crypto daily OHLCV (P4-E phase 1)
- 目的: BTC/ETH の日次OHLCVをキー不要 public API（CoinGecko/Binance）から取得し provenance 付き JSONL に永続化する acquisition サーフェス。
- 主要セクション: コマンド（crypto-fetch / crypto-ohlcv、対応銘柄はBTC/ETHのみ）、Store（data/crypto-ohlcv/{SYMBOL}/ohlcv.jsonl・(provider, currency, as_of)単位で冪等・欠落日の zero/fwd-fill なし）、API（/v1/crypto/ohlcv/*、personalモード専用でpublicは404 fail-closed）、設計判断5項（CTO固定 2026-09-24: PERSONAL_ONLY判定・provider別2ファイル・source混ぜない・BTC/ETHのみ・UTC 00:30の日次cron運用）。

### STOCKS.md
- タイトル: US stock daily OHLCV via Alpaca (P4-F)
- 目的: Alpaca Market Data API から米株の日次確定足（1Day bars）を取得し provenance 付き JSONL に永続化する personal-only acquisition サーフェス。
- 主要セクション: コマンド（stock-fetch / stock-ohlcv、crypto store と平行構造で混ぜない・vwap/trade_count を保持）、シンボル（正規 pattern のみ・未知tickerは upstream 404/422 → LookupError の fail-closed）、API（/v1/stocks/{symbol}/bars*、store読み出しのみでnetwork fetch禁止）、設計判断5項（CTO固定 2026-09-24: PERSONAL_ONLY・end=昨日(UTC)までの request window で直近SIP照会403を回避・next_page_token対応等）、Alpaca FX は実装しない（権限不足403実測）、cron運用（JST 08:30）。

### MS2_RSS.md
- タイトル: MS2 RSS (MARKET SPEED II) batch quote pipeline
- 目的: オペレーター自身の楽天証券フィードから MARKET SPEED II RSS 経由で Windows ノード上に quotes をバッチエクスポートし、SCP で VPS へ一方向に流す personal-only 経路。
- 主要セクション: Windows Node での one-shot バッチエクスポート（scripts/windows/ms2_rss_export.py・四値フィールドの未取得は null で 0 を禁止）、Task Scheduler での日次実行（16:15 JST）、SCP で VPS へコピー（到着順不問）、VPS 側の受信/検証（Ms2RssFileProvider・欠落は unavailable_symbols や Ms2RssLookupError で fail-closed）。License class は PERSONAL_ONLY。

## ブローカー・実行

### BROKER_ACCEPTANCE_MATRIX.md
- タイトル: Broker acceptance matrix — Rakuten Securities Web
- 目的: 楽天証券Webコネクタの受け入れ状態を管理する唯一の真実の源（single source of truth）。自動テスト緑と実機検証を厳格に区別する。
- 主要セクション: Status vocabulary（fixture-green / code-complete / real-read-green / real-write-green / blocked）、Read side R1–R8（口座・建玉・注文・約定・auth検出。現状 fixture-green）、Write side W1–W8（発注提案・interlock・送信transport・照会・取消等。code-complete と blocked が混在）、Standing safety gates 5か条（submissions_enabled の fail-closed・注文番号読み戻しまで accepted=True にしない等）、行列更新手順。

### RAKUTEN_WEB_SESSION.md
- タイトル: 楽天証券 Web セッション実機検証手順書（P1B read-side）
- 目的: fixture-green の楽天読み取りコネクタについて、初回の実認証セッションでリソースカタログURLを確定させるまでの検証手順（Definition of Done）。
- 主要セクション: 前提（personal mode・playwright/Chromium導入・認証の自動化は行わない）、手順1 サーバ起動と初回fetch（未ログイン時 auth_expired が正常挙動）、手順2 人間が正規ログイン（MFA含め自動化しない）、手順3 auth-check、手順4 各リソース取得と楽天画面との突合チェックリスト（残高・建玉・注文・約定・手数料・信用情報・銘柄名等）。

### EXECUTION_ADMISSION_CONTRACT_20261002.md
- タイトル: Execution admission / accounting-day contract (2026-10-02)
- 目的: local Operator Bridge 改修 AS-F2-01-R4 / AS-F2-04 の契約（検証に実口座の発注は使わない）。UTC を会計タイムゾーンとし、`reserve_order_submission` と `append_audit` が `BEGIN IMMEDIATE` 内で1回の時計サンプルからタイムスタンプ/日ペアを採ることを定める。
- 主要セクション: Live attempts（live の `order_submit_attempt` は既存の SUBMITTING reservation を要求）、Prepared transport boundary、Legacy import。

## 研究・AI

### RESEARCH_BRIEF.md
- タイトル: P5-A: LLM research brief & ask（朝ブリーフ・研究Q&A）
- 目的: ローカルに蓄積した証拠の上に動く2つのLLM研究サーフェス（朝ブリーフと自然言語研究Q&A）の設計と不変条件を定める。
- 主要セクション: What this is（MorningBriefService と research_ask。決定論的証拠パケット→BYOKプロバイダで5セクション日本語ブリーフ/1回のagentラウンドQ&A）、Provider lane（OpenRouter deepseek-chat-v3.1・ローカルshimは不使用）、Data sources and invariants（EDINET日次/信用残シグナル/マクロ/株・暗号OHLCV。欠損は zero-fill せず「未取得」・出典必須・ライセンスは入力の最も厳しいクラスを継承）、Strict prompt discipline（証拠に無い数値の捏造禁止・自由算術禁止）、API（personalモード外は403 fail-closed）。

## 研究基盤

### BACKTEST_FOUNDATION_DESIGN.md
- タイトル: Backtest foundation design（バックテスト研究基盤 設計）
- 目的: 投資バックテストを「正しく」行える研究基盤の設計提案。実装はまだ行わない旨を冒頭に明示した design only 文書。
- 主要セクション: 設計判断 D1〜D10（CTO固定 2026-09-28・第2版: 単一イベントエンジン・PIT原則・ユニバース14種（jp/us株式＋暗号現物＋MT5 5種＋HL 6種）×通貨/カレンダー分離・Hyperliquidデータプレーン（3層＝メイン234/現物330/HIP-3 294・流動性ゲート・candleSnapshot非ページング）・MT5データプレーン（1,316銘柄・金2007年〜の長期深度・取得契約）・既存canonical metricsのas-of再利用・レバレッジ/証拠金/強制ロスカットモデル（本改訂の核心）・実行モデル/provenance・提案モジュール構成）、優先順位付き機能提案7件（HL取得/MT5取得/証拠金モデル/PIT accessor/エンジンMVP/アラート拡張/IRイベントスタディ・工数S/M/L表）、ロードマップP3/P4との整合、Open Questions15件。

### BACKTESTING.md
- タイトル: Backtesting
- 目的: バックテスト戦略カタログ `src/yowayowa/backtest_strategies.json`（`BacktestStrategyDefinition` で検証）の契約。組み込みは 12-1 モメンタム、逆ボラティリティ加重の低ボラ選択、20日 mean reversion、等加重ベースライン、供給入力の基本面価値／清原バリュー ランキング。
- 主要セクション: Strategy definitions（シグナル・銘柄ユニバース・リバランス頻度等の定義フィールド）、API and CLI、Persisted financial inputs and missing-data policy（欠落はゼロとしない）、戦略別契約（S-01 crypto absolute-trend・S-05 defensive-sector rotation・S-03 GARP momentum/PBR hybrid）、Method and limits。

### STRATEGY_SIGNAL_QUALITY.md
- タイトル: Daily strategy signal data-quality contract
- 目的: 共有サービス `compute_daily_strategy_signals`（`GET /v1/screening/strategy` と `GET /v1/backtest/signals/daily`、`evaluate_strategy_signal_alerts` を駆動）のデータ品質契約。screening の失敗は銘柄ごとに分離する。
- 主要セクション: Sessions and freshness、Fail-closed reasons、Provenance in alerts、Regression verification。

### CIO_STRATEGY_PROPOSALS_20261002.md
- タイトル: CIO戦略企画書 — yowayowa-investor バックテスト新戦略・アルファ仮説の策定
- 目的: 提案者 CIO-Fortuna・2026-10-02・ステータス「提案（実装前）」。分析・設計であり実弾の売買・発注を含まず、分析の完了は売買の承認ではないことを明記した上で、実測ベースラインの上に新戦略・アルファ仮説を提案する。
- 主要セクション: エグゼクティブサマリー、実測ベースライン（提案の前提・出所明示）、共通実装要件（全提案に適用・CTO引き継ぎ事項）、戦略提案（各提案の必須記載項目）、実装順序の推奨（CTOへの引き継ぎ）、リスク注記・限界（本書の効力）。

### BACKTEST_LEAK_REVIEW_20261023.md
- タイトル: バックテスト未来情報リーク / 過剰適合 静的レビュー（2026-10-23）
- 目的: `services/backtest.py`（849行）と `services/strategy_calibration.py`（569行）を静的に読み、時系列順序を関数別に追跡して未来情報リーク／過剰適合の候補を列挙したレビュー（入力経路は参照のみ）。
- 主要セクション: 結論、時系列順序の追跡（関数別）、候補一覧（サマリ）、候補詳細、検査して問題なしと判断した項目（除外理由）、未確認範囲、再現に必要な入力、実測ログ（生データで確認した事実）、付記（レビュー範囲外の観察）。

### BACKTEST_SPLIT_ADJUSTMENT_C2_20261023.md
- タイトル: バックテスト株式分割未調整修正（C2）確定・保存記録
- 目的: 対象タスク t_a84ba708（親 t_4796fd63）。reviewer audit-cld による「Reviewed and approved (round 3)」の株式分割未調整修正について、確定内容と成果物ハッシュを保存する記録。
- 主要セクション: 修正概要と承認経緯、成果物とハッシュ、実測検証結果、利用経路確認、安全性および非改変保証。

### BACKTEST_UNIVERSE_BIAS_20261023.md
- タイトル: バックテスト ユニバース生存者バイアスの分離測定（2026-10-23）
- 目的: `BACKTEST_LEAK_REVIEW_20261023.md` C1【高】を受け、「SPY超過 12.3pp/年」がシグナル効果か銘柄選択（固定ユニバース）効果かを実測で切り分ける（既存コード変更なし・測定は読み取りとメモリ上の再構成のみ）。
- 主要セクション: 結論、ユニバースの由来と選択時点（実コード・実データ）、測定条件、3者比較表（実測・分割補正後）、シグナル効果とユニバース効果の分離（エンジン実行・実測）、分離できる／できないの結論、判断に使う数字（採用可否）、分離に必要な追加データ（優先順）、未確認範囲、再現手順。

### BACKTEST_REPORT_P3.md
- タイトル: P3 バックテスト実測レポート — 4 戦略比較
- 目的: 実行日 2026-09-30（JST）。永続化済み Alpaca SIP 米国株日足 OHLCV（raw prices・USD・シンボルごと provenance 保持）を P2 `run_backtest` エンジンで実走し、4戦略を比較した実測報告。
- 主要セクション: 実測比較、データ coverage / provenance、試した仕様、未検証の設計空間・限界、再現ファイル。

### BACKTEST_REPORT_CRYPTO.md
- タイトル: Crypto Strategy Backtest Report
- 目的: 個人研究バックテスト（投資助言ではない）。Binance 日次 BTCUSDT/ETHUSDT（USDT建て・USDT をポートフォリオ通貨として扱い USD 換算しない）による実測報告。
- 主要セクション: Data coverage、Results、Warmup and selection、Method and caveats、Provenance。

### BACKTEST_REPORT_S01_CRYPTO_TREND.md
- タイトル: S-01 バックテスト実測レポート — 暗号資産デュアルMAトレンドフィルタ
- 目的: 実行日 2026-10-02（JST）。永続化済み Binance BTCUSDT/ETHUSDT 日足（raw prices・USDT・資産別 provenance 保持）、評価期間 2023-01-01〜2026-09-29（各1,368観測）でデュアルMAトレンドフィルタを実測評価。
- 主要セクション: 結論、実測比較 — full / IS / purge / OOS、現金シフト統計 — S-01 lookback 200、感度分析 — 同一シグナル 100/150/200/300、合格目安との対照 — CIO §2-5、データ coverage / provenance、試した仕様・回帰検証、未検証の設計空間・限界、再現ファイル、逸脱・未達・残課題。

### BACKTEST_REPORT_S02_VOL_TARGET.md
- タイトル: S-02 バックテスト実測レポート — ボラティリティターゲティング・ディフェンシブオーバーレイ
- 目的: 永続化済み Alpaca SIP 日足 raw OHLCV（USD・SPY/IWM/JNJ/PG・2021-01-04〜2026-09-28 の1,440観測）を P2 `run_backtest`（lagged signal / next_open・コスト後表示・IID bootstrap 1000回・seed 20260930・95% percentile CAGR CI）で実測評価。
- 主要セクション: 結論、凍結シグナル・現金の契約、実測比較 — full / IS / purge / OOS、感度分析 — 年率目標 10/15/20%、ターゲティング誤差・実測エクスポージャー、Turnover・コストとweekly化、OOS下落局面で縮小していた証拠、比較窓の非対称性と補助診断、合格目安との対照、Coverage / provenance、検証・回帰・逸脱、限界・残課題、再現ファイル。

### BACKTEST_REPORT_S03_GARP_HYBRID.md
- タイトル: S-03 GARP momentum / PBR hybrid — 実測レポート
- 目的: 供給なし初回 baseline の記録（`backtest_results_s03.json` は回帰基準として不変）。SEC実PBR供給後の実測は BACKTEST_REPORT_S03_PBR_SUPPLY.md を参照。供給後も (c) full DD 未達のため採用未承認・生価格分割損益の制約は継続。
- 主要セクション: 結論、実装・再現性・検証、評価窓・コスト、3戦略の full/IS/OOS 指標、PBRカバレッジ・除外診断、A/B比較と帰属、OOS重点分析、CIO参照値との対照（受入基準ではない）、合格目安(a)–(d)、旧4 runner回帰・P3照合、逸脱点・限界・実ポートフォリオとの関係、Provenance。

### BACKTEST_REPORT_S03_PBR_SUPPLY.md
- タイトル: S-03 SEC point-in-time PBR supply — 実測レポート
- 目的: SEC実取得から9銘柄・15,258行のPBRを供給し、S-03 が full の69回すべてでPBRフィルタを使用した実測（momentum-only fallback は full/IS/OOS とも0回）。ローカル実装フェーズの結果であり CTO の独立受入承認を意味しない（pushなし・品質強化は t_417ebaca が f97abbd に積み上げ）。
- 主要セクション: 結論、再現手順、保存契約とPIT、銘柄別カバレッジ、分割実測、同条件A/B、初回供給の検証と回帰（f97abbd時点）、品質強化の実測（t_417ebaca）、限界。

### BACKTEST_REPORT_S05_DEFENSIVE_ROTATION.md
- タイトル: S-05 Defensive rotation 実測評価
- 目的: 凍結設計の実装と実測評価は完了。ただし採用目安の必須条件(b)（同一ユニバース momentum の OOS CAGR +15.70% に対し -8.2238pp・full MaxDD もより深い）を満たさないため戦略採用は不合格。正のOOS収益だけで採用・防御効果を主張しない。
- 主要セクション: 結論、凍結条件・再現方法、5本の指標（コスト後）、スイッチ・防御滞在と monthly/daily A/B、OOS重点分析（2026-01-09〜）、CIO参照値との対照・合否、検証・回帰・逸脱/限界、Provenance。

## 実測証跡

### P1C_EVIDENCE.md
- タイトル: P1C 実測証跡（Company IR acquisition）
- 目的: 企業IR取得パイプライン（P1C）の実データでの受け入れ証跡。ニトリHDの実サイトで end-to-end 検証した結果を記録する。
- 主要セクション: 1. 実データ end-to-end 11/11 PASS（106文書検出・6取得・売上収益912,248百万円の実KPI取得・画像PDFは fail-closed・冪等性）、2. previous-version diff 9/9 PASS、3. 実データで発見・修正した欠陥、4. 回帰テスト、5. verify / CI / 本番、6. 非交叉制約、7-8. 追補（F1/F2修正・F3: CSV解析+XLSX/CSV多列KPI抽出・unchanged(seen)分離）。

### P1D_EVIDENCE.md
- タイトル: P1D Evidence — Authorized private information sources (mailbox)
- 目的: 認可済み private 情報源（オペレーター自身のGmailメールボックス・read-only）コネクタ（P1D）の受け入れ証跡。
- 主要セクション: 1. Acceptance criteria（gog read-only メールボックスコネクタ・+123テストで633全Green）、2. 実データ end-to-end 検証（楽天証券「銘柄情報通知サービス」メール10通から決算イベント11件抽出・40通スキャンでtimeline 70件追加・再実行冪等・provenance完全・鍵無し時は AUTH_EXPIRED の fail-closed）、3. Static checks / full suite、4. Frozen-contract check（P1A〜P1C凍結ファイルへの非交叉）、5. Secrets（keyringパスワードは実行時のみ・実メール内容はテストに含めない）。

### P5_R6_BRIEF_EDINET_RECENCY_20261023.md
- タイトル: P5 R6 — 朝ブリーフが最古のEDINET提出を掲載する（選択順の修正と実測）
- 目的: カード t_3320c1ae。P5_DEADEND_AUDIT_20261023.md §3 R6（P0）の修正。brief の EDINET 選択が jsonl 追記順＝最古固定だった問題を、`submitDateTime` 降順の選択に変えて塞ぎ、同一入力（sha256 `fc71256a…3cb2719`・6,176行）で修正前後を実測比較した記録。
- 主要セクション: 結論（`filings[0]` 2026-09-24 → 2026-10-22、10-13〜10-22 の分類対象488件が1件も選ばれていなかった）、入力の sha256/行数/coverage、修正前の再現（永続化 brief も同症状）、原因（並べ替え不在）、修正（**並び順の仕様変更を明記**・大口提出リストも同順・時刻欠落は最下位・同一時刻は docID 安定順・`coverage` 2キー追加）、前後の選択行12+12と citation/coverage の実測、追加回帰4件と rc=0、未実施（brief 再生成なし・データは最大1日遅れ）と限界。

### P5_R3_PORTFOLIO_DELETE_20261023.md
- タイトル: P5 R3 — 誤作成したポートフォリオを削除できるようにする（API / CLI / 試験 / 実測）
- 目的: カード t_08b40e50。P5_DEADEND_AUDIT_20261023.md §3 R3（P1）の修正。`portfolio create` はできるが削除できない行き止まりを service/API/CLI の3面で塞ぎ、実データDBの読取コピーに対して実 HTTP ステータスと DB 行数で境界を実測した記録。
- 主要セクション: 結論（404/409/force cascade/2回目404/他ポートフォリオ不変）、cascade範囲の決定（positions・portfolio_snapshots・broker_execution_applications を明示削除・既定は全部409）、API実測（before/after の行数つき）、CLI実測（`--yes` 無しは未送信）、自動試験12件＋関連97件 rc=0、ruff/mypy（既存31件は対象外ファイル）、残る穴（UIに削除導線が無い＝次カード候補）。

### P5_R3_UI_DELETE_20261023.md
- タイトル: P5 R3（UI） — portfolio 画面から誤作成ポートフォリオを削除できるようにする（UI 導線・実測）
- 目的: カード t_ad8d18f1（親 t_08b40e50）。API は完成済みで UI に削除導線だけが無かった穴を塞ぎ、実データ DB の読取コピー＋実ブラウザ（モック無し）で 409→force 削除を通した記録。API/CLI/schema は無変更。
- 主要セクション: 結論（選択中ポートフォリオ対象・0件で disabled・409 は `detail` の件数つき二段目確認・拒否時は再送0・404 は再読込）、変更点（html/js/css/i18n ja+en・`app.js` に `error.detail` 1行）、3経路の契約表、実測ログ（作成→保有追加→409→force 200→一覧から消える・DB行数・原本 sha256 不変）、自動試験4+3件 rc=0 と関連38件 rc=0、ruff/mypy（既存34/32件は他カードのファイル）、限界（本番デプロイ未確認・Chromium のみ）。

### P5_R4_QUOTE_CURRENCY_20261023.md
- タイトル: P5 R4 — quotes/valuation の通貨欠落を直す（応答契約と重複ルートの実測）
- 目的: カード t_2355c8b3。P5_DEADEND_AUDIT_20261023.md §3 R4（P1）の修正。`/v1/markets/quotes` の `currency` が全銘柄 `null`、`/v1/valuation/{symbol}` に通貨が無く `company_name` がシンボルに退化していた問題を、provider 実値（`fast_info`）と metadata 型判定の修正で塞ぎ、実 API で 4 銘柄の通貨確定を実測した記録。
- 主要セクション: 結論（原因は2つ＝quote 組立時に通貨を未設定／yfinance 1.7 の `HistoryMetadata` を `isinstance(dict)` で捨てていた）、修正前後の実測（quotes 4件・valuation 4件・未知名404）、応答契約（`currency`/`currency_source`/`currency_reason` と理由コード3種）、通貨の出所を quote 1箇所に寄せる設計、`/v1/valuation/{symbol}` 二重定義の実測確定（fundamentals 側が応答・`routes.py` は未編集で削除/改名の提案のみ）、新規17件＋21モジュール267件 rc=0、残件（決算側 `MetricPoint.currency` 未設定＝Y04ゲートが常に互換に倒れる・CLI通貨列・ISO membership）。

### P5_DEADEND_AUDIT_20261023.md
- タイトル: P5 Dead-end Audit — 主要導線（research → screening → valuation → portfolio → morning brief）
- 目的: カード t_64253b31。主要5導線をローカル API/CLI で実走し、行き止まりを実測で列挙、1件を最小修正して同一入力で回帰させた記録。2026-09-28 の全工程監査（WORKFLOW_DEADEND_AUDIT.md・23件）の再制作はせず、差分確認に留める。
- 主要セクション: 方法と限界（内蔵LLMは呼ばない・実ブラウザ/楽天セッションは NEED-HUMAN）、実走ログ（入力の sha256 と時刻つき・各コマンドの exit 実測）、行き止まり10件（P0 3 = 5桁EDINETコードが下流で404/空200・research の空200・brief が最古の提出を毎日掲載／P1 6／P2 1）＋ 運用摩擦1件、塞いだ1件＝screening 候補のコード絞り込みをクエリへ移動（CLI/API/AI の3面・RED→GREEN・表ダンプ sha256 不変）、前回監査との差分表（S1/F1/I1/I2/E1/O1/O2/S2/G2 は解消、G1/D1/B1/B4/R1 は未解消）。

### P5_MS2_QUANTITY_UNKNOWN_20261023.md
- タイトル: P5 MS2 RSS — 数量の解析失敗を 0 ではなく「不明」として区別する
- 目的: カード t_55dbd444。SILENT_FAILURE_AUDIT_20261023.md §3 #8（影響「中」）の修正。`_to_int` の解析失敗が `0` として注文数量・約定数量・保有数量に入り、operator bridge の `/v1/brokers/rakuten/orders`・`/positions` で「値が無い」と「数量 0」が混同されていた問題を、`None`（JSON `null`）へ変え、0 は実値として温存した記録。
- 主要セクション: 結論と実測（git HEAD の旧モジュールと修正後へ同一のワークシート表を与えた before/after 比較＝`1,000株`と空文字が `0`/`Decimal(0)` → `None`、解析できていた行は不変）、原因、修正（`_to_int` の NFKC＋ASCII 数字要求・注文数量は正値のみ・約定 0 は保持・`parse_order_text_status` は数値不明時に部分約定を推測しない）、broker_models の契約拡張（必須キーは落とさない）、影響範囲（provider 外＝broker_models/broker_read_cli/tests の3ファイル＋文書、`executions_reconciliation.py` は未変更）、新規34件と関連332件 passed rc=0、mypy 既知31件・ruff 変更ファイル0件、限界（実 Excel 未検証＝NEED-HUMAN・注文数量0を不明に倒す仕様変更・監査の「全角は解析失敗」は実測と相違）。

### OPENAPI_BASELINE.md
- タイトル: OpenAPI baseline and drift gate
- 目的: 生成物である `openapi.json`（git 管理外）に代えて、契約の正本を VCS 管理下の `docs/openapi_baseline.json` に置き、生成仕様との差分を CI/`make verify`/pytest の3経路で落とすゲートの恒久運用を定める。
- 主要セクション: Why（監査 §4 P2 の12日間の無検出 drift）、Files（基準・共有ロジック `src/yowayowa/openapi_baseline.py`・`scripts/check_openapi_baseline.py`・pytest ピン）、ゲートの実行場所、意図的契約変更時の基準更新手順（`make openapi-baseline` → diff レビュー → 実装と同時コミット）、禁止事項（手編集・API 変更を伴わない再生成・`|| true` 迂回）、ゲートがやらないこと（security 境界の修復と `info.version` 運用は別件）。

### P5_OPENAPI_BASELINE_GATE_20261023.md
- タイトル: P5: 生成OpenAPI仕様の基準をVCS管理に置き、CIに差分ゲートを入れた（2026-10-23）
- 目的: カード t_142aa7f0。監査 §9-5（基準の VCS 管理＋CI 差分ゲート）を実施し、ローカルで「一致=exit 0 / 基準を1箇所古くした3種のコピー=exit 1 / pytest 15 passed」を実測した記録。CI 全体は未実走である事実も明記。
- 主要セクション: 結論、変更ファイル（新規6・追記のみ2）、CI と Makefile の diff、ゲートの設計（意味比較・canonical 保存・operation 増減と JSON pointer 差分）、ローカル再現コマンドと実測、mutation 3種の実測（うち operation 削除で子差分16行が漏れた prefix 不具合を発見・修正し回帰ピン追加）、監査 §6「追加のみ」分の独立確認、未実走・残課題（既存 ruff/mypy 失敗・Python 3.13 差の未実測・security は別カード）。

### P5_IR_MONITOR_CORRUPT_ROWS_NOTE_20261024.md
- タイトル: P5: IR監視 outcome.notes に fingerprint 破損行の件数を載せる（2026-10-24）
- 目的: カード t_b28a4ed3（親 t_ebd05744）。P5_ACQUISITION_CORRUPT_ROWS_20261024.md で観測可能になった diagnostics を、IR 監視の読み取り側（`IrMonitorService.monitor()` の `known_keys`/`known_content_urls`）に配線し、破損行があるときだけ既存 `outcome.notes` に件数・1起点行番号・再取得/重複アラートの走り得ることを1行で追加した記録。
- 主要セクション: 結論、変更の前後（両 reader が同一 diagnostics dict を clear するため note は `known_content_urls` の後に作る）、実測（破損1行あり/なしの `outcome.notes` 生出力・fetch_state は `ok` のまま）、試験（専用テスト B18b 2件追加・32 tests rc=0・ruff/mypy 変更ファイル clean）、制約確認（`FingerprintStore` の意味不変・ログ基盤/DB/画面への追加なし）、再現コマンド、申し送り（notes を外部通知に載せる場合のローカル path 含有の注意）。

### P5_ACQUISITION_CORRUPT_ROWS_20261024.md
- タイトル: P5: acquisition 系 JSONL の破損行を「無言で飛ばす」から「件数・行番号・理由つきで飛ばす」へ
- 目的: カード t_ebd05744。SILENT_FAILURE_AUDIT_20261023.md §3 #11〜#17（影響「低」7件）の修正。破損行を1行飛ばす既存契約（監査 §4 E7）と「破損行＝未取得」という安全側の意味は変えず、飛ばした事実だけを既存の戻り値の外側（`diagnostics={}` 引数）・既存の診断面（`AcquisitionOutcome.notes`・`crypto-fetch`/`stock-fetch` の警告行）に additive に載せた記録。
- 主要セクション: 結論、観測面の表（discovery の3 reader／snapshots の history・recent_entries／crypto・stock の `_existing_keys`）、共有プリミティブ（`jsonl_read_diagnostics`/`parse_jsonl_row`/`read_jsonl_rows`/`mark_corrupt_row`/`corrupt_rows_note`）と診断 dict の形、対象7箇所の前後、fixture 実測6種（破損1行で既知URLが `new` になる／latest が1つ古い版へ落ちる／同一キーの再追記／summary キー不変／全行破損／CLI 警告行）、新規19件＋周辺171 passed、既知の先行失敗2件（Binance 時刻依存・stash 実測で無関係を確認）、未実施＝`ir_monitor_service.py` への配線は他カード稼働中のため見送り（`FingerprintStore.read_diagnostics()` で同情報が取得可能）。

### P5_MYPY_DEBT_20261024.md
- タイトル: P5: 既存 mypy 31エラー（paperloop系・jp_value_quality系の5ファイル）を動作変更なしで解消（2026-10-24）
- 目的: カード t_101fa036。`docs/P5_DEADEND_AUDIT_20261023.md` §4.4 の既存型負債（31 errors / 5 files）を、型注記・`cast`・`None` の狭めだけで 0 errors にした記録。
- 主要セクション: 結論、着手時の再実測（31 errors の内訳）、変更表（14箇所すべて型のみ・動作不変の根拠）、mypy before/after（5ファイル 31→0・残留理由なし）、検証実測（mypy rc=0 / ruff 12件・format 4ファイルは HEAD と同一箇所 / openapi-check rc=0 / tracked 75 tests rc=0 / 変更箇所を叩く未追跡140 tests rc=0 / HEAD worktree との出力JSONバイト一致 sha256 `7ea19fff…`）、`make verify` 全量は TSUKUMO に canonical repo が無く未達と明記、再現コマンド。

### WORKFLOW_DEADEND_AUDIT.md
- タイトル: Workflow Dead-end Audit — Private / Family Operator v1
- 目的: カード t_00f5e53b。監査日 2026-09-28・対象リビジョン 5a87ce7（git clean）で主要工程の行き止まり／欠落を診断・一覧化した監査（コード変更なし・診断と一覧化のみ）。
- 主要セクション: 監査の方法と限界、結論（要約）、工程 × 欠落の一覧、各工程の詳細、横断的な所見、原因と影響範囲（一覧）、推奨される後続カード（本カードの範囲外）、変更の有無。

### SILENT_FAILURE_AUDIT_20261023.md
- タイトル: サイレント失敗 静的監査 — yowayowa-investor
- 目的: 監査日 2026-10-23。`src/yowayowa` 配下の全 .py（241ファイル / 55,340行）を静的走査し、サイレント失敗の候補17件を列挙した監査（P5 各修正カードの出典）。
- 主要セクション: 走査コマンド（再現用）、前提となる構造的発見、候補（17件）、除外（140件）— 規則別内訳、未確認範囲（この監査が保証しないこと）、次にやるなら（提案・未実施）。

### OPENAPI_DRIFT_20261023.md
- タイトル: OpenAPI 契約ドリフト監査（2026-10-23・読み取り専用）
- 目的: 作業ツリーの `openapi.json` と `src/yowayowa/api/*.py` 実装ルートの食い違いを、コード・仕様ファイルの変更なし・サーバ起動なし・外部HTTPなしで全件実測した監査（報告のみ）。
- 主要セクション: 結論、実行方法と環境、実測値、危険度順の一覧、ルート差分の全件、パラメータ・必須/任意・レスポンス主要キーの食い違い（機械抽出の全件）、認証境界の実測、未確認範囲、次に起票すべき作業（提案・本監査では未実施）、付録（実行コマンドと出力・主要数値の最小再検証スクリプト・anonymous allowlist の再測定）、改訂履歴。

### P1D_JPX_MARGIN_DAILY_EVIDENCE.md
- タイトル: P1D Evidence — JPX daily margin balances: live production format
- 目的: タスク t_7a943308（roadmap P4「JPX daily margin: live production format / inspection」）。2026-10-23 検証（worker-go 実装＋自己検証・独立 review 要求つき）。実配布バイトでの仕様照合・取込検証・公開状況を実測した証跡。
- 主要セクション: Result in one paragraph、Retrieval log（全取得 2026-10-23）、Format verification against the official specification、Ingest verification（実配布バイト・ダミーデータ）、Live publication status（実データ・公開PDF）、Why the real machine-readable daily file was not ingested、Confirmation commands（reviewer）、Alternatives and recommendation、Artifacts and hygiene。

### P1D_JPX_REGRESSION_20261023.md
- タイトル: P1D Regression — JPX daily margin ingestion (re-run on a different path)
- 目的: タスク t_038f26ea（親 t_7a943308）。2026-10-23 に親と意図的に異なる経路で JPX 日次信用残の取込を再走し、全ハッシュ再計算の上で比較した回帰記録（fail-closed／atomicity probe・実値の意味論も第2データ経路で確認）。
- 主要セクション: Result in one paragraph、Inputs compared（全ハッシュ再計算）、Re-run paths（親と意図的に異なる）、Comparison result、Fail-closed / atomicity probe（re-run path）、Live re-fetch and real-value semantics（second data path）、Tests、Remaining issues（正直な限界）、Confirmation commands（reviewer）、Artifacts and hygiene。

### P4_EDINET_INDEX_BOOTSTRAP_20261023.md
- タイトル: P4: EDINET 提出インデックスの既存ブートストラップ実走（0行 → 部分カバー）
- 目的: カード t_2e038963（承認済みキューからの自動供給カード）。P5_DEADEND_AUDIT 差分表 G1（`edinet_index_days` / `edinet_filings` とも0行）を受け、2026-10-23 13:27:36Z〜13:27:48Z に既存経路のみでブートストラップを実走し 0行→部分カバーにした記録。
- 主要セクション: 結論、実行前後の実測（同一DB・同一経路）、実行方法（再現手順・既存経路のみ）、上限（31リクエスト）内であることの根拠、部分カバーの表現（「完全履歴」と偽らないことの実測）、実測で分かった注意点（既存挙動・変更していない）、0行だった原因（実測・要対応）、残り（部分カバーを完全にする道）、触っていないもの。

### SECURITY_ORDERBOOK_AUTH_20261023.md
- タイトル: /v1/orderbook/* 5本の認証境界を既存トークン境界へ揃える（t_f1023629）— 実測記録
- 目的: OPENAPI_DRIFT §4 P1（`/v1/orderbook/*` の5本が無認証）と §9-1 の修正。既存トークン境界へ揃え、修正前の実測（監査数値の再確認）→ 呼び出し側の実測（先に測った）→ 修正後の実測の順で記録。
- 主要セクション: 結論、変更内容、修正前の実測、呼び出し側の実測、修正後の実測、試験、残存リスク・未実施、再現コマンド。

### SECURITY_CRON_FAILCLOSED_20261023.md
- タイトル: /internal/cron/daily の設定依存 fail-open: 呼び出し元の実測と適用判断
- 目的: カード t_f8a7dd8f（実測 worker-goat・コードは変更していない）。認証ヘッダなしで到達し fail-open の通過に依存する実在の呼び出し元（Vercel Cron）を実測で特定し、「今は fail-closed にしない」判断と将来適用の手順（順序厳守・各段で実測）を記録。
- 主要セクション: 結論、対象コード（変更前の現状）、呼び出し元の棚卸し（実測）、現状の認証分岐と回帰試験、なぜ今 fail-closed にしないか、適用する場合の手順、将来適用する場合の差分（本カードでは未適用）、付随して見つかった同型の fail-open（対象外）、再現コマンド一覧（監査用）。

### PAPERLOOP_MONEY_REVIEW_20261023.md
- タイトル: PAPERLOOP 金銭計算レビュー（2026-10-23・読み取り専用）
- 目的: `src/yowayowa/paperloop.py`（3,109行）の金銭計算を、静的読解＋実ジャーナルの読み取り＋合成ジャーナルによる再現（scratch内のみ・コード変更なし）でレビューし、欠陥候補を優先度順に列挙（カード t_d33aef07）。
- 主要セクション: 対象関数と 入力 → 計算 → 出力、候補一覧（優先度順）、検査したが欠陥としなかった観点（除外理由）、未確認範囲、数値の出所。

### PAPERLOOP_C02_WEEK_RETURN_20261023.md
- タイトル: PAPERLOOP C-02 修正: `week_return` を単週の変化率にする（2026-10-23）
- 目的: `PAPERLOOP_MONEY_REVIEW_20261023.md` C-02【高】の修正。`paperloop.py` の `weekly_summary`（週次サマリー1行）について `week_return` を単週の変化率にする最小修正（HEAD fd9a1d0・実データは読み取りのみ）。
- 主要セクション: 仕様根拠（変更前の意図の確認）、変更内容（最小）、実測（実ジャーナル・読み取りのみ）、実測（同一の合成ジャーナル・3週間）、回帰テスト、表示経路。

### PAPERLOOP_C03_WEEK_SHORTFALL_20261023.md
- タイトル: PAPERLOOP C-03 修正: 週次 `shortfall_*` を単週に限定し、異常行で週次を止めない（2026-10-23）
- 目的: `PAPERLOOP_MONEY_REVIEW_20261023.md` C-03【高】の修正。`weekly_summary` の `shortfall_*` を単週に限定し、1行の異常・欠損で週次が止まらないようにする最小修正（書き込みは scratch のコピーのみ）。
- 主要セクション: 原因（2つ・同一箇所）、変更内容（最小）、実測（実ジャーナルのコピー・A帳簿）、実測（1行の欠損で週次が止まる／止まらない）、既存ファイル不変、回帰テスト、C-01 / C-02 との同居。

### PAPERLOOP_C04_MTM_UNPRICED_20261023.md
- タイトル: PAPERLOOP C-04 修正: mtm の欠測価格を「評価不能」として記録する（2026-10-23）
- 目的: `PAPERLOOP_MONEY_REVIEW_20261023.md` C-04【高】の修正。`run_daily_mtm` の欠測価格を 0 埋めでもクラッシュでもなく「評価不能」として記録する（原因2経路を実測で再現・基点 fd9a1d0、C-01〜C-03 の未コミット変更を含むツリーをそのまま起点）。
- 主要セクション: 原因（2経路・どちらも実測で再現）、修正方針（選択肢と採用理由）、diff（本体のみ）、回帰テスト（1本・pass）、前後実測、この修正の範囲外（記録）、証跡（同時編集の分離確認）。

### MONETIZATION_READING_20261023.md
- タイトル: yowayowa 収益検証の再開（2026-10-23 / t_dddae39e）
- 目的: 収益検証を妨げていた未接続工程（S-02 帳簿Cの比較対象が存在しなかった）を1件だけ閉じ、その前後を実データで測った記録。損益はデモ（ペーパー）の仮想元本 1.0 を 100 万円と読み替えた表示で、実際の入金・発注・約定は一切ない。
- 主要セクション: 選んだ工程、入力とコードの固定、実行で確認できたこと（コード変更が実運用に載った証拠）、費用後成績（最終 mtm 2026-10-23 00:21 UTC 時点）、回帰と既存成果の不変（同一ジャーナルのコピーで新旧比較）、未成立条件・限界、次にやること（本カード外）、レビュー第2ラウンド対応（2026-10-23 / audit-cld 差し戻し）。

### P3_LOOP_20261023.md
- タイトル: P3 収益ループ:未接続工程を1つ接続して実データで再走（2026-10-23 / t_e2c98d2b）
- 目的: P3（Close the profitability / learning loop）の「判定 → 記録 → 次回シグナル」のうち、記録された判定（エッジ寿命の PAUSE）が次回決定に届いていない1箇所を接続し、実データで前後を測った記録（本番データは1バイトも変えていない）。
- 主要セクション: 選んだ工程、接続した内容（1箇所だけ）、入力とコードの固定、実走（実データ）、費用後成績（実データ・最終 mtm 2026-10-23・評価バー 暗号10-22・株10-21）、未成立条件・限界、実測ログ（再現用コマンド）、コミットと本番ループへの到達（重要・帰属の注意）、次に残る未接続工程（1行）。

### P5_CLI_HTTP_ERRORS_20261023.md
- タイトル: P5 CLI HTTP 失敗表示の修正（t_8262d080）— 実測記録
- 目的: `P5_DEADEND_AUDIT_20261023.md` §2.3/§3 R1 の表示面だけの修正。CLI の HTTP 失敗表示を直し、正式エントリポイントの実プロセス実行と実ローカル API 実走（原本DB読取のみ）で前後を実測（全量 verify は TSUKUMO-KUN）。
- 主要セクション: 変更内容（3ファイル + 本doc）、設計の要点、コード hash、対象試験（VPS・狭小）、前後実測、実ローカル API での実走、証跡（scratch・24hで消える前提）、make verify 全量（TSUKUMO-KUN・host rc記録）、未達・限界（正直な記録）。

### P5_FUNDAMENTALS_FAILURE_SIGNAL_20261023.md
- タイトル: P5: Yahoo 財務諸表の「取得失敗」と「データ無し」の分離
- 目的: カード t_5f0f572b（承認済みキューからの自動供給カード）。`SILENT_FAILURE_AUDIT_20261023.md` §3 #6 / #7（影響「中」）の修正。provider 内で完結する変更で両者を分離し、同一 fixture・ネットワーク呼び出しなしで前後を実測。
- 主要セクション: 結論、修正前後の実測（同一 fixture・ネットワーク呼び出しなし）、変更点（provider 内で完結）、呼び出し側への影響範囲（実測ベース）、試験、既知の限界（本カードの範囲外・正直な申し送り）、再現コマンド。

### P5_R2_RESEARCH_UNKNOWN_SYMBOL_20261023.md
- タイトル: P5 R2: 未知シンボルの research 応答を fail-closed にする
- 目的: カード t_65bed735（yowayowa P5 / R2・P0）。`P5_DEADEND_AUDIT_20261023.md` §3 R2・§7 推奨2 の修正。未知シンボルへの research 応答を fail-closed にし、修正前の再現と修正後の実測を記録。
- 主要セクション: 修正前の実測（再現）、修正内容、修正後の実測、試験結果（実測 rc）、残存件数・残り、触っていないもの（指示どおり）。

### P5_R5_QUOTE_SIZING_IDEA_20261023.md
- タイトル: P5 R5 — quoteからサイズ検討用ideaを生成し、価格・通貨・出典JSONの手入力をなくす
- 目的: カード t_463a2a9c（worker-flash）。`P5_DEADEND_AUDIT_20261023.md` §2.4/§3 R5（P1）の修正。quote からサイズ検討用 idea を生成して価格・通貨・出典JSONの手入力をなくし（再検証完了 2026-10-24 00:57 JST）、生成 idea から既存 sizing-proposals までを実測（scratch DB・隔離fixture・発注0）。
- 主要セクション: 結論、変更ファイルとコードhash（sha256・未commit）、失敗コード表（service固定code → APIステータス）、固定入力（fixture）と実データの区別、実測（既存 yahoo provider の実取得・再検証ラン）、自動テスト、生成idea → 既存 sizing-proposals 実測、TSUKUMO 既存環境での全量 make verify、非変更範囲と hotspot、残る項目（正直な申告）、検収への依頼（同系統検収の限界）。

### P5_R10_RISK_BENCHMARK_20261023.md
- タイトル: P5 R10 — ベンチマーク相関の無音縮退をやめる
- 目的: `P5_DEADEND_AUDIT_20261023.md` §3 R10（P2）の修正。`services/risk.py` と `providers/yahoo_risk.py` を対象に、ベンチマーク相関の無音縮退を additive に・数値を捏造せずに改める。
- 主要セクション: 問題（R10）、対策（additive・数値は捏造しない）、実測 API 出力（`GET /v1/portfolios/1/risk?benchmark=<B>`）、実測 CLI 出力、試験（実測）、静的検査（実測）、コード差分（要約）、制約・前提の説明、受入チェック。

### P5_R11_SCREEN_METRICS_20261023.md
- タイトル: P5 R11: `screen` の指標を「名前と値」の表に分離する（実測記録）
- 目的: カード t_d8dcfdcf（worker-goat）。`P5_DEADEND_AUDIT_20261023.md` §3 R11（行き止まりではない運用摩擦）の修正。`cli.py` の screen 描画ブロックと新規テスト1本のみの変更で、指標を「名前と値」の表に分離（対象リビジョン f3aed65）。
- 主要セクション: この変更で固定する見せ方、入力の同定（hash 実測）、実装、固定入力での前後比較（同一レスポンス）、実APIの実値での実走（7203.T / 6758.T）、境界試験（`tests/test_screen_metrics_display.py` 12件）、検証結果（host と rc を明記）、制約遵守の実測、未達・限界（正直な記録）。

### P5_SCREENING_SOURCE_DATES_20261023.md
- タイトル: P5: screening候補一覧で実行日と資料基準日・取得時刻を区別する（実測記録）
- 目的: カード t_8b9aa12f（worker-flash）。run_date=2026-10-23 の163候補のうち EDINET 117行の資料は 2026-08-12〜10-22 提出で、実行日だけでは資料の新しさを判定できない問題（`P5_DEADEND_AUDIT_20261023.md` §2.2・§6）を日付契約の固定で解く（対象リビジョン fd9a1d0）。
- 主要セクション: 日付契約（この変更で固定する見せ方）、入力の同定（hash実測）、実測: 前後の出力（同一DBコピー・同一環境・COLUMNS=250）、`unavailable` の実測について、差分（本カードの全変更）、制約遵守の実測、検証結果（host と rc を明記）、既知の表示制限（正直な記録）。

### P5_DOCUMENT_PARTIAL_EXTRACTION_20261024.md
- タイトル: P5 検証記録: 文書部分抽出の可視化（PDF表走査中断・XLSX欠落ワークシート部品）
- 目的: タスク t_0c4f378f（yowayowa P5）。`SILENT_FAILURE_AUDIT_20261023.md` §3 #9/#10 の可視化。PDF表走査の中断と XLSX 欠落ワークシート部品を additive に可視化し、GREEN 実測と note の伝搬（既存配線の確認・実 registry/DB への書込なし）を記録。
- 主要セクション: 問題（修正前の実物）、実装（確定設計どおり・additive）、コードハッシュ、修正後の実物（GREEN 実測・VPS rc=0）、note の伝搬、静的検査・全量検証（host / rc 付き）、TSUKUMO make verify 実測、未確認範囲・限界、制約遵守の自己申告。

### PAPERLOOP_COMPARISON_ENDPOINT_20261024.md
- タイトル: PAPERLOOP 週次比較行: book と benchmark の as-of 終点が違う日なら fail-closed にする（v1.9）
- 目的: カード t_94687d03（worker-goat）。親カード t_75cade36 の検収で audit-cld が見つけた残り（比較の両端が別の日でも値が出る）を、`_comparison_line` の終点不一致で fail-closed にする（`comparison_reason=endpoint_mismatch`・両 as-of は残す）。代替案（共有 mark まで切り詰め）を採らない理由も明記。
- 主要セクション: 決めたこと（2案の比較と採用理由）、波及した top-level 2列、入力/コードのハッシュ、v1.8 相当との A/B 実測（実データ3週 = no-op、合成ジャーナルで +10.0pp → None）、試験（89 passed・ruff/mypy の既存指摘のみ）、未確認範囲（親カードのハッシュが再現しない点を含む）。
