# P4: EDINET 提出インデックスの既存ブートストラップ実走（0行 → 部分カバー）

- 日付: 2026-10-23 (JST) / 実行時刻 2026-10-23T13:27:36Z〜13:27:48Z（22:27 JST）
- カード: t_2e038963（承認済みキューからの自動供給カード）
- 出典: `docs/P5_DEADEND_AUDIT_20261023.md` 差分表 G1（未解消: `edinet_index_days` / `edinet_filings` とも 0行）
- 対象: 既存実装のみ（`src/yowayowa/edinet_index_db.py`、`src/yowayowa/services/edinet_index.py`、
  `api/app.py` の `/internal/cron/daily`）。**コード変更なし・新規取得器なし・SQL 直書きなし。**

## 1. 結論

G1 の「0行」は解消した。原因は実装欠備ではなく **鍵が実走プロセスの環境に渡っていなかったこと**で、
既存のブートストラップ経路（`maintain_recent_filing_index`）を鍵ありで1回実走させると正常に動作した。

- `edinet_filings`: **0 → 6,164 行**
- `edinet_index_days`: **0 → 31 行**（2026-09-22〜2026-10-22・新しい順に31日）
- `coverage_complete=false`・`indexed_days=31 / expected_days=550`。**部分カバーを完全履歴として提示していない**
  （roadmap P4 の stale/incomplete coverage 明示の要件を満たす表現であることを API 応答で実測）。
- 失敗 0 件、所要 12.24 秒、EDINET へのリクエストは **31日分＝31回**（文書化された1回31リクエストの枠内）。

## 2. 実行前後の実測（同一 DB・同一経路）

実行対象 DB: `sqlite:///./data/yowayowa.db`（`/root/current-work/yowayowa-investor/data/yowayowa.db`、
`yowayowa-api.service` が使う本番と同じファイル）。

| 指標 | 実行前 | 実行後 |
|---|---|---|
| `edinet_filings` 行数 | 0 | 6,164 |
| `edinet_index_days` 行数 | 0 | 31 |
| 同期済み日の範囲 | — | 2026-09-22 〜 2026-10-22 |
| 提出日の範囲 | — | 2026-09-24 〜 2026-10-22 |
| `indexed_at` / `fetched_at`（provenance） | — | 2026-10-23 13:27:36.65Z 〜 13:27:48.29Z |
| `coverage_complete` | false | **false**（31/550） |

ランナーの生出力（実行証跡）:

```
[runner] mode=personal database_url=sqlite:///./data/yowayowa.db
[runner] key_present=True
[runner] lookback_days=550 network_day_budget=31
[runner] before={'edinet_filings_rows': 0, 'edinet_index_days_rows': 0, ...全て None}
[runner] elapsed_seconds=12.24
[runner] result target=2025-04-21..2026-10-22 days_attempted=31 days_synced=31
         documents_upserted=6187 indexed_days=31/550 coverage_complete=False failures=0
[runner] after={'edinet_filings_rows': 6164, 'edinet_index_days_rows': 31,
         'index_day_min': '2026-09-22', 'index_day_max': '2026-10-22',
         'filing_date_min': '2026-09-24', 'filing_date_max': '2026-10-22',
         'indexed_at_min': '2026-10-23 13:27:36.652818',
         'fetched_at_max': '2026-10-23 13:27:48.293980'}
```

内容の実測（読み取り専用照会）:

- 提出 6,164 行 / 一意 doc_id 6,164 / 銘柄コード 1,196 種 / CSV あり 5,374 行（XBRL ありも同数）
- 同期31日のうち **提出0件の日が11日**（土日・祝日）。`edinet_index_days` には 0 件でも行が残るので、
  「0件の日」と「未取得の日」は区別できる（`docs/EDINET.md` の設計どおり）。
- 31日分の EDINET 側総件数 `source_total_count` 合計 6,199 / ローカル取り込み `indexed_count` 合計 6,187。

