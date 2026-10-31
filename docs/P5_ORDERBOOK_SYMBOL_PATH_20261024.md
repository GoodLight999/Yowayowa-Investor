# P5 注文板ストア symbol 境界の検証（t_9ad92d9b）— 実測記録

- 実施日: 2026-10-24 JST
- 担当: worker-flash / 実装・小試験ホスト: VPS(ubuntu) / 全量verify: TSUKUMO-KUN
- 出典: AGENTS invariants 1/2/3/9、PRIVATE_OPERATOR_ROADMAP P1C/P5、
  `docs/SECURITY_ORDERBOOK_AUTH_20261023.md` §7.3（symbol検証とパストラバーサル未修正の解消）、
  `services/orderbook_service.py` `_symbol_path`、既存の同型先行実装
  `acquisition/snapshots.py` `_safe_component`。
- スコープ: 注文板ストアの **symbol 境界だけ**。feed・値変換・保存設計は別裁判（本doc §8）。

## 1. 修正前の実測欠陥（CTO読取 2026-10-24 13:33-13:36 JST + 本カードREDで再現）

| 入力 | 修正前の挙動 |
|---|---|
| `".."` | `base_dir/../latest.json` に解決され、store 外（base_dir の親）へ実書き込み |
| `""` / `"."` | 同一 `base_dir/latest.json` を指し、要求symbolと保存symbolの不一致を検出しない |
| `7203.T` + NUL(0x00) | `ValueError: embedded null byte` が service から外へ貫通（API では未処理 500） |
| 空白のみ / 前後空白 | `base_dir/"  "/latest.json` 等を作成（API route は strip するが service/CLI/AI は strip しない） |
| `"BTC/USD"` vs `"BTC:USD"` | 同一 `BTC_USD/latest.json` を黙って共有（**本カードでは設計として維持**） |

着手時 product source hash（本カード開始時・未変更）:
`services/orderbook_service.py` = `741f0cfd4a9aedde0127e7f82ac0a7a5dcc448bddb289301b22dd65eb8be6a42`、
`acquisition/snapshots.py` = `12a3c69965d4816f38063a79be2d2a0cb20593937a981154201471eace12d5bb`。

## 2. 変更内容（2ソース + 1テスト + 本doc）

| ファイル | 変更 |
|---|---|
| `src/yowayowa/services/orderbook_service.py` | 検証境界1箇所（`_validated_orderbook_symbol` + `_symbol_path` 内 containments チェック）、専用例外 `InvalidOrderbookSymbolError`、`get_snapshot` / `record_snapshot` への strip 適用、要求symbol≠保存symbolの明示note |
| `src/yowayowa/acquisition/snapshots.py` | dot-dot 判定の `reject_dot_dot()` への昇格のみ（`_safe_component` から呼ぶ。挙動・文言・戻り値は byte 同一。§6 の parity テストで担保） |
| `tests/test_orderbook_symbol_path_safety.py` | 新規33件（拒否19入力×5入口、NUL、strip一致、旧版同等6symbol、共有規則parity、不一致明示、API/CLI/AI各面） |
| `docs/P5_ORDERBOOK_SYMBOL_PATH_20261024.md` | 本ドキュメント |

修正後 hash:
`services/orderbook_service.py` = `3d3ebbc2b0f04c6c0c86ee75326ee9eb0bb3cd322b77ca42e9b8364645c521cf`、
`acquisition/snapshots.py` = `15eb3703b87be810556d5d7668ea325fd0b3bc9ea784e638e6a6ad63b72e58c4`、
`tests/test_orderbook_symbol_path_safety.py` = `9f396b351ab13ebf69285d8afd04cbe50a5e0eb703a98a512eba51d96825d0ce`。

API route / CLI / AI agent / models は **1行も触っていない**（orderbook_routes.py・orderbook_models.py・
orderbook_cli.py・ai_agent.py・app.py は全て未変更。git diff で他カードWIPと無混入を確認）。

## 3. 設計の要点（CTO確定設計の実装）

- **検証は1箇所**: モジュール関数 `_validated_orderbook_symbol()` が唯一の規則実装。
  全入口（`get_snapshot` / `record_snapshot`、およびそれを経由する `get_depth_ladder` /
  `get_metrics` / `estimate_impact`）が同一規則を通る。path 生成は `_symbol_path` が
  同関数を通した値でのみ行い、加えて `resolve()` 後の containments（store 配下以外は拒否）
  を二重防壁として持つ。
