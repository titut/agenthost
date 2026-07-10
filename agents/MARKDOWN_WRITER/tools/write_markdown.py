"""Markdown writer tool for the MARKDOWN_WRITER agent."""

from __future__ import annotations

from pathlib import Path

from agenthost.home import ensure_agenthost_home
from agenthost.logger import setup_logging

logger = setup_logging("agenthost.markdown_writer")

# Default output directory for markdown files (relative to agenthost home).
OUTPUT_DIR = ensure_agenthost_home() / "output" / "markdown_writer"
RELATIVE_OUTPUT_DIR = Path("output") / "markdown_writer"


def write_markdown(filename: str, content: str) -> str:
    """Write content to a Markdown file in the shared markdown output directory.

    Args:
        filename: Name of the file. If it does not end with `.md`, the extension
            is appended automatically.
        content: Markdown content to write.

    Returns:
        JSON string with the full absolute path of the written file.
    """
    if not filename.endswith(".md"):
        filename = f"{filename}.md"

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    target = OUTPUT_DIR / filename

    # Avoid overwriting by appending a counter if the file exists.
    original_target = target
    counter = 1
    while target.exists():
        stem = original_target.stem
        target = OUTPUT_DIR / f"{stem}_{counter}.md"
        counter += 1

    target.write_text(content, encoding="utf-8")
    logger.info("Wrote markdown file: %s", target)

    # Return a path relative to the agenthost home directory so downstream
    # tools (e.g. send_discord_file) can resolve it correctly even if the
    # model misspells the project folder name.
    relative = RELATIVE_OUTPUT_DIR / target.name
    return f"FILE_PATH: {relative}"
