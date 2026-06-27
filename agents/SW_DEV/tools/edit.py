"""File editing tool for the SW_DEV agent."""
from __future__ import annotations

from pathlib import Path


# Repository root is three levels above this file: agents/SW_DEV/tools/edit.py
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def edit_file(path: str, content: str) -> dict:
    """Overwrite an existing file in the repository.

    Refuses to create new files — use write_file for that.
    Useful for iterative development: refactor, fix bugs, update imports, etc.

    Args:
        path: File path relative to the repository root. Must exist.
        content: Complete new file content.

    Returns:
        A dict with status, resolved path, and byte count.
    """
    if not path or not path.strip():
        return {"error": "Path cannot be empty."}

    safe_path = path.strip()

    try:
        target = (_REPO_ROOT / safe_path).resolve()
        target.relative_to(_REPO_ROOT.resolve())  # prevent traversal escape
    except ValueError:
        return {"error": "Path escapes repository root."}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Invalid path: {type(exc).__name__}: {exc}"}

    if not target.exists():
        return {"error": f"File does not exist: {path}. Use write_file to create new files."}
    if not target.is_file():
        return {"error": f"Not a file: {path}"}

    try:
        target.write_text(content, encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Failed to edit file: {type(exc).__name__}: {exc}"}

    return {
        "status": "edited",
        "path": str(target.relative_to(_REPO_ROOT)),
        "bytes": target.stat().st_size,
    }
