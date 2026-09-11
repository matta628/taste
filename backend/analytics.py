"""
Analytics API router — /analytics/* and /user/* endpoints.
Included in main.py via app.include_router().
"""
import json
import os
from datetime import datetime, timezone
from uuid import uuid4

import duckdb
import random

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.db.schema import DB_PATH
from backend.db.connect import connect as db_connect

router = APIRouter()

_VALID_SORT: dict[str, set] = {
    "artist": {
        "artist", "total_plays",
        "plays_7d", "plays_30d", "plays_90d", "plays_180d", "plays_1y",
        "plays_7d_delta", "plays_30d_delta", "plays_90d_delta", "plays_180d_delta", "plays_1y_delta",
        "rank_all_time", "rank_7d", "rank_30d", "rank_90d", "rank_180d", "rank_1y",
        "rank_7d_delta", "rank_30d_delta", "rank_90d_delta",
        "first_heard", "last_heard", "days_since_last_heard",
        "unique_tracks", "unique_albums", "longest_streak_days", "current_streak_days",
        "peak_week_plays",
    },
    "album": {
        "album", "artist", "total_plays",
        "plays_7d", "plays_30d", "plays_90d", "plays_180d", "plays_1y",
        "plays_7d_delta", "plays_30d_delta", "plays_90d_delta", "plays_180d_delta", "plays_1y_delta",
        "rank_all_time", "rank_7d", "rank_30d",
        "rank_7d_delta", "rank_30d_delta",
        "first_heard", "last_heard", "days_since_last_heard",
        "unique_tracks", "longest_streak_days", "current_streak_days", "peak_week_plays",
    },
    "track": {
        "track", "artist", "total_plays",
        "plays_7d", "plays_30d", "plays_90d", "plays_180d", "plays_1y",
        "plays_7d_delta", "plays_30d_delta", "plays_90d_delta", "plays_180d_delta", "plays_1y_delta",
        "rank_all_time", "rank_7d", "rank_30d",
        "rank_7d_delta", "rank_30d_delta",
        "first_heard", "last_heard", "days_since_last_heard",
        "longest_streak_days", "current_streak_days", "peak_week_plays",
    },
}


def _db():
    # Retries through a pipeline's short write-lock window — see backend/db/connect.py
    return db_connect()


