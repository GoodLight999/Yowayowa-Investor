# cron_state_audit_20261109 — 外部cron wrapperと既存ジョブ `12f8248e71a5` の読み取り専用現況確認

- 作成: 2026-11-09 09:25 JST / worker-flash / kanban `t_687752d7`（親: `t_38d18e3e`・独立レビュー: `t_58ad2519`）
- 対象: `/root/.hermes/scripts/s07_testnet_daily.sh`（外部cron wrapper）と default profile cron job `12f8248e71a5`（s07-testnet-forward-daily）
- 本書の区分: 「1. 確認した事実」（実コマンド出力に基づく）／「2. 確認できなかった事実」／「3. 推測」を厳密に分離する。

## 0. 調査の前提と順守（受入条件「実行ゼロ」「鍵非接触」の宣言）

- **wrapper・停止標識の実行はゼロ**。`s07_testnet_daily.sh` および `s07_daily_forward.py` を bash/sh で実行していない。source していない。cron 経由のトリガー（発火・再実行・`cron run` 手動実行）も行っていない。本調査で使用したのは読み取り系コマンドのみ（§5）。
- **鍵・シークレット非接触**。`/root/.secrets/` 配下、リポジトリ直下 `.env` / `.env.example` は**中身を開いていない**（存在確認はファイル名のみ・§1.7）。復号・環境変数への展開は行っていない。本調査中に秘匿値は一切取得・表示していないため、**マスク対象はゼロ**。
- **実注文/取消/署名/mainnet送信はゼロ**。取引所照会は署名不要の公式読み取り専用エンドポイント `POST /info` のみ（§1.6）。
- **他profileのcron設定は編集していない**（変更系操作はそもそも実行していない）。
- 本成果物は `t_58ad2519`（独立レビュー・audit-glm-heavy）の入力となる。

---

## 1. 確認した事実

### 1.1 wrapper 本体（版固定）

- パス: `/root/.hermes/scripts/s07_testnet_daily.sh`
- sha256: `aca5a4cefd241ca28ef75805322cbbbdc47d3f1e79bfdf948f977ad7830a4b1d`
- mtime: `2026-11-03 15:22:01.915444660 +0900`（サイズ 510 バイト・7行）
- 本文（cat の全量・無加工）:

```bash
#!/bin/bash
# S-07 Hyperliquid Testnet Forward Execution daily batch (09:05 JST = 00:05 UTC)
# Trend+funding signal, order -> fill -> settle, 5-condition evaluation, daily ledger update.
set -u
cd /root/current-work/yowayowa-investor || exit 1
export PYTHONPATH="/root/current-work/yowayowa-investor:/root/current-work/yowayowa-investor/src:/root/current-work/yowayowa-investor/scripts/hl_testnet_s07"
exec .venv/bin/python /root/current-work/yowayowa-investor/scripts/hl_testnet_s07/s07_daily_forward.py "$@"
```

- **想定実行主体（事実）**: default profile（`HERMES_HOME=/root/.hermes`）の cron ジョブ `12f8248e71a5` が no_agent モードでこのスクリプトを起動する設計（§1.2 の `script` / `no_agent` / `workdir` フィールド）。wrapper 自体は profile 名を保持しないため、「どのprofileから呼ばれるか」はジョブ登録（§1.2）から確定した。
- wrapper の命令列挙（コードパス論証の入力）: ① shebang ② コメント 3行 ③ `set -u` ④ `cd <repo> || exit 1` ⑤ `export PYTHONPATH=...`（プロセス局所） ⑥ `exec .venv/bin/python <絶対パス>/s07_daily_forward.py "$@"`。**他に一切の命令がない**（ネットワーク・ファイル書込・ロック・署名・Telegram 送信・子プロセス起動はコード上存在しない）。

### 1.2 cron job `12f8248e71a5` の現行実在（断定）

**断定: 現行の一覧に同一ID `12f8248e71a5` で実在する。別IDへの置換・削除は発生していない。**

