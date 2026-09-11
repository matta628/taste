"""
Artist + album artwork pipeline — runs on Pi (HTTP only, no heavy deps).

Source is Deezer's public API: no key, no auth, covers both artists and albums
in one place, and returns ~25-35KB JPEGs at 250px. Last.fm was the obvious
first choice but its artist images have been dead for years — `artist.getInfo`
now returns the same placeholder hash (2a96cbd8b46e...) for every artist.
Cover Art Archive works for albums but needs a MusicBrainz release-group lookup
first, and MusicBrainz rate-limits to 1 req/s.

Images are written to data/images/{artists,albums}/ as content-addressed files
and indexed in the `entity_images` table. Fully incremental: an entity already
in `entity_images` or recorded in `enrichment_skipped` is never re-fetched, so
this is safe to run from cron forever.

Sizing (measured): ~30KB/image at 250px. The full back catalogue is 1,667
artists + 2,759 albums ≈ 4,426 images ≈ 135MB — small enough that the
"rolling window" idea isn't needed for disk reasons. Use --window-days anyway
if you'd rather keep the first run short.

Usage:
    python -m backend.pipelines.fetch_visuals [--kind artists|albums|both]
                                              [--window-days N] [--limit N]
"""
import argparse
import hashlib
import logging
import math
import os
import re
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import requests

from backend.db.schema import DB_PATH, get_connection

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DEEZER_BASE = "https://api.deezer.com"
# Deezer asks for restraint but publishes no hard limit; 0.25s ≈ 4 req/s has
# been stable and keeps a full backfill to roughly 20 minutes.
SLEEP_BETWEEN = 0.25
IMAGE_ROOT = Path(os.environ.get("IMAGE_ROOT", Path(DB_PATH).parent / "data" / "images"))
# 250px is what the dashboard strip and Deep Dive headers actually render.
# Deezer also exposes picture_big (500px) if that ever changes.
SIZE_FIELD = {"artist": "picture_medium", "album": "cover_medium"}

_CREATE_ENTITY_IMAGES = """
    CREATE TABLE IF NOT EXISTS entity_images (
        entity_type VARCHAR NOT NULL,          -- 'artist' | 'album'
        entity_name VARCHAR NOT NULL,          -- artist name, or 'album||artist'
        display_name VARCHAR,
        artist      VARCHAR,                   -- NULL for entity_type='artist'
        source      VARCHAR,
        remote_url  VARCHAR,
        local_path  VARCHAR,                   -- relative to IMAGE_ROOT
        width       INTEGER,
        bytes       INTEGER,
        fetched_at  TIMESTAMPTZ DEFAULT now(),
        PRIMARY KEY (entity_type, entity_name)
    )
"""


def _session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = "tastemaker/1.0 (personal music analytics)"
    return s


