"""ORCHESTRATOR tools: discover pre-spawned agents, send messages, and plan tracking."""

from __future__ import annotations

import asyncio
import calendar
import json
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import httpx

from agenthost.logger import setup_logging
from agenthost.tools import current_thread_id

logger = setup_logging("agenthost.orchestrator")

# How many times to retry a failed agent request before giving up.
_SEND_RETRIES = 1


def _run_agenthost_list() -> list[dict[str, object]]:
    """Run `agenthost list` and parse its output into a list of records.

    Returns records like:
    [{"name": "RESEARCHER", "host": "127.0.0.1", "port": 8000, "pid": 12345, "path": "..."}]
    """
    logger.debug("Running `agenthost list`")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "agenthost", "list"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10.0,
        )
    except Exception as exc:
        logger.warning("`agenthost list` failed: %s", exc)
        return []

    lines = result.stdout.strip().splitlines()
    agents: list[dict[str, object]] = []
    # Skip header and separator lines.
    for line in lines[2:]:
        parts = line.split()
        if len(parts) >= 5:
            try:
                agents.append(
                    {
                        "name": parts[0],
                        "host": parts[1],
                        "port": int(parts[2]),
                        "pid": int(parts[3]),
                        "path": " ".join(parts[4:]),
                    }
                )
            except ValueError:
                continue
    logger.debug("`agenthost list` returned %d active agents", len(agents))
    return agents


def _run_agenthost_agent_list() -> dict[str, Path]:
    """Run `agenthost agent list` and parse it into an alias -> path map.

    Only returns entries whose path exists and contains WHOAMI.md.
    """
    logger.debug("Running `agenthost agent list`")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "agenthost", "agent", "list"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10.0,
        )
    except Exception as exc:
        logger.warning("`agenthost agent list` failed: %s", exc)
        return {}

    agents: dict[str, Path] = {}
    for line in result.stdout.strip().splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        alias = parts[0]
        path_str = " ".join(parts[1:])
        try:
            path = Path(path_str).expanduser().resolve()
        except Exception:
            continue
        if path.is_dir() and (path / "WHOAMI.md").exists():
            agents[alias] = path
    return agents


def _get_orchestrator_path() -> Path:
    """Resolve ORCHESTRATOR's own folder path from `agenthost agent list`."""
    agents = _run_agenthost_agent_list()
    if "ORCHESTRATOR" in agents:
        return agents["ORCHESTRATOR"]
    raise RuntimeError(
        "ORCHESTRATOR is not registered. Run `agenthost agent add ORCHESTRATOR <path>`."
    )