- 現行 `/root/.hermes/cron/jobs.json` **2172行目**に実在（`grep -n -B2 -A45 '"id": "12f8248e71a5"'` で取得・下記に生値）。履歴バックアップ `jobs.json.bak-t83ee6281-20261108-013423`（2163行目）にも同一IDが存在し、IDは維持されている。
- 公式 CLI の読み取り一覧でも実在・`[paused]` 表示（2026-11-09 09:21 JST 実測）:

```
$ hermes -p default cron list --all   (該当部抜粋)
  12f8248e71a5 [paused]
    Name:      s07-testnet-forward-daily
    Script:    s07_testnet_daily.sh
```

- jobs.json から取得した全記録（秘匿項目なし・生値）:

| 項目 | 値 |
|---|---|
| id | `12f8248e71a5` |
| name | `s07-testnet-forward-daily` |
| prompt | `no_agent: S-07 Hyperliquid testnet forward runner (daily signal, order->fill->settle, 5-condition evaluation, scorecard update)` |
| script | `s07_testnet_daily.sh` |
| no_agent | `true`（model=null / provider=null / base_url=null → LLM呼び出しなし） |
| schedule | `{"kind": "cron", "expr": "5 9 * * *"}`（毎日 09:05・サーバTZはJST実測） |
| enabled | `false` |
| state | `paused` |
| paused_at | `2026-11-09T08:03:47.957228+09:00` |
| created_at | `2026-11-03T15:22:19.146813+09:00`（wrapperのmtime 15:22:01 の18秒後＝一括設置と整合） |
| next_run_at | `2026-11-09T09:05:00+09:00`（**pause前の値のまま・不更新**。paused 状態では消費されない） |
| last_run_at | `2026-11-08T09:05:52.498506+09:00` |
| last_status | `ok`（last_error=null / last_failure=null / failure_streak=0） |
| repeat | `{"times": null, "completed": 5}` |
| deliver | `local` |
| workdir | `/root/current-work/yowayowa-investor` |
| last_dispatch | scheduled_at `2026-11-08T09:05:00+09:00` / dispatched_at `09:05:39.615431` / lateness 39.6s / kind `on_time` |

- **所属profile**: default（jobs.json の所在が `/root/.hermes/cron/jobs.json`＝defaultのHERMES_HOME）。他5profile（cto-hephaestus / worker-goat / worker-flash / worker-go / worker-free）の jobs.json には `s07_testnet`・`s07_daily_forward`・`hl_testnet` を参照するジョブは **0件**（grep 実測）。
- **最終実行と最終成功**: 最終実行＝最終成功＝`2026-11-08T09:05:52.498506+09:00`（`ok`）。直近の終了コード系の記録: `last_status=ok`・`failure_streak=0`（jobs.json に生の exit code は保持されないため status/failure_streak が読み取り権限内での記録）。出力実績は `/root/.hermes/cron/output/12f8248e71a5/` に 5 ファイル（11-04〜11-08・各268バイト）で `repeat.completed=5` と一致:

```
2026-11-04_09-05-49.md
2026-11-05_09-05-49.md
2026-11-06_09-06-03.md
2026-11-07_09-05-52.md
2026-11-08_09-05-52.md   ← 最新（2026-11-09 09:25:09 JST 時点で 11-09 のファイルは存在しない）
```

- 最新出力（2026-11-08 分・全文）— **これは停止標識置換「前」の旧コードが実行された唯一の最終実績**:

```
# Cron Job: s07-testnet-forward-daily
**Job ID:** 12f8248e71a5
**Run Time:** 2026-11-08 09:05:52
**Mode:** no_agent (script)
---
[HL-Testnet S-07] Day 2026-11-08 SHORT Equity=99.7902USDC NetPnL=-0.0293USDC RoundTrips=6/8 Verdict=INCONCLUSIVE OpenPos=0 OpenOrders=0
```

- **pause の実施経路（他profile権限での変更・本workerは未関与）**: COO裁定（2026-11-09 08:05・親カード コメント）「削除ではなく pause」。実施は default 所属カード `t_04549e19`（completed）が公式 `hermes cron pause 12f8248e71a5`（exit 0・jobs.json 手編集なし）で実施。本書の readback（上表）も `state=paused` / `enabled=false` / `paused_at=2026-11-09T08:03:47` でこれと一致する。

