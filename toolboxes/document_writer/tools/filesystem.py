"""Filesystem read tools for the document_writer toolbox.

Read files and list directories by absolute path. These tools are intended to
let the document_writer agent inspect source material anywhere on the local
filesystem before generating documents.
"""

from __future__ import annotations

import json
from pathlib import Path

from agenthost.filesystem_tools import extract_text
from agenthost.logger import setup_logging


logger = setup_logging("agenthost.document_writer.filesystem")

# Suffixes that are transparently converted to text before reading.
_EXTRACTABLE_SUFFIXES = {".docx", ".pdf", ".xlsx", ".csv"}


def _require_absolute(path: str) -> Path:
    """Resolve a path after validating it is absolute.

    Raises:
        ValueError: if the path is empty or not absolute.
    """
    if not path:
        raise ValueError("Path cannot be empty")
    target = Path(path)
    if not target.is_absolute():
        raise ValueError(f"Path must be absolute: {path}")
    return target.resolve()


def read_file(path: str, max_lines: int = 1000, offset: int = 1) -> str:
    """Read a file at an absolute path.

    Text files are read directly. ``.docx``, ``.pdf``, ``.xlsx``, and ``.csv``
    files are converted to text automatically.

    Args:
        path: Absolute path to the file.
        max_lines: Maximum number of lines to return (default 1000).
        offset: Line number to start from (1-based).

    Returns:
        JSON string with the requested lines and metadata.
    """
    try:
        target = _require_absolute(path)
    except ValueError as exc:
        return json.dumps({"error": str(exc)})

    if not target.exists():
        return json.dumps({"error": f"File not found: {path}"})
    if not target.is_file():
        return json.dumps({"error": f"Path is not a file: {path}"})

    suffix = target.suffix.lower()
    try:
        if suffix in _EXTRACTABLE_SUFFIXES:
            content = extract_text(target)
            lines = content.splitlines(keepends=True)
        else:
            with target.open("r", encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
    except UnicodeDecodeError:
        return json.dumps({"error": f"File appears to be binary or non-text: {path}"})
    except ValueError:
        return json.dumps({"error": f"File appears to be binary or non-text: {path}"})
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": f"Failed to read file: {exc}"})

    start = max(0, offset - 1)
    end = start + max_lines
    selected = lines[start:end]

    return json.dumps(
        {
            "path": str(target),
            "total_lines": len(lines),
            "offset": offset,
            "max_lines": max_lines,
            "returned_lines": len(selected),
            "content": "".join(selected),
        },
        indent=2,
    )


def list_files(path: str) -> str:
    """List files and directories at an absolute path (like ``ls [path]``).

    Args:
        path: Absolute path to the directory.

    Returns:
        JSON string with directory entries and metadata.
    """
    try:
        target = _require_absolute(path)
    except ValueError as exc:
        return json.dumps({"error": str(exc)})

    if not target.exists():
        return json.dumps({"error": f"Directory not found: {path}"})
    if not target.is_dir():
        return json.dumps({"error": f"Path is not a directory: {path}"})

    entries: list[dict[str, object]] = []
    try:
        for entry in sorted(target.iterdir()):
            try:
                stat = entry.stat()
                entries.append(
                    {
                        "name": entry.name,
                        "type": "directory" if entry.is_dir() else "file",
                        "size_bytes": stat.st_size if entry.is_file() else None,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                entries.append(
                    {
                        "name": entry.name,
                        "type": "unknown",
                        "error": str(exc),
                    }
                )
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": f"Failed to list directory: {exc}"})

    return json.dumps(
        {
            "path": str(target),
            "count": len(entries),
            "entries": entries,
        },
        indent=2,
    )