- **規則**: `strip()` 後に空なら拒否 → 絶対path 形（先頭 `/`・`\`、`X:` ドライブ形）を拒否 →
  dot-dot（`acquisition/snapshots.py` の共有規則 `".." in value` を再利用・二重実装なし）を拒否 →
  既存正規化（`/`→`_`、`:`→`_`、upper）後に `.`/`..` 完全一致、制御文字（Unicode Cc。NUL 含む）、
  path 区切り（`\` と `os.sep`/`os.altsep`）、区切りだけの名前、を拒否 → 解決後 path が
  base_dir 配下でないものを拒否。
- **既存互換**: 正常symbol（`7203.T` / `7203.t` / `BTC/USD` / `BTC:USD` / 日本語 / 小文字）は
  保存先path・保存bytes・戻り値全フィールドが旧実装と deep-equal（§5.2 実測）。
  旧実装はテスト内に verbatim 複製したオラクル（`_LegacyOrderbookStore`）で比較した。
- **専用例外**: `InvalidOrderbookSymbolError(HTTPException, ClickException, ValueError)`。
  1つの例外で3面がそれぞれの native 機構で処理される: FastAPI → 400 `{"detail": ...}`、
  click/typer CLI → 例外型はそのまま typer の既定描画へ（§7 の限界参照）、
  AI tool dispatch（`_execute_tool` の `except Exception`）→ `{"error": "..."}`。
  メッセージは固定文字列のみで、入力の生値・生bytes・path は構造的に入らない
  （`str` も `detail` も `message` も同一固定文言。§5 の各面で実測）。
- **不一致の明示**: `get_snapshot` が保存板の `symbol` と要求symbol（strip後）を比較し、
  相違なら provenance notes へ `symbol mismatch: ...` を追加して返す（黙って別symbolの板を
  返さない）。`BTC:USD` → `BTC_USD` の正規化による path 共有自体は既存設計として維持。
  同一symbolの読み出しは notes を一切変えない（byte 同等）。
- **strip の適用範囲**: service 境界で1回 strip し、保存される symbol も strip 済みの値に統一。
  API route の既存 strip と一致する。正常symbolでは strip は恒等変換なので無影響。

## 4. 対象試験（VPS・狭小・ネットワーク無し）

実行: `PYTHONPATH=$PWD/src .venv/bin/pytest tests/test_orderbook_symbol_path_safety.py
tests/test_orderbook.py tests/test_orderbook_auth.py
tests/test_private_acquisition_snapshots.py tests/test_private_acquisition_service.py`
（host: ubuntu/VPS）

| 段階 | 結果 |
|---|---|
| RED（実装前・新test先行版32項目） | **26 failed, 6 passed**（新規テストファイルの先行イテレーション32項目のみで、最終版の同一テストではない。6 passed は旧版同等系＝無変更の証拠側。`..` が実書込み、NUL が貫通、API 500、AI tool が例外のまま等、挙動ベースの失敗） |
| GREEN（実装後） | **84 passed, rc=0**（収集84＝新規33＋orderbook 12＋auth 5＋snapshots 16＋service 18。既存 tests/test_orderbook.py 12件・auth 5件は無変更で緑。最終再実測: `collected 84 items` / `84 passed, 1 warning in 10.79s`） |

## 5. 前後実測

### 5.1 `".."` の脱出（scratch の一時 store・実出力）

```
OLD  get_snapshot('..')            -> accepted, symbol: ..
OLD  wrote outside the store?      -> True .../latest.json      ← store 外へ実書き込み
OLD  old_store tree                -> (empty)                   ← 中身は外
NEW  get_snapshot('..')            -> rejected: InvalidOrderbookSymbolError
NEW  message                       -> Invalid orderbook symbol: unsafe path component.
NEW  new_store tree                -> (empty)
NEW  isinstance ValueError         -> True
OLD  '7203.T' path                 -> 7203.T/latest.json
NEW  '7203.T' path                 -> 7203.T/latest.json        ← 正常symbolは不変
OLD  '7203.T' best_bid             -> 2849.0
NEW  '7203.T' best_bid             -> 2849.0
```

### 5.2 許可系の旧版同等（deep-equal・テスト実測）

`7203.T` / `7203.t` / `BTC/USD` / `BTC:USD` / `トヨタ` / `7203` の6symbolについて、
旧版複製オラクルと保存先相対path・保存bytes（sha256一致）・戻り値全フィールドが一致。
`BTC/USD` と `BTC:USD` は同一 `BTC_USD/latest.json` を共有（維持）。
シード生成の時刻はモジュールの `datetime` を凍結して byte 比較可能にした。

### 5.3 API（TestClient・実測はテスト内）

- `GET /v1/orderbook/%2E%2E` および `%2E` → **400**、`{"detail": "Invalid orderbook symbol: unsafe path component."}`、
  生path・`..` は本文に出ない（500 ではない）。
- `POST /v1/orderbook/%2E%2E/impact` → 400（同文言）。
- `GET /v1/orderbook/%20` → 400 `Symbol cannot be empty.`（route 既存文言のまま・無変更）。
- `GET /v1/orderbook/7203.T` → 200、`symbol == "7203.T"`（従来どおり）。

### 5.4 CLI（正式エントリ実プロセス + CliRunner）

- `.venv/bin/yowayowa orderbook show ..` → **rc=1**、stderr に固定文言
  `Invalid orderbook symbol: unsafe path component.`、旧の `embedded null byte` 等の
  OS 由来文言は出ない。store への書込み 0件。
- CliRunner 経由でも `orderbook show|metrics|impact` の全コマンドで rc=1 + 固定文言 +
  `result.exception` が専用例外。生入力・制御bytesは出力に現れない。
- **限界**: typer 0.27.2 + click 8.5.0 の実測では、コールバック内で送出された例外は
  **plain な `click.ClickException` であっても** typer の rich トレース枠で描画される
  （probe 実測）。1行 `Error: ...` 描画は orderbook_cli.py 側の捕捉が必要で、
  本カードのファイル whitelist（service+test+doc）では変更できない → §7/§9。

### 5.5 AI tool（`_execute_tool` 実測はテスト内）

- `get_orderbook {"symbol": ".."}` → `{"error": "InvalidOrderbookSymbolError: Invalid orderbook symbol: unsafe path component."}`
- `estimate_orderbook_impact {"symbol": "A\x1fB", ...}` → `{"error": ... "control character."}`
- 生入力・制御bytes・path は error 文言に現れない。

## 6. 共有ヘルパ昇格（snapshots.py 編集について）

`_safe_component` の dot-dot 判定1行を新設 public 関数 `reject_dot_dot()` へ移し、
orderbook 側はこれを import して再利用（二重実装なし）。snapshots 側の既存挙動・文言・
戻り値は **byte 同一**（同じ例外型・同じ `f"unsafe path component: {value!r}"`）。
先行カードの未コミット差分は全保全（diff stat: snapshots.py 230行差分 → 246行差分、
差分の増分は本ヘルパ13行+呼び出し1行のみ。`tests/test_private_acquisition_snapshots.py`
等の既存 suite は無変更で緑）。parity テスト
（`test_dot_dot_rule_is_shared_with_snapshots_safe_component`）が両側の受可否と
snapshots の文言を固定する。

## 7. CLI 描画の実測（typer 0.27.2 + click 8.5.0）

- `str(専用例外)` は固定文言そのもの（starlette 1.6 の `HTTPException.__str__` が
  `"400: "` を付けるため `__str__` を上書きし、全面で同一文言に統一）。
- 実測 probe（scratch・`p5sym_probe4.py`）: **plain `click.ClickException("plain boom")` でさえ**
  コールバック内送出では rich 枠（約30行）になる。typer は click の standalone_mode を
  使わず、`Typer.__call__` の except hook が全例外を rich 描画するため。
  → 1行化は CLI ファイル（orderbook_cli.py の3コマンドに try/except、stock_cli.py:78,121 の
  既存パターン）でのみ可能。本カードの whitelist 外のため未実施。**別カード提案**（§9）。
- 本カードが CLI 面で達成したこと: rc=1 の維持、例外文言の固定化・無漏えい化
  （旧: `ValueError: embedded null byte` 等のOS文言、新: 固定文言のみ）。
- 付随実測: **NUL は argv 経由では OS 自体が受渡を拒否する**（`fork_exec` が
  ValueError）。よって CLI 面に NUL は到達し得ず、NUL の拒否は service/API/AI 面で担保。
- ※1行化は後続カード **t_c3565178 で解決済み**（§12。orderbook_cli.py 側の捕捉で
  実エントリ stderr は 1行になった）。

## 8. 非スコープ（本カードで変えない裁判事項）

- `"BTC/USD"` と `"BTC:USD"` が同一 `BTC_USD/` を共有する保存設計（別裁判・維持）。
  本カードは共有時の**沈黙だけ**を排除した（mismatch note で明示）。
- 改竄検知・完全性保証ではない（path 共有の別symbol書込みは仕様通り可能）。
- R1 値変換 / R7 配信 / R8 他差分 / R9 未裁定画面 / legacy broker frozen。価格通貨と決済通貨は別。
- 将来の本番費用行 / Book C 初約定 / 週数を今の成果にしない。

## 9. 未達・限界（正直な記録）と後続提案

- **CLI の 1行エラー化は未達**（§7 の実測理由）。後続カード提案:
  「orderbook_cli.py の3コマンドで `InvalidOrderbookSymbolError` を捕捉し 1行stderr + exit 1
  （stock_cli.py の既存パターン準拠）」。whitelist の拡張を要する。
  → **解決済み（t_c3565178・§12）**。
- click は pyproject.toml に直接宣言済み（click>=8.5,<9）。typer 0.27.2 は click 非依存で、click 8.5.0 を引いていたのは uvicorn 0.53.0 — 実測根拠は docs/P5_CLICK_DEPENDENCY_DECLARATION_20261024.md §1
- typer の rich 枠はソース絶対path を表示する（本例外に限らず全例外の既存描画）。
  cli_shim 系（t_8262d080）が直したのは HTTP 経路の境界で、service 直呼び CLI は対象外だった。
- API での NUL は URL として送れない（httpx が URL に制御bytesを許可しない）ため、
  API 面の NUL は service 境界単体テストで担保（実HTTP経路に NUL は届かない）。
- make verify 全量の結果は §10 に記載（実施できなければ未達と明記）。

## 10. make verify 全量（TSUKUMO-KUN・host rc記録）

方法: TSUKUMO 共有ツリー（0a05a17・未コミット12件あり）には**触らず**、VPS 作業ツリーの
完全コピー（`.git`/`.venv`/`data`/cache 除外・370MB）を
`/home/good-light/vps-work/wt-p5sym-verify/` へ rsync し、コピー先の 3 対象ファイルを
sha256 照合（`3d3ebbc2…` / `15eb3703…` / `9f396b35…` ＝ VPS 作業ツリーと一致）の上で、
TSUKUMO 既存の共有 venv（cpython 3.14.7 のメイン `.venv`）で実行。
VPS HEAD f562163 は未push のローカル commit で TSUKUMO 側に存在しないため worktree 方式は不可
（実測: `git cat-file -t f562163` → MISSING）。make は lint で止まるため段階を個別実行。

| 段階 | host / command | rc | 結果 |
|---|---|---|---|
| lint | TSUKUMO / `ruff check src tests` | 1 | **36 errors・全て paperloop 系の他カード実行中WIP**（test_paperloop_v12 16 / paperloop_panel 11 / test_paperloop 4 / test_jp_value_quality_backtest_units 3 / paperloop 1 / loop_cli 1）。VPS での同一コマンドも同一 36（本変更と無関係の既存WIP lint負債）。**本カードの3ファイルは `ruff check` ＋ `ruff format --check` とも清潔** |
| typecheck | TSUKUMO / `mypy src/yowayowa` | 0 | `Success: no issues found in 243 source files` |
| test | TSUKUMO / `pytest --cov=yowayowa -q --ignore=tests/test_cusum_watch.py --ignore=tests/test_paperloop_v12.py` | 1 | **3096件中 3072 passed / 16 failed / 8 skipped**（coverage TOTAL 86%）。ignore した2ファイルは他カード実行中WIPで collection 自体が壊れる（`from tests.test_paperloop import …` が root import を要する。VPS でも同一2件が同じ理由で collection ERROR＝既存）。16 failed の内訳: crypto 2（binance・先行カードで既知の同一2件）/ backtest_p4_regressions 2 / defensive_rotation 8 / jp_value_quality_backtest_units 4 |
| openapi | TSUKUMO / `python -m yowayowa.cli_shim export-openapi` | 0 | `Wrote openapi.json` |
| openapi-check | TSUKUMO / `python scripts/check_openapi_baseline.py` | 0 | `OpenAPI baseline matches the generated document (155 operations …)` |
| 本カード対象試験 | TSUKUMO / `pytest tests/test_orderbook_symbol_path_safety.py tests/test_orderbook.py tests/test_orderbook_auth.py -q` | 0 | **50件中 49 passed / 1 skipped**（skip は venv に console script が無いための実エントリ試験1件。先行カードと同一事情） |

**16 failed の帰属実測（本変更に起因しないことの証明）**: TSUKUMO コピー上で本カードの
2 ソースを**着手前 hash へ byte 逆適用**（`741f0cfd…` / `12a3c699…` に復元・sha256 実測一致）
し、新規テストファイルを退避した状態で失敗 4 ファイルだけを再実行 → **同一 16 件が全て同じ
ように失敗**。よって 16 件は全て既存（他カードWIP・既知の binance 2件含む）で、
本変更に起因する失敗は **0**。
復元後、コピー先の 3 ファイル hash が最終版（`3d3ebbc2…` / `15eb3703…` / `9f396b35…`）に
戻っていることも sha256 で再確認済み。TSUKUMO 共有ツリー本体は未コミット12件のまま無変更。

正直な限界: 本 run のベースは「commit ではなく VPS 作業ツリーのコピー」であり、他カードの
実行中 WIP（lint 36・collection 壊れ 2・失敗 16 の一部）を含む。これらは帰属実測で
本変更と切り分けた。push ができない本運用では commit ベースの全量 verify は存在せず、
GitHub CI は push 後に別途走る。

## 11. 証跡（scratch・24hで消える前提）

`/root/.hermes/profiles/worker-flash/cache/scratch/` 配下:
`p5sym_red.txt`（RED・先行版32項目中26 failed） / `p5sym_green1-4.txt`（GREEN過程・72+12=84全pass。
初回docの「85 passed」は `[ 85%]` 進行表示の読み違いによる誤記） / `p5sym_green5.txt`・`p5sym_green5_v.txt`
（最終再実測・`collected 84 items` / `84 passed, 1 warning in 10.79s` / rc=0） /
`p5sym_demo.py` + `p5sym_demo_out.txt`（§5.1 実出力） /
`p5sym_probe1-4.py`（例外 str・CLI runner・typer hook・plain ClickException の実測） /
`orderbook_service.py` + `snapshots.py`（着手前 hash への逆適用済みベースライン複製。
sha256 が `741f0cfd…` / `12a3c699…` に一致することを VPS で実測済み）。

TSUKUMO（`/home/good-light/vps-work/wt-p5sym-verify/`）:
`verify_run.log`（make verify 全体・lint で停止） / `stages_run.log` `stages_run2.log` /
`pytest_full.log` `pytest_full2.log`（全量・ignore 版） / `baseline_attrib.log`（帰属実測） /
`myscale.log`（本カード対象 50件） / `openapi.json`。
**このコピー-dir は検証済みの作業コピー（約370MB）で、終了後も削除していない**
（`rm -rf` が実行ガードで拒否されるため、迂回せずそのまま残し、運用側で削除可能。
中身は VPS 作業ツリーのコピー + 本ログ群のみ）。
TSUKUMO 共有ツリー本体（`current-work/yowayowa-investor`）には一切触っていない
（着手時の未コミット12件のまま）。

## 12. 後続カード t_c3565178: CLI 1行化（実施記録）

- 実施日: 2026-10-24 JST / 担当: worker-flash / 小試験: VPS(ubuntu) / 全量verify: TSUKUMO-KUN
- スコープ: `src/yowayowa/orderbook_cli.py` + `tests/test_orderbook_symbol_path_safety.py`
  （CLI面3テスト）+ 本doc。service / routes / models / pyproject は無変更。

### 12.1 変更内容

- `orderbook_cli.py`: `show` / `metrics` / `impact` の3コマンドで service 呼び出しを
  try/except し、`_reject_invalid_symbol()`（NoReturn ヘルパ・1箇所に集約）で
  `typer.echo(f"Error: {exc}", err=True)` + `typer.Exit(code=1)`。
  文言は `Error: <固定文言>`（service docstring が定義する click/typer CLI 面の契約）。
  入力echo・path・rich 枠は構造的に出ない。捕捉は専用例外のみ（正常系は無変更）。
- `tests/test_orderbook_symbol_path_safety.py`:
  - `test_cli_rejection_exits_nonzero_with_the_fixed_message` を
    `test_cli_rejection_is_one_stderr_line_with_the_fixed_message` へ更新
    （byte完全一致の1行 stderr・stdout空・rc=1・raw入力・制御bytes非出現・無書込み。
    旧 assert の「`result.exception` が専用例外型」は CliRunner 例外素通りの旧契約であり、
    型担保は service 面テスト（`test_rejections_are_explicit_silent_and_nonescaping`）が
    継続して持つ。確認対象を6組（3コマンド×2拒否クラス）へ拡大）。
  - 新規 `test_cli_rejection_renders_no_rich_panel_and_keeps_the_happy_path`
    （rich 枠・Traceback・絶対path非出現、stderr 改行数==1、正常系3コマンドは rc=0・stderr空
    ＝捕捉の狭さの実測）。
  - `test_real_cli_entry_rejection_stderr_is_leak_free` を
    `..._is_one_leak_free_line` へ強化（stderr が `Error: Invalid orderbook symbol:
    unsafe path component.\n` のちょうど1行・`/root` 非出現）。既存assert（rc=1・
    旧文言非出現・無書込み）は全て維持。

### 12.2 前後実測（実エントリ＝`.venv/bin/yowayowa`・CliRunner）

| 面 | 修正前（§5.4/§7 の再現） | 修正後（3コマンド共通） |
|---|---|---|
| 実エントリ stderr | **42行**の rich 枠（Traceback・`/root/...` 絶対path表示あり） | **ちょうど1行**: `Error: Invalid orderbook symbol: unsafe path component.` |
| 実エントリ rc / stdout | 1 / 0行 | 1 / 0行 |
| 漏えい | なし（固定文言のみ） | なし（raw入力・制御bytes・path・`/root` 全て非出現） |
| CliRunner | 例外が runner まで素通り（stderr 0行・`exception=InvalidOrderbookSymbolError`） | rc=1・`exception=SystemExit`・stderr 1行・stdout 空 |
| 正常系（`7203` 等） | rc=0 描画 | rc=0 描画・stderr 空（無変更） |

probe 記録: scratch `t_c3565178_red_probe_out.txt` / `t_c3565178_green_probe_out.txt`
（typer 0.27.2 + click 8.5.0・python 3.12.3）。

### 12.3 対象試験（VPS・host/command/rc 付き）

- `PYTHONPATH=$PWD/src TZ=UTC .venv/bin/pytest tests/test_orderbook_symbol_path_safety.py
  tests/test_orderbook.py tests/test_orderbook_auth.py
  tests/test_private_acquisition_snapshots.py tests/test_private_acquisition_service.py --tb=short`
  （host: ubuntu/VPS）→ **85 passed, 1 warning in 14.41s / rc=0**
  （親カード比 +1 = 本カードの新規1件。CLI面3テストは `-v` で個別 PASSED を実測）。
- `.venv/bin/ruff check src/yowayowa/orderbook_cli.py tests/test_orderbook_symbol_path_safety.py`
  → `All checks passed!` / rc=0
- `.venv/bin/ruff format --check`（同2ファイル）→ `2 files already formatted` / rc=0
- `.venv/bin/mypy src/yowayowa/orderbook_cli.py` → `Success: no issues found in 1 source file` / rc=0

### 12.4 独立再検証（新セッション・2026-10-25 未明 JST）

§12.1〜12.3 を実施した run は途中で quota 壁により中断されたため、**新セッションが全測定を
独立に再実行した**（コード・テストは §12.1 のまま無変更。現行 hash 照合値:
`orderbook_cli.py` = `a27d13fb…`、`test_orderbook_symbol_path_safety.py` = `53348365…`）。

(a) 実エントリ byte 実測（VPS ubuntu・python 3.12.3 / typer 0.27.2 / click 8.5.0・
`.venv/bin/yowayowa`・tmp cwd で subprocess 実行）:

| コマンド | rc | stdout | stderr | 生成ファイル |
|---|---|---|---|---|
| `orderbook show ..` | 1 | 0 byte | `Error: Invalid orderbook symbol: unsafe path component.\n` のちょうど1行 | なし |
| `orderbook metrics ..` | 1 | 0 byte | 同上（byte 一致） | なし |
| `orderbook impact .. --side buy --qty 1` | 1 | 0 byte | 同上（byte 一致） | なし |
| `orderbook metrics 7203`（対照・正常系） | 0 | 描画あり | 0 byte | `data/`（正当な bootstrap 保存のみ） |

拒否3ケースとも `/root`・`Traceback`・制御bytes 非出現（漏えい 0）を byte レベルで実測。
CliRunner 側も `exit_code=1`・`exception=SystemExit`・stdout 空・stderr 1行で、click 8.5 の
stream 分離を実測済み → テストの `result.stdout == ""` assert は空池ではない。

(b) VPS 静態・対象試験（host/command/rc 付き・再実測）:

| host / command | rc | 結果 |
|---|---|---|
| ubuntu / `.venv/bin/ruff check src/yowayowa/orderbook_cli.py tests/test_orderbook_symbol_path_safety.py` | 0 | All checks passed! |
| ubuntu / `.venv/bin/ruff format --check`（同2ファイル） | 0 | 2 files already formatted |
| ubuntu / `.venv/bin/mypy src/yowayowa/orderbook_cli.py` | 0 | Success (1 source) |
| ubuntu / `PYTHONPATH=$PWD/src TZ=UTC .venv/bin/pytest`（§12.3 と同一5ファイル） | 0 | **85 passed** / 1 warning in 11.70s（§12.3 の再確認。CLI面3テストは `-vv` で個別 PASSED。real-entry 1件は VPS には console script があるため SKIPPED でなく PASSED） |

(c) make verify 相当の全量（TSUKUMO-KUN・親 §10 と同一の検証コピー
`/home/good-light/vps-work/wt-p5sym-verify/` に本カード2ファイルのみ上書き・sha256 照合済み
→ 共有 venv cpython 3.14.7 + `PYTHONPATH=src` で段階実行）:

| 段階 | host / command | rc | 結果 |
|---|---|---|---|
| lint | TSUKUMO / `ruff check src tests` | 1 | **Found 36 errors**＝親 §10 と同一の既存WIP負債。本カード2ファイルは単独 check/format --check とも 0 |
| typecheck | TSUKUMO / `mypy src/yowayowa` | 0 | `Success: no issues found in 243 source files` |
| test | TSUKUMO / `pytest --cov=yowayowa -q --ignore=tests/test_cusum_watch.py --ignore=tests/test_paperloop_v12.py` | 1 | 収集 **3077件**（`--collect-only -q` の合計 3077 と一致＝親 §10 の 3096 + 本カード新規1）。**3052 passed / 16 failed / 8 skipped / 1 xfailed**・coverage TOTAL 86%。**16 failed は親 §10 の既存16件と名寄せで完全一致**（crypto 2 / backtest_p4_regressions 2 / defensive_rotation 8 / jp_value_quality_backtest_units 4）。orderbook 関係の失敗・本変更起因の失敗は **0**。既知 flake（test_api_run_and_candidates・JST0時過ぎ）も不発 |
| openapi | TSUKUMO / `python -m yowayowa.cli_shim export-openapi` | 0 | `Wrote openapi.json` |
| openapi-check | TSUKUMO / `python scripts/check_openapi_baseline.py` | 0 | baseline matches (155 operations) |

※ 補記: 親 §10 の「3072 passed」の行は、同一コピーでの進行文字内訳では xfailed 1 を
含む読み（3052+16+8+1x = 3077 = 3096+新規1）と整合する。失敗集合の同一性は FAILED 行
16件の名寄せで確認済みであり、親カードの結論（16件は全て既存）は本再検証でも成立する。

証跡（scratch・24hで消える前提）: `t_c3565178_reverify_probe.py` + `t_c3565178_reverify_probe_out.txt`
（実エントリ byte 実測）/ `t_c3565178_pytest_reverify.txt`（5ファイル85件・rc=0）/
`t_c3565178_cli_tests_vv.txt`（CLI 3テスト個別 PASSED）。
TSUKUMO `wt-p5sym-verify/` 追加ログ: `lint_run.log` `mypy_run.log` `myscale2.log`
`pytest_full3.log` `failed_list3.txt` `collect3.log` `openapi3.log`。

