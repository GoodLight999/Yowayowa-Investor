#!/bin/bash
# demo-loop fill (spec §9): flock → 1h fetch+tape → resolve_fills (guard) → loop_run。
# cron: UTC 02:05 (JST 11:05)。02:00に確定した01:00足以降を使うため02:05起動。
set -e
LOCK=/root/current-work/yowayowa-investor/data/demo-loop/.lock
mkdir -p "$(dirname "$LOCK")"
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "demo-loop fill: another run holds the lock; exit silently"
  exit 0
fi
REPO=/root/current-work/yowayowa-investor
cd "$REPO"
export YOWAYOWA_MODE=personal
export PYTHONPATH="$REPO/src"
# v1.2 (t_d0fbc804): equity fills resolve ONLY from the local daily store —
# if the 00:20Z daily fetch missed the decision day's session bar (fetch
# failure, vendor delay), the pendings used to wait a full extra day with no
# journal trace (measured 2026-10-19 → 2026-10-21, ~45 h to Book C's first
# fill). Refresh the equity dailies right before resolving so the 02:05Z run
# is self-sufficient. Symbols = the frozen EQUITY_UNIVERSE (same as daily.sh).
"$REPO/.venv/bin/yowayowa" stock-fetch SPY IWM JNJ PG --days 10 \
  || echo "stock-fetch failed (unresolved equity pendings will record fill_wait rows)"
"$REPO/.venv/bin/python" -m yowayowa.loop_cli fill
