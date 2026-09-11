#!/usr/bin/env python3
"""
Host-side Claude bridge — replaces every direct Anthropic API call in the
backend with headless `claude` running under the subscription OAuth token.

Why this exists (PLAN.md §3 / Track A): the backend runs in a deliberately slim
Docker image with no `claude` CLI and no OAuth token. That token is what makes
these calls bill the Claude subscription instead of a metered ANTHROPIC_API_KEY.
So the CLI has to run on the host, and the container reaches it over the Docker
bridge network.

Unlike JobApplicationTracker's cron-polled queue, this is a *persistent*
service: two of the three call sites are typed-message chat UIs that stream
token-by-token, and a 2-minute cron tick would be a large UX regression.
Spawning `claude -p` per turn costs ~1-3s of startup instead.

Endpoints
  GET  /health          liveness + whether the OAuth token is loaded
  POST /stream          NDJSON event stream (guitar chat, playlist generation)
  POST /json            single structured JSON reply (analytics action bus)

Every request must carry `X-Bridge-Token`, matched against BRIDGE_TOKEN.
"""
import asyncio
import json
import os
import shutil
import sys
import time
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from backend.agent.prompts import SYSTEM_PROMPT  # noqa: E402

HOST = os.environ.get("BRIDGE_HOST", "172.18.0.1")
PORT = int(os.environ.get("BRIDGE_PORT", "8787"))
BRIDGE_TOKEN = os.environ.get("BRIDGE_TOKEN", "")
MODEL = os.environ.get("BRIDGE_MODEL", "sonnet")
# A tool-heavy playlist build legitimately runs for minutes; cap it so a wedged
# subprocess can't pin a Pi core forever.
TIMEOUT = int(os.environ.get("BRIDGE_TIMEOUT", "600"))
# The Pi has limited RAM and each `claude` is a Node process. Serialize beyond
# this rather than thrashing swap.
MAX_CONCURRENT = int(os.environ.get("BRIDGE_MAX_CONCURRENT", "2"))
# asyncio's StreamReader defaults to a 64KB line limit, and stream-json emits one
# JSON object per line — a query_database result table or a 250-track
# build_playlist payload blows straight past that and raises
# "Separator is found, but chunk is longer than limit".
STREAM_LIMIT = 32 * 1024 * 1024

SESSION_FILE = Path(os.environ.get("BRIDGE_SESSION_FILE", REPO / "data" / "bridge_sessions.json"))
MCP_CONFIG = REPO / "scripts" / "mcp_tastemaker.json"

TOOL_PREFIX = "mcp__tastemaker__"
CHAT_TOOLS = [
    f"{TOOL_PREFIX}query_database",
    f"{TOOL_PREFIX}track_similar_lookup",
    f"{TOOL_PREFIX}artist_top_tracks",
    f"{TOOL_PREFIX}discover_tracks",
]
PLAYLIST_TOOLS = CHAT_TOOLS + [f"{TOOL_PREFIX}build_playlist"]

# The CLI's own deferred-tool lookup. It's an implementation detail of tool
# loading, not something the user asked for — don't surface it as a tool call.
INTERNAL_TOOLS = {"ToolSearch"}

app = FastAPI(title="tastemaker-claude-bridge")
_semaphore = asyncio.Semaphore(MAX_CONCURRENT)


# ---------------------------------------------------------------------------
# Session mapping: our thread_id -> claude session id, so multi-turn chat can
# --resume. Replaces the LangGraph AsyncSqliteSaver checkpointer.
# ---------------------------------------------------------------------------
def _load_sessions() -> dict:
    try:
        return json.loads(SESSION_FILE.read_text())
    except Exception:
        return {}


def _save_session(thread_id: str, session_id: str):
    if not thread_id or not session_id:
        return
    sessions = _load_sessions()
    sessions[thread_id] = {"session_id": session_id, "updated_at": time.time()}
    # Keep the file from growing without bound; 500 threads is far more than
    # this single-user app will ever have open.
    if len(sessions) > 500:
        keep = sorted(sessions.items(), key=lambda kv: kv[1].get("updated_at", 0))[-500:]
        sessions = dict(keep)
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SESSION_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(sessions))
    tmp.replace(SESSION_FILE)


def _claude_env() -> dict:
    env = os.environ.copy()
    # Critical: an inherited ANTHROPIC_API_KEY would silently route back to
    # metered API billing — the exact thing this whole service exists to avoid.
    env.pop("ANTHROPIC_API_KEY", None)
    token = _read_token()
    if token:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = token
    # systemd and cron both start with a minimal PATH that lacks npm's global
    # bin dir, so a bare `claude` wouldn't resolve.
    env["PATH"] = f"/home/mambo/.npm-global/bin:{env.get('PATH', '')}"
    return env


