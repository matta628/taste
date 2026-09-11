#!/bin/bash
# Tastemaker pipeline runner for cron.
#
# Triggers a pipeline through the backend's own HTTP endpoint rather than
# running `python -m backend.pipelines.*` directly. That matters on this box:
# DuckDB allows a single writer, and the API container already holds the
# database. Going through the endpoint means the pipeline runs as a child of
# that same container, which is the pattern the app already uses for its
# manual "Sync" buttons.
#
# Every pipeline here is incremental and idempotent — a run that's interrupted
# (reboot, rate limit, container restart) just resumes on the next tick.
#
# Usage: pipelines.sh <lastfm|lyrics|mood|visuals|musicbrainz|rebuild-stats>
set -uo pipefail

API=http://localhost:8000
LOG=/home/mambo/logs/tastemaker_pipelines.log
LOCK=/tmp/tastemaker_pipeline_$1.lock

mkdir -p /home/mambo/logs

# Two locks. The per-pipeline one stops a slow pass stacking up behind itself;
# the global one stops two *different* pipelines running at once. DuckDB allows
# a single writer, so even with chunked flushes an overlap can collide — a
# concurrent lyrics run has already killed a visuals backfill this way.
exec 201>/tmp/tastemaker_pipeline_global.lock
flock -n 201 || { echo "$(date -Is) [$1] another pipeline is running, skipping" >> "$LOG"; exit 0; }

exec 200>"$LOCK"
flock -n 200 || { echo "$(date -Is) [$1] already running, skipping" >> "$LOG"; exit 0; }

case "$1" in
  lastfm)        EP="/pipelines/lastfm/sync" ;;
  lyrics)        EP="/pipelines/lyrics/fetch" ;;
  mood)          EP="/pipelines/mood/analyze" ;;
  visuals)       EP="/pipelines/visuals/fetch" ;;
  musicbrainz)   EP="/pipelines/musicbrainz/enrich" ;;
  rebuild-stats) EP="/analytics/rebuild-stats" ;;
  *) echo "$(date -Is) unknown pipeline: $1" >> "$LOG"; exit 2 ;;
esac

if ! curl -sf -o /dev/null "$API/health" --max-time 10; then
    echo "$(date -Is) [$1] backend not responding at $API — skipping" >> "$LOG"
    exit 1
fi

RESP=$(curl -sS -X POST "$API$EP" --max-time 30 2>&1)
echo "$(date -Is) [$1] -> $RESP" >> "$LOG"

# The endpoint returns 202 immediately and keeps working inside the container,
# so releasing the lock here would defeat it. Hold until the pipeline's process
# is actually gone.
# The slim backend image has no `ps`, but container processes are visible in
# the host's PID namespace, so match them from here.
for _ in $(seq 1 720); do   # up to 6h at 30s
    sleep 30
    if ! ps -eo args 2>/dev/null | grep -q "[b]ackend\.pipelines"; then
        break
    fi
done
echo "$(date -Is) [$1] finished" >> "$LOG"
