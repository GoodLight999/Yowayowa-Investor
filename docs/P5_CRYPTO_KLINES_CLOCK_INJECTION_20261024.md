# P5: Binance klines テストの恒久FAIL解消 — 時計注入による決定論化

- カード: t_4e1246a5（出典: CTO t_56ca06cb 実測 2026-10-24）
- 実施日: 2026-10-24（JST）/ host: ubuntu（VPS）/ branch: agent/commercial-foundation（作業ツリーのみ・commit/push なし）
- 対象ファイル: `src/yowayowa/providers/binance.py`（作業ツリーで未変更だった競合ファイル）、`tests/test_crypto.py`（既存未コミット差分あり）
- 結論: 失敗2件 → 0件。`pytest tests/test_crypto.py` は **44 passed / rc=0**（同一実行2回で同一結果）。

## 1. 根因（カード記載のとおり・実測で再現）

- `tests/test_crypto.py::_days()` は固定日付（2026-09-20 起）で fixture を作る。
- `src/yowayowa/providers/binance.py::_fetch` は保存前に実時計の窓
  （`end = datetime.now(UTC).date() - 1d`、`start = end - (days-1)d`）で
  `cursor_ms <= point_ms < end_ms and start <= point.as_of.date() <= end` と絞る。
- より days=30 の窓が暦とともに前進し、固定 fixture の行が窓外へ落ちる片道失敗。

修正前の実測（本作業のベースライン・2026-10-24 12:01 UTC / host ubuntu）:

```
$ .venv/bin/python -m pytest tests/test_crypto.py -q --tb=line
tests/test_crypto.py:223: AssertionError: assert 3 == 7
tests/test_crypto.py:278: AssertionError: assert 3 == 6
FAILED tests/test_crypto.py::test_binance_klines_returns_records_with_daily_provenance
FAILED tests/test_crypto.py::test_binance_zero_bar_is_skipped
```

CTO 実測（assert 3 == 7 / 3 == 6）と同一の2件。この時点の窓は [2026-09-24 .. 2026-10-23] で、
fixture（完了バー 09-20..09-25 ＋形成中 09-26）のうち 09-24..09-26 の3件しか窓内に残っていない。

## 2. 修正設計（カード第一候補「現在時刻を差し替え可能にして窓境界を固定する」）

P4 家系の既存の正しい形 `src/yowayowa/providers/alpaca.py:79` と同一流儀の clock seam を導入:

- product: `_utc_now()` という module 関数に時計を集約（本番は `datetime.now(UTC)`、挙動不変）。
  `_fetch` の窓計算だけがこれを読む。`retrieved_at`（取得 provenance）は実時計のまま。
- test: `monkeypatch.setattr(binance, "_utc_now", lambda: fixed_now)` で偽現在時刻を注入。
- fixture は**注入した偽時刻にアンカー**する（実日付に追従しない）。`_binance_payload(now=...)`
  は「`now` の前日までの完了バー6本 ＋ `now` 当日の形成中バー」を生成する。テストが読む時計は
  注入されたものだけなので、窓と fixture の関係は常に一定に保たれる。
  デフォルト値 `2026-09-26 12:00 UTC` は従来の固定 fixture 日付（09-20..09-25 完了＋09-26 形成中）と
  完全に同一の列を生成する。
- 対症療法（fixture を実日付に追従させる）との違い: 実時計はテスト内に一切現れない。
  「今日」ケースも実現在時刻をテスト開始時に1回凍結した固定値であり、以後は固定時計として扱う。

### 期待値の修正について（弱めではなく訂正）

- 旧 `assert len(records) == 7` は、実時計の窓が偶々 fixture 全件（形成中バー 09-26 を含む）を
  飲み込んでいた期間にだけ成立する値だった。product の意味（形成中バーは落とす・窓は昨日UTC終値まで）では
  完了バーは6本なので正しい期待値は 6（zero bar 試験は 5）。旧値はカードが禁じる「偶然の緑」の産物。
- 訂正と引き換えに assert を強化: 完了バーの先頭/末尾 as_of を注入時計から厳密に assert、
  zero 試験は zero 日の欠落を明示 assert、さらに窓境界そのものを1件で決定する新規テストを追加（下記）。
- 値レベルの既存 assert（provider/currency/volume/quote_volume/license_class/シンボル対応）は全部維持。
- xfail/skip は追加していない。

## 3. 修正diff

### 3.1 src/yowayowa/providers/binance.py（product・clock seam のみ）