### 1.3 停止標識（`scripts/hl_testnet_s07/s07_daily_forward.py`）

- sha256: `88f5b3d6bdd2c8e830410c9b2eb095e7a0de91c01f3a558859f913eb8e872338`
- mtime: `2026-11-08 12:32:52.039180104 +0900`（627バイト）。**旧executor本体は `docs/evidence/hl_exec_disposition_20261108/rejected/s07_daily_forward.py.txt`（9927バイト・hash付き履歴保存）へ退避済み**。
- 本文（cat の全量・無加工）:

```python
#!/usr/bin/env python3
"""Stop marker for the rejected S-07 executor, including legacy cron wrappers.

No credentials, network, locks, ledgers or report writers are loaded here.
The original source is archived as non-executable evidence; see the decision log.
"""

import sys


def main() -> int:
    print(
        "DISABLED: t_f840d5bc rejected the unsafe S-07 forward executor. "
        "No order or dry-run ledger write was performed. "
        "See docs/HL_EXEC_DISPOSITION_20261108.md; replacement needs independent review.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
```

### 1.4 wrapper 経由の呼び出しが「何も実行しない」ことのコードパス論証（実行せずに読取で証明）

前提: wrapper は §1.1 の 6 命令のみ、exec 先は §1.3 の 627バイト全文。ここから次が導ける。

1. **import 閉包が `sys` のみ**: 停止標識は `import sys` 以外に何も import しない。ネットワーク（requests/httpx/socket）、鍵読込（env / ファイル / 秘密管理）、flock 系ロック、台帳・レポート・JSONL への open()/write、Telegram 送信、subprocess のいずれもモジュール内に存在しない（全文627バイト内に該当トークンなし）。
2. **main() は即座に終了する**: `main()` の本体は stderr への1行出力と `return 2` のみ。分岐・ループ・I/O なし。
3. **argv は完全に無視される**: wrapper は `"$@"` を渡すが、停止標識は argv を参照しない（`main()` は引数を取らない）。したがって **`--dry-run` 等どの引数を付けても挙動は同一**（stderr 1行・終了コード2）。
4. **終了コード 2 は cron 側に失敗として記録されるだけ**: 副作用（口座・台帳・状態ファイル・通知）はゼロ。jobs.json 側では last_status/failure_streak に反映されるのみで、取引経路には接続されない。
5. **異常系も実行主体が死ぬだけ**: `cd` 失敗 → `exit 1`（python は起動しない）。`.venv/bin/python` が不存在でも `exec` 失敗でシェルが非零終了するのみ。どちらも取引経路に到達し得ない。
6. **補助証拠（本調査外の第三者実測・歴史値として引用）**: 検収カード `t_f840d5bc` の監査（audit-glm-heavy・実装者と別系統）が停止標識を引数なし／`--dry-run` で実行し「rc=2・stdout空・DISABLED理由表示・副作用0」を独立実測済み。本調査内での実行はゼロであり、この実測は歴史値としての参照である。

結論: wrapper 経由の呼び出しは、ジョブが発火した場合でも「stderr 1行 + 終了コード2」以外の作用を生まない。注文・取消・署名・mainnet/testnet 送信・台帳書込はコードパス上存在しない。

### 1.5 実行ゼロの証跡（cron が停止標識を一度も実行していないこと）

- 時系列（全て実測値）: 停止標識への置換 = **2026-11-08 12:32:52 JST**。以降のスケジュール発火時刻は 09:05 日次なので **2026-11-09 09:05 の1回のみ**。ジョブは **2026-11-08 09:05:52（旧コード）を最後に実行なし**、**2026-11-09 08:03:47 に pause**。
- 本日の 09:05 は未発火（4系統の独立観測が一致）:
  1. `cron list --all`（09:21 JST）→ `12f8248e71a5 [paused]`。
  2. `hermes -p default cron runs 12f8248e71a5`（09:21 JST）→ `No cron execution attempts recorded.`（試行履歴0。pause後だけでなく台帳上は全期間0・この台帳の性格は§2に記載）。
  3. 出力ディレクトリに **2026-11-09 のファイルが存在しない**（09:21 実測・**09:25:09 JST に 09:05 通過後の再確認済み**。最新は 2026-11-08_09-05-52.md のまま）。
  4. `last_run_at` が `2026-11-08T09:05:52...` のまま不変・`failure_streak=0`（09:21 実測 readback）。
