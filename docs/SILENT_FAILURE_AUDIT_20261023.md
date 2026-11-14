# サイレント失敗 静的監査 — yowayowa-investor

- 監査日: 2026-10-23 (JST)
- 対象: `/root/current-work/yowayowa-investor/src/yowayowa` 配下の全 `.py`
- 対象規模: **241 ファイル / 55,340 行**
- 手法: grep + Python AST の二系統。**コード変更なし・git 操作なし・外部送信なし**
- 結論: **except ハンドラ 547 件のうち 157 件が「無言」、うち 17 件を候補、140 件を除外（正当なフォールバック）と判定。実害「高」は 3 件。**

---

## 1. 走査コマンド（再現用）

```bash
cd /root/current-work/yowayowa-investor

# 規模
find src/yowayowa -name '*.py' | wc -l          # 241
find src/yowayowa -name '*.py' -exec wc -l {} + | tail -1   # 55340

# 生パターン（grep 側の基準値）
grep -rn --include=*.py -E '^\s*except\b' src/yowayowa | wc -l                    # 547
grep -rn --include=*.py -E '^\s*except\s*:' src/yowayowa | wc -l                  # 0  (bare except)
grep -rn --include=*.py -E 'except[^:]*:\s*pass' src/yowayowa | wc -l             # 0  (except: pass)
grep -rn --include=*.py -E '^\s*except\s+(Exception|BaseException)\b' src/yowayowa | wc -l  # 142
grep -rn --include=*.py -cE '"status"\s*:\s*"ok"' src/yowayowa | grep -v ':0'    # 3 ファイル

# AST 側（本監査の分類器）
python3 /root/.hermes/profiles/worker-goat/cache/scratch/scan_v3.py
```

AST 分類器のソース: `/root/.hermes/profiles/worker-goat/cache/scratch/scan_v3.py`
中間データ: `/root/.hermes/profiles/worker-goat/cache/scratch/silent_failure_scan_v3.json`（547 件の全レコード）
分類確定版: `.../cache/scratch/silent_v4.json`

### 件数の整合（重要）

| 指標 | 件数 | 出所 |
|---|---|---|
| except ハンドラ（grep 行数） | 547 | `grep -E '^\s*except\b'` |
| except ハンドラ（AST ノード、重複除去後） | 547 | `ast.ExceptHandler` を `(file, lineno)` で dedupe |
| 両者の差分 | 0 | 突合スクリプトで双方向 0 件を確認 |
| `except: pass` / bare `except:` | 0 / 0 | grep 実測 |
| `except Exception` | 142 | grep 実測 |
| 再 raise するハンドラ | 308 | AST |
| 失敗を収集リスト/明示フィールドに残す | 82 | AST（E1/E2/E5 ほか） |
| **無言（ログ・収集・再raise なし）** | **157** | AST |
| └ 候補 | **17** | 手動トリアージ |
| └ 除外 | **140** | 規則別（§4） |

> 注: 初回 AST 走査は 564 件と出たが、これは `ast.walk(func)` が入れ子関数内のハンドラを親関数からも二重に数えたため。dedupe 後の 547 が正で、grep と完全一致する。**564 は誤りであり本表には使わない。**

---

## 2. 前提となる構造的発見

このコードベースは **`logging` を実質使っていない**。

```
grep -rn 'import logging' src/yowayowa → 1 ファイルのみ (vercel_observability.py)
grep -oE '\b(logger|log|LOG)\.[a-z_]+\(' → 0 件
```

代わりに失敗を `errors[...] =` / `warnings.append` / `unavailable.append` / `result['checks'][..]['failures'].append` / `parse_note=` / `error=` といった**戻り値側の明示フィールド**で表す設計。
したがって本監査の「無言」判定は **「logger 呼び出しが無い」ではなく「呼び出し元・利用者が失敗を観測できる形で何も残していない」** と定義した。この定義で 547 → 157 に絞られている（logger 基準で数えると 547 件すべてが該当し、無意味になる）。

---

## 3. 候補（17 件）

重大度の定義 — 高: 誤ったデータが正常値として外部境界（API/CLI/LLM/執行記録）へ出る、または無言で機能停止する / 中: 失敗と「データ無し」が区別できず判断を誤らせる / 低: 重複処理・部分欠落など回復可能な劣化。

### 影響「高」 — 3 件

