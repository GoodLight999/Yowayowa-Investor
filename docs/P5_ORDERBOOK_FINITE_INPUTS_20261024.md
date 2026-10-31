# P5: 注文板の価格・数量と執行数量を有限正値へ制限（inf/NaN の計算混入を拒否）

カード: t_35887f34（worker-flash・2026-10-25 実施・VPS ubuntu + TSUKUMO-KUN 両環境実測）
親: t_5686873e（impact provenance・承認済） / t_c0e78ae9。未コミット差分のまま納品（commit/push なし）。

改訂（第 2 提出・2026-10-25）: 第 1 提出後の独立検収（audit-sol）で R1（本書の旧版挙動記述を
実応答に一致させる是正）と R2（専用テスト socket 遮断の kwargs/省略時既定漏れ）を是正した。
**製品コード（models/service/routes/baseline）は第 1 提出から hash 不変**。専用テストは
58→60 件（socket 遮断強化とその無通信回帰 2 件追加）。是正に伴い §4/§5/§7/§8/§9/§10 を
実測値付きで更新。

## 1. 対象と CTO 確定設計

注文板の非有限入力境界の保守 1 件。発注・新データソースは扱わない。

実測された穴（CTO 読取専用監察 2026-10-24 の独立再現を本カードでも RED として再現・§4）:

- `OrderbookLevel(price=+inf)` / `size=+inf` と `ExecutionImpactRequest(quantity=+inf)` が
  モデル検証を通過（`gt=0` は +inf を拒否しない）。
- `simulate_execution_impact(quantity=NaN)` 直呼びは既存の `<= 0` 検査を抜けて計算され、
  `requested_quantity` / `fillable_quantity` / `total_cost` が NaN。板側 `size=+inf` では
  深度合計が inf、`order_flow_imbalance` / `micro_price` が NaN になった。
- モデルの NaN / -inf は既存の `gt=0` が既に拒否していた（文字列 "-Infinity"/"NaN" も含む）。
  service 直呼びの -inf も、既存の `<= 0`（strictly positive）検査で既に ValueError で拒否されていた。
  実際の穴は **+inf（モデル 3 入力と service 直呼び）と、service 直呼びの NaN のみ** であることを
  RED で確認済み。新版は service 直呼びの -inf を「strictly positive」文から非有限固定文へ
  分類し直したのみであり、-inf を新たに塞いだ「穴」ではない。

CTO 確定設計（本書はこれから逸脱しない）:

1.  既存 3 入力 `Field` に `allow_inf_nan=False` を追加。`gt=0`・型・フィールド名・既定値・
    通常の数値変換は一切変えない。
2.  `simulate_execution_impact` 直呼びは `math.isfinite(quantity)` と `quantity > 0` を必須化し、
    計算前に固定 ValueError で拒否。例外文へ入力値・パスを埋め込まない。
3.  既存 metrics/impact の計算式・丸め・順序・正常数値・警告・synthetic/degraded/provenance は不変。
    欠測をゼロ化しない、非有限値を clamp/補完/文字列化して成功扱いにしない。
4.  API は既存 Pydantic の 422 経路を使用。独自の汎用 error handler・新 API は作らない。
5.  有限だが極大値の演算 overflow 対策、crossed book/並び順/時刻/通貨の新契約は本件外。

### 全呼出元（API / CLI / AI・実在確認済）

