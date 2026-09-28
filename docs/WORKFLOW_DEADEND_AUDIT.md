# Workflow Dead-end Audit — Private / Family Operator v1

- 監査日: 2026-09-28 (JST)
- 対象リビジョン: `agent/commercial-foundation` @ `5a87ce7`（`git status` clean、未コミットなし）
- カード: t_00f5e53b（本カードは**診断と一覧化のみ**。コード変更は行っていない）
- 一次情報: `AGENTS.md` / `docs/PRIVATE_OPERATOR_ROADMAP.md` / `docs/AUTONOMOUS_AGENT_HANDOFF.md` / `docs/AGENT_MESSAGE_BOARD.md` / `DESIGN.md` / `docs/OPERATOR_MODE.md` / `docs/BROKER_ACCEPTANCE_MATRIX.md`

## 監査の方法と限界（先に明記）

確認した面:

1. API 定義: `src/yowayowa/api/*.py` の `@router` 全件、`api/app.py` の `include_router` 27件
2. サービス: `src/yowayowa/services/*.py` の実装実読（screening / research_ask / broker_execution / order_inquiry / strategy_calibration / alerts / portfolios / operations ほか）
3. CLI: `cli_entry.py` の全コマンド登録、`*_cli.py`
4. UI: `web/templates/*.html` 20枚、`web/static/*.js` 27本、`web/assets.py` のページ配線、`base.html` のナビ
5. 実データ: `data/yowayowa.db` の実カウント、`data/*.jsonl`、`data/*/` の実在確認
6. スケジューラ: Hermes cron ジョブ実体（`/root/.hermes/cron/jobs.json` および各プロファイルの `cron/jobs.json`）、`vercel.json` crons、`crontab -l`、`systemd` timers

**辿れなかった範囲（NEED-HUMAN または環境制約）:**

- **実ブラウザでの画面操作は行っていない。** 「到達不能」の判定はリンク/fetch 先の有無・ルート登録・静的解析で決定的に確認できるため静的経路解析で確定した。見た目・体感の摩擦は本監査の対象外（各項に再現手順を付し、後続カードで実機確認可能）。
- **実楽天セッション（login/MFA）を要する検証は行っていない**（カード制約）。該当項目は NEED-HUMAN として明記する。
- **実 ChatGPT device auth を要する検証は行っていない**（同上）。
- ローカルサーバー起動による API 実打鍵は行っていない（並列タスクの worktree 競合を避けるため）。ルート・応答モデルはコード定義で確認した。

---

## 結論（要約）

`discover -> investigate -> falsify -> compare -> size -> propose execution -> execute -> observe outcome -> recalibrate` の **9工程すべてを辿った**。結果:

- **前半4工程（discover / investigate / falsify / compare）は UI 内でおおむね完結**する。ただし discover の一等地である機械スクリーニング候補が UI に届いておらず、investigate から falsify（深掘り研究）への**逆走不能**がある。
- **後半5工程（size / propose execution / execute / observe outcome / recalibrate）は UI から到達不能**。API/CLI/AI ツールのいずれかにあるが、**画面からは一切見えない**。これは「Yowayowa から離れずに完結できるか」という P5 終了条件に直接抵触する。
- さらに **size と observe outcome は機能自体が未接続**（サイズ計算サービスが存在せず、約定が観測系と portfolio に入らない）。
- **P5-A の中核 2 機能（`research_ask` / morning brief）は UI から呼ばれていない**（CLI/API/Telegram のみ）。brief は運用でも配信されていない。

一覧は **23 件**（P0 = 10件・うち EDINET データ停止 2件、P1 = 9件、P2 = 4件）。詳細は下表と各工程の節。

**追加の重要発見（実施中に判明）**: cron は「動いている」のに**データが増えていない**経路が 2 つあった。EDINET インデックスはキー未注入で毎日 skip し（`ok` 報告のまま 0 日同期）、EDINET daily は 06:30 JST に当日分を要求するため 4 日連続で 0 件だった（最終取込 2026-09-24）。**EDINET に依存する全ワークフローは現在空データで動いている**（→ G1/G2）。

---

## 工程 × 欠落の一覧

