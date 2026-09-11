"""
Rebuilds artist_stats, album_stats, track_stats tables from raw_scrobbles.
Full truncate + recompute on each run (~3-5s in DuckDB).
Triggered after every Last.fm sync.
"""
import duckdb
from backend.db.schema import DB_PATH


def rebuild_stats_tables():
    conn = duckdb.connect(str(DB_PATH))

    print("[rebuild_stats] Rebuilding artist_stats…")
    conn.execute("""
        CREATE OR REPLACE TABLE artist_stats AS
        WITH plays AS (
            SELECT
                artist,
                COUNT(*) AS total_plays,
                COUNT(*) FILTER (WHERE scrobbled_at >= now() - INTERVAL '7 days')    AS plays_7d,
                COUNT(*) FILTER (WHERE scrobbled_at >= now() - INTERVAL '30 days')   AS plays_30d,
                COUNT(*) FILTER (WHERE scrobbled_at >= now() - INTERVAL '90 days')   AS plays_90d,
                COUNT(*) FILTER (WHERE scrobbled_at >= now() - INTERVAL '180 days')  AS plays_180d,
                COUNT(*) FILTER (WHERE scrobbled_at >= now() - INTERVAL '365 days')  AS plays_1y,
                COUNT(*) FILTER (WHERE scrobbled_at >= now() - INTERVAL '730 days')  AS plays_2y,
                COUNT(*) FILTER (WHERE scrobbled_at >= now() - INTERVAL '1825 days') AS plays_5y,
                COUNT(*) FILTER (WHERE scrobbled_at >= now() - INTERVAL '14 days'
                                   AND scrobbled_at <  now() - INTERVAL '7 days')    AS plays_7d_prev,
                COUNT(*) FILTER (WHERE scrobbled_at >= now() - INTERVAL '60 days'
                                   AND scrobbled_at <  now() - INTERVAL '30 days')   AS plays_30d_prev,
                COUNT(*) FILTER (WHERE scrobbled_at >= now() - INTERVAL '180 days'
                                   AND scrobbled_at <  now() - INTERVAL '90 days')   AS plays_90d_prev,
                COUNT(*) FILTER (WHERE scrobbled_at >= now() - INTERVAL '360 days'
                                   AND scrobbled_at <  now() - INTERVAL '180 days')  AS plays_180d_prev,
                COUNT(*) FILTER (WHERE scrobbled_at >= now() - INTERVAL '730 days'
                                   AND scrobbled_at <  now() - INTERVAL '365 days')  AS plays_1y_prev,
                MIN(scrobbled_at) AS first_heard,
                MAX(scrobbled_at) AS last_heard,
                COUNT(DISTINCT track) AS unique_tracks,
                COUNT(DISTINCT album) AS unique_albums
            FROM raw_scrobbles
            GROUP BY artist
        ),
        ranked AS (
            SELECT *,
                RANK() OVER (ORDER BY total_plays  DESC NULLS LAST) AS rank_all_time,
                RANK() OVER (ORDER BY plays_7d     DESC NULLS LAST) AS rank_7d,
                RANK() OVER (ORDER BY plays_30d    DESC NULLS LAST) AS rank_30d,
                RANK() OVER (ORDER BY plays_90d    DESC NULLS LAST) AS rank_90d,
                RANK() OVER (ORDER BY plays_180d   DESC NULLS LAST) AS rank_180d,
                RANK() OVER (ORDER BY plays_1y     DESC NULLS LAST) AS rank_1y,
                RANK() OVER (ORDER BY plays_7d_prev   DESC NULLS LAST) AS rank_7d_prev_val,
                RANK() OVER (ORDER BY plays_30d_prev  DESC NULLS LAST) AS rank_30d_prev_val,
                RANK() OVER (ORDER BY plays_90d_prev  DESC NULLS LAST) AS rank_90d_prev_val
            FROM plays
        ),
        daily_plays AS (
            SELECT DISTINCT artist, CAST(scrobbled_at AS DATE) AS play_date
            FROM raw_scrobbles
        ),
        streak_groups AS (
            SELECT
                artist,
                play_date,
                DATEDIFF('day', DATE '1970-01-01', play_date)
                    - CAST(ROW_NUMBER() OVER (PARTITION BY artist ORDER BY play_date) AS INTEGER) AS grp
            FROM daily_plays
        ),
        streak_sizes AS (
            SELECT artist, grp, COUNT(*) AS streak_len, MAX(play_date) AS last_day
            FROM streak_groups
            GROUP BY artist, grp
        ),
        streaks AS (
            SELECT
                artist,
                MAX(streak_len) AS longest_streak_days,
                COALESCE(MAX(streak_len) FILTER (WHERE last_day >= CURRENT_DATE - 1), 0) AS current_streak_days
            FROM streak_sizes
            GROUP BY artist
        ),
        weekly_plays AS (
            SELECT artist, DATE_TRUNC('week', scrobbled_at)::DATE AS week_start, COUNT(*) AS week_plays
            FROM raw_scrobbles
            GROUP BY artist, DATE_TRUNC('week', scrobbled_at)::DATE
        ),
        peak_weeks AS (
            SELECT artist, week_start, week_plays,
                ROW_NUMBER() OVER (PARTITION BY artist ORDER BY week_plays DESC, week_start) AS rn
            FROM weekly_plays
        )
        SELECT
            r.artist,
            r.total_plays,
            r.plays_7d, r.plays_30d, r.plays_90d, r.plays_180d, r.plays_1y, r.plays_2y, r.plays_5y,
            r.plays_7d_prev, r.plays_30d_prev, r.plays_90d_prev, r.plays_180d_prev, r.plays_1y_prev,
            r.plays_7d   - r.plays_7d_prev   AS plays_7d_delta,
            r.plays_30d  - r.plays_30d_prev  AS plays_30d_delta,
            r.plays_90d  - r.plays_90d_prev  AS plays_90d_delta,
            r.plays_180d - r.plays_180d_prev AS plays_180d_delta,
            r.plays_1y   - r.plays_1y_prev   AS plays_1y_delta,
            r.rank_all_time, r.rank_7d, r.rank_30d, r.rank_90d, r.rank_180d, r.rank_1y,
            r.rank_7d_prev_val  - r.rank_7d  AS rank_7d_delta,
            r.rank_30d_prev_val - r.rank_30d AS rank_30d_delta,
            r.rank_90d_prev_val - r.rank_90d AS rank_90d_delta,
            r.first_heard,
            r.last_heard,
            DATEDIFF('day', r.last_heard, now()) AS days_since_last_heard,
            r.unique_tracks,
            r.unique_albums,
            COALESCE(s.longest_streak_days, 1) AS longest_streak_days,
            COALESCE(s.current_streak_days, 0) AS current_streak_days,
            pw.week_start AS peak_week_date,
            pw.week_plays AS peak_week_plays,
            now() AS refreshed_at
        FROM ranked r
        LEFT JOIN streaks s ON r.artist = s.artist
        LEFT JOIN peak_weeks pw ON r.artist = pw.artist AND pw.rn = 1
    """)

    count = conn.execute("SELECT COUNT(*) FROM artist_stats").fetchone()[0]
    print(f"[rebuild_stats] artist_stats: {count} rows")

    print("[rebuild_stats] Rebuilding album_stats…")
    conn.execute("""
        CREATE OR REPLACE TABLE album_stats AS
        WITH plays AS (
            SELECT
                COALESCE(album, '[Unknown Album]') AS album,
                artist,
                COUNT(*) AS total_plays,
                COUNT(*) FILTER (WHERE scrobbled_at >= now() - INTERVAL '7 days')    AS plays_7d,
                COUNT(*) FILTER (WHERE scrobbled_at >= now() - INTERVAL '30 days')   AS plays_30d,
                COUNT(*) FILTER (WHERE scrobbled_at >= now() - INTERVAL '90 days')   AS plays_90d,
                COUNT(*) FILTER (WHERE scrobbled_at >= now() - INTERVAL '180 days')  AS plays_180d,
                COUNT(*) FILTER (WHERE scrobbled_at >= now() - INTERVAL '365 days')  AS plays_1y,
                COUNT(*) FILTER (WHERE scrobbled_at >= now() - INTERVAL '730 days')  AS plays_2y,
                COUNT(*) FILTER (WHERE scrobbled_at >= now() - INTERVAL '1825 days') AS plays_5y,
                COUNT(*) FILTER (WHERE scrobbled_at >= now() - INTERVAL '14 days'
                                   AND scrobbled_at <  now() - INTERVAL '7 days')    AS plays_7d_prev,
                COUNT(*) FILTER (WHERE scrobbled_at >= now() - INTERVAL '60 days'
                                   AND scrobbled_at <  now() - INTERVAL '30 days')   AS plays_30d_prev,
                COUNT(*) FILTER (WHERE scrobbled_at >= now() - INTERVAL '180 days'
                                   AND scrobbled_at <  now() - INTERVAL '90 days')   AS plays_90d_prev,
                COUNT(*) FILTER (WHERE scrobbled_at >= now() - INTERVAL '360 days'
                                   AND scrobbled_at <  now() - INTERVAL '180 days')  AS plays_180d_prev,
                COUNT(*) FILTER (WHERE scrobbled_at >= now() - INTERVAL '730 days'
                                   AND scrobbled_at <  now() - INTERVAL '365 days')  AS plays_1y_prev,
                MIN(scrobbled_at) AS first_heard,
                MAX(scrobbled_at) AS last_heard,
                COUNT(DISTINCT track) AS unique_tracks
            FROM raw_scrobbles
            GROUP BY COALESCE(album, '[Unknown Album]'), artist
        ),
        ranked AS (
            SELECT *,
                RANK() OVER (ORDER BY total_plays  DESC NULLS LAST) AS rank_all_time,
                RANK() OVER (ORDER BY plays_7d     DESC NULLS LAST) AS rank_7d,
                RANK() OVER (ORDER BY plays_30d    DESC NULLS LAST) AS rank_30d,
                RANK() OVER (ORDER BY plays_90d    DESC NULLS LAST) AS rank_90d,
                RANK() OVER (ORDER BY plays_180d   DESC NULLS LAST) AS rank_180d,
                RANK() OVER (ORDER BY plays_1y     DESC NULLS LAST) AS rank_1y,
                RANK() OVER (ORDER BY plays_7d_prev  DESC NULLS LAST) AS rank_7d_prev_val,
                RANK() OVER (ORDER BY plays_30d_prev DESC NULLS LAST) AS rank_30d_prev_val,
                RANK() OVER (ORDER BY plays_90d_prev DESC NULLS LAST) AS rank_90d_prev_val
            FROM plays
        ),
        daily_plays AS (
            SELECT DISTINCT COALESCE(album, '[Unknown Album]') AS album, artist,
                CAST(scrobbled_at AS DATE) AS play_date
            FROM raw_scrobbles
        ),
        streak_groups AS (
            SELECT album, artist, play_date,
                DATEDIFF('day', DATE '1970-01-01', play_date)
                    - CAST(ROW_NUMBER() OVER (PARTITION BY album, artist ORDER BY play_date) AS INTEGER) AS grp
            FROM daily_plays
        ),
        streak_sizes AS (
            SELECT album, artist, grp, COUNT(*) AS streak_len, MAX(play_date) AS last_day
            FROM streak_groups
            GROUP BY album, artist, grp
        ),
        streaks AS (
            SELECT album, artist,
                MAX(streak_len) AS longest_streak_days,
                COALESCE(MAX(streak_len) FILTER (WHERE last_day >= CURRENT_DATE - 1), 0) AS current_streak_days
            FROM streak_sizes
            GROUP BY album, artist
        ),
        weekly_plays AS (
            SELECT COALESCE(album, '[Unknown Album]') AS album, artist,
                DATE_TRUNC('week', scrobbled_at)::DATE AS week_start, COUNT(*) AS week_plays
            FROM raw_scrobbles
            GROUP BY COALESCE(album, '[Unknown Album]'), artist, DATE_TRUNC('week', scrobbled_at)::DATE
        ),
        peak_weeks AS (
            SELECT album, artist, week_start, week_plays,
                ROW_NUMBER() OVER (PARTITION BY album, artist ORDER BY week_plays DESC, week_start) AS rn
            FROM weekly_plays
        )
        SELECT
            r.album, r.artist,
            r.total_plays,
            r.plays_7d, r.plays_30d, r.plays_90d, r.plays_180d, r.plays_1y, r.plays_2y, r.plays_5y,
            r.plays_7d_prev, r.plays_30d_prev, r.plays_90d_prev, r.plays_180d_prev, r.plays_1y_prev,
            r.plays_7d   - r.plays_7d_prev   AS plays_7d_delta,
            r.plays_30d  - r.plays_30d_prev  AS plays_30d_delta,
            r.plays_90d  - r.plays_90d_prev  AS plays_90d_delta,
            r.plays_180d - r.plays_180d_prev AS plays_180d_delta,
            r.plays_1y   - r.plays_1y_prev   AS plays_1y_delta,
            r.rank_all_time, r.rank_7d, r.rank_30d, r.rank_90d, r.rank_180d, r.rank_1y,
            r.rank_7d_prev_val  - r.rank_7d  AS rank_7d_delta,
            r.rank_30d_prev_val - r.rank_30d AS rank_30d_delta,
            r.rank_90d_prev_val - r.rank_90d AS rank_90d_delta,
            r.first_heard,
            r.last_heard,
            DATEDIFF('day', r.last_heard, now()) AS days_since_last_heard,
            r.unique_tracks,
            COALESCE(s.longest_streak_days, 1) AS longest_streak_days,
            COALESCE(s.current_streak_days, 0) AS current_streak_days,
            pw.week_start AS peak_week_date,
            pw.week_plays AS peak_week_plays,
            now() AS refreshed_at
        FROM ranked r
        LEFT JOIN streaks s ON r.album = s.album AND r.artist = s.artist
        LEFT JOIN peak_weeks pw ON r.album = pw.album AND r.artist = pw.artist AND pw.rn = 1
    """)

    count = conn.execute("SELECT COUNT(*) FROM album_stats").fetchone()[0]
    print(f"[rebuild_stats] album_stats: {count} rows")

    print("[rebuild_stats] Rebuilding track_stats…")
    conn.execute("""
        CREATE OR REPLACE TABLE track_stats AS
        WITH plays AS (
            SELECT
                track,
                artist,
                COUNT(*) AS total_plays,
                COUNT(*) FILTER (WHERE scrobbled_at >= now() - INTERVAL '7 days')    AS plays_7d,
                COUNT(*) FILTER (WHERE scrobbled_at >= now() - INTERVAL '30 days')   AS plays_30d,
                COUNT(*) FILTER (WHERE scrobbled_at >= now() - INTERVAL '90 days')   AS plays_90d,
                COUNT(*) FILTER (WHERE scrobbled_at >= now() - INTERVAL '180 days')  AS plays_180d,
                COUNT(*) FILTER (WHERE scrobbled_at >= now() - INTERVAL '365 days')  AS plays_1y,
                COUNT(*) FILTER (WHERE scrobbled_at >= now() - INTERVAL '730 days')  AS plays_2y,
                COUNT(*) FILTER (WHERE scrobbled_at >= now() - INTERVAL '1825 days') AS plays_5y,
                COUNT(*) FILTER (WHERE scrobbled_at >= now() - INTERVAL '14 days'
                                   AND scrobbled_at <  now() - INTERVAL '7 days')    AS plays_7d_prev,
                COUNT(*) FILTER (WHERE scrobbled_at >= now() - INTERVAL '60 days'
                                   AND scrobbled_at <  now() - INTERVAL '30 days')   AS plays_30d_prev,
                COUNT(*) FILTER (WHERE scrobbled_at >= now() - INTERVAL '180 days'
                                   AND scrobbled_at <  now() - INTERVAL '90 days')   AS plays_90d_prev,
                COUNT(*) FILTER (WHERE scrobbled_at >= now() - INTERVAL '360 days'
                                   AND scrobbled_at <  now() - INTERVAL '180 days')  AS plays_180d_prev,
                COUNT(*) FILTER (WHERE scrobbled_at >= now() - INTERVAL '730 days'
                                   AND scrobbled_at <  now() - INTERVAL '365 days')  AS plays_1y_prev,
                MIN(scrobbled_at) AS first_heard,
                MAX(scrobbled_at) AS last_heard
            FROM raw_scrobbles
            GROUP BY track, artist
        ),
        ranked AS (
            SELECT *,
                RANK() OVER (ORDER BY total_plays  DESC NULLS LAST) AS rank_all_time,
                RANK() OVER (ORDER BY plays_7d     DESC NULLS LAST) AS rank_7d,
                RANK() OVER (ORDER BY plays_30d    DESC NULLS LAST) AS rank_30d,
                RANK() OVER (ORDER BY plays_90d    DESC NULLS LAST) AS rank_90d,
                RANK() OVER (ORDER BY plays_180d   DESC NULLS LAST) AS rank_180d,
                RANK() OVER (ORDER BY plays_1y     DESC NULLS LAST) AS rank_1y,
                RANK() OVER (ORDER BY plays_7d_prev  DESC NULLS LAST) AS rank_7d_prev_val,
                RANK() OVER (ORDER BY plays_30d_prev DESC NULLS LAST) AS rank_30d_prev_val,
                RANK() OVER (ORDER BY plays_90d_prev DESC NULLS LAST) AS rank_90d_prev_val
            FROM plays
        ),
        daily_plays AS (
            SELECT DISTINCT track, artist, CAST(scrobbled_at AS DATE) AS play_date
            FROM raw_scrobbles
        ),
        streak_groups AS (
            SELECT track, artist, play_date,
                DATEDIFF('day', DATE '1970-01-01', play_date)
                    - CAST(ROW_NUMBER() OVER (PARTITION BY track, artist ORDER BY play_date) AS INTEGER) AS grp
            FROM daily_plays
        ),
        streak_sizes AS (
            SELECT track, artist, grp, COUNT(*) AS streak_len, MAX(play_date) AS last_day
            FROM streak_groups
            GROUP BY track, artist, grp
        ),
        streaks AS (
            SELECT track, artist,
                MAX(streak_len) AS longest_streak_days,
                COALESCE(MAX(streak_len) FILTER (WHERE last_day >= CURRENT_DATE - 1), 0) AS current_streak_days
            FROM streak_sizes
            GROUP BY track, artist
        ),
        weekly_plays AS (
            SELECT track, artist,
                DATE_TRUNC('week', scrobbled_at)::DATE AS week_start, COUNT(*) AS week_plays
            FROM raw_scrobbles
            GROUP BY track, artist, DATE_TRUNC('week', scrobbled_at)::DATE
        ),
        peak_weeks AS (
            SELECT track, artist, week_start, week_plays,
                ROW_NUMBER() OVER (PARTITION BY track, artist ORDER BY week_plays DESC, week_start) AS rn
            FROM weekly_plays
        )
        SELECT
            r.track, r.artist,
            r.total_plays,
            r.plays_7d, r.plays_30d, r.plays_90d, r.plays_180d, r.plays_1y, r.plays_2y, r.plays_5y,
            r.plays_7d_prev, r.plays_30d_prev, r.plays_90d_prev, r.plays_180d_prev, r.plays_1y_prev,
            r.plays_7d   - r.plays_7d_prev   AS plays_7d_delta,
            r.plays_30d  - r.plays_30d_prev  AS plays_30d_delta,
            r.plays_90d  - r.plays_90d_prev  AS plays_90d_delta,
            r.plays_180d - r.plays_180d_prev AS plays_180d_delta,
            r.plays_1y   - r.plays_1y_prev   AS plays_1y_delta,
            r.rank_all_time, r.rank_7d, r.rank_30d, r.rank_90d, r.rank_180d, r.rank_1y,
            r.rank_7d_prev_val  - r.rank_7d  AS rank_7d_delta,
            r.rank_30d_prev_val - r.rank_30d AS rank_30d_delta,
            r.rank_90d_prev_val - r.rank_90d AS rank_90d_delta,
            r.first_heard,
            r.last_heard,
            DATEDIFF('day', r.last_heard, now()) AS days_since_last_heard,
            COALESCE(s.longest_streak_days, 1) AS longest_streak_days,
            COALESCE(s.current_streak_days, 0) AS current_streak_days,
            pw.week_start AS peak_week_date,
            pw.week_plays AS peak_week_plays,
            now() AS refreshed_at
        FROM ranked r
        LEFT JOIN streaks s ON r.track = s.track AND r.artist = s.artist
        LEFT JOIN peak_weeks pw ON r.track = pw.track AND r.artist = pw.artist AND pw.rn = 1
    """)

    count = conn.execute("SELECT COUNT(*) FROM track_stats").fetchone()[0]
    print(f"[rebuild_stats] track_stats: {count} rows")

    conn.close()
    print("[rebuild_stats] Done.")


if __name__ == "__main__":
    rebuild_stats_tables()