| 経路 | 位置 | 本変更後の挙動 |
| --- | --- | --- |
| API impact | `api/orderbook_routes.py:70-84` | `ExecutionImpactRequest.quantity` を Pydantic が検証 → 非有限/非正は 422（既存の 422 経路・無変更） |
| API snapshots | `api/orderbook_routes.py:87-101` | `OrderbookLevel.price/size` を Pydantic が検証 → 422・保存ファイル作成無し（§5 実測） |
| CLI | `orderbook_cli.py:131-141` | typer float `--qty` → `estimate_impact`。非正は従来どおり ValueError 族（メッセージ不変）、非有限は新固定 ValueError。CLI 側の捕捉は変更なし（InvalidOrderbookSymbolError のみ捕捉の既存実装のまま） |
| AI tool | `services/ai_agent.py:2215-2232` | `float(args["quantity"])` → 従来の `qty <= 0` 護りのみでは `float("Infinity")` が通過し NaN 混入成功応答になり得た。本変更後は `estimate_impact` が固定 ValueError を出し、`_execute_tool`（同 1315-1319）の既存捕捉で `{"error": "ValueError: Execution quantity must be a finite number."}` が構造化応答になる（success 偽装の解消・既存捕捉機構のまま） |
| service 直呼び | tests / probe | §4 のとおり |

## 2. 入力 / コード hash（実測 sha256）

着手前 = 親カード t_5686873e 承認時の最終状態（VPS worktree と TSUKUMO 先行候補木で hash 一致を確認済み）:

| ファイル | 着手前 | 着手後 |
| --- | --- | --- |
| `src/yowayowa/orderbook_models.py` | `e778b4fc…a1a1` | `825a3aa3…68447` |
| `src/yowayowa/services/orderbook_service.py` | `5daeaf43…0292` | `a8e7b84f…2ec01` |
| `src/yowayowa/api/orderbook_routes.py` | `fd523921…e362` | `fd523921…e362`（無変更） |
| `docs/openapi_baseline.json` | `9a5180b9…4551` | `9a5180b9…4551`（無変更） |
| `tests/test_orderbook_finite_inputs.py` | （存在せず） | `3ab7d48d…2a3e9`（新規・60 tests・第 2 提出での R2 対応版。第 1 提出版は `97486cee…a023b`・58 tests） |
| `tests/test_orderbook.py` / `test_orderbook_auth.py` / `test_orderbook_impact_provenance.py` / `test_orderbook_symbol_path_safety.py` | 既存 | 無変更（§4・§7 で回帰確認） |

本書は検証完了後に作成したため TSUKUMO 両木には存在しない（親カード §7 と同一扱い）。

## 3. 実装差分（本カードが触ったものはこれだけ）

- `orderbook_models.py`: 3 入力 Field に `allow_inf_nan=False` を追加（`price`・`size`・`quantity`）。
  `Field(gt=0, allow_inf_nan=False, description=...)`。他は一切不変。
- `orderbook_service.py`: `import math` 追加、`simulate_execution_impact` 冒頭に
  `if not math.isfinite(quantity): raise ValueError("Execution quantity must be a finite number.")`
  を追加。既存の
  `if quantity <= 0: raise ValueError("Execution quantity must be strictly positive.")`
  はメッセージ含め完全不変（順序: 非有限を先に判定。0/負は従来どおり既存文で拒否）。
- `tests/test_orderbook_finite_inputs.py`: 新規専用テスト（socket 遮断・一時 base_dir 隔離）。
  第 2 提出で `_no_network` を強化（R2）: family 判定を 位置引数 / `family=` キーワード /
  省略時既定（stdlib 既定は AF_INET）の**全呼出形式**へ拡張し、INET/INET6 を全形式で拒否。
  AF_UNIX は TestClient 自己パイプ用に許可を維持。fake コンストラクタ（実ソケットを作らない
  記録専用 fake）を使った無通信回帰テスト 2 件を追加（位置/キーワード/省略の 3 形式が
  コンストラクタ到達前に拒否されること・AF_UNIX が通過することを実測 assert）。
- 本書。計算式・丸め・警告・provenance・帳簿様式の変更はゼロ。

## 4. RED → GREEN（VPS 実測: `.venv/bin/pytest tests/test_orderbook_finite_inputs.py`・socket 遮断・一時 store）