- pause 時刻（08:03:47）＜ 発火予定（09:05）であるため「本日の発火ゼロ」は pause と整合。発火なしの独立観測として pause 実施カード `t_04549e19` の metadata にも 09:05:50 / 09:12:40 JST の fire-check 実測（`cron runs` 記録ゼロ・last_run_at 不変）が残る。
- ジョブ由来の書込みゼロ: `data/hl_testnet_s07/` 配下の全ファイル（daily_ledger.json / execution_journal.jsonl / raw_events.jsonl / trades.jsonl / reports/S07_TESTNET_SCORECARD.md）の mtime が一様に **2026-11-08 09:05**（= 最後の旧コード実行時）で、**停止標識配置後の書込みは0件**。

**断定: cron 経由で wrapper（＝停止標識パス）が実行された事例は 0 件である。** 旧コードの実行実績は 11-03〜11-08 の計5回（旧コード時代・全て 09:05 台・`ok`）であり、停止標識置換後に cron から実行された回数はゼロ。

### 1.6 取引所口座状態（公式 /info・読み取り専用のみ）

- 照会経路: `POST https://api.hyperliquid-testnet.xyz/info`（公式読み取り専用・**署名不要**・秘密鍵不使用）。
- 対象アドレス（公開アドレスのみ・秘匿値ではない）: `0xF6cB1079F73cA0163E2EfF24eFbB651f99E2Dd82`（Hyperliquid Testnet 対象口座）。
  - アドレスの出所（COO裁定が許容する「公開アドレスのみ既存の非秘密設定にある」経路・1経路のみ示す）: **`data/hl_testnet_s07/reports/S07_TESTNET_SCORECARD.md` 4行目**（非秘密レポート・sha256 `9de1627f5d31986f659a61fef1ceac5523fd0843fe009caaf99297e8a77d32a0`）。同一アドレスが退避済み旧コードの定数 `DEFAULT_TESTNET_ACCOUNT`（rejected/s07_forward_runner.py.txt 50行目）にも記載。鍵ファイルは一切読んでいない。
- 取得時刻: **2026-11-09 09:23:12 JST 開始〜09:23:13 JST 終了**（4連続照会・`time` フィールドはサーバ返却の生値）。
- 生値（無加工・以下そのまま）:

