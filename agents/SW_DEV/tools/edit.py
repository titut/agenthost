"""File editing tool for the SW_DEV agent."""
from __future__ import annotations

import importlib.util
from pathlib import Path


# Repository root is three levels above this file: agents/SW_DEV/tools/edit.py
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def _load_backup_helper():
    """Load the private backup helper module bundled with these tools."""
    backup_path = Path(__file__).resolve().parent / "_backup.py"
    spec = importlib.util.spec_from_file_location("_sw_dev_backup", backup_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load backup helper module")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_backup = _load_backup_helper()


def edit_file(path: str, old_string: str, new_string: str) -> dict:
    """Replace a unique snippet in an existing file.

    This is the safe way to modify existing files: the *old_string* must match
    exactly one location in the file, and only that location is replaced with
    *new_string*. If *old_string* is not found, or appears more than once, the
    operation fails and the file is left untouched.

    Before any change, the current file is backed up to
    .agenthost/backups/<timestamp>/<path>.

    Args:
        path: File path relative to the repository root. Must exist.
        old_string: The exact text to replace. Must be unique in the file.
        new_string: The text to substitute for old_string.

    Returns:
        A dict with status, resolved path, byte count, and backup path.
    """
    if not path or not path.strip():
        return {"error": "Path cannot be empty."}
    if old_string is None:
        return {"error": "old_string cannot be None."}

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
        original = target.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Failed to read file: {type(exc).__name__}: {exc}"}

    occurrences = original.count(old_string)
    if occurrences == 0:
        return {
            "error": f"old_string not found in {path}. No changes made.",
            "hint": "Read the file and copy the exact text you want to replace.",
        }
    if occurrences > 1:
        return {
            "error": f"old_string appears {occurrences} times in {path}. No changes made.",
            "hint": "Use a longer, unique snippet so the replacement target is unambiguous.",
        }

    new_content = original.replace(old_string, new_string, 1)

    try:
        backup_path = _backup.backup_file(target)
        target.write_text(new_content, encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Failed to edit file: {type(exc).__name__}: {exc}"}

    return {
        "status": "edited",
        "path": str(target.relative_to(_REPO_ROOT)),
        "bytes": target.stat().st_size,
        "backup": str(backup_path.relative_to(_REPO_ROOT)),
    }