def _ensure_genre_override():
    conn = _db()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS artist_genre_override (
                artist_name VARCHAR PRIMARY KEY,
                genre       VARCHAR NOT NULL,
                updated_at  TIMESTAMPTZ DEFAULT now()
            )
        """)
    finally:
        conn.close()

_ensure_genre_override()


def _date_filter(from_date: str | None, to_date: str | None, col: str = "scrobbled_at") -> tuple[str, list]:
    conditions, params = [], []
    if from_date:
        conditions.append(f"{col} >= ?::TIMESTAMPTZ")
        params.append(from_date)
    if to_date:
        conditions.append(f"{col} < ?::TIMESTAMPTZ")
        params.append(to_date)
    clause = " AND ".join(conditions)
    return (f"WHERE {clause}" if clause else ""), params


def _parse_filters(query_params: dict, entity_type: str) -> tuple[list, list]:
    """Parse filter_field_*, filter_operator_*, filter_value_* params into WHERE conditions and params."""
    valid_fields = _VALID_SORT[entity_type]
    valid_operators = {"eq", "neq", "gt", "gte", "lt", "lte", "contains", "in_last_days"}
    conditions, params = [], []

    # Extract filter indices from params (e.g. filter_field_0, filter_value_0)
    filter_indices = set()
    for key in query_params.keys():
        if key.startswith("filter_field_"):
            idx = key.split("_")[-1]
            filter_indices.add(idx)

    for idx in sorted(filter_indices):
        field = query_params.get(f"filter_field_{idx}")
        operator = query_params.get(f"filter_operator_{idx}")
        value = query_params.get(f"filter_value_{idx}")

        if not field or not operator or not value:
            continue

        field = field.lower()
        operator = operator.lower()

        # Validate field and operator
        if field not in valid_fields or operator not in valid_operators:
            continue

        # Build condition based on operator
        if operator == "eq":
            conditions.append(f"{field} = ?")
            params.append(value)
        elif operator == "neq":
            conditions.append(f"{field} != ?")
            params.append(value)
        elif operator == "contains":
            conditions.append(f"LOWER({field}::TEXT) LIKE LOWER(?)")
            params.append(f"%{value}%")
        elif operator == "gt":
            conditions.append(f"{field} > ?::NUMERIC")
            params.append(value)
        elif operator == "gte":
            conditions.append(f"{field} >= ?::NUMERIC")
            params.append(value)
        elif operator == "lt":
            conditions.append(f"{field} < ?::NUMERIC")
            params.append(value)
        elif operator == "lte":
            conditions.append(f"{field} <= ?::NUMERIC")
            params.append(value)
        elif operator == "in_last_days":
            conditions.append(f"{field} <= ?::NUMERIC")
            params.append(value)

    return conditions, params


def _row_to_dict(row, cols):
    d = {}
    for k, v in zip(cols, row):
        if isinstance(v, datetime):
            d[k] = v.isoformat()
        else:
            d[k] = v
    return d


# ---------------------------------------------------------------------------
# Analytics endpoints
# ---------------------------------------------------------------------------

@router.get("/analytics/activity")
def activity(
    granularity: str = "day",
    from_date: str | None = None,
    to_date: str | None = None,
):
    trunc = {"day": "day", "week": "week", "month": "month"}.get(granularity, "day")
    where, params = _date_filter(from_date, to_date)
    conn = _db()
    rows = conn.execute(f"""
        SELECT DATE_TRUNC('{trunc}', scrobbled_at)::DATE AS date, COUNT(*) AS plays
        FROM raw_scrobbles
        {where}
        GROUP BY date
        ORDER BY date
    """, params).fetchall()
    conn.close()
    return [{"date": str(r[0]), "plays": r[1]} for r in rows]


@router.get("/analytics/top-albums")
def top_albums(
    limit: int = 20,
    from_date: str | None = None,
    to_date: str | None = None,
):
    where, params = _date_filter(from_date, to_date)
    conn = _db()
    rows = conn.execute(f"""
        SELECT COALESCE(album, '[Unknown]') AS album, artist, COUNT(*) AS plays
        FROM raw_scrobbles
        {where}
        GROUP BY album, artist
        ORDER BY plays DESC
        LIMIT ?
    """, params + [limit]).fetchall()
    conn.close()
    return [{"album": r[0], "artist": r[1], "plays": r[2]} for r in rows]


@router.get("/analytics/genre-breakdown")
def genre_breakdown(
    limit: int = 25,
    from_date: str | None = None,
    to_date: str | None = None,
):
    _, params = _date_filter(from_date, to_date)
    # Build a bare AND clause (no WHERE keyword) for use inside existing WHERE blocks
    date_and = ""
    if from_date and to_date:
        date_and = "AND scrobbled_at >= ?::TIMESTAMPTZ AND scrobbled_at < ?::TIMESTAMPTZ"
    elif from_date:
        date_and = "AND scrobbled_at >= ?::TIMESTAMPTZ"
    elif to_date:
        date_and = "AND scrobbled_at < ?::TIMESTAMPTZ"
    conn = _db()
    rows = conn.execute(f"""
        WITH tagged AS (
            -- Last.fm tags (preferred — have community weights)
            SELECT LOWER(s.artist) AS artist, atags.tag
            FROM raw_scrobbles s
            JOIN artist_tags atags ON LOWER(s.artist) = LOWER(atags.artist_name)
            WHERE 1=1 {date_and}
            UNION ALL
            -- MusicBrainz tags (fills in artists with no Last.fm coverage)
            SELECT LOWER(s.artist) AS artist, UNNEST(mb.tags) AS tag
            FROM raw_scrobbles s
            JOIN artist_mb mb ON LOWER(s.artist) = LOWER(mb.artist_name)
            WHERE LOWER(s.artist) NOT IN (SELECT LOWER(artist_name) FROM artist_tags)
            {date_and}
        )
        SELECT tag, COUNT(*) AS plays
        FROM tagged
        GROUP BY tag
        ORDER BY plays DESC
        LIMIT ?
    """, params + params + [limit]).fetchall()
    total = sum(r[1] for r in rows)
    conn.close()
    return [{"tag": r[0], "plays": r[1], "pct": round(r[1] / total * 100, 1) if total else 0} for r in rows]


@router.get("/analytics/genre/{tag}/tracks")
def genre_tag_tracks(
    tag: str,
    limit: int = 50,
    from_date: str | None = None,
    to_date: str | None = None,
):
    """Top tracks from artists tagged with this genre."""
    _, params = _date_filter(from_date, to_date)
    date_and = ""
    if from_date and to_date:
        date_and = "AND s.scrobbled_at >= ?::TIMESTAMPTZ AND s.scrobbled_at < ?::TIMESTAMPTZ"
    elif from_date:
        date_and = "AND s.scrobbled_at >= ?::TIMESTAMPTZ"
    elif to_date:
        date_and = "AND s.scrobbled_at < ?::TIMESTAMPTZ"
    conn = _db()
    rows = conn.execute(f"""
        SELECT s.track, s.artist, COUNT(*) AS plays,
            COALESCE(
                (SELECT ago.genre FROM artist_genre_override ago WHERE ago.artist_name = s.artist LIMIT 1),
                (SELECT att.tag FROM artist_tags att WHERE att.artist_name = s.artist ORDER BY att.weight DESC LIMIT 1)
            ) AS genre
        FROM raw_scrobbles s
        WHERE LOWER(s.artist) IN (
            SELECT LOWER(artist_name) FROM artist_tags WHERE LOWER(tag) = LOWER(?)
        ) {date_and}
        GROUP BY s.track, s.artist
        ORDER BY plays DESC
        LIMIT ?
    """, [tag] + params + [limit]).fetchall()
    conn.close()
    return [{"track": r[0], "artist": r[1], "plays": r[2], "genre": r[3]} for r in rows]


@router.get("/analytics/mood-breakdown")
def mood_breakdown(
    limit: int = 20,
    from_date: str | None = None,
    to_date: str | None = None,
):
    where_scrobbles, params = _date_filter(from_date, to_date)
    conn = _db()
    rows = conn.execute(f"""
        SELECT mood, COUNT(*) AS plays
        FROM raw_scrobbles s
        JOIN track_mood tm ON LOWER(s.track) = LOWER(tm.track)
                           AND LOWER(s.artist) = LOWER(tm.artist),
        UNNEST(tm.tags) AS t(mood)
        {where_scrobbles}
        GROUP BY mood
        ORDER BY plays DESC
        LIMIT ?
    """, params + [limit]).fetchall()
    total = sum(r[1] for r in rows)
    conn.close()
    return [{"mood": r[0], "plays": r[1], "pct": round(r[1] / total * 100, 1) if total else 0} for r in rows]


@router.get("/analytics/mood/{tag}/tracks")
def mood_tag_tracks(
    tag: str,
    limit: int = 50,
    from_date: str | None = None,
    to_date: str | None = None,
):
    """Top tracks tagged with this mood."""
    where_scrobbles, params = _date_filter(from_date, to_date)
    conn = _db()
    rows = conn.execute(f"""
        SELECT s.track, s.artist, COUNT(*) AS plays, ANY_VALUE(tm.tags) AS mood_tags
        FROM raw_scrobbles s
        JOIN track_mood tm ON LOWER(s.track) = LOWER(tm.track)
                           AND LOWER(s.artist) = LOWER(tm.artist)
        {where_scrobbles}
          AND list_contains(tm.tags, ?)
        GROUP BY s.track, s.artist
        ORDER BY plays DESC
        LIMIT ?
    """, params + [tag, limit]).fetchall()
    conn.close()
    return [{"track": r[0], "artist": r[1], "plays": r[2], "mood_tags": list(r[3]) if r[3] else []} for r in rows]


@router.get("/analytics/moods")
def available_moods():
    """All distinct mood tags across track_mood."""
    conn = _db()
    rows = conn.execute("""
        SELECT DISTINCT mood FROM track_mood, UNNEST(tags) AS t(mood) ORDER BY mood
    """).fetchall()
    conn.close()
    return [r[0] for r in rows if r[0]]


class TrackMoodBody(BaseModel):
    track: str
    artist: str
    tags: list[str]


@router.patch("/analytics/track/mood")
def set_track_mood(body: TrackMoodBody):
    """Replace mood tags for a track (marks overridden=true)."""
    conn = _db()
    try:
        conn.execute("""
            UPDATE track_mood SET tags = ?, overridden = true, analyzed_at = now()
            WHERE LOWER(track) = LOWER(?) AND LOWER(artist) = LOWER(?)
        """, [body.tags, body.track, body.artist])
    finally:
        conn.close()
    return {"ok": True}


@router.get("/analytics/heatmap")
def heatmap(
    from_date: str | None = None,
    to_date: str | None = None,
):
    where, params = _date_filter(from_date, to_date)
    conn = _db()
    rows = conn.execute(f"""
        SELECT
            EXTRACT('hour'  FROM scrobbled_at)::INTEGER AS hour,
            EXTRACT('dow'   FROM scrobbled_at)::INTEGER AS day_of_week,
            COUNT(*) AS plays
        FROM raw_scrobbles
        {where}
        GROUP BY hour, day_of_week
        ORDER BY day_of_week, hour
    """, params).fetchall()
    conn.close()
    return [{"hour": r[0], "day_of_week": r[1], "plays": r[2]} for r in rows]


@router.get("/analytics/day-of-week")
def day_of_week(
    from_date: str | None = None,
    to_date: str | None = None,
):
    where, params = _date_filter(from_date, to_date)
    conn = _db()
    rows = conn.execute(f"""
        SELECT EXTRACT('dow' FROM scrobbled_at)::INTEGER AS day_of_week, COUNT(*) AS plays
        FROM raw_scrobbles
        {where}
        GROUP BY day_of_week
        ORDER BY day_of_week
    """, params).fetchall()
    conn.close()
    return [{"day_of_week": r[0], "plays": r[1]} for r in rows]


@router.get("/analytics/new-artists")
def new_artists(
    granularity: str = "month",
    from_date: str | None = None,
    to_date: str | None = None,
):
    trunc = {"day": "day", "week": "week", "month": "month"}.get(granularity, "month")
    where, params = _date_filter(from_date, to_date, col="first_heard")
    conn = _db()
    rows = conn.execute(f"""
        WITH artist_first AS (
            SELECT artist, MIN(scrobbled_at) AS first_heard
            FROM raw_scrobbles
            GROUP BY artist
        )
        SELECT DATE_TRUNC('{trunc}', first_heard)::DATE AS period, COUNT(*) AS new_artists
        FROM artist_first
        {where}
        GROUP BY period
        ORDER BY period
    """, params).fetchall()
    conn.close()
    return [{"date": str(r[0]), "new_artists": r[1]} for r in rows]


@router.get("/analytics/listening-streak")
def listening_streak(
    days: int = 365,
    from_date: str | None = None,
    to_date: str | None = None,
):
    where, params = _date_filter(from_date, to_date)
    if not where:
        where = f"WHERE scrobbled_at >= now() - INTERVAL '{days} days'"
    conn = _db()
    rows = conn.execute(f"""
        SELECT CAST(scrobbled_at AS DATE) AS date, COUNT(*) AS plays
        FROM raw_scrobbles
        {where}
        GROUP BY date
        ORDER BY date
    """, params).fetchall()
    conn.close()
    return [{"date": str(r[0]), "plays": r[1]} for r in rows]


@router.get("/analytics/artist/{name}/history")
def artist_history(
    name: str,
    granularity: str = "week",
    from_date: str | None = None,
    to_date: str | None = None,
    metric: str = "plays",
):
    trunc = {"day": "day", "week": "week", "month": "month", "year": "year"}.get(granularity, "week")
    agg = "COUNT(DISTINCT track)" if metric == "unique_tracks" else "COUNT(*)"
    where, params = _date_filter(from_date, to_date)
    extra_and = " AND " + where[6:] if where else ""
    conn = _db()
    rows = conn.execute(f"""
        SELECT DATE_TRUNC('{trunc}', scrobbled_at)::DATE AS date, {agg} AS value
        FROM raw_scrobbles
        WHERE LOWER(artist) = LOWER(?){extra_and}
        GROUP BY date
        ORDER BY date
    """, [name] + params).fetchall()
    conn.close()
    return [{"date": str(r[0]), "value": r[1]} for r in rows]


@router.get("/analytics/artist/{name}/stats")
def artist_stats_detail(name: str):
    conn = _db()
    row = conn.execute(
        "SELECT * FROM artist_stats WHERE LOWER(artist) = LOWER(?)", [name]
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Artist not found in stats")
    cols = [d[0] for d in conn.description]
    conn.close()
    return _row_to_dict(row, cols)


@router.get("/analytics/artist/{name}/albums")
def artist_albums(
    name: str,
    from_date: str | None = None,
    to_date: str | None = None,
):
    where, params = _date_filter(from_date, to_date)
    extra_and = " AND " + where[6:] if where else ""
    conn = _db()
    rows = conn.execute(f"""
        SELECT COALESCE(album, '[Unknown]') AS album, COUNT(*) AS plays, COUNT(DISTINCT track) AS tracks
        FROM raw_scrobbles
        WHERE LOWER(artist) = LOWER(?){extra_and}
        GROUP BY album
        ORDER BY plays DESC
    """, [name] + params).fetchall()
    conn.close()
    return [{"album": r[0], "plays": r[1], "tracks": r[2]} for r in rows]


@router.get("/analytics/artist/{name}/similar")
def artist_similar(name: str):
    conn = _db()
    rows = conn.execute("""
        SELECT similar_artist, similarity
        FROM artist_similar
        WHERE LOWER(artist_name) = LOWER(?)
        ORDER BY similarity DESC
        LIMIT 10
    """, [name]).fetchall()
    conn.close()
    return [{"artist": r[0], "similarity": r[1]} for r in rows]


@router.get("/analytics/artist/{name}/timeline")
def artist_timeline(name: str):
    """Year-by-year listening breakdown for the Timeline panel."""
    conn = _db()
    rows = conn.execute("""
        SELECT
            date_part('year', scrobbled_at)::INTEGER AS year,
            COUNT(*)                                  AS plays,
            COUNT(DISTINCT track)                     AS unique_tracks,
            COUNT(DISTINCT COALESCE(album, ''))       AS unique_albums,
            MIN_BY(track, scrobbled_at)               AS first_track,
            MIN(scrobbled_at)::DATE                   AS first_date
        FROM raw_scrobbles
        WHERE LOWER(artist) = LOWER(?)
        GROUP BY year
        ORDER BY year
    """, [name]).fetchall()
    conn.close()
    return [
        {
            "year": r[0], "plays": r[1], "unique_tracks": r[2],
            "unique_albums": r[3], "first_track": r[4], "first_date": str(r[5]),
        }
        for r in rows
    ]


@router.get("/analytics/artist/{name}/sessions")
def artist_sessions(name: str, limit: int = 50):
    """Recent listening sessions for the Sessions panel (30-min gap = new session)."""
    conn = _db()
    rows = conn.execute("""
        WITH scrobbles AS (
            SELECT track, scrobbled_at,
                LAG(scrobbled_at) OVER (ORDER BY scrobbled_at) AS prev_at
            FROM raw_scrobbles
            WHERE LOWER(artist) = LOWER(?)
        ),
        with_gap AS (
            SELECT *,
                CASE WHEN prev_at IS NULL
                          OR datediff('minute', prev_at, scrobbled_at) > 30
                     THEN 1 ELSE 0 END AS is_start
            FROM scrobbles
        ),
        with_session AS (
            SELECT *,
                SUM(is_start) OVER (ORDER BY scrobbled_at ROWS UNBOUNDED PRECEDING) AS session_num
            FROM with_gap
        )
        SELECT
            MIN(scrobbled_at)::DATE                    AS session_date,
            MIN(scrobbled_at)                          AS started_at,
            COUNT(*)                                   AS track_count,
            datediff('minute', MIN(scrobbled_at), MAX(scrobbled_at)) AS duration_minutes,
            LIST(track ORDER BY scrobbled_at)          AS tracks
        FROM with_session
        GROUP BY session_num
        ORDER BY started_at DESC
        LIMIT ?
    """, [name, limit]).fetchall()
    conn.close()
    return [
        {
            "session_date": str(r[0]),
            "started_at": str(r[1]),
            "track_count": r[2],
            "duration_minutes": r[3] or 0,
            "tracks": list(r[4]) if r[4] else [],
        }
        for r in rows
    ]


@router.get("/analytics/album/{name}/history")
def album_history(
    name: str,
    artist: str | None = None,
    granularity: str = "month",
    from_date: str | None = None,
    to_date: str | None = None,
    metric: str = "plays",
):
    trunc = {"day": "day", "week": "week", "month": "month", "year": "year"}.get(granularity, "month")
    agg = "COUNT(DISTINCT track)" if metric == "unique_tracks" else "COUNT(*)"
    where, params = _date_filter(from_date, to_date)
    extra_and = " AND " + where[6:] if where else ""
    artist_and = " AND LOWER(artist) = LOWER(?)" if artist else ""
    artist_params = [artist] if artist else []
    conn = _db()
    rows = conn.execute(f"""
        SELECT DATE_TRUNC('{trunc}', scrobbled_at)::DATE AS date, {agg} AS value
        FROM raw_scrobbles
        WHERE LOWER(COALESCE(album,'')) = LOWER(?){artist_and}{extra_and}
        GROUP BY date ORDER BY date
    """, [name] + artist_params + params).fetchall()
    conn.close()
    return [{"date": str(r[0]), "value": r[1]} for r in rows]


@router.get("/analytics/album/{name}/stats")
def album_stats_detail(name: str, artist: str | None = None):
    conn = _db()
    if artist:
        row = conn.execute(
            "SELECT * FROM album_stats WHERE LOWER(album) = LOWER(?) AND LOWER(artist) = LOWER(?)",
            [name, artist]
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM album_stats WHERE LOWER(album) = LOWER(?) LIMIT 1", [name]
        ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Album not found in stats")
    cols = [d[0] for d in conn.description]
    conn.close()
    return _row_to_dict(row, cols)


@router.get("/analytics/album/{name}/tracks")
def album_tracks(
    name: str,
    artist: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
):
    where, params = _date_filter(from_date, to_date)
    extra_and = " AND " + where[6:] if where else ""
    artist_and = " AND LOWER(artist) = LOWER(?)" if artist else ""
    artist_params = [artist] if artist else []
    conn = _db()
    rows = conn.execute(f"""
        SELECT track, COUNT(*) AS plays
        FROM raw_scrobbles
        WHERE LOWER(COALESCE(album,'')) = LOWER(?){artist_and}{extra_and}
        GROUP BY track ORDER BY plays DESC
    """, [name] + artist_params + params).fetchall()
    conn.close()
    return [{"track": r[0], "plays": r[1]} for r in rows]


@router.get("/analytics/track/{name}/history")
def track_history(
    name: str,
    artist: str | None = None,
    granularity: str = "month",
    from_date: str | None = None,
    to_date: str | None = None,
    metric: str = "plays",
):
    trunc = {"day": "day", "week": "week", "month": "month", "year": "year"}.get(granularity, "month")
    agg = "COUNT(*)"  # track history only meaningful as plays
    where, params = _date_filter(from_date, to_date)
    extra_and = " AND " + where[6:] if where else ""
    artist_and = " AND LOWER(artist) = LOWER(?)" if artist else ""
    artist_params = [artist] if artist else []
    conn = _db()
    rows = conn.execute(f"""
        SELECT DATE_TRUNC('{trunc}', scrobbled_at)::DATE AS date, {agg} AS value
        FROM raw_scrobbles
        WHERE LOWER(track) = LOWER(?){artist_and}{extra_and}
        GROUP BY date ORDER BY date
    """, [name] + artist_params + params).fetchall()
    conn.close()
    return [{"date": str(r[0]), "value": r[1]} for r in rows]


@router.get("/analytics/track/{name}/stats")
def track_stats_detail(name: str, artist: str | None = None):
    conn = _db()
    if artist:
        row = conn.execute(
            "SELECT * FROM track_stats WHERE LOWER(track) = LOWER(?) AND LOWER(artist) = LOWER(?)",
            [name, artist]
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM track_stats WHERE LOWER(track) = LOWER(?) LIMIT 1", [name]
        ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Track not found in stats")
    cols = [d[0] for d in conn.description]
    conn.close()
    return _row_to_dict(row, cols)


@router.get("/analytics/entities/artists")
def entities_artists(
    sort_by: str = "rank_all_time",
    sort_dir: str = "asc",
    limit: int = 100,
    offset: int = 0,
    search: str | None = None,
    set_id: str | None = None,
    genre_filter: str | None = None,
    request: Request = None,
):
    if sort_by not in _VALID_SORT["artist"]:
        sort_by = "rank_all_time"
    sort_dir = "ASC" if sort_dir.lower() == "asc" else "DESC"
    conn = _db()
    conditions, params = [], []
    if search:
        conditions.append("LOWER(artist) LIKE LOWER(?)")
        params.append(f"%{search}%")
    if set_id:
        conditions.append("artist IN (SELECT entity_id FROM set_members WHERE set_id = ?)")
        params.append(set_id)
    if genre_filter:
        conditions.append("artist IN (SELECT artist_name FROM artist_tags WHERE LOWER(tag) = LOWER(?))")
        params.append(genre_filter)

    # Parse and apply filters from query params
    if request:
        query_params = dict(request.query_params)
        filter_conditions, filter_params = _parse_filters(query_params, "artist")
        conditions.extend(filter_conditions)
        params.extend(filter_params)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    total = conn.execute(f"SELECT COUNT(*) FROM artist_stats {where}", params).fetchone()[0]
    rows = conn.execute(f"""
        SELECT ast.*,
            COALESCE(
                (SELECT ago.genre FROM artist_genre_override ago WHERE ago.artist_name = ast.artist LIMIT 1),
                (SELECT att.tag FROM artist_tags att WHERE att.artist_name = ast.artist ORDER BY att.weight DESC LIMIT 1)
            ) AS genre
        FROM artist_stats ast
        {where}
        ORDER BY {sort_by} {sort_dir} NULLS LAST, total_plays DESC NULLS LAST
        LIMIT ? OFFSET ?
    """, params + [limit, offset]).fetchall()
    cols = [d[0] for d in conn.description]
    conn.close()
    return {"total": total, "rows": [_row_to_dict(r, cols) for r in rows]}


@router.get("/analytics/entities/albums")
def entities_albums(
    sort_by: str = "rank_all_time",
    sort_dir: str = "asc",
    limit: int = 100,
    offset: int = 0,
    search: str | None = None,
    genre_filter: str | None = None,
    request: Request = None,
):
    if sort_by not in _VALID_SORT["album"]:
        sort_by = "rank_all_time"
    sort_dir = "ASC" if sort_dir.lower() == "asc" else "DESC"
    conn = _db()
    conditions, params = [], []
    if search:
        conditions.append("(LOWER(album) LIKE LOWER(?) OR LOWER(artist) LIKE LOWER(?))")
        params += [f"%{search}%", f"%{search}%"]
    if genre_filter:
        conditions.append("artist IN (SELECT artist_name FROM artist_tags WHERE LOWER(tag) = LOWER(?))")
        params.append(genre_filter)

    # Parse and apply filters from query params
    if request:
        query_params = dict(request.query_params)
        filter_conditions, filter_params = _parse_filters(query_params, "album")
        conditions.extend(filter_conditions)
        params.extend(filter_params)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    total = conn.execute(f"SELECT COUNT(*) FROM album_stats {where}", params).fetchone()[0]
    rows = conn.execute(f"""
        SELECT ast.*,
            COALESCE(
                (SELECT ago.genre FROM artist_genre_override ago WHERE ago.artist_name = ast.artist LIMIT 1),
                (SELECT att.tag FROM artist_tags att WHERE att.artist_name = ast.artist ORDER BY att.weight DESC LIMIT 1)
            ) AS genre
        FROM album_stats ast
        {where}
        ORDER BY {sort_by} {sort_dir} NULLS LAST, total_plays DESC NULLS LAST
        LIMIT ? OFFSET ?
    """, params + [limit, offset]).fetchall()
    cols = [d[0] for d in conn.description]
    conn.close()
    return {"total": total, "rows": [_row_to_dict(r, cols) for r in rows]}


@router.get("/analytics/entities/tracks")
def entities_tracks(
    sort_by: str = "rank_all_time",
    sort_dir: str = "asc",
    limit: int = 100,
    offset: int = 0,
    search: str | None = None,
    genre_filter: str | None = None,
    mood_filter: str | None = None,
    set_id: str | None = None,
    request: Request = None,
):
    if sort_by not in _VALID_SORT["track"]:
        sort_by = "rank_all_time"
    sort_dir = "ASC" if sort_dir.lower() == "asc" else "DESC"
    conn = _db()
    conditions, params = [], []
    if search:
        conditions.append("(LOWER(track) LIKE LOWER(?) OR LOWER(artist) LIKE LOWER(?))")
        params += [f"%{search}%", f"%{search}%"]
    if genre_filter:
        conditions.append("artist IN (SELECT artist_name FROM artist_tags WHERE LOWER(tag) = LOWER(?))")
        params.append(genre_filter)
    if mood_filter:
        conditions.append("""EXISTS (
            SELECT 1 FROM track_mood tm
            WHERE LOWER(tm.track) = LOWER(track) AND LOWER(tm.artist) = LOWER(artist)
            AND list_contains(tm.tags, ?)
        )""")
        params.append(mood_filter)
    if set_id:
        conditions.append("(track || '|||' || artist) IN (SELECT entity_id FROM set_members WHERE set_id = ?)")
        params.append(set_id)

    # Parse and apply filters from query params
    if request:
        query_params = dict(request.query_params)
        filter_conditions, filter_params = _parse_filters(query_params, "track")
        conditions.extend(filter_conditions)
        params.extend(filter_params)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    total = conn.execute(f"SELECT COUNT(*) FROM track_stats {where}", params).fetchone()[0]
    rows = conn.execute(f"""
        SELECT ast.*,
            COALESCE(
                (SELECT ago.genre FROM artist_genre_override ago WHERE ago.artist_name = ast.artist LIMIT 1),
                (SELECT att.tag FROM artist_tags att WHERE att.artist_name = ast.artist ORDER BY att.weight DESC LIMIT 1)
            ) AS genre
        FROM track_stats ast
        {where}
        ORDER BY {sort_by} {sort_dir} NULLS LAST, total_plays DESC NULLS LAST
        LIMIT ? OFFSET ?
    """, params + [limit, offset]).fetchall()
    cols = [d[0] for d in conn.description]
    conn.close()
    return {"total": total, "rows": [_row_to_dict(r, cols) for r in rows]}


class GenreOverrideBody(BaseModel):
    artist: str
    genre: str  # empty string = clear override


@router.patch("/analytics/entities/genre")
def set_genre_override(body: GenreOverrideBody):
    conn = _db()
    try:
        if body.genre.strip():
            conn.execute("""
                INSERT INTO artist_genre_override (artist_name, genre, updated_at)
                VALUES (?, ?, now())
                ON CONFLICT (artist_name) DO UPDATE SET
                    genre = excluded.genre,
                    updated_at = excluded.updated_at
            """, [body.artist, body.genre.strip()])
        else:
            conn.execute("DELETE FROM artist_genre_override WHERE artist_name = ?", [body.artist])
    finally:
        conn.close()
    return {"ok": True}


@router.get("/analytics/top-entities")
def top_entities(
    entity_type: str = "artist",
    limit: int = 15,
    from_date: str | None = None,
    to_date: str | None = None,
    genre_filter: str | None = None,
):
    """Top entities by play count within a date range — queries raw_scrobbles directly."""
    where, params = _date_filter(from_date, to_date)
    conn = _db()

    genre_join = ""
    if genre_filter:
        genre_join = "JOIN artist_tags atags ON LOWER(s.artist) = LOWER(atags.artist_name) AND LOWER(atags.tag) = LOWER(?)"
        params.append(genre_filter)

    if entity_type == "artist":
        rows = conn.execute(f"""
            SELECT s.artist AS name, NULL AS secondary, COUNT(*) AS plays
            FROM raw_scrobbles s
            {genre_join}
            {where}
            GROUP BY s.artist ORDER BY plays DESC LIMIT ?
        """, params + [limit]).fetchall()
    elif entity_type == "album":
        rows = conn.execute(f"""
            SELECT COALESCE(s.album, '[Unknown]') AS name, s.artist AS secondary, COUNT(*) AS plays
            FROM raw_scrobbles s
            {genre_join}
            {where}
            GROUP BY name, secondary ORDER BY plays DESC LIMIT ?
        """, params + [limit]).fetchall()
    else:
        rows = conn.execute(f"""
            SELECT s.track AS name, s.artist AS secondary, COUNT(*) AS plays
            FROM raw_scrobbles s
            {genre_join}
            {where}
            GROUP BY name, secondary ORDER BY plays DESC LIMIT ?
        """, params + [limit]).fetchall()
    conn.close()
    return [{"name": r[0], "secondary": r[1], "plays": r[2]} for r in rows]


@router.get("/analytics/search")
def search(q: str, limit: int = 20):
    if not q or len(q) < 2:
        return []
    like = f"%{q}%"
    conn = _db()
    rows = conn.execute("""
        SELECT 'artist' AS type, artist AS name, NULL AS secondary, total_plays AS plays
        FROM artist_stats WHERE LOWER(artist) LIKE LOWER(?)
        UNION ALL
        SELECT 'album', album, artist, total_plays
        FROM album_stats WHERE LOWER(album) LIKE LOWER(?) OR LOWER(artist) LIKE LOWER(?)
        UNION ALL
        SELECT 'track', track, artist, total_plays
        FROM track_stats WHERE LOWER(track) LIKE LOWER(?) OR LOWER(artist) LIKE LOWER(?)
        ORDER BY plays DESC NULLS LAST
        LIMIT ?
    """, [like, like, like, like, like, limit]).fetchall()
    conn.close()
    return [{"type": r[0], "name": r[1], "secondary": r[2], "plays": r[3]} for r in rows]


# ---------------------------------------------------------------------------
# User API endpoints
# ---------------------------------------------------------------------------

class DashboardCreate(BaseModel):
    name: str


class DashboardUpdate(BaseModel):
    name: str | None = None


class ChartCreate(BaseModel):
    predefined_id: str | None = None
    is_custom: bool = False
    chart_type: str
    title: str | None = None
    metric: str | None = None
    timespan: str | None = None
    top_n: int | None = None
    filters: dict | None = None
    visible: bool = True
    sort_order: int = 0


class ChartUpdate(BaseModel):
    title: str | None = None
    metric: str | None = None
    timespan: str | None = None
    top_n: int | None = None
    filters: dict | None = None
    visible: bool | None = None
    sort_order: int | None = None


class ExploreChartCreate(BaseModel):
    predefined_id: str | None = None
    is_custom: bool = False
    pinned: bool = False
    chart_type: str
    metric: str | None = None
    default_timespan: str | None = None
    visible: bool = True
    sort_order: int = 0


class ReportCreate(BaseModel):
    name: str
    entity_type: str
    columns: list | None = None
    filters: list | None = None
    sort: dict | None = None
    set_id: str | None = None


class SetCreate(BaseModel):
    name: str
    entity_type: str


class SetMembersUpdate(BaseModel):
    add: list[dict] | None = None     # [{entity_id, display_name}]
    remove: list[str] | None = None   # [entity_id]


class AnalyticsChatRequest(BaseModel):
    prompt: str
    context_snapshot: dict


@router.get("/user/dashboards")
def list_dashboards():
    conn = _db()
    rows = conn.execute(
        "SELECT dashboard_id, name, created_at, updated_at FROM user_dashboards ORDER BY updated_at DESC"
    ).fetchall()
    conn.close()
    return [{"dashboard_id": r[0], "name": r[1],
             "created_at": r[2].isoformat() if r[2] else None,
             "updated_at": r[3].isoformat() if r[3] else None} for r in rows]


@router.post("/user/dashboards", status_code=201)
def create_dashboard(body: DashboardCreate):
    did = str(uuid4())
    now = datetime.now(tz=timezone.utc)
    conn = _db()
    conn.execute(
        "INSERT INTO user_dashboards (dashboard_id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
        [did, body.name, now, now]
    )
    conn.close()
    return {"dashboard_id": did, "name": body.name}


@router.patch("/user/dashboards/{dashboard_id}")
def update_dashboard(dashboard_id: str, body: DashboardUpdate):
    now = datetime.now(tz=timezone.utc)
    conn = _db()
    conn.execute(
        "UPDATE user_dashboards SET name = COALESCE(?, name), updated_at = ? WHERE dashboard_id = ?",
        [body.name, now, dashboard_id]
    )
    conn.close()
    return {"dashboard_id": dashboard_id}


@router.delete("/user/dashboards/{dashboard_id}", status_code=204)
def delete_dashboard(dashboard_id: str):
    conn = _db()
    conn.execute("DELETE FROM dashboard_charts WHERE dashboard_id = ?", [dashboard_id])
    conn.execute("DELETE FROM user_dashboards WHERE dashboard_id = ?", [dashboard_id])
    conn.close()


@router.get("/user/dashboards/{dashboard_id}/charts")
def get_dashboard_charts(dashboard_id: str):
    conn = _db()
    rows = conn.execute(
        "SELECT * FROM dashboard_charts WHERE dashboard_id = ? ORDER BY sort_order",
        [dashboard_id]
    ).fetchall()
    cols = [d[0] for d in conn.description]
    conn.close()
    result = []
    for r in rows:
        d = dict(zip(cols, r))
        if isinstance(d.get("filters"), str):
            try:
                d["filters"] = json.loads(d["filters"])
            except Exception:
                pass
        result.append(d)
    return result


@router.post("/user/dashboards/{dashboard_id}/charts", status_code=201)
def save_chart(dashboard_id: str, body: ChartCreate):
    cid = str(uuid4())
    conn = _db()
    conn.execute("""
        INSERT INTO dashboard_charts
            (chart_id, dashboard_id, predefined_id, is_custom, chart_type, title, metric,
             timespan, top_n, filters, visible, sort_order)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [cid, dashboard_id, body.predefined_id, body.is_custom, body.chart_type,
          body.title, body.metric, body.timespan, body.top_n,
          json.dumps(body.filters) if body.filters else None,
          body.visible, body.sort_order])
    conn.close()
    return {"chart_id": cid}


