# C2ペーパーフォワード運用 建玉開始日の読み替えに関する覚書 (2026-11-03)

## 1. 概要と実測の事実（原因の同定）

2026年11月2日 19:16 JST（`2026-11-02T10:16:03Z`）に先行実行された C2 ペーパー運用（週次サイクル）において、`as_of_date: 2026-11-03` の判定に基づき Alpaca Paper API に対しリバランス注文4本（SPY, IWM, JNJ, PG）が発注されました。

2026年11月3日 20:20 JST / 21:00 JST 時点における Alpaca Paper API の実測調査（GETのみ: `/v2/orders?status=all`, `/v2/account`, `/v2/positions`）により、以下の事実が確認されました：

### (1) 発注注文の最終ステータス実測結果（4/4 取得成功）
| client_order_id | Alpaca order ID | 銘柄 | 売買 | 数量 | 指値 ($) | status | filled_qty | filled_avg_price | canceled_at | expired_at | expires_at (予定) | time_in_force |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `c2_JNJ_buy_2026-11-03` | `9c196a2c-43ba-4a46-807d-32f7b8e00305` | JNJ | buy | 91 | 274.57 | **canceled** | 0 | null | `2026-11-02T11:02:08.901182Z` | **null** | `2026-11-02T21:00:00Z` | day |
| `c2_PG_buy_2026-11-03` | `c8255837-13f7-468a-84eb-82a7dc216ba6` | PG | buy | 154 | 162.38 | **canceled** | 0 | null | `2026-11-02T11:02:08.913876Z` | **null** | `2026-11-02T21:00:00Z` | day |
| `c2_SPY_buy_2026-11-03` | `cbb32a78-08d4-4279-a85e-d3d1ad3c5acc` | SPY | buy | 33 | 759.50 | **canceled** | 0 | null | `2026-11-02T11:02:08.921006Z` | **null** | `2026-11-02T21:00:00Z` | day |
| `c2_IWM_buy_2026-11-03` | `a5a3ecb7-fab3-47f1-92ca-10d03978751f` | IWM | buy | 89 | 280.73 | **canceled** | 0 | null | `2026-11-02T11:02:08.910150Z` | **null** | `2026-11-02T21:00:00Z` | day |

### (2) 直接原因の同定（失効ではなく明示全取消）
- **実測結果**: 4本すべて `expired_at = null` かつ `canceled_at = 2026-11-02T11:02:08Z`。
- **直接原因**: 失効予定時刻（`2026-11-02T21:00:00Z` = 11-02 16:00 ET / 11-03 06:00 JST）の約10時間前（11:02:08 UTC = 20:02 JST）に、前カード `t_6a99c045` の完了検証によって `cancel_all_orders()` による明示全取消が実行されていました。
- したがって、11-03 セッションで建玉が 0 になった直接原因は「**前カード検証時の明示全取消**」であり、TIF=day による自然失効ではありません（失効は発生していません）。

### (3) 口座建玉・残高実測値（Paper API）
- **保有銘柄数**: 0 銘柄 (`positions = []`)
- **保有数量**: 0 株
- **口座資産 (Account Equity)**: `$100,000.00` USD（Cash: `$100,000.00` USD, Buying Power: `$400,000.00` USD）
- **新規発注数**: 0 件（status=all の全注文は上記4件のみ）

---

## 2. コード上の `as_of_date` → `expires_at` / TIF 決定則（行番号つき1行）

> `c2_runner.py:326-334/331`（現行コードでは先頭ガード追加により `335-343/340`、修正前は `time_in_force` 未指定のまま `submit_paper_order` を呼出）→ `alpaca_paper_client.py:209` の既定 `"day"` → Alpaca Paper API が発注送信日（11-02）当日セッション引け時刻 `2026-11-02T21:00:00Z` を `expires_at` に割当。

---

## 3. 修正の向きと設計判断（発注タイミングの1点への絞り込み）