備考（正直な試行記録）: 最初の RED 実行（`red_run.txt`・25F/33P）は socket 遮断ハーネスが
asyncio ループの AF_UNIX 自己パイプ（`socketpair`）まで遮断し TestClient が全滅したため
**無効** であり、さらに OrderbookLevel の項目 parametrize が他必須フィールド欠落の
ValidationError で空振りだったため、遮断を INET のみへ限定し有効な他フィールドを与えて
やり直した。以降の実測は `red_run2.txt` / `green_run2.txt` が正。

RED（着手前コード）: rc=1・**16 failed / 42 passed**。失敗の全実測:

- モデル: `price=+inf` / `size=+inf` / `quantity=+inf` と文字列 `"Infinity"` が
  ValidationError なしで受理（DID NOT RAISE ×6 ケース実測）。
- service 直呼び: `+inf` / `NaN` が ValueError 無しで計算混入（DID NOT RAISE ×2）、
  `-inf` は「strictly positive」文で逸脱（期待は非有限固定文）。
- API: impact `quantity` 文字列 `"Infinity"` → **200**。snapshots の `price`/`size` 文字列
  `"Infinity"` → **200**。非標準 JSON リテラル `Infinity` → **200**（snapshots では
  inf 付き `latest.json` が実際に永続化される＝本カードが塞ぐ穴）。
- 保存済み +inf 板: degraded にならず `provenance.provider == "orderbook-engine"` で
  正常板として読み込まれる（実測 assert）。
- 一方で文字列 `"-Infinity"` / `"NaN"` / `"0"` / `"-1"` と service の 0/負 は着手前でも既に
  422 / ValidationError / 既存文（strictly positive）で拒否されていた（42 passed の内実・
  モデル `gt=0` の既存防御）。非標準リテラル `NaN` / `-Infinity` もモデル層では着手前から
  ValidationError だったが、**API 応答としては 422 ではなかった**（既定 422 ハンドラの
  描画失敗で 500・§5 の実測表どおり）。第 1 提出時の本書に記した「着手前でも 422」の
  表現は誤記で、第 2 提出で §5 実応答どおりに訂正した。

GREEN（実装後）: **rc=0・全 pass**。第 1 提出時は 58 tests（`green_run2.txt`・
`collect.txt` 収集数 58 実測）。第 2 提出は R2 対応で 60 tests（`green_run3.txt` ・
先頭行 60 dots 全 pass・rc=0・`collect3.txt` 収集数 60 実測）。本環境の `-q` 実行は
最終サマリ行が出力されないため件数は進行ストリームの文字実測と収集数で確定
（§8.4 と同一手法）。製品差分は第 1 提出から無変更のため RED（16F/42P・当時の
58 件構成）は再実行せず第 1 提出時の実測を引用。

## 5. API 境界の実測（TestClient・標準 JSON 系は契約として固定、非標準リテラルは観測として記録）

標準 JSON（本カードが契約として固定する面）:

- `POST /v1/orderbook/{s}/impact` に `quantity` へ文字列 `"Infinity"` / `"-Infinity"` /
  `"NaN"` / `"0"` / `"-1"` → 全て **422**・500 無し・保存ファイル作成無し（着手前は
  `"Infinity"` のみ 200）。
- `POST /v1/orderbook/{s}/snapshots` に `price` / `size` へ上記文字列 → 全て 422・
  **`latest.json` 未作成**（着手前は `"Infinity"` が 200・永続化までしていた）。
- 正常系は既存どおり 200: impact `quantity=300.0` （fully_filled=True・全 12 数値+provenance 不変）、
  snapshot POST は 200 で round-trip 同一（price 100.0 / size 10.0 実測）。

非標準 JSON（`NaN` / `Infinity` / `-Infinity` リテラル・観測として記録。第 2 提出で実応答を実測し直し）:

