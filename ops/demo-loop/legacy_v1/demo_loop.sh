#!/bin/bash
# demo-loop v1: zero-order paper loop, no_agent cron body (VPS-light).
# Modes via argv: decide | fill | mark | report  (weekly report: report)
# Schedule (UTC): decide 00:15, fill 02:05, mark 03:30, report Fri 04:00.
set -e
REPO=/root/current-work/yowayowa-investor
PY=$REPO/.venv/bin/python
export YOWAYOWA_MODE=personal
cd "$REPO"
MODE="${1:-}"
case "$MODE" in
  decide|fill|mark|report)
    $PY -m yowayowa.loop_cli "$MODE"
    ;;
  *)
    echo "usage: demo_loop.sh {decide|fill|mark|report}" >&2
    exit 2
    ;;
esac