@router.patch("/user/dashboards/{dashboard_id}/charts/{chart_id}")
def update_chart(dashboard_id: str, chart_id: str, body: ChartUpdate):
    conn = _db()
    updates = body.model_dump(exclude_none=True)
    if "filters" in updates:
        updates["filters"] = json.dumps(updates["filters"])
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    if not set_clause:
        conn.close()
        return {"chart_id": chart_id}
    conn.execute(
        f"UPDATE dashboard_charts SET {set_clause} WHERE chart_id = ? AND dashboard_id = ?",
        list(updates.values()) + [chart_id, dashboard_id]
    )
    conn.close()
    return {"chart_id": chart_id}


@router.delete("/user/dashboards/{dashboard_id}/charts/{chart_id}", status_code=204)
def delete_chart(dashboard_id: str, chart_id: str):
    conn = _db()
    conn.execute(
        "DELETE FROM dashboard_charts WHERE chart_id = ? AND dashboard_id = ?",
        [chart_id, dashboard_id]
    )
    conn.close()


@router.get("/user/explore-layout/{entity_type}")
def get_explore_layout(entity_type: str):
    conn = _db()
    rows = conn.execute(
        "SELECT * FROM explore_layouts WHERE entity_type = ? ORDER BY sort_order",
        [entity_type]
    ).fetchall()
    cols = [d[0] for d in conn.description]
    conn.close()
    return [dict(zip(cols, r)) for r in rows]