```diff
--- a/src/yowayowa/providers/binance.py
+++ b/src/yowayowa/providers/binance.py
@@ -44,6 +44,12 @@ class BinanceTransportError(RuntimeError):
     """Binance is unreachable (maps to HTTP 502)."""


+def _utc_now() -> datetime:
+    """Clock seam: tests inject a fixed instant; production always uses UTC now."""
+
+    return datetime.now(UTC)
+
+
 class BinanceKlinesProvider:
@@ -102,7 +108,7 @@ class BinanceKlinesProvider:
         cached = self._history_cache.get(cache_key)
         if cached is not None:
             return cached
-        end = datetime.now(UTC).date() - timedelta(days=1)
+        end = _utc_now().date() - timedelta(days=1)
         start = end - timedelta(days=days - 1)
```

product の意味は一切弱めていない: 窓は昨日UTC終値まで、形成中バー除外、窓外除外、
all-zero は欠測で no zero-fill — すべての実装行は無変更（時計の読み出し経路を差し替えただけ）。

### 3.2 tests/test_crypto.py（テストのみ）

```diff
--- a/tests/test_crypto.py
+++ b/tests/test_crypto.py
@@ (imports)
 from yowayowa.crypto_models import (
     BINANCE_SYMBOLS,
     COINGECKO_COIN_IDS,
     CryptoOhlcvRecord,
     normalize_crypto_symbol,
+    utc_midnight,
 )
 from yowayowa.domain import LicenseClass
+from yowayowa.providers import binance
 from yowayowa.providers.binance import BinanceKlinesProvider, BinanceTransportError

@@ (fixture: 実日付ではなく注入時計にアンカー)
+_BINANCE_FIXTURE_NOW = datetime(
+    2026, 9, 26, 12, 0, tzinfo=UTC
+)  # mid-forming-day of the classic fixture dates
+
-def _binance_payload(days: int = 6) -> list[list[float]]:
+def _binance_payload(days: int = 6, *, now: datetime | None = None) -> list[list[float]]:
+    """Klines ending yesterday relative to ``now``, plus a forming bar at now's day.
+
+    Rows are anchored to the injected clock (never the wall clock), so the
+    window a test asserts on is fixed by the ``_utc_now`` injection alone.
+    """
+    now = now or _BINANCE_FIXTURE_NOW
+    completed_days = _days(
+        days,
+        start=datetime.combine(now.date() - timedelta(days=days), datetime.min.time(), tzinfo=UTC),
+    )
     rows = []
-    for index, ts in enumerate(_days(days)):
+    for index, ts in enumerate(completed_days):
         rows.append( ... 行内容は無変更（open/high/low/close/volume/quote_volume の系列も同じ）... )
-    forming = _days(days)[-1] + timedelta(days=1)
+    forming = completed_days[-1] + timedelta(days=1)
     rows.append(  # currently-forming bar, must be dropped ... 無変更 ... )

@@ (対象テスト1: 3つの偽現在時刻で parametrize)
+# Three injected "now" instants: right after the classic fixture dates, at the
+# calendar middle between fixture and today, and today's real instant frozen
+# once at test start. The payload is anchored to the injected clock, so all
+# three must produce the same expectations: the wall clock is out of the loop.
+_BINANCE_INJECTED_NOWS = [
+    datetime(2026, 9, 27, 0, 20, tzinfo=UTC),
+    datetime(2026, 10, 13, 12, 0, tzinfo=UTC),
+    None,  # today, frozen at test start
+]
+
+@pytest.mark.parametrize(
+    "injected_now",
+    _BINANCE_INJECTED_NOWS,
+    ids=["after-fixture", "mid-calendar", "today"],
+)
 def test_binance_klines_returns_records_with_daily_provenance(
+    monkeypatch: pytest.MonkeyPatch, injected_now: datetime | None
 ) -> None:
+    fixed_now = injected_now or datetime.now(UTC).replace(microsecond=0)
+    monkeypatch.setattr(binance, "_utc_now", lambda: fixed_now)
-    provider = _stub_provider(BinanceKlinesProvider, _binance_payload())
+    provider = _stub_provider(BinanceKlinesProvider, _binance_payload(now=fixed_now))
     records = provider.ohlcv("BTC", days=30)
-    assert len(records) == 7  # all fixture dates are before the live query window
+    assert len(records) == 6  # six completed days; the forming bar at now's day is dropped
     first = records[0]
     assert first.provider == "binance"
     assert first.currency == "USDT"
     assert first.volume == 12.5
     assert first.quote_volume == 2540.0
     assert first.license_class == LicenseClass.PERSONAL_ONLY.value
     assert BINANCE_SYMBOLS["BTC"] == "BTCUSDT"
+    # window placement is decided by the injected clock alone, not the wall clock
+    assert records[0].as_of == utc_midnight(fixed_now - timedelta(days=6))
+    # the window ends at yesterday's UTC close
+    assert records[-1].as_of == utc_midnight(fixed_now - timedelta(days=1))

@@ (対象テスト2: 同じ3時刻で parametrize)
+@pytest.mark.parametrize(
+    "injected_now",
+    _BINANCE_INJECTED_NOWS,
+    ids=["after-fixture", "mid-calendar", "today"],
+)
 def test_binance_zero_bar_is_skipped(
+    monkeypatch: pytest.MonkeyPatch, injected_now: datetime | None
 ) -> None:
+    fixed_now = injected_now or datetime.now(UTC).replace(microsecond=0)
+    monkeypatch.setattr(binance, "_utc_now", lambda: fixed_now)
-    payload = _binance_payload()
+    payload = _binance_payload(now=fixed_now)
     payload[2] = [payload[2][0], 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
     provider = _stub_provider(BinanceKlinesProvider, payload)
     records = provider.ohlcv("BTC", days=30)
-    assert len(records) == 6
+    assert len(records) == 5  # zero bar + forming bar dropped, no zero-fill
     assert all(record.close > 0 for record in records)
+    zero_day = utc_midnight(fixed_now - timedelta(days=4))  # payload[2] is now - 4 days
+    assert all(record.as_of != zero_day for record in records)

@@ (新規: 窓境界を1件の決定論テストで明示 — 「偶然fixture全件を含む緑」を排除)
+def test_binance_window_boundaries_follow_injected_clock(
+    monkeypatch: pytest.MonkeyPatch,
+) -> None:
+    fixed_now = datetime(2026, 10, 13, 0, 20, tzinfo=UTC)
+    monkeypatch.setattr(binance, "_utc_now", lambda: fixed_now)
+    # days=30: the window is [now - 30d, yesterday]; the start day itself counts in
+    window_start = utc_midnight(fixed_now - timedelta(days=30))
+    window_end = utc_midnight(fixed_now - timedelta(days=1))
+    forming_day = utc_midnight(fixed_now)  # today is still forming and must never persist
+
+    def row(day: datetime, close: float) -> list[float]:
+        return [
+            day.timestamp() * 1000.0,
+            close - 1.0,
+            close + 1.0,
+            close - 2.0,
+            close,
+            10.0,
+            (day + timedelta(days=1)).timestamp() * 1000.0,
+            close * 10.0,
+            0,
+            0,
+            0,
+        ]
+
+    payload = [
+        row(window_start - timedelta(days=1), 300.0),  # day before start: outside, excluded
+        row(window_start, 301.0),  # start day itself: included
+        row(window_end, 302.0),  # end day (yesterday): included
+        row(forming_day, 303.0),  # forming bar (today): excluded
+    ]
+    provider = _stub_provider(BinanceKlinesProvider, payload)
+    records = provider.ohlcv("BTC", days=30)
+    # exact boundary decision, not a count that could pass by accident
+    assert [record.as_of for record in records] == [window_start, window_end]
```

