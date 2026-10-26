# P5 CLI HTTP 失敗表示の修正（t_8262d080）— 実測記録

- 実施日: 2026-10-23 JST
- 担当: worker-flash / 実装ホスト: VPS(ubuntu) / 全量verify: TSUKUMO-KUN
- 関連: `docs/P5_DEADEND_AUDIT_20261023.md` §2.3/§3 R1 の**表示面だけの修正**。
  **R1（5桁コード→ティッカーの値変換）の解消ではない。** 29010 は修正後も 404 のままで、
  コードの補正・変換・意味の変更はしていない。

## 1. 変更内容（3ファイル + 本doc）

| ファイル | 変更 |
|---|---|
| `src/yowayowa/cli_shim.py` | `main()` 境界でのみ `httpx.HTTPError`（= `HTTPStatusError` + 全 `RequestError`）を捕捉し、stderr 1行の固定文言 + `SystemExit(文字列)`（終了コード1）に変換 |
| `tests/test_cli_http_errors.py` | 新規。26件（HTTP status 9種 / transport 4種 / 漏えい検査 7種 / 境界回帰 5種 / 正式エントリポイント subprocess 2種） |
| `tests/test_cli_shim.py` | **未変更**（既存の optional 依存エラー回帰2件がそのまま通ることを確認） |
| `docs/P5_CLI_HTTP_ERRORS_20261023.md` | 本ドキュメント |

cli.py / cli_entry.py / サービス / API / モデルは触っていない（git diff で他カードWIPと無混入を確認）。

## 2. 設計の要点

- 捕捉は `except httpx.HTTPError` の1箇所のみ。httpx 0.28.1 では `HTTPStatusError` と
  `RequestError` は `HTTPError` の下で兄弟（実測: probe）。`TimeoutException` /
  `ConnectError` は `RequestError` の派生（実測: isinstance True）。
- 文言分類は**数値statusのみ**を拾う: 401/403=認証・権限、404=対象なし、422=入力、
  429=取得制限、5xx=API側失敗、他=HTTP失敗。timeout / connect / その他RequestErrorも固定文言。
- URL・トークン・ヘッダ・ボディ・detail・HTML・改行・ANSI・Rich markup は**構造的に出ない**
  （固定文字列しか組み立てない。未知サーバ内容の「 sanitize 表示」機構は作らない）。
- リトライ・再送・新規通信は追加していない（fixture サーバの受付数カウンタで1回を検証）。
- typer 0.27.2 の `app()` は httpx 例外を握り潰さず外へ投げる（実測probe）。click/typer の
  自前処理（SystemExit 2 等）・KeyboardInterrupt・非HTTPの ValueError/ModuleNotFoundError
  は従来どおり透過（テストで回帰担保）。

## 3. コード hash

| 対象 | sha256 |
|---|---|
| 修正前 `cli_shim.py`（= HEAD, HEAD@fd9a1d0） | `fe7205cc5dc9e481e2c1be5897910d5a3e44e7a8cf7c7a322fa7b17d86b3df35` |
| 修正後 `cli_shim.py` | `fae32bfa8a2be80a0bc6d6ae31b824e3f8790c40fb42f9f22d22bd88da6e2e91` |
| 固定入力 `fixture_api.py`（404汚染ボディ + 200固定payload） | `f207958044b5ee1587aed00a54c7bc0dee7f38b9562b6b74c88765add52a71d3` |
| 固定入力 `run_variant.py`（BEFORE境界の再現ランナー） | `74afb81ca76d0bb2603fe37e0450753e38f304d7b0a3ba2028bdf07165cf4612` |
| `tests/test_cli_http_errors.py` | `e11a9deefb9a302fca2b8154786ff0a9d178ae2290038d7d031cb1551c1ad33f` |

VPS作業ツリーの `cli_shim.py` は着手時 unmodified（git status で競合カードなし確認）。
TSUKUMO 側の同ファイルも同一 hash `fe7205cc…` で着手前から未変更だったことを確認済み。

## 4. 対象試験（VPS・狭小）

実行: `PYTHONPATH=$PWD/src .venv/bin/pytest tests/test_cli_http_errors.py tests/test_cli_shim.py`（host: ubuntu/VPS）

