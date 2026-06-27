"""File and directory deletion tool for the SW_DEV agent."""
from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path


# Operations are scoped to the directory from which agenthost was invoked.
_REPO_ROOT = Path.cwd()


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


def _is_empty_directory(path: Path) -> bool:
    """Return True if *path* is an empty directory."""
    try:
        next(path.iterdir())
    except StopIteration:
        return True
    return False


def delete_file(path: str, recursive: bool = False) -> dict:
    """Delete a file or directory within the repository.

    Files are backed up automatically before deletion. Empty directories are
    deleted without a backup. Non-empty directories are only deleted when
    *recursive* is True, and are archived to a zip backup first.

    Args:
        path: File or directory path relative to the repository root.
        recursive: If True, delete non-empty directories recursively.
            Default False.

    Returns:
        A dict with status, resolved path, and (if applicable) backup path.
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
        return {"error": f"Path does not exist: {path}"}

    try:
        if target.is_file() or target.is_symlink():
            backup_path = _backup.backup_file(target)
            target.unlink()
            return {
                "status": "deleted",
                "path": str(target.relative_to(_REPO_ROOT)),
                "backup": str(backup_path),
            }

        if target.is_dir():
            if not _is_empty_directory(target):
                if not recursive:
                    return {
                        "error": f"Directory is not empty: {path}",
                        "hint": "Set recursive=True to delete it and its contents.",
                    }
                backup_path = _backup.backup_directory(target)
                shutil.rmtree(target)
                return {
                    "status": "deleted",
                    "path": str(target.relative_to(_REPO_ROOT)),
                    "backup": str(backup_path),
                }
            target.rmdir()
            return {
                "status": "deleted",
                "path": str(target.relative_to(_REPO_ROOT)),
            }

        return {"error": f"Unsupported path type: {path}"}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Failed to delete: {type(exc).__name__}: {exc}"}
