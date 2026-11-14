# P5: 実行インパクト応答への板 provenance 書き込み（t_5686873e）

- 実施: 2026-10-24、worker-flash（VPS ubuntu、ネットワークなし・一時 base_dir のみ・製品データ書き込み 0）。
  2026-10-25 に第1回レビュー指摘（全量検証証跡）へ対応する TSUKUMO 全量追試を追記（本書 §7）。
- 目的: `ExecutionImpactResult`（実行インパクト応答）がどの板（snapshots）由来かを応答自身が示すようにする。
  合成板（provider=synthetic-seed-orderbook / license_class=synthetic）や degraded 板の数値が、
  実フィード由来と誤認される経路を塞ぐ（AGENTS invariants 1/3/9、PRIVATE_OPERATOR_ROADMAP P1C/P5）。
- CTO 確定設計の通り additive 変更のみ。既存 12 フィールドの名前・型・既定値・値・順序は不変。
  計算式・丸め・分岐・警告文は一切変えていない。

## 1. 変更ファイル（5 件のみ・commit/push なし）

| file | 着手前（.pre 複製, SHA256） | 着手後（SHA256） |
| --- | --- | --- |
| src/yowayowa/orderbook_models.py | f836a6e510098ec3f068e5dce9fa6864442e7e7c6337c5a1bcb63e2e88458e75 | e778b4fc4bb565b8bfde31b3d920ac6f57af906239a2ace117694ae7befaa1a1 |
| src/yowayowa/services/orderbook_service.py | 3d3ebbc2b0f04c6c0c86ee75326ee9eb0bb3cd322b77ca42e9b8364645c521cf（先行 t_9ad92d9b 完了状態） | 5daeaf43c4aa05506836ddb2e0d61fdd37ceaac6cfacb40f4aff9f033f660292 |
| docs/openapi_baseline.json（生成物・`make openapi-baseline`） | 5a2f0d02473e24d36de6c945451d3d2851634d2267defb7b865d2fee05b44f5b（.pre 複製） | 9a5180b9b2dcf66cdae7e4c99fcce64fdeefa62c1fcb525765f91ed7c9b54551 |
| tests/test_orderbook_impact_provenance.py | （新規） | a309fed3cc745486cfe62d99f7c58c50fe54cff247ded6e40a84695d57dc067d |
| docs/P5_ORDERBOOK_IMPACT_PROVENANCE_20261024.md（本書） | （新規） | （本書） |

- src/yowayowa/api/orderbook_routes.py は不変: fd52392186b42689e5546fed4004cf4fe8a2948906afbb9cd3e54497b0c3e362
  （response_model が provenance を自動追従。カード指示通り編集せず）。先行 t_9ad92d9b 分を含む他カードの
  未コミット差分は全保全（stash/reset/commit なし）。
- ソース差分は .pre 複製との unified diff で 2 箇所のみ:
  - models: `ExecutionImpactResult` 末尾にコメント 3 行＋`provenance: Provenance` 1 行（必須・既定値なし）。
  - service: `simulate_execution_impact` の流動性なし早期 return と通常 return の両方に
    `provenance=snapshot.provenance,` を 1 行ずつ。
- `services/ai_agent.py`（estimate_orderbook_impact tool, L2191-2209）は無編集:
  `return result.model_dump(mode="json")` がそのまま新キーを LLM へ渡す。

## 2. RED → GREEN（独立テスト tests/test_orderbook_impact_provenance.py）

- RED（実装着手前・run 7866・2026-10-24 17:39, host=ubuntu）:
  `TZ=UTC .venv/bin/pytest tests/test_orderbook_impact_provenance.py` →
  **7 failed, 1 passed**（1 passed は `test_same_input_reexecution_is_identical` のみ=旧実装でも決定論的）。
  失敗本体は `AttributeError: 'ExecutionImpactResult' object has no attribute 'provenance'` ×4、
  フィールド順/キー集合 assert 失敗 ×3（証跡: red_run.txt）。
