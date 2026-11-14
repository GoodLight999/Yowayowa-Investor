#!/bin/bash
# =============================================================================
# S-07 X4 — dry-run daily wrapper（cron script 実体 / 9 必須引数の唯一の供給点）
#
# 契約: docs/s07_x3_contract.md の X3 runner（yowayowa.hl_exec.s07_forward.runner）。
# 本 wrapper は dry-run 固定で runner を起動するだけ。発注経路・署名・鍵・
# 停止標識スクリプト・cron 設定には触れない（停止標識を起動・参照しない）。
#
# 供給表（X4 独立検収の不足① / COO 裁定 A-C）:
#   引数             供給元                                              備考
#   --day            前日 UTC 日（date -u -d yesterday +%F）              09:05 JST = 00:05 UTC 時点で完了済みの日
#   --market-json    <repo>/var/s07_forward/<day>/market.json             取得カード t_021cce3b の出力契約。本 wrapper は読むだけ
#   --out            <repo>/var/s07_forward/out                           固定。同日再実行は追記0（already_recorded）
#   --data-dir       <repo>/tests/fixtures/s07_authenticity/real/data     保存済み real fixture（replay 限定・読み取り専用）
#   --account        0xF6cB1079F73cA0163E2EfF24eFbB651f99E2Dd82          provenance.json の公開アドレス（秘密鍵なし）
#   --fills-json     .../real/info/userFills.json                         非空（X1 は空 fills を拒否するため）
#   --funding-json   .../real/info/userFunding.json
#   --state-json     .../real/info/clearinghouseState.json
#   --orders-json    .../real/info/openOrders.json
#
# 引数の扱い:
#   * runner へ渡すのは上表の固定値のみ。$@ の転送はしない。
#   * 任意の第1引数 YYYY-MM-DD だけを replay 用に受理する（calendar 妥当性を検証）。
#   * 発注経路を有効化するフラグは渡さない（本ファイルに文字列としても存在しない）。
#   * 子プロセスは env -i で起動し、継承した環境変数（API 秘密等）を一切渡さない。
#
# 事前検査（COO 裁定C）:
#   market.json が要求 day を覆っているか（当該日の日足終値バー t == day 00:00 UTC が
#   1本、かつ day 00:00〜23:00 UTC の 1 時間ごと funding が 24 件）を jq で確認し、
#   覆っていなければ理由付きで exit 2。静的な過去 snapshot を毎日 replay して
#   「成功」と扱うことを構造的に防ぐ。
#
# ログ（1 実行につき 1 行。stdout と logs/wrapper.log に同じ 1 行）:
#   ts=<UTC ISO8601> day=<day> rc=<exit> status=<runner status> out=<out>
#   ledger_lines=<n> reason=<理由|-> argv=<実行した全 argv>
#   runner の stdout/stderr は logs/last_runner.stdout.json / last_runner.stderr.log
#   に退避し、cron が見る stdout は常に 1 行に保つ。
#
# 終了コード: runner の終了コードをそのまま返す（0=成功 / 2=入力不正・X1 失敗・遮断）。
#   事前検査での停止も 2 + reason を残す（cron は last_status=failed として可視化する）。
#
# 変更履歴:
#   t_86d669ed: 旧停止標識起動 wrapper を置換（dry-run 専用 runner へ委譲）。
# =============================================================================
set -u
set -o pipefail

# --- 固定供給元（ここ以外から引数を組み立てない） ---------------------------
REPO="/root/current-work/yowayowa-investor"
PY="$REPO/.venv/bin/python"
WORK="$REPO/var/s07_forward"
OUT="$WORK/out"
LOGDIR="$WORK/logs"
LOG="$LOGDIR/wrapper.log"
FIXROOT="$REPO/tests/fixtures/s07_authenticity/real"
DATA_DIR="$FIXROOT/data"
FILLS_JSON="$FIXROOT/info/userFills.json"
FUNDING_JSON="$FIXROOT/info/userFunding.json"
STATE_JSON="$FIXROOT/info/clearinghouseState.json"
ORDERS_JSON="$FIXROOT/info/openOrders.json"
ACCOUNT="0xF6cB1079F73cA0163E2EfF24eFbB651f99E2Dd82"
MODULE="yowayowa.hl_exec.s07_forward.runner"

DAY=""
MARKET=""
RC=0
STATUS="none"
REASON=""
LEDGER=0
ARGV_STR="(not_invoked)"

ledger_lines() {
    if [ -f "$OUT/ledger.jsonl" ]; then
        wc -l <"$OUT/ledger.jsonl" | tr -d ' '
    else
        printf '0'
    fi
}

