"""Shared helpers for ORCHESTRATOR tools."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from agenthost.agents_config import AgentsConfig
from agenthost.home import get_agents_yaml_path
from agenthost.logger import setup_logging

logger = setup_logging("agenthost.orchestrator")

# How many times to retry a failed agent request before giving up.
_SEND_RETRIES = 1


def run_agenthost_list() -> list[dict[str, object]]:
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


def lookup_active_agent(name: str) -> dict[str, object] | None:
    """Find a running agent by name using `agenthost list`."""
    for entry in run_agenthost_list():
        if entry.get("name") == name:
            return entry
    return None


def get_orchestrator_path() -> Path:
    """Resolve ORCHESTRATOR's own folder path from agents.yaml.

    The registry is located via home.py so it respects AGENTHOST_HOME overrides.
    """
    agents_config = AgentsConfig(path=get_agents_yaml_path())
    path = agents_config.resolve("ORCHESTRATOR")
    if path is None:
        raise RuntimeError(
            "ORCHESTRATOR is not registered. Run `agenthost agent add ORCHESTRATOR <path>`."
        )
    return path


def get_memory() -> tuple[Any, Any]:
    """Create and return (AgentMemory, AgentConfig) for the ORCHESTRATOR."""
    from agenthost.config import AgentConfig
    from agenthost.memory import AgentMemory

    config = AgentConfig.from_path(get_orchestrator_path())
    memory = AgentMemory(config)
    return memory, config
