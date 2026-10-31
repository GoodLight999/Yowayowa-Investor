# S-03 SEC point-in-time PBR supply — 実測レポート

初回供給の開始 HEAD: `d1541f642e7322d687e7af7e78aeabcae6d2a21d`。品質強化カード `t_417ebaca` は `f97abbd` に積み上げた。ローカル実装フェーズの結果であり、CTOの独立受入承認を意味しない。push は行わない。供給なしの初回レポートは [S-03 GARP hybrid](BACKTEST_REPORT_S03_GARP_HYBRID.md)、供給後の結果は `backtest_results_s03_supplied.json`。

## 結論

SEC実取得から9株式に 15,258 行のPBRを供給し、S-03が full の69回すべてでPBRフィルタを使用した。momentum-only fallback は full/IS/OOS とも0回。価格入力・バックテストエンジン・凍結契約は変更していない。

(a) OOS Sharpe改善、(b) IS–OOS CAGRギャップ縮小、(d) full bootstrap CI下限 ≥ −15% は達成。(c) OOS DDは改善するがfull DDが悪化するため、**全基準達成ではなく、採用承認不可**。特にエンジンは生価格の分割損失を引き続き計上するため、これを実運用収益の改善と解釈しない。

## 再現手順

リポジトリ直下で実行:

```sh
PYTHONPATH=src .venv/bin/python scripts/fetch_sec_pbr.py
PYTHONPATH=src .venv/bin/python scripts/fetch_sec_pbr.py --offline
PYTHONPATH=src .venv/bin/python run_backtests_garp_momentum_hybrid.py --pbr-dir data/fundamentals/pbr --p3-reference /absolute/path/to/regenerated/backtest_results_p3.json
PYTHONPATH=src .venv/bin/python run_backtests_garp_momentum_hybrid.py --output /absolute/path/to/no_supply.json
make verify
```

初回のみSEC取得。ticker-mapとCIKごとのraw envelope（取得時刻、HTTP status、未切り詰めpayload）は `data/fundamentals/sec-raw/` に保存し、以後は既存snapshotを使用する。`--offline` は欠けたsnapshotがあると失敗し、ネットワークへ逃げない。HTTP404は欠損としてcacheし、403等は中断して返す。User-Agent/rateはSettings経由。既存 `.env` はコミットしない。

`--pbr-dir` は明示opt-in。指定なしなら、供給ストアが存在しても旧S-03出力が維持される。供給はGARP入力だけに適用し、A/Bのmomentum・EW参照は元の価格履歴のまま。新結果は `backtest_results_s03_supplied.json`、初回検証は `docs/S03_PBR_SUPPLY_VERIFICATION.json`、今回の品質強化検証は `docs/S03_PBR_HARDENING_VERIFICATION.json`。既存P3結果はstaleなので、P3 runnerを別作業ディレクトリへコピーして同じdataから実走し、その出力を `--p3-reference` に渡す（追跡済み旧JSONは上書きしない）。

## 保存契約とPIT

- 財務ストア: `data/fundamentals/pbr/{SYMBOL}.jsonl`、分割カレンダー `split-calendar.json`、schema v2来歴 `manifest.json`。既存 `ohlcv.jsonl` はread-only。`join_pbr` とrunnerは全sidecar（SPY空ファイルを含む）・分割カレンダー・raw snapshot・ticker-mapのSHA256をmanifestと照合し、不一致/欠損/旧schemaでfail closed。公開manifest自体の真正性を保証する署名ではない。
- 財務情報は各accession内の最新equity期末と同一提出のsharesから一括選択。別提出の株数を結合しない。original filingを未来のrestatementで置き換えない。
- `disclosure_date` は **filed**。非取引日提出も保持し、翌価格バー以降の供給に利用。`period_end` を公開日に流用しない。
- 各バーに当日まで公開済みの財務状態から、複数CIKをまたいで最新 **filed** の同一提出を選ぶ（同日tieは期末/accession、重複tieは候補順）。これは供給系列の構築であり、欠損フィールドの補完ではない。新提出の株数不足・非正equity等はその行のPBRをnullとし、古い有効値で穴埋めしない。
- runnerは **同じバー日付の財務行だけ** を結合。missing date/fieldはnull。宣言済みsidecarが消失した場合はnull化せずfail closed。元の価格辞書も変更しない。
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

現ticker-map `0002115436` の1提出と旧CIK `0000034088` の69提出を **filed基準で併合**。点数最大のCIKだけを採る旧heuristicを廃止した。2026-08-03の10-Qは新CIK snapshotにのみ存在し、accessionは元の `0000034088-26-000093` を保持する（accession prefixとsource CIKは一致するとは限らない）。2026-07-31は旧提出のまま、08-03〜09-28の40バーは新提出へ移行。08-03のequityは254,381,000,000→259,380,000,000、sharesは4,144,947,162→4,111,911,960、PBRは2.526586→2.458143。