### GTC（Good 'Til Canceled）採用の欠陥
当初検討された `time_in_force="gtc"` は以下の重大な副作用を持ちます：
1. 発注時刻が `2026-11-02T10:16Z`（= 11-02 05:16 ET、前営業日プレマーケット）の場合、GTC にすると 11-02 09:30 ET（前営業日の寄り付き）から約定可能になってしまい、本来の判定日である `as_of_date: 2026-11-03` より **1セッション早く約定し得る**（起算前混入）。
2. 未約定のまま放置された場合、無期限に注文が残り、翌週 11-09 22:00 JST の新規注文と **二重建玉** になるリスクがある。
3. `cancel_all_orders()` が呼ばれた場合は GTC であっても取り消されるため、前カードで観測された失敗（明示取消）を防ぐことはできない。

### 採用した最小修正: 発注タイミングの厳格化と判定未消費スキップ
カードが許容した2案（TIF／発注タイミング）のうち、「**発注タイミング**」の1点に絞り込みました：

- **方針**:
  - `scripts/paper_c2/c2_runner.py` において、先行実行タイミングガード（`current_utc_date < as_of_date`）を**冪等判定（ステップ2）より前（ステップ1b）に配置**。
  - 先行実行時（`current_utc_date < as_of_date`）は、判定レコード（`decisions.jsonl` 2行）の追記や `state["last_decision_date"]` の更新を一切行わず、`status="skipped_prior_to_decision_date"` で早期 return する。
  - **スキップは判定を消費しない**: 先行実行が判定を消費せず状態を汚さないため、判定日当日の正規実行時（`current_utc_date == as_of_date`）に冪等判定（`last_decision_date == seq_date`）に阻まれることなく、`force=False` で確実に判定・発注が実行される。
  - TIF は安全な既定値 `"day"` を維持する。
- **実効性の担保**:
  - 週次リバランスの運用仕様（cron: `0 22 * * 1` = 毎週月曜 22:00 JST = 13:00 UTC = 09:00 EDT/08:00 EST）では、月曜セッション寄り付き直前（プレマーケット）に実行されるため、`current_utc_date == as_of_date`（月曜当日）となる。
  - これにより、注文は **decision日セッション直前に出され、同日セッション（09:30〜16:00 ET）でのみ有効** となり、セッション引けで自動失効するため、前セッションでの誤約定も翌週への注文残存（二重建玉）も完全に防止される。

---

## 4. 再現テストと実行出力（修正前FAIL / 修正後PASS）

新規テストファイル: `tests/test_c2_order_timing.py`
- `test_weekly_cycle_skips_broker_submission_when_prior_to_decision_date`: 先行実行時（`current_utc_date < as_of_date`）に `status="skipped_prior_to_decision_date"` で早期終了し、判定・状態を書き込まずスキップすることを検証。
- `test_weekly_cycle_submits_day_orders_on_decision_date`: 当日セッション直前実行（`current_utc_date == as_of_date`）時に `time_in_force="day"` で発注されることを検証。
- `test_prior_execution_skipped_allows_full_execution_on_decision_date_without_force`: 先行実行（11-02）で判定を消費せずスキップした後、判定日当日（11-03）の実行において `force=False` で4本発注されることを検証。

