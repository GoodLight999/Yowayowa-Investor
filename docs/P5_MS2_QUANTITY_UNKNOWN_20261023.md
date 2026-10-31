# P5 MS2 RSS — 数量の解析失敗を 0 ではなく「不明」として区別する

- カード: t_55dbd444（worker-goat）
- 出典: `docs/SILENT_FAILURE_AUDIT_20261023.md` §3 #8（影響「中」）
- 実施日: 2026-10-23 (JST)
- 対象: MARKET SPEED II RSS の読み取り専用 inquiry（`providers/rakuten_ms2_rss.py`）
- 制約の遵守: 実口座・本番注文・送金・公開 push・外部送信・課金はいずれも無し。内蔵 LLM は呼んでいない。MS2 RSS は既存の読み取り bridge の範囲のみ（発注経路は追加していない）。

## 結論

`_to_int` が解析不能値を `0` で返していたため、注文数量・約定数量・保有数量が「0」として下流（operator bridge の `/v1/brokers/rakuten/orders`、`/positions`）に出ていた。修正後は「不明」= `None`（JSON では `null`）として出る。**0 は実在する値（約定数量 0）として温存**し、不明と 0 を混同しない。

実測（同一のワークシート表を修正前モジュール＝git HEAD と修正後モジュールに与えた比較。ネットワーク・実 Excel なし）:

```
--- before (git HEAD)
order 123456 quantity= 0 filled= 40 status= unknown          # 注文数量 "1,000株"
order 123457 quantity= 100 filled= 40 status= partially_filled
order 123458 quantity= 0 filled= 0 status= pending           # 注文数量 0（実データ）
order 123459 quantity= 1000 filled= 40 status= partially_filled
position 4755 quantity= Decimal('0')                          # 保有数量 "1,000株"
position 6758 quantity= Decimal('0')                          # 保有数量 ""（空）
position 9984 quantity= Decimal('-100')
--- after (working tree)
order 123456 quantity= None filled= 40 status= unknown
order 123457 quantity= 100 filled= 40 status= partially_filled
order 123458 quantity= None filled= 0 status= pending
order 123459 quantity= 1000 filled= 40 status= partially_filled
position 4755 quantity= None
position 6758 quantity= None
position 9984 quantity= Decimal('-100')
```

差分は「解析不能だった行」だけで、解析できていた行（`1,000`、全角 `１００`、負値 `-100`）は値も状態も変わらない。

再現スクリプト: `/root/.hermes/profiles/worker-goat/cache/scratch/ms2_before_after.py`（読取専用。git HEAD のソースを `git show` で読み、実行時のみメモリ上で旧モジュールとして評価する）。

## 原因

`_to_int` の `except ValueError: return 0` と、`None`/bool を `0` に落とす分岐。呼び出し側（`list_orders` の `quantity`/`filled_quantity`、`list_positions` の `保有数量`）はその戻り値をそのまま `BrokerOrder`/`BrokerPosition` に入れていた。

なお監査 §3 #8 の入力例のうち **全角数字は実測では解析成功していた**（`int("１００") == 100`）。つまり実際の欠落は「空文字・単位付き（`1,000株`）・小数・非数値」であり、全角は「正しく読めていた」。この事実は監査の例示と食い違うため、本修正では全角を**情報を落とさず**読む側に倒した（下記）。

## 修正

### `src/yowayowa/providers/rakuten_ms2_rss.py`

- `_to_int(value) -> int | None`: 解析不能は `None`。`bool`/`None`/空文字/空白/単位付き（`1,000株`）/小数（`100.5`、`100.5` の float）/アンダースコア区切りは不明。`int()` が全角数字や `_` を受理してしまうため、NFKC 正規化後に「ASCII 数字のみ」を要求する。NFKC は全角数字・全角カンマを半角へ落とす（同じ数を表すため情報を捨てない）。既存の同種実装（`services/edinet.py:219`）と同じ手段。
- `_order_quantity(value) -> int | None`: 注文数量は正の整数のみ採用。`0` と負値は「注文数量として成立しない」ため不明（0 を数量として通すと、まさに今回の混同が再発するため）。
- `_filled_quantity(value) -> int | None`: 約定数量は `0` を実値として保持し、解析不能・負値のみ不明。
- `list_orders`: 数量不明でも行自体は返し、`quantity=None`（行を黙って落とさない）。`parse_order_text_status` には `None` を渡す。
- `parse_order_text_status(filled_quantity: int | None, quantity: int | None)`: 部分約定判定は両方の数値が揃っているときだけ行う。数値不明のまま `出来有` を `partially_filled` と断定しない（`unknown` に留める＝推測しない）。
- `list_positions`: `保有数量` 不明は `quantity=None`。負値（信用の売建等）は実値として保持。→ 検算: `Decimal(-100)` は修正前後で同一。
- `order_id_records`: `_to_int` の型変更に伴い `None` を明示的に弾く（従来は 0 以下を弾いていた。挙動は同じ）。

### `src/yowayowa/broker_models.py`（契約の拡張。破壊ではない）

- `BrokerOrder.quantity: int | None`（必須のまま。既定値は付けない）、`BrokerOrder.filled_quantity: int | None = 0`。
- `BrokerPosition.quantity: Decimal | None`（必須のまま）。
- 既存の欠損表現に合わせた拡張で、`BrokerExecution` の docstring「absent fields remain absent, never zero-filled」と同じ原則。必須キーは落としていない（試験で `ValidationError` を確認）。既存の生成側（`operator_bridge/rakuten_web.py`、`services/order_inquiry_service.py`）は常に実値を渡すため挙動不変。

### `src/yowayowa/broker_read_cli.py`

broker-read CLI の表で `None` が文字列 `"None"` と出ないよう、数量セルだけ `—` 表示にした（`_cell` ヘルパ1つ）。値の解釈は変えていない。

