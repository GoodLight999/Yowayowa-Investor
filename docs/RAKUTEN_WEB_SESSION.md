# 楽天証券 Web セッション実機検証手順書（P1B read-side）

この文書は P1B（楽天証券Web 読み取り側コネクタ）の実セッション検証手順（Definition of Done）である。
コード側は fixture ベースで全て green だが、**リソースカタログの URL は実機未検証の初期仮定**（`verified=False`）であり、
初回実セッション時に下記手順で確定してカタログを更新するまで、実データ取得は完了とは言えない。

## 前提

- personal mode（`YOWAYOWA_MODE=personal`）。public mode では `/v1/broker-read/*` は 403 で fail-closed。
- operator-browser extra の導入: `uv sync --extra operator-browser`（playwright 同梱）。
  加えて Chromium 本体の導入が必要: `uv run playwright install chromium`
  （未導入だと fetch は `failed` outcome を返し、note に
  `BrowserType.launch_persistent_context: Executable doesn't exist` が出る）。
  未導入環境では fetch は例外で死なず `failed` outcome（`operator browser session not configured ...`）を返す。
- プロファイル保存先: 既定 `./data/broker-profiles/rakuten`（設定 `YOWAYOWA_BROKER_RAKUTEN_WEB_PROFILE_DIR`）。
  永続 Chromium プロファイルなので、初回以降はログイン状態が残る。
- 認証の自動化は**行わない**。MFA・ログインは人間がブラウザ上で正規手順により行う（本ツールは認証情報を扱わない）。

## 手順1: サーバ起動と初回 fetch（未ログインの挙動確認）

```bash
uv run uvicorn yowayowa.api.app:app
```

別ターミナルから:

```bash
uv run yowayowa broker-read fetch rakuten-web positions --market jp
```

初回はセッション起動（プロファイルなし→空のブラウザが開く場合がある）→ 未ログインなので
楽天側がログイン画面へリダイレクト/ログイン HTML を返し、outcome は
`state auth_expired` / `auth unauthenticated` / note `operator reauthentication required` となる。
**これが正常挙動**。auth 検出は login URL マーカー（login/signin/sign-in）とログイン文言（「ログイン」等）のヒューリスティック。

## 手順2: 人間が正規ログインする（自動化しない）

1. fetch により起動したブラウザ（プロファイル `./data/broker-profiles/rakuten`）で
   `https://www.rakuten-sec.co.jp/` を開く。
2. 楽天証券の正規ログイン手順（ユーザID・パスワード、必要なら MFA/デバイス承認）を人間が完了する。
   Yowayowa 側に認証情報・パスワード・ワンタイムコードを入力・保存する機能は存在しない。
3. ログイン済みの見た目（ポートフォリオ画面等）を確認したらブラウザを閉じてよい
   （セッション Cookie はプロファイルに永続化される）。

## 手順3: auth-check で authenticated を確認

```bash
uv run yowayowa broker-read auth-check rakuten-web
# -> state ok · auth authenticated
```

`auth_expired` が返り続く場合はトラブルシュート参照。

## 手順4: 各リソース取得と楽天画面との突合チェックリスト

各リソースを取得し、**楽天 Web 画面の実値と突合**する（missing data is not zero: 欠損は `null`/note で表現される）:

```bash
uv run yowayowa broker-read fetch rakuten-web account     --market jp
uv run yowayowa broker-read fetch rakuten-web positions   --market jp
uv run yowayowa broker-read fetch rakuten-web open_orders   --market jp
uv run yowayowa broker-read fetch rakuten-web order_history --market jp
uv run yowayowa broker-read fetch rakuten-web executions    --market jp
uv run yowayowa broker-read fetch rakuten-web account     --market us
uv run yowayowa broker-read fetch rakuten-web positions   --market us
uv run yowayowa broker-read fetch rakuten-web open_orders   --market us
uv run yowayowa broker-read fetch rakuten-web order_history --market us
uv run yowayowa broker-read fetch rakuten-web executions    --market us
```

