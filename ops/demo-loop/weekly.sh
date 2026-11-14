#!/bin/bash
# demo-loop weekly (spec §9): flock → paperloop_verify(prev week) → weekly_summary。
# cron: 毎月曜 UTC 00:50 (JST 09:50)。検証→サマリの順。
set -e
LOCK=/root/current-work/yowayowa-investor/data/demo-loop/.lock
mkdir -p "$(dirname "$LOCK")"
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "demo-loop weekly: another run holds the lock; exit silently"
  exit 0
fi
REPO=/root/current-work/yowayowa-investor
cd "$REPO"
export YOWAYOWA_MODE=personal
export PYTHONPATH="$REPO/src"
PY="$REPO/.venv/bin/python"
# verify (best effort; a chain break exits 2 and fails the job)
FROM=$(date -u -d '7 days ago' +%F)
TO=$(date -u +%F)
"$PY" -m yowayowa.paperloop_verify --data-dir ./data --from "$FROM" --to "$TO" \
  --json data/demo-loop/verify/latest.json || exit 2
# CUSUM edge-lifetime monitor for book A (PREREG_S01_EXT_2017_2022 section 5;
# prereg.jsonl row __cusum_A__): z_w/S_w over complete ISO weeks of the mtm
# history with the registered constants; S_w > 4.8 records PAUSE and S is
# never reset after an alarm. Fail-closed (set -e): a monitor failure fails
# the weekly job loudly instead of silently skipping the vital.
"$PY" -m yowayowa.cusum_watch --data-dir ./data
# weekly_summary() already appends the row itself (paperloop.py, summary_path);
# teeing it here wrote EVERY row twice (measured 2026-10-12: one run produced
# two identical lines). Print only - stdout is the cron log.
"$PY" -m yowayowa.loop_cli report