## 3. 実行方法（再現手順・既存経路のみ）

`/internal/cron/daily` が呼ぶのと同じサービス関数を、本番 cron（`p4b-edinet-daily-index`）と同じ引数で1回呼ぶ。
`maintain_recent_filing_index` は「新しい順・未取得日のみ・1回あたり最大 network_day_budget 日」を守る。

```python
# 既存 Settings のまま（YOWAYOWA_EDINET_API_KEY を環境に渡すだけ。値は表示しない）
from yowayowa.config import get_settings
from yowayowa.db import get_session, init_database
from yowayowa.providers.registry import edinet_client
from yowayowa.services.edinet_index import maintain_recent_filing_index

s = get_settings(); init_database(s)
with get_session() as session:
    r = maintain_recent_filing_index(
        session, edinet_client(),
        lookback_days=s.edinet_index_lookback_days,        # 550
        network_day_budget=s.edinet_index_backfill_days_per_run,  # 31
    )
```

鍵は既存 collector（`/root/.hermes/scripts/yowayowa_edinet_daily.sh`）と同じ運用で、
運用鍵ファイルから **読み取りのみ** で取得し、上記の既存フィールドへ渡した。鍵の値は出力・成果物・ログのどこにも書いていない。

実行コマンド（スクラッチのランナー、`src` を PYTHONPATH にして `python` 実行）:

```
cd /root/current-work/yowayowa-investor
.venv/bin/python <scratch>/p4_edinet_bootstrap_run.py
```

## 4. 上限（31リクエスト）内であることの根拠

- 1日分の同期 = `document_list(client, filing_date, downloadable_only=True)` の **1リクエスト**
  （`EdinetClient.documents` は type=2 を1回だけ叩く。ページ送りなし）。
- 今回の1回の実行で試行した日数は `days_attempted=31`、成功 `days_synced=31`、失敗 0。
  → **EDINET への文書リスト要求は31回のみ**。`settings.edinet_index_backfill_days_per_run=31` の上限と一致。
- 実行は1回だけ。**同日にこのブートストラップを繰り返していない。**
- 別系統の `yowayowa-edinet-daily`（JSONL 取得・1日1秒間隔・5営業日）は**触っていない**。
- 実測ペース: 31リクエスト / 12.24 秒。既存の本番経路（`maintain_recent_filing_index`）は
  リクエスト間スリープを持たない実装であり、**こちらで間隔を短くする変更はしていない**（実装そのまま）。
  EDINET から 429・エラーエンベロープは返らなかった（失敗0件）。
  より厳しい間隔を運用要件にする場合は実装変更になるため、本カードでは扱わない（下記 §7）。

## 5. 部分カバーの表現（「完全履歴」と偽らないことの実測）

`GET /v1/filings/edinet/index/history?security_code=7203&start_date=2026-09-01&end_date=2026-10-22`
（稼働中 `yowayowa-api.service` :8001、読み取りのみ）の実応答:

```
"matched_count": 1, "indexed_days": 31, "expected_days": 52,
"coverage_complete": false, "index_start": "2026-09-22", "index_end": "2026-10-22"
```

- 中身も実データ: `S100Z7HA` / トヨタ自動車株式会社 / 自己株券買付状況報告書 / 提出 2026-10-13 / csv_available=true。
- 要求期間 52日のうち同期済みは 31日なので `coverage_complete=false`、`index_start`/`index_end` で実際の範囲を示す。
  → **部分カバーが完全履歴として提示されない**ことを応答で確認した。
- 逆方向も実測: 同期済み範囲に収まる要求（`security_code=6758` / 2026-09-22〜2026-10-22 = 31日）では
  `indexed_days=31, expected_days=31, coverage_complete=true`（ソニーグループ `S100Z81E` / 自己株券買付状況報告書 /
  提出 2026-10-14 / csv_available=true）。**同期済み窓の中では完全、外へ出ると不完全**と、要求期間に応じて正しく切り替わる。

