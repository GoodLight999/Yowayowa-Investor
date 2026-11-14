# S-07 復帰候補のオフライン検証実装（t_b92fb6b1）

2026-11-09。設計書 `docs/design/S07_REENTRY_DESIGN_20261109.md` §6 の受入試験のうち、
**オフラインで検証可能な契約だけ**を隔離モジュールとして実装した。実注文・取消・署名・
鍵生成・mainnet 送信・push は 0 件。停止標識
`scripts/hl_testnet_s07/s07_daily_forward.py` は未変更。

**データ種別: `synthetic`（合成）。本レポートの PASS は試験アサーションの PASS であり、
運用成績・実約定・実口座の証明ではない。**

## 1. 結論

- 新規隔離パッケージ `src/yowayowa/hl_exec/s07_offline/` と別入口
  `python -m yowayowa.hl_exec.s07_offline.runner` を追加した。既存の
  `hl_exec.guards`（前段 t_49064d09）を unknown-order 非再送の検証に使用した。
- 受入試験は 9 ID すべてを **PASS / SKIP / NOT-IMPLEMENTED** に分類した（§4）。
  オフライン試験は **57 passed**（合成データ）。
- 出力は `<明示tmp>/dry-run/<run_id>/` の `state.json` / `events.jsonl` /
  `report.json` / `report.md` だけに限定し、実台帳 `data/` とは物理分離した。
  模擬実台帳の inode/size/mtime/sha256 不変・open 試行 0 を試験で確認した。
- 通信遮断（pytest-socket `--disable-socket` + 独自 socket 拒否 fixture）で
  57 passed。遮断の positive control も同一実行内で PASS。
- 停止標識・実台帳に変更なし（§7 の diff・sha256・metadata 比較）。

## 2. 実装

| ファイル | 役割 |
|---|---|
| `s07_offline/manifest.py` | testnet/coin/source_url/data_kind/response hash の完全一致検証、確定足のみ抽出、UTC 日境界・1日長、重複/衝突/欠測、warmup と窓の分離（A01） |
| `s07_offline/isolation.py` | `dry-run/<run_id>/` の realpath・symlink・hardlink・inode・親子/相対 path 検査、`O_EXCL|O_NOFOLLOW` + 0700/0600 の OS 強制（A02） |
| `s07_offline/fills.py` | 個別 fill の数量/価格/fee 会計、重複排除、衝突拒否、窓外隔離、`NEW→PARTIAL→FILLED/CANCELED/REJECTED/UNKNOWN`、追記型訂正、ページ完全性、funding 被覆（A03/A05/A06） |
| `s07_offline/drawdown.py` | ピーク比 DD の候補計測（固定100分母を使わない）、窓外除外、日末頻度・日中非保証の明示（A04） |
| `s07_offline/evidence.py` | `events.jsonl` 逐次 flush+fsync、`report.json` 必須項目、書込失敗の非zero、秘密 redaction（A07） |
| `s07_offline/report.py` | JSON/Markdown 双方で `data_kind` 必須、`synthetic`/`mixed` の非公式表示（A08） |
| `s07_offline/criteria.py` | 8 / net>0 / 10% / 5bp / 28日窓の凍結値と比較演算子、窓端点の保留（A09） |
| `s07_offline/runner.py` | 上記を結合する dry-run 別入口（ネットワーク・署名器・注文クライアントを保持しない） |

再利用: `yowayowa.hl_exec.guards.send_order_once` / `reconcile_unknown_order` /
`canonicalize_testnet_url` 相当の境界契約、`orders.round_price` 等は今回の会計に不要なため
呼んでいない。旧 `rejected/*.txt` は実行コードへ戻していない。

## 3. 実行環境とログ

TSUKUMO-KUN の固有 snapshot（`~/vps-work/sessions/t_b92fb6b1/repo`、git 管理外コピー）。
共有 venv の再 sync は行っていない（既存 `t_f840d5bc/verify-venv` と `sdk-venv` を使用）。
環境: Python 3.14.7 / pytest 8.4.2 / hyperliquid-python-sdk 0.24.0
（`checks/s07_offline_20261109/environment.txt`）。遮断強制の実行だけは同じ
Python 3.14.7 上で pylibs の pytest 9.2.0 + pytest-socket 0.8.1 を併用した。

