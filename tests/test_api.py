"""
API endpoint tests — HTTP smoke tests + SSE streaming correctness.
No real Anthropic API calls. Agent is mocked via conftest fixtures.
"""
import json
import pytest
from unittest.mock import patch, AsyncMock


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# Songs (guitar log)
# ---------------------------------------------------------------------------

def test_list_songs(client):
    r = client.get("/songs")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_add_and_delete_song(client):
    payload = {
        "title": "Stairway to Heaven",
        "artist": "Led Zeppelin",
        "difficulty": 4,
        "status": "want_to_learn",
        "notes": "Long intro fingerpicking",
    }
    r = client.post("/songs", json=payload)
    assert r.status_code == 201
    song = r.json()
    assert song["title"] == "Stairway to Heaven"
    song_id = song["song_id"]

    # Verify it appears in the list
    r = client.get("/songs")
    ids = [s["song_id"] for s in r.json()]
    assert song_id in ids

    # Clean up
    r = client.delete(f"/songs/{song_id}")
    assert r.status_code == 204


# ---------------------------------------------------------------------------
# Playlists
# ---------------------------------------------------------------------------

def test_list_playlists(client):
    r = client.get("/playlists")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


# ---------------------------------------------------------------------------
# Agent — playlist SSE stream
# ---------------------------------------------------------------------------

def _parse_sse(raw: str) -> list[dict]:
    """Parse a raw SSE response body into a list of {event, data} dicts."""
    events = []
    current = {}
    for line in raw.splitlines():
        if line.startswith("event:"):
            current["event"] = line[len("event:"):].strip()
        elif line.startswith("data:"):
            current["data"] = line[len("data:"):].strip()
        elif line == "" and current:
            events.append(current)
            current = {}
    if current:
        events.append(current)
    return events


def test_agent_playlist_streams_playlist_event(client, mock_playlist_agent):
    with patch("backend.agent.bridge_client.stream_agent", new=mock_playlist_agent):
        r = client.post(
            "/agent/playlist",
            json={"prompt": "Focus music for work", "playlist_id": None},
            headers={"Accept": "text/event-stream"},
        )

    assert r.status_code == 200
    events = _parse_sse(r.text)

    event_names = [e.get("event") for e in events]
    assert "playlist" in event_names, f"No playlist event found. Events: {event_names}"
    assert "done" in event_names

    # Validate playlist payload
    playlist_event = next(e for e in events if e.get("event") == "playlist")
    data = json.loads(playlist_event["data"])
    assert "name" in data
    assert "tracks" in data
    assert len(data["tracks"]) > 0
    assert "shortcuts_url" in data
    assert data["shortcuts_url"].startswith("shortcuts://")


def test_agent_playlist_streams_text_chunks(client, mock_playlist_agent):
    with patch("backend.agent.bridge_client.stream_agent", new=mock_playlist_agent):
        r = client.post(
            "/agent/playlist",
            json={"prompt": "Focus music for work", "playlist_id": None},
        )

    # Default SSE events (no event: field) carry streamed text
    text_chunks = [e["data"] for e in _parse_sse(r.text) if "event" not in e]
    combined = "".join(text_chunks)
    assert len(combined) > 0


def test_agent_playlist_always_ends_with_done(client, mock_playlist_agent):
    with patch("backend.agent.bridge_client.stream_agent", new=mock_playlist_agent):
        r = client.post(
            "/agent/playlist",
            json={"prompt": "Anything", "playlist_id": None},
        )

    events = _parse_sse(r.text)
    assert events[-1].get("event") == "done"


# ---------------------------------------------------------------------------
# Agent — chat SSE stream
# ---------------------------------------------------------------------------

def test_agent_chat_streams_text(client, mock_chat_agent):
    with patch("backend.agent.bridge_client.stream_agent", new=mock_chat_agent):
        r = client.post(
            "/agent/chat",
            json={"message": "What have I been listening to?", "thread_id": "test-thread-1"},
        )

    assert r.status_code == 200
    text_chunks = [e["data"] for e in _parse_sse(r.text) if "event" not in e]
    combined = "".join(text_chunks)
    assert "Beatles" in combined


def test_agent_chat_always_ends_with_done(client, mock_chat_agent):
    with patch("backend.agent.bridge_client.stream_agent", new=mock_chat_agent):
        r = client.post(
            "/agent/chat",
            json={"message": "Hello", "thread_id": "test-thread-2"},
        )

    events = _parse_sse(r.text)
    assert events[-1].get("event") == "done"