### (1) 修正前 pytest 実行出力（FAIL）
```text
$ .venv/bin/pytest tests/test_c2_order_timing.py
============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-8.3.4, pluggy-1.5.0
rootdir: /root/current-work/yowayowa-investor
configfile: pyproject.toml
collected 1 item

tests/test_c2_order_timing.py F                                          [100%]

=================================== FAILURES ===================================
____ test_weekly_cycle_skips_broker_submission_when_prior_to_decision_date _____

tmp_path = PosixPath('/tmp/pytest-of-root/pytest-70/test_weekly_cycle_skips_broker0')
monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x7cc4f3d7faa0>

    def test_weekly_cycle_skips_broker_submission_when_prior_to_decision_date(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ...
        res = c2_runner.run_weekly_cycle(
            as_of_date=date(2026, 11, 3),
            dry_run=False,
            force=True,
            force_rule_only=True,
            ledger_path=test_ledger_file,
        )
    
        assert res["status"] == "success"
        # When current date (2026-11-02) is before as_of_date (2026-11-03),
        # broker submission must be skipped to prevent premature execution in prior session.
>       assert len(submitted_orders) == 0, (
            f"Expected 0 orders submitted when executing prior to as_of_date, but got {len(submitted_orders)}"
        )
E       AssertionError: Expected 0 orders submitted when executing prior to as_of_date, but got 4
E       assert 4 == 0
E        +  where 4 = len([{'client_order_id': 'c2_SPY_buy_2026-11-03', 'limit_price': 755.86, 'order_type': 'limit', 'qty': 33, ...}, {'client_...: 92, ...}, {'client_order_id': 'c2_PG_buy_2026-11-03', 'limit_price': 164.38, 'order_type': 'limit', 'qty': 152, ...}])

tests/test_c2_order_timing.py:124: AssertionError
=========================== short test summary info ============================
FAILED tests/test_c2_order_timing.py::test_weekly_cycle_skips_broker_submission_when_prior_to_decision_date
1 failed in 0.89s
```

### (2) 修正後 pytest 実行出力（PASS・全3テスト）
```text
$ .venv/bin/pytest tests/test_c2_order_timing.py -v
============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-8.4.2, pluggy-1.6.0
rootdir: /root/current-work/yowayowa-investor
configfile: pyproject.toml
plugins: base-url-2.1.0, playwright-0.9.0, cov-6.3.0, anyio-4.15.1, httpx-0.35.0
collected 3 items

tests/test_c2_order_timing.py::test_weekly_cycle_skips_broker_submission_when_prior_to_decision_date PASSED [ 33%]
tests/test_c2_order_timing.py::test_weekly_cycle_submits_day_orders_on_decision_date PASSED [ 66%]
tests/test_c2_order_timing.py::test_prior_execution_skipped_allows_full_execution_on_decision_date_without_force PASSED [100%]

============================== 3 passed in 1.14s ===============================
```

### (3) C2 全体テストスイート実行結果（全28件 PASS）
```text
$ .venv/bin/pytest tests/test_c2_*.py
============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-8.4.2, pluggy-1.6.0
rootdir: /root/current-work/yowayowa-investor
configfile: pyproject.toml
plugins: base-url-2.1.0, playwright-0.9.0, cov-6.3.0, anyio-4.15.1, httpx-0.35.0
collected 28 items

tests/test_c2_attribution.py ........                                    [ 28%]
tests/test_c2_daily_cycle.py .......                                     [ 53%]
tests/test_c2_kill_switch.py ....                                        [ 67%]
tests/test_c2_ledger.py ...                                              [ 78%]
tests/test_c2_order_timing.py ...                                        [ 89%]
tests/test_c2_portfolio_risk.py ...                                      [100%]

============================== 28 passed in 2.91s ==============================
```

---

## 5. 60営業日ゲート判定の読み替えルール

過去データの捏造・埋め合わせ・遡及的再発注は行わず、実測の事実に基づき、2027年2月1日に予定されている 60営業日ゲート判定（`docs/G2_PAPER_PHASE_DESIGN_20261102.md` §6.1）の集計ルールを以下の通り確定します：

1. **実質的な前向き運用の起点**:
   2026-11-03 セッションは建玉 0（現金 100%）となったため、前向き運用の有効なサンプル起点を当初の 2026-11-03 から「**初めて建玉が成立した日（First Filled Position Date）**」に読み替えて集計する。
2. **不変の条件**:
   - ゲート判定日: **2027-02-01**（変更なし）
   - 7大判定基準（Sharpe >= 0.50, MaxDD >= -15%, PF >= 1.05, LLM Attribution >= 0 等）の閾値・事前登録条件は一切変更しない。
