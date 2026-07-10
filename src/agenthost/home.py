"""Home-directory helpers for agenthost.

Two concepts:

- **Agenthost home**: the fixed project directory (``~/Workspace/agenthost``).
  All global state lives here: registry, keys, agent aliases, logs.
- **User home**: the user's home directory (``Path.home()``). Built-in tools such
  as ``send_discord_file`` can read files from anywhere under this directory.
"""

from __future__ import annotations

import os
from pathlib import Path


def get_agenthost_home() -> Path:
    """Return the agenthost home (project) directory.

    Respects the ``AGENTHOST_HOME`` environment variable. If unset, derives the
    project root from the location of this module so it works no matter where
    the repository was cloned.
    """
    env_home = os.environ.get("AGENTHOST_HOME")
    if env_home:
        return Path(env_home).expanduser().resolve()
    # This file is at src/agenthost/home.py, so the project root is three levels up.
    return Path(__file__).resolve().parent.parent.parent


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
    """Create the agenthost home directory if needed and return it."""
    home = get_agenthost_home()
    home.mkdir(parents=True, exist_ok=True)
    return home


# User home directory for tools that need broader file access.
HOME_DIR = Path(os.environ.get("AGENTHOST_HOME", Path.home())).expanduser().resolve()


def resolve_home_path(path: str) -> Path:
    """Resolve a path relative to the user's home directory.

    - Absolute paths are resolved as-is.
    - Relative paths are resolved relative to ``HOME_DIR``.
    - ``..`` traversal that escapes ``HOME_DIR`` raises ``ValueError``.

    Returns the resolved ``Path``.
    """
    if not path:
        raise ValueError("Path cannot be empty")

    target = Path(path)
    if target.is_absolute():
        resolved = target.resolve()
    else:
        resolved = (HOME_DIR / target).resolve()

    try:
        resolved.relative_to(HOME_DIR)
    except ValueError as exc:
        raise ValueError(
            f"Access denied: '{path}' resolves outside the home directory '{HOME_DIR}'."
        ) from exc

    return resolved