| # | 工程 | 欠落 | 重要度 | 出所（ファイル:行） | 再現手順 | 推定工数 |
|---|---|---|---|---|---|---|
| D1 | discover | 機械スクリーニング候補が UI に一切出ない。`/v1/screening/*` への web 参照ゼロ | **P0** | `api/screening_routes.py:66,85`、`web/` 内 `/v1/screening` 参照 0件 | `grep -rn '/v1/screening' src/yowayowa/web/` → 0件 | 2〜3日（画面+定期実行） |
| D2 | discover | 暗号資産 OHLCV が UI に出ず、実データも存在しない（`data/crypto-ohlcv` 不在） | **P0** | `services/research_ask.py:55`、`web/` 内 `/v1/crypto` 参照 0件 | `ls data/crypto-ohlcv` → 不在 | 1〜2日（配線+初回取得） |
| D3 | discover | 定期更新の穴: screening / 信用残 / JPX / crypto のジョブなし。EDINET index は 0 日同期 | **P1** | cron 実体（root 4件 + cto-hephaestus 3件）、DB 実測 | `sqlite3 data/yowayowa.db "select count(*) from edinet_index_days;"` → 0 | 1〜2日 |
| I1 | investigate | instrument 画面から deep research（`/research/{symbol}`）への導線が存在しない（discover 側 1箇所からのみ） | **P1** | `web/static/discover.js:363`、`instrument.html` / `instrument*.js` に `/research/` 参照 0件 | `grep -rn '/research/' src/yowayowa/web/templates/instrument.html src/yowayowa/web/static/instrument*.js` → 0件 | 0.5日 |
| I2 | investigate | `research_ask`（P5-A 中核）が UI から呼ばれない | **P1** | `web/` 内 `/v1/research/ask` 参照 0件、`research_cli.py:113`（CLIのみ） | `grep -rn 'research/ask' src/yowayowa/web/` → 0件 | 2〜3日 |
| F1 | falsify | 反証・仮説の永続化がない（hypothesis/invalidation レコード 0件）。`research_ask` の `invalidation_conditions` は応答限りで消える | **P1** | `services/` に `hypothesis` 参照 0件、`PRIVATE_OPERATOR_ROADMAP.md:412` | `grep -rn 'hypothesis' src/yowayowa --include='*.py'` → 0件 | 3〜5日 |
| C1 | compare | compare → AI の動線がない（比較結果を AI に問えない） | **P2** | `web/static/compare.js` に `/ai` 参照 0件（discover は `discover.js:473`） | `grep -n '/ai' src/yowayowa/web/static/compare.js` → 0件 | 0.5日 |
| S1 | size | サイズ決定（何株買うか）の機能が存在しない | **P0** | `services/` に `sizing` 参照 0件、`PRIVATE_OPERATOR_ROADMAP.md:411` | `grep -rn 'sizing' src/yowayowa --include='*.py'` → 0件 | 3〜5日 |
| S2 | size | portfolio の保有が手入力/CSV のみ。ブローカー実ポジション（broker-read `positions`）からの取り込み経路ゼロ | **P0** | `api/import_routes.py:17`（CSV必須列 `symbol,quantity,average_cost,currency`）、`web/static/portfolio.js` に broker 参照 0件 | `grep -rn 'broker' src/yowayowa/web/static/portfolio.js` → 0件 | 1〜2日 |
| P1′ | propose | AI エージェントの 25 ツールに発注提案ツールがない（broker 参照 0） | **P0** | `services/ai_agent.py:613-960`（`ToolSpec(` 25件）、同ファイル broker 参照 0件 | `grep -c broker src/yowayowa/services/ai_agent.py` → 0 | 2〜3日 |
| P2′ | propose | 調査結果 → 発注提案の自動連携がない（`source_research_link` は手入力の自由文字列） | **P1** | `broker/execution/models.py:39`、`broker_execution_cli.py:171` | proposal 作成に `--motivation` 必須 / `--source-research-link` 手入力 | 1〜2日 |
| E1 | execute | 発注の提案・評価・実行・結果確認のすべてが UI から到達不能（CLI 専用）。HTTP 送信ルートは意図的に不存在 | **P0** | `web/` 内 `/v1/broker-execution` 参照 0件、`broker_execution_cli.py:257`、`AGENTS.md:29` | `grep -rn 'broker' src/yowayowa/web/` → 0件 | 4〜6日 |
| O1 | observe | 約定（executions）が観測系に入らない。`_audit_only_order` は `filled_quantity=0` / `average_fill_price=None` 固定 | **P0** | `services/order_inquiry_service.py:294-311`、`operator_bridge/rakuten_web.py:898`（`normalize_executions` は broker-read 側のみ） | `grep -n executions src/yowayowa/services/order_inquiry_service.py` → 0件 | 2〜3日 |
| O2 | observe | 約定 → portfolio 反映の経路がゼロ（fill reconciliation / portfolio update 未接続） | **P0** | `services/order_inquiry_service.py` に portfolio 参照 0件 | `grep -n portfolio src/yowayowa/services/order_inquiry_service.py` → 0件 | 2〜4日 |
| O3 | observe | 発注/約定の観測表面が UI にない | **P1** | `PRIVATE_OPERATOR_ROADMAP.md:348-354`（API/CLI のみ）、`web/` 参照 0件 | 同 E1 | 2〜3日 |
| R1 | recalibrate | 校正表面が UI にない（API/AIツールのみ） | **P1** | `api/fundamentals_routes.py:184,221`、`web/` に `strategy-research` 参照 0件 | `grep -rn 'strategy-research' src/yowayowa/web/` → 0件 | 2〜3日 |
| R2 | recalibrate | 校正が実データで回らない（`strategy_research_snapshots` 0行 / 定期生成なし / walk-forward 未実装） | **P1** | DB 実測 0行、`services/strategy_calibration.py:227-231` | `sqlite3 data/yowayowa.db "select count(*) from strategy_research_snapshots;"` → 0 | 3〜5日 |
| B1 | 横断 | morning brief が UI から見えず、運用でも配信されていない（`--send` なし・cron `deliver: local`） | **P1** | `research_cli.py:62,108`、`/root/.hermes/scripts/yowayowa_brief_daily.sh`（`--send` なし）、`jobs.json` の `"deliver": "local"` | 同左 | 0.5日 |
| B2 | 横断 | アラート / イベント購読の push が存在しない（画面を開かないと発火に気づけない） | **P2** | `services/alerts.py`（notify 参照 0件）、`web/static/alerts.js:233`（手動評価ボタンのみ） | `grep -n 'notify\|telegram' src/yowayowa/services/alerts.py` → 0件 | 2〜3日 |
| B3 | 横断 | データ供給の偏り: roadmap が DONE とする credit/jpx/screening/crypto に運用データなし（各 0 行） | **P2** | DB 実測、`PRIVATE_OPERATOR_ROADMAP.md:42` | `sqlite3 data/yowayowa.db "select count(*) from credit_margin_weekly;"` → 0 | 1日（取得+登録） |
| B4 | 横断 | 未参照 CSS（`mobile-nav.css` ほか3件）が読み込まれていない | **P2** | `web/static/mobile-nav.css`（全リポジトリ内参照 0件）、`base.html:10-15` は 6本のみ link | `grep -rn 'mobile-nav' src/ tests/` → 0件 | 0.5日（削除 or 配線判断） |
| G1 | 横断 | EDINET index ジョブがキー未注入で毎日 skip（`ok` 報告のまま 0 日同期） | **P0** | `p4b_edinet_index.py:15`、`cron/output/ff396d46901f/2026-09-28_06-30-36.md:9` | `cat` 当該 output → `skip: YOWAYOWA_EDINET_API_KEY is not set` | 0.5日（設定） |
| G2 | 横断 | EDINET daily が 06:30 JST に当日分を要求するため毎回 0 件（4日連続、最終取込 2026-09-24） | **P0** | `/root/.hermes/scripts/yowayowa_edinet_daily.sh:11-13`、`cron/output/bc47b8dfde18/*.md` | `tail -1 data/edinet-daily.jsonl` → submitDateTime 2026-09-24 | 0.5日（時刻/対象日修正） |

