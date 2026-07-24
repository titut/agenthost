"""CSV writer tool for the document_writer toolbox."""

from __future__ import annotations

import csv
import importlib.util
from pathlib import Path
from typing import Any

_tools_dir = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("_shared", str(_tools_dir / "_shared.py"))
_shared = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_shared)


def write_csv(
    filename: str,
    content: str = "",
    data: list[dict[str, Any]] | None = None,
) -> str:
    """Write structured data to a CSV file.

    Args:
        filename: Name of the file. If it does not end with `.csv`, the
            extension is appended automatically.
        content: Optional JSON array or Markdown table. Ignored if `data` is
            provided.
        data: Optional list of dictionaries. Takes precedence over `content`.

    Returns:
        JSON string with the relative file path and format.
    """
    if not filename.lower().endswith(".csv"):
        filename = f"{filename}.csv"

    _shared.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    target = _shared.unique_path(_shared.OUTPUT_DIR / filename)

    headers, rows = _shared.normalize_spreadsheet_data(content, data)

    with target.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        if headers:
            writer.writerow(headers)
        writer.writerows(rows)

    _shared.logger.info("Wrote csv file: %s", target)
    return _shared.format_response(target, ".csv")
