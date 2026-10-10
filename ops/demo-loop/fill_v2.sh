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
"$REPO/.venv/bin/python" -m yowayowa.loop_cli fill
