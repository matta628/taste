"""
Shared fixtures for the Tastemaker test suite.

All tests run against a temporary DuckDB file — never the real tastemaker.db.
The LangGraph agent is mocked so tests run without hitting the Anthropic API.
"""
import json
import os
import pytest
import duckdb
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

# ---------------------------------------------------------------------------
# Test database
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def test_db_path(tmp_path_factory) -> Path:
    """Minimal DuckDB with just enough data for tool and API tests."""
    db_path = tmp_path_factory.mktemp("db") / "test.db"
    conn = duckdb.connect(str(db_path))

    conn.execute("""
        CREATE TABLE raw_scrobbles (
            track VARCHAR NOT NULL,
            artist VARCHAR NOT NULL,
            album VARCHAR,
            scrobbled_at TIMESTAMPTZ NOT NULL,
            mbid VARCHAR,
            PRIMARY KEY (artist, track, scrobbled_at)
        )
    """)
    conn.execute("""
        INSERT INTO raw_scrobbles VALUES
            ('Blackbird', 'The Beatles', 'White Album', '2024-01-01 10:00:00+00', NULL),
            ('Yesterday',  'The Beatles', 'Help!',       '2024-01-02 11:00:00+00', NULL),
            ('Wish You Were Here', 'Pink Floyd', 'WYWH', '2024-01-03 12:00:00+00', NULL)
    """)

    conn.execute("""
        CREATE TABLE artist_tags (
            artist_name VARCHAR NOT NULL,
            tag VARCHAR NOT NULL,
            weight INTEGER NOT NULL,
            PRIMARY KEY (artist_name, tag)
        )
    """)
    conn.execute("""
        INSERT INTO artist_tags VALUES
            ('The Beatles', 'rock', 90),
            ('The Beatles', 'classic rock', 85),
            ('Pink Floyd',  'progressive rock', 95)
    """)

    conn.execute("""
        CREATE TABLE guitar_songs (
            song_id VARCHAR PRIMARY KEY,
            title VARCHAR NOT NULL,
            artist VARCHAR NOT NULL,
            part VARCHAR,
            difficulty INTEGER,
            status VARCHAR,
            notes TEXT,
            date_started DATE,
            created_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now()
        )
    """)
    conn.execute("""
        INSERT INTO guitar_songs VALUES
            ('test-id-1', 'Blackbird', 'The Beatles', 'tabs', 3, 'learning',
             'Fingerpicking pattern is tricky', '2024-01-01', now(), now())
    """)

    conn.execute("""
        CREATE TABLE playlists (
            playlist_id VARCHAR PRIMARY KEY,
            name VARCHAR NOT NULL,
            prompt VARCHAR NOT NULL,
            tracks JSON NOT NULL,
            shortcuts_url VARCHAR NOT NULL,
            thoughts TEXT,
            queries JSON,
            created_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now()
        )
    """)

    conn.execute("""
        CREATE TABLE artist_similar (
            artist_name VARCHAR NOT NULL,
            similar_artist VARCHAR NOT NULL,
            similarity FLOAT NOT NULL,
            PRIMARY KEY (artist_name, similar_artist)
        )
    """)

    conn.execute("""
        CREATE TABLE track_tags (
            track VARCHAR NOT NULL,
            artist VARCHAR NOT NULL,
            tag VARCHAR NOT NULL,
            weight INTEGER NOT NULL,
            PRIMARY KEY (track, artist, tag)
        )
    """)

    conn.close()
    return db_path


# ---------------------------------------------------------------------------
# FastAPI test client pointing at the test DB
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def app(test_db_path):
    """FastAPI app with DB_PATH patched to the test database."""
    import backend.db.schema as schema_module
    schema_module.DB_PATH = test_db_path

    import backend.agent.tools as tools_module
    tools_module.DB_PATH = test_db_path

    # Patch the db() helper used inside main.py endpoints
    import backend.main as main_module
    original_db = main_module.db

    def test_db():
        return duckdb.connect(str(test_db_path))

    main_module.db = test_db

    from backend.main import app
    yield app

    main_module.db = original_db


@pytest.fixture(scope="session")
def client(app):
    from fastapi.testclient import TestClient
    return TestClient(app)


# ---------------------------------------------------------------------------
# Mock Claude bridge
#
# These mirror the event vocabulary scripts/claude_bridge.py emits, so the
# endpoints are exercised without spawning a real `claude` subprocess.
# ---------------------------------------------------------------------------

def make_text_chunk(text: str):
    return {"type": "text", "text": text}


def make_tool_start(name: str, tool_input: dict | None = None):
    return {"type": "tool_input", "name": name, "input": tool_input or {}}


def make_tool_end(name: str, output_content: str):
    return {"type": "tool_end", "name": name, "result": output_content}


def make_error(message: str):
    return {"type": "error", "message": message}


def mock_stream_agent(events: list):
    """Build a stand-in for bridge_client.stream_agent yielding scripted events."""

    async def _stream(prompt, thread_id=None, mode="chat"):
        for event in events:
            yield event
        yield {"type": "done"}

    return _stream


@pytest.fixture
def mock_playlist_agent():
    """Bridge stream that calls build_playlist and returns a valid playlist."""
    from backend.agent.tools import build_playlist

    playlist_output = build_playlist.invoke({
        "name": "Test Focus Flow",
        "tracks": [
            {"title": "Blackbird", "artist": "The Beatles"},
            {"title": "Wish You Were Here", "artist": "Pink Floyd"},
        ],
    })

    events = [
        make_text_chunk("Let me check your listening history..."),
        make_tool_start("query_database", {"sql": "SELECT artist FROM raw_scrobbles"}),
        make_tool_end("query_database", "| artist | plays |\n|---|---|\n| The Beatles | 50 |"),
        make_text_chunk("Based on your taste, here's a focus playlist."),
        make_tool_start("build_playlist"),
        make_tool_end("build_playlist", playlist_output),
    ]
    return mock_stream_agent(events)


@pytest.fixture
def mock_chat_agent():
    """Bridge stream with a simple text response."""
    events = [
        make_text_chunk("You've been listening to "),
        make_text_chunk("The Beatles a lot lately."),
    ]
    return mock_stream_agent(events)