| # | 重大度 | ファイル:行 | 関数 | 該当コード（3行以内） | 発生入力 | 起きること / 実害 |
|---|---|---|---|---|---|---|
| 1 | **高** | `services/orderbook_service.py:292` | `get_snapshot` | `try: data = json.loads(...)`<br>`except Exception:`<br>`    pass` | `latest.json` の破損・スキーマ不一致（書込み中断、手編集、旧スキーマ移行） | 例外を捨て、`generate_seed_orderbook()` の**合成板**を返し、さらに `record_snapshot(seed)` で `latest.json` を上書きする。合成板の provenance は `provider="jpx-tse-feed"` / `"binance-orderbook"` / `"ose-derivatives-feed"`、`license_class=OFFICIAL_PUBLIC`。つまり**実取引所フィードの公的データ**として API `/orderbook`・CLI・`ai_agent` の `get_orderbook` ツール（LLM 経由）に出る。加えて破損した実データは復旧不能に破壊される。合成データを返すこと自体は設計意図（docstring"generate a high-fidelity seed"）だが、**「実フィードの provider 名 + OFFICIAL_PUBLIC」を付けて返す点と、破損検知を握り潰して上書きする点**が実害。 |
| 2 | **高** | `services/ir_monitor_service.py:177` | `load` | `except json.JSONDecodeError:`<br>`    return []` | `data/ir-sources/sources.json` の破損（書込み中断・手編集） | 破損を「ソース 0 件」と同義にし、`__init__` で `self._sources = {}` になる。**IR 監視が対象 0 件で正常終了**し、以後の新規開示取得・通知が無言で止まる。さらに `sources` 引数付きで再構築されると 253–254 行が `self._registry.save(self.list_sources())` を実行し、**破損ファイルを「渡された分だけ」で上書き**してユーザー定義を喪失する。 |
| 3 | **高** | `services/private_source_service.py:130` | `load` | `except json.JSONDecodeError:`<br>`    return []` | `data/private-sources/sources.json` の破損 | #2 と同一構造（218–224 行が同じ merge + save）。メールボックス取り込みが無言停止し、registry が上書きされ得る。 |

### 影響「中」 — 5 件

| # | 重大度 | ファイル:行 | 関数 | 該当コード（3行以内） | 発生入力 | 起きること / 実害 |
|---|---|---|---|---|---|---|
| 4 | 中 | `api/routes.py:126` | `search_instruments` | `except ProviderPolicyError:`<br>`    pass` | public モード等でライセンスポリシーが yahoo 検索を拒否 | 例外を `errors` に積まず握り潰す。`merged` が空かつ `errors` も空なら 144 行の `if not merged and errors` を通過せず **HTTP 200 + `[]`**。「規約で使えない」と「該当 0 件」が区別できない（SEC 側が結果を返す場合は部分結果になるため中）。 |
| 5 | 中 | `operator_bridge/state.py:379` | `_migrate_legacy_audit` | `except Exception:`<br>`    pass` | 旧 `events` の `payload_json` 破損 / `intent` が dict でない | `intent_hash` が `"LEGACY_UNKNOWN_HASH"` のまま INSERT される。再生検知（448–452 行）は `saved_hash != intent_hash` で MISMATCH を出す設計だが、legacy 行同士は常に一致するため**別内容の再送が素通り**する。対象は legacy 移行行に限定されるため中。 |
| 6 | 中 | `providers/yahoo_fundamentals.py:199`（同 201 も同型） | `_safe_frame` | `except Exception:`<br>`    return pd.DataFrame()` | yfinance のレート制限・一時障害・スキーマ変更 | 空 DataFrame を返し、後段では「財務諸表が 1 期も無い」と同義になる。取得失敗とデータ無しが区別不能。同ファイル 186–189 行のコメントが「同種の無言劣化で company_name がシンボルに落ちた（P5 R4）」と**過去に実際に踏んだ不具合として明記**している。 |
| 7 | 中 | `providers/yahoo_fundamentals.py:201` | `_safe_frame` | `except Exception:`<br>`    return pd.DataFrame()` | 上記の別経路（`frequency=` 引数なし呼び出しの再試行失敗） | #6 と同じ。行を分けて計上（別ハンドラのため）。 |
| 8 | 中 | `providers/rakuten_ms2_rss.py:61` | `_to_int` | `except ValueError:`<br>`    return 0` | 発注数量・約定数量・保有数量が空文字 / `"1,000株"` / 全角数字など | 解析失敗を **0** として返し、`quantity` / `filled_quantity` / `保有数量(Decimal(0))` にそのまま入る（188–189, 235 行）。執行記録・保有照合（`executions_reconciliation`）が数量 0 として扱い、建玉判定や突合が誤る。`None` ではなく 0 を返す点が「値が無い」と「数量 0」を混同させる。 |

