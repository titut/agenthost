"""ORCHESTRATOR tool: list registered agents and their capabilities."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from agenthost.agents_config import AgentsConfig
from agenthost.config import AgentConfig

# Load the sibling helper module by absolute path so imports work regardless of
# the current working directory.
_TOOLS_DIR = Path(__file__).resolve().parent
_helpers_spec = importlib.util.spec_from_file_location(
    "_helpers", str(_TOOLS_DIR / "_helpers.py")
)
_helpers = importlib.util.module_from_spec(_helpers_spec)
_helpers_spec.loader.exec_module(_helpers)

logger = _helpers.logger
run_agenthost_list = _helpers.run_agenthost_list


def list_agents() -> str:
    """List every registered agent, whether it is running, and what it can do.

    Returns each agent's persona (from WHOAMI.md), model settings, tools,
    skills, and current runtime status. Use this to decide which agent to
    delegate to.
    """
    logger.info("ORCHESTRATOR list_agents()")

    running = {entry["name"]: entry for entry in run_agenthost_list()}
    agents_config = AgentsConfig()
    aliases = agents_config.list()

    results = []
    for alias in sorted(aliases):
        path = agents_config.resolve(alias)
        if path is None or not path.is_dir() or not (path / "WHOAMI.md").exists():
            continue

        cfg = AgentConfig.from_path(path)
        entry: dict[str, object] = {
            "name": alias,
            "path": str(path),
            "model": cfg.model,
            "temperature": cfg.temperature,
            "persona": cfg._agent_description(path),
            "tools": [
                {"name": name, "description": desc}
                for name, desc in cfg._agent_tools(path / "tools")
            ],
            "skills": [
                {"name": name, "description": desc}
                for name, desc in cfg._agent_skills(path / "skills")
            ],
        }

        if alias in running:
            run_info = running[alias]
            entry["status"] = "running"
            entry["host"] = run_info.get("host")
            entry["port"] = run_info.get("port")
            entry["pid"] = run_info.get("pid")
        else:
            entry["status"] = "idle"

        results.append(entry)

    return json.dumps({"count": len(results), "agents": results}, indent=2)