推定工数は**私の概算（推測）**であり、実装判断は CTO が行う。

---

## 各工程の詳細

### 1. discover — 到達した / 欠落

**辿れた経路**: `/discover`（`app.py:406`）→ `discover.js`。①`/v1/discover/catalog` + `/v1/strategy-presets`（`discover.js:481-482`）でプリセット読込、②`/v1/discover/screen` で一覧（`discover.js:397`）、③戦略選択時は `/v1/strategy-presets/{id}/evaluate` に `record: true` で送信し point-in-time スナップショットを記録（`discover.js:317-319`、記録実装 `api/fundamentals_routes.py:320`）。結果行から `/instrument/{symbol}`（`discover.js:351`）と `/research/{symbol}`（`:363`）、複数選択から `/compare?symbols=`（`:463`）と `/ai?...`（`:473`）、watchlist 追加（`:417-421`）、CSV エクスポート（`:449-453`）へ出られる。**discover はハブとして機能している。**

**欠落**:

- **D1 (P0): 機械スクリーニング（P4-D）の候補が UI に出ない。** `screening_pipeline.run_screening_pipeline` の出力は `POST /v1/screening/run` と `GET /v1/screening/candidates`（`screening_routes.py:66,85`）にあり、AI ツール `get_screening_candidates`（`ai_agent.py:933`）も存在する。しかし `web/` 配下に `/v1/screening` を呼ぶ箇所が 1 つもない。`/discover` は Yahoo スクリーナー（`/v1/discover/screen`）で完結しており、EDINET daily + 信用残 + スクリーナーを統合した機械スクリーニングへは画面から到達できない。
- **D2 (P0): 暗号資産が実質不存在。** `get_ohlcv(market="crypto")` は `./data/crypto-ohlcv` を読む（`ai_agent.py:1339`、定義 `:1314`、`research_ask.py:55`）。`data/crypto-ohlcv` はディレクトリ自体が存在せず、`crypto-fetch` を定期実行するジョブもない。roadmap は「crypto/US OHLCV」を DONE-equivalent とするが（`PRIVATE_OPERATOR_ROADMAP.md:42`）、**コードはあるが運用データはない**。UI にも crypto 表面はない（`web/static/settings.js:387,393` の `crypto.*` はブラウザの WebCrypto API であり、資産としての暗号資産とは無関係）。
- **D3 (P1): 定期更新は一部しか回っていない。** Hermes cron の yowayowa ジョブは**プロファイルを跨いで** 7 件存在する:
  - `/root/.hermes/cron/jobs.json`: `yowayowa-alpaca-daily`(08:30 JST)、`yowayowa-edinet-daily`(06:30 JST、EDINET daily JSONL)、`yowayowa-macro-daily`(07:00 JST、macro JSONL)、`yowayowa-brief-daily`(09:00 JST、morning brief)
  - `/root/.hermes/profiles/cto-hephaestus/cron/jobs.json`: `p4b-macro-daily-ingest`(07:30)、`p4b-edinet-daily-index`(06:30)、`p4b-ir-daily-monitor`(08:30)

  実データも供給されている（`data/macro-observations/{bls,fred,treasury}.jsonl` 計 24,361 行、`data/edinet-daily.jsonl` 434 行、`data/stock-ohlcv/{AAPL,MSFT,NVDA}`、`data/private-acquisition/ir-*`）。
  **回っていないもの**: 機械スクリーニング（screening-run を呼ぶジョブ 0 件）、信用残/JPX 日次（0 件）、crypto（0 件、`data/crypto-ohlcv` 不在）。さらに **`edinet_index_days` は 0 行・`edinet_filings` は 0 行**で、EDINET キーは `.env` に存在する（`grep -c '^EDINET_API_KEY=' /root/.hermes/.env` → 1）にもかかわらず **EDINET インデックスが 1 日も同期していない**（`p4b-edinet-daily-index` の実行結果が未反映）。`vercel.json` の cron は `/internal/cron/daily` 1 本（`0 22 * * *`）で、これは EDINET index・アラート評価・portfolio snapshot のみ（`api/app.py:321-370`）。

