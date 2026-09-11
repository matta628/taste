#!/bin/bash
# One-off: drive the initial backfills to completion, in order.
#   1. visuals  (Deezer artwork — incremental)
#   2. mood     (Claude lyric tagging — incremental, the long one)
# Both are resumable, so re-running this is always safe. Stats were rebuilt
# separately once the RANK()->ROW_NUMBER() fix shipped.
set -uo pipefail
LOG=/home/mambo/logs/finish_backfill.log
exec >>"$LOG" 2>&1
echo "=== $(date -Is) start ==="

# The pipeline endpoints return 202 *before* their worker process exists, so
# always grace-sleep before polling — checking immediately returns "idle" and
# lets the next step race this one for DuckDB's single write lock.
run_pipeline() {
  local name=$1 path=$2
  echo "$(date -Is) starting $name"
  curl -sS -X POST "http://localhost:8000$path" --max-time 30
  echo
  sleep 25
  for _ in $(seq 1 1200); do   # up to 10h
    ps -eo args 2>/dev/null | grep -q "[b]ackend\.pipelines" || { echo "$(date -Is) $name finished"; return 0; }
    sleep 30
  done
  echo "$(date -Is) $name TIMED OUT"; return 1
}

run_pipeline visuals /pipelines/visuals/fetch
run_pipeline mood    /pipelines/mood/analyze
echo "=== $(date -Is) done ==="
