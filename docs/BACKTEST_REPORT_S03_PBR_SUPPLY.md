# S-03 SEC point-in-time PBR supply — 実測レポート

開始 HEAD: `d1541f642e7322d687e7af7e78aeabcae6d2a21d`。ローカル実装フェーズの結果であり、CTOの独立受入承認を意味しない。push は行わない。

## 結論

SEC実取得から9株式に 15,258 行のPBRを供給し、S-03が full の69回すべてでPBRフィルタを使用した。momentum-only fallback は full/IS/OOS とも0回。価格入力・バックテストエンジン・凍結契約は変更していない。

(a) OOS Sharpe改善、(b) IS–OOS CAGRギャップ縮小、(d) full bootstrap CI下限 ≥ −15% は達成。(c) OOS DDは改善するがfull DDが悪化するため、**全基準達成ではなく、採用承認不可**。特にエンジンは生価格の分割損失を引き続き計上するため、これを実運用収益の改善と解釈しない。

## 再現手順

リポジトリ直下で実行:

```sh
PYTHONPATH=src .venv/bin/python scripts/fetch_sec_pbr.py
PYTHONPATH=src .venv/bin/python scripts/fetch_sec_pbr.py --offline
PYTHONPATH=src .venv/bin/python run_backtests_garp_momentum_hybrid.py --pbr-dir data/fundamentals/pbr
PYTHONPATH=src .venv/bin/python run_backtests_garp_momentum_hybrid.py --output /absolute/path/to/no_supply.json
make verify
```

初回のみSEC取得。ticker-mapとCIKごとのraw envelope（取得時刻、HTTP status、未切り詰めpayload）は `data/fundamentals/sec-raw/` に保存し、以後は既存snapshotを使用する。`--offline` は欠けたsnapshotがあると失敗し、ネットワークへ逃げない。HTTP404は欠損としてcacheし、403等は中断して返す。User-Agent/rateはSettings経由。既存 `.env` はコミットしない。

`--pbr-dir` は明示opt-in。指定なしなら、供給ストアが存在しても旧S-03出力が維持される。供給はGARP入力だけに適用し、A/Bのmomentum・EW参照は元の価格履歴のまま。新結果は `backtest_results_s03_supplied.json`、検証実測は `docs/S03_PBR_SUPPLY_VERIFICATION.json`。

## 保存契約とPIT

- 財務ストア: `data/fundamentals/pbr/{SYMBOL}.jsonl`、分割カレンダー `split-calendar.json`、来歴 `manifest.json`。既存 `ohlcv.jsonl` はread-only。
- 財務情報は各accession内の最新equity期末と同一提出のsharesから一括選択。別提出の株数を結合しない。original filingを未来のrestatementで置き換えない。
- `disclosure_date` は **filed**。非取引日提出も保持し、翌価格バー以降の供給に利用。`period_end` を公開日に流用しない。
- 各バーに当日まで公開済みの財務状態からPBRを生成。これは供給系列の構築であり、欠損フィールドの補完ではない。新提出の株数不足・非正equity等はその行のPBRをnullとし、古い有効値で穴埋めしない。
- runnerは **同じバー日付の財務行だけ** を結合。missing sidecar/date/fieldはnull。元の価格辞書も変更しない。
- `_garp_point_in_time_pbr`/エンジンは無変更。バー日付 **と** filed がdecision dateより厳密に前である行のうち最新行を読み、その行のnullを他行で埋めない。
- `per` はnull。整合的な同一提出のTTM利益系列を本カードでは供給していない。
- raw SECは `official_public`。PBRはSEC公開値そのものではなくAlpaca価格からの派生値なので、価格側と派生PBRのlicenseも保存し、`personal_only`をpublicへ昇格させない。価格provenanceはそのまま、結果JSONの`pbr_supply`にSEC側のsource/hashを別記する。

## 銘柄別カバレッジ

| 銘柄 | 価格行数 | PBR有効行数 | full有効リバランス | top-8候補内有効回数 |
|---|---:|---:|---:|---:|
| AAPL | 1696 | 1696 | 69 | 53 |
| MSFT | 1696 | 1696 | 69 | 62 |
| NVDA | 1696 | 1696 | 69 | 47 |
| GOOGL | 1695 | 1695 | 69 | 54 |
| AMZN | 1695 | 1695 | 69 | 45 |
| META | 1695 | 1695 | 69 | 57 |
| TSLA | 1695 | 1695 | 69 | 50 |
| JPM | 1695 | 1695 | 69 | 67 |
| XOM | 1695 | 1695 | 69 | 50 |
| SPY | 1695 | 0 | 0 | 0 |

