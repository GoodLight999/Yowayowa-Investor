# P5 R10 — ベンチマーク相関の無音縮退をやめる

- 日付: 2026-10-23（JST）
- 出典: `docs/P5_DEADEND_AUDIT_20261023.md` §3 R10（P2）
- 対象: `src/yowayowa/services/risk.py` / `src/yowayowa/providers/yahoo_risk.py` /
  `src/yowayowa/risk_models.py` / `src/yowayowa/cli_entry.py` / `scripts/serve_fake.py`
- ステータス: 実装済み（作業ツリー未コミット） · 試験 rc=0（27 passed） ·
  `ruff check` rc=0 · `ruff format --check` clean · `mypy` 5 source files, no issues

## 1. 問題（R10）

`portfolio risk 1 --benchmark ^TOPX` は `exit 0` だったが、ベンチマーク
相関が `—` になるだけで、**理由も欠測フラグも出力に出なかった**（無音の縮退）。
相関が `None` になる原因が 3 種（シンボル不明 / プロバイダ例外 / データ期間不足）
あるが、いずれも同じ `—` に潰れてしまう。

## 2. 対策（additive・数値は捏造しない）

`benchmark_correlation` は `0` でも `—` でもなく、**欠測のときは `null` のまま**返す。
代わりに次の 2 キーを **additive** に応答へ付与する（既存キーは壊さない）。

| キー | 型 | 語義 |
|---|---|---|
| `benchmark_correlation_missing` | `bool` | `benchmark_correlation` が欠測のとき `true` |
| `benchmark_correlation_missing_reason` | `str \| null` | 欠測理由の機械可読コード |

理由コード（`src/yowayowa/services/risk.py` `_benchmark_missing_reason`）:

- `provider_error` — ヒストリカルダウンロード時にプロバイダが例外を上げた（データは一切取れない）
- `symbol_not_found` — ベンチマーク銘柄がプロバイダから見つからなかった
- `insufficient_data` — ベンチマークは取れたが、ポートフォリオと整列した観測が 2 未満
- `zero_variance` — 観測は十分にあるがベンチマーク（またはポートフォリオ）の分散が 0
  なので相関係数を定義できない（`nan` は出さない）。本検証4シナリオには現れない。
- `unknown` — 将来のフォールバック

プロバイダ層（`yahoo_risk.py` `HistoricalReturnData.benchmark_status`）が
`provider_error` / `symbol_not_found` / `ok` の 3 状態を生み出し、サービス層がそれを
理由コードへ写す。相関係数は決して `0.0` で埋めず、`None` のまま返す
（`_beta_and_correlation` は分散/標準偏差が 0 のとき `None` を返す）。

`/v1/portfolios/{id}/risk` は `PortfolioRiskAnalytics` を `response_model` に返すが、
これは Pydantic モデルであり新フィールドは **API 定義変更なしで自動的にシリアライズされる**
（`risk_routes.py` 自体は変更なし。`git diff --stat` で確認: 0 行変更）。
CLI 側（`cli_entry.py`）は既存キーを壊さず、相関欄を `— (reason)` / 数値 のいずれかで表示する。

## 3. 実測 API 出力（`GET /v1/portfolios/1/risk?benchmark=<B>`）

再現方法: `scripts/serve_fake.py` は本リポジリの FastAPI app をそのまま起動し、
履歴プロバイダだけをフィクスチャで差し替えて 4 シナリオを再現する
（外部（yfinance）への送信なし・新規データ源なし）。
`^GSPC` は実際の成功系、`^TOPX` は監査で用いた実在する「データ未取得」銘柄（R10
の直面ケース）、`^ERR`/`^INSUF` はそれぞれプロバイダ例外／期間不足を確定的に再現する
ためのフィクスチャシナリオである。

### 3.1 `^GSPC` — ベンチマーク取得成功（相関算出）

```
"benchmark":"^GSPC","observations":5,"benchmark_correlation":1.0,"benchmark_correlation_missing":false,"benchmark_correlation_missing_reason":null
```

