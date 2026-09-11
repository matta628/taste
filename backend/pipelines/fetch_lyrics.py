"""
Lyrics fetch pipeline — runs on Pi (HTTP only, no heavy deps).

Fetches full lyrics for every distinct track in raw_scrobbles via lyrics.ovh (free, no key).
Fully incremental: skips tracks already in track_lyrics or enrichment_skipped.

Usage:
    python -m backend.pipelines.fetch_lyrics
"""
import logging
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

from backend.db.schema import get_connection

root = Path(__file__).parent.parent.parent
load_dotenv(root / ".env")
load_dotenv(root / ".env.secret")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

LYRICS_OVH_BASE = "https://api.lyrics.ovh/v1"
SLEEP_BETWEEN   = 0.5
ENTITY_TYPE     = "track_lyrics"


def _fetch_lyrics(session: requests.Session, track: str, artist: str) -> str | None:
    try:
        url = f"{LYRICS_OVH_BASE}/{urllib.parse.quote(artist)}/{urllib.parse.quote(track)}"
        resp = session.get(url, timeout=10)
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            log.warning(f"[lyrics] {resp.status_code} for {artist!r} — {track!r}")
            return None
        lyrics = resp.json().get("lyrics", "")
        if not lyrics or not lyrics.strip():
            return None
        return lyrics
    except Exception as e:
        log.warning(f"[lyrics] Request error for {artist!r} — {track!r}: {e}")
        return None


def _clean_lyrics(raw: str) -> str:
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    # Collapse 3+ consecutive blank lines to 2
    import re
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Strip trailing whitespace per line
    lines = [l.rstrip() for l in text.split("\n")]
    # Drop leading/trailing empty lines
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


# Writes are flushed in chunks. The previous version held one connection open
# for the whole loop — with ~1,500 tracks at 0.5s each that pinned the DuckDB
# write lock for ~13 minutes, which blocks every API request and hard-fails any
# other pipeline that happens to overlap (it killed a visuals backfill).
FLUSH_EVERY = 50


def _flush(rows: list[tuple], skips: list[tuple]):
    if not rows and not skips:
        return
    conn = get_connection()
    try:
        if rows:
            conn.executemany("""
                INSERT INTO track_lyrics (track, artist, lyrics, source, fetched_at)
                VALUES (?, ?, ?, 'lyrics.ovh', ?)
                ON CONFLICT (track, artist) DO UPDATE SET
                    lyrics     = excluded.lyrics,
                    source     = excluded.source,
                    fetched_at = excluded.fetched_at
            """, rows)
        if skips:
            conn.executemany("""
                INSERT INTO enrichment_skipped (entity_type, entity_name, reason)
                VALUES (?, ?, ?) ON CONFLICT DO NOTHING
            """, skips)
    finally:
        conn.close()
    rows.clear()
    skips.clear()


def run():
    conn = get_connection()
    try:
        tracks = conn.execute("""
            SELECT DISTINCT s.track, s.artist
            FROM raw_scrobbles s
            WHERE (s.track, s.artist) NOT IN (SELECT track, artist FROM track_lyrics)
              AND (s.track || '||' || s.artist) NOT IN (
                  SELECT entity_name FROM enrichment_skipped WHERE entity_type = 'track_lyrics'
              )
            ORDER BY s.artist, s.track
        """).fetchall()
    finally:
        conn.close()  # lock released before any HTTP work

    log.info(f"[lyrics] {len(tracks)} tracks to fetch")

    session = requests.Session()
    session.headers.update({"User-Agent": "Tastemaker/1.0"})

    rows: list[tuple] = []
    skips: list[tuple] = []
    fetched = skipped = 0

    for i, (track, artist) in enumerate(tracks):
        raw = _fetch_lyrics(session, track, artist)

        if raw is None:
            skips.append((ENTITY_TYPE, f"{track}||{artist}", "no_lyrics"))
            skipped += 1
        else:
            cleaned = _clean_lyrics(raw)
            if len(cleaned) < 20:
                skips.append((ENTITY_TYPE, f"{track}||{artist}", "too_short"))
                skipped += 1
            else:
                rows.append((track, artist, cleaned, datetime.now(tz=timezone.utc)))
                fetched += 1

        if (i + 1) % FLUSH_EVERY == 0:
            _flush(rows, skips)
        if (i + 1) % 100 == 0:
            log.info(f"[lyrics] {i + 1}/{len(tracks)} processed — {fetched} fetched, {skipped} skipped")

        time.sleep(SLEEP_BETWEEN)

    _flush(rows, skips)

    conn = get_connection()
    try:
        conn.execute("""
            INSERT INTO pipeline_state (pipeline_name, last_fetched_at)
            VALUES ('fetch_lyrics', ?)
            ON CONFLICT (pipeline_name) DO UPDATE SET last_fetched_at = excluded.last_fetched_at
        """, [datetime.now(tz=timezone.utc)])
    finally:
        conn.close()
    log.info(f"[lyrics] Done. {fetched} fetched, {skipped} skipped out of {len(tracks)} tracks.")


if __name__ == "__main__":
    run()
