#!/bin/bash
# wrapper: demo-loop mark (cron entry)。実行日の日足を先に冪等追記してからmark。
set -e
REPO=/root/current-work/yowayowa-investor
cd "$REPO"
export YOWAYOWA_MODE=personal
"$REPO/.venv/bin/yowayowa" crypto-fetch BTC ETH --days 3 || echo "crypto-fetch failed (mark will skip)"
exec bash "$REPO/ops/demo-loop/demo_loop.sh" mark