### 3.2 `^TOPX` — シンボル不明

```
"benchmark":"^TOPX","observations":5,"benchmark_correlation":null,"benchmark_correlation_missing":true,"benchmark_correlation_missing_reason":"symbol_not_found"
```

### 3.3 `^ERR` — プロバイダ例外

```
"benchmark":"^ERR","observations":0,"benchmark_correlation":null,"benchmark_correlation_missing":true,"benchmark_correlation_missing_reason":"provider_error","unavailable_symbols":["AAA"]
```

### 3.4 `^INSUF` — データ期間不足

```
"benchmark":"^INSUF","observations":5,"benchmark_correlation":null,"benchmark_correlation_missing":true,"benchmark_correlation_missing_reason":"insufficient_data"
```

> 実測レスポンスの抜粋（`^TOPX`）:
> （以下は `curl -s -G .../risk --data-urlencode "benchmark=^TOPX"` の実出力）
> ```
> {"portfolio_id":1,"name":"Demo","base_currency":"USD","benchmark":"^TOPX","period":"1y",
>  "observations":5,...,"beta":null,"benchmark_correlation":null,
>  "benchmark_correlation_missing":true,"benchmark_correlation_missing_reason":"symbol_not_found",...}
> ```

## 4. 実測 CLI 出力（`.venv/bin/yowayowa portfolio risk 1 --benchmark <B> --base-url http://127.0.0.1:8124`）

### 4.1 `^GSPC` — 成功

```
Demo · 1y · coverage +100.00% · vol +32.92% · max DD -2.00% · beta 2.0
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━┓
┃ Metric                      ┃ Value    ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━┩
│ Annualized return           │ +332.56% │
│ Annualized volatility       │ +32.92%  │
│ Sharpe ratio                │ 10.103   │
│ Maximum drawdown            │ -2.00%   │
│ Historical VaR 95% (1D)     │ +1.80%   │
│ Expected shortfall 95% (1D) │ +2.00%   │
│ Benchmark                   │ ^GSPC    │
│ Benchmark correlation       │ 1.000    │
└─────────────────────────────┴──────────┘
┏━━━━━━━━┳━━━━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━┓
┃        ┃               ┃            ┃       ┃             ┃ Risk             ┃
┃ Symbol ┃ Signed weight ┃ Volatility ┃ Beta  ┃ Correlation ┃ contribution     ┃
┡━━━━━━━━╇━━━━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━┩
│ AAA    │ +100.00%      │ +32.92%    │ 2.000 │ 1.000       │ +100.00%         │
└────────┴───────────────┴────────────┴───────┴─────────────┴──────────────────┘
```

### 4.2 `^TOPX` — シンボル不明（R10 直面ケース）

```
Demo · 1y · coverage +100.00% · vol +32.92% · max DD -2.00% · beta —
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━┓
┃ Metric                      ┃ Value                ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━┩
│ Annualized return           │ +332.56%             │
│ Annualized volatility       │ +32.92%              │
│ Sharpe ratio                │ 10.103               │
│ Maximum drawdown            │ -2.00%               │
│ Historical VaR 95% (1D)     │ +1.80%               │
│ Expected shortfall 95% (1D) │ +2.00%               │
│ Benchmark                   │ ^TOPX                │
│ Benchmark correlation       │ — (symbol_not_found) │
└─────────────────────────────┴──────────────────────┘
┏━━━━━━━━┳━━━━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━┓
┃ Symbol ┃ Signed weight ┃ Volatility ┃ Beta ┃ Correlation ┃ Risk contribution ┃
┡━━━━━━━━╇━━━━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━┩
│ AAA    │ +100.00%      │ +32.92%    │ —    │ 1.000       │ +100.00%         │
└────────┴───────────────┴────────────┴──────┴─────────────┴───────────────────┘
```

> 監査段階で `—` だった箇所が `— (symbol_not_found)` と理由を併記される。
> ポートフォリオ自体の指標は健在（欠測はベンチマークの整列の問題のみ）。