チェックリスト（CLI summary + `--json` の detail で確認）:

- [ ] 残高（預り金）: `account.cash_balance` が画面の預り金と一致（JPY/USD 別）
- [ ] 買付余力: `account.buying_power` が画面と一致
- [ ] 建玉数: `positions` の行数が画面の保有銘柄数と一致
- [ ] 平均取得単価: 各 position の `average_cost` が画面と一致
- [ ] 評価損益: `unrealized_pnl`（▲は負数として正規化）が画面と一致
- [ ] 評価額・現在値: `market_value` / `market_price`
- [ ] 注文明細: `open_orders` の注文件数・銘柄・数量・状況が画面と一致
- [ ] 注文照会（履歴）と注文一覧（未約定）の区別: `order_history` は取消・失効・約定済みを
      含み、`open_orders` は未約定のみを返す（終端 status の行は
      `... status <X> is not an open order; row excluded from open_orders` の note 付きで除外）。
      両者の注文番号が混ざらないこと
- [ ] `order_history` の履歴件数が楽天の注文照会画面の件数と一致すること（取消済み注文が
      含まれること。`open_orders` と件数が違って当然）
- [ ] 約定: `executions` が約定履歴（status=FILLED, filled_quantity, average_fill_price）と一致
- [ ] 手数料: `detail.fees`（BrokerOrder に手数料フィールドは無いので detail 行き）。
      `order_history` 経由でも取得できること: 履歴payloadのリストキーが
      `orders` / `history` / `order_history` / `orderHistory` / `rows` / `list`
      のいずれでも `detail.fees` に同一内容が入る（キー名の違いで無言欠落しない）。
      認識キーが list でない形状の場合は `fees: field '...' present but not a list`
      の note が出る
- [ ] 信用 collateral / margin availability: `detail.margin_state` に
      拘束保証金・維持率・建玉明細に加え、信用新規建余力 / 信用建余力 / 信用余力 /
      保証金余裕額 / 委託保証金率 / 委託保証金維持率 / 保証金現金 /
      受入保証金合計 / 必要保証金合計 / 現物買付可能額 が入ること（JPY/USD 別。
      画面に項目が無ければキーが無いのが正しい。値があるのに解釈できない場合は note が出る）
- [ ] 銘柄名: `detail.symbol_names`（銘柄名は generic model に無いので detail 行き）。
      `order_history` 経由でも同じく `orders` / `history` / `order_history` / `orderHistory` /
      `rows` / `list` のどのリストキーでも同一内容が入る
- [ ] 通貨混在: JPY リソースと USD リソースで currency が明示分離されていること（換算は行われない）
- [ ] 注文番号欠損行: `broker_order_id=""` + note が出るだけで落ちないこと

## 手順5: catalog URL が実機と違う場合（devtools で確定→更新→verified=True）

初期仮定 URL（`src/yowayowa/operator_bridge/rakuten_web.py` の `RAKUTEN_WEB_RESOURCE_CATALOG`）が
404・リダイレクト・空 payload を返す場合:

catalog は account / positions / open_orders / order_history / executions の 5 種類を JPY・USD 別に持つ 10 エントリである。

1. 実ブラウザで楽天証券の当該画面（ホーム/口座サマリ、保有商品、注文照会、約定照会）を開く。
2. devtools の Network パネルで、その画面が実際に叩いている XHR/fetch（JSON）と HTML を特定する。
   優先順位は JSON/XHR > HTML tables。
3. `RAKUTEN_WEB_RESOURCE_CATALOG` の該当 entry の `url`・`parser_kind`（json/tables）を実 URL に合わせて更新し、
   `verified=True` に立てる。`parser_version`/`schema_version` が変わる場合は版本を上げる。
