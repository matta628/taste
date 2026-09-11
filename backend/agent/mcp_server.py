"""
MCP stdio server exposing the Tastemaker domain tools to headless `claude`.

Spawned by the host-side bridge (`scripts/claude_bridge.py`) via the CLI's
`--mcp-config`. The tool bodies and their prose descriptions still live in
`backend/agent/tools.py` — the wrappers below only adapt those to MCP, so the
schema stays in one place rather than being duplicated per transport.

Runs on the HOST, not in the backend container: the container image has no
`claude` CLI and no subscription token. Every tool reads DuckDB read-only, so
it coexists safely with the container holding the same database file open.
"""
import asyncio
import logging

from mcp.server.mcpserver import MCPServer

from backend.agent import tools as t

# stderr only — stdout is the JSON-RPC channel and a stray print corrupts it.
logging.basicConfig(level=logging.INFO, format="[mcp] %(message)s")
log = logging.getLogger(__name__)

server = MCPServer("tastemaker")


async def _run(tool, **kwargs) -> str:
    """Tool bodies are sync and do blocking I/O (DuckDB, Last.fm HTTP)."""
    log.info("call %s %s", tool.name, {k: str(v)[:80] for k, v in kwargs.items()})
    return str(await asyncio.to_thread(tool.invoke, kwargs))


async def query_database(sql: str) -> str:
    return await _run(t.query_database, sql=sql)


async def build_playlist(name: str, tracks: list[dict]) -> str:
    return await _run(t.build_playlist, name=name, tracks=tracks)


async def track_similar_lookup(track: str, artist: str, limit: int = 10) -> str:
    return await _run(t.track_similar_lookup, track=track, artist=artist, limit=limit)


async def artist_top_tracks(artist: str, limit: int = 10) -> str:
    return await _run(t.artist_top_tracks, artist=artist, limit=limit)


async def discover_tracks(genre_tag: str, limit: int = 30) -> str:
    return await _run(t.discover_tracks, genre_tag=genre_tag, limit=limit)


# Descriptions come from the LangChain tool objects so the docstrings in
# tools.py remain the single source of truth for what the model is told.
for _fn, _tool in (
    (query_database, t.query_database),
    (build_playlist, t.build_playlist),
    (track_similar_lookup, t.track_similar_lookup),
    (artist_top_tracks, t.artist_top_tracks),
    (discover_tracks, t.discover_tracks),
):
    server.add_tool(_fn, name=_tool.name, description=_tool.description)


if __name__ == "__main__":
    server.run(transport="stdio")
