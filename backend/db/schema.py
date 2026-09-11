"""
DuckDB schema creation. Run once to initialize tastemaker.db.
"""
import duckdb
import os
from pathlib import Path

DB_PATH = Path(__file__).parent.parent.parent / "tastemaker.db"


def get_connection(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(DB_PATH), read_only=read_only)


def create_schema():
    conn = get_connection()

    # -------------------------------------------------------------------------
    # Pipeline state — tracks incremental sync cursors
    # -------------------------------------------------------------------------
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pipeline_state (
            pipeline_name  VARCHAR PRIMARY KEY,
            last_fetched_at TIMESTAMPTZ
        )
    """)

    # -------------------------------------------------------------------------
    # Raw tables — direct ingestion, no transformation
    # -------------------------------------------------------------------------
    conn.execute("""
        CREATE TABLE IF NOT EXISTS raw_scrobbles (
            track       VARCHAR NOT NULL,
            artist      VARCHAR NOT NULL,
            album       VARCHAR,
            scrobbled_at TIMESTAMPTZ NOT NULL,
            mbid        VARCHAR,
            PRIMARY KEY (artist, track, scrobbled_at)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS raw_books (
            book_id         VARCHAR PRIMARY KEY,  -- Goodreads book ID
            title           VARCHAR NOT NULL,
            author          VARCHAR,
            isbn            VARCHAR,
            isbn13          VARCHAR,
            rating          INTEGER,              -- 0-5, 0 = not rated
            date_read       DATE,
            date_added      DATE,
            shelf           VARCHAR,
            bookshelves     VARCHAR,
            exclusive_shelf VARCHAR,
            publisher       VARCHAR,
            year_published  INTEGER,
            original_year   INTEGER,
            num_pages       INTEGER,
            ol_subjects     VARCHAR[],            -- OpenLibrary subjects
            ol_description  TEXT,                 -- OpenLibrary description
            -- Mine, not Goodreads'. A Goodreads export has no "date started"
            -- and no private notes, and the importer only updates the columns
            -- it owns, so these survive every re-import.
            date_started    DATE,
            notes           TEXT,
            source          VARCHAR DEFAULT 'goodreads'  -- or 'manual'
        )
    """)
    for col, typedef in (("date_started", "DATE"), ("notes", "TEXT"),
                         ("source", "VARCHAR DEFAULT 'goodreads'")):
        try:
            conn.execute(f"ALTER TABLE raw_books ADD COLUMN IF NOT EXISTS {col} {typedef}")
        except Exception:
            pass

    conn.execute("""
        CREATE TABLE IF NOT EXISTS raw_reddit_posts (
            post_id     VARCHAR PRIMARY KEY,
            subreddit   VARCHAR NOT NULL,
            title       VARCHAR NOT NULL,
            body        TEXT,
            upvotes     INTEGER,
            created_at  TIMESTAMPTZ
        )
    """)

    # -------------------------------------------------------------------------
    # Guitar songs — written directly by the React app (no pipeline)
    # -------------------------------------------------------------------------
    conn.execute("""
        CREATE TABLE IF NOT EXISTS guitar_songs (
            song_id      VARCHAR PRIMARY KEY DEFAULT gen_random_uuid(),
            title        VARCHAR NOT NULL,
            artist       VARCHAR NOT NULL,
            part         VARCHAR CHECK (part IN ('chords', 'tabs', 'solo')),
            difficulty   INTEGER CHECK (difficulty BETWEEN 1 AND 5),       -- NULL = not yet validated
            status       VARCHAR CHECK (status IN (
                             'want_to_learn', 'learning', 'learned', 'abandoned'
                         )),                                                -- NULL = not yet validated
            notes        TEXT,
            date_started DATE DEFAULT CURRENT_DATE,
            created_at   TIMESTAMPTZ DEFAULT now(),
            updated_at   TIMESTAMPTZ DEFAULT now()
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS practice_log (
            log_id      VARCHAR PRIMARY KEY DEFAULT gen_random_uuid(),
            song_id     VARCHAR NOT NULL REFERENCES guitar_songs(song_id),
            practiced_at TIMESTAMPTZ DEFAULT now()
        )
    """)

    # -------------------------------------------------------------------------
    # Dimension tables — built from raw by dbt
    # -------------------------------------------------------------------------
    conn.execute("""
        CREATE TABLE IF NOT EXISTS artists (
            artist_id   VARCHAR PRIMARY KEY DEFAULT gen_random_uuid(),
            name        VARCHAR NOT NULL UNIQUE
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS albums (
            album_id    VARCHAR PRIMARY KEY DEFAULT gen_random_uuid(),
            title       VARCHAR NOT NULL,
            artist_id   VARCHAR REFERENCES artists(artist_id),
            UNIQUE (title, artist_id)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS tracks (
            track_id    VARCHAR PRIMARY KEY DEFAULT gen_random_uuid(),
            title       VARCHAR NOT NULL,
            artist_id   VARCHAR REFERENCES artists(artist_id),
            UNIQUE (title, artist_id)
        )
    """)

    # -------------------------------------------------------------------------
    # Fact tables — built by dbt
    # -------------------------------------------------------------------------
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scrobbles (
            scrobble_id VARCHAR PRIMARY KEY DEFAULT gen_random_uuid(),
            track_id    VARCHAR REFERENCES tracks(track_id),
            artist_id   VARCHAR REFERENCES artists(artist_id),
            album_id    VARCHAR REFERENCES albums(album_id),
            scrobbled_at TIMESTAMPTZ NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS listening_sessions (
            session_id   VARCHAR PRIMARY KEY DEFAULT gen_random_uuid(),
            started_at   TIMESTAMPTZ NOT NULL,
            ended_at     TIMESTAMPTZ NOT NULL,
            track_count  INTEGER NOT NULL
        )
    """)

    # -------------------------------------------------------------------------
    # Enrichment tables — populated by lastfm enrichment pipeline
    # -------------------------------------------------------------------------
    conn.execute("""
        CREATE TABLE IF NOT EXISTS artist_tags (
            artist_name  VARCHAR NOT NULL,
            tag          VARCHAR NOT NULL,
            weight       INTEGER NOT NULL,   -- 0-100, Last.fm community weight
            PRIMARY KEY (artist_name, tag)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS artist_similar (
            artist_name     VARCHAR NOT NULL,
            similar_artist  VARCHAR NOT NULL,
            similarity      FLOAT NOT NULL,  -- 0-1, Last.fm similarity score
            PRIMARY KEY (artist_name, similar_artist)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS track_tags (
            track        VARCHAR NOT NULL,
            artist       VARCHAR NOT NULL,
            tag          VARCHAR NOT NULL,
            weight       INTEGER NOT NULL,   -- 0-100
            PRIMARY KEY (track, artist, tag)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS track_context_tags (
            track        VARCHAR NOT NULL,
            artist       VARCHAR NOT NULL,
            tag          VARCHAR NOT NULL,   -- e.g. 'late_night', 'winter', 'staple'
            tag_type     VARCHAR NOT NULL,   -- 'time_of_day' | 'season' | 'frequency'
            confidence   FLOAT NOT NULL,     -- 0-1: fraction of plays in that bucket
            play_count   INTEGER NOT NULL,
            PRIMARY KEY (track, artist, tag)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS artist_mb (
            artist_name   VARCHAR PRIMARY KEY,
            mb_artist_id  VARCHAR,
            artist_type   VARCHAR,           -- 'Person', 'Group', 'Orchestra', 'Choir'
            country       VARCHAR,           -- ISO 3166-1 alpha-2, e.g. 'US', 'GB'
            formed_year   INTEGER,
            ended         BOOLEAN DEFAULT FALSE,
            tags          VARCHAR[],         -- MusicBrainz genre tags
            fetched_at    TIMESTAMPTZ DEFAULT now()
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS playlists (
            playlist_id  VARCHAR PRIMARY KEY DEFAULT gen_random_uuid(),
            name         VARCHAR NOT NULL,
            prompt       VARCHAR NOT NULL,
            tracks       JSON    NOT NULL,   -- [{title, artist}, ...]
            shortcuts_url VARCHAR NOT NULL,
            thoughts     TEXT,               -- agent reasoning text
            queries      JSON,               -- [sql_string, ...] used to build the playlist
            created_at   TIMESTAMPTZ DEFAULT now(),
            updated_at   TIMESTAMPTZ DEFAULT now()
        )
    """)
    # Migrate existing tables that predate these columns
    for col, typedef in [("thoughts", "TEXT"), ("queries", "JSON")]:
        try:
            conn.execute(f"ALTER TABLE playlists ADD COLUMN IF NOT EXISTS {col} {typedef}")
        except Exception:
            pass

    conn.execute("""
        CREATE TABLE IF NOT EXISTS enrichment_skipped (
            entity_type  VARCHAR NOT NULL,  -- 'artist' or 'track'
            entity_name  VARCHAR NOT NULL,  -- artist name or "track||artist"
            reason       VARCHAR,           -- e.g. 'no_data', 'max_retries'
            skipped_at   TIMESTAMPTZ DEFAULT now(),
            PRIMARY KEY (entity_type, entity_name)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS track_lyrics (
            track       VARCHAR NOT NULL,
            artist      VARCHAR NOT NULL,
            lyrics      TEXT,
            source      VARCHAR,
            fetched_at  TIMESTAMPTZ DEFAULT now(),
            PRIMARY KEY (track, artist)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS track_mood (
            track       VARCHAR NOT NULL,
            artist      VARCHAR NOT NULL,
            tags        VARCHAR[],
            scores      JSON,
            model       VARCHAR,
            overridden  BOOLEAN DEFAULT FALSE,
            analyzed_at TIMESTAMPTZ DEFAULT now(),
            PRIMARY KEY (track, artist)
        )
    """)
    try:
        conn.execute("ALTER TABLE track_mood ADD COLUMN IF NOT EXISTS overridden BOOLEAN DEFAULT FALSE")
    except Exception:
        pass

    # -------------------------------------------------------------------------
    # taste_tags — the cross-domain junction table
    # Maps both books and artists to a shared tag/genre namespace.
    # This is what lets the agent traverse from music → books → guitar.
    # -------------------------------------------------------------------------
    conn.execute("""
        CREATE TABLE IF NOT EXISTS artist_genre_override (
            artist_name VARCHAR PRIMARY KEY,
            genre       VARCHAR NOT NULL,
            updated_at  TIMESTAMPTZ DEFAULT now()
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS taste_tags (
            tag_id      VARCHAR PRIMARY KEY DEFAULT gen_random_uuid(),
            entity_type VARCHAR NOT NULL CHECK (entity_type IN ('book', 'artist', 'track')),
            entity_id   VARCHAR NOT NULL,
            tag         VARCHAR NOT NULL,
            source      VARCHAR NOT NULL,    -- 'openlibrary', 'lastfm', 'manual'
            UNIQUE (entity_type, entity_id, tag)
        )
    """)

    # -------------------------------------------------------------------------
    # Chat persistence — stores agent chat sessions and their messages
    # -------------------------------------------------------------------------
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chats (
            chat_id    VARCHAR PRIMARY KEY DEFAULT gen_random_uuid(),
            title      VARCHAR NOT NULL,
            created_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now()
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS chat_messages (
            message_id VARCHAR PRIMARY KEY DEFAULT gen_random_uuid(),
            chat_id    VARCHAR NOT NULL,
            role       VARCHAR NOT NULL CHECK (role IN ('user', 'assistant')),
            content    TEXT NOT NULL,
            seq        INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ DEFAULT now()
        )
    """)

    # -------------------------------------------------------------------------
    # Stats tables — pre-aggregated, rebuilt after each Last.fm sync
    # -------------------------------------------------------------------------
    conn.execute("""
        CREATE TABLE IF NOT EXISTS artist_stats (
            artist                VARCHAR PRIMARY KEY,
            total_plays           INTEGER NOT NULL DEFAULT 0,
            plays_7d              INTEGER NOT NULL DEFAULT 0,
            plays_30d             INTEGER NOT NULL DEFAULT 0,
            plays_90d             INTEGER NOT NULL DEFAULT 0,
            plays_180d            INTEGER NOT NULL DEFAULT 0,
            plays_1y              INTEGER NOT NULL DEFAULT 0,
            plays_2y              INTEGER NOT NULL DEFAULT 0,
            plays_5y              INTEGER NOT NULL DEFAULT 0,
            plays_7d_prev         INTEGER NOT NULL DEFAULT 0,
            plays_30d_prev        INTEGER NOT NULL DEFAULT 0,
            plays_90d_prev        INTEGER NOT NULL DEFAULT 0,
            plays_180d_prev       INTEGER NOT NULL DEFAULT 0,
            plays_1y_prev         INTEGER NOT NULL DEFAULT 0,
            plays_7d_delta        INTEGER NOT NULL DEFAULT 0,
            plays_30d_delta       INTEGER NOT NULL DEFAULT 0,
            plays_90d_delta       INTEGER NOT NULL DEFAULT 0,
            plays_180d_delta      INTEGER NOT NULL DEFAULT 0,
            plays_1y_delta        INTEGER NOT NULL DEFAULT 0,
            rank_all_time         INTEGER,
            rank_7d               INTEGER,
            rank_30d              INTEGER,
            rank_90d              INTEGER,
            rank_180d             INTEGER,
            rank_1y               INTEGER,
            rank_7d_delta         INTEGER,
            rank_30d_delta        INTEGER,
            rank_90d_delta        INTEGER,
            first_heard           TIMESTAMPTZ,
            last_heard            TIMESTAMPTZ,
            days_since_last_heard FLOAT,
            unique_tracks         INTEGER NOT NULL DEFAULT 0,
            unique_albums         INTEGER NOT NULL DEFAULT 0,
            longest_streak_days   INTEGER NOT NULL DEFAULT 0,
            current_streak_days   INTEGER NOT NULL DEFAULT 0,
            peak_week_date        DATE,
            peak_week_plays       INTEGER,
            refreshed_at          TIMESTAMPTZ
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS album_stats (
            album                 VARCHAR NOT NULL,
            artist                VARCHAR NOT NULL,
            total_plays           INTEGER NOT NULL DEFAULT 0,
            plays_7d              INTEGER NOT NULL DEFAULT 0,
            plays_30d             INTEGER NOT NULL DEFAULT 0,
            plays_90d             INTEGER NOT NULL DEFAULT 0,
            plays_180d            INTEGER NOT NULL DEFAULT 0,
            plays_1y              INTEGER NOT NULL DEFAULT 0,
            plays_2y              INTEGER NOT NULL DEFAULT 0,
            plays_5y              INTEGER NOT NULL DEFAULT 0,
            plays_7d_prev         INTEGER NOT NULL DEFAULT 0,
            plays_30d_prev        INTEGER NOT NULL DEFAULT 0,
            plays_90d_prev        INTEGER NOT NULL DEFAULT 0,
            plays_180d_prev       INTEGER NOT NULL DEFAULT 0,
            plays_1y_prev         INTEGER NOT NULL DEFAULT 0,
            plays_7d_delta        INTEGER NOT NULL DEFAULT 0,
            plays_30d_delta       INTEGER NOT NULL DEFAULT 0,
            plays_90d_delta       INTEGER NOT NULL DEFAULT 0,
            plays_180d_delta      INTEGER NOT NULL DEFAULT 0,
            plays_1y_delta        INTEGER NOT NULL DEFAULT 0,
            rank_all_time         INTEGER,
            rank_7d               INTEGER,
            rank_30d              INTEGER,
            rank_90d              INTEGER,
            rank_180d             INTEGER,
            rank_1y               INTEGER,
            rank_7d_delta         INTEGER,
            rank_30d_delta        INTEGER,
            rank_90d_delta        INTEGER,
            first_heard           TIMESTAMPTZ,
            last_heard            TIMESTAMPTZ,
            days_since_last_heard FLOAT,
            unique_tracks         INTEGER NOT NULL DEFAULT 0,
            longest_streak_days   INTEGER NOT NULL DEFAULT 0,
            current_streak_days   INTEGER NOT NULL DEFAULT 0,
            peak_week_date        DATE,
            peak_week_plays       INTEGER,
            refreshed_at          TIMESTAMPTZ,
            PRIMARY KEY (album, artist)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS track_stats (
            track                 VARCHAR NOT NULL,
            artist                VARCHAR NOT NULL,
            total_plays           INTEGER NOT NULL DEFAULT 0,
            plays_7d              INTEGER NOT NULL DEFAULT 0,
            plays_30d             INTEGER NOT NULL DEFAULT 0,
            plays_90d             INTEGER NOT NULL DEFAULT 0,
            plays_180d            INTEGER NOT NULL DEFAULT 0,
            plays_1y              INTEGER NOT NULL DEFAULT 0,
            plays_2y              INTEGER NOT NULL DEFAULT 0,
            plays_5y              INTEGER NOT NULL DEFAULT 0,
            plays_7d_prev         INTEGER NOT NULL DEFAULT 0,
            plays_30d_prev        INTEGER NOT NULL DEFAULT 0,
            plays_90d_prev        INTEGER NOT NULL DEFAULT 0,
            plays_180d_prev       INTEGER NOT NULL DEFAULT 0,
            plays_1y_prev         INTEGER NOT NULL DEFAULT 0,
            plays_7d_delta        INTEGER NOT NULL DEFAULT 0,
            plays_30d_delta       INTEGER NOT NULL DEFAULT 0,
            plays_90d_delta       INTEGER NOT NULL DEFAULT 0,
            plays_180d_delta      INTEGER NOT NULL DEFAULT 0,
            plays_1y_delta        INTEGER NOT NULL DEFAULT 0,
            rank_all_time         INTEGER,
            rank_7d               INTEGER,
            rank_30d              INTEGER,
            rank_90d              INTEGER,
            rank_180d             INTEGER,
            rank_1y               INTEGER,
            rank_7d_delta         INTEGER,
            rank_30d_delta        INTEGER,
            rank_90d_delta        INTEGER,
            first_heard           TIMESTAMPTZ,
            last_heard            TIMESTAMPTZ,
            days_since_last_heard FLOAT,
            longest_streak_days   INTEGER NOT NULL DEFAULT 0,
            current_streak_days   INTEGER NOT NULL DEFAULT 0,
            peak_week_date        DATE,
            peak_week_plays       INTEGER,
            refreshed_at          TIMESTAMPTZ,
            PRIMARY KEY (track, artist)
        )
    """)

    # -------------------------------------------------------------------------
    # User API tables — persists analytics page configuration
    # -------------------------------------------------------------------------
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_dashboards (
            dashboard_id  VARCHAR PRIMARY KEY DEFAULT gen_random_uuid(),
            name          VARCHAR NOT NULL,
            created_at    TIMESTAMPTZ DEFAULT now(),
            updated_at    TIMESTAMPTZ DEFAULT now()
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS dashboard_charts (
            chart_id       VARCHAR PRIMARY KEY DEFAULT gen_random_uuid(),
            dashboard_id   VARCHAR NOT NULL REFERENCES user_dashboards(dashboard_id),
            predefined_id  VARCHAR,
            is_custom      BOOLEAN NOT NULL DEFAULT FALSE,
            chart_type     VARCHAR NOT NULL,
            title          VARCHAR,
            metric         VARCHAR,
            timespan       VARCHAR,
            top_n          INTEGER,
            filters        JSON,
            visible        BOOLEAN NOT NULL DEFAULT TRUE,
            sort_order     INTEGER NOT NULL DEFAULT 0
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS explore_layouts (
            layout_id        VARCHAR PRIMARY KEY DEFAULT gen_random_uuid(),
            entity_type      VARCHAR NOT NULL CHECK (entity_type IN ('artist', 'album', 'track')),
            predefined_id    VARCHAR,
            is_custom        BOOLEAN NOT NULL DEFAULT FALSE,
            pinned           BOOLEAN NOT NULL DEFAULT FALSE,
            chart_type       VARCHAR NOT NULL,
            metric           VARCHAR,
            default_timespan VARCHAR,
            visible          BOOLEAN NOT NULL DEFAULT TRUE,
            sort_order       INTEGER NOT NULL DEFAULT 0
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_reports (
            report_id    VARCHAR PRIMARY KEY DEFAULT gen_random_uuid(),
            name         VARCHAR NOT NULL,
            entity_type  VARCHAR NOT NULL CHECK (entity_type IN ('artist', 'album', 'track')),
            columns      JSON,
            filters      JSON,
            sort         JSON,
            set_id       VARCHAR,
            created_at   TIMESTAMPTZ DEFAULT now()
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_sets (
            set_id       VARCHAR PRIMARY KEY DEFAULT gen_random_uuid(),
            name         VARCHAR NOT NULL,
            entity_type  VARCHAR NOT NULL CHECK (entity_type IN ('artist', 'album', 'track')),
            created_at   TIMESTAMPTZ DEFAULT now()
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS set_members (
            set_id       VARCHAR NOT NULL REFERENCES user_sets(set_id),
            entity_id    VARCHAR NOT NULL,
            display_name VARCHAR NOT NULL,
            added_at     TIMESTAMPTZ DEFAULT now(),
            PRIMARY KEY (set_id, entity_id)
        )
    """)

    conn.close()
    print(f"Schema created at {DB_PATH}")


if __name__ == "__main__":
    create_schema()
