"""
Client for the host-side Claude bridge (`scripts/claude_bridge.py`).

The backend container has no `claude` CLI and no subscription OAuth token, so
every Claude call is forwarded over the Docker bridge network to the host
service that does. This module is the only place that knows the bridge exists.
"""
import json
import os

import httpx

BRIDGE_URL = os.environ.get("CLAUDE_BRIDGE_URL", "http://172.18.0.1:8787")
BRIDGE_TOKEN = os.environ.get("BRIDGE_TOKEN", "")
# Generous: a tool-heavy playlist build legitimately runs for minutes. The
# bridge enforces its own hard timeout, so this is only a backstop.
TIMEOUT = httpx.Timeout(connect=10.0, read=660.0, write=30.0, pool=10.0)


class BridgeError(RuntimeError):
    pass


def _headers() -> dict:
    return {"X-Bridge-Token": BRIDGE_TOKEN, "Content-Type": "application/json"}


async def stream_agent(prompt: str, thread_id: str | None = None, mode: str = "chat"):
    """
    Yield normalized events from the bridge:
      {"type": "text",       "text": str}
      {"type": "tool_start", "name": str}
      {"type": "tool_end",   "name": str, "result": str}
      {"type": "result",     "text": str, "session_id": str}
      {"type": "error",      "message": str, "retryable": bool?}
      {"type": "done"}
    """
    payload = {"prompt": prompt, "thread_id": thread_id, "mode": mode}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            async with client.stream(
                "POST", f"{BRIDGE_URL}/stream", json=payload, headers=_headers()
            ) as resp:
                if resp.status_code != 200:
                    body = (await resp.aread()).decode("utf-8", "replace")[:300]
                    yield {"type": "error", "message": f"bridge {resp.status_code}: {body}"}
                    return
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        continue
    except httpx.ConnectError:
        yield {
            "type": "error",
            "message": (
                "Claude bridge unreachable. On the Pi: "
                "systemctl --user status claude-bridge"
            ),
        }
    except httpx.HTTPError as e:
        yield {"type": "error", "message": f"bridge transport error: {e}"}


async def call_json(prompt: str, system_prompt: str, json_schema: dict | None = None) -> str:
    """Single-shot structured call. Returns the raw result string."""
    payload = {"prompt": prompt, "system_prompt": system_prompt, "json_schema": json_schema}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.post(f"{BRIDGE_URL}/json", json=payload, headers=_headers())
    except httpx.ConnectError:
        raise BridgeError(
            "Claude bridge unreachable. On the Pi: systemctl --user status claude-bridge"
        )
    except httpx.HTTPError as e:
        raise BridgeError(f"bridge transport error: {e}")

    if resp.status_code != 200:
        raise BridgeError(f"bridge {resp.status_code}: {resp.text[:300]}")
    return resp.json().get("result", "")


async def health() -> dict:
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
        resp = await client.get(f"{BRIDGE_URL}/health", headers=_headers())
        resp.raise_for_status()
        return resp.json()