境界の意味: **start 当日は含む**（`now-30d` のバーが採用）、**end 翌日（= now 当日・形成中）は含まない**
（同日バーが棄却）、さらに start の前日は棄却。4本のバーを境界の内外に置き、返る列を
`[window_start, window_end]` と完全一致で assert するため、本数の偶然では通らない。

## 4. 偽時刻3ケースの生出力（受入検証その1）

```
$ .venv/bin/python -m pytest tests/test_crypto.py \
    -k "klines_returns or zero_bar or window_boundaries" -rA -o addopts="--strict-markers"
PASSED tests/test_crypto.py::test_binance_klines_returns_records_with_daily_provenance[after-fixture]
PASSED tests/test_crypto.py::test_binance_klines_returns_records_with_daily_provenance[mid-calendar]
PASSED tests/test_crypto.py::test_binance_klines_returns_records_with_daily_provenance[today]
PASSED tests/test_crypto.py::test_binance_zero_bar_is_skipped[after-fixture]
PASSED tests/test_crypto.py::test_binance_zero_bar_is_skipped[mid-calendar]
PASSED tests/test_crypto.py::test_binance_zero_bar_is_skipped[today]
PASSED tests/test_crypto.py::test_binance_window_boundaries_follow_injected_clock
================= 8 passed, 36 deselected, 1 warning in 1.02s ==================
rc=0
```

