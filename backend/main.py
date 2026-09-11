"""
FastAPI application — Guitar song CRUD + practice logging + agent chat.
"""
from datetime import date, datetime, timezone
from typing import Literal
from uuid import uuid4

import asyncio
import json
import os
import time
import urllib.parse
import duckdb
import requests
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from pydantic import BaseModel, Field
from dotenv import load_dotenv

load_dotenv(".env.secret")

from backend.analytics import router as analytics_router

LYRICS_CACHE = Path("lyrics_cache.json")
LYRICS_CACHE_MAX_AGE_DAYS = 30  # invalidated by sync, not by age

from backend.db.schema import DB_PATH, create_schema
from backend.db.connect import connect as db_connect


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_schema()
    asyncio.create_task(_lyrics_background_fetch())
    yield


app = FastAPI(title="Tastemaker API", version="0.2.0", lifespan=lifespan)
app.include_router(analytics_router)

# Artist/album artwork fetched by backend.pipelines.fetch_visuals. Bind-mounted
# from ./data/images so it survives image rebuilds.
IMAGE_ROOT = Path(os.environ.get("IMAGE_ROOT", Path(__file__).parent.parent / "data" / "images"))
IMAGE_ROOT.mkdir(parents=True, exist_ok=True)
app.mount("/images", StaticFiles(directory=str(IMAGE_ROOT)), name="images")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173", "http://100.116.200.117:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

Status = Literal["want_to_learn", "learning", "learned", "abandoned"]
Part = Literal["chords", "tabs", "solo"]

# ── Pipeline run-state (in-process flags) ─────────────────────────────────
_enrich_running: bool = False
_enrich_last_error: str | None = None
_sync_running: bool = False
_goodreads_running: bool = False
_goodreads_last_error: str | None = None
_guitar_running: bool = False
_guitar_last_error: str | None = None
_mb_running: bool = False
_mb_last_error: str | None = None
_lyrics_fetch_running:    bool = False
_lyrics_fetch_last_error: str | None = None
_mood_running:    bool = False
_mood_last_error: str | None = None
_visuals_running:    bool = False
_visuals_last_error: str | None = None


def db():
    # Retries through a pipeline's short write-lock window — see backend/db/connect.py
    return db_connect()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

class SongCreate(BaseModel):
    title: str
    artist: str
    part: Part | None = None
    difficulty: int = Field(..., ge=1, le=5)   # required in form
    status: Status = "want_to_learn"            # required in form
    notes: str = ""
    date_started: date = Field(default_factory=date.today)


class SongUpdate(BaseModel):
    title: str | None = None
    artist: str | None = None
    part: Part | None = None
    difficulty: int | None = Field(None, ge=1, le=5)
    status: Status | None = None
    notes: str | None = None
    date_started: date | None = None


class Song(BaseModel):
    song_id: str
    title: str
    artist: str
    part: Part | None
    difficulty: int | None       # None = imported, not yet validated
    status: Status | None        # None = imported, not yet validated
    notes: str | None
    date_started: date | None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/songs", response_model=list[Song])
def list_songs(status: Status | None = None):
    conn = db()
    if status:
        rows = conn.execute(
            "SELECT * FROM guitar_songs WHERE status = ? ORDER BY updated_at DESC",
            [status]
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM guitar_songs ORDER BY updated_at DESC"
        ).fetchall()
    conn.close()
    cols = ["song_id", "title", "artist", "part", "difficulty", "status",
            "notes", "date_started", "created_at", "updated_at"]
    return [Song(**dict(zip(cols, r))) for r in rows]


