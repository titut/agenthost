"""The Systems Engineer's only tool: writing structured markdown files."""
from __future__ import annotations

import json
from pathlib import Path


# Agent output workspace. All markdown files are written under this directory.
_OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"


def write_markdown(path: str, content: str) -> dict:
    """Write a markdown file inside the SYS_ENG output directory.

    The given path is sanitized so it cannot escape the output directory.
    Parent directories are created automatically. Existing files are overwritten.

    Args:
        path: Relative path for the markdown file (e.g. "runbooks/database-failover.md").
              A ".md" extension is appended automatically if missing.
        content: Full markdown content to write.

    Returns:
        A dict with the resolved file path and status.
    """
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    safe_path = path.strip()
    if not safe_path:
        return {"error": "Path cannot be empty."}

    if not safe_path.lower().endswith(".md"):
        safe_path += ".md"

    try:
        target = (_OUTPUT_DIR / safe_path).resolve()
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Invalid path: {type(exc).__name__}: {exc}"}

    # Prevent directory traversal outside the output workspace.
    try:
        target.relative_to(_OUTPUT_DIR.resolve())
    except ValueError:
        return {
            "error": f"Path escapes output directory. Must be inside {_OUTPUT_DIR}."
        }

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Failed to write file: {type(exc).__name__}: {exc}"}

    return {
        "status": "written",
        "path": str(target.relative_to(_OUTPUT_DIR.parent)),
        "bytes": target.stat().st_size,
    }
