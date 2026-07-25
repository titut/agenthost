"""Search the legacy archive SQLite database."""

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


def search_archive(
    query: str = "",
    time_range: str = "",
    category: str = "",
    bank: str = "",
    currency: str = "",
    entry_type: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    """Search historical transactions from the legacy archive.

    Args:
        query: Keyword to search in descriptions.
        time_range: YYYY-MM, YYYY, last_N_months, or empty for all time.
        category: Filter by category.
        bank: Filter by bank.
        currency: Filter by currency.
        entry_type: Filter by income/expense.
        limit: Maximum results (default 50).

    Returns:
        Matching archived transactions and count.
    """
    try:
        return _store_module.search_archive(
            query=query,
            time_range=time_range,
            category=category,
            bank=bank,
            currency=currency,
            entry_type=entry_type,
            limit=limit,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error searching archive")
        return {"error": f"Failed to search archive: {type(exc).__name__}: {exc}"}
