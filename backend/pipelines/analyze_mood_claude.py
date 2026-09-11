"""
Mood analysis via headless Claude — runs on the Pi, no torch required.

Replaces the laptop-only `analyze_mood.py` (transformers + a DeBERTa zero-shot
classifier, ~1GB RAM and 6-18h of CPU for a full backfill on a Pi 5). This
version batches tracks through the host-side Claude bridge instead: no new
dependencies, ~350MB peak RAM for one `claude` subprocess, and it bills the
subscription rather than the metered API.

It writes the same `track_mood` schema the old pipeline did — `tags` plus a
`scores` JSON of every label — so `MoodChart`, the `/mood` review UI, the
agent's `query_database` docstring, and `analyze_mood.py --retag` all keep
working unchanged.

Two properties that matter for running unattended:
  - **Lock-friendly.** The DuckDB write lock is only held while a finished batch
    is written. The Claude call happens with the database closed, so the API
    stays responsive through an hour-long backfill.
  - **Resumable.** Work is selected by "in track_lyrics but not track_mood", so
    a rate-limit stop, a crash, or a reboot just means the next run picks up
    where this one left off. Rows with overridden=true are never touched.

Usage:
    python -m backend.pipelines.analyze_mood_claude [--limit N] [--batch-size N]
"""
import argparse
import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from backend.db.schema import DB_PATH, get_connection
from backend.pipelines.analyze_mood import MOOD_LABELS, SCORE_THRESH, _CREATE_TRACK_MOOD

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

BATCH_SIZE = int(os.environ.get("MOOD_BATCH_SIZE", "20"))
# Enough lyric to establish mood without blowing up the prompt. Choruses repeat,
# so the opening is nearly always representative.
LYRIC_CHARS = 1200
MODEL_LABEL = f"claude:{os.environ.get('BRIDGE_MODEL', 'sonnet')}"
MAX_RETRIES = 4

SYSTEM_PROMPT = f"""You are a music mood classifier. You will be given song lyrics.

For EACH song, score how strongly it expresses each of these {len(MOOD_LABELS)} moods,
from 0.0 (absent) to 1.0 (dominant):
{", ".join(MOOD_LABELS)}

Rules:
- Score EVERY label for EVERY song. Never omit one.
- Moods are multi-label: a song can be both melancholic and tender. Score them independently.
- Judge the emotional content of the writing, not the genre or tempo you imagine.
- Be decisive. Most labels for a given song should be near 0.0; only genuinely
  present moods should exceed 0.5. Do not hedge everything into the middle.
- Return one entry per song, in the same order given, echoing track and artist EXACTLY
  as provided so results can be matched back.
"""

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "songs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "track": {"type": "string"},
                    "artist": {"type": "string"},
                    "scores": {
                        "type": "object",
                        "properties": {label: {"type": "number"} for label in MOOD_LABELS},
                        "required": MOOD_LABELS,
                    },
                },
                "required": ["track", "artist", "scores"],
            },
        }
    },
    "required": ["songs"],
}

# Claude's own phrasing when the subscription cap is hit — treat as retryable,
# not as a failed batch (mirrors the JAT watcher's handling).
_RATE_LIMIT = re.compile(r"usage limit|session limit|rate limit|429|overloaded", re.I)


def _clean(text: str) -> str:
    text = re.sub(r"\s+", " ", (text or "")).strip()
    return text[:LYRIC_CHARS]


def _pending(conn, limit: int | None) -> list[tuple[str, str, str]]:
    rows = conn.execute("""
        SELECT l.track, l.artist, l.lyrics
        FROM track_lyrics l
        LEFT JOIN track_mood m
          ON LOWER(m.track) = LOWER(l.track) AND LOWER(m.artist) = LOWER(l.artist)
        WHERE m.track IS NULL
          AND l.lyrics IS NOT NULL AND l.lyrics <> ''
        ORDER BY l.artist, l.track
    """).fetchall()
    return rows[:limit] if limit else rows