### 影響「低」 — 9 件

| # | 重大度 | ファイル:行 | 関数 | 該当コード | 発生入力 | 起きること |
|---|---|---|---|---|---|---|
| 9 | 低 | `acquisition/documents.py:226` | `extract_pdf_tables` | `except Exception:`<br>`    return tables` | pdfminer のページ走査が途中で失敗 | 途中まで積んだ `tables` をそのまま返す。`parsed=True` のまま**部分抽出が完全抽出として**渡る。0 件なら「表なし」扱い。 |
| 10 | 低 | `acquisition/documents.py:417` | `_read_sheet_rows` | `except KeyError:`<br>`    return []` | xlsx 内に該当 `sheetN.xml` が無い（チャートシート混在など） | 当該シートのみ黙って飛ばし、他シートがあれば `parsed=True` で返る。`parse_note` は付かない。全シート欠落時のみ 371 行の "no readable sheets" が出る。 |
| 11 | 低 | `acquisition/discovery.py:227` | `known_keys` | `except json.JSONDecodeError:`<br>`    continue` | `fingerprints.jsonl` の破損行 | 破損行の URL が「未取得」と判定され、**再取得・再通知**が走る（重複ダウンロード・重複アラート）。件数のカウンタは無い。 |
| 12 | 低 | `acquisition/discovery.py:248` | `known_content_urls` | 同上 | 同上 | 同上（内容ハッシュ既知判定が外れ、再取得）。 |
| 13 | 低 | `acquisition/discovery.py:268` | `known_urls` | 同上 | 同上 | 同上（URL 既知判定が外れる）。 |
| 14 | 低 | `crypto_acquisition.py:64` | `_existing_keys` | `except ValueError:`<br>`    continue` | `ohlcv.jsonl` の破損行 | 重複排除キーが欠落し、同一 (provider, currency, as_of) の行が**重複追記**される。 |
| 15 | 低 | `stock_acquisition.py:74` | `_existing_keys` | `except ValueError:`<br>`    continue` | 同上（stock 側） | 同上。なお 130 行の read 側は「corrupt line is skipped, never repaired silently」とコメントがあるが、実際に件数を残しているのは `macro_store` のみで、ここは無言。 |
| 16 | 低 | `acquisition/snapshots.py:80` | `history` | `except (json.JSONDecodeError, KeyError, ValueError):`<br>`    continue` | スナップショット jsonl の破損行 | 履歴から無言で消える。前回比較（差分検出）が「変化あり」と誤判定し得る。 |
| 17 | 低 | `acquisition/snapshots.py:110` | `recent_entries` | 同上 | 同上 | `latest_payload` が壊れた最新行を飛ばして**1 つ古い版を最新として返す**。 |

---

## 4. 除外（140 件）— 規則別内訳

| 規則 | 件数 | 除外理由 |
|---|---|---|
| E1 失敗が戻り値/収集リストに明示される | 9 | `errors[symbol]=…` / `unavailable.append(…)` / `missing_fundamentals.append(…)` / `result['checks'][..]['failures'].append(…)` / `_failure(reason)` 等で呼び出し元が観測可能。 |
| E2 明示的な UNKNOWN enum | 4 | `BrokerOrderStatus.UNKNOWN` を返す。`FILLED` 等と区別できる明示値であり、無言ではない。 |
| E3 型付きOptional解析ヘルパ（None が契約） | 32 | `_optional_float` / `_parse_date` / `_number` / `_to_decimal` / `positive_finite` 等、戻り値型が `X \| None` で None が仕様。呼び出し側が欠損として扱う設計。 |
| E4 失敗時クローズド / 明示マーカー | 11 | 認証プローブ `False`、ループバック判定 `False`、`_tz_ok` `False`、`_screener_policy_admits` `False`、`_segment_ic` の `insufficient=True` フラグ、`yahoo._quote_currency` の `CURRENCY_REASON_*` 等。**失敗時に安全側へ倒す**方向。 |
| E5 明示的な理由文字列 | 6 | `'audit state file unparsable'`、`'invalid_provenance'`、`_UNAVAILABLE`、`'csv decode failed …; not parsed'` 等を返す。 |
| E7 追記型 JSONL の行単位ベストエフォート読取 | 37 | 表示・集計用の読み取りで、破損 1 行のために全体を落とさない契約（多くはコメントで明記）。重複排除キーや監視状態に影響するものは §3 の候補へ分離済み。 |
| E8 文書化済みの意図的パス | 6 | 下記 E8 表。 |
| E9 個別確認 | 35 | 下記 E9 表。各行を実コードで確認済み。 |