### 2. investigate — 到達した / 欠落

**辿れた経路**: `/instrument/{symbol}`（`app.py:416`）は `instrument.js` + `instrument_ux.js` + 共通 `instrument_context.js` を積む（`assets.py:15`）。valuation / fundamentals / history+indicators / quotes / watchlist トグル、`instrument_context.js` が news（`:64`）・calendar（`:70`）・EDINET 開示（`:77`）を並行取得する。`/edinet?security_code=` への原典リンクもある（`instrument_context.js:58`）。`/research/{symbol}`（`app.py:421`）は 9 セクションのタブ（`research.html:14-24`）を持ち `/v1/research/{symbol}?sections=` を引く。

**欠落**:

- **I1 (P1): 逆行不能。** `/research/{symbol}` **画面**への入口は discover の結果表 1 箇所のみ（`discover.js:363`）。`instrument.html` にも `instrument*.js` にも `/research/{symbol}` 画面へのリンクは 0 件（`instrument_ux.js:196` は `/v1/research/{symbol}?sections=profile,analyst` を呼ぶが、これは instrument 画面内のアナリスト欄を埋めるデータ取得であり、deep research 画面への導線ではない）。つまり「銘柄を開いてから財務・出典を眺めて深掘りしたくなる」という最も自然な順序で deep research に入れない。`research.html:10` には `/instrument/{symbol}` への戻りリンクがあるので、**discover → research → instrument は行けるが instrument → research は行けない**（一方通行）。
- **I2 (P1): `research_ask` が UI にない。** P5-A の中核（cited research Q&A）は `research_cli.py:113`（CLI `research-ask`）と `/v1/research/ask`（`research_llm_routes.py:70`）にあるが、`web/` に `/v1/research/ask` 参照が 0 件。`/ai` 画面（`ai.js:295` の `/v1/ai/chat`）は別系統であり、出所分離（facts/calculations/inferences/missing_inputs）を備えた research_ask の応答は画面に出ない。

### 3. falsify — 到達した / 欠落

**辿れた経路**: `research_ask` は `invalidation_conditions` を返し、`### 推論` / `### 反証条件` を決定論的にパースして `supporting_fact_ids` を保持する（`services/research_ask.py:232`、`docs/AGENT_MESSAGE_BOARD.md` CG-006 で検証済み）。AI 戦略トリアージも notes で「最初の棄却条件を能動的に試せ」と指示する（`ai_agent.py:1160-1161`）。**反証の"実行"は AI ターン内で行われている。**

**欠落**:

- **F1 (P1): 反証の永続化がない。** `hypothesis` の語は `src/yowayowa` の Python に 1 件も存在せず（`grep -rn hypothesis --include='*.py'` → 0件）、`invalidation_conditions` は `ResearchAskResponse` の応答内に閉じる。roadmap の P3 残項目「hypothesis/invalidation records that can later be evaluated」は NOT STARTED（`PRIVATE_OPERATOR_ROADMAP.md:412`）。結果として「あの時こう考え、この条件で棄却するはずだった」を後から検証できない（recalibrate 工程 R2 と直結）。

### 4. compare — 到達した / 欠落

**辿れた経路**: `/compare`（`app.py:426`）、`compare.js` は `/v1/compare/metrics`（`:152`）と `/v1/compare`（`:236`）を呼び、行のシンボルは `/instrument/{symbol}` にリンクする（`:116`）。プリセット保存/削除あり（`:197-222`）。`?symbols=` の受け渡しが discover・portfolio からある（`discover.js:463`、`compare.js:286` の main watchlist 利用）。

**欠落**:

- **C1 (P2): compare → AI の動線がない。** `compare.js` に `/ai` 参照が 0 件（discover は `discover.js:473` で AI に文脈を渡している）。比較画面で指標を並べた後、「この差はなぜか」を AI に問うには、手で `/ai` に移動して銘柄を打ち直す必要がある。

### 5. size — 到達した / 欠落

**辿れた経路**: `/portfolio`（`app.py:411`）の `portfolio.js` は `/v1/portfolios/{id}/risk`（`:252`）・`/analytics`（`:288`）・`/snapshots`（`:263`）でリターン・ボラ・シャープ・最大DD・相関を出す（実装 `services/risk.py:119`）。**リスクの測定までは画面で完結する。**

**欠落（本監査の最重要の一つ）**:

- **S1 (P0): サイズ決定機能が存在しない。** `src/yowayowa` の Python に `sizing` は 1 件も存在せず（`grep -rn sizing --include='*.py'` → 0件）、roadmap の P3 残項目「portfolio-aware sizing proposals」は NOT STARTED（`PRIVATE_OPERATOR_ROADMAP.md:411`）。発注提案の数量は `--quantity` を手で入れる（`broker_execution_cli.py:165`）。つまり**「どれだけ買うか」がワークフローから欠落**しており、9工程の `size` は実質空である。
- **S2 (P0): portfolio とブローカー実態が接続しない。** 保有は `/v1/portfolios/{id}/positions`（手入力）または `/v1/portfolios/{id}/import.csv`（CSV 必須列 `symbol,quantity,average_cost,currency`、`api/import_routes.py:17,37`）のみ。ブローカー実ポジションは broker-read の `positions` リソース（`api/broker_read_routes.py:47-121` の `/connectors[/{id}]` + `POST .../fetch`、リソース定義は `operator_bridge/rakuten_web.py` のカタログ）から取得できるが、`portfolio.js` に broker 参照が 0 件で、broker-read → portfolio の取り込み経路も存在しない。手入力の穴であると同時に、**リスク分析が実ポジションとずれ得る**。