def read_agent_folder(name: str) -> str:
    """Read an agent folder registered in agents.yaml and return its capabilities.

    Returns the agent's persona, tools (name + description), skills,
    model, and temperature.
    """
    agents = _run_agenthost_agent_list()
    agent_dir = agents.get(name)
    if agent_dir is None:
        return json.dumps({"error": f"Agent not registered: {name}"})

    if not agent_dir.is_dir() or not (agent_dir / "WHOAMI.md").exists():
        return json.dumps({"error": f"Agent folder not found: {agent_dir}"})

    result = {
        "name": name,
        "model": "gpt-4o-mini",
        "temperature": 0.7,
    }

    # Read WHOAMI.md
    whoami = agent_dir / "WHOAMI.md"
    if whoami.exists():
        result["persona"] = whoami.read_text(encoding="utf-8")

    # Read agent.yaml
    yaml_path = agent_dir / "agent.yaml"
    if yaml_path.exists():
        import yaml

        cfg = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        result["model"] = cfg.get("model", result["model"])
        result["temperature"] = cfg.get("temperature", result["temperature"])

    # Discover tools (import and inspect, don't execute)
    tools_dir = agent_dir / "tools"
    result["tools"] = []
    if tools_dir.is_dir():
        for py_file in sorted(tools_dir.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            module_name = f"_orchestrator_inspect_{name}_{py_file.stem}"
            import importlib.util

            spec = importlib.util.spec_from_file_location(module_name, py_file)
            if spec and spec.loader:
                import sys as _sys

                module = importlib.util.module_from_spec(spec)
                _sys.modules[module_name] = module
                spec.loader.exec_module(module)
                import inspect

                for func_name, func in inspect.getmembers(module, inspect.isfunction):
                    if not func_name.startswith("_"):
                        sig = inspect.signature(func)
                        doc = inspect.getdoc(func) or ""
                        result["tools"].append(
                            {
                                "name": func_name,
                                "description": doc,
                                "parameters": list(sig.parameters.keys()),
                            }
                        )

    # List skills
    skills_dir = agent_dir / "skills"
    result["skills"] = (
        sorted(f.name for f in skills_dir.glob("*.md")) if skills_dir.is_dir() else []
    )

    return json.dumps(result, indent=2)


def list_agents() -> str:
    """List all currently running agents discovered via `agenthost list`.

    Agents are expected to be started outside the ORCHESTRATOR (e.g. by a
    supervisor or manually). This tool only reports agents that are currently
    registered and alive.
    """
    logger.info("ORCHESTRATOR list_agents()")
    active = _run_agenthost_list()

    agents_list = []
    for entry in active:
        agents_list.append(
            {
                "name": entry["name"],
                "host": entry["host"],
                "port": entry["port"],
                "pid": entry["pid"],
                "status": "running",
            }
        )

    return json.dumps(
        {
            "count": len(agents_list),
            "agents": agents_list,
        },
        indent=2,
    )


def _lookup_active_agent(name: str) -> dict[str, object] | None:
    """Find a running agent by name using `agenthost list`."""
    for entry in _run_agenthost_list():
        if entry.get("name") == name:
            return entry
    return None


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
    `list_agents()` to discover running agents. The same thread_id as the
    ORCHESTRATOR's current conversation is used so that the specialist agent
    shares memory/context with this conversation.

    This function is async so that cancellation (e.g. Discord !stop) propagates
    to the downstream agent and closes the HTTP connection.
    """
    logger.info("ORCHESTRATOR send_message(agent_name='%s')", agent_name)

    entry = _lookup_active_agent(agent_name)
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


# ---------------------------------------------------------------------------
# Plan Management Tools
# ---------------------------------------------------------------------------


def _get_memory() -> tuple:
    """Create and return (AgentMemory, AgentConfig) for the ORCHESTRATOR."""
    from agenthost.memory import AgentMemory
    from agenthost.config import AgentConfig

    config = AgentConfig(path=_get_orchestrator_path(), name="ORCHESTRATOR")
    memory = AgentMemory(config)
    return memory, config


def plan_save(
    key: str,
    plan_id: str,
    status: str,
    request: str,
    coarse_steps: list[dict[str, str]],
    fine_steps: list[dict[str, Any]],
) -> str:
    """Save a plan to the ORCHESTRATOR's KV store.

    The key should follow the format 'plan:<plan_id>' for plans.
    Pass each top-level field as its own parameter; do not wrap the whole plan
    in a JSON string. The coarse_steps and fine_steps are lists of objects.

    Returns a confirmation or error message.
    """
    plan = {
        "plan_id": plan_id,
        "status": status,
        "request": request,
        "coarse_steps": coarse_steps or [],
        "fine_steps": fine_steps or [],
    }

    if not fine_steps:
        return json.dumps({"error": "fine_steps cannot be empty"})

    try:
        memory, _config = _get_memory()
        memory.set(key, plan)
        return json.dumps(
            {"success": True, "key": key, "size_bytes": len(json.dumps(plan))}
        )
    except Exception as exc:
        return json.dumps({"error": f"Failed to save plan: {exc}"})


def plan_load(key: str) -> str:
    """Load a plan (or any JSON value) from the ORCHESTRATOR's KV store by key.

    Returns the stored JSON as a string, or an error if the key does not exist.
    """
    try:
        memory, _config = _get_memory()
        value = memory.get(key)
        if value is None:
            return json.dumps({"error": f"No data found for key '{key}'."})
        return json.dumps({"success": True, "key": key, "data": value}, indent=2)
    except Exception as exc:
        return json.dumps({"error": f"Failed to load plan: {exc}"})