### E8 — 文書化済みの意図的 `pass`（6 件）

| ファイル:行 | 関数 | 除外理由 |
|---|---|---|
| `acquisition/documents.py:262` | `_decode_csv_bytes` | utf-8-sig 失敗時に cp932 を試す**二段デコードの 1 段目**。両方失敗時は 266 行が明示 note を返す。 |
| `db.py:680` | `_ensure_screening_candidates_document_id` | `ADD COLUMN` の冪等化。コメントで「望む最終状態は既に達成」と明記。 |
| `operator_bridge/rakuten_web.py:707` | `_captured_at` | 取得時刻の任意項目。失敗時は他経路の値を使う。 |
| `operator_bridge/rakuten_web.py:1042` | `_parse_execution_datetime` | 同上（日時が読めない行は後段で別途判定）。 |
| `providers/yahoo_deep_research.py:398` | `_jsonable` | JSON 化できない値を落とす整形処理。データ欠落の意味を持たない。 |
| `providers/yahoo_research.py:279` | `_json_value` | 同上。 |

### E9 — 個別確認した除外（35 件）

| ファイル:行 | 関数 | 除外理由（実コード確認済み） |
|---|---|---|
| `acquisition/documents.py:159` | `extract_pdf_tables` | 任意 extra（pdfminer）未導入時の `ImportError`。docstring に「callers treat that as "no tables", never as an error」と明記。 |
| `acquisition/documents.py:266` | `_decode_csv_bytes` | 2 段デコードも失敗 → `("", "csv decode failed …")` と**明示 note を返す**。fail closed。 |
| `acquisition/documents.py:385` | `_read_shared_strings` | `sharedStrings.xml` 不在はインライン文字列のみの xlsx で**正常**。 |
| `acquisition/documents.py:402` | `_sheet_names` | 空になると 367–372 行が `parsed=False` + "xlsx contained no readable sheets" を返す（観測可能）。 |
| `acquisition/documents.py:447` | `_read_sheet_rows` | shared strings の添字外は生の `value_node.text` にフォールバック。セル値は失われない。 |
| `api/app.py:366` | `daily_maintenance` | `edinet_index_failure` に格納され、391–392 行の `"ok"` 判定と応答フィールドに出る。 |
| `api/app.py:380` | `daily_maintenance` | `event_failure` として同上。 |
| `broker/execution/transport.py:170` | `_extract_broker_order_id` | 失敗時 `(None, "not_found")` を返す。docstring「A missing number is a parse failure, never an acceptance」。 |
| `broker/execution/transport.py:178` | `_extract_broker_order_id` | 同上（`content=""` → `not_found`）。 |
| `broker/execution/transport.py:539` | `_probe_authenticated` | `text=""` → 528/543 行の `return False`（未認証として扱う）。 |
| `broker/session_notify.py:77` | `_load_state` | `{}` は抑制状態の喪失＝**通知が増える方向**（安全側）。 |
| `broker/session_notify.py:117` | `notify_session_expired` | `previous=None` は初回判定。 |
| `broker/session_notify.py:162` | `notify_authenticated` | コメント通り best-effort。失敗しても誤通知は起きない。 |
| `calibration_cli.py:22` | `_get_json` | 詳細取得の失敗時は `response.text` にフォールバックし、直後に `typer.Exit(1)` で失敗を通知。 |
| `hypothesis_cli.py:20` | `_request` | 同上。 |
| `crypto_acquisition.py:33` | `_key_ts` | 時刻を文字列化するだけのフォールバック。 |
| `paperloop.py:213` | `code_rev` | `'unknown'` を明示返却。 |
| `paperloop.py:227` | `code_rev` | `status=''` → 213 行で `'unknown'` として返る。 |
| `paperloop.py:2563` | `_milestone_judgment` | 日付不明イベントを `in_window=True` にして**未知イベント数に算入**（fail-closed、コメント明記）。 |
| `providers/alpha_vantage.py:47` | `_load` | 状態ファイル不在＝当日未使用 → `(today, 0)`。正常系。 |
| `providers/alpha_vantage.py:49` | `_load` | 破損時 `(today, limit)` = **残枠 0 として fail closed**（過剰消費を防ぐ）。コメント明記。 |
| `providers/alpha_vantage.py:62` | `_load` | 同上（日付不正）。 |
| `providers/estat.py:131` | `_updated` | datetime 失敗時に date へフォールバック（同じ値をより粗く解釈）。 |
| `providers/frankfurter.py:305` | `_error_message` | HTTP ステータスから理由文字列を組み立てる表示専用処理。 |
| `providers/fundamentals.py:95` | `company_facts` | `LookupError` → 明示的な fallback プロバイダへ委譲（設計された多段）。 |
| `providers/yahoo_fundamentals.py:184` | `_safe_history_metadata` | `{}` だが 186–189 行に「過去の誤判定（P5 R4）を修正済み」と経緯が明記され、`Mapping` 受理へ改善済み。※ 同関数群の `_safe_frame` は §3 #6/#7 で候補。 |
| `providers/yahoo_fundamentals.py:196` | `_safe_frame` | `frequency=` 引数なしでの**再試行**（1 段目）。最終失敗は 199/201 が受ける（候補側）。 |
| `services/ai_agent.py:2056` | `_parse_arguments` | 壊れた tool 引数を `{'_raw': value}` として保全し LLM に返す（捨てない）。 |
| `services/ai_agent.py:2112` | `_tool_get_orderbook` | `depth` の型不正を既定 10 にクランプ。値の意味を変えない。 |
| `services/broker_read_service.py:219` | `_notify_on_outcome` | コメント「never let a notification problem change the read outcome」。読み取り結果を守るための double defense。 |
| `services/codex_cli.py:88` | `codex_cli_status` | `version=None` だが 100 行が `reason=f'Could not inspect Codex login: …'` を返す（観測可能）。 |
| `services/private_http.py:106` | `_request` | `declared=0` でも 110 行が実バイト長で上限検査する（二重防御）。 |
| `services/strategy_outcomes.py:130` | `forward_outcome_report` | `benchmark_history=None` は 177 行で明示的に分岐（ベンチマーク無しとして扱う）。 |
| `stock_acquisition.py:35` | `_key_ts` | `crypto_acquisition.py:33` と同一の文字列化フォールバック。 |
| `vercel_observability.py:57` | `runtime_source_info` | パッケージメタ情報の欠落。観測用途のみで判断に影響しない。 |

