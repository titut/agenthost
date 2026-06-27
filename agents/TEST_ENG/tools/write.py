"""General-purpose file writer for the TEST_ENG agent."""
from __future__ import annotations

from pathlib import Path


# Repository root is three levels above this file: agents/TEST_ENG/tools/write.py
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def write_file(path: str, content: str) -> dict:
    """Write a file to the repository. Any extension is allowed.

    Creates parent directories automatically. Overwrites existing files silently.
    Path traversal outside the repository root is rejected.

    Use this for test plans (`.md`) and test code (`.py`, `.js`, etc.).

    Args:
        path: File path relative to the repository root.
              E.g. "tests/test_feature.py" or "agents/TEST_ENG/output/test-plans/feature.md"
        content: Complete file content as a string.

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

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Failed to write file: {type(exc).__name__}: {exc}"}

    return {
        "status": "written",
        "path": str(target.relative_to(_REPO_ROOT)),
        "bytes": target.stat().st_size,
    }