実測方法: 着手前複製（`.pre` の models/service `e778b4fc…`/`5daeaf43…` + 現行 routes）を
別プロセスに読み込み、TestClient(app, raise_server_exceptions=False)・一時 store・
INET ソケット遮断（AF_UNIX は自己パイプ用に許可）で **3 スロット × 3 リテラルの
9 ケース両版を実測**（`literal_probe.py` → `literal_probe_pre.json` / `literal_probe_post.json`）。

| スロット | リテラル | 着手前 | 着手後 |
| --- | --- | --- | --- |
| impact.quantity | Infinity | 200（latest.json 生成 2425B は空 store への既存合成 seed 書込 = inf 入力と無関係の既存経路） | 500・保存 0 |
| impact.quantity | -Infinity | **500**・保存 0 | 500・保存 0 |
| impact.quantity | NaN | **500**・保存 0 | 500・保存 0 |
| snapshots.bids[0].price | Infinity | **200 + inf 板そのものを latest.json へ永続化（831B）＝本カードが塞ぐ穴** | 500・保存 0 |
| snapshots.bids[0].price | -Infinity | **500**・保存 0 | 500・保存 0 |
| snapshots.bids[0].price | NaN | **500**・保存 0 | 500・保存 0 |
| snapshots.bids[0].size | Infinity | **200 + inf 板を永続化（824B）＝本カードが塞ぐ穴** | 500・保存 0 |
| snapshots.bids[0].size | -Infinity | **500**・保存 0 | 500・保存 0 |
| snapshots.bids[0].size | NaN | **500**・保存 0 | 500・保存 0 |

- 着手前の **500** は pydantic（`gt=0`）が値を拒否した後、FastAPI **既定** 422 ハンドラが
  starlette `JSONResponse`（`allow_nan=False`）でエラー詳細内の生 `inf`/`nan` を描画できず
  `ValueError: Out of range float values are not JSON compliant: inf` になるため
  （`http.exception` 構造化ログに ValueError 実測・`green_run.txt` に traceback 保存）。
  **「着手前でも 422」ではない。** 着手後も値の拒否箇所が `gt=0` から
  `allow_inf_nan=False` に替わるだけで、既存の描画失敗相互作用はそのまま
  500・保存 0 のまま（第 1 提出の本書に記した「着手前 422」は誤記）。
- 本カードの CTO 設計は「既存 422 経路のみ・独自の汎用 error handler を作らない」のため、
  この描画失敗は **修正せず観測として記録** した。専用テストは「例外は表面化するが
  永続化は起きない」ことのみを実測 assert（`latest.json` 未作成・実測）。
  リテラル入力の 422 化が必要なら targeted handler の新契約判断として CTO へ返す題材。
- seed 内容確認: 着手前 impact=Infinity ケースの latest.json（2425B）は
  `provider=synthetic-seed-orderbook`・全 price/size 有限の合成 seed 板
  （`impact_seed_check.py` で内容実測）であり、inf 入力自体の永続化ではない。

## 6. 保存済み +inf 板: 既存 degraded/帳簿経路と原物保全（実測）

生バイト（標準 JSON 外のため直接書込）で `price: Infinity` 板 / `size: Infinity` 板の
2 変数を一時 store に置き、`OrderbookService.get_snapshot` を実行:

- 着手後: `ValidationError` → 既存の schema-invalid 経路で **synthetic seed 板 + degraded note** を
  返し、`corrupt_reads.jsonl` に 1 件帳簿記載（`error_type=ValidationError`・`sha256` は原物と一致）。
  原物ファイルは **hash 不変**。degraded provenance を載せた impact 結果も実測。
- 着手前: 正常板（`provenance.provider == "orderbook-engine"`・既定 provenance）として
  読み込まれていた（§4 の RED 実測）。原物上書き・ゼロ埋め・clamp は本カードでも起きない。

## 7. VPS 静的検査（host/command/rc 付き実測）

