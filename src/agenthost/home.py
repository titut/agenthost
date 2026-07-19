"""Home-directory helpers for agenthost.

Two concepts:

- **Agenthost home**: the runtime data directory (``~/.agenthost`` by default).
  All global state lives here: registry, keys, agent aliases, logs, uploads, and
  agent-generated data. Override with the ``AGENTHOST_HOME`` environment variable.
- **User home**: for sandboxed file access this is also the agenthost home
  directory. Built-in tools resolve relative paths inside this directory.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def _legacy_project_root() -> Path:
    """Return the legacy project-root directory.

    This file is at src/agenthost/home.py, so the project root is three levels up.
    """
    return Path(__file__).resolve().parent.parent.parent


def get_agenthost_home() -> Path:
    """Return the agenthost home (data) directory.

    Respects the ``AGENTHOST_HOME`` environment variable. If unset, defaults to
    ``~/.agenthost`` so that multiple agenthost checkouts can share the same
    runtime state.
    """
    env_home = os.environ.get("AGENTHOST_HOME")
    if env_home:
        return Path(env_home).expanduser().resolve()
    return Path.home() / ".agenthost"


def _is_default_home() -> bool:
    """Return True when the agenthost home is the default ~/.agenthost."""
    return os.environ.get("AGENTHOST_HOME") is None


def get_agents_yaml_path() -> Path:
    """Path to the alias → folder mapping file."""
    return get_agenthost_home() / "agents.yaml"


def get_registry_path() -> Path:
    """Path to the running-agent registry file."""
    return get_agenthost_home() / ".agenthost-registry.json"


def get_keys_db_path() -> Path:
    """Path to the default KeePass secrets database."""
    return get_agenthost_home() / "keys.kdbx"


def _copy_if_exists(src: Path, dst: Path) -> bool:
    """Copy a file or directory tree if the source exists. Returns True if copied."""
    if not src.exists():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(
            src, dst, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".git")
        )
    else:
        shutil.copy2(src, dst)
    return True


def migrate_legacy_home() -> list[str]:
    """Copy legacy project-root state into ~/.agenthost on first run.

    Only runs when using the default home (AGENTHOST_HOME unset) and the home
    directory does not yet exist. Existing files are copied, not moved, so the
    project root remains intact.
    """
    if not _is_default_home():
        return []

    home = get_agenthost_home()
    if home.exists():
        return []

    legacy = _legacy_project_root()
    if not legacy.exists():
        return []

    migrated: list[str] = []

    # Global state files.
    for name in (
        "agents.yaml",
        ".agenthost-registry.json",
        "agenthost.log",
        "keys.kdbx",
    ):
        if _copy_if_exists(legacy / name, home / name):
            migrated.append(name)

    # Global directories.
    for name in ("uploads", "entries", "output"):
        if _copy_if_exists(legacy / name, home / name):
            migrated.append(f"{name}/")

    # Agent memory directories, both in agents/ and agents_personal/.
    for base in (legacy / "agents", legacy / "agents_personal"):
        if not base.is_dir():
            continue
        for agent_dir in base.iterdir():
            if not agent_dir.is_dir():
                continue
            memory_src = agent_dir / "memory"
            if memory_src.exists():
                agent_name = agent_dir.name
                memory_dst = home / "memory" / agent_name
                _copy_if_exists(memory_src, memory_dst)
                migrated.append(f"memory/{agent_name}/")

    # Agent-specific data files that live next to agent code.
    todo_db = legacy / "agents" / "TODO" / "todos.db"
    if _copy_if_exists(todo_db, home / "agents" / "TODO" / "todos.db"):
        migrated.append("agents/TODO/todos.db")

    return migrated


def ensure_agenthost_home() -> Path:
    """Create the agenthost home directory if needed and return it.

    On the first run with the default ~/.agenthost location, legacy state from
    the project root is migrated automatically.  A ``default_agent.yaml`` is
    seeded the first time the home directory is created so users have a starting
    point for global agent defaults.
    """
    migrate_legacy_home()
    home = get_agenthost_home()
    home.mkdir(parents=True, exist_ok=True)

    # Seed default_agent.yaml on first creation so users have a starting point.
    _seed_default_agent_config(home)

    return home


def _seed_default_agent_config(home: Path) -> None:
    """Write a default_agent.yaml if one does not already exist in *home*."""
    default_yaml = home / "default_agent.yaml"
    if default_yaml.exists():
        return

    # Lazy import to avoid circular dependency (config.py imports home.py).
    import yaml

    from agenthost.config import DEFAULT_CONFIG

    content = (
        "# Default agent configuration for agenthost.\n"
        "# Edit this file to set global defaults for all agents.\n"
        "# Individual agents override these values in their own agent.yaml.\n"
        "# See templates/agent/agent.yaml for the full reference.\n\n"
        + yaml.safe_dump(DEFAULT_CONFIG, sort_keys=False, default_flow_style=False)
        + "\n"
        "# embedding:\n"
        "#   model: BAAI/bge-m3\n"
        "#   base_url: https://api.deepinfra.com/v1ai\n"
        "#\n"
        "# memory:\n"
        "#   mode: rag\n"
        "#   recent_messages: 8\n"
        "#   chunk_size: 512\n"
        "#   budget_tokens: 6000\n"
        "#   max_chunks: 12\n"
        "#   max_pool_chunks: 5000\n"
        "#   similarity_weight: 0.6\n"
        "#   recency_weight: 0.3\n"
        "#   role_weight: 0.1\n"
        "#   mmr_lambda: 0.7\n"
    )
    default_yaml.write_text(content, encoding="utf-8")


# User home directory for tools that need broader file access.
# Defaults to the agenthost home so agents are sandboxed inside ~/.agenthost.
HOME_DIR = (
    Path(os.environ.get("AGENTHOST_HOME", get_agenthost_home())).expanduser().resolve()
)


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