```json
// clearinghouseState（取得 09:23:12 JST）
{"marginSummary":{"accountValue":"99.790224","totalNtlPos":"0.0","totalRawUsd":"99.790224","totalMarginUsed":"0.0"},"crossMarginSummary":{"accountValue":"99.790224","totalNtlPos":"0.0","totalRawUsd":"99.790224","totalMarginUsed":"0.0"},"crossMaintenanceMarginUsed":"0.0","withdrawable":"99.790224","assetPositions":[],"time":1794183792260}

// openOrders（取得 09:23:12 JST）
[]

// userFills（取得 09:23:12 JST・16レコード）
[{"coin":"BTC","px":"75674.0","sz":"0.00039","side":"B","time":1794096349690,"startPosition":"-0.00039","dir":"Close Short","closedPnl":"-0.00273","hash":"0x1891e92bccc72ba91a0b042da01c9a010200011167ca4a7bbc5a947e8bcb0593","oid":64790008907,"crossed":true,"fee":"0.01328","tid":165149739743139,"feeToken":"USDC","twapId":null},{"coin":"BTC","px":"75667.0","sz":"0.00039","side":"A","time":1794096347460,"startPosition":"0.0","dir":"Open Short","closedPnl":"0.0","hash":"0x98337a15208a023b99ad042da01c8601020091fabb8d210d3bfc2567df8ddc26","oid":64790008234,"crossed":true,"fee":"0.013279","tid":459504292604079,"feeToken":"USDC","twapId":null},{"coin":"BTC","px":"75967.0","sz":"0.00039","side":"B","time":1794009950160,"startPosition":"-0.00039","dir":"Close Short","closedPnl":"-0.00039","hash":"0x30ffa978fe64cc763279042d91a995010500c15e9967eb48d4c854cbbd68a660","oid":64717165447,"crossed":true,"fee":"0.013332","tid":925030378302976,"feeToken":"USDC","twapId":null},{"coin":"BTC","px":"75966.0","sz":"0.00039","side":"A","time":1794009948162,"startPosition":"0.0","dir":"Open Short","closedPnl":"0.0","hash":"0x3d9e8c3be27fcbd63f18042d91a977010400a4217d72eaa8e167378ea173a5c0","oid":64717162441,"crossed":true,"fee":"0.013332","tid":314767584632362,"feeToken":"USDC","twapId":null},{"coin":"BTC","px":"76427.0","sz":"0.00039","side":"B","time":1793923559879,"startPosition":"-0.00039","dir":"Close Short","closedPnl":"0.010452","hash":"0x3a586bab2ec945e43bd2042d7cc1df0109008390c9cc64b6de2116fdedcd1fce","oid":64631377209,"crossed":true,"fee":"0.013412","tid":191772852541753,"feeToken":"USDC","twapId":null},{"coin":"BTC","px":"76426.0","sz":"0.00004","side":"A","time":1793923557824,"startPosition":"-0.00035","dir":"Open Short","closedPnl":"0.0","hash":"0x618a57c6e7a4d3a56304042d7cc1b90105006fac82a7f27705530319a6a8ad90","oid":64631374196,"crossed":true,"fee":"0.001375","tid":373908581936625,"feeToken":"USDC","twapId":null},{"coin":"BTC","px":"76457.0","sz":"0.00035","side":"A","time":1793923557824,"startPosition":"0.0","dir":"Open Short","closedPnl":"0.0","hash":"0x618a57c6e7a4d3a56304042d7cc1b90105006fac82a7f27705530319a6a8ad90","oid":64631374196,"crossed":true,"fee":"0.012041","tid":986952925637720,"feeToken":"USDC","twapId":null},{"coin":"BTC","px":"80377.0","sz":"0.00018","side":"B","time":1793837147235,"startPosition":"-0.00018","dir":"Close Short","closedPnl":"-0.011592","hash":"0x251c9c216409b6be2696042d67969c010c00b406ff0cd590c8e54774230d90a8","oid":64539871312,"crossed":true,"fee":"0.00651","tid":546134119603642,"feeToken":"USDC","twapId":null},{"coin":"BTC","px":"80362.0","sz":"0.00019","side":"B","time":1793837147235,"startPosition":"-0.00037","dir":"Close Short","closedPnl":"-0.009386","hash":"0x251c9c216409b6be2696042d67969c010c00b406ff0cd590c8e54774230d90a8","oid":64539871312,"crossed":true,"fee":"0.00687","tid":500111868699317,"feeToken":"USDC","twapId":null},{"coin":"BTC","px":"80307.0","sz":"0.00024","side":"A","time":1793837145179,"startPosition":"-0.00013","dir":"Open Short","closedPnl":"0.0","hash":"0xaf172f30044d928eb090042d67967f01010047159f40b16052dfda82c3416c79","oid":64539868829,"crossed":true,"fee":"0.008673","tid":627497349876969,"feeToken":"USDC","twapId":null},{"coin":"BTC","px":"80323.0","sz":"0.00013","side":"A","time":1793837145179,"startPosition":"0.0","dir":"Open Short","closedPnl":"0.0","hash":"0xaf172f30044d928eb090042d67967f01010047159f40b16052dfda82c3416c79","oid":64539868829,"crossed":true,"fee":"0.004698","tid":493666958685657,"feeToken":"USDC","twapId":null},{"coin":"BTC","px":"76915.0","sz":"0.00038","side":"B","time":1793750747088,"startPosition":"-0.00038","dir":"Close Short","closedPnl":"-0.009462","hash":"0x34cee9933d21818e3648042d5205f10105000178d824a060d89794e5fc255b78","oid":64448973676,"crossed":true,"fee":"0.013152","tid":232411881091943,"feeToken":"USDC","twapId":null},{"coin":"BTC","px":"76888.0","sz":"0.00011","side":"A","time":1793750745092,"startPosition":"-0.00027","dir":"Open Short","closedPnl":"0.0","hash":"0x7aa2f0967ef84e247c1c042d5205d2010500087c19fb6cf61e6b9be93dfc280f","oid":64448971625,"crossed":true,"fee":"0.003805","tid":625204572051219,"feeToken":"USDC","twapId":null},{"coin":"BTC","px":"76891.0","sz":"0.00027","side":"A","time":1793750745092,"startPosition":"0.0","dir":"Open Short","closedPnl":"0.0","hash":"0x7aa2f0967ef84e247c1c042d5205d2010500087c19fb6cf61e6b9be93dfc280f","oid":64448971625,"crossed":true,"fee":"0.009342","tid":817398580515410,"feeToken":"USDC","twapId":null},{"coin":"BTC","px":"76063.0","sz":"0.00039","side":"B","time":1793686618274,"startPosition":"-0.00039","dir":"Close Short","closedPnl":"-0.02691","hash":"0xb2755a9c41c71140b3ef042d42b4100104007281dcca3012563e05ef00caeb2b","oid":64388840531,"crossed":true,"fee":"0.013349","tid":948018881441084,"feeToken":"USDC","twapId":null},{"coin":"BTC","px":"75994.0","sz":"0.00039","side":"A","time":1793686616324,"startPosition":"0.0","dir":"Open Short","closedPnl":"0.0","hash":"0xf955b46347a4414dfacf042d42b3ed010100cc48e2a760209d1e5fb606a81b38","oid":64388838936,"crossed":true,"fee":"0.013336","tid":1102152445943739,"feeToken":"USDC","twapId":null}]

// extraAgents（取得 09:23:13 JST）
[{"name":"yowayowa-acct","address":"0xa2af82d975e52d4fa110575d6cac8f83e1ae9239","validUntil":1801366072117}]
```

