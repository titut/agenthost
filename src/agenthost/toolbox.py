"""Toolbox discovery: folder-based bundles of tools and skills.

A toolbox is a directory under the project ``toolboxes/`` folder that contains an
optional ``tools/`` directory and an optional ``skills/`` directory. Agents can
switch toolboxes at runtime to change their available tools and skills while
keeping the same memory and conversation state.
"""

from __future__ import annotations

import re
from pathlib import Path

import agenthost


def get_toolboxes_root() -> Path:
    """Return the project ``toolboxes/`` directory.

    The toolboxes folder lives at the repository root, next to ``agents/`` and
    ``templates/``. If it does not exist yet, the path is still returned so
    callers can create it or report it as empty.
    """
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