@router.post("/user/explore-layout/{entity_type}/charts", status_code=201)
def save_explore_chart(entity_type: str, body: ExploreChartCreate):
    lid = str(uuid4())
    conn = _db()
    conn.execute("""
        INSERT INTO explore_layouts
            (layout_id, entity_type, predefined_id, is_custom, pinned, chart_type,
             metric, default_timespan, visible, sort_order)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [lid, entity_type, body.predefined_id, body.is_custom, body.pinned,
          body.chart_type, body.metric, body.default_timespan, body.visible, body.sort_order])
    conn.close()
    return {"layout_id": lid}


@router.get("/user/reports")
def list_reports():
    conn = _db()
    rows = conn.execute(
        "SELECT * FROM user_reports ORDER BY created_at DESC"
    ).fetchall()
    cols = [d[0] for d in conn.description]
    conn.close()
    result = []
    for r in rows:
        d = dict(zip(cols, r))
        for f in ("columns", "filters", "sort"):
            if isinstance(d.get(f), str):
                try:
                    d[f] = json.loads(d[f])
                except Exception:
                    pass
        result.append(d)
    return result


@router.post("/user/reports", status_code=201)
def save_report(body: ReportCreate):
    rid = str(uuid4())
    now = datetime.now(tz=timezone.utc)
    conn = _db()
    conn.execute("""
        INSERT INTO user_reports (report_id, name, entity_type, columns, filters, sort, set_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, [rid, body.name, body.entity_type,
          json.dumps(body.columns) if body.columns is not None else None,
          json.dumps(body.filters) if body.filters is not None else None,
          json.dumps(body.sort) if body.sort is not None else None,
          body.set_id, now])
    conn.close()
    return {"report_id": rid}


@router.delete("/user/reports/{report_id}", status_code=204)
def delete_report(report_id: str):
    conn = _db()
    conn.execute("DELETE FROM user_reports WHERE report_id = ?", [report_id])
    conn.close()


@router.get("/user/sets")
def list_sets():
    conn = _db()
    rows = conn.execute(
        "SELECT set_id, name, entity_type, created_at FROM user_sets ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return [{"set_id": r[0], "name": r[1], "entity_type": r[2],
             "created_at": r[3].isoformat() if r[3] else None} for r in rows]