| 検査 | command | rc | 内容 |
| --- | --- | --- | --- |
| mypy 対象 2 ファイル | `.venv/bin/mypy src/yowayowa/orderbook_models.py src/yowayowa/services/orderbook_service.py` | 0 | no issues in 2 source files |
| mypy 全体 | `make typecheck` 相当（mypy src/yowayowa） | 0 | no issues in 243 source files |
| ruff check 全体 | `.venv/bin/ruff check src tests` | 1 | **36 件・親カード §6 記録と同一ファイル/件数**（test_paperloop_v12 16・paperloop_panel 11・test_paperloop 4・test_jp_value_quality_backtest_units 3・paperloop 1・loop_cli 1）。本カード 3 ファイルは 0 件（grep 実測） |
| ruff format 全体 | `.venv/bin/ruff format --check src tests` | 1 | 8 ファイル would be reformatted・全て他カード WIP（paperloop 系・jp_value_quality 系・test_screening_cli_dates）。本カード 0 件 |
| openapi 生成 | `make openapi` | 0 | Wrote openapi.json |
| openapi-check | `make openapi-check` | 0 | **baseline matches（155 operations）・baseline hash `9a5180b9…` 不変 = 新規 schema 差分 0**（`allow_inf_nan` は JSON Schema キーワードを生成しない実測） |

- 専用 + 既存 orderbook/provenance/auth/symbol + openapi + ai_agent の targeted 実行
  （9 ファイル）: **rc=0・skip 無し**。第 1 提出時は 167 tests
  （`targeted_run.txt` 末尾実測「167 passed, 1 warning in 22.66s」）。第 1 提出の本書に
  記した「既知の環境差 skip を含む」は**誤記**であり、skip は全量側（§8.4 の 11 件）にのみ
  存在していた（targeted_run.txt に skip は無し）。
  第 2 提出時（専用 +2）は **169 tests**: `targeted_run2.txt` が 169 dots 全 pass・rc=0、
  junit（`orderbook2.xml`）で tests=169 / failures=0 / errors=0 / skipped=0 を機械実測。
- normal buy/sell・流動性不足・早期 return・丸めは既存テスト群 + 専用テストの
  数値固定（`average_price=101.333333` 等・`test_orderbook.py` と同一の期待値）で
  無変更を回帰確認。

## 8. TSUKUMO 全量検証（親カード §7 と同一方式・hash 検証済み両木）

### 8.1 転送と版検証

- 元木: VPS ubuntu `/root/current-work/yowayowa-investor`（branch agent/commercial-foundation、
  HEAD a05d248・未コミット差分を含む working tree 全体）。
- 転送: `rsync -a`（.git/.venv/cache 類除外）→ **4,822 files / 286,092,919 bytes**。
- 検証: git dirty/untracked 187 ファイルの SHA256 manifest 照合 → **186/187 byte 一致**。
  例外 1 件（親カードと同一種の既知レース）: `src/yowayowa/acquisition/documents.py` は
  転送中に他カードが編集中で manifest 時点（`d83988a0…`）→ 転送版（`0932e4e7…`）→ 現 VPS
  （`f2c50826…→再転送後 c19772fc…`）と 3 値不一致。本カード対象外ファイルで、
  **cand/pre 両木が同一バイト** のため帰属判定に影響なし。TSUKUMO 側は転送時点で凍結。
- ツリー: 候補 `~/yw-verify-t_35887f34` と pre-card 対照 `~/yw-verify-t_35887f34-pre`
  （候補から models/service を `.pre`（`e778b4fc…`/`5daeaf43…`）へ戻し hash 実測一致・
  専用 test を除外。他は同一）。本カード 4 ファイルの実測 hash:
  cand: models `825a3aa3…` / service `a8e7b84f…` / routes `fd523921…` / 専用test `97486cee…` /
  baseline `9a5180b9…` = VPS 現物と一致。pre: `e778b4fc…` / `5daeaf43…` / `fd523921…` / `9a5180b9…`。

### 8.2 環境と実行方法