価格の共通評価窓は2021-01-04〜2026-09-28（1440 sessions）。AAPL/MSFT/NVDAに既存2026-09-29バーが1本ずつあるが、共通窓の末尾は変えていない。全9株式でfullの69回すべてに有効PBRがあり、SPYは0回。

### SPY

**SPYは構造的PBR欠損**。ETFへのcompanyfacts HTTP404は本カードの前提としてCTOが実測済み。今回のgeneratorはSPYを意図的に取得対象外とし、空のsidecarを保存。偽のequity/PBRは作成しない。SPYはtop-8候補に入った67回でPBR欠損除外、残り2回は候補外。供給ありの選択対象にはならない。

### XOM

現ticker-map `0002115436` はequityの同一提出recordが1件。既知の旧CIK `0000034088` を別取得して69件の履歴を実測し、record数を比較して旧issuerを採用した。ticker-mapを無検証で使用しない。これは既知のXOM企業再編に限定したcandidateであり、一般的なissuer-change自動解決ではない。

### META

保存rawの全namespace（dei/us-gaap/srt/ecd/ffd）でshares候補を確認。common instantのdei/GAAPタグは不在。`PreferredStockSharesOutstanding`のinstant2点は普通株でないため使わず、diluted/basic weightedにはinstant点がない。同一accession・同一期末で最短の整合durationの `us-gaap:WeightedAverageNumberOfSharesOutstandingBasic` を採用。

**METAの株数は期中平均による近似であり、PITのinstant株数ではない**。公開時刻はPITで守るが、概念上の株数近似は解消されていない。sidecarに`shares_basis=period_average`を明示する。

## 分割実測

価格不連続の実日をeffective dateとし、前日close/当日openを主に、当日closeも整合確認する。通常の分割比（2/3/4/5/7/10/20）へ±15%以内でsnapし、市場リターン込みの価格比自体を株数倍率にしない。観測close比・残余return・SEC初提出/accessionを全て保存。

| 銘柄 | 価格適用日 | 倍率 | 前close → 当close | 観測close比 | SEC初提出 | PBR前 → 当 |
|---|---|---:|---|---:|---|---|
| AAPL | 2020-08-31 | 4 | 499.23 → 129.04 | 3.868800 | 2020-10-30 | 29.530516 → 30.531962 |
| NVDA | 2021-07-20 | 4 | 751.19 → 186.12 | 4.036052 | 2021-08-20 | 24.927632 → 24.704966 |
| NVDA | 2024-06-10 | 10 | 1208.88 → 121.79 | 9.925938 | 2024-08-28 | 60.515339 → 60.966872 |
| GOOGL | 2022-07-18 | 20 | 2235.55 → 109.03 | 20.503990 | 2022-07-27 | 5.797931 → 5.655417 |
| AMZN | 2022-06-06 | 20 | 2447 → 124.79 | 19.608943 | 2022-07-29 | 9.289774 → 9.475038 |
| TSLA | 2020-08-31 | 5 | 2213.4 → 498.32 | 4.441724 | 2020-10-26 | 41.856220 → 47.117085 |
| TSLA | 2022-08-25 | 3 | 891.29 → 296.07 | 3.010403 | 2022-10-24 | 25.592245 → 25.503807 |

SECタグの未来の提出日は監査用で、過去の株数倍率や適用日を決める入力にはしない。株数の観測日より後の市場分割だけを当日以降に乗じ、新しいinstant株数が提出されたら倍率をリセットする。

GOOGLの20.503990のclose比は20:1と市場変動の合成。別の2:1タグを積算して40倍にはしない。さらに2022-07-27提出には6月末GAAP株数が既に20倍表示されているため、その提出内の分割開示でpresentation basisを確認し、追加20倍を防ぐ。2022-07-26は旧658,763,000株×20、07-27は提出済13,078,000,000株×1で、PBRは5.447417→5.788914。

TSLA 2020-08-31は当日上昇によりclose比4.441724となる。open基準で5倍を判定し、4倍との誤認を防いだ。この2020分割も2021評価開始前のlookback供給に含める。

## 同条件A/B

月次、momentum上位8からPBR下位5、同一10銘柄ユニバース、commission10bps/slippage5bps、252日年率換算、1000 bootstrap、seed20260930。70% IS=1007 sessions、253-session purge、OOS=2026-01-09〜2026-09-28の180 sessions。各segmentは独立cash初期化。合成データはテストfixture限定で、この実走は保存Alpacaと実SEC snapshotのみ。

