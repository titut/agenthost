"""ORCHESTRATOR tools: agent lifecycle management and plan tracking."""

from __future__ import annotations

import calendar
import copy
import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import httpx

from agenthost.logger import setup_logging

logger = setup_logging("agenthost.orchestrator")

_MAX_AGENTS = 3
_HEALTH_TIMEOUT = 15  # seconds to wait for agent to become healthy
_DESPAWN_GRACE = 5  # seconds to wait after SIGTERM before SIGKILL

# Key used to store spawned-agent records in the ORCHESTRATOR's own KV store.
_REGISTRY_KEY = "agent_registry"

# Keep handles to spawned child processes so we can reap them and avoid zombies.
_PROCS: dict[int, subprocess.Popen] = {}


def _reap_process(proc: subprocess.Popen) -> None:
    """Wait on a child process so it does not become a zombie."""
    try:
        proc.wait()
    except Exception:
        pass


def _track_process(proc: subprocess.Popen) -> None:
    """Track a spawned process and reap it automatically when it exits."""
    _PROCS[proc.pid] = proc
    threading.Thread(target=_reap_process, args=(proc,), daemon=True).start()


def _get_orchestrator_path() -> Path:
    """Resolve ORCHESTRATOR's own folder path from `agenthost agent list`."""
    agents = _run_agenthost_agent_list()
    if "ORCHESTRATOR" in agents:
        return agents["ORCHESTRATOR"]
    raise RuntimeError(
        "ORCHESTRATOR is not registered. Run `agenthost agent add ORCHESTRATOR <path>`."
    )


def _get_registry() -> dict:
    """Read the agent registry from the ORCHESTRATOR's KV store."""
    from agenthost.memory import AgentMemory
    from agenthost.config import AgentConfig

    config = AgentConfig(path=_get_orchestrator_path(), name="ORCHESTRATOR")
    memory = AgentMemory(config)
    registry = copy.deepcopy(memory.get(_REGISTRY_KEY, {}))
    logger.debug("Loaded ORCHESTRATOR registry with %d entries", len(registry))
    return registry


def _save_registry(registry: dict) -> None:
    """Persist the agent registry to the ORCHESTRATOR's KV store."""
    from agenthost.memory import AgentMemory
    from agenthost.config import AgentConfig

    config = AgentConfig(path=_get_orchestrator_path(), name="ORCHESTRATOR")
    memory = AgentMemory(config)
    memory.set(_REGISTRY_KEY, registry)
    logger.debug("Saved ORCHESTRATOR registry with %d entries", len(registry))


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


def spawn_agent(name: str) -> str:
    """Spawn a registered agent by alias. Port is assigned dynamically by agenthost.

    Max 3 concurrent agents. Returns an agent_id (UUID) on success.
    """
    logger.info("ORCHESTRATOR spawn_agent('%s')", name)
    registry = _get_registry()
    running = {k: v for k, v in registry.items() if v.get("status") == "running"}

    if len(running) >= _MAX_AGENTS:
        logger.warning(
            "ORCHESTRATOR at max agents (%d) when spawning '%s'", _MAX_AGENTS, name
        )
        return json.dumps(
            {
                "error": f"Already at max ({_MAX_AGENTS}) concurrent agents. Despawn one first.",
                "running_agents": [
                    {"agent_id": k, "name": v["name"], "port": v["port"]}
                    for k, v in running.items()
                ],
            }
        )

    agents = _run_agenthost_agent_list()
    agent_dir = agents.get(name)
    if agent_dir is None:
        logger.error("ORCHESTRATOR cannot spawn unregistered agent '%s'", name)
        return json.dumps({"error": f"Agent not registered: {name}"})

    if not agent_dir.is_dir() or not (agent_dir / "WHOAMI.md").exists():
        logger.error("ORCHESTRATOR agent folder missing for '%s': %s", name, agent_dir)
        return json.dumps({"error": f"Agent folder not found: {agent_dir}"})

    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "agenthost", "serve", name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.info("ORCHESTRATOR started agent '%s' as pid %d", name, proc.pid)
    except Exception as exc:
        logger.exception("ORCHESTRATOR failed to spawn agent '%s': %s", name, exc)
        return json.dumps({"error": f"Failed to start subprocess: {exc}"})

    # Track the process so we can reap it later and avoid zombies.
    _track_process(proc)

    # Discover the dynamically assigned port by running `agenthost list`.
    assigned_port = None
    for _ in range(30):  # wait up to ~3 seconds for the agent to register
        active = _run_agenthost_list()
        for entry in active:
            if entry.get("pid") == proc.pid or entry.get("name") == name:
                assigned_port = entry.get("port")
                break
        if assigned_port:
            break
        time.sleep(0.1)

    if assigned_port is None:
        logger.error(
            "ORCHESTRATOR could not discover port for spawned agent '%s'", name
        )
        proc.kill()
        try:
            proc.wait(timeout=2.0)
        except Exception:
            pass
        return json.dumps(
            {"error": f"Could not discover assigned port for agent '{name}'."}
        )

    port = assigned_port
    logger.info("ORCHESTRATOR discovered agent '%s' on port %d", name, port)

    # Health poll
    health_url = f"http://127.0.0.1:{port}/health"
    deadline = time.monotonic() + _HEALTH_TIMEOUT
    healthy = False
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(health_url, timeout=2.0)
            if resp.status_code == 200:
                healthy = True
                break
        except Exception:
            pass
        time.sleep(0.5)

    if not healthy:
        proc.kill()
        try:
            proc.wait(timeout=2.0)
        except Exception:
            pass
        return json.dumps(
            {
                "error": f"Agent '{name}' failed to become healthy on port {port} within {_HEALTH_TIMEOUT}s.",
            }
        )

    agent_id = uuid.uuid4().hex[:8]
    registry[agent_id] = {
        "name": name,
        "port": port,
        "pid": proc.pid,
        "spawned_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "status": "running",
    }
    _save_registry(registry)

    return json.dumps(
        {
            "success": True,
            "agent_id": agent_id,
            "name": name,
            "port": port,
            "pid": proc.pid,
        }
    )