# 1 実行 1 行。stdout と log ファイルへ同じ行を出す。
emit() {
    local ts line
    ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    line="ts=$ts day=${DAY:-unknown} rc=$RC status=${STATUS:-none} out=$OUT ledger_lines=$LEDGER reason=${REASON:--} argv=$ARGV_STR"
    printf '%s\n' "$line"
    if [ -d "$LOGDIR" ]; then
        printf '%s\n' "$line" >>"$LOG" 2>/dev/null || true
    fi
}

fail() {
    RC=2
    REASON="$1"
    LEDGER="$(ledger_lines)"
    emit
    exit 2
}

# --- 引数（0 個 or 1 個の YYYY-MM-DD のみ） ---------------------------------
if [ "$#" -gt 1 ]; then
    fail "unexpected extra arguments: only an optional YYYY-MM-DD replay day is accepted"
fi
if [ "$#" -eq 1 ]; then
    case "$1" in
        [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]) DAY="$1" ;;
        *) fail "invalid day argument (expected YYYY-MM-DD): $1" ;;
    esac
    if [ "$(date -u -d "$DAY" +%F 2>/dev/null || true)" != "$DAY" ]; then
        fail "invalid calendar day: $DAY"
    fi
else
    DAY="$(date -u -d 'yesterday' +%F)"
fi
MARKET="$WORK/$DAY/market.json"

# runner へ渡す固定 argv（供給表の 9 引数だけ。ここ以外から組み立てない）
ARGV=(
    "$PY" -m "$MODULE"
    --day "$DAY"
    --market-json "$MARKET"
    --out "$OUT"
    --data-dir "$DATA_DIR"
    --account "$ACCOUNT"
    --fills-json "$FILLS_JSON"
    --funding-json "$FUNDING_JSON"
    --state-json "$STATE_JSON"
    --orders-json "$ORDERS_JSON"
)
printf -v ARGV_STR '%q ' "${ARGV[@]}"
ARGV_STR="${ARGV_STR% }"

# --- 事前検査（必須パスと当日カバレッジ） -----------------------------------
command -v jq >/dev/null 2>&1 || fail "jq is required for the market coverage check"
mkdir -p "$LOGDIR" "$OUT" 2>/dev/null || fail "cannot create directories under $WORK"
[ -x "$PY" ] || fail "python interpreter missing or not executable: $PY"
[ -f "$MARKET" ] || fail "market input missing for day $DAY: $MARKET"

START_MS=$(( $(date -u -d "$DAY 00:00:00" +%s) * 1000 ))
NEXT_MS=$(( START_MS + 86400000 ))
COVER="$(jq -r --argjson start "$START_MS" --argjson next "$NEXT_MS" '
    ((.response | type) == "array")
    and (([.response[] | select((.t | type) == "number" and .t == $start)] | length) == 1)
    and (([.funding[] | select((.time | type) == "number" and .time >= $start and .time < $next)] | length) == 24)
' "$MARKET" 2>/dev/null)" || fail "market input is not readable JSON: $MARKET"
[ "$COVER" = "true" ] || fail "market input does not cover day $DAY (need daily bar t=$START_MS and 24 hourly funding rows in [$START_MS,$NEXT_MS)): $MARKET"

# --- 実行（固定引数のみ・継承環境なし・cwd 固定） ---------------------------
STDOUT_FILE="$LOGDIR/last_runner.stdout.json"
STDERR_FILE="$LOGDIR/last_runner.stderr.log"
: >"$STDOUT_FILE" || fail "cannot write $STDOUT_FILE"
: >"$STDERR_FILE" || fail "cannot write $STDERR_FILE"

(
    cd "$REPO" || exit 2
    env -i \
        PATH=/usr/bin:/bin \
        TZ=UTC \
        PYTHONPATH="$REPO/src:$REPO" \
        PYTHONDONTWRITEBYTECODE=1 \
        "${ARGV[@]}"
) >"$STDOUT_FILE" 2>"$STDERR_FILE"
RC=$?

LEDGER="$(ledger_lines)"
STATUS="$(jq -r '.status // "none"' "$STDOUT_FILE" 2>/dev/null || true)"
[ -n "${STATUS:-}" ] || STATUS="none"
if [ "$RC" -ne 0 ]; then
    REASON="$(head -n 1 "$STDERR_FILE" 2>/dev/null | tr -d '\r' | cut -c1-400)"
    [ -n "$REASON" ] || REASON="runner exited $RC without a message"
fi
emit
exit "$RC"