- host=TSUKUMO-KUN、既存共有 venv `/home/good-light/vps-work/current-work/yowayowa-investor/.venv`
  （**Python 3.14.7 / pytest 8.4.2 / ruff 0.16.8 / mypy 1.20.2** — 親カード実測と同一・
  VPS venv とは Python 版が異なる第 2 環境として有効）。`make V=<main>/.venv <target>`（TZ=UTC）。
- 全段階実行: `run_verify_t35887f34.sh`（TSUKUMO `~/t35887f34_verify/`・本書方式で lint /
  typecheck / test / openapi / openapi-check の 5 段階を個別に rc 付き記録）。

### 8.3 実測結果（cand / pre・すべて rc 実測）

| stage | cand | pre | 内容 |
| --- | --- | --- | --- |
| lint | rc=2 | rc=2 | **error 集合は cand/pre で diff 完全一致**（paperloop 系 36 ＋ 転送凍結 `documents.py` 1 = 37 件・両木共通）。本カード対象 3 ファイルは 0 件 |
| typecheck | rc=0 | rc=0 | mypy src/yowayowa 全体 |
| test | rc=2 | rc=2 | 両木とも既知 WIP 2 ファイル（`test_cusum_watch.py`・`test_paperloop_v12.py`）の collection error で中断・同一。→ 8.4 の fill-in |
| openapi | rc=0 | rc=0 | openapi.json 生成 |
| openapi-check | rc=0 | rc=0 | 両木とも baseline matches（155 operations・差分はログ内の木パスのみ） |

### 8.4 test fill-in（全量 pytest・帰属実測）

実行コマンド（両木同一・TZ=UTC・PYTHONPATH=<tree>/src・`run_verify_t35887f34.sh <tree> <tag> fillin`）:

```
<main>/.venv/bin/pytest -p no:cacheprovider --cov=yowayowa --cov-report=term-missing \
  --basetemp=<tree>/.pytest_tmp_<tag> \
  --ignore=tests/test_cusum_watch.py --ignore=tests/test_paperloop_v12.py -q
```

| | cand | pre |
| --- | --- | --- |
| rc | 1 | 1 |
| passed | 3251 | 3193 |
| failed | 12 | 12 |
| skipped | 11 | 11 |
| xfail | 1 | 1 |
| 合計 | 3275 | 3217 |

- 差分 **+58 pass = 本カード専用テスト 58 件**（収集数と一致）であり、**全件 pass**。
- failed 12 件の集合は両木で **byte 一致**（test_defensive_rotation 8・
  test_jp_value_quality_backtest_units 4・いずれも既存 WIP/既存ゴールデン系）。
- skipped 11・xfail 1 も両木同数。**本カード差分に起因する失敗は 0 件**。
- 備考: 本環境では `--cov` 付き `-q` 実行の最終件数サマリ行が出力されないため、
  件数は進行ストリーム文字集計（`.`/`F`/`s`/`x`）と short summary の FAILED 列挙
  （12 件・両木 diff 一致・`FAILED_SETS_IDENTICAL` 実測）で確定（親 §7.4 と同一手法）。

### 8.5 結論

- 全段階で cand と pre が同一挙動。検出された全失敗（lint 37・collection error 2 ファイル・
  pytest failed 12）は **pre-card 対照でも同一に発生する既存 WIP／既存環境起因**であり、
  本カード差分（3 入力 Field の有限化 + service の数量検査 + 専用テスト）に起因する
  失敗は 0 件。TSUKUMO 第 2 環境でも RED→GREEN が成立（fill-in 内で専用 58 全 pass 実測）。

### 8.6 第 2 提出の補足（R2 対応後）

