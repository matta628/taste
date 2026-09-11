"""
One-off export of real analytics data into a single JSON lookup fixture for
the static GitHub Pages demo build (see PLAN.md §2).

Reuses the real query functions in backend/analytics.py directly (no SQL
reimplementation) so fixtures match real endpoint shapes exactly.

The frontend's date pickers (Dashboard/DeepDive period buttons, Time
Machine era presets) always compute from_date/to_date either relative to
"now" (e.g. "last 90 days") or as exact calendar-year eras (e.g.
2021-01-01..2022-01-01) — never arbitrary literal dates. So instead of
baking one fixture per literal date pair (which would go stale the day
after export), each fixture is keyed by which *bucket* the date range
falls into ("rel:90d", "era:2021", ...). frontend/src/demo/mockFetch.js
buckets each live request's from_date/to_date the same way and looks the
result up — see canonicalKey()/bucketize() there, which this script's
ckey()/REL_DAYS/ERA_YEARS must stay in sync with.

Run from repo root:
    PYTHONPATH=. .venv/bin/python -m scripts.export_demo_fixtures
"""
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.analytics import (  # noqa: E402
    activity, genre_breakdown, mood_breakdown, heatmap, day_of_week,
    new_artists, listening_streak, top_entities, entities_artists,
    entities_albums, entities_tracks, artist_history, artist_stats_detail,
    artist_albums, artist_similar, artist_timeline, artist_sessions,
    album_history, album_stats_detail, album_tracks,
    track_history, track_stats_detail,
    genre_tag_tracks, mood_tag_tracks, available_moods, top_visuals, lyric_lines,
)
from backend.books import list_books  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / "frontend/src/demo/fixtures/analytics.json"
BOOKS_PATH = ROOT / "frontend/src/demo/fixtures/books.json"
ENTITY_EXPORT_LIMIT = 300
NUM_DEEP_DIVE_ARTISTS = 10
# Beyond everything reachable from the Dashboard and Time Machine lists, give
# the top of each Explore table a full Deep Dive too.
EXPLORE_DEEP_DIVE = {"artist": 50, "album": 30, "track": 30}
# DeepDive.jsx PERIODS / AUTO_GRAN / METRIC_OPTIONS, and the Dashboard's period
# buttons (which drive the artwork strip).
DEEP_DIVE_PERIODS = ["7d", "30d", "90d", "1y", "2y", "3y", "4y", "5y", "all"]
DEEP_DIVE_GRAN = {"7d": "day", "30d": "day", "90d": "week", "1y": "week", "2y": "month",
                  "3y": "month", "4y": "month", "5y": "month", "all": "month"}
METRICS = ["plays", "unique_tracks"]
DASHBOARD_PERIODS = ["7d", "30d", "90d", "1y", "2y", "3y", "4y", "5y", "all"]
ARTWORK_LIMIT = 20   # ArtworkStrip.jsx

REL_DAYS = {
    "7d": 7, "30d": 30, "90d": 90, "180d": 180, "1y": 365,
    "2y": 730, "3y": 1095, "4y": 1460, "5y": 1825,
}
RELATIVE_BUCKETS = list(REL_DAYS) + ["all"]
GRAN_FOR_BUCKET = {
    "7d": "day", "30d": "day", "90d": "week", "180d": "week", "1y": "week",
    "2y": "month", "3y": "month", "4y": "month", "5y": "month", "all": "month",
}
ERA_YEARS = list(range(2019, 2026))
TODAY = date.today()

fixtures = {}


def rel_range(bucket):
    if bucket == "all":
        return None, None
    d = REL_DAYS[bucket]
    return (TODAY - timedelta(days=d)).isoformat(), TODAY.isoformat()


def era_range(year):
    return f"{year}-01-01", f"{year + 1}-01-01"


def ckey(path, params=None):
    """Canonical cache key — must exactly match mockFetch.js's canonicalKey()."""
    params = params or {}
    parts = [f"{k}={v}" for k, v in sorted(params.items()) if v is not None]
    return path + ("?" + "&".join(parts) if parts else "")


def put(fn, path, call_params=None, bucket=None, year=None, **fn_extra_kwargs):
    """Call fn with the resolved date range, store result under the bucketed key."""
    call_params = call_params or {}
    from_date, to_date = era_range(year) if year is not None else rel_range(bucket)

    kwargs = dict(call_params)
    kwargs.update(fn_extra_kwargs)
    if from_date is not None:
        kwargs["from_date"] = from_date
    if to_date is not None:
        kwargs["to_date"] = to_date

    result = fn(**kwargs)

    key_params = dict(call_params)
    if year is not None:
        key_params["_range"] = f"era:{year}"
    elif bucket is not None and bucket != "all":
        key_params["_range"] = f"rel:{bucket}"
    fixtures[ckey(path, key_params)] = result