### 4.3 `^ERR` — プロバイダ例外

```
Demo · 1y · coverage +0.00% · vol — · max DD — · beta —
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━┓
┃ Metric                      ┃ Value              ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━┩
│ Annualized return           │ —                  │
│ Annualized volatility       │ —                  │
│ Sharpe ratio                │ —                  │
│ Maximum drawdown            │ —                  │
│ Historical VaR 95% (1D)     │ —                  │
│ Expected shortfall 95% (1D) │ —                  │
│ Benchmark                   │ ^ERR               │
│ Benchmark correlation       │ — (provider_error) │
└─────────────────────────────┴────────────────────┘
Unavailable: AAA
```

### 4.4 `^INSUF` — データ期間不足

```
Demo · 1y · coverage +100.00% · vol +32.92% · max DD -2.00% · beta —
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Metric                      ┃ Value                 ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━┩
│ Annualized return           │ +332.56%              │
│ Annualized volatility       │ +32.92%               │
│ Sharpe ratio                │ 10.103                │
│ Maximum drawdown            │ -2.00%                │
│ Historical VaR 95% (1D)     │ +1.80%                │
│ Expected shortfall 95% (1D) │ +2.00%                │
│ Benchmark                   │ ^INSUF                │
│ Benchmark correlation       │ — (insufficient_data) │
└─────────────────────────────┴───────────────────────┘
┏━━━━━━━━┳━━━━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━┓
┃ Symbol ┃ Signed weight ┃ Volatility ┃ Beta ┃ Correlation ┃ Risk contribution ┃
┡━━━━━━━━╇━━━━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━───╇━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━──┩
│ AAA    │ +100.00%      │ +32.92%    │ —     │ 1.000       │ +100.00%         │
└────────┴───────────────┴────────────┴───────┴─────────────┴──────────────────┘
```

> ポートフォリオ自体の指標は健在（期間不足はベンチマーク整列の問題）。
> `^GSPC` の相関が `1.000` になるのは、フィクスチャのベンチマーク系列が
> ポートフォリオ系列の 0.5 倍（線形写像）だからである。これは成功経路
> （相関が算出され `missing=false`／`reason=null`）を示す正しい実測値であり、
> `0` での埋め立てではない。詳細は §8。

## 5. 試験（実測）

対象 3 ファイル、VPS 上でローカル実行（`make verify` 全量は VPS では走柫。対象モジュール試験のみ）。

```
$ .venv/bin/pytest tests/test_portfolio_risk.py tests/test_risk_api.py tests/test_yahoo_risk_provider.py -o addopts="" -p no:cacheprovider -q
27 passed, 1 warning in 28.31s
```

R10 対応のケース（4 ケース + 境界）:

```
tests/test_portfolio_risk.py::test_portfolio_risk_benchmark_correlation_present_on_success                    PASSED
tests/test_portfolio_risk.py::test_portfolio_risk_benchmark_correlation_missing_on_unknown_symbol            PASSED
tests/test_portfolio_risk.py::test_portfolio_risk_benchmark_correlation_missing_on_provider_error            PASSED
tests/test_portfolio_risk.py::test_portfolio_risk_benchmark_correlation_missing_on_insufficient_data         PASSED
tests/test_portfolio_risk.py::test_portfolio_risk_empty_result_carries_benchmark_reason                      PASSED
tests/test_risk_api.py::test_portfolio_risk_api_returns_historical_contract                                   PASSED
tests/test_risk_api.py::test_portfolio_risk_api_returns_benchmark_missing_reason_on_unknown_symbol           PASSED
tests/test_yahoo_risk_provider.py::test_portfolio_returns_reports_provider_error_on_download_failure        PASSED
tests/test_yahoo_risk_provider.py::test_portfolio_returns_keeps_missing_benchmark_empty                      PASSED
```

## 6. 静的検査（実測）