## 影響範囲（provider の外に広がった点）

| ファイル | 変更 | 理由 |
|---|---|---|
| `src/yowayowa/providers/rakuten_ms2_rss.py` | 本体 | 解析不能を `None` に |
| `src/yowayowa/broker_models.py` | `BrokerOrder.quantity`/`filled_quantity`、`BrokerPosition.quantity` を Optional 化 | 「不明」を運ぶ器が無かったため。既存の欠損原則に合わせた拡張 |
| `src/yowayowa/broker_read_cli.py` | 表示のみ | `None` を `—` に |
| `tests/test_rakuten_gaps.py` | 旧挙動を固定していた1テストを新契約へ更新 | `test_to_int_value_shapes` が `_to_int(None) == 0` を要求していた（＝バグの固定） |
| `tests/test_rakuten_ms2_rss_quantity.py` | 新規34件 | 境界・下流・契約 |
| `docs/P5_MS2_QUANTITY_UNKNOWN_20261023.md` ほか | 文書 | 本書 |

触っていない: `services/executions_reconciliation.py`（他カードの作業と衝突しないよう未変更。数量不明の扱いは既存実装で既に「incomplete＋blocker」であり、試験で区別を確認したのみ）、`operator_bridge/rakuten_web.py`（既に `None`/行スキップ＋note 方式で正しい）。

`git status` 実測（着手時）: 上記4ファイルはいずれも未変更（`rakuten_ms2_rss.py` は親カードの記載どおり差分なし、`broker_models.py`・`broker_read_cli.py`・`tests/test_rakuten_gaps.py` も同様）。着手時に `hotspot` 申告が必要な衝突は無かった。

## 試験

新規 `tests/test_rakuten_ms2_rss_quantity.py`（34件・rc=0）:

- 解析境界: 正常整数 / カンマ付き / 全角（`１００`→100・`１，０００`→1000）/ 空・空白 / `1,000株`→不明 / 負値の扱い（注文数量は不明・保有数量は保持）/ `100.5`・`1_000`・`N/A`→不明 / bool→不明。
- 注文: 数量不明でも行は返る（`quantity is None`、`!= 0`）/ 約定数量 0 は 0 のまま / 数量不明では `partially_filled` を推測しない / 数値が揃えば従来どおり `partially_filled`。
- 保有: 不明は `None`（`Decimal(0)` ではない）/ 負値は保持。
- 契約: `quantity` 不明が JSON で `null`（`0` ではない）/ 必須キー欠落は `ValidationError`（キーを落としていない）/ connector 経由で `None` がそのまま通る。
- 下流 API: `create_operator_bridge_app` を実際に組み立て、`GET /v1/brokers/rakuten/orders` と `/positions` が 200 かつ `quantity: null` を返すことを実測。
- 執行照合: `BrokerExecution.quantity=None` は `quantity missing; value remains missing` で `incomplete`・`changes=[]`・`can_apply=False`、`Decimal(0)` は `quantity must be positive; value is not coerced` と**別の理由**で弾かれる（0 と不明が混同されない）。

関連回帰: broker/order/broker-read/execution/operator_bridge/rakuten 系 17 ファイル **332件 passed / rc=0**（新規34件を含む）。

リポジトリ全体（他カードが作業中の2ファイル `tests/test_defensive_rotation.py`・`tests/test_jp_value_quality_backtest_units.py` を除く）: **2,766件中 2,757 passed / 2 failed / 2 skipped / 5 xfailed**。失敗2件は `tests/test_crypto.py::test_binance_klines_returns_records_with_daily_provenance` と `::test_binance_zero_bar_is_skipped`（zero-bar の期待件数 6 に対し実測 4）で、**本カードの変更とは無関係**であることを実測で確認した: 当該テストファイルを import しても本カードの変更モジュール（`broker_models`・`providers.rakuten_ms2_rss`・`broker_read_cli`）は 1 つもロードされず、crypto 系ソースは working tree で未変更（＝HEAD でも同じ結果）。除外した2ファイルの失敗も同様に symbol/strategy の golden hash・テストコード側の `TypeError` で、本カードの変更モジュールをロードしないことを同様に実測済み。

静的検査: 変更ファイルの `ruff check` / `ruff format --check` は rc=0。`mypy src/yowayowa` は **31 errors / 5 files**（`paperloop*`・`jp_value_quality*`）で、これは本カード着手前の既知ベースライン（`refill_queue` の yw-p5-mypy-existing-debt と同数）と一致し、増えていない。リポジトリ全体の `ruff check src tests` は 36 件出るが、いずれも他カードが作業中の未コミットファイル由来で、本カードの変更ファイルは 0 件。

## 残る穴・限界

- 本番/実 Excel（MARKET SPEED II RSS）での実測はしていない。RSS ワークシートは Linux 上に無いため、表データは偽 reader で与えている（NEED-HUMAN: Windows 実機での確認）。ただし修正は「セル値 → 数量」の純関数部分に閉じており、通信・認証は変わっていない。
- `注文数量 0` を「不明」に倒す判断は仕様変更である（0 株の注文は成立しないため）。もし 0 を正当値として出すブローカー仕様が later 判明したら、`_order_quantity` の1行で戻せる。
- 発注 ID（`発注ID`）が解析不能な行は従来どおりスキップのまま。数量と違い注文番号の突合にしか使わないため本次対象外（必要なら別カード）。
- 監査 §3 #8 の記述「全角数字は解析失敗」は実測と食い違う（実際は成功していた）。監査文書自体は本カードでは書き換えていない。
- `filled_quantity` の既定値 `0` は据え置いた（既存生成側の意味を変えないため）。「不明を明示したい生成側」は `None` を渡す契約とした。
