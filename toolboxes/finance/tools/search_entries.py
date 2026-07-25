"""Search historical entries."""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from typing import Any

_tools_dir = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("_store", str(_tools_dir / "_store.py"))
_store_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_store_module)

logger = logging.getLogger(__name__)


def search_entries(
    query: str = "",
    time_range: str = "",
    category: str = "",
    currency: str = "",
    entry_type: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    """Search historical entries by keyword and filters.

    Args:
        query: Keyword to search in description, category, or note.
        time_range: YYYY-MM, YYYY, last_N_months, or empty for all time.
        category: Filter to an exact category (case-insensitive).
        currency: Filter to a currency code.
        entry_type: Filter to "income" or "expense".
        limit: Maximum entries to return (default 50).

    Returns:
        A dict with matched entries and a short summary.
    """
    try:
        df = _store_module.list_all_entries()
        df = _store_module.filter_entries(df, time_range, category, currency, entry_type, query)
        df = df.sort_values(["date"], ascending=[False])
        records = _store_module.entries_to_records(df, limit)
        return {
            "count": len(df),
            "returned": len(records),
            "time_range": time_range or "all time",
            "entries": records,
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error searching entries")
        return {"error": f"Failed to search entries: {type(exc).__name__}: {exc}"}
