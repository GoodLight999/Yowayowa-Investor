# docs/INDEX_ja.md — ドキュメント日本語インデックス

対象: `docs/` 配下の全 Markdown 21 ファイル。各ファイルのタイトル/目的/主要セクションを3行で要約。
最終更新: 2026-09-28（JST）。内容の正本は各英語原文ドキュメント。食い違いがあれば原文を優先。

## 目次（カテゴリ別）

- 全体像・運用: ARCHITECTURE / PRIVATE_OPERATOR_ROADMAP / AUTONOMOUS_AGENT_HANDOFF / OPERATOR_MODE / AGENT_MESSAGE_BOARD / AGENT_ENVIRONMENT
- データソース・取得: DATA_POLICY / SEC / EDINET / ESTAT / JPX_DAILY_MARGIN / CREDIT_MARGIN / CRYPTO / STOCKS / MS2_RSS
- ブローカー・実行: BROKER_ACCEPTANCE_MATRIX / RAKUTEN_WEB_SESSION
- 研究・AI: RESEARCH_BRIEF
- 研究基盤: BACKTEST_FOUNDATION_DESIGN
- 実測証跡: P1C_EVIDENCE / P1D_EVIDENCE

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
- 主要セクション: Publication facts（全TSE上場銘柄・前営業日適用・16:00頃公表・金額列は2026-09-25以降）、Official URLs（TMI参照ページ・仕様PDF・サンプルZIP・J-Quants Pro API）、「日々公表信用取引残高」との混同禁止、Observed CSV format（公式サンプルから実測: 18列・英語CSVはUTF-8無BOM/日本語はCP932・CRLF・一般/制度の売買別株数・金額）。

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

## 実測証跡

### P1C_EVIDENCE.md
- タイトル: P1C 実測証跡（Company IR acquisition）
- 目的: 企業IR取得パイプライン（P1C）の実データでの受け入れ証跡。ニトリHDの実サイトで end-to-end 検証した結果を記録する。
- 主要セクション: 1. 実データ end-to-end 11/11 PASS（106文書検出・6取得・売上収益912,248百万円の実KPI取得・画像PDFは fail-closed・冪等性）、2. previous-version diff 9/9 PASS、3. 実データで発見・修正した欠陥、4. 回帰テスト、5. verify / CI / 本番、6. 非交叉制約、7-8. 追補（F1/F2修正・F3: CSV解析+XLSX/CSV多列KPI抽出・unchanged(seen)分離）。

### P1D_EVIDENCE.md
- タイトル: P1D Evidence — Authorized private information sources (mailbox)
- 目的: 認可済み private 情報源（オペレーター自身のGmailメールボックス・read-only）コネクタ（P1D）の受け入れ証跡。
- 主要セクション: 1. Acceptance criteria（gog read-only メールボックスコネクタ・+123テストで633全Green）、2. 実データ end-to-end 検証（楽天証券「銘柄情報通知サービス」メール10通から決算イベント11件抽出・40通スキャンでtimeline 70件追加・再実行冪等・provenance完全・鍵無し時は AUTH_EXPIRED の fail-closed）、3. Static checks / full suite、4. Frozen-contract check（P1A〜P1C凍結ファイルへの非交叉）、5. Secrets（keyringパスワードは実行時のみ・実メール内容はテストに含めない）。
