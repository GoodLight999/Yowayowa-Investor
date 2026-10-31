# バックテスト株式分割未調整修正（C2）確定・保存記録

- 対象タスク: `t_a84ba708`（親: `t_4796fd63`）
- 承認判定: `Reviewed and approved (round 3)`（reviewer: `audit-cld`）
- 修正対象:
  - `src/yowayowa/services/backtest.py`
  - `tests/test_backtest.py`
  - `docs/BACKTEST_SPLIT_ADJUSTMENT_C2_20261023.md`（本記録）

---

## 1. 修正概要と承認経緯

Alpaca 等のプロバイダから取得される生価格（`adjustment=raw`）を用いたバックテストにおいて、株式分割の未調整によりリターンが歪み、CAGR が約 9.2pp 過小評価されていた問題（C2）を解消。

### 経緯（Round 1〜3）
- **Round 1 指摘**: 価格比率のみによる推定補正では、本物の暴落（-50%, -66.6% 等）を分割と誤認して損失を消去してしまうこと、および連続価格系列に対して `split_factor` メタデータが二重適用される問題が指摘された。
- **Round 2 指摘**: 価格比率のみの推定を全廃し、SEC確認済み分割カレンダーおよび明示的メタデータとの照合（両者一致時のみ補正）に改修。テスト・負例を追加。コード品質検査（`ruff check`, `ruff format`, `mypy`）の適合と、カレンダー欠落/破損時の明示警告化を要求。
- **Round 3 承認**: コード検査（ruff/mypy 0 errors）、パッケージ相対パス優先化、カレンダー欠落・破損・無効エントリ警告の実装およびテスト（`test_stock_split_missing_or_corrupt_calendar_warns`）を完備し、独立検収により `Reviewed and approved (round 3)` を受領。

---

## 2. 成果物とハッシュ

### コード・テストファイル
- `src/yowayowa/services/backtest.py`
  - sha256: `c2cdfe7879b0edd1a3b66cbc8531ebf6e9f3661ef521c220ded1c5a886b341ac`
- `tests/test_backtest.py`
  - sha256: `5f7c28e869140b737be93a30314e913e3739c5f7b25374ebb801fe9085f4120c`

### 分割カレンダー（gitignored 実データ）
- パス: `data/fundamentals/pbr/split-calendar.json`
- sha256: `ae7668f015440277c86cce1aab7dc62f963772430cf37343851007f0d397d522`
- 留保事項: `data/` は `.gitignore` 配下であり、wheel パッケージ等には収録されない既存留保を維持。
- パス解決仕様: `Path(__file__).resolve().parents[3] / "data" / "fundamentals" / "pbr" / "split-calendar.json"`（パッケージ相対パス）を最優先とし、カレントディレクトリに依存しないロードを保証。

---

## 3. 実測検証結果

### 3.1 正常カレンダーの 7 イベント補正確認
SEC 確認済み分割カレンダーに登録された以下の 7 イベントのみが正確に `basis=sec_calendar` として適用されることを確認:
1. `AAPL` 2020-08-31（比率 4.0）
2. `NVDA` 2021-07-20（比率 4.0）
3. `NVDA` 2024-06-10（比率 10.0）
4. `GOOGL` 2022-07-18（比率 20.0）
5. `AMZN` 2022-06-06（比率 20.0）
6. `TSLA` 2020-08-31（比率 5.0）
7. `TSLA` 2022-08-25（比率 3.0）

### 3.2 負例・耐性テスト
- **本物の暴落保持**:
  - genuine -50% persistent crash: `final_equity=0.7500`, `maxdd=-0.2500`（損失正常保全）
  - genuine -66.6% persistent crash: `final_equity=0.6670`, `maxdd=-0.3330`（損失正常保全）
  - genuine +100% persistent surge: `final_equity=1.5000`, `maxdd=0.0000`（利益正常保全）
  - genuine -55% crash (control): `final_equity=0.7250`, `maxdd=-0.2750`（損失正常保全）
- **二重適用防止**:
  - 既に連続している生価格系列に `split_factor=2.0` タグが存在しても補正を二重適用せずスキップ（`final_equity=1.0000`）。
- **カレンダー欠落・破損警告**:
  - カレンダー不在時: `split calendar unavailable: file not found at ...; splits not adjusted`
  - JSON 破損時: `split calendar unavailable: failed to read ...; splits not adjusted`
  - スキップエントリ時: `split calendar: skipped invalid entry for ...`

### 3.3 10銘柄ユニバース 実データ前後比較（2021-01-04〜2026-10-20）
| 戦略 | 修正前 CAGR | 修正後 CAGR | 差分 | 修正前 MaxDD | 修正後 MaxDD | 修正前 Sharpe | 修正後 Sharpe |
|---|---:|---:|---:|---:|---:|---:|---:|
| equal_weight | 16.42% | 25.42% | +9.00pp | -50.21% | -35.50% | 0.7425 | 1.0840 |
| momentum_12_1 | 13.85% | 20.30% | +6.45pp | -34.64% | -31.13% | 0.6217 | 0.8318 |
| low_volatility | 13.69% | 16.96% | +3.28pp | -35.41% | -23.63% | 0.7825 | 0.9938 |
| mean_reversion_20 | 17.66% | 17.78% | +0.12pp | -45.89% | -45.89% | 0.7501 | 0.7550 |

---

## 4. 利用経路確認

- **API 経路**: `src/yowayowa/api/backtest_routes.py` が `yowayowa.services.backtest.run_backtest` を直接インポートし、エンドポイント `/v1/backtest/run` から実行。
- **CLI 経路**: `src/yowayowa/backtest_cli.py`（Typer コマンド `yowayowa backtest run`）が HTTP クライアント経由で `/v1/backtest/run` を呼び出し、同一のバックテスト処理を実行。

---

## 5. 安全性および非改変保証

1. 着手時基点 HEAD `1374165c787d0a7569615499ae5cf2b41b55b2bc`
2. 本カードによる変更コミットは `backtest.py`, `test_backtest.py`, 本ドキュメントのみを対象とし、他タスクの差分（31ファイル）の混入・変更なし。
3. 原本データ、DB、本番運用ジャーナル、外部サービス等への影響: 0。