### 6. propose execution — 到達した / 欠落

**辿れた経路**: `POST /v1/broker-execution/proposals`（`broker_execution_routes.py:97`、`motivation` 必須 `:53`、`source_research_link` 任意 `:54`）と `POST .../evaluate`（`:135`）、`GET /audit`（`:156`）、CLI `yowayowa broker-exec proposals-create` / `proposals-evaluate`（`broker_execution_cli.py:159,222`）。proposal は payload から audit に intent として永続化され、hash で冪等（P2A/F1-F3 で強化済み）。

**欠落**:

- **P1′ (P0): AI が発注提案を形成できない。** 25-tool カタログ（`ai_agent.py:610-960`、`ToolSpec(` 25件）に broker 系が 1 つもない（`grep -c broker ai_agent.py` → 0）。`invoke` されるのは watchlist / compare / screen / chart の**作業用提案のみ**（`_proposal` `:1396`、`OperationKind` は `domain.py:355-360` の 5 種）。roadmap は「AI should lead ... portfolio/execution proposal formation」を掲げるが（`AUTONOMOUS_AGENT_HANDOFF.md:626-628`）、**発注提案は完全に手作業**である。
- **P2′ (P1): 調査 → 提案の自動連携がない。** `source_research_link` は任意の自由文字列で、`research_ask` の応答やスナップショット ID を自動で埋める仕組みはない（`broker/execution/models.py:39`、CLI `:171`）。

### 7. execute — 到達した / 欠落

**辿れた経路（コードとしては）**: `yowayowa broker-exec submit <client_order_id>`（`broker_execution_cli.py:257`）。fail-closed な多段ゲート（`submissions_enabled` 既定 False → `stage(submit-frozen)` で拒否し、proposal 参照・セッション・DOM・submit 監査書き込みのいずれも行わない）は `transport.submit_order` に実装され、`--armed` + notional/日次上限 + 認証済みセッションが必要（`services/broker_execution.py:20-47`、`broker/execution/interlocks.py:65-92`）。HTTP 送信ルートは**意図的に不存在**（`AGENTS.md:29`）。

**欠落**:

- **E1 (P0): 実行フェーズが UI から完全に切り離されている。** `web/` 配下に broker 参照が 0 件。「Yowayowa から離れずに完結」を掲げる以上、提案・評価・実行・結果確認がすべてターミナル CLI 専用なのは設計上の死角である。安全ゲートを UI に移植せよという意味ではなく、**安全ゲートを保ったまま画面から到達できる表面（提案作成と承認・実行結果の閲覧）が存在しない**ことが問題。
- **NEED-HUMAN**: 実楽天セッション（login/MFA）がなければ write 系は 1 行も検証できない（`docs/BROKER_ACCEPTANCE_MATRIX.md` の W1-W8）。本監査では実施していない（カード制約）。
- 参考（既知の設計判断で欠落ではない）: 取消は `cancels_enabled` ゲートと実セッション証拠が揃うまで**意図的に未実装**（`transport.py:389-391`、`docs/OPERATOR_MODE.md:243-250`）。P2C2 の設計凍結どおりで、本監査では欠陥に数えない。

### 8. observe outcome — 到達した / 欠落

**辿れた経路**: `GET /v1/broker-execution/orders`（`broker_execution_routes.py:199`）と `/orders/{client_order_id}`（`:214`）。`OrderInquiryService` は broker-read で `open_orders` と `order_history` を取得し、`broker_order_id` で dedupe して audit と突合、`audit_matched` / `unmatched_web` / `audit_only` に分類する（`services/order_inquiry_service.py:91-100,209-240`）。

**欠落（P2 の exit criteria を直撃）**:

- **O1 (P0): 約定（executions）が観測に入らない。** `order_inquiry_service.py` に `executions` 参照が 0 件。`normalize_executions` は broker-read 側に実装済み（`operator_bridge/rakuten_web.py:898`、`broker_read_service.py:269`）だが、inquiry は `open_orders` + `order_history` のみを merge する。結果、`_audit_only_order` は `filled_quantity=0` / `average_fill_price=None` を返し（`order_inquiry_service.py:294-311`）、**実データの約定価格・数量・手数料が観測表面に出ない**。
- **O2 (P0): 約定 → portfolio 反映がゼロ。** `order_inquiry_service.py` に portfolio 参照 0 件。roadmap の P2 exit criteria「research -> order proposal -> ... -> status/fill -> portfolio update」は未接続（`PRIVATE_OPERATOR_ROADMAP.md:368-370`）。
- **O3 (P1): 観測表面が UI にない。** 上記 E1 と同根（`web/` に broker 参照 0 件）。roadmap の P2C1 チェックポイントも「Surfaces: `GET /v1/broker-execution/orders` ... CLI `yowayowa broker-exec orders`」と HTTP/CLI のみを列挙し、画面表面を持たない（`PRIVATE_OPERATOR_ROADMAP.md:348-354`）。