- GREEN（本 run・2026-10-24 20:10, host=ubuntu）:
  - `TZ=UTC .venv/bin/pytest tests/test_orderbook_impact_provenance.py -v` → **8 passed** rc=0 (2.50s)
  - `TZ=UTC .venv/bin/pytest tests/test_orderbook_impact_provenance.py tests/test_orderbook.py
    tests/test_orderbook_auth.py tests/test_orderbook_symbol_path_safety.py` → **59 passed** rc=0 (14.86s)
    （既存 test_orderbook.py 12・test_orderbook_auth.py 5・symbol_path_safety 34 は無変更で回帰なし）
  - `TZ=UTC .venv/bin/pytest tests/test_openapi_baseline.py tests/test_ai_agent.py
    tests/test_ai_agent_429_backoff.py` → **32 passed** rc=0 (13.73s)

テストは 4 系統＋契約形状＋数値同一性を自動検証する:
- 契約形状: `list(ExecutionImpactResult.model_fields) == [12 個の既存フィールド順, "provenance"]`。
- 合成板 / 実 provenance 板（jpx-tse-feed・retrieved_at・notes 保持）/ degraded 板（corrupt JSON →
  degraded note・`corrupt_reads.jsonl` 記録・原物ファイル hash 不変）/ 流動性なし早期 return、
  の 4 系統で `result.provenance == snapshot.provenance`（pydantic 等価）を表明。
- 数値: テスト内にカード前実装の逐語複製 `_reference_impact_v1`（container を dict に変えただけ）を置き、
  12 フィールド全件を各ケースで deep-equal 表明（8 ケース買/売・全充填/不足/ちょうど充填/片板/早期 return）。
  逐語性は機械検査済み: .pre 複製の関数本体との unified diff = **VERBATIM: 0 diff lines**。
- API: TestClient で `/v1/orderbook/{symbol}/impact` の 200 応答キー集合が
  `12 既存キー + provenance` のみ、`provenance` が同板 GET の provenance と JSON deep-equal、
  `license_class == "synthetic"`・notes 読める、数値は逐語複製と一致。

## 3. 旧版複製（scratch .pre モジュール動的ロード）との runtime parity（本 run 実測）

scratch/p5_prov_baseline/probe_old_vs_new_runtime.py が .pre 複製 2 ファイルを
`importlib.machinery.SourceFileLoader` で動的ロードし（旧 service は旧 models を参照させるため
exec 中のみ sys.modules を差し替え）、同一の固定スナップショットを旧新両実装へ渡した:

- 4 系統＋境界 9 ケース全部で **PARITY-OK**（rc=0）: 旧結果 12 フィールド（mode="json"）と
  新結果の同名フィールドが全件一致、新 `provenance` は入力 snapshot の provenance と deep-equal、
  同一入力の再実行で dump 完全一致（決定論）。
- 実測値の例（S3-degraded buy 200・流動性不足）: fillable 80.0 / avg 102.5 / slippage 1.5 /
  slippage_bps 148.51 / total_cost 8200.0 / levels_swept 3 /
  warning "Liquidity exhausted: only 80.0/200.0 units could be filled across 3 book level(s)." /
  provenance provider=synthetic-seed-orderbook・license_class=synthetic・degraded note 維持。
  旧実装（provenance 欠如）と 12 フィールド全件一致。
- S4 流動性なし（sell・bids=[]）: fillable 0.0・fully_filled false・
  warning "No orderbook liquidity available on the requested side."・provenance は板のものを維持。

## 4. API 実応答（本 run 実測・TestClient・scratch store）

`POST /v1/orderbook/DOCPROBE/impact {"side":"buy","quantity":300.0}` → status **200**、
キー集合 = 12 既存キー＋`provenance`、`board_provenance_matches: True`:

```
"provenance": {"as_of": null, "license_class": "synthetic",
  "notes": ["Synthetic multi-level depth generated locally; not live exchange data."],
  "provider": "synthetic-seed-orderbook",
  "retrieved_at": "2026-10-24T11:14:28.717987Z",
  "source": "Yowayowa synthetic seed orderbook (not an exchange feed)", "source_url": null}
```

（数値: fillable 300.0 / avg 1501.0 / slippage 0.0 / slippage_bps 0.0 / total_cost 450300.0 /
levels_swept 1 / warning null。診断スクリプトは router の `_service` を一時 dir に差し替え、
既定へ復元。./data への書き込みなし。）

## 5. OpenAPI baseline（公式手順・本 run 実測）