| 検証 | コマンド（要旨） | 結果 | ログ |
|---|---|---|---|
| S-07 オフライン | `pytest tests/test_s07_offline_*.py` | **57 passed in 1.64s** | `s07_plain.txt` |
| S-07 遮断強制 | 同上 + pytest 9.2 `--disable-socket` + socket 拒否 fixture | **57 passed in 2.11s**（positive control PASS） | `s07_strict_socket.txt` |
| HL 一式 + S-07 | `pytest tests/test_hl_exec_*.py tests/test_s07_offline_*.py` | **269 passed in 5.04s** | `hl_plus_s07_suite.txt` |
| lint | `ruff check src tests`（TSUKUMO snapshot） | All checks passed（exit 0） | `ruff_check.txt` |
| format | `ruff format --check src tests` | 502 files already formatted（exit 0） | `ruff_format.txt` |
| 型検査 | `mypy src/yowayowa` | Success: no issues found in 272 source files | `typecheck.txt` |
| 全体 suite | `pytest tests`（TSUKUMO snapshot） | **3770 passed / 3 failed / 15 skipped / 1 xfailed**（0:54:52）。3 失敗は未変更 HEAD でも同一再現する既存 baseline（§6）。 | `full_suite.txt` |

`make verify` 本体は `V=$(CURDIR)/.venv` を前提に bootstrap するため、共有 venv を
再 sync しない本カードの制約下では実行していない。同等の lint / typecheck / pytest を
固定 snapshot 上で個別に実測した。

## 4. 受入試験の判定

| ID / 要件 | 判定 | 理由 | 根拠 |
|---|---|---|---|
| S07-A01 / 1 | **PASS** | 確定足のみ指標入力。未確定・未来足を除外し理由/件数を記録。mainnet/未知/行内混在/偽装 URL/ラベル書換はバッチ拒否。UTC 境界・1日長・重複・欠測を検出し補間しない。 | `test_s07_offline_marketdata_synthetic.py`（15） |
| S07-A02 / 2 | **PASS** | 出力は明示 tmp の `dry-run/<run_id>/` のみ。same/parent/child、相対、`..`、symlink、hardlink、run root 再利用を拒否。模擬実台帳は不変・open 0。通信遮断 positive control あり。 | `test_s07_offline_execution_synthetic.py`（3） |
| S07-A03 / 3 | **PASS**（実データ真正性は **NOT-IMPLEMENTED**） | 個別 fee と符号付き funding を 1 回だけ適用し net を照合。リベート符号維持。通貨混在・欠損は INCONCLUSIVE/拒否。実口座の fill/funding 真正性は実データが必要で未実装。 | 同（4） |
| S07-A04 / 4 | **PASS**（公式判定は **SKIP**） | `[100,120,108]` をピーク 120 分母で 10%、固定100 の 12% を明示的に排除。窓外除外・日末頻度・日中非保証を表示。欠損/非正 peak/非有限/外部資金移動は INCONCLUSIVE。公式窓端点の裁定は保留。 | `test_s07_offline_reporting_synthetic.py`（4） |
| S07-A05 / 5 | **PASS** | 同一 duplicate は 1 回・件数/hash 記録、同一 ID 衝突は拒否、窓外は隔離して証跡保持。ページ飽和/最終ページ limit 一致は完全性不能。funding 欠測検出。訂正は出典付き追記のみで原本不変。 | `test_s07_offline_execution_synthetic.py`（5） |
| S07-A06 / 6 | **PASS** | 複数 partial fill の累計数量・残数量・平均価格・fee を検証。過剰約定拒否。取消は実約定を消さない。entry/exit 数量一致・建玉0・未約定0・照合済みのときだけ往復完了。timeout は guards 経由で照合し再送 0（送信回数 1）。 | 同（6） |
| S07-A07 / 7 | **PASS** | `events.jsonl` に逐次記録（fsync）、`report.json` に原因・入力 hash・retry 判断・未確定注文 ID を保存。report 書込拒否は exit 2 + stderr で、成功を装わない。秘密 redaction。UNKNOWN は retryable=false。 | 同（6） |
| S07-A08 / 8 | **PASS** | JSON/Markdown とも `data_kind` 必須。`synthetic`/`mixed` は非公式・非実績と明記。欠落/未知ラベル拒否。試験 PASS と `official_result=false` を分離。 | `test_s07_offline_reporting_synthetic.py`（8） |
| S07-A09 / 9 | **PASS**（公式裁定は **SKIP**） | 8 / 0 / 10% / 5bp の等号・不等号と 5 条件を凍結値のまま検証。8 未満は INCONCLUSIVE。11/03 行を窓外（warmup）へ除外。窓は `[start,end)` 候補とし、端点差は起票・公式保留。 | 同（5） |

SDK 境界（A06 の前提）は前段 t_49064d09 の guards が利用可能なため、その
`send_order_once` を直接使い、skip にはしていない。**実注文は 1 件も送っていない**
（fake exchange object のみ。送信回数 1、再送 0）。