### 9. recalibrate — 到達した / 欠落

**辿れた経路**: `GET /v1/strategy-research/outcomes`（`fundamentals_routes.py:184`）と `/calibration`（`:221`）が `forward_outcome_report` → `calibration_report` を返す（`services/strategy_calibration.py`）。AI ツール `get_strategy_calibration`（`ai_agent.py:760` 付近）からも参照可能。スナップショットは discover の `record: true` で記録される。

**欠落**:

- **R1 (P1): 校正表面が UI にない。** `web/` に `strategy-research` 参照 0 件。戦略履歴・outcomes・calibration は API（`fundamentals_routes.py:184,221`）と AI ツール（`ai_agent.py:758` 付近）のみ。
- **R2 (P1): 校正が実データで回らない。** `strategy_research_snapshots` は実測 **0 行**（`sqlite3 data/yowayowa.db "select count(*) from strategy_research_snapshots;"` → 0）。定期生成もない（D3）。加えて walk-forward / out-of-sample は未実装で、rank IC は重複窓を独立扱いするとコード内に明記されている（`services/strategy_calibration.py:227-231`）。**校正ループは構造としては存在するが、回す入力がない。**
- 発注結果（proposal / 約定）とスナップショットを結ぶ経路もない（F1 と O2 の帰結）。「スコアが良かった候補に実際に投資してどうなったか」を評価できない。

---

### 横断: データ鮮度の実測（新規発見・P0）

cron は「回っている」が**データが増えていない**経路が 2 つある。両方とも `last_status: ok` を返すため、**無音で失敗し続ける**タイプの死角である。

1. **EDINET インデックス（`p4b-edinet-daily-index`, 06:30 JST）は一度も動いていない。**
   - cron ジョブの記録は `last_status: "ok"`（`/root/.hermes/profiles/cto-hephaestus/cron/jobs.json`）。
   - しかし実行出力の実体は毎日 `[edinet] skip: YOWAYOWA_EDINET_API_KEY is not set (register the key to enable)`（`/root/.hermes/profiles/cto-hephaestus/cron/output/ff396d46901f/2026-09-28_06-30-36.md:9`）。
   - スクリプトは `YOWAYOWA_EDINET_API_KEY` か `EDINET_API_KEY` を要求する（`p4b_edinet_index.py:15`）。しかし配備済みの `.env` にあるのは **`EDINET_API_KEY`** のみ（`grep -o '^[A-Z_]*EDINET[A-Z_]*=' /root/.hermes/.env` → `EDINET_API_KEY=`、値 33 文字で存在）、しかも cto-hephaestus プロファイルの `.env` には EDINET の記載が 0 件。**ジョブの実行環境にキーが注入されていない。**
   - 結果: `edinet_index_days` 0 行、`edinet_filings` 0 行。`/edinet` 画面の「決算・開示書類」自動照合は**空を返し続ける**（`instrument_context.js:77` が叩く `/v1/filings/edinet/index/history`）。
   - fail-closed 設計自体は正しいが、「キー未設定で exit 0（成功扱い）」のため**監視に引っかからない**。

2. **EDINET daily（`yowayowa-edinet-daily`, 06:30 JST）は構造的に何も取得できない。**
   - 実行出力は 2026-09-25 / 09-26 / 09-27 / 09-28 の **4 日連続**で `edinet: +0 (total today=0)`（`/root/.hermes/cron/output/bc47b8dfde18/*.md` の最終行）。
   - スクリプトは `TODAY=datetime.date.today()` で**当日 JST** を取得対象日にし、その日の EDINET documents API を叩く（`/root/.hermes/scripts/yowayowa_edinet_daily.sh:11-13`）。しかし 06:30 JST は EDINET の当日分がまだ公開されていない時刻であり、**毎回 0 件**になる。
   - 実測（鍵は出さずに実行、`api.edinet-fsa.go.jp` へ直接照会）: 2026-09-28 → count 375、2026-09-25 → 399、2026-09-24 → 434（2026-09-27 は 0 = 提出なし日）。つまり **09-25 と 09-28 の提出一覧は今まさに取得可能だが、取り込まれていない**。
   - `data/edinet-daily.jsonl` の実体は 434 行で、`submitDateTime` は 2026-09-18 / 2026-09-24 の 2 日分のみ。しかも書込時刻（`retrieved_at`）は **2026-09-24T11:49Z = 09-24 20:49 JST** であり、**06:30 の cron ではなく後刻の手動実行で書かれた**ことが分かる。
   - 帰結: 06:30 JST 実行という**スケジュール自体が欠陥**。当日ではなく前日（または実行時刻を公開後へ移動）を対象にすべき。EDINET daily の JSONL は機械スクリーニングの入力源 A（`screening_pipeline.py:66` の `_DEFAULT_EDINET_PATH`）なので、**screening の EDINET 候補も同時に飢えている**。

**影響範囲**: `/edinet` 画面の全機能（issuers / history / documents / financials / facts）、instrument 画面の「決算・開示書類」欄、機械スクリーニングの EDINET ソース、`research_ask` の EDINET 引用。**EDINET に関わるワークフローは現状すべて空データで動いている。**

**分類**: 原因は環境変数の注入漏れ（1）と、当日 06:30 に当日分を要求するスケジュール設計（2）。いずれも**コード修正ではなく運用/設定の欠落**で、NEED-HUMAN ではなく自動化判断で直せる範囲。ただしキー注入は設定変更を伴うため、本監査では診断のみ記録し修正は別カードに委ねる。