@router.post("/user/sets", status_code=201)
def create_set(body: SetCreate):
    sid = str(uuid4())
    now = datetime.now(tz=timezone.utc)
    conn = _db()
    conn.execute(
        "INSERT INTO user_sets (set_id, name, entity_type, created_at) VALUES (?, ?, ?, ?)",
        [sid, body.name, body.entity_type, now]
    )
    conn.close()
    return {"set_id": sid, "name": body.name, "entity_type": body.entity_type}


@router.delete("/user/sets/{set_id}", status_code=204)
def delete_set(set_id: str):
    conn = _db()
    conn.execute("DELETE FROM set_members WHERE set_id = ?", [set_id])
    conn.execute("DELETE FROM user_sets WHERE set_id = ?", [set_id])
    conn.close()


@router.get("/user/sets/{set_id}/members")
def get_set_members(set_id: str):
    conn = _db()
    rows = conn.execute(
        "SELECT entity_id, display_name, added_at FROM set_members WHERE set_id = ? ORDER BY added_at DESC",
        [set_id]
    ).fetchall()
    conn.close()
    return [{"entity_id": r[0], "display_name": r[1],
             "added_at": r[2].isoformat() if r[2] else None} for r in rows]


@router.patch("/user/sets/{set_id}/members")
def update_set_members(set_id: str, body: SetMembersUpdate):
    now = datetime.now(tz=timezone.utc)
    conn = _db()
    if body.add:
        for m in body.add:
            conn.execute("""
                INSERT INTO set_members (set_id, entity_id, display_name, added_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (set_id, entity_id) DO UPDATE SET display_name = EXCLUDED.display_name
            """, [set_id, m["entity_id"], m["display_name"], now])
    if body.remove:
        for eid in body.remove:
            conn.execute(
                "DELETE FROM set_members WHERE set_id = ? AND entity_id = ?", [set_id, eid]
            )
    conn.close()
    return {"set_id": set_id}


