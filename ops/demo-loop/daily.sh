#!/bin/bash
# demo-loop daily (spec §9): flock → crypto-fetch → mark(前日分) → decide(+mtm) → anchor。
# cron: UTC 00:20 (JST 09:20)。no_agent。軽い処理のみ。
set -e
LOCK=/root/current-work/yowayowa-investor/data/demo-loop/.lock
mkdir -p "$(dirname "$LOCK")"
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "demo-loop daily: another run holds the lock; exit silently"
  exit 0
fi
REPO=/root/current-work/yowayowa-investor
cd "$REPO"
export YOWAYOWA_MODE=personal
export PYTHONPATH="$REPO/src"
PY="$REPO/.venv/bin/python"
"$REPO/.venv/bin/yowayowa" crypto-fetch BTC ETH --days 5 \
  || echo "crypto-fetch failed (HALT rows will be recorded)"
# 10 calendar days (not 5): the window must still contain the previous session
# after a long weekend plus a market holiday.  The provider sends one request
# per symbol regardless of the width, and re-appends are idempotent per
# (provider, currency, as_of), so the wider window costs nothing.
"$REPO/.venv/bin/yowayowa" stock-fetch SPY IWM JNJ PG --days 10 \
  || echo "stock-fetch failed (HALT rows will be recorded)"
"$PY" -m yowayowa.loop_cli mark || true
"$PY" -m yowayowa.loop_cli decide
"$PY" -c "from yowayowa.paperloop import loop_root, _write_anchor; from pathlib import Path; _write_anchor(loop_root(Path('./data')))" \
  || true