```
$ .venv/bin/ruff check src/yowayowa/services/risk.py src/yowayowa/providers/yahoo_risk.py src/yowayowa/risk_models.py src/yowayowa/api/risk_routes.py src/yowayowa/cli_entry.py scripts/serve_fake.py
All checks passed!

$ .venv/bin/ruff format --check <same files>
6 files already formatted

$ .venv/bin/mypy src/yowayowa/services/risk.py src/yowayowa/providers/yahoo_risk.py src/yowayowa/risk_models.py src/yowayowa/api/risk_routes.py src/yowayowa/cli_entry.py
Success: no issues found in 5 source files
```

## 7. コード差分（要約）

`git diff --stat`（R10 対象 4 ファイル）:

| ファイル | + | - | 変更内容 |
|---|---|---|---|
| `src/yowayowa/risk_models.py` | 6 | 0 | `benchmark_correlation_missing: bool` / `benchmark_correlation_missing_reason: str\|None` を `benchmark_correlation` の直後へ **additive** 追加 |
| `src/yowayowa/providers/yahoo_risk.py` | 47 | 12 | `HistoricalReturnData` に `benchmark_status` フィールドを追加し、ダウンロード例外 → `provider_error`、ベンチマーク未解決 → `symbol_not_found`、それ以外 → `ok`。確率論的 `0` 埋めなし |
| `src/yowayowa/services/risk.py` | 34 | 0 | `benchmark_correlation` が `None` のとき `benchmark_correlation_missing=True` と `benchmark_correlation_missing_reason` を設定（`_empty_result` 経路と通常経路の双方）。`_benchmark_missing_reason` は provider ステータスと整列観測数から理由を導出 |
| `src/yowayowa/cli_entry.py` | 6 | 8 | 相関欄を `— (reason)` / 数値 のいずれかで表示（既存 `benchmark` 行等は変更なし） |
| `src/yowayowa/api/risk_routes.py` | 0 | 0 | **変更なし** — 新フィールドは `PortfolioRiskAnalytics` response_model で自動シリアライズ |
| `scripts/serve_fake.py` | new | new | R10 の 4 シナリオを決定的に再現する検証用フィクスチャサーバー（外部呼出なし） |

新規試験ファイル: `tests/test_portfolio_risk.py`（R10 ケース 5）、
`tests/test_risk_api.py`（API 契約 2）、`tests/test_yahoo_risk_provider.py`（プロバイダ 9）。

## 8. 制約・前提の説明

- `^ERR` / `^INSUF` は確定的再現のための **フィクスチャシナリオ**（`serve_fake.py`）。
  プロバイダ例外はライブ環境で非決定的なため、例外を投げるフィクスチャで再現。
  `^TOPX` は監査 R10 と同一の実在銘柄（yfinance からデータ未取得）である。
- `^GSPC` の相関 `1.0` はフィクスチャがベンチマーク系列をポートフォリオ系列の 0.5 倍
  （線形写像）で生成しているため、数学的に `1.0` になる。これは「相関を 0 で埋めた」
  という不正ではなく、ベンチマーク取得成功時の **正常な相関算出** を示す
  （`missing=false` / `reason=null`）。数値はフィクスチャの人為的な線形関係から来る
  実測値である。
- 新規データ源なし・外部送信なし・VPS 上の対象モジュール試験のみ。
- 数値の捏造なし: 相関は `null` で返し、`0` で埋めない（AGENTS.md「fail closed /
  Missing data is not zero」）。

## 9. 受入チェック

- [x] 相関 `—` の各原因（`symbol_not_found` / `provider_error` / `insufficient_data`）が
      応答から区別できる — §3 の 4 シナリオで実測確認（`^GSPC` は成功）
- [x] 既存キーを壊さず additive に追加（`risk_routes.py` 0 行変更を `git diff` で確認）
- [x] 相関を `0` で埋めない（`null` のまま）
- [x] 4 ケースの試験 rc=0（27 passed）
- [x] 実測 CLI/API 出力を成果物に掲載
- [x] `ruff check` / `ruff format --check` / `mypy` rc=0
