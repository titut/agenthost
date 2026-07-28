"""Unified CRUD for toolbox items (tools and skills)."""

from __future__ import annotations

import json
import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Sandbox root and validation (inlined — each tool file is standalone)
# ---------------------------------------------------------------------------
_EVOLVE_ROOT = Path("/home/koroko/Workspace/agenthost/toolboxes-evolve")
_VALID_NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]*$")


def _ok(name: str) -> bool:
    return bool(name) and len(name) <= 100 and bool(_VALID_NAME.fullmatch(name))


def _item_type_info(item_type: str) -> tuple[str, str, str]:
    """Validate and normalise. Returns (normalised, dir_name, suffix)."""
    t = item_type.strip().lower()
    if t == "tool":
        return (t, "tools", ".py")
    if t == "skill":
        return (t, "skills", ".md")
    raise ValueError(f"Invalid item_type '{item_type}'. Use 'tool' or 'skill'.")


def _require(toolbox: str) -> Path:
    """Validate toolbox name and return its directory."""
    if not _ok(toolbox):
        raise ValueError(
            f"Invalid toolbox name '{toolbox}'. "
            f"Use only letters, numbers, hyphens, and underscores (max 100 chars)."
        )
    d = _EVOLVE_ROOT / toolbox
    if not d.is_dir():
        raise ValueError(
            f"Toolbox '{toolbox}' does not exist. Use create_toolbox() first."
        )
    return d


# ===================================================================
# Tools
# ===================================================================


def list_toolbox_items(toolbox: str, item_type: str) -> str:
    """List all tools or skills in a specific toolbox.

    Args:
        toolbox: Name of the toolbox to inspect.
        item_type: One of ``"tool"`` or ``"skill"``.
    """
    try:
        item_type, dir_name, suffix = _item_type_info(item_type)
        toolbox_dir = _require(toolbox)
    except ValueError as exc:
        return json.dumps({"error": str(exc)})

    items_dir = toolbox_dir / dir_name
    if not items_dir.is_dir():
        return json.dumps(
            {"error": f"Toolbox '{toolbox}' has no {dir_name}/ directory."}
        )

    names: list[str] = sorted(
        p.stem for p in items_dir.glob(f"*{suffix}") if not p.name.startswith("_")
    )
    return json.dumps(
        {
            "toolbox": toolbox,
            "item_type": item_type,
            "items": names,
            "count": len(names),
        },
        indent=2,
    )


def create_toolbox_item(toolbox: str, item_type: str, name: str, content: str) -> str:
    """Create a new tool or skill in a specific toolbox.

    Writes the file to ``<toolbox>/tools/<name>.py`` (for tools) or
    ``<toolbox>/skills/<name>.md`` (for skills).

    After calling this, switch to the toolbox to use the new item::

        toolbox(action="switch", target="<toolbox>")

    Use ``create_toolbox(name)`` first if the toolbox doesn't exist yet.

    **Tools** must be top-level ``async def`` or ``def`` functions with type
    hints and a docstring. Files or functions starting with ``_`` are ignored.

    **Skills** must contain a ``# Description`` section to appear correctly
    in the Available Skills list.

    Args:
        toolbox: Name of the target toolbox.
        item_type: One of ``"tool"`` or ``"skill"``.
        name: Item name (becomes the file stem).
        content: Full source code (tool) or markdown (skill).
    """
    try:
        item_type, dir_name, suffix = _item_type_info(item_type)
        toolbox_dir = _require(toolbox)
    except ValueError as exc:
        return json.dumps({"error": str(exc)})

    if not _ok(name):
        return json.dumps(
            {
                "error": f"Invalid {item_type} name '{name}'. "
                f"Use only letters, numbers, hyphens, and underscores."
            }
        )

    # Skills must include a # Description section.
    if (
        item_type == "skill"
        and "# Description" not in content
        and "# description" not in content.lower()
    ):
        return json.dumps(
            {"error": "Skill content must include a '# Description' section."}
        )

    item_path = toolbox_dir / dir_name / f"{name}{suffix}"
    try:
        item_path.parent.mkdir(parents=True, exist_ok=True)
        item_path.write_text(content, encoding="utf-8")
    except Exception as exc:
        return json.dumps({"error": f"Failed to write {item_type}: {exc}"})

    return json.dumps(
        {
            "status": "written",
            "toolbox": toolbox,
            "item_type": item_type,
            "name": name,
            "size_bytes": item_path.stat().st_size,
        },
        indent=2,
    )


def read_toolbox_item(toolbox: str, item_type: str, name: str) -> str:
    """Read the full source code of a tool or skill in a specific toolbox.

    Args:
        toolbox: Name of the toolbox containing the item.
        item_type: One of ``"tool"`` or ``"skill"``.
        name: Item name (file stem, without the extension).
    """
    try:
        item_type, dir_name, suffix = _item_type_info(item_type)
        toolbox_dir = _require(toolbox)
    except ValueError as exc:
        return json.dumps({"error": str(exc)})

    if not _ok(name):
        return json.dumps({"error": f"Invalid {item_type} name '{name}'."})

    item_path = toolbox_dir / dir_name / f"{name}{suffix}"
    if not item_path.exists():
        return json.dumps(
            {
                "error": f"{item_type.capitalize()} '{name}' not found in toolbox '{toolbox}'."
            }
        )
    if not item_path.is_file():
        return json.dumps({"error": f"'{name}' in toolbox '{toolbox}' is not a file."})

    try:
        content = item_path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return json.dumps({"error": f"Failed to read {item_type}: {exc}"})

    return json.dumps(
        {
            "toolbox": toolbox,
            "item_type": item_type,
            "name": name,
            "content": content,
        },
        indent=2,
    )


def delete_toolbox_item(toolbox: str, item_type: str, name: str) -> str:
    """Delete a tool or skill from a specific toolbox.

    A ``.bak`` backup copy is saved before deletion.

    Args:
        toolbox: Name of the toolbox containing the item.
        item_type: One of ``"tool"`` or ``"skill"``.
        name: Item name (file stem, without the extension).
    """
    try:
        item_type, dir_name, suffix = _item_type_info(item_type)
        toolbox_dir = _require(toolbox)
    except ValueError as exc:
        return json.dumps({"error": str(exc)})

    if not _ok(name):
        return json.dumps({"error": f"Invalid {item_type} name '{name}'."})

    item_path = toolbox_dir / dir_name / f"{name}{suffix}"
    if not item_path.exists():
        return json.dumps(
            {
                "error": f"{item_type.capitalize()} '{name}' not found in toolbox '{toolbox}'."
            }
        )

    # Back up then delete.
    backup_path = item_path.with_suffix(suffix + ".bak")
    try:
        if backup_path.exists():
            backup_path.unlink()
        item_path.rename(backup_path)
    except Exception as exc:
        return json.dumps({"error": f"Failed to back up {item_type}: {exc}"})

    return json.dumps(
        {
            "status": "deleted",
            "toolbox": toolbox,
            "item_type": item_type,
            "name": name,
            "backup": str(backup_path.relative_to(_EVOLVE_ROOT)),
        },
        indent=2,
    )