| 段階 | 結果 |
|---|---|
| RED（実装前・同一入力） | **22 failed, 4 passed**（404実走で rich の30行トレースが出ることを subprocess 実測で確認） |
| GREEN（実装後） | **28 passed, rc=0**（9.1s） |
| `ruff check`（3ファイル） | pass |
| `ruff format --check`（3ファイル） | pass |
| `mypy src/yowayowa/cli_shim.py`（strict） | **Success: no issues found in 1 source file** |

## 5. 前後実測（正式エントリポイントを実プロセス実行）

方法: BEFORE は `git show HEAD:src/yowayowa/cli_shim.py` を取り出し、同一venv・同一
`PYTHONPATH=<tree>/src` で旧 `main()` 境界だけを差し替えて実行（`run_variant.py`）。
AFTER は正式コンソールスクリプト `.venv/bin/yowayowa` を実行。API応答は同一固定レスポンス
（fixture、`127.0.0.1:8390`）で byte 比較。

### 5.1 HTTP 404（fixture・汚染応答）

| 項目 | BEFORE | AFTER |
|---|---|---|
| exit code | 1 | 1 |
| stdout | 0 byte（e3b0c442…b855） | 0 byte（e3b0c442…b855） |
| stderr | **30行 2682byte** の typer/rich トレースボックス（URL `http://127.0.0.1:8390/v1/valuation/29010`・ソースパス・`HTTPStatusError: Client error '404 Not Found' for url …` を表示。末尾は MDN リンク） | **1行 55byte**: `Yowayowa API request failed: HTTP 404 (no such target)` |
| stderr sha256 | `73b01fc9491f16170ba4ac097c5d090f118e2cb5172b25c1ebb0716ecaa19006` | `9f863ae836192b12154970ff513c46d8e1d52ffc7cc0593c73c904ef41c5753a` |

漏えい検査（fixture の偽token `fake-token-abc123`・URL userinfo `user:secret`・ANSI `\x1b`・
Rich `[bold]` を応答ボディ/ヘッダに仕込んだ上で）: AFTER の stderr は全 needle **0件**。
BEFORE のトレースはボディ自体は表示していなかった（typer はトレースに応答本文を出さない）ため
needle 0件 — ** Before/After で消えたのは「内部ソース行・URL・例外連鎖の30行」であり、
「ボディ漏えいがあった」わけではない点は正直に記録する。** ボディ・トークンを含む例外を
境界に通す単体7種（漏えい検査）で構造的に出ないことを担保。

### 5.2 成功（既知銘柄・同一固定レスポンス・byte不変）

| 項目 | BEFORE | AFTER |
|---|---|---|
| exit code | 0 | 0 |
| stdout sha256 | `f45f84030c2b12dc45fc31f4db8df13b8bdb1e6e17f522bcd4947c89298c0698` | **同一** |
| stderr | 0 byte | 0 byte |

→ 成功出力は同一固定レスポンスに対し **byte 等価**（表示系の変更が成功面に触れていない実測）。

### 5.3 ネットワーク失敗（接続先不在・API HTTPと別ケース）

| 項目 | BEFORE | AFTER |
|---|---|---|
| exit code | 1 | 1 |
| stdout | 0 byte | 0 byte |
| stderr | 30行トレース（末尾 `ConnectError: [Errno 111] Connection refused`） | 1行: `Yowayowa API request failed: could not connect to the API` |

## 6. 実ローカルAPIでの実走（原本DB不触・読取のみ）

方法: `data/yowayowa.db` の**コピー**（sha256 `a4eb6c64cfb8f28e1ccbb825df0968d529437eefa49eb8ca78cfd5d5edc50540`）を
`DATABASE_URL=sqlite:////<scratch>/api_copy.db` で `uvicorn yowayowa.api.app:app --host 127.0.0.1 --port 8123` に接続。
（CTO時点の 8123 は不通だったため、本検証用に読取専用で起動。原本DBには書いていない。）

| 手順 | 実測 |
|---|---|
| `GET /v1/valuation/29010`（CLI 実走前） | **404**, `{"detail":"SEC ticker mapping not found for 29010"}` |
| CLI `valuation 29010`（BEFORE 境界） | rc=1・30行トレース・stdout 0 |
| CLI `valuation 29010`（AFTER・正式エントリ） | **rc=1・1行** `Yowayowa API request failed: HTTP 404 (no such target)`・stdout 0 |
| `GET /v1/valuation/29010`（CLI 実走後） | **404 のまま**（API不変） |