- 生値からの直接読み取り（換算を含まない）: accountValue `99.790224`・`assetPositions: []`（建玉なし）・`openOrders: []`（未約注残なし）・`withdrawable: 99.790224`・登録済みエージェント 1件（`yowayowa-acct`）。
- 換算値（`date -d @<epoch>` による計算結果・生値は上記）: `clearinghouseState.time=1794183792260ms` → **2026-11-09 09:23:12 JST**（照合: 取得実施時刻と一致）。`userFills` 最新 `time=1794096349690ms` → **2026-11-08 09:05:49 JST**。`extraAgents.validUntil=1801366072117ms` → **2027-01-31 12:27:52 JST**。
- 相互照合（観察・推測ではない直接比較）: ① `/info` accountValue `99.790224` はスコアカード最終行（2026-11-08 Equity `99.7902`）と一致 — **11-08 09:05（旧コード最終実行）以降、口座に変動がない**。② `userFills` の entry OID 6種（64388838936 / 64448971625 / 64539868829 / 64631374196 / 64717162441 / 64790008234）と exit OID 6種（…40531 / …3676 / …1312 / …7209 / …5447 / …8907）はスコアカード§2の取引表の OID と **6組すべて一致**。16レコードは部分約定による同一OID分割を含む（例: OID 64539868829 が 2 レコード）。③ スコアカード記載の「承認エージェント `0xa2aF82D975...` (yowayowa-acct)」と `extraAgents` の返却（name/address 小文字表現）は同一アドレス。
- 参考として記録するが本書の照会対象ではない値: 履歴 Runbook（docs/evidence/.../historical_runbooks/）には mainnet 用アドレス等の公開値も記載されているが、本カードの対象は testnet 経路のため **mainnet は未照会**（§2）。

### 1.7 その他の実行経路の不在（wrapper 12f8248e71a5 以外）

