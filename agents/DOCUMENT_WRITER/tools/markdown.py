"""Markdown writer tool for the MARKDOWN_WRITER agent."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_tools_dir = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("_shared", str(_tools_dir / "_shared.py"))
_shared = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_shared)


def write_markdown(filename: str, content: str) -> str:
    """Write content to a Markdown file.

    Args:
        filename: Name of the file. If it does not end with `.md`, the extension
            is appended automatically.
        content: Markdown or text content to write.

    Returns:
        JSON string with the relative file path and format.
    """
    if not filename.lower().endswith(".md"):
        filename = f"{filename}.md"

    _shared.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    target = _shared.unique_path(_shared.OUTPUT_DIR / filename)
    target.write_text(content, encoding="utf-8")
    _shared.logger.info("Wrote markdown file: %s", target)
    return _shared.format_response(target, ".md")
