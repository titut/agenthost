"""Repository exploration tools for the Systems Engineer agent."""
from __future__ import annotations

from pathlib import Path


# Operations are scoped to the directory from which agenthost was invoked.
_REPO_ROOT = Path.cwd()

_IGNORED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "venv",
    ".venv",
    "__pycache__",
    "node_modules",
    ".pytest_cache",
    ".mypy_cache",
    ".tox",
    "dist",
    "build",
    ".egg-info",
    ".coverage",
    "target",
}

_IGNORED_FILE_PREFIXES = (".",)


def _resolve_within_repo(path: str) -> Path:
    """Resolve a path relative to the repo root, refusing traversal escapes."""
    target = (_REPO_ROOT / path).resolve()
    target.relative_to(_REPO_ROOT.resolve())
    return target


def read_directory_tree(path: str = ".", max_depth: int = 3) -> dict:
    """Return a directory tree summary for repository exploration.

    Args:
        path: Directory path relative to the repository root. Defaults to the repo root.
        max_depth: How many levels deep to traverse (default 3).

    Returns:
        A dict with the root path, max_depth, and a list of entries.
        Each entry is {name, type, depth, children?}.
    """
    try:
        root = _resolve_within_repo(path)
    except ValueError as exc:
        return {"error": f"Path escapes repository root: {exc}"}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Invalid path: {type(exc).__name__}: {exc}"}

    if not root.is_dir():
        return {"error": f"Not a directory: {path}"}

    entries = []

    def _walk(current: Path, depth: int) -> list[dict]:
        items: list[dict] = []
        try:
            for child in sorted(current.iterdir()):
                if child.name in _IGNORED_DIRS:
                    continue
                if child.name.startswith(_IGNORED_FILE_PREFIXES):
                    continue

                rel = child.relative_to(root)
                if child.is_dir():
                    item: dict = {
                        "name": child.name,
                        "type": "directory",
                        "path": str(rel),
                        "depth": depth,
                    }
                    if depth < max_depth:
                        item["children"] = _walk(child, depth + 1)
                    else:
                        item["truncated"] = True
                    items.append(item)
                elif child.is_file():
                    size = child.stat().st_size
                    items.append({
                        "name": child.name,
                        "type": "file",
                        "path": str(rel),
                        "depth": depth,
                        "size_bytes": size,
                    })
        except PermissionError:
            items.append({"name": str(rel), "type": "error", "reason": "permission_denied"})
        return items

    entries = _walk(root, 1)

    return {
        "root": str(root.relative_to(_REPO_ROOT)),
        "max_depth": max_depth,
        "entries": entries,
    }


def read_file(path: str, max_lines: int = 300) -> dict:
    """Read a text file from the repository and return its contents.

    Args:
        path: File path relative to the repository root.
        max_lines: Maximum number of lines to return (default 300).

    Returns:
        A dict with metadata and the file content (or an error).
    """
    try:
        target = _resolve_within_repo(path)
    except ValueError as exc:
        return {"error": f"Path escapes repository root: {exc}"}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Invalid path: {type(exc).__name__}: {exc}"}

    if not target.exists():
        return {"error": f"File not found: {path}"}
    if not target.is_file():
        return {"error": f"Not a file: {path}"}

    size = target.stat().st_size
    if size > 2 * 1024 * 1024:
        return {
            "error": "File too large to read.",
            "path": path,
            "size_bytes": size,
            "max_bytes": 2 * 1024 * 1024,
        }

    try:
        text = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return {"error": "File appears to be binary or non-UTF-8.", "path": path}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Failed to read file: {type(exc).__name__}: {exc}"}

    lines = text.splitlines()
    total_lines = len(lines)
    truncated = total_lines > max_lines
    if truncated:
        lines = lines[:max_lines]

    return {
        "path": path,
        "total_lines": total_lines,
        "returned_lines": len(lines),
        "truncated": truncated,
        "content": "\n".join(lines),
    }