---

## 横断的な所見

- **B1 (P1) morning brief は作られているが届いていない。** DB には直近 5 日分の brief がある（`research_briefs` 5 行、最新 run_date 2026-09-28）。しかし配信は `--send` 指定時のみ（`research_cli.py:62,108`）で、日次スクリプト `/root/.hermes/scripts/yowayowa_brief_daily.sh` は `--send` を付けず、cron ジョブも `"deliver": "local"`。**「Telegram 配信つき朝ブリーフ」は実装済みだが運用では配信されていない。**
- **B2 (P2) 通知が存在しない。** 価格アラートは `/alerts` 画面で手動評価するのみ（`web/static/alerts.js:233` の `#evaluate-alerts` ボタン）。イベント購読の inbox も同様に手動取得（`:155` の `/v1/event-inbox`）。`services/alerts.py` に notify / telegram 参照は 0 件。`price_alerts` 実測 0 件、`event_inbox` 0 件。**「気づく」ためには画面を開き続ける必要がある。**
- **B3 (P2) データ供給の偏り（roadmap の DONE 表記との差）。** roadmap は P4 の日本エッジとして「JPX margin / credit-margin weekly / machine screening / crypto」を DONE-equivalent（コード）に数える（`PRIVATE_OPERATOR_ROADMAP.md:42`）。実際にデータが入っているのは macro（24,361 行）・EDINET daily（434 行）・US株 OHLCV（3 銘柄）・IR（`data/private-acquisition/ir-*`、nichiban/nintendo の 2 ソース登録）までで、**`credit_margin_weekly` 0 行、`jpx_margin_balances` 0 行、`screening_candidates` 0 行、`strategy_research_snapshots` 0 行、`edinet_index_days` 0 行**。取得実装（`credit_margin_cli.py:35`）と API 表面（`api/credit_routes.py:52`、`api/jpx_routes.py:46`）はあるが、**データが投入されていないため画面/API は空を返す**。roadmap の DONE は「コード実装」の意味であり「運用データあり」を意味しない旨を roadmap 側で明示すべき。
- 補足: IR 監視は `p4b-ir-daily-monitor` が日次で `sources.json` の 2 ソースを叩いており（`/root/.hermes/profiles/cto-hephaestus/scripts/p4b_ir_monitor.py:62-97`、`data/private-acquisition/ir-sources/sources.json`）、**IR は実際に自動運用されている**。ただし `ir-timeline/` には `2222-T` `3333-T` `4444-T` `5555-T` というテスト由来のシンボルディレクトリが実データ（`4212-T` ニチバン）と同居しており、テスト実行が本番データディレクトリへ書き込んだ痕跡がある（`data/private-acquisition/ir-timeline/`）。データ分離の確認を推奨。
- **B4 (P2) 未参照 CSS が 4 件ある。** `base.html:10-15` が link するのは styles / expansion / ux / product / pages / interface の 6 本のみ。`web/static/` にある **`mobile-nav.css` / `market.css` / `portfolio.css` / `settings_data_sources.css` の 4 本はリポジトリ内のどこからも参照されていない**（`grep -rn 'market.css\|portfolio.css\|settings_data_sources.css\|mobile-nav.css' src/yowayowa/ tests/` → 0件）。モバイルでは `interface.css` の `@media (max-width:900px)` が `.nav` を 4 列グリッドに切り替える（`:793` 以降、`:818` の `grid-template-columns: repeat(4, ...)`）ため実害は確認できないが、`mobile-nav.css` の `max-width:800px` ルール（`.nav { overflow-x: auto }`）は**適用されていない死んだ CSS**である。DESIGN.md の「モバイルで横スクロール禁止」に対する実装の所在が `interface.css` 側であることも、これら冗長ファイルと併せて整理が必要。
- **通貨・期間の整合（Y04/Y05 の確認）**: 2026-09-28 の Y01-Y05 監査修正で通貨・filing 整合は実装済み（`services/valuation.py:24-40` の `_currency_compatible`、`screening._period_key` の period+currency+accession 一致）。**「未実装」と誤記しない。** 残る注意点は、`MetricPoint.currency` が None のとき計算を許す後方互換経路が意図的に残ること（`valuation.py:35-39`、docstring に「currency-unverified」と明記）。fail-closed 化は別カードの判断事項であり、本監査では欠陥に数えない。

---

## 原因と影響範囲（一覧）

各欠落の「なぜ起きているか」と「どこまで壊れるか」。受入条件の明示用に ID 順で列挙する（詳細な本文は各工程の節を参照）。