- `make openapi-baseline`（= `scripts/check_openapi_baseline.py --write`）→ rc=0。
  --write 前後で SHA256 不変（9a5180b9…→9a5180b9…）= 生成は決定論的で、disk 上 baseline は既に最新。
- `make openapi-check` → **rc=0**（155 operations, "OpenAPI baseline matches the generated document"）。
- .pre baseline（5a2f0d02…）との意味論差分（リポジトリの `describe_differences` 使用）:
  operations 155 → 155、差分 **2 項目のみ**、いずれも ExecutionImpactResult への provenance 追加:
  1. `/components/schemas/ExecutionImpactResult/properties/provenance/$ref` → `#/components/schemas/Provenance`
  2. `/components/schemas/ExecutionImpactResult/required/5` → `"provenance"`
  byte diff も同 2 hunk のみ（properties の provenance 追加＋required 配列への追記）。
  operation・他スキーマ・他フィールドの差分は 0。

## 6. 静的検査（本 run 実測・host=ubuntu）

| command | rc | 結果 |
| --- | --- | --- |
| `.venv/bin/ruff check <対象 3 file>` | 0 | All checks passed! |
| `.venv/bin/ruff format --check <対象 3 file>` | 0 | 3 files already formatted |
| `.venv/bin/mypy src/yowayowa/orderbook_models.py src/yowayowa/services/orderbook_service.py` | 0 | Success: no issues found in 2 source files |
| `make typecheck`（mypy src/yowayowa 全体） | 0 | Success: no issues found in 243 source files |
| `make lint`（ruff src tests 全体） | 2 | **既存失敗**: 36 件すべて paperloop 系 WIP（tests/test_paperloop_v12.py 16・src/yowayowa/paperloop_panel.py 11・tests/test_paperloop.py 4・tests/test_jp_value_quality_backtest_units.py 3・paperloop.py 1・loop_cli.py 1）。本カード対象 3 file は 0 件（grep 実測）。他カード未コミット差分に起因し、本候補の失敗ではない |

## 7. TSUKUMO 全量検証（第1回レビュー指摘への補充・2026-10-25 実測）

第1回独立レビュー（audit-sol / run 8947）の必須補充要求「既存 TSUKUMO 環境または同等 CI で、
この候補と検算可能なコード hash に対して全量検証を実施し、host・実行 command・rc・ログ・実行対象の版を
本書へ追記。既存 WIP で make が早期停止する場合は pre-card 対照と候補の結果を分け、停止後の段階も
個別に実行して未実行を埋める」に従い実施した。

### 7.1 実行対象の版（検算可能な hash）

- 元木: VPS ubuntu `/root/current-work/yowayowa-investor`（branch agent/commercial-foundation、
  HEAD a05d248、未コミット差分を含む working tree 全体）のポイントインタイムコピー。
- カード 5 ファイルは本書 §1 の hash と一致し、全 run 後の再実測でも不変:
  models e778b4fc… / service 5daeaf43… / baseline 9a5180b9… / 専用test a309fed3… / routes fd523921…。
- 転送: `rsync -a`（.git/.venv/cache 類除外）→ **5,202 files / 285,432,704 bytes**、
  git dirty/untracked 184 ファイルの SHA256 manifest 照合で同一
  （例外 1 件: tests/test_brief_daily_wrapper_retry_contract.py は転送中に他カードが編集中で、
  VPS 側は転送後にさらに別版へ変化。TSUKUMO 側は転送時点の版で凍結・両木共通のため帰属判定に影響なし）。
- ツリー: 候補 `~/yw-verify-t_5686873e` と pre-card 対照 `~/yw-verify-t_5686873e-pre`
  （候補から本カード 4 ファイルを .pre 版に戻し hash 検証: f836a6e5…/3d3ebbc2…/5a2f0d02…/fd523921…、
  専用 test と本書を除外。他は同一）の 2 本を同一環境で同一コマンド実行（TZ=UTC）。

### 7.2 環境と実行方法

- host=TSUKUMO-KUN、既存共有 venv `/home/good-light/vps-work/current-work/yowayowa-investor/.venv`
  （Python 3.14.7 / pytest 8.4.2 / ruff 0.16.8 / mypy 1.20.2 — VPS venv と同一 version）。
  Makefile の worktree 規約通り `make V=<main>/.venv <target>` で実行
  （PYTHONPATH=<tree>/src は Makefile が export・editable install 不変）。