def send_message(agent_id: str, message: str) -> str:
    """Send a message to a spawned agent and return the full response.

    Reuses the same thread_id for the lifetime of the spawned agent so the
    conversation retains memory. A new spawn gets a new thread_id.
    The agent must be running.
    """
    logger.info("ORCHESTRATOR send_message(agent_id='%s')", agent_id)
    registry = _get_registry()
    entry = registry.get(agent_id)

    if not entry:
        logger.error("ORCHESTRATOR send_message: no agent with id '%s'", agent_id)
        return json.dumps(
            {
                "error": f"No agent found with ID '{agent_id}'. Use list_agents to see running agents."
            }
        )

    if entry.get("status") != "running":
        logger.warning(
            "ORCHESTRATOR send_message: agent '%s' status is '%s'",
            agent_id,
            entry.get("status"),
        )
        return json.dumps(
            {
                "error": f"Agent '{agent_id}' ({entry['name']}) is not running. Despawn and re-spawn."
            }
        )

    port = entry["port"]
    chat_url = f"http://127.0.0.1:{port}/chat"
    logger.info(
        "ORCHESTRATOR send_message: target is '%s' on port %d", entry.get("name"), port
    )

    # Health check first
    try:
        health_resp = httpx.get(f"http://127.0.0.1:{port}/health", timeout=2.0)
        if health_resp.status_code != 200:
            raise RuntimeError("Health check failed")
        logger.debug("ORCHESTRATOR send_message: health check passed for port %d", port)
    except Exception as exc:
        logger.warning(
            "ORCHESTRATOR send_message: health check failed for port %d: %s", port, exc
        )
        return json.dumps(
            {
                "error": f"Agent '{entry['name']}' (port {port}) is not responding. "
                f"Use despawn_agent('{agent_id}') and re-spawn."
            }
        )

    # Reuse the same thread_id for this spawned agent's lifetime.
    thread_id = entry.get("thread_id")
    if not thread_id:
        thread_id = uuid.uuid4().hex
        entry["thread_id"] = thread_id
        _save_registry(registry)

    payload = {"message": message, "thread_id": thread_id}

    try:
        response_text = ""
        with httpx.stream("POST", chat_url, json=payload, timeout=600.0) as resp:
            resp.raise_for_status()
            current_event = None
            for line in resp.iter_lines():
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
            "ORCHESTRATOR send_message: received response from '%s' (%d chars)",
            entry.get("name"),
            len(response_text),
        )
        return json.dumps(
            {
                "agent_id": agent_id,
                "name": entry["name"],
                "response": response_text,
            }
        )
    except Exception as exc:
        logger.exception(
            "ORCHESTRATOR send_message: communication with '%s' on port %d failed: %s",
            entry.get("name"),
            port,
            exc,
        )
        return json.dumps({"error": f"Communication with agent failed: {exc}"})