@router.get("/user/metrics")
def get_metrics(chart_type: str | None = None):
    catalog = {
        "line":     ["plays", "unique_artists", "unique_tracks"],
        "bar":      ["plays", "unique_artists", "unique_tracks", "unique_albums"],
        "pie":      ["plays"],
        "heatmap":  ["plays"],
        "scatter":  ["total_plays", "days_since_last_heard", "unique_tracks", "rank_7d_delta"],
        "calendar": ["plays"],
    }
    if chart_type:
        return catalog.get(chart_type, [])
    return catalog


# ---------------------------------------------------------------------------
# Analytics Chat endpoint is defined in main.py (supports ANALYTICS_CHAT_STUBS)


@router.get("/analytics/lyric-lines")
def lyric_lines(limit: int = 40, days: int = 0, sample_tracks: int = 400):
    """
    Random couplets from the lyrics of tracks I actually play, for the
    Dashboard's carousel.

    Sampling is weighted the only way that matters here: the pool is the most
    played tracks that have lyrics, so the carousel quotes what's in rotation
    rather than something scrobbled once in 2019. `days` narrows the pool to a
    recent window; 0 means all time.
    """
    limit = max(1, min(limit, 200))
    conn = _db()
    try:
        window = ""
        params: list = []
        if days > 0:
            window = "WHERE s.scrobbled_at >= now() - INTERVAL ? DAY"
            params.append(days)
        rows = conn.execute(f"""
            WITH played AS (
                SELECT s.track, s.artist, COUNT(*) AS plays
                FROM raw_scrobbles s
                {window}
                GROUP BY s.track, s.artist
            )
            SELECT l.track, l.artist, l.lyrics, p.plays, i.local_path
            FROM track_lyrics l
            JOIN played p ON LOWER(p.track) = LOWER(l.track) AND LOWER(p.artist) = LOWER(l.artist)
            LEFT JOIN entity_images i
                   ON i.entity_type = 'artist' AND LOWER(i.entity_name) = LOWER(l.artist)
            WHERE l.lyrics IS NOT NULL AND LENGTH(l.lyrics) > 80
            ORDER BY p.plays DESC
            LIMIT ?
        """, [*params, max(limit, sample_tracks)]).fetchall()
    finally:
        conn.close()

    random.shuffle(rows)
    out = []
    for track, artist, lyrics, plays, image in rows:
        lines = [ln.strip() for ln in lyrics.splitlines()]
        # Section markers ("[Chorus]"), blanks, and lines that are one word
        # repeated ("Do-do, do-do...") all quote badly.
        def quotable(ln):
            words = [w.strip(",.!?\"'()").lower() for w in ln.split()]
            return 12 <= len(ln) <= 90 and not ln.startswith("[") and len(set(words)) >= 3

        usable = [i for i, ln in enumerate(lines) if quotable(ln)]
        if not usable:
            continue
        start = random.choice(usable)
        couplet = [lines[start]]
        if start + 1 < len(lines) and quotable(lines[start + 1]):
            couplet.append(lines[start + 1])
        out.append({
            "track": track, "artist": artist, "plays": plays,
            "lines": couplet, "image_path": image,
        })
        if len(out) >= limit:
            break
    return out