- make verify 全段階は `run_verify_t5686873e.sh`（TSUKUMO 各木 / scratch に同内容）で実行:
  `make V=<main>/.venv verify` を首先に、失敗で停止しても全段階（lint / typecheck / test /
  openapi / openapi-check）を個別に継続実行し rc を記録。

### 7.3 実測結果（候補 / pre-card 対照・すべて rc 実測）

| stage | 候補 | pre-card | 内容 |
| --- | --- | --- | --- |
| make verify | rc=2 | rc=2 | 最初の段階 lint で停止（集約ターゲットの正常挙動） |
| lint | rc=2 | rc=2 | **40 件 / 40 件・同一ファイル集合**（paperloop 系 36＋転送中に他カード編集中の tests/test_brief_daily_wrapper_retry_contract.py 4）。orderbook 系対象 3 file は 0 件。カード開始時 VPS 実測 36 件との +4 差分は上記他カード編集中ファイル由来。両木完全一致=本カード差分に起因しない |
| typecheck | rc=0 | rc=0 | mypy src/yowayowa 全体（候補 243 files） |
| test | rc=2 | rc=2 | 両木とも他カード WIP 2 ファイル（tests/test_cusum_watch.py・tests/test_paperloop_v12.py）の collection error（`No module named 'tests'`・tests/__init__.py 不在の WIP import 形式）で中断・1 件も実行されず。**VPS の同一木でも同一 2 ファイルが同一エラーで収集失敗することを実測**（ファイル起因・環境起因でない）。→ 7.4 の fill-in で残り全量を実行 |
| test fill-in | rc=1 | rc=1 | 上記 2 ファイルを `--ignore` した同一コマンド（下記）で残り全量実行。**帰属実測は 7.4** |
| openapi | rc=0 | rc=0 | openapi.json 生成 |
| openapi-check | rc=0 | rc=0 | 各木の baseline と一致（155 operations） |

- 契約の直接確認（生成 openapi.json 実測）: 候補 = 12 既存 properties＋`provenance`
  ＋required に `"provenance"` 追加 / pre-card = 12 のみ（provenance 無し）。
  本カード差分 1 フィールドの追加のみで他契約差分なし。

### 7.4 test fill-in（全量 pytest・帰属実測）

実行コマンド（両木同一・TZ=UTC・PYTHONPATH=<tree>/src）:

```
<main>/.venv/bin/pytest -p no:cacheprovider --cov=yowayowa --cov-report=term-missing \
  --basetemp=<tree>/.pytest_tmp_<tag> \
  --ignore=tests/test_cusum_watch.py --ignore=tests/test_paperloop_v12.py -q
```

| | 候補 | pre-card |
| --- | --- | --- |
| rc | 1 | 1 |
| passed | 3119 | 3111 |
| failed | 12 | 12 |
| skipped | 11 | 11 |
| 合計 | 3142 | 3134 |

- 差分 8 件（3142−3134）= 候補のみが持つ専用テスト tests/test_orderbook_impact_provenance.py の
  8 件であり、**全件 pass**（全量実行内での GREEN 実測）。
- failed 12 件・skipped 11 件の集合は両木で **完全一致**
  （failed: test_defensive_rotation.py 8・test_jp_value_quality_backtest_units.py 4 —
  いずれも他カード WIP／既存ゴールデン系）。本カードの差分で説明される失敗は **0 件**。
  進行ストリーム（dot/F/s）の差分は % 進行表記の桁ずれのみでパターンは同一。
- 備考（正直な環境特記）: 本環境（pytest 8.4.2 + pytest-cov + Python 3.14）では `--cov` 付き実行の
  最終件数サマリ行が出力されない（rc=0 の小規模実行でも再現する系統的挙動）。
  件数は進行ストリームの文字集計（passed="." / failed="F" / skipped="s"）と
  short test summary の FAILED 列挙（12 件抽出・両木 diff 一致）で確定した。rc・coverage ファイル
  生成・セクション構造（FAILURES→warnings→coverage→short summary）から実行完了は自明。

### 7.5 カード固有テストの第 2 環境実測（TSUKUMO 候補木）

