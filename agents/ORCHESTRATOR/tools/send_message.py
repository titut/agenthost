"""ORCHESTRATOR tool: send a task to a running specialist agent."""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path

import httpx

from agenthost.tools import current_thread_id

# Load the sibling helper module by absolute path so imports work regardless of
# the current working directory.
_TOOLS_DIR = Path(__file__).resolve().parent
_helpers_spec = importlib.util.spec_from_file_location(
    "_helpers", str(_TOOLS_DIR / "_helpers.py")
)
_helpers = importlib.util.module_from_spec(_helpers_spec)
_helpers_spec.loader.exec_module(_helpers)

_SEND_RETRIES = _helpers._SEND_RETRIES
logger = _helpers.logger
lookup_active_agent = _helpers.lookup_active_agent


async def _send_once(host: str, port: int, message: str, thread_id: str) -> dict:
    """Send one message to an agent and return its response."""
    chat_url = f"http://{host}:{port}/chat"
    health_url = f"http://{host}:{port}/health"

    # Health check first
    try:
        async with httpx.AsyncClient() as health_client:
            health_resp = await health_client.get(health_url, timeout=2.0)
            if health_resp.status_code != 200:
                raise RuntimeError("Health check failed")
    except Exception as exc:
        logger.warning(
            "ORCHESTRATOR send_message: health check failed for %s:%d: %s",
            host,
            port,
            exc,
        )
        return {
            "error": f"Agent at {host}:{port} is not responding. "
            "It may have crashed or not finished starting. Please check the agent process."
        }

    payload = {"message": message, "thread_id": thread_id}

    try:
        response_text = ""
        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST", chat_url, json=payload, timeout=1800.0
            ) as resp:
                resp.raise_for_status()
                current_event = None
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line:
                        current_event = None
                        continue
                    if line.startswith("event:"):
                        current_event = line.split(":", 1)[1].strip()
                        continue
                    if line.startswith("data:") and current_event == "message":
                        data = json.loads(line.split(":", 1)[1].strip())
                        if data.get("type") == "content":
                            response_text += data["data"]

        logger.info(
            "ORCHESTRATOR send_message: received response from '%s:%d' (%d chars)",
            host,
            port,
            len(response_text),
        )
        return {"response": response_text}
    except Exception as exc:
        logger.exception(
            "ORCHESTRATOR send_message: communication with %s:%d failed: %s",
            host,
            port,
            exc,
        )
        return {"error": f"Communication with agent failed: {exc}"}


async def send_message(agent_name: str, message: str) -> str:
    """Send a message to a running agent by name and return its full response.

    The agent must already be running (started by a supervisor or manually). Use
    `list_agents()` to discover running agents and their capabilities. The same
    thread_id as the ORCHESTRATOR's current conversation is used so that the
    specialist agent shares memory/context with this conversation.

    This function is async so that cancellation (e.g. Discord !stop) propagates
    to the downstream agent and closes the HTTP connection.
    """
    logger.info("ORCHESTRATOR send_message(agent_name='%s')", agent_name)

    entry = lookup_active_agent(agent_name)
    if entry is None:
        logger.error(
            "ORCHESTRATOR send_message: no active agent named '%s'", agent_name
        )
        return json.dumps(
            {
                "error": f"No active agent named '{agent_name}'. "
                "Use list_agents() to see running agents, then start it manually."
            }
        )

    host = str(entry.get("host", "127.0.0.1"))
    port = int(entry["port"])
    thread_id = current_thread_id.get()
    if thread_id is None:
        # Fallback: create a stable thread id based on the agent name. This should
        # not happen in normal operation because the tool runner always sets the
        # context variable.
        thread_id = f"orchestrator-{agent_name}"

    last_error: dict | None = None
    for attempt in range(_SEND_RETRIES + 1):
        result = await _send_once(host, port, message, thread_id)
        if "error" not in result:
            return json.dumps(
                {
                    "agent_name": agent_name,
                    "host": host,
                    "port": port,
                    "thread_id": thread_id,
                    "response": result["response"],
                }
            )
        last_error = result
        if attempt < _SEND_RETRIES:
            logger.info(
                "ORCHESTRATOR send_message: retrying %s:%d (attempt %d/%d)",
                host,
                port,
                attempt + 1,
                _SEND_RETRIES,
            )
            await asyncio.sleep(0.5)

    return json.dumps(
        {
            "agent_name": agent_name,
            "error": last_error["error"] if last_error else "Unknown error",
        }
    )
