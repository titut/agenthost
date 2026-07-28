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


def get_toolboxes_root(toolboxes_dir: Path | None = None) -> Path:
    """Return the project ``toolboxes/`` directory.

    Resolution order:
    1. ``toolboxes_dir`` if provided (e.g. from ``agent.yaml``).
    2. ``toolboxes/`` under the current working directory, if it exists.
    3. ``toolboxes/`` next to the installed agenthost package (derived from
       ``agenthost.__file__``).

    If the directory does not exist yet, the resolved path is still returned so
    callers can create it or report it as empty.
    """
    if toolboxes_dir is not None:
        return Path(toolboxes_dir).expanduser().resolve()

    # CWD-relative toolboxes/ if it exists (e.g. user is inside the repo).
    cwd_toolboxes = Path.cwd() / "toolboxes"
    if cwd_toolboxes.is_dir():
        return cwd_toolboxes

    # Package-relative fallback.
    package_dir = Path(agenthost.__file__).resolve().parent
    project_root = package_dir.parent.parent
    return project_root / "toolboxes"


def validate_toolbox_name(name: str) -> bool:
    """Return True if ``name`` is a safe toolbox directory stem.

    Names must start with a letter or digit (never underscore), be at
    most 100 characters, and contain only letters, digits, hyphens, and
    underscores.
    """
    if not name or name.startswith("_") or len(name) > 100:
        return False
    return bool(re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*$", name))


def list_toolboxes(toolboxes_dir: Path | None = None) -> list[str]:
    """Return the names of all valid toolbox packages under ``toolboxes/``.

    A directory is considered a toolbox only if it has at least a ``tools/`` or
    ``skills/`` subdirectory, so plain notes or documentation folders are ignored.
    """
    root = get_toolboxes_root(toolboxes_dir)
    if not root.exists():
        return []

    names: list[str] = []
    for item in sorted(root.iterdir()):
        if item.is_dir() and validate_toolbox_name(item.name):
            if (item / "tools").is_dir() or (item / "skills").is_dir():
                names.append(item.name)
    return names