`ISSUER_CIK_GROUPS` はレビュー済みの同一issuer lineage登録点。XOMの2026持株会社再編と上記accessionをコード内に根拠として明記した。mapが旧CIK/欠損でも両CIKを対象にし、未確認の第三CIKへの変化は中断。一般的なissuer-changeの自動探索や名前類似マージはしない。snapshot本体のCIKと取得endpointの一致も確認する。

### META

保存rawの全namespace（dei/us-gaap/srt/ecd/ffd）でshares候補を確認。common instantのdei/GAAPタグは不在。`PreferredStockSharesOutstanding`のinstant2点は普通株でないため使わず、diluted/basic weightedにはinstant点がない。同一accession・同一期末で最短の整合durationの `us-gaap:WeightedAverageNumberOfSharesOutstandingBasic` を採用。

**METAの株数は期中平均による近似であり、PITのinstant株数ではない**。公開時刻はPITで守るが、概念上の株数近似は解消されていない。sidecarに`shares_basis=period_average`を明示する。

## 分割実測

価格不連続の実日をeffective dateとし、前日close/当日openを主に、当日closeも整合確認する。価格と比率は正値・有限必須、欠損/0/負/NaN/Infはskip。SEC分割タグとの±15%一致、タグ期末と観測日の距離180日以内、観測日以後180日以内のfiledを必須にする。倍率はSECの正確な比率を使い、市場リターン込みの価格比は使わない。逆分割（比率<1）と端数比率を同じ式で扱う。観測close比・残余return・SEC初提出/accession/source CIKを保存。

| 銘柄 | 価格適用日 | 倍率 | 前close → 当close | 観測close比 | SEC初提出 | PBR前 → 当 |
|---|---|---:|---|---:|---|---|
| AAPL | 2020-08-31 | 4 | 499.23 → 129.04 | 3.868800 | 2020-10-30 | 29.530516 → 30.531962 |
| NVDA | 2021-07-20 | 4 | 751.19 → 186.12 | 4.036052 | 2021-08-20 | 24.927632 → 24.704966 |
| NVDA | 2024-06-10 | 10 | 1208.88 → 121.79 | 9.925938 | 2024-08-28 | 60.515339 → 60.966872 |
| GOOGL | 2022-07-18 | 20 | 2235.55 → 109.03 | 20.503990 | 2022-07-27 | 5.797931 → 5.655417 |
| AMZN | 2022-06-06 | 20 | 2447 → 124.79 | 19.608943 | 2022-07-29 | 9.289774 → 9.475038 |
| TSLA | 2020-08-31 | 5 | 2213.4 → 498.32 | 4.441724 | 2020-10-26 | 41.856220 → 47.117085 |
| TSLA | 2022-08-25 | 3 | 891.29 → 296.07 | 3.010403 | 2022-10-24 | 25.592245 → 25.503807 |

SECタグは現在 **事後の採用可否ゲートと正確な倍率の根拠**。適用日は価格の観測日を維持するが、未来のSEC確認に依存するため「ex-ante検出」や「未来情報を全く使わない系列」とは呼ばない。`sec_is_retrospective_eligibility_gate=true` を明示。財務equity/sharesのfiled制約は維持する。株数の観測日より後の市場分割だけを当日以降に乗じ、新しいinstant株数が提出されたら倍率をリセットする。

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

## 初回供給の検証と回帰（f97abbd時点）

- 専用オフラインテスト35 passed。分割日/倍率、TSLA intraday、GOOGL二重調整防止、週末filed、バー/disclosureのstrict境界、future-restatement、同一accession、null非補完、fallback、public raw、CIK入力、404 cache/403中断、ネットワーク禁止replay、XOM選択、元OHLCV不変を試験。
- `make verify` exit0: **1922 passed, 2 skipped, 1 warning in 381.11s (0:06:21)**。lint/format/mypy/OpenAPIもexit0。
- standalone式/出典照合: 15,258行のequity/shares値・accession・filed・日付・分割倍率と価格×株数/equityをrawから照合。CTOの独立オラクル受入の代替ではない。
- 初期data hash manifestの231既存ファイルすべてbyte不変。新規fundamentalsファイルだけを追加。OHLCVの金融nullも書き換えない。
- 実際のd1541f6コードを`git archive`で別workspaceへ展開して同じdataで実行し、P3/S-02/S-05/cryptoの4runner **全出力byte一致**。供給なしS-03もbyte一致し、最終変更後もう一度実行して一致を確認。
- P3 momentumのmetrics/status/sample-count/bootstrap/OOS/IS/window/sessions/trades/purgeの15項目は全一致。既存8signalのエンジン・凍結strategy JSONに差分なし。
- 注意: committed P3 JSONは以前のcoverage/assumptionsで、d1541f6自体の再実行とbyte一致しない。本検証は古いJSONを基準にせず、**d1541f6を実行した結果**と比較し、追跡済み旧結果JSONは元のまま維持した。
- raw/財務全ファイルのoffline再生成byte一致、供給あり結果2回のbyte一致を実測。検証JSONに主要hashを記録。