@router.get("/analytics/top-visuals")
def top_visuals(period: str = "90d", limit: int = 18, entity: str = "artist"):
    """
    Top artists or albums for a period, joined to their fetched artwork.

    Powers the artwork strip at the top of the Dashboard. Entities without an
    image are still returned (with image=None) so the UI can show a styled
    initials tile rather than a gap — artwork coverage is never 100%, since
    Deezer won't match every obscure release.

    Periods with a precomputed column in the stats tables read from those.
    The Dashboard also offers 3y and 4y, which have no column; those are
    counted straight from raw_scrobbles. (They used to fall back to 90d
    silently, so the strip labelled "3y" showed the last three months.)
    """
    stats_col = {
        "7d": "plays_7d", "30d": "plays_30d", "90d": "plays_90d",
        "180d": "plays_180d", "1y": "plays_1y", "2y": "plays_2y",
        "5y": "plays_5y", "all": "total_plays",
    }.get(period)
    raw_days = {"3y": 1095, "4y": 1460}.get(period)
    if stats_col is None and raw_days is None:
        raise HTTPException(status_code=400, detail=f"unknown period: {period}")
    limit = max(1, min(limit, 60))
    conn = _db()

    try:
        if entity == "album":
            if stats_col:
                src = f"""SELECT album, artist, {stats_col} AS plays FROM album_stats
                          WHERE {stats_col} > 0"""
            else:
                src = f"""SELECT album, artist, COUNT(*) AS plays FROM raw_scrobbles
                          WHERE scrobbled_at >= now() - INTERVAL '{raw_days} days'
                          GROUP BY album, artist"""
            rows = conn.execute(f"""
                SELECT s.album, s.artist, s.plays, i.local_path
                FROM ({src}) s
                LEFT JOIN entity_images i
                       ON i.entity_type = 'album'
                      AND LOWER(i.entity_name) = LOWER(s.album || '||' || s.artist)
                WHERE s.album IS NOT NULL AND s.album <> ''
                ORDER BY s.plays DESC
                LIMIT ?
            """, [limit]).fetchall()
            return [
                {
                    "name": r[0],
                    "artist": r[1],
                    "plays": r[2],
                    # Relative to the image root; the frontend prefixes it
                    # (/api/images/...) since only it knows the proxy layout.
                    "image_path": r[3] or None,
                }
                for r in rows
            ]

        if stats_col:
            src = f"SELECT artist, {stats_col} AS plays FROM artist_stats WHERE {stats_col} > 0"
        else:
            src = f"""SELECT artist, COUNT(*) AS plays FROM raw_scrobbles
                      WHERE scrobbled_at >= now() - INTERVAL '{raw_days} days'
                      GROUP BY artist"""
        rows = conn.execute(f"""
            SELECT s.artist, s.plays, i.local_path
            FROM ({src}) s
            LEFT JOIN entity_images i
                   ON i.entity_type = 'artist' AND LOWER(i.entity_name) = LOWER(s.artist)
            ORDER BY s.plays DESC
            LIMIT ?
        """, [limit]).fetchall()
        return [
            {
                "name": r[0],
                "artist": None,
                "plays": r[1],
                "image_path": r[2] or None,
            }
            for r in rows
        ]
    except Exception as e:
        # entity_images may not exist until the visuals pipeline first runs.
        raise HTTPException(status_code=503, detail=f"visuals unavailable: {e}")
    finally:
        conn.close()