def _read_token() -> str | None:
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return os.environ["CLAUDE_CODE_OAUTH_TOKEN"]
    token_file = Path(os.environ.get("CLAUDE_TOKEN_FILE", "/home/mambo/.claude_token"))
    try:
        for line in token_file.read_text().splitlines():
            line = line.strip()
            if "CLAUDE_CODE_OAUTH_TOKEN" in line:
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return None


def _auth(token: str | None):
    if not BRIDGE_TOKEN:
        raise HTTPException(500, "BRIDGE_TOKEN not configured on the bridge")
    if token != BRIDGE_TOKEN:
        raise HTTPException(401, "bad bridge token")


def _base_argv(system_prompt: str, allowed: list[str]) -> list[str]:
    return [
        "claude",
        "-p",
        "--model", MODEL,
        # Replaces Claude Code's default coding-agent system prompt (~24k
        # tokens) with the taste-assistant one.
        "--system-prompt", system_prompt,
        "--mcp-config", str(MCP_CONFIG),
        # Don't inherit the user's personal MCP servers into an app request.
        "--strict-mcp-config",
        "--allowedTools", ",".join(allowed),
        "--disable-slash-commands",
    ]


# ---------------------------------------------------------------------------
# Streaming (guitar chat + playlist)
# ---------------------------------------------------------------------------
class StreamRequest(BaseModel):
    prompt: str
    thread_id: str | None = None
    mode: str = "chat"  # "chat" | "playlist"
    resume: bool = True


async def _run_stream(req: StreamRequest):
    """Yield normalized NDJSON events; the container re-emits them as SSE."""
    allowed = PLAYLIST_TOOLS if req.mode == "playlist" else CHAT_TOOLS
    argv = _base_argv(SYSTEM_PROMPT, allowed) + [
        "--output-format", "stream-json",
        "--include-partial-messages",
        "--verbose",
    ]

    sessions = _load_sessions()
    prior = sessions.get(req.thread_id or "", {}).get("session_id")
    if req.resume and prior:
        argv += ["--resume", prior]
    argv += [req.prompt]

    async with _semaphore:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(REPO),
            env=_claude_env(),
            limit=STREAM_LIMIT,
        )
        deadline = time.monotonic() + TIMEOUT
        saw_result = False
        # tool_use_id -> tool name. Tool results reference the id only, but the
        # playlist endpoint needs to know which result came from build_playlist.
        tool_names: dict[str, str] = {}
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    yield {"type": "error", "message": f"timed out after {TIMEOUT}s"}
                    break
                try:
                    raw = await asyncio.wait_for(proc.stdout.readline(), timeout=remaining)
                except asyncio.TimeoutError:
                    yield {"type": "error", "message": f"timed out after {TIMEOUT}s"}
                    break
                if not raw:
                    break

                try:
                    evt = json.loads(raw.decode("utf-8", "replace").strip())
                except json.JSONDecodeError:
                    continue

                for out in _translate(evt, req.thread_id, tool_names):
                    if out["type"] == "result":
                        saw_result = True
                    yield out
        finally:
            if proc.returncode is None:
                proc.kill()
            stderr = b""
            try:
                stderr = await asyncio.wait_for(proc.stderr.read(), timeout=5)
            except Exception:
                pass
            await proc.wait()

        if not saw_result:
            msg = stderr.decode("utf-8", "replace").strip()[-500:] or "claude exited without a result"
            # A subscription cap is not a code failure — label it so the caller
            # (and the user) can tell the two apart.
            low = msg.lower()
            if "usage limit" in low or "session limit" in low or "rate limit" in low:
                yield {"type": "error", "message": f"Claude subscription limit reached: {msg}", "retryable": True}
            else:
                yield {"type": "error", "message": msg}

    yield {"type": "done"}