@app.post("/songs", response_model=Song, status_code=201)
def add_song(body: SongCreate):
    song_id = str(uuid4())
    now = datetime.now(tz=timezone.utc)
    conn = db()
    conn.execute("""
        INSERT INTO guitar_songs
            (song_id, title, artist, part, difficulty, status, notes, date_started, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [song_id, body.title, body.artist, body.part, body.difficulty,
          body.status, body.notes, body.date_started, now, now])
    conn.close()
    return Song(song_id=song_id, **body.model_dump(), created_at=now, updated_at=now)


@app.put("/songs/{song_id}", response_model=Song)
def update_song(song_id: str, body: SongUpdate):
    conn = db()
    row = conn.execute(
        "SELECT * FROM guitar_songs WHERE song_id = ?", [song_id]
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Song not found")

    cols = ["song_id", "title", "artist", "part", "difficulty", "status",
            "notes", "date_started", "created_at", "updated_at"]
    current = dict(zip(cols, row))
    updates = body.model_dump(exclude_none=True)
    current.update(updates)
    now = datetime.now(tz=timezone.utc)
    current["updated_at"] = now

    conn.execute("""
        UPDATE guitar_songs
        SET title=?, artist=?, part=?, difficulty=?, status=?, notes=?, date_started=?, updated_at=?
        WHERE song_id=?
    """, [current["title"], current["artist"], current["part"], current["difficulty"],
          current["status"], current["notes"], current["date_started"], now, song_id])
    conn.close()
    return Song(**current)


@app.delete("/songs/{song_id}", status_code=204)
def delete_song(song_id: str):
    conn = db()
    result = conn.execute(
        "DELETE FROM guitar_songs WHERE song_id = ?", [song_id]
    )
    conn.close()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Song not found")


@app.post("/songs/{song_id}/practice", status_code=201)
def log_practice(song_id: str):
    conn = db()
    row = conn.execute(
        "SELECT song_id FROM guitar_songs WHERE song_id = ?", [song_id]
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Song not found")

    log_id = str(uuid4())
    now = datetime.now(tz=timezone.utc)
    conn.execute(
        "INSERT INTO practice_log (log_id, song_id, practiced_at) VALUES (?, ?, ?)",
        [log_id, song_id, now]
    )
    conn.execute(
        "UPDATE guitar_songs SET updated_at = ? WHERE song_id = ?", [now, song_id]
    )
    conn.close()
    return {"log_id": log_id, "song_id": song_id, "practiced_at": now}


@app.delete("/practice/{log_id}", status_code=204)
def delete_practice(log_id: str):
    conn = db()
    result = conn.execute("DELETE FROM practice_log WHERE log_id = ?", [log_id])
    conn.close()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Log entry not found")


@app.get("/songs/{song_id}/practice")
def get_practice_log(song_id: str):
    conn = db()
    rows = conn.execute(
        "SELECT log_id, practiced_at FROM practice_log WHERE song_id = ? ORDER BY practiced_at DESC LIMIT 10",
        [song_id]
    ).fetchall()
    conn.close()
    return [{"log_id": r[0], "practiced_at": r[1]} for r in rows]


# ---------------------------------------------------------------------------
# Taste
# ---------------------------------------------------------------------------

@app.get("/taste/top-artists")
def top_artists(days: int = 30, limit: int = 10):
    conn = db()
    rows = conn.execute("""
        SELECT artist, COUNT(*) as plays
        FROM raw_scrobbles
        WHERE scrobbled_at >= now() - INTERVAL (? || ' days')
        GROUP BY artist
        ORDER BY plays DESC
        LIMIT ?
    """, [days, limit]).fetchall()
    conn.close()
    return [{"artist": r[0], "plays": r[1]} for r in rows]


@app.get("/taste/top-tracks")
def top_tracks(days: int = 30, limit: int = 20):
    conn = db()
    rows = conn.execute("""
        SELECT track, artist, COUNT(*) as plays
        FROM raw_scrobbles
        WHERE scrobbled_at >= now() - INTERVAL (? || ' days')
        GROUP BY track, artist
        ORDER BY plays DESC
        LIMIT ?
    """, [days, limit]).fetchall()
    conn.close()
    return [{"track": r[0], "artist": r[1], "plays": r[2]} for r in rows]


def _genius_get(url: str, headers: dict, params: dict, retries: int = 3) -> requests.Response | None:
    """GET with retry on 429 (rate limit) and 5xx errors."""
    for attempt in range(retries):
        try:
            res = requests.get(url, headers=headers, params=params, timeout=8)
            if res.status_code == 429:
                wait = 10.0 * (attempt + 1)
                print(f"[genius] Rate limited — sleeping {wait:.0f}s (attempt {attempt+1}/{retries})")
                time.sleep(wait)
                continue
            if res.status_code >= 500:
                wait = 5.0 * (attempt + 1)
                print(f"[genius] {res.status_code} server error — sleeping {wait:.0f}s")
                time.sleep(wait)
                continue
            return res
        except requests.RequestException as e:
            wait = 5.0 * (attempt + 1)
            print(f"[genius] Request error ({e}) — sleeping {wait:.0f}s")
            time.sleep(wait)
    return None


def _genius_top_fragment(track: str, artist: str, token: str) -> str | None:
    """Get the most-upvoted annotated lyric fragment for a song via Genius API."""
    try:
        headers = {"Authorization": f"Bearer {token}"}

        # Step 1: search for the song to get its ID
        res = _genius_get(
            "https://api.genius.com/search",
            headers=headers,
            params={"q": f"{track} {artist}"},
        )
        if res is None or res.status_code != 200:
            return None
        hits = res.json().get("response", {}).get("hits", [])
        if not hits:
            return None
        song_id = hits[0]["result"]["id"]

        # Step 2: get all annotated fragments for the song
        res = _genius_get(
            "https://api.genius.com/referents",
            headers=headers,
            params={"song_id": song_id, "text_format": "plain", "per_page": 50},
        )
        if res is None or res.status_code != 200:
            return None
        referents = res.json().get("response", {}).get("referents", [])
        if not referents:
            return None

        # Score each fragment by its top annotation's vote count
        candidates = []
        for ref in referents:
            fragment = ref.get("fragment", "").strip()
            annotations = ref.get("annotations", [])
            if not fragment or not annotations:
                continue
            votes = max(a.get("votes_total", 0) for a in annotations)
            word_count = len(fragment.split())
            char_count = len(fragment)
            # Skip non-lyric fragments: too short/long, ratings (x/10), or URLs
            if not (3 <= word_count <= 30 and char_count <= 200):
                continue
            if any(x in fragment for x in ["http", "/10", "/5", "10/10", "@"]):
                continue
            candidates.append((votes, fragment))

        if not candidates:
            return None

        # Return the fragment with the most upvoted annotation
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]

    except Exception:
        return None


def _lyrics_ovh_snippet(track: str, artist: str) -> str | None:
    """Fallback: fetch a lyric snippet from lyrics.ovh (free, no key needed)."""
    try:
        res = requests.get(
            f"https://api.lyrics.ovh/v1/{urllib.parse.quote(artist)}/{urllib.parse.quote(track)}",
            timeout=8,
        )
        if res.status_code != 200:
            return None
        lyrics = res.json().get("lyrics", "")
        lines = [l.strip() for l in lyrics.splitlines() if l.strip()]
        if len(lines) < 2:
            return None
        start = max(0, len(lines) // 3)
        return " / ".join(lines[start:start + 3])
    except Exception:
        return None


def _lyrics_snippet(track: str, artist: str) -> str | None:
    """Try Genius top-annotated fragment first, fall back to lyrics.ovh."""
    token = os.environ.get("GENIUS_ACCESS_TOKEN")
    if token:
        fragment = _genius_top_fragment(track, artist, token)
        if fragment:
            return fragment
    return _lyrics_ovh_snippet(track, artist)


async def _lyrics_background_fetch():
    """On startup: fetch lyric snippets for top 100 tracks (last 30 days) via lyrics.ovh, cache to disk.
    Skips if cache is fresh. Rate-limited to 1 request per second."""
    if LYRICS_CACHE.exists() and LYRICS_CACHE.stat().st_size > 10:
        age_days = (time.time() - LYRICS_CACHE.stat().st_mtime) / 86400
        if age_days < LYRICS_CACHE_MAX_AGE_DAYS:
            cached = json.loads(LYRICS_CACHE.read_text())
            print(f"[lyrics] Cache fresh ({age_days:.1f}d old, {len(cached)} tracks) — skipping")
            return

    await asyncio.sleep(5)
    print("[lyrics] Starting lyrics fetch for top 100 tracks (last 30 days)…")

    try:
        # Through db_connect like every other connection in this process: a
        # read_only open alongside the API's read-write ones is a config
        # conflict for DuckDB (see backend/db/connect.py).
        conn = db_connect()
        rows = conn.execute("""
            SELECT track, artist, COUNT(*) as plays
            FROM raw_scrobbles
            WHERE scrobbled_at >= now() - INTERVAL '30 days'
            GROUP BY track, artist
            ORDER BY plays DESC
            LIMIT 100
        """).fetchall()
        conn.close()
        print(f"[lyrics] {len(rows)} tracks to look up")
    except Exception as e:
        print(f"[lyrics] DB query failed: {e}")
        return

    results = []
    for i, (track, artist, plays) in enumerate(rows):
        snippet = await asyncio.to_thread(_lyrics_snippet, track, artist)
        if snippet:
            results.append({"track": track, "artist": artist, "plays": plays, "snippet": snippet})
            print(f"[lyrics] ({i+1}/{len(rows)}) ✓ {artist} — {track}")
        else:
            print(f"[lyrics] ({i+1}/{len(rows)}) ✗ {artist} — {track}")
        await asyncio.sleep(1.5)

    LYRICS_CACHE.write_text(json.dumps(results))
    print(f"[lyrics] Done — {len(results)}/{len(rows)} with snippets")


@app.get("/taste/lyrics-snippets")
def lyrics_snippets():
    """Return cached Genius lyric snippets. Cache is populated in background on startup."""
    if LYRICS_CACHE.exists() and LYRICS_CACHE.stat().st_size > 10:
        return json.loads(LYRICS_CACHE.read_text())
    return []   # missing, or truncated by a sync and not refetched yet


# ---------------------------------------------------------------------------
# Pipelines
# ---------------------------------------------------------------------------

@app.get("/pipelines/status")
def pipelines_status():
    """Return last sync time, staleness, and enrichment progress."""
    conn = db()

    # Sync state timestamps
    rows = conn.execute("SELECT pipeline_name, last_fetched_at FROM pipeline_state").fetchall()
    state = {r[0]: r[1] for r in rows}
    now = datetime.now(tz=timezone.utc)

    def sync_entry(name):
        ts = state.get(name)
        if ts is None:
            return {"last_fetched_at": None, "days_ago": None, "stale": True}
        days = (now - ts).total_seconds() / 86400
        return {"last_fetched_at": ts.isoformat(), "days_ago": round(days, 1), "stale": days > 7}

    # Enrichment progress counts
    total_artists = conn.execute("SELECT COUNT(DISTINCT artist) FROM raw_scrobbles").fetchone()[0]
    total_tracks  = conn.execute("""
        SELECT COUNT(*) FROM (
            SELECT track, artist FROM raw_scrobbles
            GROUP BY track, artist HAVING COUNT(*) >= 2
        )
    """).fetchone()[0]

    done_artist_tags    = conn.execute("SELECT COUNT(DISTINCT artist_name) FROM artist_tags").fetchone()[0]
    done_artist_similar = conn.execute("SELECT COUNT(DISTINCT artist_name) FROM artist_similar").fetchone()[0]
    done_track_tags     = conn.execute("SELECT COUNT(DISTINCT track || artist) FROM track_tags").fetchone()[0]
    done_context_tags   = conn.execute("SELECT COUNT(DISTINCT track || artist) FROM track_context_tags").fetchone()[0]
    done_mb             = conn.execute("SELECT COUNT(*) FROM artist_mb").fetchone()[0]

    # Lyrics + mood counts
    lyrics_fetched = conn.execute("SELECT COUNT(*) FROM track_lyrics").fetchone()[0]
    lyrics_skipped = conn.execute(
        "SELECT COUNT(*) FROM enrichment_skipped WHERE entity_type = 'track_lyrics'"
    ).fetchone()[0]
    mood_analyzed  = conn.execute("SELECT COUNT(*) FROM track_mood").fetchone()[0]
    mood_overridden = conn.execute("SELECT COUNT(*) FROM track_mood WHERE overridden = TRUE").fetchone()[0]
    total_unique_tracks = conn.execute("SELECT COUNT(DISTINCT track || artist) FROM raw_scrobbles").fetchone()[0]

    # Goodreads book count
    total_books = conn.execute("SELECT COUNT(*) FROM raw_books").fetchone()[0]

    # Guitar songs count
    total_guitar = conn.execute("SELECT COUNT(*) FROM guitar_songs").fetchone()[0]

    conn.close()

    def pct(done, total):
        return round(done / total * 100, 1) if total else 0

    return {
        "lastfm": {**sync_entry("lastfm"), "process_running": _sync_running},
        "enrichment": {
            **sync_entry("lastfm_enrich"),
            "process_running": _enrich_running,
            "last_error":      _enrich_last_error,
            "artist_tags":    {"done": done_artist_tags,    "total": total_artists, "pct": pct(done_artist_tags,    total_artists)},
            "artist_similar": {"done": done_artist_similar, "total": total_artists, "pct": pct(done_artist_similar, total_artists)},
            "track_tags":     {"done": done_track_tags,     "total": total_tracks,  "pct": pct(done_track_tags,     total_tracks)},
            "context_tags":   {"done": done_context_tags,   "total": total_tracks},
        },
        "musicbrainz": {
            **sync_entry("musicbrainz"),
            "process_running": _mb_running,
            "last_error":      _mb_last_error,
            "artist_count":    {"done": done_mb, "total": total_artists, "pct": pct(done_mb, total_artists)},
        },
        "lyrics": {
            **sync_entry("fetch_lyrics"),
            "process_running": _lyrics_fetch_running,
            "last_error":      _lyrics_fetch_last_error,
            "fetched":  lyrics_fetched,
            "skipped":  lyrics_skipped,
            "total":    total_unique_tracks,
            "pct":      pct(lyrics_fetched, total_unique_tracks),
        },
        "mood": {
            "analyzed":   mood_analyzed,
            "overridden": mood_overridden,
            "total":      lyrics_fetched,
            "pct":        pct(mood_analyzed, lyrics_fetched),
        },
        "goodreads": {
            **sync_entry("goodreads"),
            "process_running": _goodreads_running,
            "last_error":      _goodreads_last_error,
            "book_count":      total_books,
        },
        "guitar_import": {
            **sync_entry("guitar_import"),
            "process_running": _guitar_running,
            "last_error":      _guitar_last_error,
            "song_count":      total_guitar,
        },
    }


@app.post("/pipelines/lastfm/sync", status_code=202)
async def trigger_lastfm_sync():
    """Kick off a Last.fm sync in the background. Returns immediately."""
    global _sync_running
    if _sync_running:
        return {"status": "already_running"}

    async def run():
        global _sync_running
        _sync_running = True
        try:
            import subprocess
            result = await asyncio.to_thread(
                subprocess.run,
                ["python", "-m", "backend.pipelines.lastfm"],
                capture_output=True, text=True
            )
            print("[lastfm] sync done:", result.stdout[-500:] if result.stdout else result.stderr[-500:])
            # Invalidate lyrics cache so it rebuilds with fresh scrobble data.
            # Truncate, don't unlink: the file is bind-mounted into the
            # container, and unlinking a mount point fails with EBUSY. That
            # used to kill this task before the stats rebuild below, so every
            # sync left artist/album/track stats stale.
            if LYRICS_CACHE.exists():
                LYRICS_CACHE.write_text("")
                print("[lyrics] Cache invalidated after sync — rebuilding in background")
                asyncio.create_task(_lyrics_background_fetch())
            # Rebuild pre-aggregated stats tables
            try:
                from backend.pipelines.rebuild_stats import rebuild_stats_tables
                await asyncio.to_thread(rebuild_stats_tables)
            except Exception as e:
                print(f"[rebuild_stats] Failed: {e}")
        finally:
            _sync_running = False

    asyncio.create_task(run())
    return {"status": "syncing"}


_rebuild_running: bool = False

@app.post("/analytics/rebuild-stats", status_code=202)
async def trigger_rebuild_stats():
    """Rebuild pre-aggregated stats tables (artist/album/track). Returns immediately."""
    global _rebuild_running
    if _rebuild_running:
        return {"status": "already_running"}

    async def run():
        global _rebuild_running
        _rebuild_running = True
        try:
            from backend.pipelines.rebuild_stats import rebuild_stats_tables
            await asyncio.to_thread(rebuild_stats_tables)
            print("[rebuild_stats] Done")
        except Exception as e:
            print(f"[rebuild_stats] Failed: {e}")
        finally:
            _rebuild_running = False

    asyncio.create_task(run())
    return {"status": "rebuilding"}


@app.post("/pipelines/lastfm/enrich", status_code=202)
async def trigger_lastfm_enrich():
    """Kick off enrichment. Rejects if already running to prevent stacked processes."""
    global _enrich_running, _enrich_last_error
    if _enrich_running:
        return {"status": "already_running"}

    async def run():
        global _enrich_running, _enrich_last_error
        _enrich_running = True
        _enrich_last_error = None
        try:
            import subprocess
            result = await asyncio.to_thread(
                subprocess.run,
                ["python", "-m", "backend.pipelines.lastfm", "enrich"],
                capture_output=True, text=True
            )
            tail = (result.stdout or result.stderr or "")[-800:]
            if result.returncode != 0:
                _enrich_last_error = f"Exit {result.returncode}: {(result.stderr or result.stdout or 'no output')[-400:]}"
                print(f"[lastfm] enrich FAILED (exit {result.returncode}):", tail)
            else:
                print("[lastfm] enrich done:", tail)
        except Exception as e:
            _enrich_last_error = str(e)
            print(f"[lastfm] enrich exception: {e}")
        finally:
            _enrich_running = False

    asyncio.create_task(run())
    return {"status": "enriching"}


@app.post("/pipelines/goodreads/upload", status_code=202)
async def upload_goodreads(file: UploadFile = File(...)):
    """Accept a Goodreads CSV export, save it, and run the import pipeline."""
    global _goodreads_running, _goodreads_last_error
    if _goodreads_running:
        return {"status": "already_running"}

    contents = await file.read()

    async def run():
        global _goodreads_running, _goodreads_last_error
        _goodreads_running = True
        _goodreads_last_error = None
        try:
            import subprocess, tempfile
            with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
                tmp.write(contents)
                tmp_path = tmp.name
            result = await asyncio.to_thread(
                subprocess.run,
                ["python", "-m", "backend.pipelines.goodreads", "--csv", tmp_path],
                capture_output=True, text=True
            )
            tail = (result.stdout or result.stderr or "")[-800:]
            if result.returncode != 0:
                _goodreads_last_error = f"Exit {result.returncode}: {(result.stderr or result.stdout or 'no output')[-400:]}"
                print(f"[goodreads] import FAILED (exit {result.returncode}):", tail)
            else:
                print("[goodreads] import done:", tail)
                # Update pipeline_state timestamp
                conn = db()
                conn.execute("""
                    INSERT INTO pipeline_state (pipeline_name, last_fetched_at)
                    VALUES ('goodreads', now())
                    ON CONFLICT (pipeline_name) DO UPDATE SET last_fetched_at = now()
                """)
                conn.close()
        except Exception as e:
            _goodreads_last_error = str(e)
            print(f"[goodreads] import exception: {e}")
        finally:
            _goodreads_running = False

    asyncio.create_task(run())
    return {"status": "importing"}


@app.post("/pipelines/guitar/upload", status_code=202)
async def upload_guitar(file: UploadFile = File(...)):
    """Accept an Ultimate Guitar My Tabs HTML file, save it, and run the import pipeline."""
    global _guitar_running, _guitar_last_error
    if _guitar_running:
        return {"status": "already_running"}

    contents = await file.read()

    async def run():
        global _guitar_running, _guitar_last_error
        _guitar_running = True
        _guitar_last_error = None
        try:
            import subprocess, tempfile
            with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as tmp:
                tmp.write(contents)
                tmp_path = tmp.name
            result = await asyncio.to_thread(
                subprocess.run,
                ["python", "-m", "backend.pipelines.ug", "--html", tmp_path],
                capture_output=True, text=True
            )
            tail = (result.stdout or result.stderr or "")[-800:]
            if result.returncode != 0:
                _guitar_last_error = f"Exit {result.returncode}: {(result.stderr or result.stdout or 'no output')[-400:]}"
                print(f"[guitar] import FAILED (exit {result.returncode}):", tail)
            else:
                print("[guitar] import done:", tail)
                # Update pipeline_state timestamp
                conn = db()
                conn.execute("""
                    INSERT INTO pipeline_state (pipeline_name, last_fetched_at)
                    VALUES ('guitar_import', now())
                    ON CONFLICT (pipeline_name) DO UPDATE SET last_fetched_at = now()
                """)
                conn.close()
        except Exception as e:
            _guitar_last_error = str(e)
            print(f"[guitar] import exception: {e}")
        finally:
            _guitar_running = False

    asyncio.create_task(run())
    return {"status": "importing"}


@app.post("/pipelines/musicbrainz/enrich", status_code=202)
async def trigger_musicbrainz_enrich():
    """Kick off MusicBrainz artist enrichment in the background (~30-40 min for full run)."""
    global _mb_running, _mb_last_error
    if _mb_running:
        return {"status": "already_running"}

    async def run():
        global _mb_running, _mb_last_error
        _mb_running = True
        _mb_last_error = None
        try:
            import subprocess
            result = await asyncio.to_thread(
                subprocess.run,
                ["python", "-m", "backend.pipelines.musicbrainz"],
                capture_output=True, text=True
            )
            tail = (result.stdout or result.stderr or "")[-800:]
            if result.returncode != 0:
                _mb_last_error = f"Exit {result.returncode}: {(result.stderr or result.stdout or 'no output')[-400:]}"
                print(f"[musicbrainz] enrich FAILED (exit {result.returncode}):", tail)
            else:
                print("[musicbrainz] enrich done:", tail)
        except Exception as e:
            _mb_last_error = str(e)
            print(f"[musicbrainz] enrich exception: {e}")
        finally:
            _mb_running = False

    asyncio.create_task(run())
    return {"status": "enriching"}


@app.post("/pipelines/lyrics/fetch", status_code=202)
async def trigger_lyrics_fetch():
    """Kick off lyrics fetch pipeline in the background."""
    global _lyrics_fetch_running, _lyrics_fetch_last_error
    if _lyrics_fetch_running:
        return {"status": "already_running"}

    async def run():
        global _lyrics_fetch_running, _lyrics_fetch_last_error
        _lyrics_fetch_running = True
        _lyrics_fetch_last_error = None
        try:
            import subprocess
            result = await asyncio.to_thread(
                subprocess.run,
                ["python", "-m", "backend.pipelines.fetch_lyrics"],
                capture_output=True, text=True
            )
            tail = (result.stdout or result.stderr or "")[-800:]
            if result.returncode != 0:
                _lyrics_fetch_last_error = f"Exit {result.returncode}: {(result.stderr or result.stdout or 'no output')[-400:]}"
                print(f"[lyrics] fetch FAILED (exit {result.returncode}):", tail)
            else:
                print("[lyrics] fetch done:", tail)
        except Exception as e:
            _lyrics_fetch_last_error = str(e)
            print(f"[lyrics] fetch exception: {e}")
        finally:
            _lyrics_fetch_running = False

    asyncio.create_task(run())
    return {"status": "fetching"}


def _bg_pipeline(module: str, args: list[str], tag: str, set_running, set_error):
    """Shared runner for the background pipeline endpoints."""
    async def run():
        set_running(True)
        set_error(None)
        try:
            import subprocess
            result = await asyncio.to_thread(
                subprocess.run,
                ["python", "-m", module, *args],
                capture_output=True, text=True,
            )
            tail = (result.stdout or result.stderr or "")[-800:]
            if result.returncode != 0:
                set_error(f"Exit {result.returncode}: {(result.stderr or result.stdout or 'no output')[-400:]}")
                print(f"[{tag}] FAILED (exit {result.returncode}):", tail)
            else:
                print(f"[{tag}] done:", tail)
        except Exception as e:
            set_error(str(e))
            print(f"[{tag}] exception: {e}")
        finally:
            set_running(False)

    return run


@app.post("/pipelines/mood/analyze", status_code=202)
async def trigger_mood_analyze(limit: int | None = None):
    """Tag lyric moods via headless Claude. Incremental and resumable."""
    global _mood_running
    if _mood_running:
        return {"status": "already_running"}

    def _set_running(v):
        global _mood_running
        _mood_running = v

    def _set_error(v):
        global _mood_last_error
        _mood_last_error = v

    args = ["--limit", str(limit)] if limit else []
    asyncio.create_task(_bg_pipeline(
        "backend.pipelines.analyze_mood_claude", args, "mood", _set_running, _set_error
    )())
    return {"status": "analyzing"}


@app.post("/pipelines/visuals/fetch", status_code=202)
async def trigger_visuals_fetch(kind: str = "both", window_days: int | None = None):
    """Fetch artist/album artwork from Deezer. Incremental."""
    global _visuals_running
    if _visuals_running:
        return {"status": "already_running"}

    def _set_running(v):
        global _visuals_running
        _visuals_running = v

    def _set_error(v):
        global _visuals_last_error
        _visuals_last_error = v

    args = ["--kind", kind]
    if window_days:
        args += ["--window-days", str(window_days)]
    asyncio.create_task(_bg_pipeline(
        "backend.pipelines.fetch_visuals", args, "visuals", _set_running, _set_error
    )())
    return {"status": "fetching"}


class MoodUpdateRequest(BaseModel):
    tags: list[str]


@app.get("/mood")
def get_mood(unreviewed: bool = False, tag: str | None = None, limit: int = 200):
    """Return mood-tagged tracks, sorted by confidence (uncertain first)."""
    conn = db()

    conditions = []
    params = []

    if unreviewed:
        conditions.append("tm.overridden = FALSE")
    if tag:
        conditions.append("? = ANY(tm.tags)")
        params.append(tag)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    rows = conn.execute(f"""
        SELECT
            tm.track,
            tm.artist,
            tm.tags,
            tm.scores,
            tm.overridden,
            COUNT(s.scrobbled_at) AS play_count
        FROM track_mood tm
        LEFT JOIN raw_scrobbles s ON LOWER(s.track) = LOWER(tm.track)
                                  AND LOWER(s.artist) = LOWER(tm.artist)
        {where}
        GROUP BY tm.track, tm.artist, tm.tags, tm.scores, tm.overridden
        ORDER BY tm.overridden ASC, play_count DESC
        LIMIT ?
    """, params + [limit]).fetchall()
    conn.close()

    result = []
    for track, artist, tags, scores_raw, overridden, play_count in rows:
        scores = {}
        try:
            scores = json.loads(scores_raw) if scores_raw else {}
        except Exception:
            pass

        if tag and tags and scores:
            confidence = scores.get(tag, 0.0)
        elif tags and scores:
            relevant = [scores.get(t, 0.0) for t in (tags or [])]
            confidence = round(sum(relevant) / len(relevant), 3) if relevant else 0.0
        else:
            confidence = 0.0

        result.append({
            "track":      track,
            "artist":     artist,
            "tags":       tags or [],
            "confidence": round(confidence, 3),
            "play_count": play_count,
            "overridden": overridden,
        })

    # Sort: unreviewed (overridden=False) first, within those lowest confidence first
    result.sort(key=lambda r: (r["overridden"], r["confidence"]))
    return result


@app.put("/mood/update")
def update_mood(track: str, artist: str, body: MoodUpdateRequest):
    """Override mood tags for a track. Sets overridden=TRUE."""
    conn = db()
    conn.execute("""
        UPDATE track_mood
        SET tags = ?, overridden = TRUE, analyzed_at = ?
        WHERE track = ? AND artist = ?
    """, (body.tags, datetime.now(tz=timezone.utc), track, artist))
    conn.close()
    return {"track": track, "artist": artist, "tags": body.tags, "overridden": True}


@app.post("/pipelines/enrichment/clear-no-data", status_code=200)
def clear_no_data_skips():
    """
    Clear enrichment_skipped entries with reason='no_data' so re-enrichment will
    retry them with updated thresholds. Does NOT touch max_retries entries.
    Call once after lowering Last.fm vote thresholds.
    """
    conn = db()
    before = conn.execute(
        "SELECT COUNT(*) FROM enrichment_skipped WHERE reason = 'no_data' AND entity_type IN ('artist_tags', 'track_tags')"
    ).fetchone()[0]
    conn.execute("""
        DELETE FROM enrichment_skipped
        WHERE reason = 'no_data'
          AND entity_type IN ('artist_tags', 'track_tags')
    """)
    conn.close()
    return {"deleted": before}


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    message: str
    thread_id: str = "default"


# ---------------------------------------------------------------------------
# Chat CRUD
# ---------------------------------------------------------------------------

class ChatCreate(BaseModel):
    title: str


class SaveMessagesRequest(BaseModel):
    messages: list[dict]


@app.get("/chats")
def list_chats():
    conn = db()
    rows = conn.execute(
        "SELECT chat_id, title, created_at, updated_at FROM chats ORDER BY updated_at DESC LIMIT 50"
    ).fetchall()
    conn.close()
    cols = ["chat_id", "title", "created_at", "updated_at"]
    return [dict(zip(cols, r)) for r in rows]


@app.post("/chats", status_code=201)
def create_chat(body: ChatCreate):
    chat_id = str(uuid4())
    now = datetime.now(tz=timezone.utc)
    conn = db()
    conn.execute(
        "INSERT INTO chats (chat_id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
        [chat_id, body.title, now, now]
    )
    conn.close()
    return {"chat_id": chat_id, "title": body.title, "created_at": now.isoformat()}


@app.get("/chats/{chat_id}/messages")
def get_chat_messages(chat_id: str):
    conn = db()
    rows = conn.execute(
        "SELECT role, content FROM chat_messages WHERE chat_id = ? ORDER BY seq ASC, created_at ASC",
        [chat_id]
    ).fetchall()
    conn.close()
    return [{"role": r[0], "content": r[1]} for r in rows]


@app.post("/chats/{chat_id}/messages", status_code=201)
def save_chat_messages(chat_id: str, body: SaveMessagesRequest):
    conn = db()
    max_seq_row = conn.execute(
        "SELECT COALESCE(MAX(seq), -1) FROM chat_messages WHERE chat_id = ?", [chat_id]
    ).fetchone()
    max_seq = max_seq_row[0] if max_seq_row else -1
    now = datetime.now(tz=timezone.utc)
    for i, msg in enumerate(body.messages):
        conn.execute(
            "INSERT INTO chat_messages (message_id, chat_id, role, content, seq, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            [str(uuid4()), chat_id, msg["role"], msg["content"], max_seq + 1 + i, now]
        )
    conn.execute("UPDATE chats SET updated_at = ? WHERE chat_id = ?", [now, chat_id])
    conn.close()
    return {"saved": len(body.messages)}


@app.delete("/chats/{chat_id}", status_code=204)
def delete_chat(chat_id: str):
    conn = db()
    conn.execute("DELETE FROM chat_messages WHERE chat_id = ?", [chat_id])
    result = conn.execute("DELETE FROM chats WHERE chat_id = ?", [chat_id])
    conn.close()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Chat not found")


class PlaylistRequest(BaseModel):
    prompt: str
    playlist_id: str | None = None  # set when modifying an existing playlist


# ---------------------------------------------------------------------------
# Playlist CRUD
# ---------------------------------------------------------------------------

@app.get("/playlists")
def list_playlists():
    conn = db()
    rows = conn.execute(
        "SELECT playlist_id, name, prompt, tracks, shortcuts_url, thoughts, queries, created_at, updated_at "
        "FROM playlists ORDER BY updated_at DESC"
    ).fetchall()
    conn.close()
    cols = ["playlist_id", "name", "prompt", "tracks", "shortcuts_url", "thoughts", "queries", "created_at", "updated_at"]
    return [dict(zip(cols, r)) for r in rows]


@app.delete("/playlists/{playlist_id}", status_code=204)
def delete_playlist(playlist_id: str):
    conn = db()
    result = conn.execute("DELETE FROM playlists WHERE playlist_id = ?", [playlist_id])
    conn.close()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Playlist not found")


@app.post("/agent/playlist")
async def agent_playlist(body: PlaylistRequest):
    """
    Generate or modify a playlist from a natural language prompt.
    Streams SSE. Emits a 'playlist' event with the final JSON, then auto-saves to DB.
    """
    from backend.agent.bridge_client import stream_agent

    # Build the prompt — include existing playlist context when modifying
    if body.playlist_id:
        conn = db()
        row = conn.execute(
            "SELECT name, tracks FROM playlists WHERE playlist_id = ?",
            [body.playlist_id]
        ).fetchone()
        conn.close()
        if row:
            existing = f'Existing playlist "{row[0]}" has tracks: {row[1]}. '
            system_msg = (
                f"{existing}The user wants to modify it: {body.prompt}. "
                "Query their personal data as needed, then call build_playlist with the updated track list."
            )
        else:
            system_msg = (
                f"The user wants a playlist. Query their personal data first, "
                f"then call build_playlist with the result. Their request: {body.prompt}"
            )
    else:
        system_msg = (
            "The user wants a playlist. Query their personal data first, "
            f"then call build_playlist with the result. Their request: {body.prompt}"
        )

    print(f"[playlist] Starting: {body.prompt!r}")

    async def stream():
        queries: list[str] = []
        thoughts_parts: list[str] = []

        gen = stream_agent(
            system_msg,
            thread_id=f"playlist-{body.playlist_id or id(body)}",
            mode="playlist",
        ).__aiter__()
        try:
            while True:
                try:
                    event = await asyncio.wait_for(gen.__anext__(), timeout=25.0)
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                kind = event.get("type")
                if kind == "text":
                    text = event.get("text") or ""
                    if text:
                        thoughts_parts.append(text)
                        encoded = "\n".join(f"data: {line}" for line in text.split("\n"))
                        yield f"{encoded}\n\n"

                elif kind == "tool_start":
                    yield f"event: tool_start\ndata: {event.get('name', 'tool')}\n\n"

                elif kind == "tool_input":
                    tool = event.get("name", "tool")
                    print(f"[playlist] Tool call: {tool}")
                    # Capture SQL queries for provenance
                    if tool == "query_database":
                        sql = (event.get("input") or {}).get("sql", "")
                        if sql:
                            queries.append(sql)

                elif kind == "error":
                    msg = str(event.get("message", "unknown error")).replace("\n", " ")
                    print(f"[playlist] Bridge error: {msg}")
                    yield f"event: error\ndata: {msg}\n\n"
                    return

                elif kind == "tool_end":
                    tool = event.get("name", "")
                    print(f"[playlist] Tool end: {tool}")
                    if tool == "build_playlist":
                        raw = event.get("result", "")
                        print(f"[playlist] build_playlist raw: {repr(raw)[:200]}")
                        try:
                            playlist_data = json.loads(raw) if isinstance(raw, str) else raw
                            if isinstance(playlist_data, str):
                                playlist_data = json.loads(playlist_data)
                        except Exception as e:
                            print(f"[playlist] Failed to parse build_playlist output: {e} — raw: {raw!r}")
                            yield f"event: error\ndata: Failed to parse playlist output\n\n"
                            return

                        print(f"[playlist] Built: {playlist_data.get('name')!r} with {len(playlist_data.get('tracks', []))} tracks")

                        thoughts_text = "".join(thoughts_parts)

                        # Auto-save to DB
                        try:
                            now = datetime.now(tz=timezone.utc)
                            pid = body.playlist_id
                            conn = db()
                            if pid:
                                conn.execute(
                                    "UPDATE playlists SET name=?, prompt=?, tracks=?, shortcuts_url=?, "
                                    "thoughts=?, queries=?, updated_at=? WHERE playlist_id=?",
                                    [playlist_data["name"], body.prompt,
                                     json.dumps(playlist_data["tracks"]),
                                     playlist_data["shortcuts_url"],
                                     thoughts_text, json.dumps(queries), now, pid]
                                )
                            else:
                                pid = str(uuid4())
                                conn.execute(
                                    "INSERT INTO playlists "
                                    "(playlist_id, name, prompt, tracks, shortcuts_url, thoughts, queries, created_at, updated_at) "
                                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                    [pid, playlist_data["name"], body.prompt,
                                     json.dumps(playlist_data["tracks"]),
                                     playlist_data["shortcuts_url"],
                                     thoughts_text, json.dumps(queries), now, now]
                                )
                            conn.close()
                            playlist_data["playlist_id"] = pid
                            playlist_data["thoughts"] = thoughts_text
                            playlist_data["queries"] = queries
                            print(f"[playlist] Saved to DB: {pid} ({len(queries)} queries captured)")
                        except Exception as e:
                            print(f"[playlist] DB save failed: {e}")

                        yield f"event: playlist\ndata: {json.dumps(playlist_data)}\n\n"
                    else:
                        yield f"event: tool_end\ndata: done\n\n"

        except Exception as e:
            import traceback
            print(f"[playlist] Stream error: {e}\n{traceback.format_exc()}")
            yield f"event: error\ndata: {str(e)}\n\n"
            return

        if not any("playlist" in t for t in thoughts_parts):
            print("[playlist] WARNING: stream ended with no build_playlist call")

        yield "event: done\ndata: done\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


class AnalyticsChatRequest(BaseModel):
    prompt: str
    context_snapshot: dict | None = None


# Enforced by the CLI's --json-schema, which is why the old "strip markdown code
# fences before json.loads" workaround is gone.
_ACTION_BUS_SCHEMA = {
    "type": "object",
    "properties": {
        "response": {"type": "string"},
        "ui_actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string"},
                    "payload": {"type": "object"},
                },
                "required": ["type", "payload"],
            },
        },
    },
    "required": ["response", "ui_actions"],
}


_ANALYTICS_SYSTEM = """You are a music analytics assistant for a personal Last.fm scrobble database with ~121k plays since 2019.

You help the user explore their listening data via two mechanisms:
1. A natural language response explaining what you found or suggesting insights.
2. A JSON array of UI actions that update the analytics frontend.

Always respond with valid JSON in this exact shape:
{
  "response": "Your natural language answer here",
  "ui_actions": [
    {"type": "action_name", "payload": {...}}
  ]
}

Available action types and their payloads:
- navigate: { "path": "/dashboard" | "/discover" | "/timemachine" | "/explore/artist/<name>" }
- set_time_range: { "period": "7d" | "30d" | "90d" | "1y" | "2y" | "all" }
- set_entity_type: { "entity_type": "artist" | "album" | "track" }
- apply_filter: { "search": "text", "sort_by": "column", "sort_dir": "asc" | "desc" }
- clear_filters: {}
- drill_down: { "entity_type": "artist" | "album" | "track", "entity_id": "name" }
- open_panel: { "panel": "Stats" | "Albums" | "Similar" }
- set_era_preset: { "preset": "2019" | "2020" | "2021" | "2022" | "2023" | "2024" | "2025", "from": "YYYY-MM-DD", "to": "YYYY-MM-DD" }
- show_toast: { "message": "text" }
- global_search: { "query": "text" }

Current context is provided in the user message. Use it to understand what page the user is on and what they're looking at.
Only include ui_actions that are meaningful given the user's request. An empty array is fine if no UI change is needed.
"""


# Dev stubs — set ANALYTICS_CHAT_STUBS=1 to bypass Claude API during local development.
# Each entry: (keywords_that_must_all_appear_in_prompt, response_dict).
# Remove individual entries as you verify the real Claude response for that scenario.
_CHAT_STUBS: list[tuple[list[str], dict]] = [
    # 1. Summer 2021 / time machine navigation
    (["summer", "2021"], {
        "response": "[STUB] Navigating to Time Machine and setting era to Summer 2021 (June–August).",
        "ui_actions": [
            {"type": "navigate", "payload": {"path": "/timemachine"}},
            {"type": "set_era_preset", "payload": {"preset": "2021", "from": "2021-06-01", "to": "2021-08-31"}},
            {"type": "highlight_chart", "payload": {"chart_id": "timemachine_chart"}},
            {"type": "show_toast", "payload": {"message": "Time Machine → Summer 2021"}},
        ],
    }),
    # 2. Artist listening history over the years (e.g. "how has my Radiohead listening changed")
    (["radiohead", "year"], {
        "response": "[STUB] Opening Radiohead deep dive with all-time yearly granularity.",
        "ui_actions": [
            {"type": "navigate", "payload": {"path": "/explore/artist/Radiohead"}},
            {"type": "set_time_range", "payload": {"period": "all"}},
            {"type": "highlight_chart", "payload": {"chart_id": "deepdive_chart"}},
            {"type": "show_toast", "payload": {"message": "Deep Dive → Radiohead (all time)"}},
        ],
    }),
    # 3. Compare two artists (flagship multi-action sequence)
    (["compare", "lana", "mitski"], {
        "response": "[STUB] Opening Lana Del Rey deep dive, switching to all-time, and comparing with Mitski.",
        "ui_actions": [
            {"type": "navigate", "payload": {"path": "/explore/artist/Lana Del Rey"}},
            {"type": "set_time_range", "payload": {"period": "all"}},
            {"type": "open_panel", "payload": {"panel": "Compare"}},
            {"type": "add_compare_entity", "payload": {"type": "artist", "id": "Mitski"}},
            {"type": "highlight_chart", "payload": {"chart_id": "deepdive_chart"}},
            {"type": "show_toast", "payload": {"message": "Compare: Lana Del Rey vs Mitski"}},
        ],
    }),
    # 4. Artists I used to listen to but haven't recently (flagship Discover filter demo)
    (["haven't", "month"], {
        "response": "[STUB] Filtering Discover to artists with 50+ total plays but not heard in 6+ months.",
        "ui_actions": [
            {"type": "navigate", "payload": {"path": "/discover"}},
            {"type": "set_entity_type", "payload": {"entity_type": "artist"}},
            {"type": "apply_filter", "payload": {"sort_by": "total_plays", "sort_dir": "desc"}},
            {"type": "highlight_chart", "payload": {"chart_id": "discover_table"}},
            {"type": "show_toast", "payload": {"message": "Discover → dormant artists filter"}},
        ],
    }),
    # 5. Scatter plot — plays vs recency
    (["scatter"], {
        "response": "[STUB] Scatter plot of top 50 artists by total plays vs days since last heard.",
        "ui_actions": [
            {"type": "navigate", "payload": {"path": "/discover"}},
            {"type": "set_entity_type", "payload": {"entity_type": "artist"}},
            {"type": "set_top_n", "payload": {"n": 50}},
            {"type": "set_viz_type", "payload": {"viz_type": "scatter"}},
            {"type": "set_viz_axes", "payload": {"x_metric": "total_plays", "y_metric": "days_since_last_heard"}},
            {"type": "highlight_chart", "payload": {"chart_id": "discover_viz"}},
            {"type": "show_toast", "payload": {"message": "Discover → scatter: plays vs recency"}},
        ],
    }),
    # 6. Top artists this year
    (["top artist"], {
        "response": "[STUB] Showing top artists over the last year on the Dashboard.",
        "ui_actions": [
            {"type": "navigate", "payload": {"path": "/dashboard"}},
            {"type": "set_time_range", "payload": {"period": "1y"}},
            {"type": "highlight_chart", "payload": {"chart_id": "top_entities"}},
            {"type": "show_toast", "payload": {"message": "Dashboard → top artists (1y)"}},
        ],
    }),
    # 7. Genre breakdown / what genres do I listen to
    (["genre"], {
        "response": "[STUB] Opening the Dashboard to show your genre breakdown chart.",
        "ui_actions": [
            {"type": "navigate", "payload": {"path": "/dashboard"}},
            {"type": "highlight_chart", "payload": {"chart_id": "genre_mood"}},
            {"type": "show_toast", "payload": {"message": "Dashboard → genre breakdown"}},
        ],
    }),
    # 8. Deep dive into a specific artist
    (["deep dive", "nirvana"], {
        "response": "[STUB] Opening Nirvana deep dive.",
        "ui_actions": [
            {"type": "navigate", "payload": {"path": "/explore/artist/Nirvana"}},
            {"type": "highlight_chart", "payload": {"chart_id": "deepdive_chart"}},
            {"type": "show_toast", "payload": {"message": "Deep Dive → Nirvana"}},
        ],
    }),
    # 9. Compare this year vs last year in Time Machine
    (["compare", "year"], {
        "response": "[STUB] Navigating to Time Machine and enabling compare mode for this year vs last year.",
        "ui_actions": [
            {"type": "navigate", "payload": {"path": "/timemachine"}},
            {"type": "set_era_preset", "payload": {"preset": "2024", "from": "2024-01-01", "to": "2024-12-31"}},
            {"type": "highlight_chart", "payload": {"chart_id": "timemachine_chart"}},
            {"type": "show_toast", "payload": {"message": "Time Machine → 2024 vs 2025 compare"}},
        ],
    }),
    # 10. Show me the discover page / browse
    (["discover"], {
        "response": "[STUB] Opening the Discover page with all artists sorted by total plays.",
        "ui_actions": [
            {"type": "navigate", "payload": {"path": "/discover"}},
            {"type": "set_entity_type", "payload": {"entity_type": "artist"}},
            {"type": "apply_filter", "payload": {"sort_by": "total_plays", "sort_dir": "desc"}},
            {"type": "highlight_chart", "payload": {"chart_id": "discover_table"}},
            {"type": "show_toast", "payload": {"message": "Discover → all artists"}},
        ],
    }),
]


def _match_stub(prompt: str) -> dict | None:
    p = prompt.lower()
    for keywords, response in _CHAT_STUBS:
        if all(k in p for k in keywords):
            return response
    return None


@app.post("/analytics/chat")
async def analytics_chat(body: AnalyticsChatRequest):
    """Analytics action bus endpoint. Returns { response, ui_actions } JSON."""
    if os.environ.get("ANALYTICS_CHAT_STUBS", "").strip() in ("1", "true"):
        stub = _match_stub(body.prompt)
        if stub:
            return stub
        return {
            "response": f"[STUB] No stub matched: '{body.prompt}'. Add a stub or disable ANALYTICS_CHAT_STUBS.",
            "ui_actions": [],
        }

    from backend.agent.bridge_client import BridgeError, call_json

    user_msg = body.prompt
    if body.context_snapshot:
        user_msg = f"Current context: {json.dumps(body.context_snapshot)}\n\nUser request: {body.prompt}"

    raw = ""
    try:
        raw = await call_json(user_msg, _ANALYTICS_SYSTEM, json_schema=_ACTION_BUS_SCHEMA)
        result = json.loads(raw)
        # Validate structure
        result.setdefault("response", "")
        result.setdefault("ui_actions", [])
        return result
    except json.JSONDecodeError as e:
        return {"response": raw, "ui_actions": [], "parse_error": str(e)}
    except BridgeError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/agent/bridge-health")
async def bridge_health():
    """
    Diagnostic for the host-side Claude bridge. Every Claude-powered feature
    depends on it, so a failure here explains a dead chat far faster than
    reading container logs.
    """
    from backend.agent.bridge_client import BRIDGE_URL, health

    try:
        return {"reachable": True, "url": BRIDGE_URL, **(await health())}
    except Exception as e:
        return {
            "reachable": False,
            "url": BRIDGE_URL,
            "error": str(e),
            "hint": "On the Pi: systemctl --user status claude-bridge",
        }


@app.post("/agent/chat")
async def agent_chat(body: ChatRequest):
    from backend.agent.bridge_client import stream_agent

    async def stream():
        # Keep-alive: if a tool call takes >25s with no event, emit an SSE
        # comment so mobile browsers don't drop the connection. SSE comments
        # (": ...") are ignored by the client.
        gen = stream_agent(body.message, thread_id=body.thread_id, mode="chat").__aiter__()
        try:
            while True:
                try:
                    event = await asyncio.wait_for(gen.__anext__(), timeout=25.0)
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue

                kind = event.get("type")
                if kind == "text":
                    text = event.get("text") or ""
                    if text:
                        encoded = "\n".join(f"data: {line}" for line in text.split("\n"))
                        yield f"{encoded}\n\n"
                elif kind == "tool_start":
                    yield f"event: tool_start\ndata: {event.get('name', 'tool')}\n\n"
                elif kind == "tool_end":
                    yield "event: tool_end\ndata: done\n\n"
                elif kind == "error":
                    msg = str(event.get("message", "unknown error")).replace("\n", " ")
                    print(f"[chat] Bridge error: {msg}")
                    yield f"event: error\ndata: {msg}\n\n"
        except Exception as e:
            print(f"[chat] Stream error: {e}")
            yield f"event: error\ndata: {str(e)}\n\n"
        yield "event: done\ndata: done\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")