製品コード（models/service/routes/baseline）は第 1 提出から hash 不変のため、TSUKUMO 全量
fill-in は再実行していない（§8.4 は第 1 提出時の 58 件構成の実測のまま）。代替として
第 2 環境（TSUKUMO-KUN・Python 3.14.7・同一共有 venv）で R2 対応後の専用テスト 60 件を
候補木 `~/yw-verify-t_35887f34` に更新した上で再実行: **rc=0・junit tests=60 /
failures=0 / errors=0 / skipped=0**（`~/t35887f34_verify/dedicated_r2_tsukumo.log` /
`dedicated_r2_tsukumo.xml`）。VPS 側 targeted 169 rc=0（§7）と併せて R2 対応後の回帰を
確認済み。

## 9. 未検証範囲（正直な限界）

- **実フィード板・本番応答は未検証**（検証は socket 遮断・一時 store / scratch store のみ。
  Vercel 本番 `/v1/orderbook/*` への反映は未確認・本カードは未コミットのため未配信）。
- 非標準 JSON リテラル（NaN/Infinity/-Infinity）の実応答は旧新とも **500** である
  （Infinity のみ、着手前は 200＋inf 板永続化 → 着手後 500＋永続化なし。§5 実測表）。
  422 応答化は **本カードでは実施せず観測記録のみ**（独自 error handler 禁止の
  設計確定点による。422 化には targeted handler の新契約判断が必要 = CTO 判定事項）。
- 有限だが極大値（1e308 級）の丸め・overflow 挙動は本件外（CTO 設計明示）。
- CLI での非有限 `--qty inf` 入力時の表示体裁（typer の unhandled ValueError 流儀）は
  既存の例外族に乗るのみで、CLI 固有の表示は変更・検証していない。
- TSUKUMO fill-in の skipped 11・xfail 1 の内訳は個別検証していない（両木同一集合まで実測）。
- VPS では `make verify` 一括は既存 lint 36 件で早期停止するため、§7 の段階別実測で代替。

## 10. 証跡

VPS worker-flash scratch `/root/.hermes/profiles/worker-flash/cache/scratch/p5_finite/`
（24h 自動掃除対象のため要旨は本書に転記済み）:
`red_run.txt`（無効ハーネス試行・参考）・`red_run2.txt`（RED 16F/42P 正式）・
`green_run.txt`（GREEN 実行・非標準リテラル観測の traceback 含む）・`green_run2.txt`（GREEN 58P・rc=0）・
`collect.txt`（dedicated 58 収集数）・`targeted_run.txt`（167 tests rc=0）・
`green_run3.txt` / `collect3.txt`（**第 2 提出専用 60**・rc=0・収集数 60）・
`targeted_run2.txt` / `orderbook2.xml`（**第 2 提出 targeted 169**・rc=0・junit tests=169/F0/E0/S0）・
`literal_probe.py` / `literal_probe_pre.json` / `literal_probe_post.json`
（**非標準リテラル実応答 9 ケース両版**・INET 遮断下・§5 表の出所）・
`impact_seed_check.py`（着手前 impact=Infinity ケースの latest.json 内容確認=合成 seed 板）・
`mypy_files.txt` / `mypy_full.txt` / `lint_check.txt` / `lint_fmt.txt` /
`openapi_gen.txt` / `openapi_check.txt` / `wip_manifest_vps.txt`（187 件）/
`rsync_t35887f34.log` / `cand_fillin.log` / `pre_fillin.log` / `cand_counts.txt` /
`fillin_counts.txt` / `pre_src/orderbook_models.py.pre`・`orderbook_service.py.pre`
（着手前複製・hash §2 実測）。

TSUKUMO 本体（good-light@TSUKUMO-KUN・残存）:
`~/yw-verify-t_35887f34/`（候補木＋verify_logs/・第 2 提出で専用テストを R2 対応版に更新）・
`~/yw-verify-t_35887f34-pre/`（対照木＋verify_logs/）・
`~/t35887f34_verify/`（実行スクリプト・manifest・.pre 複製・第 2 提出の
`dedicated_r2_tsukumo.log` / `dedicated_r2_tsukumo.xml` = 専用 60 rc=0 junit）。