def despawn_agent(agent_id: str) -> str:
    """Stop a spawned agent gracefully. SIGTERM, wait, SIGKILL if needed."""
    registry = _get_registry()
    entry = registry.pop(agent_id, None)

    if not entry:
        return json.dumps({"error": f"No agent found with ID '{agent_id}'."})

    pid = entry["pid"]
    port = entry["port"]
    name = entry["name"]

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass  # Already dead
    except Exception as exc:
        return json.dumps({"error": f"Failed to signal agent: {exc}"})

    # Wait for graceful shutdown
    deadline = time.monotonic() + _DESPAWN_GRACE
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)  # Check if alive
            time.sleep(0.3)
        except ProcessLookupError:
            break
    else:
        # Force kill
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    # Reap the child so it doesn't become a zombie. If we have the Popen handle,
    # use it; otherwise fall back to waitpid.
    proc = _PROCS.pop(pid, None)
    if proc is not None:
        try:
            proc.wait(timeout=_DESPAWN_GRACE + 1)
        except Exception:
            pass
    else:
        try:
            os.waitpid(pid, 0)
        except (ChildProcessError, ProcessLookupError):
            pass

    _save_registry(registry)

    return json.dumps(
        {
            "success": True,
            "agent_id": agent_id,
            "name": name,
            "port": port,
            "action": "terminated",
        }
    )


def list_agents() -> str:
    """List all spawned agents and their status.

    Refreshes port/status information from `agenthost list` so the ORCHESTRATOR
    stays in sync with the actual state of running agents.
    """
    logger.info("ORCHESTRATOR list_agents()")
    registry = _get_registry()
    active = {a["name"]: a for a in _run_agenthost_list()}

    # Refresh running entries against `agenthost list`. Mark dead entries.
    for agent_id, entry in list(registry.items()):
        if entry.get("status") != "running":
            continue
        name = entry["name"]
        live = active.get(name)
        if live and live.get("pid") == entry["pid"]:
            entry["port"] = live["port"]
            entry["host"] = live["host"]
        else:
            logger.warning(
                "ORCHESTRATOR list_agents: marking agent '%s' (pid %s) as down",
                name,
                entry.get("pid"),
            )
            entry["status"] = "down"

    # Remove entries that have been down for a while or no longer exist in agenthost list.
    registry = {k: v for k, v in registry.items() if v.get("status") == "running"}
    _save_registry(registry)
    logger.info("ORCHESTRATOR list_agents: %d running agents", len(registry))

    running = registry

    agents_list = []
    for agent_id, entry in running.items():
        uptime = 0
        if "spawned_at" in entry:
            try:
                spawned = time.strptime(entry["spawned_at"], "%Y-%m-%dT%H:%M:%SZ")
                # spawned_at is UTC; use calendar.timegm to avoid local-time skew.
                uptime = int(time.time() - calendar.timegm(spawned))
            except Exception:
                pass

        agents_list.append(
            {
                "agent_id": agent_id,
                "name": entry["name"],
                "port": entry["port"],
                "pid": entry["pid"],
                "status": entry.get("status", "unknown"),
                "uptime_seconds": uptime,
            }
        )

    return json.dumps(
        {
            "count": len(agents_list),
            "max": _MAX_AGENTS,
            "slots_remaining": _MAX_AGENTS - len(agents_list),
            "agents": agents_list,
        },
        indent=2,
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


def plan_save(key: str, plan_json: str) -> str:
    """Save a plan (or any JSON value) to the ORCHESTRATOR's KV store.

    The key should follow the format 'plan:<plan_id>' for plans.
    The value must be a valid JSON string.

    Returns a confirmation or error message.
    """
    # Validate JSON before saving
    try:
        parsed = json.loads(plan_json)
    except json.JSONDecodeError as exc:
        return json.dumps({"error": f"Invalid JSON: {exc}"})

    try:
        memory, _config = _get_memory()
        memory.set(key, parsed)
        return json.dumps({"success": True, "key": key, "size_bytes": len(plan_json)})
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


def plan_delete(key: str) -> str:
    """Delete a plan (or any key) from the ORCHESTRATOR's KV store.

    Returns a confirmation or error if the key does not exist.
    """
    try:
        memory, _config = _get_memory()
        existing = memory.get(key)
        if existing is None:
            return json.dumps({"error": f"No data found for key '{key}'."})
        memory.set(key, None)  # kv store uses set with None to clear
        # We need to actually delete the key. Let's use sqlite directly.
        import sqlite3

        db_path = _get_orchestrator_path() / "memory" / "memory.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("DELETE FROM kv WHERE key = ?", (key,))
        conn.commit()
        conn.close()
        return json.dumps({"success": True, "key": key, "action": "deleted"})
    except Exception as exc:
        return json.dumps({"error": f"Failed to delete plan: {exc}"})

