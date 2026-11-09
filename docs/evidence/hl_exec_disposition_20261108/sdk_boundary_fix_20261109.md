# SDK境界3契約のRED→GREEN是正（t_49064d09）

2026-11-09。対象は Hyperliquid 実行アダプタの境界防御（cloid変換・testnet URL
allowlist・不明注文の非再送照合）。実注文・取消・署名・鍵生成・送信・pushは0件。
既存の停止標識 `scripts/hl_testnet_s07/s07_daily_forward.py` は変更していない。

## 結論

- 3契約を `src/yowayowa/hl_exec/guards/`（新規・隔離パッケージ）に実装し、
  既存 `HyperliquidExchangeClient` をそこへ接続した。
- 通信遮断下（pytest-socket `--disable-socket` + `socket.connect`/`create_connection`
  拒否fixture）で、本物の `hyperliquid-python-sdk 0.24.0` の
  `Exchange.order` / `bulk_orders` / wire変換を通した試験が **77 passed**。
  HL関連スイート全体は **212 passed**。
- 旧アダプタに対するREDは **6 failed / 71 passed**、guards未実装時は
  collection error 2件。RED→GREENのログは `checks/sdk_boundary_20261109/`。
- 不明注文経路に再送は存在しない。コードは `send_order_once` 内の送信呼び出し
  1箇所のみ・ループなしをASTで固定し、テストでも送信回数1を全ケースで確認した。

## 契約と実装

| 契約 | 実装 | 内容 |
|---|---|---|
| (a) cloid:str → 公式 `Cloid` | `guards/cloid.py` | `str`のみ受付。`^0x[0-9a-fA-F]{32}$`、全長34。16進部分だけ小文字化。変換後に `str()`/`to_raw()` の往復一致を再検証。不正は `CloidContractError`（`ValueError`）で送信前に停止。`cloid_to_str` は公式型のみ受付（生文字列の送信経路を型で遮断）。 |
| (b) testnet URL allowlist | `guards/urls.py` | 正規化後に完全一致のみ。部分一致・前方一致・suffix一致を禁止。注入clientの `base_url` も同一関数で照合し、`base_url` を持たない注入物は照合不能として生成時に拒否。 |
| (c) 不明注文の照合 | `guards/unknown_order.py` | 送信は1回だけ。timeout/不明応答では既存cloidを open orders と orderStatus に突合し、`confirmed` / `absent` / `unresolved` の3値。`unresolved` は `UnknownOrderStateError` で上位へ返し、再送しない。 |

### URL正規化規則（`guards/urls.py` docstring が正本）

1. `str` かつ非空。
2. ASCII印字可能文字のみ（空白・制御文字・非ASCII/IDN・`\` を拒否）。
3. 生文字列が `https://` で始まる（`HTTPS://` は拒否）。
4. userinfo 拒否。
5. ホストは既に小文字（大文字化は正規化せず拒否）。末尾ドット拒否。
6. ホストは `api.hyperliquid-testnet.xyz` と完全一致。
7. ポートは省略または `443` のみ（`:443` は省略形へ正規化、他は拒否）。
8. パスは空または `/` のみ。
9. `?` / `#` を含む文字列は拒否。

正規形は常に `https://api.hyperliquid-testnet.xyz`。

### 不明注文の3値判定

| 値 | 条件 |
|---|---|
| `confirmed` | open orders に cloid（またはoid）一致、もしくは orderStatus が `status=order`。 |
| `absent` | orderStatus が `unknownOid`/`unknownCloid` を返した（存在の確定否定）。 |
| `unresolved` | 照会失敗・想定外応答・照会手段なし。`UnknownOrderStateError`。 |

発注応答自体も分類する。`resting`/`filled` は確定、全statusが `error` は
拒否＝不在、それ以外（statuses欠落・非object）は照合へ回す。

## 変更ファイル

- 新規: `src/yowayowa/hl_exec/guards/{__init__,cloid,urls,unknown_order}.py`
- 変更: `src/yowayowa/hl_exec/client.py`
  - `base_url` を `canonicalize_testnet_url` へ接続（表記ゆれの正規化と拒否を同一規則に）。
  - 注入 `exchange` を `assert_testnet_exchange` で生成時に照合（`base_url` 無しは拒否）。
  - `order()` の cloid を `to_sdk_cloid` に置換。
  - `cancel_by_cloid()` が生文字列をSDKへ渡していた不具合を修正（公式型へ変換）。
- 新規テスト:
  - `tests/test_hl_exec_testnet_url_guard.py`（SDK非依存・43件）
  - `tests/test_hl_exec_sdk_boundary_guards.py`（本物SDK・34件）
- 既存テストdoubleの契約追随: `tests/test_hl_exec_exchange_contract.py`,
  `tests/test_hl_exec_roundtrip.py`（`base_url` 属性の付与のみ）。

## negative case 表（すべてテスト化）