合計 57 テスト（A01 15 + A02 3 + A03 4 + A04 4 + A05 5 + A06 6 + A07 6 + A08 8 + A09 5
= 56。残り 1 は通信遮断の positive control）。

## 5. 合成データの明示

- fixture は `tests/fixtures/s07_offline_synthetic/*_synthetic_*.json`。
- `report.json` / `report.md` は `data_kind` と合成バナーを必ず出力し、
  `official_result: false` を固定する。
- 本カードの PASS を運用実績として記述・転用してはならない。

## 6. 全体 suite

`pytest tests` を TSUKUMO snapshot 上で実行した（0:54:52）。

```
3 failed, 3770 passed, 15 skipped, 1 xfailed, 1 warning
```

失敗 3 件は次のとおりで、いずれも本カードの変更ファイル（`s07_offline/` と
`tests/test_s07_offline_*`）に触れない既存テストである。

- `tests/test_openapi_baseline.py::test_committed_baseline_matches_generated_document`
- `tests/test_paperloop_a_half_20bd.py::test_gate_thresholds_match_prereg_text`
- `tests/test_positions_constraint_sql_dialect.py::test_non_sqlite_alter_failure_is_fail_closed`

未変更 HEAD（`t_49064d09` の snapshot）で同じ 3 件を実行しても **3 failed** で
再現した（`baseline_failures_pristine_head.txt`）。したがって本カード由来の
回帰ではない。S-07 の 57 件と HL 一式 269 件はすべて PASS している。

## 7. 停止標識・実台帳の不変性

- 停止標識: `git diff -- scripts/hl_testnet_s07/s07_daily_forward.py` は空。
  sha256 は作業前後とも
  `88f5b3d6bdd2c8e830410c9b2eb095e7a0de91c01f3a558859f913eb8e872338`。
  証跡: `checks/s07_offline_20261109/stop_marker_and_diff.txt`。
- 実台帳 (`data/hl_testnet_s07`, `data/execution_logs`): ファイル一覧・inode・size・
  mtime の before/after 比較で差分 0 行（`ledger_meta.diff` は空）。
  本実装は `data/` を入力にも出力にも参照しない（`default_forbidden_roots` が拒否）。
- 実注文 / 取消 / 署名 / 鍵生成 / mainnet 送信 / push / cron 変更: 0 件。

## 8. 未検証・残課題（独立裁可へ）

- 実口座の `userFills` / `userFunding` / equity の真正性、部分約定の実発生、実 fee、
  実 funding、取消・決済の実挙動はオフラインで証明できない。**実装せず独立裁可へ**。
- 評価窓の閉区間/半開区間、DD の日末サンプリング、原典の正式定義は未確認のため、
  `official_verdict = HELD_PENDING_ADJUDICATION` のまま（既存の起票 t_75944344 /
  レビュー t_58ad2519）。
- 日次建玉0と自然な持越し/部分約定が矛盾する場合の強制決済は実装していない。
- 全体 suite の結果（§6）と、既知の baseline 失敗の切り分けは別カード領域。

## 9. 再現手順

```bash
# TSUKUMO-KUN の固定 snapshot 上（共有 venv は再 sync しない）
cd ~/vps-work/sessions/t_b92fb6b1/repo
V=~/vps-work/sessions/t_f840d5bc/verify-venv
S=~/vps-work/sessions/t_f840d5bc/sdk-venv
PYTHONPATH=src:$S/lib/python3.14/site-packages \
  $V/bin/python -m pytest tests/test_s07_offline_*.py

# 通信遮断を OS プラグインでも強制する場合
P=~/vps-work/sessions/t_49064d09/pylibs
PYTHONPATH=$P:src:$S/lib/python3.14/site-packages \
  $V/bin/python -m pytest tests/test_s07_offline_*.py --disable-socket

# 別入口（合成 fixture → dry-run/<run_id>/ のみ）
# --input は絶対パス必須。相対パスを渡すと
# S07_OFFLINE_ABORTED: IsolationError: input path must be absolute （exit 2）で拒否される。
# --out のベースディレクトリは事前に存在している必要があり、自動作成されない（fail closed）。
# 同じ run-id の再実行も「run root already exists」で拒否されるため、毎回新しいベースを作る。
OUT=$(mktemp -d /tmp/s07-offline.XXXXXX)
PYTHONPATH=src:$S/lib/python3.14/site-packages \
  $V/bin/python -m yowayowa.hl_exec.s07_offline.runner \
    --input "$PWD/tests/fixtures/s07_offline_synthetic/run_synthetic_A02_A07_A08.json" \
    --out "$OUT" --run-id synthetic-a02-a07-a08 \
    --repo-root "$PWD"
```
