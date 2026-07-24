"""Toolbox discovery: folder-based bundles of tools and skills.

A toolbox is a directory under the project ``toolboxes/`` folder that contains an
optional ``tools/`` directory and an optional ``skills/`` directory. Agents can
switch toolboxes at runtime to change their available tools and skills while
keeping the same memory and conversation state.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import agenthost


def get_toolboxes_root() -> Path:
    """Return the project ``toolboxes/`` directory.

    Resolution order:
    1. ``AGENTHOST_TOOLBOXES`` environment variable (absolute path or relative
       to the current working directory).
    2. ``toolboxes/`` under the current working directory, if it exists.
    3. ``toolboxes/`` next to the installed agenthost package (derived from
       ``agenthost.__file__``).

    If the directory does not exist yet, the resolved path is still returned so
    callers can create it or report it as empty.
    """
    # 1. Explicit override via environment variable.
    env_override = os.environ.get("AGENTHOST_TOOLBOXES")
    if env_override:
        target = Path(env_override)
        if not target.is_absolute():
            target = Path.cwd() / target
        return target.resolve()

    # 2. CWD-relative toolboxes/ if it exists (e.g. user is inside the repo).
    cwd_toolboxes = Path.cwd() / "toolboxes"
    if cwd_toolboxes.is_dir():
        return cwd_toolboxes

    # 3. Package-relative fallback.
    package_dir = Path(agenthost.__file__).resolve().parent
    project_root = package_dir.parent.parent
    return project_root / "toolboxes"


def validate_toolbox_name(name: str) -> bool:
    """Return True if ``name`` is a safe toolbox directory stem."""
    if not name or len(name) > 100:
        return False
    return bool(re.fullmatch(r"[a-zA-Z0-9_-]+", name))


def list_toolboxes() -> list[str]:
    """Return the names of all valid toolbox packages under ``toolboxes/``.

    A directory is considered a toolbox only if it has at least a ``tools/`` or
    ``skills/`` subdirectory, so plain notes or documentation folders are ignored.
    """
    root = get_toolboxes_root()
    if not root.exists():
        return []

    names: list[str] = []
    for item in sorted(root.iterdir()):
        if item.is_dir() and validate_toolbox_name(item.name):
            if (item / "tools").is_dir() or (item / "skills").is_dir():
                names.append(item.name)
    return names
