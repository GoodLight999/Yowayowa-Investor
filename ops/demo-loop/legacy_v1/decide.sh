#!/bin/bash
# wrapper: demo-loop decide (cron entry)。直前に Binance 日足を冪等追記する。
# 鍵不要（Binance公開API）。personal モードは provider policy 上必須。
set -e
REPO=/root/current-work/yowayowa-investor
cd "$REPO"
export YOWAYOWA_MODE=personal
"$REPO/.venv/bin/yowayowa" crypto-fetch BTC ETH --days 5 || echo "crypto-fetch failed (HALT rows will be recorded)"
exec bash "$REPO/ops/demo-loop/demo_loop.sh" decide