| 分類 | 入力 | 期待 |
|---|---|---|
| mainnet | `https://api.hyperliquid.xyz` | 拒否 |
| scheme | `http://...` / `HTTPS://...` | 拒否 |
| suffix攻撃 | `https://api.hyperliquid-testnet.xyz.attacker.invalid` | 拒否 |
| prefix攻撃 | `https://attacker.invalid/api.hyperliquid-testnet.xyz` | 拒否 |
| サブドメイン | `https://evil.api.hyperliquid-testnet.xyz` | 拒否 |
| 部分ホスト | `https://api.hyperliquid-testnet.xy` / `...xyz0` | 拒否 |
| 別ポート | `:8443` / `:80` | 拒否 |
| userinfo | `https://user:pass@...` / `https://user@...` | 拒否 |
| パス | `/evil` / `/.` | 拒否 |
| クエリ/フラグメント | `?redirect=evil` / `#frag` | 拒否 |
| 末尾ドット | `https://api.hyperliquid-testnet.xyz.` | 拒否 |
| 大小文字 | `https://API.HYPERLIQUID-TESTNET.XYZ` | 拒否 |
| IDN | `https://аpi...`（キリル文字） / `xn--pi-8cd...` | 拒否 |
| 空白/制御 | 前後空白・埋め込み改行 | 拒否 |
| backslash | `https://api.hyperliquid-testnet.xyz\@evil.invalid` | 拒否 |
| 空/相対/非str | `` / `api....xyz` / `None`/`123`/`bytes`/`True` | 拒否 |
| 注入client | 上記URLを `base_url` に持つ注入物 | 生成時に拒否 |
| 注入client | `base_url` 属性なし | 照合不能として生成時に拒否 |
| cloid | 長さ違い・非hex・`0X`・空白・改行・非str・公式型 | `CloidContractError` |
| 不明注文 | 照会例外 / 想定外応答 | `unresolved` → 明示エラー・再送なし |

正常系: 正規形、末尾 `/`、`:443`、`:443/` は同一正規形へ。cloidは大文字hexを
小文字へ正規化し、公式 `Cloid` の `str()`/`to_raw()`/wireの `c` が一致する。

## RED→GREEN 実測

環境: TSUKUMO-KUN / Python 3.14.7 / pytest 8.4.2 / pytest-socket 0.8.1 /
hyperliquid-python-sdk 0.24.0（`checks/sdk_boundary_20261109/environment.txt`）。
遮断: `--disable-socket` に加え、テストモジュールの autouse fixture が
`socket.socket.connect` / `connect_ex` / `socket.create_connection` を拒否。
positive control `test_network_access_is_blocked_in_this_run` が遮断を実測。

| 段階 | コマンド（要旨） | 結果 | ログ |
|---|---|---|---|
| RED-0: guards未実装 | 新テスト2ファイルを旧ツリーで実行 | collection error 2件（`No module named 'yowayowa.hl_exec.guards'`）、exit 2 | `red_before_guards.txt` |
| RED-1: 旧adapter | guardsのみ配置し `client.py` はHEADのまま | **6 failed / 71 passed**。末尾`/`・`:443`拒否、注入`base_url`無し許容、cloid正規化なし、`cancel_by_cloid`文字列渡し、invalid cloid送信到達 | `red_before_client_fix.txt` |
| GREEN: 境界2ファイル | 遮断下で実行 | **77 passed in 1.36s** | `green_boundary.txt` |
| GREEN: HL一式 | `tests/test_hl_exec_*.py` | **212 passed in 3.95s** | `green_hl_suite.txt` |
| 遮断+本物SDKの個別表示 | socket plugin 0.8.1 読込、`real_sdk`/遮断control 4件 | 4 passed | `green_socket_blocked_and_real_sdk.txt` |
| 型検査 | `mypy src/yowayowa`（TSUKUMO検証venv） | **Success: no issues found in 263 source files** | `typecheck.txt` |
| lint/format | `ruff check src tests` / `ruff format --check src tests`（VPS） | All checks passed / 490 files already formatted | 本文記載 |

RED-1の6失敗は、いずれも「旧アダプタが新契約を満たさない」ことによる失敗で、
guards側の契約テスト（cloid正規化・往復一致・URL negative 27件＋非str 4件・
注入client 7件・3値照合）は同時点で通っている。したがってREDは実装漏れの
検出として機能している。

## 非再送のコード/テスト双方の証跡

- コード: `send_order_once` は `exchange.order(...)` を1回だけ呼ぶ。再送ループ
  （`for`/`while`）を持たない。timeout時は `reconcile_unknown_order` へ分岐する。
- テスト: `test_send_order_once_has_no_retry_loop_and_single_transport_call` が
  ASTで「`order` 呼び出し1件・ループ0件・`cloid` 引数が `sdk_cloid` 変数」を固定。
- 挙動: timeout→confirmed / timeout→absent / timeout→unresolved の3ケースと、
  本物SDKの `_post_action` をTimeoutErrorにしたケースで、送信回数が常に1である
  ことを確認（`len(calls)==1` / `len(sent)==1`）。

## 境界・禁止事項の遵守

- 実注文・取消・入出金・署名・鍵生成・実ネットワーク送信は0。署名関数はテスト内で
  空dictへ差し替え、送信は `_post_action` のテストdoubleで停止。ウォレット/
  秘密鍵は生成も読取もしていない。
- `scripts/hl_testnet_s07/s07_daily_forward.py` は未変更（`git status` 対象外）。
- 本番送信経路の挙動変更は、注入transportの `base_url` 必須化とcloid正規化に限定。
  実SDK経由の `base_url` は常に存在するため、通常構築は影響を受けない。
- push・デプロイ・cron変更は行っていない。

## 残課題

- 本カードは隔離モジュール＋既存アダプタの境界是正まで。S-07の自動運用再開、
  停止標識の解除、実注文は別途裁可が必要。
- 全体 `make verify` は本カードでは未実行（VPS pytest禁止・TSUKUMOのfull suiteは
  既知のbaseline 9失敗を含む別カード領域）。HL一式・lint・format・型検査は実測済み。
