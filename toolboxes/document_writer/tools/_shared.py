"""Shared helpers for the document_writer toolbox tools."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agenthost.home import ensure_agenthost_home
from agenthost.logger import setup_logging

logger = setup_logging("agenthost.document_writer")

# Default output directory for files (relative to agenthost home).
OUTPUT_DIR = ensure_agenthost_home() / "output" / "document_writer"
RELATIVE_OUTPUT_DIR = Path("output") / "document_writer"


def unique_path(target: Path) -> Path:
    """Avoid overwriting by appending a counter if the file exists."""
    if not target.exists():
        return target
    original_target = target
    counter = 1
    while target.exists():
        stem = original_target.stem
        suffix = original_target.suffix
        target = original_target.parent / f"{stem}_{counter}{suffix}"
        counter += 1
    return target


def parse_table_data(content: str) -> tuple[list[str], list[list[Any]]]:
    """Parse structured data from JSON string or Markdown table.

    Returns (headers, rows).
    """
    text = content.strip()
    if not text:
        return [], []

    # Try JSON first.
    try:
        data = json.loads(text)
        if isinstance(data, list) and data:
            if isinstance(data[0], dict):
                headers = list(data[0].keys())
                rows = [[row.get(h, "") for h in headers] for row in data]
                return headers, rows
            if isinstance(data[0], list):
                return [], [list(row) for row in data]
    except json.JSONDecodeError:
        pass

    # Try Markdown table.
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) >= 2 and lines[0].startswith("|") and lines[1].startswith("|"):
        header_line = lines[0]
        headers = [cell.strip() for cell in header_line.split("|")[1:-1]]
        rows: list[list[Any]] = []
        for line in lines[2:]:
            if not line.startswith("|"):
                continue
            cells = [cell.strip() for cell in line.split("|")[1:-1]]
            while len(cells) < len(headers):
                cells.append("")
            cells = cells[: len(headers)]
            rows.append(cells)
        return headers, rows

    return [], []


def normalize_spreadsheet_data(
    content: str, data: list[dict[str, Any]] | None
) -> tuple[list[str], list[list[Any]]]:
    """Return (headers, rows) for spreadsheet formats."""
    if data is not None:
        headers = list(data[0].keys()) if data else []
        rows = [[row.get(h, "") for h in headers] for row in data]
        return headers, rows
    return parse_table_data(content)


def format_response(target: Path, suffix: str) -> str:
    """Return a JSON response with the relative file path and format."""
    relative = RELATIVE_OUTPUT_DIR / target.name
    return json.dumps({"file_path": str(relative), "format": suffix.lstrip(".")})