- after-fixture = 2026-09-27 00:20 UTC（fixture直後）/ mid-calendar = 2026-10-13 12:00 UTC（暦の中間）/
  today = 実現在時刻を凍結した値。**期待値は3時刻とも同一（6 と 5）のまま PASS**。
- 生成レコード自体も3時刻で同構造（テスト内で `records[0].as_of == fixed_now-6d`、
  `records[-1].as_of == fixed_now-1d` を assert済み）。

## 5. 決定論（同一実行2回）とファイル全体（受入検証その2・その3）

```
$ .venv/bin/python -m pytest tests/test_crypto.py -o addopts="--strict-markers"   # run A
======================== 44 passed, 1 warning in 4.45s ==========================
$ .venv/bin/python -m pytest tests/test_crypto.py -o addopts="--strict-markers"   # run B
======================== 44 passed, 1 warning in 4.99s ==========================
run A rc=0 / run B rc=0
$ diff <(grep -v 'in [0-9.]*s' run_full_final_A.txt | grep -v '^$') \
       <(grep -v 'in [0-9.]*s' run_full_final_B.txt | grep -v '^$')
IDENTICAL
```

- **失敗2件 → 0件**（44 passed = 修正前42件＋対象2件のparametrize展開6件−旧2件＋境界1件で整合）。
- 他の既存テストも維持: coingecko / store / orchestration / API routes / CLI / licensing 全て pass。

## 6. 静的検査（host/command/rc 付き）

| host | command | rc |
| --- | --- | --- |
| ubuntu | `.venv/bin/ruff check src/yowayowa/providers/binance.py tests/test_crypto.py` | 0（`All checks passed!`） |
| ubuntu | `.venv/bin/ruff format --check src/yowayowa/providers/binance.py tests/test_crypto.py` | 0（`2 files already formatted`） |
| ubuntu | `.venv/bin/mypy src/yowayowa` | 0（`Success: no issues found in 243 source files`） |

## 7. make verify をVPSで実施しない代わりの区分記録（受入その4）

- 本カードの指示により全量 `make verify` はVPSで実施していない。
- 本件2件（`test_binance_klines_returns_records_with_daily_provenance` /
  `test_binance_zero_bar_is_skipped`）は本修正で解消済み（§5・rc=0 実測）。
- 既存の paperloop WIP 失敗は本件と無関係の別カード領域であり、本修正は
  `tests/test_paperloop.py` のどのアサーションも変更していない。
  実測: `pytest tests/test_paperloop.py -q` → **45 passed / rc=0**（611.03s — 単に低速・WIPツリー環境）。
  なお同ファイル内の "binance" は fixture の provider 文字列（`"provider": "binance"`）のみで、
  BinanceKlinesProvider の窓経路を実行するテストは同ファイルに存在しない
  （`-k "binance or crypto"` は 45 deselected / 0.36s）。
  （binance を参照する他テストの回帰確認:
  `test_registry_factories.py` + `test_crypto_trend_dual_ma.py` + `test_backtest_p4_regressions.py`
  + `test_ohlcv_coverage.py` → **67 passed / rc=0** を実測済み。）

## 8. byte 保全（受入その5）

編集前スナップショット（scratch: `t_4e1246a5/pre_*`）との編集後機械検証:

```
OK crypto_acquisition.py byte-identical: 3cc0b250329438ef...
OK test_crypto.py protected diff intact: all 72 pre-existing '+' lines present
OK binance.py: _utc_now seam in place; real now only in retrieved_at provenance
```

- `tests/test_crypto.py` の既存未コミット差分（+72行・破損行観測ブロック）は全行現存。
- `src/yowayowa/crypto_acquisition.py` は sha256 一致（未touch）。

## 9. 制約の遵守

- 実データ源の追加・ネットワーク取得なし（stub/socket 遮断のまま。`_stub_provider` は MockTransport）。
- commit/push なし・config/cron/モデル変更なし・実注文/送金/課金/外部送信なし。
- 他カード実行中ファイル（acquisition/documents.py, orderbook_service.py, orderbook_models.py,
  api/orderbook_routes.py, orderbook_cli.py）は未touch。
- 新しい抽象・新依存は持ち込んでいない（alpaca.py の既存 clock seam 流儀、既存 `utc_midnight`、
  `_days(count, start)` の既存引数のみ使用）。
