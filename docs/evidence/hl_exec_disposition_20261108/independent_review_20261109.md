# hl_exec 4成果物の独立レビュー（independent_review_20261109）

- レビュー担当: audit-glm-heavy（監査役・実装者4名〔worker-flash / worker-go / worker-terra 系〕と別系統）。カード `t_58ad2519`。
- 実施日時: 2026-11-09 11:20〜12:10 JST。
- 手法: 実装者申告を前提とせず、VPS の実ファイル・git diff・生ログの直接計測と、TSUKUMO に構築した**独自隔離環境での再実行**のみで判定した。本レビュー中の実注文・取消・署名・鍵読出・mainnet送信・push は 0。`/info` は署名不要の読み取り専用照会のみ（COO裁定 2026-11-09 許容範囲・公開アドレス1経路）。
- 対象（前段4カード）:
  1. `t_687752d7` cron/wrapper 現況確認レポート `cron_state_audit_20261109.md`
  2. `t_49064d09` SDK境界是正 `sdk_boundary_fix_20261109.md` + commit `424b557`
  3. `t_440529c8` S-07復帰設計書 `docs/design/S07_REENTRY_DESIGN_20261109.md`
  4. `t_b92fb6b1` オフライン受入実装 `s07_offline_impl_20261109.md` + commit `c323d68`

## 総合判定

| # | 成果物 | 判定 | 要旨 |
|---|---|---|---|
| 1 | cron/wrapper 現況監査 | **承認（合格）** | 主要申告の全て（ハッシュ3点・jobs.json・実行0・台帳不変・口座不変・他profile参照0）を私の独立実測で再現 |
| 2 | SDK境界是正（guards） | **承認（合格）** | コード・negative case・AST非再送証明を直接確認。遮断下 77/77・HL一式+S-07 269/269 を私の環境で再現 |
| 3 | S-07復帰設計書・受入表 | **承認（合格）** | 要件1〜9とA01〜A09が1:1。窓・5条件は原典と一致し変更なし。矛盾の開示は原典で裏付け |
| 4 | s07_offline 実装 | **承認（合格・オフライン契約の範囲で）** | 57/57（通常・遮断）を私の環境で再現。dry-run分離・合成ラベル・fail-closed を実走行で実証 |

**S-07 復帰可否（COO要求の3択）: まだ馴目（不足Xを満たせば可）。**
停止標識 `s07_daily_forward.py` の解除・cron resume は**本レビュー単独では決めない。別途裁可が必要**（本カード本文の指定どおり）。

## 1. 独立実測による申告の裏付け（全て私の計測）

| 申告 | 私の実測（2026-11-09） | 判定 |
|---|---|---|
| wrapper sha256 `aca5a4ce…` | `aca5a4cefd241ca28ef75805322cbbbdc47d3f1e79bfdf948f977ad7830a4b1d` | 一致 |
| 停止標識 sha256 `88f5b3d6…` | `88f5b3d6bdd2c8e830410c9b2eb095e7a0de91c01f3a558859f913eb8e872338`（中身は `import sys` のみ・627B・argv無視・exit 2） | 一致 |
| スコアカード sha256 `9de1627f…` | `9de1627f5d31986f659a61fef1ceac5523fd0843fe009caaf99297e8a77d32a0` | 一致 |
| jobs.json 2172行目に `12f8248e71a5` 実在・paused | grep で一致行・`enabled=false/state=paused/paused_at=2026-11-09T08:03:47/last_run_at=2026-11-08T09:05:52/last_status=ok/failure_streak=0/no_agent=true` | 一致 |
| cron出力は11-04〜11-08の5件のみ | `/root/.hermes/cron/output/12f8248e71a5/` に5ファイル、11-09 なし | 一致 |
| 停止標識配置後の台帳書込0 | `data/hl_testnet_s07` `data/execution_logs` 配下で 2026-11-08 10:00 以降の更新ファイル **0件**（全 mtime ≤ 11-08 09:05:52） | 一致 |
| 他5profileの jobs.json 参照0件 | cto-hephaestus / worker-goat / worker-flash / worker-go / worker-free すべて grep 0件 | 一致 |
| 口座 accountValue=99.790224・建玉0・未約0 | 私の `/info` 読取（11:54 JST）: accountValue 99.790224・`assetPositions:[]`・`openOrders:[]` — 09:23 JST の監査時点と同一 | 一致 |
| userFills 16件・entry/exit OID 6組がスコアカードと一致 | 私の読取: 16レコード・一意OID 12個が申告の12 OID と完全一致・最新 time=1794096349690（11-08 09:05:49 JST）以降の新約定なし | 一致 |
| 共有venv未再sync | `t_f840d5bc/verify-venv` `sdk-venv` の mtime が 2026-11-08 のまま（11-09 の再構成なし） | 裏付け |
| コミットは push されていない | `agent/commercial-foundation` は `ahead 133`、`424b557` `c323d68` を含む未push状態 | 一致 |
| 変更ファイルが申告どおり | `git show --stat` で両コミットの変更対象が申告リストと完全一致（余分なし・`s07_daily_forward.py` は両コミットに不含） | 一致 |