| # | 原因（実装/運用のどこが原因か） | 影響範囲（利用者に何が起きるか） |
|---|---|---|
| D1 | 機械スクリーニングに UI ページ・JS が作られていない（API と AI ツールのみ先行実装） | ホームから「今日の候補」を見られない。EDINET+信用残+スクリーナー統合の利点が画面に出ない |
| D2 | `crypto-fetch` の定期実行ジョブが存在せず、`data/crypto-ohlcv` が未生成 | 暗号資産の OHLCV 参照経路（AI・research_ask）が常に空を返す |
| D3 | screening / 信用残 / JPX / crypto の日次ジョブが未登録 | 日々のデータが増えない。画面は古いまま（EDINET は G1/G2 で完全停止） |
| I1 | instrument 画面に deep research へのリンクが実装されていない（discover 側のみ） | 銘柄を深掘りしたくなった時点で手で銘柄を打ち直す必要（状態が切れる） |
| I2 | `research_ask` を呼ぶ画面が未実装（CLI/API/Telegram のみ） | cited Q&A の出所分離つき応答を PC 画面で読めない |
| F1 | 仮説・反証条件の永続化テーブル/モデルが存在しない | 後から「あの判断は正しかったか」を検証できない（R2 と連鎖） |
| C1 | compare 画面から AI への導線が未実装 | 比較後に「なぜ差があるか」を問うのに手動で文脈を打ち直す |
| S1 | portfolio-aware sizing が未実装（数量は CLI 手入力） | 「何株買うか」の判断が完全に人手。ワークフローの中核工程が空 |
| S2 | broker-read → portfolio の取り込み経路が未実装（手入力/CSV のみ） | リスク分析・サイズ判断の前提となる保有が実態とずれる |
| P1′ | AI の 25 ツールに broker 系が 1 つもない | AI が発注提案まで到達できない（提案形成が完全に手作業） |
| P2′ | `source_research_link` が自由文字列で、research との自動連携がない | 提案と根拠調査が乖離し、後から追跡できない |
| E1 | broker の画面が未実装（HTTP は提案/評価/閲覧まで、submit は意図的不存在） | 執行工程はターミナルへ離脱。「Yowayowa から離れず完結」に抵触 |
| O1 | `order_inquiry` が `executions` を merge しない | 約定価格・数量・手数料が観測に出ない（`filled_quantity=0` 固定） |
| O2 | 約定 → portfolio 反映の経路が未実装 | 約定しても保有・リスクに反映されない（P2 exit criteria 未達） |
| O3 | 発注/約定の閲覧画面が未実装 | 執行の結果を画面で確認できない |
| R1 | 校正表面の画面が未実装（API/AI ツールのみ） | 戦略の当たり外れを PC 画面で振り返れない |
| R2 | スナップショットの定期生成がなく、walk-forward 未実装 | 校正ループに入力がなく、改善が回らない |
| B1 | brief 配信に `--send` が付かず、cron も `deliver: local` | 毎朝の brief を人が見に行かない限り届かない（通知なし） |
| B2 | push 通知機構が存在しない（手動評価のみ） | アラート発火に画面を開くまで気づけない |
| B3 | データ投入運用が未整備（コードはある） | roadmap は DONE だが画面は空。期待と実態がずれる |
| B4 | CSS 4 本がどこからも link されていない | 見た目の一部ルールが効かない（実害は限定的） |
| G1 | `YOWAYOWA_EDINET_API_KEY` が cron 実行環境に注入されていない（fail-closed で exit 0） | EDINET index が 0 日。`/edinet` 全機能と screening の EDINET 源が空 |
| G2 | 06:30 JST に「当日」を要求するスケジュール設計（当日分は未公開） | EDINET daily が 4 日連続 0 件。**EDINET 依存の全ワークフローが空データで稼働** |

**最重要の連鎖**: G1/G2（供給停止）→ D1/D3（表面欠落）→ S1/S2（判断欠落）→ E1/O1/O2（執行・反映欠落）→ R2（改善不能）。**供給が止まっているため、後段の欠落が実際にはまだ表面化していない**点に注意（データが入れば D1/S1 等が同時に露見する）。

---

## 推奨される後続カード（本カードの範囲外）

優先度順。いずれも **本カードでは修正しない**（診断のみ）。

1. **EDINET データ経路の復旧**（設定 + TZ の 2 点、各 0.5 日）— G1/G2。**EDINET 依存の全画面が空のため最優先。**
2. **execute / observe の UI 表面**: 提案一覧・承認状態・実行結果・約定の閲覧（安全ゲートは傍受せず、既存 CLI/transport を背面で使う）— E1/O3
3. **size の実装**: portfolio-aware sizing（リスクと保有から数量候補を算出）— S1
4. **約定 → portfolio 反映**: `executions` を inquiry に取り込み、fill reconciliation を接続 — O1/O2
5. **AI 発注提案ツール**: 25-tool カタログに broker 提案を追加（提案のみ、送信は不可）— P1′
6. **機械スクリーニングの表面化と定期実行**: `/v1/screening` の UI 配線 + 日次ジョブ — D1/D3
7. **crypto の実データ投入と表面化**: `crypto-fetch` の定期実行 + OHLCV 表示 — D2
8. **research_ask / brief の画面到達**: `/ai` または新規表面への統合、brief の配信を運用に載せる — I2/B1
9. **instrument → research 導線**（0.5日）— I1
10. **反証・仮説レコード**（P3 残項目）— F1
11. **校正用スナップショットの定期生成**と walk-forward — R2

---

## 変更の有無

本監査は**読み取りのみ**。コード・設定・DB・スケジューラを一切変更していない。

成果物は本ドキュメント 1 ファイルのみで、ローカルコミット `b5e8d77`（parent `5a87ce7`）として記録した。変更ファイルは `docs/WORKFLOW_DEADEND_AUDIT.md` の追加 1 件のみ（`git diff-tree --no-commit-id --name-only -r b5e8d77` で確認）。**push はしていない**（push は CTO の担当）。

楽天実セッション / ChatGPT device auth を要する検証は実施していない（NEED-HUMAN）。