| 区間 | GARP CAGR | momentum CAGR | GARP Sharpe | momentum Sharpe | GARP MaxDD | momentum MaxDD |
|---|---:|---:|---:|---:|---:|---:|
| full | 13.4436% | 13.7242% | 0.635160 | 0.616090 | -54.9811% | -34.6371% |
| IS | 13.0821% | 16.3218% | 0.603330 | 0.695652 | -54.9811% | -34.6371% |
| OOS | 3.8873% | -6.5985% | 0.309216 | -0.310141 | -9.7087% | -14.3790% |

- (a) OOS Sharpe: 0.309216 > -0.310141: pass。
- (b) absolute IS–OOS CAGR gap: GARP 9.1948%、momentum 22.9203%: pass。
- (c) full MaxDD -54.9811% vs -34.6371%: **not_met**。OOS -9.7087% vs -14.3790%: pass。full/OOS両方の軽減を満たさない。
- (d) full IID bootstrap CAGR 95% CI: [-9.4416%, 38.2961%]、下限≥−15%: pass。momentumは[-8.2287%, 40.7288%]で、GARP下限がbaselineより優れるという意味ではない。
- EW参照OOS CAGR 18.0446%、Sharpe 1.069561。GARPはEWに優越していない。
- full/IS/OOS rebalanceは69/49/9、fallback・no-momentum・full-cashは各区間すべて0。OOSの9回のみで一般化しない。

## 検証と回帰

- 専用オフラインテスト35 passed。分割日/倍率、TSLA intraday、GOOGL二重調整防止、週末filed、バー/disclosureのstrict境界、future-restatement、同一accession、null非補完、fallback、public raw、CIK入力、404 cache/403中断、ネットワーク禁止replay、XOM選択、元OHLCV不変を試験。
- `make verify` exit0: **1922 passed, 2 skipped, 1 warning in 381.11s (0:06:21)**。lint/format/mypy/OpenAPIもexit0。
- standalone式/出典照合: 15,258行のequity/shares値・accession・filed・日付・分割倍率と価格×株数/equityをrawから照合。CTOの独立オラクル受入の代替ではない。
- 初期data hash manifestの231既存ファイルすべてbyte不変。新規fundamentalsファイルだけを追加。OHLCVの金融nullも書き換えない。
- 実際のd1541f6コードを`git archive`で別workspaceへ展開して同じdataで実行し、P3/S-02/S-05/cryptoの4runner **全出力byte一致**。供給なしS-03もbyte一致し、最終変更後もう一度実行して一致を確認。
- P3 momentumのmetrics/status/sample-count/bootstrap/OOS/IS/window/sessions/trades/purgeの15項目は全一致。既存8signalのエンジン・凍結strategy JSONに差分なし。
- 注意: committed P3 JSONは以前のcoverage/assumptionsで、d1541f6自体の再実行とbyte一致しない。本検証は古いJSONを基準にせず、**d1541f6を実行した結果**と比較し、追跡済み旧結果JSONは元のまま維持した。
- raw/財務全ファイルのoffline再生成byte一致、供給あり結果2回のbyte一致を実測。検証JSONに主要hashを記録。

## 限界

PBRの株数単位は修正したが、エンジンのraw価格momentum・損益には分割不連続が残る。供給あり診断ではAMZNの2022-06-06分割直前（06-01決定）とGOOGLの2022-07-18分割直前（07-01決定）にそれぞれ20%のscheduled targetがあり、価格だけ約1/20になる窓に保有していた。full DDは生価格分割による人工的損失を含み、分割調整済みtotal-returnバックテストではない。エンジンを変更しないという契約を守った結果であり、このまま資金配分判断に用いない。

通常分割比への価格snapはheuristic。±15%内の大きな株価変動を分割と誤認する可能性があり、逆分割・非標準比率・端数比率は未対応。今回は全7観測イベントについてSECタグで事後照合できたが、別銘柄への自動汎化を保証しない。株数は提出間の実際の買戻し/増資を反映せず、METAは期中平均近似。GAAP株数のpresentation-basis判定は同一提出の分割タグを根拠にするが、特殊な複数クラス再編すべてを解釈するものではない。

ユニバースは固定・現在銘柄由来、survivorship biasあり。filedは日付精度（提出時刻/取引終了時刻なし）で、decision境界のstrict < により同日利用をしない。配当、税、market impact等も旧契約のまま。bootstrapはIIDで時系列依存を扱わず、CIは予測区間でない。SEC snapshotを全CIKについて保存し再走可能だが、このignored dataをgitへ含めないため、別環境ではsnapshot別途移送か初回取得が必要。
