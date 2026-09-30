# P3 バックテスト実測レポート — 4 戦略比較

実行日: 2026-09-30 (JST)  
データ: 永続化済み Alpaca SIP 米国株日足 OHLCV（raw prices、USD、シンボルごとの provenance を保持）  
エンジン: P2 `run_backtest`  
評価期間: 2021-01-04〜2026-09-28（各 1,440 観測）  
想定売買コスト: 片道 15 bps（commission 10 bps + slippage 5 bps、売買元本に適用）  
ブートストラップ: IID 日次リターン再標本化 1,000 回、95% percentile interval、seed `20260930`

## 実測比較

CAGR、Sharpe、Sortino、Calmar、hit rate、turnover は年率化を含む P2 定義に従う。MaxDD はピークからの最大下落率。リターンは架空の補完をせず、P2 エンジンが返した値を掲載する。

| 戦略 | CAGR | Sharpe | Sortino | MaxDD | Calmar | Hit rate | 年間 turnover | OOS CAGR | OOS Sharpe | OOS MaxDD | OOS 観測数 | purge |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 12–1 momentum | 13.72% | 0.616 | 0.835 | -34.64% | 0.396 | 53.47% | 4.45x | -6.60% | -0.310 | -14.38% | 180 | 253 |
| inverse-volatility / low-volatility | 13.54% | 0.773 | 1.042 | -35.41% | 0.382 | 55.76% | 2.79x | 35.37% | 2.087 | -7.25% | 372 | 61 |
| 20-session mean reversion | 16.84% | 0.722 | 1.043 | -45.89% | 0.367 | 53.75% | 12.76x | 16.73% | 0.724 | -28.74% | 412 | 21 |
| equal-weight 1/N baseline | 16.27% | 0.735 | 1.030 | -50.21% | 0.324 | 53.96% | 0.96x | 20.00% | 0.933 | -24.27% | 433 | 0 |

同じ 1,007 観測の in-sample CAGR / Sharpe は momentum 16.32% / 0.696、low-volatility 10.41% / 0.605、mean reversion 16.93% / 0.720、equal-weight 14.35% / 0.652 でした。

P2 の分割実装は、全期間の先頭 70% を IS とし、その境界後に最大シグナル lookback 分を purge して残りを OOS とする単一の時系列 holdout です。これは複数窓を繰り返す rolling walk-forward 検証ではありません。OOS 開始日は最大 lookback/purge によって異なります: momentum 2026-01-09、low-volatility 2025-04-04、mean reversion 2025-02-06、equal-weight 2025-01-06。

| 戦略 | full-period CAGR の IID-bootstrap 95% 区間 | OOS Sortino | OOS 年間 turnover |
|---|---:|---:|---:|
| 12–1 momentum | -8.23%〜40.73% | -0.421 | 8.06x |
| inverse-volatility / low-volatility | -4.15%〜31.07% | 3.483 | 3.41x |
| 20-session mean reversion | -5.32%〜42.88% | 1.093 | 11.10x |
| equal-weight 1/N baseline | -5.20%〜41.00% | 1.414 | 1.23x |

4 戦略すべての bootstrap 区間はゼロをまたぎます。これは IID 再標本化による幅の大きい記述的区間であり、将来予測区間でも、将来収益が正である証拠でもありません。特に momentum は full-period CAGR が正でも OOS CAGR は負でした。Low-volatility の高い OOS 数値も、この固定ユニバースにおける一度の過去期間に限られ、独立した将来検証を意味しません。

## データ coverage / provenance

戦略定義で共通の 10 銘柄 (AAPL, AMZN, GOOGL, JPM, META, MSFT, NVDA, SPY, TSLA, XOM) を使用しました。各銘柄に Alpaca 行が 1,695 件あり、利用可能期間は 2019-12-30〜2026-09-28、通貨は USD でした。各 run はシンボルごとの Alpaca provenance を持ち、10 件を記録しています。

評価開始日は 2021-01-04 とし、12–1 momentum に必要な pre-window 履歴も含めました。評価開始前の OHLCV は遅行シグナルの lookback 専用です。欠損値の forward-fill、ゼロ補完はしていません。この評価窓は共通の保存データ期間であり、各証券の上場以来の全期間を意味しません。

## 試した仕様

- 12–1 momentum: 過去 252 セッションのリターンから直近 21 セッションを skip、月次上位 5 銘柄を均等加重。
- Low-volatility: trailing 60-session volatility の小さい 5 銘柄を選択し、inverse-volatility weighting。
- 20-session mean reversion: trailing 20-session price return の逆順で上位 5 銘柄を月次均等加重。
- Equal-weight baseline: 同一 10 銘柄を月次 1/N 加重。
- P2 エンジンの next-session-open 実行、lagged signal、リバランス間のウェイトドリフトを使用。
- 取引コストは片道 turnover あたり commission 10 bps + slippage 5 bps を差し引き。

上記は価格系列のみで表現できる戦略の結果です。課題で挙がった戦略群のうち momentum、low-volatility、equal-weight baseline の3つを含み、追加の 20-session mean reversion も比較しました。

## 未検証の設計空間・限界

Value/quality（低 PER/PBR + 高 ROE）および清原モードは、この価格バー限定エンジンでは point-in-time fundamentals のランキング入力がないため実走していません。価格だけの代替戦略をそれらの実測と呼ぶのは不正確なので、含めませんでした。清原モードを非公式とも別戦略とも偽っていません。

その他の主要制約:

- ユニバースは現在の固定銘柄集合であり、point-in-time 構成銘柄でないため survivorship bias が残ります。
- raw OHLCV であり、配当・分割・その他 corporate actions はモデル化していません。
- 税、market impact、borrow cost、delisting return、流動性制約は未モデル化です。
- 独立した市場指数ベンチマークはこの比較に入れていません。Equal-weight は戦略ベースラインで、指数ベンチマークではありません。
- コスト感度、alternate rebalance frequency、パラメータ探索は未実施です。コストは単一の明示した仮定です。
- バックテストは forecast や実行可能な投資推奨ではありません。OOS と full-period に差があるため、順位から採用判断をしないでください。

## 再現ファイル

- 実測データ、全指標、sample counts、OOS/IS 値、assumptions、provenance coverage: `backtest_results_p3.json`
- 実走スクリプト: `run_backtests_p3.py`（P2 ソースツリーとプロジェクト venv のある環境で実行）
- 再実行（P2 worktree をカレントにしてその保存データを読む）:
  `cd <P2-worktree> && PYTHONPATH=src <project-venv>/bin/python <P3-worktree>/run_backtests_p3.py`

Notion ミラーは未反映です。このワーカー環境では `ntn whoami` が `No workspace selected` を返し、Notion integration token も設定されていないため、既存の伝言板を特定して書き込むことができません。Notion workspace 認証/選択後にこの Markdown をミラーする必要があります。
