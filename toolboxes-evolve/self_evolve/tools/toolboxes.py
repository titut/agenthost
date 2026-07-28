"""Toolbox-level management: list and create toolboxes."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Sandbox root and validation (inlined — each tool file is standalone)
# ---------------------------------------------------------------------------
_EVOLVE_ROOT = Path("/home/koroko/Workspace/agenthost/toolboxes-evolve")
_VALID_NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]*$")


def _ok(name: str) -> bool:
    return bool(name) and len(name) <= 100 and bool(_VALID_NAME.fullmatch(name))


# ===================================================================
# Tools
# ===================================================================


def list_toolboxes() -> str:
    """List all toolboxes under the evolve root.

    A directory is considered a toolbox if it has at least a ``tools/`` or
    ``skills/`` subdirectory. Returns each toolbox name along with its
    tool and skill counts.
    """
    if not _EVOLVE_ROOT.exists():
        return json.dumps({"toolboxes": []}, indent=2)

    results: list[dict[str, Any]] = []
    for entry in sorted(_EVOLVE_ROOT.iterdir()):
        if not entry.is_dir() or not _ok(entry.name):
            continue
        has_tools = (entry / "tools").is_dir()
        has_skills = (entry / "skills").is_dir()
        if not has_tools and not has_skills:
            continue

        tool_count = len(list((entry / "tools").glob("*.py"))) if has_tools else 0
        skill_count = len(list((entry / "skills").glob("*.md"))) if has_skills else 0

        results.append({"name": entry.name, "tools": tool_count, "skills": skill_count})

    return json.dumps({"toolboxes": results}, indent=2)


def create_toolbox(name: str) -> str:
    """Create a new toolbox under the evolve root.

    Scaffolds ``tools/`` and ``skills/`` directories inside the new toolbox.
    Use this before creating tools or skills in a toolbox that doesn't exist yet.

    Args:
        name: Toolbox name. Use only letters, numbers, hyphens, and underscores.
    """
    if not _ok(name):
        return json.dumps(
            {
                "error": f"Invalid toolbox name '{name}'. "
                f"Use only letters, numbers, hyphens, and underscores (max 100 chars)."
            }
        )

    toolbox_dir = _EVOLVE_ROOT / name
    if toolbox_dir.exists():
        has_tools = (toolbox_dir / "tools").is_dir()
        has_skills = (toolbox_dir / "skills").is_dir()
        return json.dumps(
            {
                "error": f"Toolbox '{name}' already exists.",
                "has_tools": has_tools,
                "has_skills": has_skills,
            }
        )

    try:
        (toolbox_dir / "tools").mkdir(parents=True, exist_ok=True)
        (toolbox_dir / "skills").mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        return json.dumps({"error": f"Failed to create toolbox: {exc}"})

    return json.dumps({"status": "created", "toolbox": name}, indent=2)