## 品質強化の実測（t_417ebaca）

- 専用テスト **85 passed in 1.44s**。CIK filed境界・来歴・異CIK株数非結合、逆/端数分割、非分割急変、古いSECタグ、0/負/欠損/NaN/Inf価格、ハッシュ改変/消失、絶対/脱出path、移送、分割タグparse回数を検証。
- `make verify` exit0: **1972 passed, 2 skipped, 1 warning in 372.33s**。lint/format/mypy/OpenAPIも通過。scripts/runnerも個別ruff検査exit0。
- 実rawから15,258行のequity/shares/accession/filedと価格×株数/equityを照合。XOM40行だけが変更し、他9sidecarはbyte不変。7分割の銘柄/日/倍率は一致（上表にAAPL 2020-08-31、NVDA 2021-07-20、TSLA 2020-08-31を含む）。分割タグのparseはbarループ外へ移動。
- `f97abbd`をgit archiveで別配置へ展開し実走したP3/S-02/S-05/cryptoと、供給なしS-03の **5runner出力がすべてbyte一致**。既存追跡結果JSON5ファイルも不変。初期hashの保護対象246ファイル、うちstock OHLCV14本/全OHLCV19本がbyte不変。
- 別配置へrawと価格をコピーし、SEC client構築禁止でoffline再生成して全供給ファイルbyte一致。供給ありrunnerも移送前後でbyte一致。さらに可搬archiveを新ディレクトリへ実際に展開してrunner再走、同じ結果byteを確認。
- 実runnerでXOM sidecar/SPY空sidecar/calendar/新CIK rawを各々改変し、4ケースすべてSHA256 mismatchでexit1、出力未作成を確認。
- 更新した `backtest_results_s03_supplied.json` は来歴と分割カレンダーを反映。XOMの値は修正されたが、この固定slotランキングでは取引/metricsは変更なし。上記(a)(b)(d)達成、(c)full未達、採用未承認の結論は維持。元GARPレポートへ完了・相互参照と実測要約を追記し、供給なしJSONは不変。

### 可搬性と保存物

`raw_snapshot` はPBRストア相対 `../sec-raw/CIK*.json`。`fundamentals/pbr` と `fundamentals/sec-raw` を一緒に移送する。旧絶対pathのschemaは受け付けず、現在のgeneratorで再生成する。

本カードのprivate成果物 `s03-pbr-portable-replay.tar.gz`（4,928,314 bytes、SHA256 `546c94c276002500f69a709782eeebf9a61051bcb949b9fe77ddf411e7cd6802`）はSEC raw、相対manifest/sidecar/calendar、実価格14本、再生成P3 reference、期待供給結果、`REPLAY.md` を含む。空の別ディレクトリへ展開し、同梱手順で再現できる。credentials/.envは含めない。価格/PBR/resultは`personal_only`であり公開配布へ昇格しない。git commit単体にはignored dataを含めないが、bundleとcommitを合わせたoffline再現を実測済み。

## 限界

PBRの株数単位は修正したが、エンジンのraw価格momentum・損益には分割不連続が残る。供給あり診断ではAMZNの2022-06-06分割直前（06-01決定）とGOOGLの2022-07-18分割直前（07-01決定）にそれぞれ20%のscheduled targetがあり、価格だけ約1/20になる窓に保有していた。full DDは生価格分割による人工的損失を含み、分割調整済みtotal-returnバックテストではない。エンジンを変更しないという契約を守った結果であり、このまま資金配分判断に用いない。

SEC確認必須でも、タグの日付はannouncement/会計期末であり厳密なaction dateではない。180日窓内の同率の別ショックを完全に識別するものではない。重複historicalタグで連続ショックを承認しないよう、同率イベントは180日内に2回採用しない（実際に短期間に同率分割を繰り返した場合は手動確認が必要）。小さな変化（open比の1からの差≤15%）は分割として採用しない。SECタグ不在/確認が180日超の場合も保守的に不採用で、完全なcorporate-action feedではない。今回の既存7イベントはすべて維持。株数は提出間の実際の買戻し/増資を反映せず、METAは期中平均近似。GAAP株数のpresentation-basis判定は同一提出の分割タグを根拠にするが、特殊な複数クラス再編すべてを解釈するものではない。

ユニバースは固定・現在銘柄由来、survivorship biasあり。filedは日付精度（提出時刻/取引終了時刻なし）で、decision境界のstrict < により同日利用をしない。配当、税、market impact等も旧契約のまま。bootstrapはIIDで時系列依存を扱わず、CIは予測区間でない。SEC snapshotを全CIKについて保存し再走可能だが、このignored dataをgitへ含めないため、別環境ではsnapshot別途移送か初回取得が必要。
