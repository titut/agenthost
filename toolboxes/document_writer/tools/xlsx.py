"""XLSX writer tool for the document_writer toolbox."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

_tools_dir = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("_shared", str(_tools_dir / "_shared.py"))
_shared = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_shared)


def write_xlsx(
    filename: str,
    content: str = "",
    data: list[dict[str, Any]] | None = None,
) -> str:
    """Write structured data to an Excel workbook.

    Args:
        filename: Name of the file. If it does not end with `.xlsx`, the
            extension is appended automatically.
        content: Optional JSON array or Markdown table. Ignored if `data` is
            provided.
        data: Optional list of dictionaries. Takes precedence over `content`.

    Returns:
        JSON string with the relative file path and format.
    """
    from openpyxl import Workbook  # type: ignore[import-untyped]

    if not filename.lower().endswith(".xlsx"):
        filename = f"{filename}.xlsx"

    _shared.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    target = _shared.unique_path(_shared.OUTPUT_DIR / filename)

    headers, rows = _shared.normalize_spreadsheet_data(content, data)

    wb = Workbook()
    ws = wb.active
    if ws is None:
        ws = wb.create_sheet()
    if headers:
        ws.append(headers)
    for row in rows:
        ws.append(row)
    wb.save(str(target))

    _shared.logger.info("Wrote xlsx file: %s", target)
    return _shared.format_response(target, ".xlsx")