def _remote_image_urls():
    """local_path -> the CDN URL it was downloaded from."""
    from backend.db.connect import connect
    conn = connect()
    try:
        return dict(conn.execute(
            "SELECT local_path, remote_url FROM entity_images WHERE local_path IS NOT NULL").fetchall())
    finally:
        conn.close()


def main():
    print(f"[export] Exporting demo fixtures as of {TODAY.isoformat()}...")

    # -- Dashboard: relative period buckets ---------------------------------
    for b in RELATIVE_BUCKETS:
        put(activity, "/analytics/activity", {"granularity": GRAN_FOR_BUCKET[b]}, bucket=b)
        put(genre_breakdown, "/analytics/genre-breakdown", {"limit": 12}, bucket=b)
        put(mood_breakdown, "/analytics/mood-breakdown", {}, bucket=b)
        put(heatmap, "/analytics/heatmap", {}, bucket=b)
        put(day_of_week, "/analytics/day-of-week", {}, bucket=b)
        put(new_artists, "/analytics/new-artists", {"granularity": "month"}, bucket=b)
        put(listening_streak, "/analytics/listening-streak", {}, bucket=b)
        if b != "all":
            for et in ("artist", "album", "track"):
                put(top_entities, "/analytics/top-entities",
                    {"entity_type": et, "limit": 15}, bucket=b)
    # DriftAnalysis's "current" (last-30-days) side uses limit=10, not 12
    put(genre_breakdown, "/analytics/genre-breakdown", {"limit": 10}, bucket="30d")
    print(f"[export] Dashboard relative-period fixtures: {len(fixtures)}")

    # -- Time Machine: calendar-year era buckets -----------------------------
    n_before_era = len(fixtures)
    for y in ERA_YEARS:
        put(activity, "/analytics/activity", {"granularity": "month"}, year=y)
        put(genre_breakdown, "/analytics/genre-breakdown", {"limit": 12}, year=y)
        put(day_of_week, "/analytics/day-of-week", {}, year=y)
        put(heatmap, "/analytics/heatmap", {}, year=y)
        put(listening_streak, "/analytics/listening-streak", {}, year=y)
        for et in ("artist", "album", "track"):
            put(top_entities, "/analytics/top-entities",
                {"entity_type": et, "limit": 15}, year=y)
        # EraStory panel
        put(top_entities, "/analytics/top-entities",
            {"entity_type": "artist", "limit": 5}, year=y)
        put(genre_breakdown, "/analytics/genre-breakdown", {"limit": 5}, year=y)
        # DriftAnalysis era side
        put(genre_breakdown, "/analytics/genre-breakdown", {"limit": 10}, year=y)
    print(f"[export] Time Machine era fixtures: {len(fixtures) - n_before_era}")

    # -- Explore / Discover entity tables (all-time, full arrays) ------------
    fixtures[ckey("/analytics/entities/artists")] = entities_artists(
        sort_by="rank_all_time", sort_dir="asc", limit=ENTITY_EXPORT_LIMIT)
    fixtures[ckey("/analytics/entities/albums")] = entities_albums(
        sort_by="rank_all_time", sort_dir="asc", limit=ENTITY_EXPORT_LIMIT)
    fixtures[ckey("/analytics/entities/tracks")] = entities_tracks(
        sort_by="rank_all_time", sort_dir="asc", limit=ENTITY_EXPORT_LIMIT)
    fixtures[ckey("/analytics/moods")] = available_moods()
    print("[export] Entity tables + moods list exported")

    # -- Artwork strip ---------------------------------------------------------
    # The demo points each tile at the artwork's source URL on Deezer's CDN
    # (image_url) instead of shipping copies: album art isn't ours to
    # republish, and the repo stays small. A tile whose image fails to load
    # falls back to initials, same as one with no artwork at all.
    remote = _remote_image_urls()
    reachable = {"artist": set(), "album": set(), "track": set()}
    for p in DASHBOARD_PERIODS:
        for ent in ("artist", "album"):
            rows = top_visuals(period=p, limit=ARTWORK_LIMIT, entity=ent)
            fixtures[ckey("/analytics/top-visuals",
                          {"entity": ent, "limit": ARTWORK_LIMIT, "period": p})] = rows
            for r in rows:
                reachable[ent].add(r["name"])
                r["image_url"] = remote.get(r["image_path"])
    print("[export] Artwork strip fixtures")

    # -- Dashboard lyrics carousel and rails ---------------------------------
    # Both call with a fixed limit; the demo serves one shuffle of the same pool.
    for n in (40, 60):
        lines = lyric_lines(limit=n)
        for l in lines:
            l["image_url"] = remote.get(l["image_path"])
        fixtures[ckey("/analytics/lyric-lines", {"limit": n})] = lines
    print("[export] Lyric lines exported")

    # -- Deep Dive: every entity a visitor can click through to --------------
    for key, value in fixtures.items():
        if key.startswith("/analytics/top-entities?"):
            ent = key.split("entity_type=")[1].split("&")[0]
            reachable[ent].update(r["name"] for r in value)
    for ent, n in EXPLORE_DEEP_DIVE.items():
        rows = fixtures[ckey(f"/analytics/entities/{ent}s")]["rows"][:n]
        reachable[ent].update(r[ent] if ent in r else r.get("name") for r in rows)
    reachable["artist"].update(
        r["artist"] for r in fixtures[ckey("/analytics/entities/artists")]["rows"][:NUM_DEEP_DIVE_ARTISTS])
    for ent in reachable:
        reachable[ent].discard(None)
    print("[export] Deep Dive entities: " +
          ", ".join(f"{len(v)} {k}s" for k, v in reachable.items()))

    history = {"artist": artist_history, "album": album_history, "track": track_history}
    for ent, names in reachable.items():
        for name in sorted(names):
            for period in DEEP_DIVE_PERIODS:
                for metric in METRICS:
                    put(history[ent], f"/analytics/{ent}/{name}/history",
                        {"granularity": DEEP_DIVE_GRAN[period], "metric": metric},
                        bucket=period, name=name)
                if ent == "artist":
                    put(artist_albums, f"/analytics/artist/{name}/albums", {}, bucket=period, name=name)
                elif ent == "album":
                    put(album_tracks, f"/analytics/album/{name}/tracks", {}, bucket=period, name=name)
            if ent == "artist":
                fixtures[ckey(f"/analytics/artist/{name}/stats")] = artist_stats_detail(name)
                fixtures[ckey(f"/analytics/artist/{name}/similar")] = artist_similar(name)
                fixtures[ckey(f"/analytics/artist/{name}/timeline")] = artist_timeline(name)
                fixtures[ckey(f"/analytics/artist/{name}/sessions")] = artist_sessions(name)
            else:
                stats = album_stats_detail if ent == "album" else track_stats_detail
                try:
                    fixtures[ckey(f"/analytics/{ent}/{name}/stats")] = stats(name)
                except Exception as e:   # a name the stats tables don't know: leave it to the entity-row fallback
                    print(f"[export]   no {ent} stats for {name!r}: {e}")
    print(f"[export] Deep Dive fixtures done, total keys: {len(fixtures)}")

    # -- Genre / mood tag drill-down tables (top tags only) ------------------
    top_genre_tags = [r["tag"] for r in genre_breakdown(limit=8)]
    top_mood_tags = [r["mood"] for r in mood_breakdown(limit=8)]
    for tag in top_genre_tags:
        fixtures[ckey(f"/analytics/genre/{tag}/tracks", {"limit": 50})] = \
            genre_tag_tracks(tag, limit=50)
    for tag in top_mood_tags:
        fixtures[ckey(f"/analytics/mood/{tag}/tracks", {"limit": 50})] = \
            mood_tag_tracks(tag, limit=50)
    print(f"[export] Tag drill-down fixtures for {len(top_genre_tags)} genres, "
          f"{len(top_mood_tags)} moods")

    # -- Reading log ---------------------------------------------------------
    BOOKS_PATH.write_text(json.dumps(list_books(), indent=None, separators=(",", ":"), default=str))
    print(f"[export] Books: {BOOKS_PATH.stat().st_size / 1024:.0f} KB")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(fixtures, indent=None, separators=(",", ":"), default=str))
    size_kb = OUT_PATH.stat().st_size / 1024
    print(f"[export] Wrote {len(fixtures)} fixture entries ({size_kb:.0f} KB) to {OUT_PATH}")


if __name__ == "__main__":
    main()