def _norm(text: str) -> str:
    """Loose comparison key: casefold, strip accents, drop non-alphanumerics."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "", text.lower())


# Edition/format noise that Last.fm keeps but Deezer's album titles don't:
# "(Deluxe)", "(Remastered)", and dash-suffixed forms like " - Single", " - EP".
_EDITION_PAREN = re.compile(r"[\(\[][^)\]]*[)\]]\s*$")
_EDITION_DASH = re.compile(
    r"\s+-\s+(single|ep|deluxe|remaster(ed)?|bonus track version|"
    r"expanded( edition)?|special edition|anniversary edition)\s*$", re.I
)


def _clean_title(title: str) -> str:
    """Human-readable title with edition suffixes removed (not normalised)."""
    out = title or ""
    for _ in range(3):  # e.g. "X (Deluxe) (Remastered)"
        before = out
        out = _EDITION_PAREN.sub("", out).strip()
        out = _EDITION_DASH.sub("", out).strip()
        if out == before:
            break
    return out or (title or "")


def _strip_edition(title: str) -> str:
    """'Ultraviolence (Deluxe)' -> 'ultraviolence' so editions still match."""
    return _norm(_clean_title(title))


def _search(session: requests.Session, kind: str, query: str, limit: int = 8) -> list[dict]:
    """Deezer's plain search, returning several candidates for scoring.

    Deliberately NOT using Deezer's structured `artist:"x" album:"y"` syntax —
    tested against this library it returns markedly worse results (it matched
    meditation-playlist artists for `artist:"Nirvana"` and remix bootlegs for
    Lana Del Rey albums). Plain text search plus the scoring in _best_* below
    is far more accurate.
    """
    try:
        resp = session.get(f"{DEEZER_BASE}/search/{kind}", params={"q": query, "limit": limit}, timeout=15)
        if resp.status_code != 200:
            log.warning(f"[visuals] {resp.status_code} searching {kind} {query!r}")
            return []
        return resp.json().get("data") or []
    except Exception as e:
        log.warning(f"[visuals] search error for {kind} {query!r}: {e}")
        return []


def _best_artist(candidates: list[dict], artist: str) -> dict | None:
    """
    Pick the right artist among same-named acts.

    Deezer's first result is not reliably the one you mean: searching "Nirvana"
    returns the obscure 1960s UK band (199 fans) ahead of the Seattle one
    (10M fans). An exact name match plus fan count settles it.
    """
    target = _norm(artist)
    best, best_score = None, -1.0
    for c in candidates:
        name = c.get("name") or ""
        if not c.get(SIZE_FIELD["artist"]):
            continue
        exact = _norm(name) == target
        if not exact and target not in _norm(name) and _norm(name) not in target:
            continue
        # Popularity only breaks ties among plausible name matches; it never
        # promotes a wrong-name artist over a right-name one.
        score = (2.0 if exact else 0.0) + min(math.log10((c.get("nb_fan") or 0) + 1) / 10.0, 0.9)
        if score > best_score:
            best, best_score = c, score
    return best


def _best_album(candidates: list[dict], album: str, artist: str) -> dict | None:
    """Require the artist to match; prefer the closest title, ignoring editions."""
    t_artist, t_album = _norm(artist), _strip_edition(album)
    # Text before the first censoring asterisk, used as a fallback prefix match.
    censored_prefix = _norm(_clean_title(album).split("*", 1)[0]) if "*" in album else ""
    best, best_score = None, -1.0
    for c in candidates:
        if not c.get(SIZE_FIELD["album"]):
            continue
        c_artist = _norm((c.get("artist") or {}).get("name", ""))
        if not c_artist or (c_artist != t_artist and t_artist not in c_artist and c_artist not in t_artist):
            continue  # wrong artist — rules out remix/tribute/karaoke uploads
        c_album = _strip_edition(c.get("title") or "")
        if c_album == t_album:
            score = 3.0
        elif t_album and (t_album in c_album or c_album in t_album):
            score = 1.0
        elif censored_prefix and c_album.startswith(censored_prefix):
            # Last.fm stores some titles censored ("Norman F*****g Rockwell!"),
            # which can never match Deezer's uncensored text word-for-word.
            # The artist check above already ran, so a prefix match is safe.
            score = 2.0
        else:
            continue
        score += min((c.get("nb_tracks") or 0) / 100.0, 0.5)
        if score > best_score:
            best, best_score = c, score
    return best


def _download(session: requests.Session, url: str, dest_dir: Path, key: str) -> tuple[Path, int] | None:
    try:
        resp = session.get(url, timeout=20)
        if resp.status_code != 200 or not resp.content:
            return None
        # Content-addressed by entity key, not by title: titles contain slashes,
        # quotes, and non-ASCII that don't survive a filesystem round-trip.
        name = hashlib.sha1(key.encode("utf-8")).hexdigest()[:20] + ".jpg"
        dest_dir.mkdir(parents=True, exist_ok=True)
        path = dest_dir / name
        path.write_bytes(resp.content)
        return path, len(resp.content)
    except Exception as e:
        log.warning(f"[visuals] download error {url}: {e}")
        return None


def _pending_artists(conn, window_days: int | None, limit: int | None):
    where = "WHERE s.total_plays > 0"
    if window_days:
        where += f" AND s.last_heard >= now() - INTERVAL {int(window_days)} DAY"
    rows = conn.execute(f"""
        SELECT s.artist
        FROM artist_stats s
        LEFT JOIN entity_images i
               ON i.entity_type = 'artist' AND LOWER(i.entity_name) = LOWER(s.artist)
        LEFT JOIN enrichment_skipped k
               ON k.entity_type = 'image_artist' AND LOWER(k.entity_name) = LOWER(s.artist)
        {where} AND i.entity_name IS NULL AND k.entity_name IS NULL
        ORDER BY s.total_plays DESC
    """).fetchall()
    return [r[0] for r in (rows[:limit] if limit else rows)]


def _pending_albums(conn, window_days: int | None, limit: int | None):
    where = "WHERE s.total_plays > 0 AND s.album IS NOT NULL AND s.album <> ''"
    if window_days:
        where += f" AND s.last_heard >= now() - INTERVAL {int(window_days)} DAY"
    rows = conn.execute(f"""
        SELECT s.album, s.artist
        FROM album_stats s
        LEFT JOIN entity_images i
               ON i.entity_type = 'album' AND LOWER(i.entity_name) = LOWER(s.album || '||' || s.artist)
        LEFT JOIN enrichment_skipped k
               ON k.entity_type = 'image_album' AND LOWER(k.entity_name) = LOWER(s.album || '||' || s.artist)
        {where} AND i.entity_name IS NULL AND k.entity_name IS NULL
        ORDER BY s.total_plays DESC
    """).fetchall()
    return [(r[0], r[1]) for r in (rows[:limit] if limit else rows)]


# Rows are flushed in chunks: a full backfill is ~20 minutes of HTTP, and
# holding the DuckDB write lock for that long would block every API request.
FLUSH_EVERY = 25


def _flush(rows: list[tuple], skips: list[tuple]):
    if not rows and not skips:
        return
    conn = get_connection()
    try:
        conn.execute(_CREATE_ENTITY_IMAGES)
        if rows:
            conn.executemany("""
                INSERT INTO entity_images
                  (entity_type, entity_name, display_name, artist, source,
                   remote_url, local_path, width, bytes, fetched_at)
                VALUES (?, ?, ?, ?, 'deezer', ?, ?, 250, ?, ?)
                ON CONFLICT (entity_type, entity_name) DO UPDATE SET
                    remote_url = excluded.remote_url,
                    local_path = excluded.local_path,
                    bytes      = excluded.bytes,
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


