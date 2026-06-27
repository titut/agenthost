"""Fixed agenthost home directory and derived paths.

The agenthost executable can live anywhere on PATH, but all global state
(registry, keys, agent aliases) lives in a single fixed home directory.
"""

from __future__ import annotations

from pathlib import Path


def get_agenthost_home() -> Path:
    """Return the fixed agenthost home directory."""
    return Path.home() / "Workspace" / "agenthub"


def get_agents_yaml_path() -> Path:
    """Path to the alias → folder mapping file."""
    return get_agenthost_home() / "agents.yaml"


def get_registry_path() -> Path:
    """Path to the running-agent registry file."""
    return get_agenthost_home() / ".agenthost-registry.json"


def get_keys_db_path() -> Path:
    """Path to the default KeePass secrets database."""
    return get_agenthost_home() / "keys.kdbx"


def ensure_agenthost_home() -> Path:
    """Create the home directory if it does not exist and return it."""
    home = get_agenthost_home()
    home.mkdir(parents=True, exist_ok=True)
    return home