def _build_prompt(batch: list[tuple[str, str, str]]) -> str:
    parts = [f"Classify these {len(batch)} songs.\n"]
    for i, (track, artist, lyrics) in enumerate(batch, 1):
        parts.append(f"--- SONG {i} ---\nTRACK: {track}\nARTIST: {artist}\nLYRICS: {_clean(lyrics)}\n")
    return "\n".join(parts)


async def _classify(batch: list[tuple[str, str, str]]) -> list[dict]:
    from backend.agent.bridge_client import call_json

    raw = await call_json(_build_prompt(batch), SYSTEM_PROMPT, json_schema=RESPONSE_SCHEMA)
    return json.loads(raw).get("songs", [])


def _write(rows: list[tuple]):
    conn = get_connection()
    try:
        conn.execute(_CREATE_TRACK_MOOD)
        # overridden rows are user corrections from the /mood review UI — the
        # classifier must never clobber them.
        conn.executemany("""
            INSERT INTO track_mood (track, artist, tags, scores, model, analyzed_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (track, artist) DO UPDATE SET
                tags        = excluded.tags,
                scores      = excluded.scores,
                model       = excluded.model,
                analyzed_at = excluded.analyzed_at
            WHERE track_mood.overridden = FALSE
        """, rows)
    finally:
        conn.close()


def run(limit: int | None = None, batch_size: int = BATCH_SIZE):
    conn = get_connection()
    try:
        conn.execute(_CREATE_TRACK_MOOD)
        pending = _pending(conn, limit)
    finally:
        conn.close()  # lock released before any network work

    if not pending:
        log.info("[mood] Nothing to analyze — every track with lyrics is already tagged.")
        return

    batches = [pending[i:i + batch_size] for i in range(0, len(pending), batch_size)]
    log.info(f"[mood] {len(pending)} tracks to analyze in {len(batches)} batches of ≤{batch_size}")

    done = failed = 0
    for bi, batch in enumerate(batches, 1):
        wanted = {(t.lower(), a.lower()): (t, a) for t, a, _ in batch}
        results, attempt = None, 0

        while attempt < MAX_RETRIES:
            attempt += 1
            try:
                results = asyncio.run(_classify(batch))
                break
            except Exception as e:
                msg = str(e)
                if _RATE_LIMIT.search(msg):
                    # Subscription cap, not a bug. Back off; if it persists, stop
                    # cleanly — the next cron run resumes from the same place.
                    wait = min(60 * (2 ** (attempt - 1)), 600)
                    log.warning(f"[mood] batch {bi}: rate limited, sleeping {wait}s ({msg[:120]})")
                    time.sleep(wait)
                    continue
                log.warning(f"[mood] batch {bi} attempt {attempt} failed: {msg[:200]}")
                time.sleep(5)

        if results is None:
            failed += 1
            log.error(f"[mood] batch {bi} gave up after {MAX_RETRIES} attempts — will retry next run")
            if failed >= 3:
                log.error("[mood] 3 batches failed in a row; stopping. Rerun to resume.")
                break
            continue
        failed = 0

        rows, now = [], datetime.now(tz=timezone.utc)
        for song in results:
            key = (str(song.get("track", "")).lower(), str(song.get("artist", "")).lower())
            if key not in wanted:
                # Claude echoed something we didn't send; skip rather than
                # inserting a row that can never join back to track_lyrics.
                continue
            track, artist = wanted[key]
            scores = {k: float(v) for k, v in (song.get("scores") or {}).items() if k in MOOD_LABELS}
            if not scores:
                continue
            tags = sorted(
                [lbl for lbl, sc in scores.items() if sc >= SCORE_THRESH],
                key=lambda l: scores[l], reverse=True,
            )
            rows.append((track, artist, tags, json.dumps(scores), MODEL_LABEL, now))

        if rows:
            _write(rows)
            done += len(rows)
        log.info(f"[mood] batch {bi}/{len(batches)} → {len(rows)} tagged ({done} total)")

    log.info(f"[mood] Done. {done} tracks analyzed into {DB_PATH.name}.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Only process the first N pending tracks")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    args = parser.parse_args()
    run(limit=args.limit, batch_size=args.batch_size)