報告書の記載と実測の**食い違いは検出されなかった**。

## 2. SDK境界是正（t_49064d09）の検証

- `guards/urls.py`: 正規化9規則どおりの完全一致 allowlist（大文字ホスト・末尾ドット・`:8443`・userinfo・`/evil`・`?`/`#`・非ASCII・`\` を全て拒否）。`assert_testnet_exchange` は `base_url` 属性なしの注入物を「照合不能」として生成時拒否 — `client.py` diff で旧「属性なし許容」から fail-closed へ変更されたことを確認。
- `guards/cloid.py`: `^0x[0-9a-fA-F]{32}$`・小文字正規化・`str()`/`to_raw()` 往復再検証・公式型以外受け付けない逆変換。
- `guards/unknown_order.py`: `send_order_once` は `exchange.order` 呼び出し1回のみ・`for`/`while` なし（本文読取）。timeout/不明時は open orders / orderStatus 照合の3値（confirmed/absent/unresolved）で、unresolved は `UnknownOrderStateError` で上位へ。**再送経路は存在しない。**
- テスト二重証明: ASTテスト `test_send_order_once_has_no_retry_loop_and_single_transport_call`（L220: ast.parse で order 呼び出し1件・ループ0・cloid 引数が変数）を確認。negative case は suffix/prefix 攻撃等が実在（collected 43+34=77 と `collected_boundary_tests.txt` 一致）。
- RED→GREEN ログの真正性: `red_before_guards.txt`（両ファイル collection error・`No module named 'yowayowa.hl_exec.guards'` のトレースバック実在）、`red_before_client_fix.txt`（6 failed / 71 passed・失敗名は申告6件と一致）、`green_boundary.txt`（77 passed）。環境ログ（TSUKUMO/Python3.14.7/pytest8.4.2/pytest-socket0.8.1/SDK0.24.0）も整合。
- **私の環境での再現（下記§6）: 境界 77/77（`--disable-socket`・通常の両方）、HL一式+S-07 269/269（`--disable-socket`）。**

## 3. S-07復帰設計書（t_440529c8）の検証

- 受入表は S07-A01〜A09 の9行で要件1〜9と1:1（§6表を直接確認）。
- 凍結基準の不変を原典と突合: 原典 `S07_TESTNET_FORWARD_EVALUATION.md:6` の「2026-11-04 00:00 UTC〜2026-12-02 00:00 UTC（28日間）」・5条件（8往復・net>0・MaxDD≤10%・slippage中央値≤5bp・日次建玉0/ロック/未約定0）・8未満 INCONCLUSIVE（原典:7）が、設計書§1と `s07_offline/criteria.py` の定数（`WINDOW_START_UTC=11-04`/`WINDOW_END_UTC=12-02`/`WINDOW_DAYS=28`/`10`/`5`/net>0）で一致。**期間・成績基準の変更なし。**
- 矛盾の開示（11/03取引は窓外）は原典:38 の実取引 #1 が実日付 2026-11-03 であることで裏付け。これは私の前回裁定（`t_75944344` → `docs/evidence/S07_WINDOW_DD_ADJUDICATION_20261109.md`: 窓端点は4原典一致・レポート矛盾の正体は窓開始前スナップショット・旧DDは固定分母100）と整合する。
- 設計書は「候補の明確化であって承認ではない」旨を明記し、DD・窓端点の公式裁定を保留（`WINDOW_SEMANTICS="half_open_candidate"` / `SOURCE_CONFIRMED=False`）— 恣意的確定をしていない。

## 4. s07_offline 実装（t_b92fb6b1）の検証

- `git show --stat c323d68`: 変更は `src/yowayowa/hl_exec/s07_offline/` 9ファイル・テスト3ファイル・fixtures・証跡のみ。実行経路（`client.py` 等）への追加変更なし。
- 合成データのラベル明示: fixtures は `*_synthetic_*`、`report.json` に `"data_kind": "synthetic"`、`report.md` に「SYNTHETIC DATA / 合成データ」バナーと `official_result: false` — **私の実走行で出力を確認**。
- dry-run分離の実証（私の実走行）: 別入口 `python -m yowayowa.hl_exec.s07_offline.runner` を合成fixtureで実行 → 出力は明示out直下 `dry-run/<run_id>/` の `state.json`/`events.jsonl`/`report.json`/`report.md` 4ファイルのみ。**相対パス指定の入力は `IsolationError` で exit 2（fail-closed 実測）**。監査コピーには実 `data/` を同梱していませんでしたが、`isolation.py` の `default_forbidden_roots` が `data/` 全体を realpath/リンク/inode で拒否する構造も確認。
- 停止標識・実台帳の無変更: 停止標識 sha256 不変（§1表）・実装者の `ledger_meta.diff` 空に加え、VPS 側実台帳の mtime 計測でも 11-08 09:05 以降 0 件（§1表）。
- 台帳比較メタ（`ledger_meta_before/after.tsv`）: 5ファイルの inode/size/mtime が完全一致することを私が diff で確認。

## 5. A01〜A09 の独立判定（実装者分類の鵜呑みなし）

私の判定は「コード・テストの実在確認＋私の環境での再実行」に基づく。全項目オフライン契約としての PASS を独立確認した。

| ID | 私の判定 | SKIP/NOT-IMPL の扱い | 復帰可否への効き |
|---|---|---|---|
| A01 manifest/確定足 | **PASS**（marketdata 15件） | なし | 効かない（実データ由来のtestnet保証は実取得時にA03系で担保） |
| A02 dry-run/台帳分離 | **PASS**（3件） | ファイルロックAPI不在＝構成上0（将来runnerの要件） | **効く（実装要件として）**: 本番runnerにはロック取得/解放記録が必要 |
| A03 fee/funding会計 | **PASS**（計算契約4件） | **実データ真正性 NOT-IMPLEMENTED** | **効く（必須ギャップ）**: 実fill/funding/equity の検証は未実装・未実施 |
| A04 ピーク比DD | **PASS**（候補契約4件） | **公式窓/DD裁定 SKIP/HELD** | **効く**: 公式裁定が確定しない限り成績確定不可（裁定資料は t_75944344 で作成済・裁可待ち） |
| A05 重複/完全性 | **PASS**（5件） | なし | 効かない（実運用のページング完全性は実データで再確認要だが契約あり） |
| A06 部分約定・非再送 | **PASS**（6件・guards経由・送信回数1） | 実部分約定の発生検証は不可（オフライン限界） | 効かない（初回実運用時の監視要件に繰り越し） |
| A07 失敗証跡 | **PASS**（6件） | なし | 効かない |
| A08 data_kind表示 | **PASS**（8件＋私の実走行で出力確認） | なし | 効かない |
| A09 凍結値判定 | **PASS**（5件・凍結値の不変を原典突合で確認） | **公式裁定 SKIP/HELD**（A04と同一） | **効く** |

### 復帰可否の結論（3択）: **まだ馴目（不足Xを満たせば可）**

不足 X（この順で埋まれば「復帰させてよい」に更新できる）:

- **X1. 実データ検証の実装と実施**（A03のNOT-IMPLEMENTED解消）: 同一口座の実 `userFills`/`userFunding`/equity と実台帳の照合を read-only `/info` の範囲で実施し、s07_offline のハーネスに実データ経路を通す。
- **X2. 窓端点・DDサンプリングの公式裁定の確定反映**（A04/A09のSKIP解消）: `S07_WINDOW_DD_ADJUDICATION_20261109.md`（t_75944344）を裁可し、`criteria.py` の `WINDOW_SEMANTICS_SOURCE_CONFIRMED` を確定させる。
- **X3. 本番代替runner（送信経路）の実装・受入・独立レビュー**: s07_offline は受入検証ハーネスであり、日常発注を行う実運用入口はまだ存在しない。設計§5の「独立裁可前に送信経路を組み込まない」制約どおり、未実装のまま。
- **X4. 停止標識解除・cron resume の別途裁可**: 本レビューの判定とは独立に、マスター/COO の裁可と `HL_EXEC_DISPOSITION_20261108.md` の手続を要する（本レビュー単独では決めない）。

## 6. 私の検証環境と再実行結果（TSUKUMO-KUN）

- 素材: VPS の `git archive c323d68`（sha256 `edbd1a41d08ecea4e6c6a044ce61ac612b4e1a6cdc961c372bec01be0e625977`・転送先で照合一致）を TSUKUMO `~/vps-work/sessions/t_58ad2519_audit/repo` に展開（実装者snapshotを不使用・`data/` 不含）。
- 隔離venv `.venv-iso`: Python 3.14.7 / pytest 8.4.2 / pytest-socket 0.8.1 / hyperliquid-python-sdk 0.24.0（実装者申告と同バージョンを新規pip調達）。
- 結果（ログ: `checks/independent_review_20261109/run1..5_*.txt`）:

| 実行 | 対象 | モード | 私の実測 |
|---|---|---|---|
| run1 | s07_offline 3ファイル | 通常 | **57テスト・失敗0・RC=0** |
| run2 | s07_offline 3ファイル | `--disable-socket` | **57テスト・失敗0・RC=0**（positive control 含む） |
| run3 | SDK境界2ファイル | `--disable-socket` | **77テスト・失敗0・RC=0** |
| run4 | SDK境界2ファイル | 通常 | **77テスト・失敗0・RC=0** |
| run5 | HL一式+S-07 | `--disable-socket` | **269テスト・失敗0・RC=0** |
| run6 | 別入口runner（合成fixture） | dry-run隔離 | exit 0・出力4ファイルのみ・`data_kind=synthetic`・`official_result: false`・相対入力は拒否 |

注: pytest の件数サマリ行がパイプ出力で欠落したため、件数はプログレス行のドット数（57/77/269）と exit code 0・`F` 文字不在で判定した（ログ原文のまま保存）。

## 7. 実注文・署名・鍵・送信・push の痕跡確認

- 私の `/info` 独立照会で 11-08 09:05:49 JST 以降の新約定なし（userFills 最新 time 不変）・建玉0・未約0 — 停止後に誰も発注していない。
- 口座値が 09:23監査時点と 11:54私計測で完全一致（accountValue 99.790224）。
- 両コミットは未push（ahead 133）・cron設定変更なし（§1表）・鍵・秘密ファイルの読取痕跡なし（本レビューでも鍵類は未開封）。

## 8. 未検証項目（明示）

1. **全体 suite（3770 passed/3 failed）は私の環境で未再現**（所要~55分・COO要求対象外）。構造的裏付けとして、3失敗の対象テストファイルは `c323d68` の変更対象外であることを `git show --stat` で確認済み。実装者の pristine HEAD 再現ログ（`baseline_failures_pristine_head.txt`）とも矛盾なし。
2. mypy/ruff の私環境での再実行は未実施（実装者ログ `typecheck.txt` 等）。
3. `/info` の `userFunding`・`extraAgents` の再照会は未実施（clearinghouseState/openOrders/userFills の3種で口座不変を確認済み）。
4. cron 発火ゼロの保証は監査時点（11:25 JST）までの観測。以後の発火有無は本レビューの保証範囲外（pause 状態のままなら継続しない）。

## 9. 差戻し・指摘事項（起票）

重大度「高」「中」の差戻し事項は**存在しない**。以下の低重要度2件を起票した（本レビューの合格判定を覆すものではない）:

1. **[低] 再現手順の記載不備**: `s07_offline_impl_20261109.md` §9 の `--input tests/fixtures/...`（相対パス）は実際には `IsolationError: input path must be absolute` で失敗する。絶対パス必須と明記するかコマンドを修正（`cli_demo.txt` の実績は `/tmp` 出力で成功しているため記載のみの問題）。→ 起票（worker-go）
2. **[低] 証跡ファイルが未コミット**: `cron_state_audit_20261109.md`・`docs/design/S07_REENTRY_DESIGN_20261109.md`・`docs/evidence/S07_{WINDOW_DD,CIO_ADJUDICATION}*_20261109.md` が untracked。消失リスクがあり、証跡の永続性のためコミットを推奨。→ 起票（worker-luna・集約時）

なお、TSUKUMO の私の検証ディレクトリ `~/vps-work/sessions/t_58ad2519_audit/`（約401MB・venv込み）と `~/vps-work/sessions/repo_c323d68.tar.gz`（4.3MB）は、削除用 `rm -rf` が無人モードの承認ガードで拒否されたため残置している。ログは VPS 側 `checks/independent_review_20261109/` に退避済み。マスター/運用担当が `ssh tsukumo 'rm -rf ~/vps-work/sessions/t_58ad2519_audit ~/vps-work/sessions/repo_c323d68.tar.gz'` で削除してよい（ディスク99%使用のため早期推奨）。

## 10. 結論の再掲

- 4成果物: **すべて承認（合格）**。レポートと diff・実データ・実測の食い違いは検出されず、主要な数値申告は私の独立計測で再現された。
- S-07 復帰: **まだ馴目**（X1 実データ検証・X2 公式裁定確定・X3 本番runner実装と受入・X4 別途裁可を満たせば可）。
- 停止標識の解除・cron resume は**別途裁可が必要**（本レビュー単独では決定しない）。