4. 正規化器（`normalize_*`）のキー名・テーブルヘッダ対応も実 payload 形状に合わせて更新し、テスト fixture を実形状に寄せる。
5. 手順4のチェックリストを再実行して全項目一致を確認。

URL の host は `RAKUTEN_ALLOWED_HOSTS`（www.rakuten-sec.co.jp）に制限されている。
許可外 host への取得は `failed`（`host not allowed: ...`）で落ちる。

## 変更経緯

- 2026-09-23: ベースURL/許可hostを、権威DNSでNXDOMAINとなった旧hostから `www.rakuten-sec.co.jp` に変更。
  旧hostはグローバル消滅（IIJ権威 SOA 付きで確認済）。
  実ログインページは `https://www.rakuten-sec.co.jp/ITS/V_ACT_Login.html`。
  カタログURL自体は実機未検証（verified=False）のまま。親カード t_97c79206 の立会い検証で対応。

## トラブルシュート

- **auth_expired が続く**: 未ログイン、または楽天が別ドメイン（例: `www.rakuten-sec.co.jp` 配下のログインURL）へ
  リダイレクトしている。ブラウザプロファイルで実際にログイン済みか目視確認。auth-check の `source_url` が
  ログイン画面 URL になっていれば検出は正常動作。ログイン後に再 fetch。
- **stale（TTL 60s 超過・max-stale 600s 以内）**: 仕様どおりの挙動。cache 由来の payload で正規化結果を返す。
  `--force-refresh` で再取得。
- **host not allowed**: catalog URL が許可 host 外（またはリダイレクト先が許可外）。devtools で実 URL を確認し
  catalog を更新。どうしても必要な host がある場合は `RAKUTEN_ALLOWED_HOSTS` を楽天正規ドメインに限定して拡張すること。
- **playwright 未導入**: `operator browser session not configured (install yowayowa-investor[operator-browser])`
  の failed outcome。`uv sync --extra operator-browser` で導入。
- **Chromium 本体が無い**: note に `BrowserType.launch_persistent_context: Executable doesn't exist ...`
  が出て failed になる。`uv run playwright install chromium` を実行する。
- **note の例外文言が長い**: ブラウザ起動系の失敗は例外メッセージをそのまま note に載せる
  （URL のクエリは除去される）。原因特定のため先頭の例外型名を確認する。
- **failed: invalid json / expected object**: JSON 想定の URL が実は HTML を返している（未ログイン画面の可能性）。
  手順2〜3を確認後、実形状に合わせ catalog の parser_kind を見直す。

## 手順6: snapshot / diff と正規化結果の確認

正規化前の生 payload（dict 全体）は outcome には含まれない。`--json` の `detail.payload_summary` が
形状サマリ（table ヘッダ・行数・キー一覧）で、値そのものは正規化済みの
`account` / `positions` / `orders` に入る。

```bash
# 正規化結果 + provenance + snapshot + diff を JSON 全体で確認
uv run yowayowa broker-read fetch rakuten-web positions --market jp --json

# snapshot 履歴（payload hash・captured_at・parser/schema 版本）
uv run yowayowa broker-read snapshots rakuten-web positions --market jp

# 直近 2 スナップショット間の field 単位差分
uv run yowayowa broker-read diff rakuten-web positions --market jp
```

snapshot は `YOWAYOWA_PRIVATE_ACQUISITION_DATA_DIR`（既定 `./data/private-acquisition`）配下の
JSONL に追記され、`<connector_id>/<resource>.jsonl` 形式で保存される。
tables 系リソース（parser_kind=tables）は内部 connector `rakuten-web-html` 側のファイルになる。
同一 payload なら diff は changed=false、変化があれば field 単位の変更が出る。

`rakuten-web` は broker-read 専用サービスに登録される connector であり、汎用の
`yowayowa private` 面（`/v1/private`）には登録されない。生 payload は broker-read の
`--json` 出力（`detail.payload_summary` と `snapshot`/`diff`/`network`）で確認する。
