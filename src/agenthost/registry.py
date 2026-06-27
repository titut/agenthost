"""Process registry for tracking active agenthost agents.

The registry is a small JSON file stored at the repository root. Each running
agent registers itself on startup and removes itself on shutdown. Because the
registry can be left stale if a process crashes, `list_agents()` filters out
entries whose PID is no longer alive.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


# Repo root is two levels above this file: src/agenthost/registry.py
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_REGISTRY_FILE = _REPO_ROOT / ".agenthost-registry.json"


@dataclass
class AgentRecord:
    pid: int
    name: str
    path: str
    host: str
    port: int
    started_at: float


def _is_process_alive(pid: int) -> bool:
    """Return True if a process with the given PID is currently running.

    Also treats zombie processes as dead, because a killed child that has not
    been reaped still has a PID but is no longer an active agent.
    """
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False

    # On Linux, a zombie's PID still responds to signal 0. Check /proc status.
    try:
        status_path = f"/proc/{pid}/status"
        with open(status_path, encoding="utf-8") as f:
            for line in f:
                if line.startswith("State:"):
                    # State line looks like: "State:\tZ (zombie)\n"
                    state = line.split()[1] if len(line.split()) > 1 else ""
                    return state != "Z"
    except (OSError, IndexError):
        pass

    return True


def _load_registry() -> list[dict[str, Any]]:
    if not _REGISTRY_FILE.exists():
        return []
    try:
        data = json.loads(_REGISTRY_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return []


def _save_registry(records: list[dict[str, Any]]) -> None:
    _REGISTRY_FILE.write_text(json.dumps(records, indent=2), encoding="utf-8")


def register_agent(name: str, path: Path, host: str, port: int) -> None:
    """Register a running agent. Called on serve startup."""
    records = _load_registry()
    record = {
        "pid": os.getpid(),
        "name": name,
        "path": str(path.resolve()),
        "host": host,
        "port": port,
        "started_at": time.time(),
    }
    # Remove any stale entry for the same PID first.
    records = [r for r in records if r.get("pid") != record["pid"]]
    records.append(record)
    _save_registry(records)


def unregister_agent() -> None:
    """Remove the current process from the registry. Called on serve shutdown."""
    pid = os.getpid()
    records = [r for r in _load_registry() if r.get("pid") != pid]
    _save_registry(records)


def list_agents() -> list[AgentRecord]:
    """Return all currently running registered agents, filtering out dead PIDs."""
    records = _load_registry()
    alive: list[AgentRecord] = []
    stale: list[dict[str, Any]] = []

    for r in records:
        pid = r.get("pid")
        if pid is not None and _is_process_alive(pid):
            alive.append(AgentRecord(**r))
        else:
            stale.append(r)

    # Clean up stale entries if any were found.
    if stale:
        _save_registry([r for r in records if r not in stale])

    return alive
