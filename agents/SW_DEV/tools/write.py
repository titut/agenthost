"""General-purpose file writer for the SW_DEV agent."""
from __future__ import annotations

import importlib.util
from pathlib import Path


# Repository root is three levels above this file: agents/SW_DEV/tools/write.py
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


def write_file(path: str, content: str) -> dict:
    """Write a file to the repository. Any extension is allowed.

    Creates parent directories automatically. Overwrites existing files, but
    only after creating a timestamped backup under
    .agenthost/backups/<timestamp>/<path>.

    Use this for creating new files. For modifying existing files, prefer
    edit_file(old_string, new_string) so only a targeted snippet is changed.

    Args:
        path: File path relative to the repository root.
              E.g. "src/my_module/new_feature.py" or "config/nginx.conf"
        content: Complete file content as a string.

    Returns:
        A dict with status, resolved path, byte count, and (if applicable)
        backup path.
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

    backup_path = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.is_file():
            backup_path = _backup.backup_file(target)
        target.write_text(content, encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Failed to write file: {type(exc).__name__}: {exc}"}

    result = {
        "status": "written",
        "path": str(target.relative_to(_REPO_ROOT)),
        "bytes": target.stat().st_size,
    }
    if backup_path is not None:
        result["backup"] = str(backup_path.relative_to(_REPO_ROOT))
    return result