## 6. 実測で分かった注意点（既存挙動・変更していない）

1. **`source_total_count` と `indexed_count` の差 12件** は、`document_list` の既存フィルタ
   （`downloadable_only=True`: `withdrawalStatus` 立っている文書と `legalStatus` が {1,2,None} 以外を除外）によるもの。
   差が出た日は 8日（例: 2026-09-30 は 590→589、2026-10-20 は 197→194）。EDINET 側の総件数は保持しているので乖離は監査可能。
2. **upsert 6,187 に対し格納は 6,164 行**（差 23）。主キーが `doc_id` で、同じ文書IDが複数の日次リストに現れると
   後から同期した日へ `filing_date` が移る既存挙動（`_apply_summary`）による。同じ理由で
   「その日の `indexed_count`」と「その日に格納されている行数」が一致しない日がある（例: 2026-10-16 は 228 対 221）。
   いずれも本実行で作った挙動ではなく、`sync_filing_day` の既存仕様。
3. 戦略層の「最新通期」判定は、当該提出日から直近完了日までの全カレンダー日が `edinet_index_days` に揃うときだけ
   最新とみなす（`docs/EDINET.md`）。現状は 31/550 の部分カバーなので、**古い通期を最新として使わない側に倒れる**（fail-closed）。

## 7. 0行だった原因（実測・要対応）

`p4b-edinet-daily-index`（cto-hephaestus、毎日 06:30 JST、29回実行、`last_status=ok`）は、
`YOWAYOWA_EDINET_API_KEY` がプロセス環境に無いため **毎回 skip して exit 0** していた。

```
/root/.hermes/profiles/cto-hephaestus/cron/output/ff396d46901f/2026-10-23_06-30-53.md
[edinet] skip: YOWAYOWA_EDINET_API_KEY is not set (register the key to enable)
```

- 鍵自体は運用鍵ファイルに存在する（`EDINET_API_KEY`、既存の `yowayowa-edinet-daily` が同じファイルから読んで動作している）。
- `yowayowa-api.service` の unit にも `YOWAYOWA_EDINET_API_KEY` が無いため、`/internal/cron/daily` の EDINET 分岐も実行されない。
- 「ok で skip」は失敗として表面化しない（無言の未実行）。ここは本カードの範囲外の制御系変更（cron 環境・unit）なので、
  後続カードに切り出した（§8）。**本カードでは cron・unit・設定を変更していない。**

## 8. 残り（部分カバーを完全にする道）

- 550日カバーには残り **519日**。1実行31日上限なので **追加17回**の実行が必要（1日1回ずつ、既存の日次メンテで進む設計）。
  ただし §7 のとおり現状の日次ジョブは skip するため、鍵が渡るようになるまで自動では進まない。
- 後続カード: **t_2c4b6154**（`p4b-edinet-daily-index` / `/internal/cron/daily` に鍵を渡す・parent=t_2e038963）。
  MODEL_MODE の制約で cto-hephaestus を直接 assignee にできないため worker-flash で起票し、制御系の書き込みが
  役割ガードで止まった場合は cto-hephaestus へレビュー引き渡す条件を本文に書いた。
- 手動で進めたい場合の最短手順は §3 の1回実行を日を変えて繰り返すこと（1日31リクエスト上限を守る）。

## 9. 触っていないもの

- `src/yowayowa/api/app.py`（他カードと競合するため変更なし。既存関数の呼び出しのみ）
- `src/yowayowa/edinet_index_db.py` / `services/edinet_index.py`（コード変更なし）
- `yowayowa-edinet-daily`（JSONL 取得経路・取得日付の扱い）、cron ジョブ定義、systemd unit
- DB への書き込みは `sync_filing_day`（ORM）経由のみ。SQL の直接実行・直接更新なし（計測は `-readonly` の SELECT のみ）