対象 103 テスト（dedicated 8＋test_orderbook 12＋auth 5＋symbol_path_safety 34＋
openapi_baseline＋ai_agent＋429_backoff）を 2 回実行 → **rc=0・102 passed＋1 skipped**
（skip 1 件は symbol_path_safety 内の console script install 有無を見る環境差分テスト
"yowayowa console script is not installed in this venv"・Makefile の no-editable-install 規約と整合）。

### 7.6 結論

- 全段階で候補と pre-card が同一挙動。検出された全失敗（lint 40・collection error 2 ファイル・
  pytest failed 12）は **pre-card 対照でも同一に発生する既存 WIP／既存環境起因**であり、
  本カード差分（ExecutionImpactResult への provenance 追加のみ）に起因する失敗は 0 件。
- AGENTS.md:44-48 の全量検証要求について: VPS では lint が既存 WIP で停止するため
  `make verify` 一括は停止するが、レビュー指摘の方式通り「verify 集約実測＋全段階個別実行＋
  pre-card 対照」で全段階を TSUKUMO 既存環境・hash 検証済み候補に対して実行し充足した。

## 8. 未検証範囲（正直な限界）

- **実フィード板・本番応答は未検証**（検証はネットワークなし・一時 store / scratch store のみ。Vercel 本番の
  `/v1/orderbook/*/impact` 応答への反映は未確認）。
- VPS 側での `make verify` 一括実行は、lint が既存 WIP で停止するため未実施（代わりに §7 の通り
  TSUKUMO で全段階個別実行＋pre-card 対照を完了）。VPS 全体 lint の既存 36 件（カード時点実測）は
  §6 の通り。
- AI tool は unit・dispatch レベル（test_ai_agent 18 件）のみ。実 LLM 呼び出し経路での
  provenance キーの実際の消費は未検証。
- CLI（`orderbook_cli.py impact`）は既存の rich 表レンダリングで、provenance 行の表示は
  **従来通り無し**（モデル内部では provenance を保持。表示追加は本カード範囲外・無変更）。
- `corrupt_reads.jsonl` 帳簿・degraded 経路の表示形式は既存のまま（本件は応答への provenance
  書き込みのみで、帳簿様式は変更していない）。
- TSUKUMO fill-in の skipped 11 件の内訳（ネットワーク系等の通常 skip と想定）は個別検証していない
  （両木同一集合であることまでは実測）。

## 9. 証跡（scratch・24h 自動掃除対象のため要旨は本書に転記済み）

VPS worker-flash scratch `/root/.hermes/profiles/worker-flash/cache/scratch/p5_prov_baseline/` 配下
（§2〜§6 の証跡）:
SHA256SUMS.pre・*.pre（着手前複製 4 件）・red_run.txt（RED 7F/1P）・green_run_final_v.txt（59 passed）・
pytest_dedicated_final.txt / pytest_combined_final.txt / openapi_final.txt / make_lint_typecheck.txt /
lint_type_final.txt（本 run 再実測）・probe_old_vs_new_runtime.py + probe_old_vs_new_out.txt（PARITY-OK）・
probe_api_response.py + probe_api_response_out.txt（API 200 実応答）・check_verbatim.py + verbatim_diff.txt
（VERBATIM: 0 diff lines）・check_semantic_diff.py + semantic_diff.txt（差分 2 項目）・baseline_byte_diff.txt・
wip_paths.txt / wip_manifest_vps.txt（184 ファイル転送照合元）・rsync_t5686873e.log（転送統計）。

同 scratch `tsukumo_logs/` 配下（§7 の証跡バックアップ 22 ファイル）:
cand_/pre_stages_index.txt・cand_/pre_env.txt・cand_/pre_stage_*.log（lint/typecheck/test/fill-in/
openapi/openapi-check）・cand_/pre_failures.txt（12 件・diff 一致）・cand_/pre_progress.txt・
targeted_103_cand(.log/_rs.log)。

TSUKUMO 本体（good-light@TSUKUMO-KUN・残存）:
`~/yw-verify-t_5686873e/`（候補木＋verify_logs/）・`~/yw-verify-t_5686873e-pre/`（対照木＋verify_logs/）・
`~/t5686873e_pre_src/`（.pre 複製 4 件）・`~/t5686873e_pre_removed/`（対照木から除外した専用 test・本書）。
