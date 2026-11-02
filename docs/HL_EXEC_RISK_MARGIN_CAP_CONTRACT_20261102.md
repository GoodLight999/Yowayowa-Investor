# 隔離証拠金の比率上限（10%）適用契約 — 内部適用に確定

- 日付: 2026-11-02
- 裁定: audit-glm-heavy（監査役・カード t_e91f2dbc）
- 実装: 子カード t_dad0e051（worker-agy）が本書のとおり行う。本書と子カード本文が食い違う場合は本書を優先する。
- 対象: `src/yowayowa/hl_exec/risk.py` の `check_isolated_margin` と `evaluate_entry_gates`
- 出典の設計書: `docs/CIO_HL_EXECUTION_TRACK_PLAN.md` §2.4「1建玉あたりの隔離証拠金: 20.0 USDC（口座資金の 10.0%）」

## 決定

5つの論点はすべて下記のとおり1つに確定する。未解決点はなし。

| 論点 | 決定 |
|---|---|
| 1. (a) 内部適用 vs (b) 呼出側責務 | **(a) 内部適用** |
| 2. 比率の基準残高 | **当日開始残高**（その UTC 日に最初に取得した `clearinghouseState` の `accountValue`） |
| 3. 浮動小数の丸め | 実効 cap を**1セント（0.01 USDC）単位で切り下げ**。margin 側は丸めない |
| 4. `cap` 引数の後方互換 | **廃止**。`equity_usdc` は必須キーワード専用引数（デフォルトなし） |
| 5. 第3段の呼出側責務 | 当日開始残高を `equity_usdc` として渡す。取れないなら新規エントリーしない |

## 裁定の根拠となった実測（2026-11-02、本カードで私自身が読み取った値）

1. `check_isolated_margin(margin_usdc, *, cap=ISOLATED_MARGIN_PER_POSITION_CAP_USDC)` は絶対額 20.0 USDC のみを見る（`src/yowayowa/hl_exec/risk.py` 58行目から）。
2. `ISOLATED_MARGIN_FRACTION_OF_EQUITY = 0.10` は `src/` 内で参照ゼロ。grep の実測では定義（risk.py 25行目）だけで、他の参照は `scripts/hl_exec_phase2/phase2_read_probe.py` の2件（呼出側スクリプト）のみ。
3. 集約ゲート `evaluate_entry_gates`（risk.py 219行目から）は `check_isolated_margin(isolated_margin_usdc)` を cap 未指定で呼ぶため、比率を通す口が現状のままでは存在しない。
4. リポジトリ内の呼出側はテスト2件と第2段プローブのみで、エントリー経路（第3段）はまだない（grep 実測）。
5. テストネット実測（`docs/evidence/hl_exec_phase2_20261102/summary.json`）: 実残高 100.0 USDC の口座で `check_isolated_margin(20.0).ok == True`、すなわち実残高の 20% が通る（`at_cap_share_of_real_equity: 0.2`）。プローブが呼出側で `min(20.0, 0.10×実残高) = 10.0` を適用したときの設計上の cap は `design_margin_cap_usdc: 10.0`。署名・発注・鍵読み取りは0回。

## 理由

1. **(b) には fail-open が残る。** (b) ではライブラリの既定の経路が絶対額 20.0 のままなので、cap を渡し忘れた呼出側はそのまま 20% まで通す。第3段以降のすべての呼出側が規約を覚えていることが前提になる。実残高 100 USDC の口座では許容の2倍が通る。
2. **(b) は集約ゲートを救えない。** `evaluate_entry_gates` には cap を渡す引数が無い。API を変えない限り、第3段の正規の入口である集約ゲートは構造的に比率なしのまま残る。(b) を選ぶと、集約ゲートを避けて個別ゲートを呼ぶような実装を誘導する。
3. **(a) は機械的に fail-closed になる。** `equity_usdc` を必須引数にすれば、渡し忘れは TypeError で落ちる。実行時に黙って絶対額で通る経路が1つも残らない。死んでいた `ISOLATED_MARGIN_FRACTION_OF_EQUITY` に実参照が付く。
4. **モジュールの既存の形と同じになる。** 停止系の `daily_loss_stop_hit` は `day_start_equity` を受け取り、内部で「絶対額 4.0 と 2%×残高のうち早く発動する側」を取る。上限系の隔離証拠金も同じ形（equity を受けて内部で min を取る）に揃う。