- OS root crontab（`crontab -l`・読み取りのみ）: S-07 / Hyperliquid / hl_testnet 関連は **0件**（grep 誤一致1件: keirin の `odds_snapshots_forward.parquet` は "forward" の部分一致のみ・S-07と無関係）。
- `/etc/cron.d` / `/etc/crontab` / `/etc/cron.daily` / `/etc/cron.hourly`: s07 / hyperliquid / hl_testnet 関連 **0件**。
- `systemctl list-timers --all`: s07 / hl / hyper 関連 **0件**。
- 他profileの jobs.json 5件: 参照 **0件**（§1.2）。
- default jobs.json 内の他ジョブが `s07_testnet_daily.sh` / `s07_daily_forward.py` / `hl_testnet` を参照: **0件**。
- 秘密系ファイルの存在（名前のみ確認・中身未開封）: リポジトリ直下に `.env` と `.env.example` が存在。

---

## 2. 確認できなかった事実

1. **`hermes cron runs` の台帳が全期間で空である理由**: 11-04〜11-08 に5回の実行実績（output 5ファイル・`repeat.completed=5`・`last_run_at`）があるのに、`cron runs` は「No cron execution attempts recorded」を返す。読み取り権限内では、この台帳が no_agent 実行を記録対象外にしているのか、別管理・クリア済みなのかを判別できない。実行実績の確定には jobs.json フィールドと output ファイルを用いた（§1.2・§1.5）。
2. **停止標識の現在時点での実行時挙動の実測**: カード規律により実行禁止のため未実施。終了コード2・stderr 1行・副作用0 という挙動は、コードパス読取（§1.4）と歴史値としての第三者監査実測（t_f840d5bc）に基づく。
3. **本ホスト外部からの参照**: TSUKUMO 等の他ホストから当 wrapper を参照する外部トリガーの有無は範囲外のため未確認（本ホスト内の OS cron / systemd / Hermes cron は全て不在を確認済み・§1.7）。
4. **`/root/.secrets/` 配下の実在確認と `.env` 内の変数**: 鍵非接触規律により名前を含め調査していない。歴史 Runbook が鍵ファイルのパス（例: `/root/.secrets/hl_agent_testnet.key`）を記載しているが、それは歴史値であり現存確認はしていない。
5. **mainnet 口座の現況**: 本カードの対象（testnet 経路）ではないため未照会。

## 3. 推測（根拠を明示）

1. `cron runs` 台帳が空なのは「no_agent ジョブの実行が attempts 台帳の対象外（または別管理）」であるためと推測する。根拠: output ファイル5件・`repeat.completed=5`・`last_run_at/last_status=ok` が実行実績と一致し、台帳だけが空である。
2. 将来このジョブが resume された場合、次回 09:05 発火では「wrapper → 停止標識 rc=2」でジョブは失敗扱いになり、副作用はゼロ、と推測する。根拠: §1.4 のコードパス（実行はしていないため実測なし）。なお resume/再開自体は COO 裁定上「停止標識解除・独立レビュー・別途裁可」が前提であり、本カードからは行わない・推奨しない。

## 4. cron 変更依頼（起票形式）

**結論: 本調査からは cron 変更依頼は発生しない。** 対象ジョブは既に公式 `hermes cron pause`（default 所属 `t_04549e19`・COO裁定 2026-11-09 08:05）で停止済みであり、readback（§1.2）で `enabled=false / state=paused` を確認した。resume・削除・再設定のいずれも本カードの裁量外であり、起票しない。

参考（未発行・将来の起票時に使う形式の雛形）:

```
起票: cron変更依頼（未発行・参考雛形）
- 対象 job / profile: 12f8248e71a5（s07-testnet-forward-daily）/ default（HERMES_HOME=/root/.hermes）
- 変更内容: （例）resume / remove / schedule 変更のいずれかを1行で
- 理由: （S-07 再設計の受入結果など、根拠カード番号付きで）
- リスク: resume 時は毎日09:05に発火する。停止標識が残る限り rc=2 で no-op（§1.4）だが、
  停止標識が解除済みの状態での resume は実発注経路の再稼働を意味する。
- 実注文禁止の担保方法: ①停止標識（s07_daily_forward.py・exit2・副作用0）の解除は
  docs/HL_EXEC_DISPOSITION_20261108.md の独立レビュー＋別途裁可を完了するまで行わない。
  ②replacement 実装の受入テスト（testnet・dry-run分離）を完了するまで resume しない。
  ③resume 実行直後の readback（cron list / cron runs / output ファイル）を証跡として添付する。
- 復帰手段: 再 pause は `hermes cron pause 12f8248e71a5`（公式CLI・1行）。
```

## 5. 実行したコマンドと出力（秘匿値マスク方針）

使用コマンド（すべて読み取り系）: `cat`（wrapper・停止標識・スコアカード・cron出力md）/ `ls`・`stat` / `sha256sum` / `grep`（jobs.json・cron一覧・ディレクトリ走査）/ `date`（現在時刻・epoch換算）/ `curl -X POST /info`（署名不要の公式読み取り専用・4種）/ `hermes -p default cron list --all` / `hermes -p default cron runs 12f8248e71a5` / `crontab -l` / `systemctl list-timers --all` / `find`（ファイル名のみ）。

- 主要出力は本書 §1.1〜§1.7 に無加工で貼付した。
- **マスク対象: なし。** 調査中に秘匿値（鍵・シークレット・トークン）は一切取得・表示していない。/info 照会に使用したのは署名不要の公開アドレスのみ。エポックの JST 換算値は「換算値」と明記して生値と区別した。
- 補記: 解析用スクリプト実行（python heredoc）が単発モードの承認ガードにより1件拒否されたため、同一情報を `grep` 等の承認不要の読み取り primitive で取得し直した（ガードの迂回ではなく読み取り primitive への置換。jobs.json の全フィールドは grep で取得・§1.2 掲載）。
- 版固定ハッシュ（調査時点）: wrapper `aca5a4ce…`、停止標識 `88f5b3d6…`、スコアカード `9de1627f…`、jobs.json `9693224e21ca0b8335bacd15c342d7418872221fa80ecea59a98d98cf600d6c0`（jobs.json は他ジョブの実行履歴更新でshaが動く値・設定差分の照合は§1.2のフィールド値で行った）。
- 生値の再照合検証（2026-11-09 09:28:43 JST 実施）: §1.6 掲載の4値を再取得して照合した結果、**openOrders / userFills / extraAgents は改行を除くsha256比較でバイト一致**。**clearinghouseState は `time` フィールド（サーバが照合ごとの現在時刻を返す）を除き一致**（再取得時の time=1794184123676 = 換算値 2026-11-09 09:28:43 JST・掲載値は 09:23:12 JST 時点のスナップショットとして正しい）。なお初回掲載時に userFills の1レコードの `time` に引用符を付ける写しミスがあり、上記検証で検出・修正済み（修正後にバイト一致を確認）。

## 6. 受入条件との対応

| 受入条件 | 本書での担保 |
|---|---|
| 実行ゼロの証跡 | §0（宣言）＋§1.4（コードパス論証）＋§1.5（4系統の独立観測：cron list / cron runs / output欠落 / last_run_at不変・09:25:09再確認）＋§5（使用コマンド一覧） |
| 鍵非接触の明記 | §0（宣言）＋§1.6（/info は署名不要・公開アドレスのみ・出所1経路明示）＋§2-4（未開封の範囲を明示）＋§5（マスク対象なし） |
| `12f8248e71a5` の現行実在有無の断定 | §1.2 冒頭の断定文（現行 jobs.json 2172行目・公式CLIで実在・`[paused]`・置換/削除なし） |
| 口座状態の取得時刻付き記録 | §1.6（取得時刻 2026-11-09 09:23:12–13 JST・4種の生値全文・換算値は明記の上で分離） |
| 事実／未確認／推測の分離 | §1 ／ §2 ／ §3 |
| cron変更依頼の起票形式 | §4（結論: 依頼不要・雛形を未発行で添付） |
