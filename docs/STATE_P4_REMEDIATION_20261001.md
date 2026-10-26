# P4是正スレッド状態スナップショット（2026-10-01 14:05 JST・COO記）

## 現在の滞留（原因: agy 5時間枠切れ・Resets ~15:45 JST）
- t_1509396e【P4再監査REJECT是正】→ **done**（worker-solが93be654実装+自己確認ツール同梱で完遂・CTO統合待ち）
  - 実装: quote currencyの明示必須・lineage保持・派生不正系列のcalendar除外
  - 実測: make verify 1645 passed/2 skipped、旧6FAIL期待値未変更で6/6、独立110/110×2、37/17/447 probes、typed両API、crypto110/110・JSON/MD一致
  - 注意: run 268の「レビュー承認」は **同worker-sol系統の自己確認**であり第三者独立監査ではない
- t_c257ac6e【P4是正統合】93be654のcanonical統合+CTO検収→独立再監査手配 → **blocked(transient)**・agy回復後に再開
  - cronjob 71ff07f35d85（15:50 JST 1回）が unblock+dispatch を自動実行する
- t_45e4e276【P4 LOW文書契約】→ todo（t_c257ac6e完了後に自動昇格・worker-luna担当）

## 重複カード整理
- t_32012407（2本目監査 t_8e13a106 起票）→ **archived**・所見は t_1509396e に統合コメント済み
- 証拠: /root/.hermes/kanban/boards/yowayowa/attachments/t_8e13a106/{audit_report.txt,audit_evidence_t_8e13a106.zip}
- 実装側: /root/.hermes/kanban/boards/yowayowa/attachments/t_1509396e/{implementation_handoff.txt,implementation_evidence_t_1509396e.zip}

## 運用上の教訓
- agy枠切れの実害: kanban再試行が枠回復まで失敗し続ける。**crash連発時はclaim_lockのstatus確認→reclaim→別レーン（worker-sol/oauth-2等）へassign→dispatch** が有効だった（今回実証）。
- worker-sol (openai-codex oauth-2 priority=1) は枠切れしていない。worker-lunaのoauth-1のみ exhausted。
- レーン切替はPOLICY.mdのモデル階層（2026-10-01マスター許可）に基づく。CTO検収はagy復帰後に必ず実施すること（職務分離）。

## 追記（2026-10-02 20:15 JST）〜 安全保護フェーズ1の監査ループ進捗
- 遷移: 8791db6 (REJECT HIGH4/M2) → 856ad33 (REJECT HIGH3) → 3272231 (REJECT HIGH2/M2) → 1ab37ff (REJECT HIGH1) → f585b8d (REJECT HIGH1/M1) → **現行是正 t_be15212c（AS-F2-01-R4 日跨ぎstale attempt時刻 + AS-F2-04 所有枠ガード）**
- 監査残余は縮小傾向: 4→3→3(2/2)→1→1+1。原本契約74/74は全ラウンドPASS安定・回帰なし。
- t_be15212c は agy 枠切れで2クラッシュ→自動blocked。**cron 655742f8f4f1（21:36 JST）が unblock+dispatch を自動実行**。
- 教訓追補: crash通知はよく遅延配信される。必ず show/runs で現状を実測してから対応・report する（完了済みタスクのcrash通知は無視でよい）。
## 追記（2026-10-02 20:50 JST）・運用拡張（マスター指示）
- system-kun /codexreset 新設: banked reset creditの有効期限を一次情報（codex quota APIのrateLimitResetCredits.expiresAt）で表示。実測: 保有3枚・期限 10/05 07:54 / 10/23 04:47 / 10/30 03:25。10/05 07:54失効分が最も緊急。
- GOAT月間リセット時期: /alpha/billing/credits はAPI-tokenでは非公開（実測・CodexBarも同報告）→ /zandakaには「APIが日付非公開」と正直表示。goat_month_watch.sh（free_tier_watcherに配線済み）が月跨ぎ急増を観測したら実績ベース表示に自動拡充。
- worker-sol本格稼働（マスター許可 2026-10-02）: 監督層ではなく作業員として本体稼働。fallback: space-bunny-alpha/mimo:free等。並列可（Codex枠使い倒し）。
- zai GLM-5.3-flash利用帯: 現規約どおり単一レーン(worker-flash/default)。マスターの「ピーク以外活用したい」は、非ピーク帯（平日19時〜翌15時・週末）にworker-flashへ軽業務を優先配分する形で対応（quota_status.shのピーク注記とセット）。