## API 仕様

### 新しいシグネチャ（第3段実装はここに一字一句合わせること）

```python
def check_isolated_margin(
    margin_usdc: float,
    *,
    equity_usdc: float,   # 必須・キーワード専用・デフォルトなし
) -> RiskCheck: ...

def evaluate_entry_gates(
    *,
    isolated_margin_usdc: float,
    open_position_count: int,
    notional_usdc: float,
    equity_usdc: float,   # 必須・キーワード専用・デフォルトなし
) -> tuple[RiskCheck, ...]: ...
```

### 実効 cap の計算式

```python
effective_cap = math.floor(min(ISOLATED_MARGIN_PER_POSITION_CAP_USDC,
                               ISOLATED_MARGIN_FRACTION_OF_EQUITY * equity_usdc) * 100.0) / 100.0
```

- 判定は従来の境界規約どおり `margin_usdc <= effective_cap` で許可（上限ちょうどは許可、超えたら拒否）。
- `RiskCheck.limit` には丸め後の実効 cap を入れる。`rule` は `isolated_margin_per_position` のまま変えない。

### 入力検証（計算の前に必ず行う）

- `margin_usdc <= 0` は従来どおり `RiskInputError`。
- `equity_usdc` は**有限かつ正**であること（`math.isfinite(equity_usdc) and equity_usdc > 0`）。違反は `RiskInputError`。
- この検証を省けない実測根拠: Python では `min(20.0, float("nan"))` が黙って `20.0` を返す（2026-11-02 実測）。NaN の equity は絶対額だけの cap に化けて fail-open になる。また `float("inf")` も `min` を通ると絶対額 20.0 で通ってしまう。検証は `min` の前に置くこと。

### 変更しないもの

- 数値定数は一切変えない（20.0 / 60.0 / 10.0 / 3.0 / 2% / 4% / 5.0 / 15% / 200.0）。
- 他のゲートと停止系（`check_leverage`、`margin_preflight_ok`、`daily_loss_stop_hit`、`drawdown_stop_hit`）の意味と既定値は本契約では変えない。`daily_loss_stop_hit` の `day_start_equity` 既定値 200.0 もそのままでよい（呼出側は本契約により明示的に渡す）。

## 基準残高（equity baseline）の定義

- **当日開始残高**: その UTC 日に最初に取得した `clearinghouseState` の `marginSummary.accountValue`。API は文字列で返すので `float` へ変換してから渡す。
- **管理は1つにまとめる。** 日次損失停止の `day_start_equity` と同じ値を1つだけ保持し、`daily_loss_stop_hit` と `equity_usdc` の両方へ渡す。日替わりの基準が2つできると、停止と上限で当日の資金前提が食い違う。
- **取得と更新。** 起動時に1回取得して（UTC日付, 値）を保持する。UTC の日付が変わったら再取得して翌日の値に差し替える。設計書 §2.5 の日次サマリ（00:05 UTC）の後での更新でよい。
- **再起動したとき。** その日の最初の取得が起動直後になる。当日すでに損益が出ていれば開始残高の近似になるが、この運用で固定する（00:00 丁度の値を再構成する仕組みは作らない）。
- **取得できないとき。** 新規エントリーしない（fail-closed）。絶対額 20.0 へのフォールバックは禁止。
- **現在値とピークを採らない理由。** 現在の accountValue は含み損益で日中に揺れ、境界が動いて同じ入力でも判定が変わる。また利益が出た後に建てると、その日の資金前提の 10% を超えてしまう。ピークはDD停止のための概念で、建玉に割り当てる証拠金の基準にする根拠が設計に無い。

## 丸め規約