def run(kind: str = "both", window_days: int | None = None, limit: int | None = None):
    IMAGE_ROOT.mkdir(parents=True, exist_ok=True)

    conn = get_connection()
    try:
        conn.execute(_CREATE_ENTITY_IMAGES)
        artists = _pending_artists(conn, window_days, limit) if kind in ("artists", "both") else []
        albums = _pending_albums(conn, window_days, limit) if kind in ("albums", "both") else []
    finally:
        conn.close()  # lock released before any HTTP work

    session = _session()
    now = lambda: datetime.now(tz=timezone.utc)
    rows: list[tuple] = []
    skips: list[tuple] = []
    fetched = skipped = 0

    if artists:
        log.info(f"[visuals] {len(artists)} artists need images")
        for i, artist in enumerate(artists, 1):
            hit = _best_artist(_search(session, "artist", artist), artist)
            url = (hit or {}).get(SIZE_FIELD["artist"])
            got = _download(session, url, IMAGE_ROOT / "artists", f"artist:{artist}") if url else None
            if got:
                path, size = got
                rows.append(("artist", artist, (hit.get("name") or artist), None,
                             url, str(path.relative_to(IMAGE_ROOT)), size, now()))
                fetched += 1
            else:
                skips.append(("image_artist", artist, "no deezer match" if not url else "download failed"))
                skipped += 1
            if i % FLUSH_EVERY == 0:
                _flush(rows, skips)
                log.info(f"[visuals] artists {i}/{len(artists)} ({fetched} fetched, {skipped} skipped)")
            time.sleep(SLEEP_BETWEEN)
        _flush(rows, skips)

    if albums:
        log.info(f"[visuals] {len(albums)} albums need covers")
        for i, (album, artist) in enumerate(albums, 1):
            key = f"{album}||{artist}"
            clean = _clean_title(album)
            hit = _best_album(_search(session, "album", f"{clean} {artist}"), album, artist)
            if not hit and clean != album:
                hit = _best_album(_search(session, "album", f"{album} {artist}"), album, artist)
            if not hit:
                # Censored titles ("Norman F*****g Rockwell!") never match a
                # word-for-word search; the artist filter in _best_album still
                # keeps a title-only search honest.
                hit = _best_album(_search(session, "album", clean), album, artist)
            url = (hit or {}).get(SIZE_FIELD["album"])
            got = _download(session, url, IMAGE_ROOT / "albums", f"album:{key}") if url else None
            if got:
                path, size = got
                rows.append(("album", key, (hit.get("title") or album), artist,
                             url, str(path.relative_to(IMAGE_ROOT)), size, now()))
                fetched += 1
            else:
                skips.append(("image_album", key, "no deezer match" if not url else "download failed"))
                skipped += 1
            if i % FLUSH_EVERY == 0:
                _flush(rows, skips)
                log.info(f"[visuals] albums {i}/{len(albums)} ({fetched} fetched, {skipped} skipped)")
            time.sleep(SLEEP_BETWEEN)
        _flush(rows, skips)

    conn = get_connection()
    total = conn.execute("SELECT COUNT(*) FROM entity_images").fetchone()[0]
    conn.close()
    log.info(f"[visuals] Done. +{fetched} fetched, {skipped} skipped. entity_images now {total} rows.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", choices=["artists", "albums", "both"], default="both")
    parser.add_argument("--window-days", type=int, default=None,
                        help="Only entities heard in the last N days (default: entire library)")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--retry-skipped", action="store_true",
                        help="Clear previously-recorded image misses so an improved "
                             "matcher can try them again")
    args = parser.parse_args()

    if args.retry_skipped:
        conn = get_connection()
        try:
            n = conn.execute(
                "SELECT COUNT(*) FROM enrichment_skipped WHERE entity_type LIKE 'image_%'"
            ).fetchone()[0]
            conn.execute("DELETE FROM enrichment_skipped WHERE entity_type LIKE 'image_%'")
            log.info(f"[visuals] cleared {n} prior misses for retry")
        finally:
            conn.close()

    run(kind=args.kind, window_days=args.window_days, limit=args.limit)