→ 「API は 404 のまま、CLI は exit 1 のまま、**内部トレースだけが消える**」を本物のAPI応答で実証。
API HTTP 失敗（5.1/6）とネットワーク失敗（5.3）は別ケースとして記録済み。
**R1 は解消していない**: 29010 は依然 404（`= 対象なし` の正しい意味）で、文言はその意味を保つ。

## 7. 証跡（scratch・24hで消える前提）

`/root/.hermes/profiles/worker-flash/cache/scratch/p5clihttp/` 配下:
`old_cli_shim.py` / `fixture_api.py` / `run_variant.py` / `before_*.out,err` / `after_*.out,err` /
`realapi_*.out,err` / `api_29010_before.json` / `api_copy.db` / `red_run.txt` / `green_run*.txt`

## 8. make verify 全量（TSUKUMO-KUN・host rc記録）

`ssh tsukumo` → `/home/good-light/vps-work/current-work/yowayowa-investor`（branch `agent/commercial-foundation` @ `0a05a17`、他カードの未コミット変更13件あり・触っていない）。

### 8.1 ベースライン（本変更の2ファイルを**適用前**）

- `make verify` → **RC=2**
  - lint ✓（`All checks passed!`）/ typecheck ✓ → `test` で停止
  - test: **2 failed, 1988 passed, 2 skipped**（323.20s）
    - FAILED: `tests/test_crypto.py::test_binance_klines_returns_records_with_daily_provenance` / `tests/test_crypto.py::test_binance_zero_bar_is_skipped`（binance系・本変更と無関係の既存失敗）
  - openapi: 未到達（make は test 失敗で停止）

### 8.2 本変更の2ファイル適用後（scp、双方でsha256照合済み・最終hashで再実行）

- `make verify` → **RC=2**（ベースラインと**同一の既存crypto2件のみ**で失敗）
  - lint ✓（`All checks passed!`）/ typecheck ✓（`Success: no issues found in 228 source files`）
  - test: **2 failed, 2012 passed, 4 skipped**（330.03s）— FAILED はベースラインと同一2件
  - **差分 = +24 passed / +2 skipped ＝ 本変更が追加した26テストと正確に一致**
    （subprocess 2件は TSUKUMO の共有venvに console script が無いため skip。`-rs` 実測:
    `SKIPPED [2] tests/test_cli_http_errors.py:264: yowayowa console script is not installed in this venv`）
  - 注: 最初の実走は テストファイル旧hash `c3b29b70…`（fixture のカウンタ increment を応答後に
    行う race を含む版）で同じ結果。VPSで race を修正（受付時に increment）した最終hash
    `e11a9dee…` で**再実行し同結果**（§8.2の数値は最終版）。修正はテストfixtureのみで
    プロダクトdiff（`fae32bfa…`）は両実行で同一。
- `make openapi`（test失敗で make が止まるため個別実行）→ **RC=0**
  （`export-openapi` は修正後の `cli_shim.main()` を TSUKUMO 上で実際に通過して成功）
- 対象試験: `pytest tests/test_cli_http_errors.py tests/test_cli_shim.py` → **26 passed, 2 skipped**（失敗0）
- 測定後、TSUKUMO ツリーを**着手前の状態に復元**済み（cli_shim.py sha256 `fe7205cc…` に復帰・
  新規テストファイルを unlink・`git status` 13件で着手時と一致。`openapi.json` は gitignore 済み）。

→ **本変更に起因する失敗は0**。既存2件の crypto 失敗は本カードのスコープ外（別カードまたは既知問題）。

## 9. 未達・限界（正直な記録）

- TimeoutException は**実サーバを 30 秒止めての実走はしていない**（`timeout=30` 固定のため
  試験が重い）。`httpx.ReadTimeout`/`ConnectTimeout` インスタンスを `main()` 境界に通す
  単体4種で分類を担保。実走での挙動は connect と同一経路（`RequestError` 派生）。
- 429/5xx の分類は fixture 応答による（実APIで意図的に 429/5xx は起こしていない）。
- make verify 全量の結果は §8 に記載（実施できなければ未達と明記）。