- 実効 cap は1セント単位で**切り下げ**（上の式どおり `math.floor(x * 100.0) / 100.0`）。margin 側の値は丸めず、呼出側が渡したまま cap と比較する。
- **方向の安全性は全数検査で確認済み**（2026-11-02 実測）: E = 0.01 から 200.00 までのセント刻み 20000 点で、`floor` 付きの計算が真値（Decimal による切り下げ）より**大きくなる例は 0 件**、小さくなる例は 56 件。崩れるときは必ず拒否側（cap が1セント下がる）で、許可側に崩れることはない。
- **カードにあった前提の訂正。** この処理系（Python 3.14）では `0.1 * 100` はちょうど `10.0` になる（実測）。`10.000000000000002` という例は再現しない。ただし誤差そのものは実在する（`0.1 * 3 == 0.30000000000000004`、E = 0.70 では生積が `0.06999999999999999` になる）。だから丸め規約は引き続き必要で、境界表の数値はこの規約で固定する。
- cap 未満でもグリッド上に無い margin（例: equity=100 での 10.005）は `10.005 > 10.00` で拒否される。判定は常に `margin <= effective_cap` の1本で表せる。

## 境界表（実測・判定は `margin <= effective_cap`）

| 当日開始残高 E | 比率側 floor(0.10×E×100)/100 | 実効 cap = min(20.0, 比率側) | 効く側 | 許可される最大 margin | その次の値（拒否） |
|---|---|---|---|---|---|
| 50.00 | 5.00 | 5.00 | 比率側 | **5.00（許可）** | 5.01（拒否） |
| 100.00 | 10.00 | 10.00 | 比率側 | **10.00（許可）** | 10.01（拒否） |
| 150.00 | 15.00 | 15.00 | 比率側 | **15.00（許可）** | 15.01（拒否） |
| 200.00 | 20.00 | 20.00 | 両方同値 | **20.00（許可）** | 20.01（拒否） |
| 300.00 | 30.00 | 20.00 | 絶対側 | **20.00（許可）** | 20.01（拒否） |

- 200.00 は設計書 §2.4 の想定口座で、この水準では絶対側と比率側が同値になる。既存テストの「20.0 許可 / 20.0001 拒否」境界は、equity=200.00 の前提に置き換えると意味が保存される。
- 検算の出所: 2026-11-02 に `math.floor(min(20.0, 0.10*E)*100.0)/100.0` と生の `<=` 比較で各水準の許可・拒否を確認した。表の数値はこの実行の出力どおりである。

## 呼出側の責務（第3段の1行契約）

**エントリー経路は、当日開始残高（その UTC 日に最初に取得した `clearinghouseState` の `accountValue`）を `equity_usdc` として `evaluate_entry_gates` と `check_isolated_margin` に必ず渡す。値を取得できないときは新規エントリーしない。**

補足:

- `cap` 引数は廃止されるので、呼出側で cap を計算してはならない。第2段プローブ（`phase2_read_probe.py`）が呼出側で cap を計算していたのは検証時の暫定措置であり、本契約により内部適用が正となる。API が変わるのでプローブは最小限の追従をする（子カードの指示どおり）。
- 第3段で境界テストを書くときの固定値: equity=100.00 で 10.00 許可 / 10.01 拒否、equity=50.00 で 5.00 許可 / 5.01 拒否。小さい equity で比率側が効くことを1本は必ず含める。

## 後方互換

- `cap` キーワード引数は**廃止**する。リポジトリ内に `cap=` を明示指定している呼出側は grep で 0 件（テストは位置引数のみ）なので、廃止で壊れる呼出側はない。絶対上限を変えたくなった場合は、定数の変更提案として別カードで扱う（本契約の禁止事項どおり数値は変えない）。
- `equity_usdc` にデフォルト値は持たせない。未指定の呼び出しは TypeError で落ちる。「equity が無いときは絶対額 20.0 で通す」という既定経路を残さないためである（fail-closed）。
- `evaluate_entry_gates` の呼出側（テスト2件とプローブ）は新しいシグネチャへ追従する。これは子カード t_dad0e051 の範囲で行う。

## 未解決点

なし。本書の日付時点で、論点1から5まで全て上記のとおり確定している。