def _translate(evt: dict, thread_id: str | None, tool_names: dict[str, str]):
    """claude stream-json -> the small event vocabulary the backend consumes."""
    etype = evt.get("type")

    if etype == "stream_event":
        inner = evt.get("event", {})
        itype = inner.get("type")
        if itype == "content_block_delta":
            delta = inner.get("delta", {})
            if delta.get("type") == "text_delta" and delta.get("text"):
                yield {"type": "text", "text": delta["text"]}
        elif itype == "content_block_start":
            block = inner.get("content_block", {})
            if block.get("type") == "tool_use":
                name = block.get("name", "tool")
                if block.get("id"):
                    tool_names[block["id"]] = name
                if name not in INTERNAL_TOOLS:
                    yield {"type": "tool_start", "name": _display(name)}
        return

    if etype == "assistant":
        # The complete tool_use block, with fully-assembled input. content_block_start
        # fires earlier but carries an empty input (it arrives as input_json_delta
        # chunks), so provenance capture — e.g. the SQL behind a playlist — has to
        # read it here.
        for block in evt.get("message", {}).get("content") or []:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            name = block.get("name", "tool")
            if block.get("id"):
                tool_names[block["id"]] = name
            if name not in INTERNAL_TOOLS:
                yield {
                    "type": "tool_input",
                    "name": _display(name),
                    "input": block.get("input") or {},
                }
        return

    if etype == "user":
        # Tool results come back as a synthetic user turn.
        content = evt.get("message", {}).get("content") or []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                name = tool_names.get(block.get("tool_use_id", ""), "tool")
                if name in INTERNAL_TOOLS:
                    continue
                yield {
                    "type": "tool_end",
                    "name": _display(name),
                    "result": _result_text(block),
                }
        return

    if etype == "result":
        if evt.get("is_error"):
            yield {"type": "error", "message": str(evt.get("result") or "claude reported an error")}
            return
        session_id = evt.get("session_id")
        if session_id and thread_id:
            _save_session(thread_id, session_id)
        yield {
            "type": "result",
            "text": evt.get("result") or "",
            "session_id": session_id,
        }


def _display(tool_name: str) -> str:
    return tool_name[len(TOOL_PREFIX):] if tool_name.startswith(TOOL_PREFIX) else tool_name


def _result_text(block: dict) -> str:
    content = block.get("content")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        parts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
        text = "\n".join(p for p in parts if p)
    else:
        return ""
    return _unwrap_mcp(text)


def _unwrap_mcp(text: str) -> str:
    """
    MCP wraps a tool's return value as {"result": <value>}. Strip that envelope
    so callers see exactly what the tool function returned — build_playlist's
    consumer expects its own JSON, not a wrapped copy of it.
    """
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text
    if isinstance(parsed, dict) and set(parsed) == {"result"}:
        inner = parsed["result"]
        return inner if isinstance(inner, str) else json.dumps(inner)
    return text


@app.post("/stream")
async def stream(req: StreamRequest, x_bridge_token: str | None = Header(default=None)):
    _auth(x_bridge_token)

    async def gen():
        async for evt in _run_stream(req):
            yield json.dumps(evt) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")


# ---------------------------------------------------------------------------
# Single-shot structured JSON (analytics action bus)
# ---------------------------------------------------------------------------
class JsonRequest(BaseModel):
    prompt: str
    system_prompt: str
    json_schema: dict | None = None


@app.post("/json")
async def json_call(req: JsonRequest, x_bridge_token: str | None = Header(default=None)):
    _auth(x_bridge_token)

    # No tools: the action bus is a single-turn intent -> UI-actions translation.
    argv = _base_argv(req.system_prompt, []) + ["--output-format", "json"]
    if req.json_schema:
        argv += ["--json-schema", json.dumps(req.json_schema)]
    argv += [req.prompt]

    async with _semaphore:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(REPO),
            env=_claude_env(),
            limit=STREAM_LIMIT,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=TIMEOUT)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise HTTPException(504, f"claude timed out after {TIMEOUT}s")

    if proc.returncode != 0:
        raise HTTPException(502, stderr.decode("utf-8", "replace").strip()[-500:] or "claude failed")

    try:
        envelope = json.loads(stdout.decode("utf-8", "replace"))
    except json.JSONDecodeError:
        raise HTTPException(502, "claude returned non-JSON output")

    if envelope.get("is_error"):
        raise HTTPException(502, str(envelope.get("result") or "claude reported an error"))

    return {"result": envelope.get("result", ""), "session_id": envelope.get("session_id")}


@app.get("/health")
def health():
    return {
        "ok": True,
        "claude": shutil.which("claude", path=_claude_env()["PATH"]),
        "token_loaded": bool(_read_token()),
        "model": MODEL,
        "max_concurrent": MAX_CONCURRENT,
    }


if __name__ == "__main__":
    import uvicorn

    if not BRIDGE_TOKEN:
        sys.exit("BRIDGE_TOKEN must be set")
    if not _read_token():
        sys.exit("No CLAUDE_CODE_OAUTH_TOKEN — run `claude setup-token` and save it to ~/.claude_token")
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