（E1/E2/E3/E5/E7 の各 1 行単位の明細は `silent_v4.json` の `surfaced` フィールドと `line` で全件追跡可能。）

---

## 5. 未確認範囲（この監査が保証しないこと）

| 範囲 | 内容 |
|---|---|
| `tests/`（175 ファイル）・`browser_tests/` | 走査対象外。テストコードの握り潰しは未評価。 |
| `app.py`（リポジトリ直下）、`run_backtests_*.py`（7 本）、`scripts/`（6 本） | 走査対象外。`src/yowayowa` 外のため。 |
| except 以外の無言化 | `return []` / `or []` 等の**早期リターン**（grep で 96 箇所）は本監査の対象外。except に紐づかない「空を正常扱い」は未評価。 |
| 呼び出し側の「空を成功と解釈」 | 候補の現象説明では呼び出し元を最大 2 段まで追跡したが、全経路の網羅はしていない。 |
| 動的パターン | `except` を含む文字列の `eval`/`exec`、デコレータ内の握り潰し、非同期ジェネレータの中断は AST で検出できない。 |
| `status="ok"` 固定 | grep で 3 ファイルのみ（`operator_bridge/app.py:83`、`api/routes.py:99`、`vercel_observability.py:165`）。いずれもヘルス/メタ応答で、例外ハンドラ内の固定 ok は **0 件**。 |
| 実行時挙動 | 本監査は静的解析のみ。候補 #1〜#3 の再現（破損ファイル投入）は行っていない。 |

---

## 6. 次にやるなら（提案・未実施）

1. #1 `orderbook_service.get_snapshot`: 破損検知時に合成板を返さず明示エラーにする、または seed の provenance を `provider="synthetic"` / `license_class=…` に落として `record_snapshot` で上書きしない。
2. #2/#3 registry `load()`: `JSONDecodeError` を「0 件」ではなく明示エラーにし、少なくとも `save()` で破損ファイルを上書きしない（`.corrupt` へ退避）。
3. #7 `_to_int`: 数量は `int | None` にし、0 と「不明」を分離。
4. 横断: このコードベースには logging が無いため、収集リスト（`errors[]`）へ積む規約を機械的に強制する lint（`except` 本文に `errors`/`warnings` 追加か `raise` が無ければ警告）を入れると再発を防げる。

---

*本監査は読み取り専用。リポジトリのコード・git 履歴・設定には一切変更を加えていない。*
